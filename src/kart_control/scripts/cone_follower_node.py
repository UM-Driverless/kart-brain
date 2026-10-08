#!/usr/bin/env python3
"""Cone-following controller for the kart.

Supports four controller types (selected via the ``controller_type`` param):

**geometric**
    Nearest blue/yellow midpoint → atan2 → steer.  Six tunable params.

**neural**
    Small feed-forward net (8→8→2), 90 weights.

**neural_v2**
    Larger net (17→16→2) with 4 cones per side + speed feedback, 322 weights.
    Trained with lap-time fitness for faster driving.

**mpc**
    Kinematic bicycle model MPC. Builds a midpoint reference path from cone
    pairs, then minimises lateral cross-track error + steering-rate over a
    receding horizon using scipy SLSQP.  Key tunable params:

    * ``mpc_horizon``       – prediction steps  (default 8)
    * ``mpc_dt``            – step duration [s] (default 0.10)
    * ``mpc_w_cte``         – cross-track-error weight (default 3.0)
    * ``mpc_w_dsteer``      – steering-rate weight     (default 40.0)
    * ``mpc_w_heading``     – heading-error weight     (default 2.0)
    * ``mpc_lookahead``     – max path distance used   (default 8.0 m)

**none**
    No steering control at all. Speed is still computed and published, but the
    steering motor is left unpowered (direct-PWM mode, 0 output) so a human can
    turn the wheel by hand while the kart drives itself forward.

All controllers receive Detection3DArray in the camera *optical* frame
(Z=forward, X=right, Y=down) and publish Twist on ``/kart/cmd_vel``.
"""

import json
import math

import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy
from geometry_msgs.msg import PointStamped, Twist

# from nav_msgs.msg import Odometry  # removed: kart has no speed sensor
from rcl_interfaces.msg import SetParametersResult
from std_msgs.msg import Float32, String

# from std_msgs.msg import Float32    # removed: kart has no speed sensor
from vision_msgs.msg import Detection3DArray
from kb_interfaces.msg import Frame

try:
    from scipy.optimize import minimize as scipy_minimize

    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

try:
    from kart_perception.zed_od_utils import HAS_ZED_INTERFACES, zed_objects_to_det3d

    if HAS_ZED_INTERFACES:
        from zed_interfaces.msg import ObjectsStamped
except ImportError:
    HAS_ZED_INTERFACES = False


# ---------------------------------------------------------------------------
# Kinematic bicycle model helpers (used by MPC)
# ---------------------------------------------------------------------------


def _bicycle_step(x, y, psi, v, delta, wheelbase, dt):
    """Propagate kinematic bicycle model one step forward."""
    x_next = x + v * math.cos(psi) * dt
    y_next = y + v * math.sin(psi) * dt
    psi_next = psi + (v / wheelbase) * math.tan(delta) * dt
    return x_next, y_next, psi_next


def _build_midpoint_path(cones, half_track_width):
    """Pair blue/yellow cones and return a sorted list of (fwd, left) midpoints."""
    blues = [
        (fwd, left, math.hypot(fwd, left))
        for cls, fwd, left in cones
        if cls == "blue_cone" and fwd > 0.3
    ]
    yellows = [
        (fwd, left, math.hypot(fwd, left))
        for cls, fwd, left in cones
        if cls == "yellow_cone" and fwd > 0.3
    ]

    blues.sort(key=lambda c: c[2])
    yellows.sort(key=lambda c: c[2])

    midpoints = []
    if blues and yellows:
        used_y = set()
        for bx, by, bd in blues:
            best_j, best_dd = -1, float("inf")
            for j, (yx, yy, yd) in enumerate(yellows):
                if j in used_y:
                    continue
                dd = abs(bd - yd)
                if dd < best_dd:
                    best_dd = dd
                    best_j = j
            if best_j >= 0 and best_dd < 8.0:
                yx, yy, _ = yellows[best_j]
                used_y.add(best_j)
                midpoints.append(((bx + yx) / 2.0, (by + yy) / 2.0))
            else:
                midpoints.append((bx, by - half_track_width))
        for j, (yx, yy, _) in enumerate(yellows):
            if j not in used_y:
                midpoints.append((yx, yy + half_track_width))
    elif blues:
        for bx, by, _ in blues:
            midpoints.append((bx, by - half_track_width))
    elif yellows:
        for yx, yy, _ in yellows:
            midpoints.append((yx, yy + half_track_width))

    midpoints.sort(key=lambda p: p[0])
    return midpoints


def _path_heading(midpoints, idx):
    """Estimate path heading at *idx* using finite differences."""
    n = len(midpoints)
    if n < 2:
        return 0.0
    i0 = max(0, idx - 1)
    i1 = min(n - 1, idx + 1)
    dx = midpoints[i1][0] - midpoints[i0][0]
    dy = midpoints[i1][1] - midpoints[i0][1]
    return math.atan2(dy, dx)


def _nearest_path_point(midpoints, x, y):
    """Return the index of the closest midpoint to (x, y)."""
    best_i, best_d = 0, float("inf")
    for i, (px, py) in enumerate(midpoints):
        d = math.hypot(px - x, py - y)
        if d < best_d:
            best_d = d
            best_i = i
    return best_i


def _mpc_cost(
    u_flat,
    x0,
    y0,
    psi0,
    v,
    midpoints,
    N,
    dt,
    wheelbase,
    w_cte,
    w_dsteer,
    w_heading,
    prev_steer,
    max_steer,
):
    """Compute the MPC objective for a candidate control sequence."""
    x, y, psi = x0, y0, psi0
    cost = 0.0
    prev_u = prev_steer

    for k in range(N):
        delta = float(u_flat[k])

        idx = _nearest_path_point(midpoints, x, y)
        px, py = midpoints[idx]
        cte = math.hypot(x - px, y - py)

        path_psi = _path_heading(midpoints, idx)
        heading_err = math.atan2(math.sin(psi - path_psi), math.cos(psi - path_psi))

        d_steer = delta - prev_u

        cost += w_cte * cte**2
        cost += w_heading * heading_err**2
        cost += w_dsteer * d_steer**2

        x, y, psi = _bicycle_step(x, y, psi, v, delta, wheelbase, dt)
        prev_u = delta

    return cost


class ConeFollowerNode(Node):
    """@brief Cone-following controller node.

    Supports geometric, neural (v1), neural_v2, and mpc controller types. Receives
    Detection3DArray in camera optical frame and publishes Twist on /kart/cmd_vel.
    """

    WHEELBASE = 1.05  # [m]

    def __init__(self):
        """@brief Initialize the controller with parameters, neural weights (if applicable), and ROS plumbing."""
        super().__init__("cone_follower")

        # --- common params ---
        self.declare_parameter("detections_topic", "/perception/cones_3d")
        self.declare_parameter("cmd_vel_topic", "/kart/cmd_vel")
        self.declare_parameter("odom_topic", "/zed/zed_node/odom")
        self.declare_parameter("no_cone_timeout", 1.0)
        self.declare_parameter(
            "controller_type", "geometric"
        )  # geometric|pure_pursuit|neural|neural_v2|mpc|stanley
        self.declare_parameter(
            "speed_controller_type", "curve_factor"
        )  # curve_factor|constant_throttle|constant_throttle_blind
        #    |constant_throttle_stop|constant_speed|neural_v2|zero
        self.declare_parameter("weights_json", "")  # path for neural

        # --- geometric params ---
        self.declare_parameter("steering_gain", 3.0)
        self.declare_parameter("max_steer", 1.309)
        self.declare_parameter("max_speed", 2.625)
        self.declare_parameter("min_speed", 0.5)
        self.declare_parameter("lookahead_max", 15.0)
        self.declare_parameter("half_track_width", 1.5)
        self.declare_parameter("speed_curve_factor", 0.0)

        # --- constant_speed params (closed loop on /kart/speed) ---
        # 0.28 m/s ≈ 1 km/h: walking-pace default, so selecting the closed loop
        # by accident does not launch the kart. The dashboard sets it live.
        self.declare_parameter("target_speed", 0.28)         # m/s
        # Raised from 0.6 on 2026-08-10. Both gains are settable at runtime:
        #     ros2 param set /cone_follower speed_kp 1.2
        self.declare_parameter("speed_kp", 0.9)              # throttle units per m/s error
        self.declare_parameter("speed_ki", 0.4)              # per m/s error per second
        self.declare_parameter("speed_stale_timeout", 0.4)   # s

        # --- Stanley params ---
        self.declare_parameter("stanley_k", 1.5)
        # TODO: once the PCB/hall-sensor speed reading is available, replace
        # stanley_assumed_speed with the live speed topic — Stanley's
        # cross-track term is speed-dependent and self-normalizes at higher
        # speeds when fed a real v.
        self.declare_parameter("stanley_assumed_speed", 2.0)

        # --- MPC params ---
        self.declare_parameter("mpc_horizon", 8)
        self.declare_parameter("mpc_dt", 0.10)
        self.declare_parameter("mpc_w_cte", 3.0)
        self.declare_parameter("mpc_w_dsteer", 40.0)
        self.declare_parameter("mpc_w_heading", 2.0)
        self.declare_parameter("mpc_lookahead", 15.0)

        det_topic = str(self.get_parameter("detections_topic").value)
        cmd_topic = str(self.get_parameter("cmd_vel_topic").value)
        odom_topic = str(self.get_parameter("odom_topic").value)
        self.no_cone_timeout = float(self.get_parameter("no_cone_timeout").value)
        self.controller_type = str(self.get_parameter("controller_type").value)
        self.speed_controller_type = self.SPEED_CONTROLLER_ALIASES.get(
            str(self.get_parameter("speed_controller_type").value),
            str(self.get_parameter("speed_controller_type").value),
        )

        # geometric fields
        self.steering_gain = float(self.get_parameter("steering_gain").value)
        self.max_steer = float(self.get_parameter("max_steer").value)
        self.max_speed = float(self.get_parameter("max_speed").value)
        self.min_speed = float(self.get_parameter("min_speed").value)
        self.lookahead_max = float(self.get_parameter("lookahead_max").value)
        self.half_track_width = float(self.get_parameter("half_track_width").value)
        self.speed_curve_factor = float(self.get_parameter("speed_curve_factor").value)

        # constant_speed fields
        self.target_speed = float(self.get_parameter("target_speed").value)
        self.speed_kp = float(self.get_parameter("speed_kp").value)
        self.speed_ki = float(self.get_parameter("speed_ki").value)
        self.speed_stale_timeout = float(
            self.get_parameter("speed_stale_timeout").value
        )
        self._speed_integral = 0.0
        self._speed_pid_time = None

        # Stanley fields
        self.stanley_k = float(self.get_parameter("stanley_k").value)
        self.stanley_assumed_speed = float(
            self.get_parameter("stanley_assumed_speed").value
        )

        # MPC fields
        self.mpc_N = int(self.get_parameter("mpc_horizon").value)
        self.mpc_dt = float(self.get_parameter("mpc_dt").value)
        self.mpc_w_cte = float(self.get_parameter("mpc_w_cte").value)
        self.mpc_w_dsteer = float(self.get_parameter("mpc_w_dsteer").value)
        self.mpc_w_heading = float(self.get_parameter("mpc_w_heading").value)
        self.mpc_lookahead = float(self.get_parameter("mpc_lookahead").value)
        self._mpc_prev_solution: np.ndarray | None = None

        if self.controller_type == "mpc" and not HAS_SCIPY:
            self.get_logger().error("controller_type=mpc but scipy is not installed")
            raise SystemExit(1)

        # neural net weights (loaded for neural or neural_v2)
        self._nn_W1 = self._nn_b1 = self._nn_W2 = self._nn_b2 = None
        self._nn_max_steer = 0.785  # must match training MAX_STEER (current weights)
        self._nn_max_speed = 5.0  # must match training MAX_SPEED (current weights)
        self._nn_input_size = 8  # v1 default
        self._nn_n_blue = 2  # cones per side for v1
        self._nn_n_yellow = 2
        self._nn_uses_speed = False
        self._current_speed = 0.0

        if self.controller_type in ("neural", "neural_v2"):
            self._load_neural_weights()

        # ROS plumbing
        self.cmd_pub = self.create_publisher(Twist, cmd_topic, 10)
        self.sub = self.create_subscription(
            Detection3DArray, det_topic, self._on_detections, 10
        )
        # Also subscribe to ZED SDK ObjectsStamped for built-in OD mode
        if HAS_ZED_INTERFACES:
            self.declare_parameter("zed_objects_topic", "/zed/zed_node/obj_det/objects")
            self.create_subscription(
                ObjectsStamped,
                str(self.get_parameter("zed_objects_topic").value),
                lambda msg: self._on_detections(zed_objects_to_det3d(msg)),
                10,
            )
        # Speed feedback: /kart/speed comes from speed_estimator (cone range rates,
        # see kart_perception/speed_model.py). The ZED VIO odometry that used to feed
        # this was disabled in 02eda4d for its GPU cost, and the commented-out lines
        # below are what it looked like.
        # self.odom_sub = self.create_subscription(Odometry, odom_topic, self._on_odom, odom_qos)
        self.create_subscription(Float32, "/kart/speed", self._on_speed, 10)
        self._actual_speed = 0.0
        self._speed_time = None  # None until the first reading arrives
        self._last_steer = 0.0
        # Controller-selected target (fwd, left) for HUD arrow rendering.
        # Set by each controller after it picks its aim point.
        self._last_target = None
        self.target_pub = self.create_publisher(PointStamped, "/kart/target", 10)
        # Steering mode (0=PID angle, 1=direct PWM). Only used by the "none"
        # controller, to leave the motor unpowered. Repeated for 1 s after a
        # change because the frame goes out over UART with no acknowledgement.
        self.steer_mode_pub = self.create_publisher(Frame, "/orin/steer_mode", 10)
        self._steer_mode_frame = None
        self._steer_mode_repeats = 0
        self.create_timer(0.01, self._send_steer_mode)
        # This topic has three publishers — this node, state_machine and the
        # dashboard's Angle/PWM toggle — and "none" is the only one of them that
        # is a safety claim rather than a preference: it means nothing may drive
        # the steering motor while a human has the wheel. state_machine forces
        # PID mode on every autonomous mission reaching AS_READY, which used to
        # silently undo it and put a PID target of 0 rad on the column, i.e. it
        # steered to centre against the driver. So watch the topic and re-assert
        # direct PWM whenever anything else sets PID while "none" is selected.
        self.create_subscription(Frame, "/orin/steer_mode", self._on_steer_mode, 10)
        self.create_timer(1.0, self._refresh_steer_mode)
        self.last_detection_time = self.get_clock().now()
        self._detection_invalid = False
        self._received_detections = False
        self._desired_mode_pub = self.create_publisher(Frame, "/kart/controller_steer_mode", 10)
        self._config_pub = self.create_publisher(String, "/kart/controller_config", 10)
        self._sensor_safety_pub = self.create_publisher(String, "/kart/controller_safety", 10)
        self.create_timer(0.1, self._publish_sensor_safety)
        self.timer = self.create_timer(0.1, self._safety_check)
        self.create_subscription(
            String, "/dashboard/controller_type", self._on_controller_type, 10
        )
        self.create_subscription(
            String,
            "/dashboard/speed_controller_type",
            self._on_speed_controller_type,
            10,
        )
        self.create_subscription(
            Float32, "/dashboard/target_speed", self._on_target_speed, 10
        )
        self.add_on_set_parameters_callback(self._on_param_set)

        self.get_logger().info(
            f"Controller: steer={self.controller_type} speed={self.speed_controller_type}"
        )

    def _on_controller_type(self, msg: String):
        """@brief Callback for runtime controller type changes from the dashboard."""
        new_type = msg.data
        valid = ("geometric", "pure_pursuit", "neural_v2", "mpc", "stanley", "none")
        if new_type in valid and new_type != self.controller_type:
            self.get_logger().info(
                f"Controller type: {self.controller_type} → {new_type}"
            )
            self.controller_type = new_type
            # "none" means the steering motor must not be driven at all. A PID
            # target of 0 rad would hold the wheel straight and fight the
            # driver, so switch the ESP32 to direct PWM, where 0 is no drive.
            # Any other controller needs the PID back.
            self._request_steer_mode(1 if new_type == "none" else 0)
            if new_type == "mpc" and not HAS_SCIPY:
                self.get_logger().error("scipy not installed – cannot switch to mpc")
                self.controller_type = "geometric"
                return
            if new_type in ("neural", "neural_v2") and self._nn_W1 is None:
                self._load_neural_weights()

    def _request_steer_mode(self, mode: int, repeats: int = 100):
        """@brief Queue a steering-mode frame for the ESP32. 0=PID angle, 1=direct PWM.

        @param mode 0=PID angle, 1=direct PWM.
        @param repeats How many times to send it, at 100 Hz. The default 1 s burst
               covers a frame lost on the unacknowledged UART link; the periodic
               re-assertion passes 1, because it runs again a second later anyway.
        """
        frame = Frame()
        frame.type = Frame.ORIN_STEER_MODE
        frame.payload = [mode]
        self._steer_mode_frame = frame
        self._steer_mode_repeats = repeats

    def _on_steer_mode(self, msg: Frame):
        """@brief Re-assert direct PWM if something else asks for PID while steering is off.

        Fires on this node's own frames too; those already carry the mode it wants,
        so they change nothing and the check cannot loop.
        """
        if not msg.payload or self.controller_type != "none":
            return
        if int(msg.payload[0]) != 1:
            self.get_logger().warn(
                "steer_mode was set to PID while steering is None — re-asserting "
                "direct PWM so the motor stays unpowered"
            )
            self._request_steer_mode(1)

    def _refresh_steer_mode(self):
        """@brief Once a second, restate direct PWM while steering is None.

        The subscription above catches anything that announces itself on this
        topic. This covers what it cannot: an ESP32 that rebooted and came back in
        its flashed default mode, having missed every frame sent before it.
        """
        if self.controller_type == "none" and self._steer_mode_repeats <= 0:
            # One frame, not the 1 s burst a mode change gets: re-arming that burst
            # every second would put a permanent 100 Hz stream on the UART, competing
            # with the steering frames that actually matter.
            self._request_steer_mode(1, repeats=1)

    def _send_steer_mode(self):
        """@brief Timer callback: resend a pending steering-mode frame until the repeats run out."""
        if self._steer_mode_frame is not None and self._steer_mode_repeats > 0:
            self.steer_mode_pub.publish(self._steer_mode_frame)
            self._steer_mode_repeats -= 1

    # Old names for the constant-throttle modes, still accepted so a browser tab
    # left open across the rename keeps working instead of being ignored.
    SPEED_CONTROLLER_ALIASES = {
        "constant": "constant_throttle",
        "constant_stop": "constant_throttle_stop",
    }

    def _on_speed_controller_type(self, msg: String):
        """@brief Callback for runtime speed controller type changes from the dashboard."""
        new_type = self.SPEED_CONTROLLER_ALIASES.get(msg.data, msg.data)
        if (
            new_type
            in (
                "curve_factor",
                "constant_throttle",
                "constant_throttle_blind",
                "constant_speed",
                "constant_throttle_stop",
                "neural_v2",
                "zero",
            )
            and new_type != self.speed_controller_type
        ):
            self.get_logger().info(
                f"Speed controller: {self.speed_controller_type} → {new_type}"
            )
            self.speed_controller_type = new_type
            # A stale integral from a previous stint would otherwise apply itself
            # the instant closed-loop control is selected.
            self._speed_integral = 0.0
            self._speed_pid_time = None

    def _on_param_set(self, params) -> SetParametersResult:
        """@brief Apply live gain changes, so the speed loop can be tuned at the kart.

        The closed-loop speed gains, and max_speed — which the PI no longer uses at
        all, but which is still the level the open-loop modes command outright.
        Everything else here needs a restart, deliberately: these are the ones tuned
        by hand at the kart.
        """
        for p in params:
            if p.name in ("speed_kp", "speed_ki", "max_speed"):
                if float(p.value) < 0.0:
                    return SetParametersResult(
                        successful=False, reason=f"{p.name} must not be negative"
                    )
                setattr(self, p.name, float(p.value))
                # A gain or ceiling change makes the accumulated integral meaningless.
                self._speed_integral = 0.0
                self.get_logger().info(f"{p.name} → {p.value}")
        return SetParametersResult(successful=True)

    def _on_target_speed(self, msg: Float32):
        """@brief Callback for the closed-loop speed setpoint from the dashboard.

        @param msg Float32 setpoint in m/s. Only the negative side is rejected —
                   a negative setpoint is not a slower kart, it is a reversed sign
                   through a loop that cannot brake. The integral is dropped on a
                   change so the old error does not push the kart past the new
                   setpoint.
        """
        new_target = max(0.0, float(msg.data))
        if abs(new_target - self.target_speed) < 1e-6:
            return
        self.get_logger().info(
            f"Target speed: {self.target_speed:.2f} → {new_target:.2f} m/s"
        )
        self.target_speed = new_target
        self._speed_integral = 0.0
        self._speed_pid_time = None

    def _on_speed(self, msg: Float32):
        """@brief Callback for the estimated forward speed from speed_estimator."""
        self._actual_speed = float(msg.data)
        self._speed_time = self.get_clock().now()

    def _speed_is_fresh(self) -> bool:
        """@brief Whether a speed reading arrived recently enough to steer a loop by.

        speed_estimator stops publishing entirely once its estimate is no longer
        backed by cone detections, so silence here means "no measurement", not
        "zero". Treating a stale value as current is what would let the closed-loop
        mode keep feeding throttle against a number frozen from before the cones
        were lost.
        """
        if self._speed_time is None:
            return False
        age = (self.get_clock().now() - self._speed_time).nanoseconds / 1e9
        return 0 <= age <= self.speed_stale_timeout and math.isfinite(self._actual_speed)

    def _constant_speed_throttle(self) -> float:
        """@brief PI control of throttle to hold target_speed, in the fake-m/s units.

        There is no ceiling on the output. It used to be capped at self.max_speed —
        52.5% throttle — so that a wrong speed estimate could never command more than
        the open-loop mode would have. That cap was removed on request (Rubén,
        2026-08-10): the loop may now wind up to full throttle, and the only thing
        bounding it is cmd_vel_bridge clamping the throttle fraction at 1.0, which is
        the actuator's own range rather than a limit. The speed estimate this closes
        on has still never been checked against a real speed.

        Throttle only, never brake. A negative command would ask cmd_vel_bridge for
        the brake actuator, and braking on an unvalidated speed reading is a
        different risk from merely lifting off. Overspeed is handled by commanding
        zero throttle and letting the kart coast down.
        """
        now = self.get_clock().now()

        if not self._speed_is_fresh():
            # No measurement: shut the throttle and drop the integral, so nothing
            # accumulates while blind and slams in when the reading returns.
            self._speed_integral = 0.0
            self._speed_pid_time = now
            return 0.0

        dt = 0.0
        if self._speed_pid_time is not None:
            dt = (now - self._speed_pid_time).nanoseconds / 1e9
        self._speed_pid_time = now
        # A long gap means the loop was not running; treat it as a restart rather
        # than integrating an error over a period nobody was controlling.
        if dt <= 0.0 or dt > 0.5:
            return 0.0

        error = self.target_speed - self._actual_speed
        proportional = self.speed_kp * error

        # Integrated with no upper clamp, so the term winds up freely while the kart
        # is held back — on a slope, or against a wheel chock — and dumps whatever it
        # has accumulated the moment it frees. Only the lower bound survives, because
        # a negative total would command the brakes rather than less throttle.
        self._speed_integral += self.speed_ki * error * dt
        self._speed_integral = max(0.0, self._speed_integral)

        return max(0.0, proportional + self._speed_integral)

    def _compute_speed(self, steer, nn_out=None, cones=None):
        """@brief Compute speed based on the active speed controller type.

        Every mode except constant_speed is open loop. cmd_vel_bridge_node divides
        the returned value by its own max_speed param to get a throttle fraction,
        so for those modes this number is a throttle command in m/s clothing —
        hence constant_throttle rather than constant_speed: it holds the pedal
        still, not the speed. constant_speed is the one mode that closes a loop,
        using /kart/speed from the cone-based estimator.

        @param steer Current steering angle (rad).
        @param nn_out Raw neural net output (2-element array), or None if steering is not neural.
        @param cones List of (class_id, fwd, left) tuples — used by stop-on-orange modes.
        @return Speed in m/s.
        """
        if self.speed_controller_type == "zero":
            return 0.0
        if self.speed_controller_type in ("constant_throttle", "constant_throttle_blind"):
            return self.max_speed
        if self.speed_controller_type == "constant_speed":
            return self._constant_speed_throttle()
        if self.speed_controller_type == "constant_throttle_stop":
            if cones:
                for cls, _fwd, _left in cones:
                    if cls in ("orange_cone", "large_orange_cone"):
                        return 0.0
            return self.max_speed
        elif self.speed_controller_type == "neural_v2":
            if nn_out is not None:
                speed = float(1.0 / (1.0 + np.exp(-nn_out[1]))) * self._nn_max_speed
                return max(self.min_speed, min(self.max_speed, speed))
            # neural_v2 speed requires neural steering; fall back
            speed = self.max_speed * (1.0 - self.speed_curve_factor * abs(steer))
            return max(self.min_speed, min(self.max_speed, speed))
        # default: curve_factor
        speed = self.max_speed * (1.0 - self.speed_curve_factor * abs(steer))
        return max(self.min_speed, min(self.max_speed, speed))

    # _on_odom removed: kart has no speed sensor and ZED VIO speed was unreliable.
    # def _on_odom(self, msg: Odometry):
    #     pos = msg.pose.pose.position
    #     t = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
    #     if self._prev_odom_pos is not None and self._prev_odom_time is not None:
    #         dt = t - self._prev_odom_time
    #         if dt > 1e-6:
    #             dx = pos.x - self._prev_odom_pos[0]
    #             dy = pos.y - self._prev_odom_pos[1]
    #             dz = pos.z - self._prev_odom_pos[2]
    #             self._actual_speed = math.sqrt(dx*dx + dy*dy + dz*dz) / dt
    #     self._prev_odom_pos = (pos.x, pos.y, pos.z)
    #     self._prev_odom_time = t
    #     self.speed_pub.publish(Float32(data=self._actual_speed))

    # ── neural net loading ────────────────────────────────────────────

    def _load_neural_weights(self):
        """@brief Load neural network weights from a JSON file specified by the weights_json parameter."""
        path = str(self.get_parameter("weights_json").value)
        if not path:
            self.get_logger().error(
                f"controller_type={self.controller_type} but weights_json not set"
            )
            raise SystemExit(1)

        with open(path) as f:
            data = json.load(f)

        genes = np.array(data["genes"], dtype=np.float64)
        self.get_logger().info(
            f"Loaded {self.controller_type} weights from {path} "
            f"(fitness={data.get('fitness', '?')})"
        )

        if self.controller_type == "neural_v2":
            # 17→16→2: W1 (17×16), b1 (16), W2 (16×2), b2 (2) — 322 genes
            self._nn_input_size = 17
            self._nn_n_blue = 4
            self._nn_n_yellow = 4
            self._nn_uses_speed = True
            hs = 16
            i = 0
            self._nn_W1 = genes[i : i + 17 * hs].reshape(17, hs)
            i += 17 * hs
            self._nn_b1 = genes[i : i + hs]
            i += hs
            self._nn_W2 = genes[i : i + hs * 2].reshape(hs, 2)
            i += hs * 2
            self._nn_b2 = genes[i : i + 2]
        else:
            # 8→8→2: W1 (8×8), b1 (8), W2 (8×2), b2 (2) — 90 genes
            hs = 8
            i = 0
            self._nn_W1 = genes[i : i + 8 * hs].reshape(8, hs)
            i += 8 * hs
            self._nn_b1 = genes[i : i + hs]
            i += hs
            self._nn_W2 = genes[i : i + hs * 2].reshape(hs, 2)
            i += hs * 2
            self._nn_b2 = genes[i : i + 2]

    # ── detection callback ────────────────────────────────────────────

    def _on_detections(self, msg: Detection3DArray):
        """@brief Callback for 3D cone detections. Filters cones by FOV/range and runs the active controller.

        @param msg Detection3DArray in camera optical frame (Z=forward, X=right, Y=down).
        """
        self.last_detection_time = self.get_clock().now()
        self._received_detections = True
        self._detection_invalid = False
        self._last_target = None  # cleared each frame; set by the active controller

        cones = []
        for det in msg.detections:
            if not det.results:
                continue
            class_id = det.results[0].hypothesis.class_id
            pos = det.results[0].pose.pose.position
            if not (math.isfinite(pos.x) and math.isfinite(pos.z)):
                self._detection_invalid = True
                self.cmd_pub.publish(Twist())
                return
            fwd = pos.z
            left = -pos.x
            if fwd < 0.5:
                continue
            cones.append((class_id, fwd, left))

        nn_out = None
        if self.controller_type == "none":
            # Human steers. 0 here is a direct-PWM 0 (see _on_controller_type),
            # i.e. no drive to the steering motor, not a PID target of straight.
            steer = 0.0
        elif self.controller_type in ("neural", "neural_v2"):
            steer, _, nn_out = self._control_neural(cones)
        elif self.controller_type == "pure_pursuit":
            steer, _ = self._control_pure_pursuit(cones)
        elif self.controller_type == "mpc":
            steer, _ = self._control_mpc(cones)
        elif self.controller_type == "stanley":
            steer, _ = self._control_stanley(cones)
        else:
            steer, _ = self._control_geometric(cones)
        speed = self._compute_speed(steer, nn_out, cones)

        # Empty frames must match perception silence: only the explicit blind
        # mode keeps its throttle, and all modes command zero steering.
        if not cones:
            if self.speed_controller_type != "constant_throttle_blind":
                speed = 0.0
            steer = 0.0

        cmd = Twist()
        cmd.angular.z = steer
        cmd.linear.x = speed
        self.cmd_pub.publish(cmd)

        # Publish the controller-selected target for HUD rendering. This is
        # the SAME point the controller aims at, so the dashboard arrow cannot
        # disagree with the steering command.
        if self._last_target is not None:
            tp = PointStamped()
            tp.header = msg.header
            fwd, left = self._last_target
            tp.point.x = -left  # optical frame: x = right (negative of "left")
            tp.point.y = (
                0.0  # ground-level approx; HUD projects along the principal row
            )
            tp.point.z = fwd
            self.target_pub.publish(tp)

    # ── geometric controller ──────────────────────────────────────────

    def _control_geometric(self, cones):
        """@brief Geometric controller: steer toward nearest blue/yellow midpoint.

        @param cones List of (class_id, fwd, left) tuples in camera_link frame.
        @return Tuple of (steer_rad, speed_mps).
        """
        nearest_blue = None
        nearest_yellow = None
        min_bd = float("inf")
        min_yd = float("inf")

        for cls, fwd, left in cones:
            dist = math.hypot(fwd, left)
            if dist > self.lookahead_max:
                continue
            if cls == "blue_cone" and dist < min_bd:
                min_bd = dist
                nearest_blue = (fwd, left)
            elif cls == "yellow_cone" and dist < min_yd:
                min_yd = dist
                nearest_yellow = (fwd, left)

        if nearest_blue and nearest_yellow:
            mid_f = (nearest_blue[0] + nearest_yellow[0]) / 2.0
            mid_l = (nearest_blue[1] + nearest_yellow[1]) / 2.0
        elif nearest_blue:
            mid_f = nearest_blue[0]
            mid_l = nearest_blue[1] - self.half_track_width
        elif nearest_yellow:
            mid_f = nearest_yellow[0]
            mid_l = nearest_yellow[1] + self.half_track_width
        else:
            return self._last_steer, self.min_speed

        angle = math.atan2(mid_l, mid_f)
        steer = max(-self.max_steer, min(self.max_steer, self.steering_gain * angle))
        self._last_steer = steer
        self._last_target = (mid_f, mid_l)

        speed = self.max_speed * (1.0 - self.speed_curve_factor * abs(steer))
        speed = max(self.min_speed, min(self.max_speed, speed))

        self.get_logger().info(
            f"[geo] angle={math.degrees(angle):.1f}° steer={steer:.3f} "
            f"speed={speed:.1f} blue={nearest_blue} yellow={nearest_yellow}"
        )
        return steer, speed

    # ── pure pursuit controller ─────────────────────────────────────────

    def _control_pure_pursuit(self, cones):
        """@brief Pure pursuit: build centreline from cone pairs, follow with adaptive lookahead.

        Uses nearest-neighbor pairing in 2D (not just distance), sorts midpoints
        along the path by forward distance, interpolates a target at a speed-adaptive
        lookahead, and applies the pure pursuit steering law.

        @param cones List of (class_id, fwd, left) tuples in camera_link frame.
        @return Tuple of (steer_rad, speed_mps).
        """
        WHEELBASE = 1.05
        LOOKAHEAD_MIN = 3.0
        LOOKAHEAD_MAX = 5.0
        CONE_RANGE = 8.0  # only consider cones within this range

        blues = []
        yellows = []
        for cls, fwd, left in cones:
            if fwd < 0.3:
                continue
            if math.hypot(fwd, left) > CONE_RANGE:
                continue
            if cls == "blue_cone":
                blues.append((fwd, left))
            elif cls == "yellow_cone":
                yellows.append((fwd, left))

        blues.sort(key=lambda c: c[0])  # sort by forward distance
        yellows.sort(key=lambda c: c[0])

        # Pair blue/yellow cones by nearest 2D distance
        midpoints = []
        if blues and yellows:
            used_y = set()
            for bx, by in blues:
                best_j, best_dd = -1, float("inf")
                for j, (yx, yy) in enumerate(yellows):
                    if j in used_y:
                        continue
                    dd = math.hypot(bx - yx, by - yy)
                    if dd < best_dd:
                        best_dd = dd
                        best_j = j
                if best_j >= 0 and best_dd < 6.0:
                    yx, yy = yellows[best_j]
                    used_y.add(best_j)
                    midpoints.append(((bx + yx) / 2.0, (by + yy) / 2.0))
                else:
                    midpoints.append((bx, by - self.half_track_width))
            for j, (yx, yy) in enumerate(yellows):
                if j not in used_y:
                    midpoints.append((yx, yy + self.half_track_width))
        elif blues:
            for bx, by in blues:
                midpoints.append((bx, by - self.half_track_width))
        elif yellows:
            for yx, yy in yellows:
                midpoints.append((yx, yy + self.half_track_width))

        if not midpoints:
            return self._last_steer, self.min_speed

        # Sort by distance from kart (origin)
        midpoints.sort(key=lambda p: math.hypot(p[0], p[1]))

        # Adaptive lookahead from kart: shorter when turning, longer when straight
        abs_last_steer = abs(self._last_steer)
        steer_ratio = abs_last_steer / self.max_steer  # 0 = straight, 1 = max turn
        lookahead = LOOKAHEAD_MAX - (LOOKAHEAD_MAX - LOOKAHEAD_MIN) * steer_ratio

        # Pick the midpoint closest to the lookahead distance from the kart
        target_x, target_y = midpoints[0]
        best_diff = float("inf")
        for mx, my in midpoints:
            d = math.hypot(mx, my)
            diff = abs(d - lookahead)
            if diff < best_diff:
                best_diff = diff
                target_x, target_y = mx, my

        # Pure pursuit steering law
        alpha = math.atan2(target_y, target_x)
        ld = math.hypot(target_x, target_y)
        if ld < 0.5:
            ld = 0.5
        steer = math.atan2(2.0 * WHEELBASE * math.sin(alpha), ld)
        steer = self.steering_gain * steer
        steer = max(-self.max_steer, min(self.max_steer, steer))
        self._last_steer = steer
        self._last_target = (target_x, target_y)

        # Speed: same profile as geometric
        speed = self.max_speed * (1.0 - self.speed_curve_factor * abs(steer))
        speed = max(self.min_speed, min(self.max_speed, speed))

        self.get_logger().info(
            f"[pp] target=({target_x:.1f},{target_y:.1f}) ld={ld:.1f} "
            f"steer={steer:.3f} speed={speed:.1f} midpts={len(midpoints)}"
        )
        return steer, speed

    # ── MPC controller ────────────────────────────────────────────────

    def _control_mpc(self, cones):
        """@brief MPC: kinematic bicycle model over a receding horizon.

        Builds a midpoint reference path from paired blue/yellow cones,
        trims to mpc_lookahead metres, then minimises cross-track error,
        heading error, and steering rate via scipy SLSQP.

        @param cones List of (class_id, fwd, left) tuples in camera_link frame.
        @return Tuple of (steer_rad, speed_mps).
        """
        raw_midpoints = _build_midpoint_path(cones, self.half_track_width)

        if len(raw_midpoints) < 2:
            return self._last_steer, self.min_speed

        # Densify and trim path to mpc_lookahead distance
        # Camera (fwd, left) maps directly to bicycle model (x, y)
        # Interpolate between midpoints at ~1m spacing so the MPC has enough
        # reference points to detect curvature even with sparse cone pairs.
        dense_path: list[tuple[float, float]] = []
        INTERP_SPACING = 1.0  # metres between interpolated points
        for i in range(len(raw_midpoints)):
            mx, my = raw_midpoints[i]
            if i == 0:
                dense_path.append((mx, my))
                continue
            px, py = raw_midpoints[i - 1]
            seg_len = math.hypot(mx - px, my - py)
            n_interp = max(1, int(seg_len / INTERP_SPACING))
            for j in range(1, n_interp + 1):
                t = j / n_interp
                dense_path.append((px + t * (mx - px), py + t * (my - py)))

        # Trim to mpc_lookahead
        path: list[tuple[float, float]] = []
        cum = 0.0
        prev = (0.0, 0.0)
        for mx, my in dense_path:
            cum += math.hypot(mx - prev[0], my - prev[1])
            if cum > self.mpc_lookahead:
                break
            path.append((mx, my))
            prev = (mx, my)

        if len(path) < 2:
            return self._last_steer, self.min_speed

        # No speed sensor: plan MPC at max_speed as best-effort assumption.
        v = self.max_speed

        N = self.mpc_N
        dt = self.mpc_dt
        bounds = [(-self.max_steer, self.max_steer)] * N

        # Shifted warm-start from previous solution
        if self._mpc_prev_solution is not None and len(self._mpc_prev_solution) == N:
            u0 = np.empty(N)
            u0[:-1] = self._mpc_prev_solution[1:]
            u0[-1] = self._mpc_prev_solution[-1]
        else:
            u0 = np.full(N, self._last_steer)

        result = scipy_minimize(
            _mpc_cost,
            u0,
            args=(
                0.0,
                0.0,
                0.0,  # x0, y0, psi0 — always at local origin
                v,
                path,
                N,
                dt,
                self.WHEELBASE,
                self.mpc_w_cte,
                self.mpc_w_dsteer,
                self.mpc_w_heading,
                self._last_steer,
                self.max_steer,
            ),
            method="SLSQP",
            bounds=bounds,
            options={"maxiter": 60, "ftol": 1e-4},
        )

        self._mpc_prev_solution = result.x.copy()

        steer = float(np.clip(result.x[0], -self.max_steer, self.max_steer))
        self._last_steer = steer
        self._last_target = path[0] if path else None

        speed = self._compute_speed(steer)

        # Count cone types for debugging
        n_blue = sum(1 for c in cones if c[0] == "blue_cone")
        n_yellow = sum(1 for c in cones if c[0] == "yellow_cone")
        self.get_logger().info(
            f"[mpc] steer={steer:.3f} speed={speed:.1f} "
            f"pts={len(path)}/{len(raw_midpoints)} b={n_blue} y={n_yellow} "
            f"ok={result.success} itr={result.nit}"
        )
        return steer, speed

    # ── Stanley controller ────────────────────────────────────────────

    def _control_stanley(self, cones):
        """@brief Stanley controller: heading error + cross-track error, speed-normalized.

        Classical Stanley law:
            delta = theta_e + atan2(k * e_fa, v + ks)
        where theta_e is path-heading error, e_fa is signed cross-track error
        at the front axle, k is the cross-track gain, v is forward speed, and
        ks softens the term near zero speed.

        Origin Alberto Rodríguez Blanco (commit 7e66399, standalone node).
        Folded into the cone_follower dispatcher so it participates in the
        runtime Steering dropdown, /kart/target publisher, and speed-controller
        mux like the other algorithms.

        @param cones List of (class_id, fwd, left) tuples in camera_link frame.
        @return Tuple of (steer_rad, None) — outer _compute_speed sets speed.
        """
        midpoints = _build_midpoint_path(cones, self.half_track_width)
        if not midpoints:
            return self._last_steer, None

        # Closest path point to the kart (origin in its own frame)
        min_idx = 0
        min_dist = float("inf")
        for i, (px, py) in enumerate(midpoints):
            d = math.hypot(px, py)
            if d < min_dist:
                min_dist = d
                min_idx = i
        cx, cy = midpoints[min_idx]

        # Heading error: path tangent vs. kart heading (0 in its own frame)
        path_psi = _path_heading(midpoints, min_idx)
        theta_e = math.atan2(
            math.sin(path_psi), math.cos(path_psi)
        )  # normalize to [-pi, pi]

        # Cross-track error: signed distance from kart to path (project onto path normal)
        if min_idx < len(midpoints) - 1:
            dx = midpoints[min_idx + 1][0] - midpoints[min_idx][0]
            dy = midpoints[min_idx + 1][1] - midpoints[min_idx][1]
        elif min_idx > 0:
            dx = midpoints[min_idx][0] - midpoints[min_idx - 1][0]
            dy = midpoints[min_idx][1] - midpoints[min_idx - 1][1]
        else:
            dx, dy = 1.0, 0.0  # single-point path, straight-ahead fallback
        length = math.hypot(dx, dy)
        if length > 1e-3:
            nx, ny = -dy / length, dx / length  # left-pointing normal
        else:
            nx, ny = 0.0, 1.0
        e_fa = cx * nx + cy * ny

        # TODO: switch to real speed from the hall sensor on the kart-medulla
        # PCB once it's wired. Until then, Stanley assumes a constant speed —
        # self-normalization is lost but behaviour is predictable at the
        # operating speed we actually run at.
        v = self.stanley_assumed_speed
        ks = 0.5  # softening; literal since we don't run at zero speed

        cross_track_steer = math.atan2(self.stanley_k * e_fa, v + ks)
        steer = theta_e + cross_track_steer
        steer = max(-self.max_steer, min(self.max_steer, steer))

        self._last_steer = steer
        self._last_target = (cx, cy)

        self.get_logger().info(
            f"[stanley] steer={steer:.3f}({math.degrees(steer):.0f}°) "
            f"theta_e={theta_e:.2f} e_fa={e_fa:.2f} "
            f"target=({cx:.1f},{cy:.1f}) midpts={len(midpoints)}"
        )
        return steer, None

    # ── neural net controller ─────────────────────────────────────────

    def _control_neural(self, cones):
        """@brief Neural net controller: feed-forward network produces steer and speed.

        @param cones List of (class_id, fwd, left) tuples in camera_link frame.
        @return Tuple of (steer_rad, speed_mps).
        """
        blues = []
        yellows = []
        for cls, fwd, left in cones:
            dist = math.hypot(fwd, left)
            angle = math.atan2(left, fwd)
            if cls == "blue_cone":
                blues.append((dist, angle))
            elif cls == "yellow_cone":
                yellows.append((dist, angle))
        blues.sort()
        yellows.sort()

        nb = self._nn_n_blue
        ny = self._nn_n_yellow
        inp = np.zeros(self._nn_input_size)
        for j, (d, a) in enumerate(blues[:nb]):
            inp[j * 2] = d / 15.0
            inp[j * 2 + 1] = a / np.pi
        for j, (d, a) in enumerate(yellows[:ny]):
            inp[nb * 2 + j * 2] = d / 15.0
            inp[nb * 2 + j * 2 + 1] = a / np.pi
        if self._nn_uses_speed:
            # No speed sensor: feed 0.0 (neural_v2 was trained with this input).
            inp[-1] = 0.0

        hidden = np.tanh(inp @ self._nn_W1 + self._nn_b1)
        out = hidden @ self._nn_W2 + self._nn_b2

        steer = float(np.tanh(out[0])) * self._nn_max_steer
        speed = self._compute_speed(steer, out)

        self._last_steer = steer
        self.get_logger().info(
            f"[{self.controller_type}] steer={steer:.3f}({math.degrees(steer):.0f}°) "
            f"spd={speed:.1f} "
            f"b={len(blues)} y={len(yellows)} "
            f"out=[{out[0]:.2f},{out[1]:.2f}] inp={np.round(inp, 2).tolist()}"
        )
        return steer, speed, out

    # ── safety timeout ────────────────────────────────────────────────

    def _publish_sensor_safety(self):
        """Report required-input health independently of the commanded effort."""
        reason = ""
        requires_perception = (self.controller_type != "none" or
                               self.speed_controller_type not in ("zero", "constant_throttle_blind"))
        age = (self.get_clock().now() - self.last_detection_time).nanoseconds / 1e9
        if requires_perception and (not self._received_detections or
                                    not 0 <= age <= self.no_cone_timeout):
            reason = "Perception missing or stale"
        elif requires_perception and self._detection_invalid:
            reason = "Perception contains invalid coordinates"
        elif self.speed_controller_type == "constant_speed" and not self._speed_is_fresh():
            reason = "Speed measurement missing, invalid or stale"
        msg = String()
        msg.data = reason
        self._sensor_safety_pub.publish(msg)
        mode = Frame()
        mode.type = Frame.ORIN_STEER_MODE
        mode.payload = [1 if self.controller_type == "none" else 0]
        self._desired_mode_pub.publish(mode)
        if hasattr(self, "_config_pub"):
            config = String()
            config.data = json.dumps(dict(steering=self.controller_type,
                speed=self.speed_controller_type, max_speed=self.max_speed))
            self._config_pub.publish(config)

    def _safety_check(self):
        """@brief Timer callback: decide what to command when no detections are arriving.

        This node is driven by the detection callback, not merely gated by it — with
        no camera running, nothing else here ever publishes. So this timer is the only
        thing that speaks when perception is silent, and what it should say depends on
        the speed controller:

        - constant_throttle_blind wants to move anyway. It is the bench mode for
          checking the throttle wiring and the ESP32 path with no ZED, no cones and
          no perception nodes at all, so the timeout publishes the fixed throttle
          with the steering centred.
        - every other mode treats silence as a fault and gets zero.
        """
        elapsed = (self.get_clock().now() - self.last_detection_time).nanoseconds / 1e9
        if elapsed <= self.no_cone_timeout:
            return
        cmd = Twist()
        if self.speed_controller_type == "constant_throttle_blind":
            cmd.linear.x = self.max_speed
        else:
            cmd.linear.x = 0.0
        cmd.angular.z = 0.0
        self.cmd_pub.publish(cmd)


def main():
    """@brief Entrypoint for the cone follower node."""
    rclpy.init()
    node = ConeFollowerNode()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
