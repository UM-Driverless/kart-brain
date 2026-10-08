#!/usr/bin/env python3
"""State machine node — gates cmd_vel based on mission and AS state.

Subscribes to dashboard commands and muxes autonomous/manual cmd_vel
to /kart/cmd_vel_muxed, which cmd_vel_bridge reads.

Transitions live in state_logic.py; safety_supervisor.py gates them on fresh
inputs and firmware permission. Both are pure Python, tested without ROS. This node
is plumbing only: subscribe, delegate, publish.

States follow Formula Student AS (Autonomous System) conventions:
  AS_OFF(0) → AS_READY(1) → AS_DRIVING(2) → AS_FINISHED(3)
  Any state (except AS_OFF) → AS_EMERGENCY(4)
"""

import json
import math
import os
import time

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from std_msgs.msg import String
from rcl_interfaces.srv import GetParameters, SetParameters
from rclpy.parameter import Parameter
from kb_interfaces.msg import Frame

from state_logic import STATE_NAMES, AS_OFF, AS_EMERGENCY
from safety_supervisor import SafetySupervisor


class StateMachineNode(Node):
    """@brief State machine node that gates cmd_vel based on mission and AS state.

    Wraps SafetySupervisor and muxes autonomous/manual cmd_vel to
    /kart/cmd_vel_muxed at 100 Hz. Publishes state heartbeat at 10 Hz.
    """

    def __init__(self):
        """@brief Initialize the state machine in AS_OFF with subscriptions, publishers, and timers."""
        super().__init__("state_machine")

        self._logic = SafetySupervisor(bench_throttle=os.environ.get("KART_BENCH_THROTTLE") == "1")
        self._last_auto_cmd = Twist()
        self._last_manual_cmd = Twist()
        self._last_forced_steer_mode = None

        self._bench_status_pub = self.create_publisher(String, "/kart/bench_status", 10)
        self._bridge_params = self.create_client(GetParameters, "/cmd_vel_bridge/get_parameters")
        self._controller_params = self.create_client(SetParameters, "/cone_follower/set_parameters")
        self._bench_zeroed = not self._logic.bench_throttle
        self._bench_zero_ack = False
        self._bench_request_time = None
        self.create_timer(1.0, self._bench_poll)

        # Subscriptions
        self.create_subscription(String, "/dashboard/mission", self._on_mission, 10)
        self.create_subscription(String, "/dashboard/state_cmd", self._on_state_cmd, 10)
        self.create_subscription(Twist, "/kart/cmd_vel", self._on_auto_cmd, 10)
        self.create_subscription(Twist, "/kart/cmd_vel_manual", self._on_manual_cmd, 10)

        self.create_subscription(Frame, "/esp32/safety", self._on_safety, 10)
        self.create_subscription(String, "/kart/controller_safety", self._on_controller_safety, 10)
        self.create_subscription(Frame, "/kart/controller_steer_mode", self._on_controller_mode, 10)

        self.create_subscription(String, "/kart/controller_config", self._on_controller_config, 10)
        self.create_subscription(String, "/dashboard/bench_mode", self._on_bench_mode, 10)
        self.create_subscription(String, "/dashboard/bench_throttle", self._on_bench_throttle, 10)

        # Publishers
        self._muxed_pub = self.create_publisher(Twist, "/kart/cmd_vel_muxed", 10)
        self._state_pub = self.create_publisher(String, "/kart/state", 10)
        self._machine_state_pub = self.create_publisher(Frame, "/orin/machine_state", 10)
        self._mission_pub = self.create_publisher(Frame, "/orin/mision", 10)
        self._steer_mode_pub = self.create_publisher(Frame, "/orin/steer_mode", 10)

        self._safety_reset_pub = self.create_publisher(Frame, "/orin/safety_reset", 10)
        self._safety_reason_pub = self.create_publisher(String, "/kart/safety_reason", 10)

        # 100 Hz mux timer
        self.create_timer(0.01, self._mux_tick)
        # 10 Hz state heartbeat
        self.create_timer(0.1, self._publish_state)

        self.get_logger().info("StateMachine: started in AS_OFF")

    # ── Subscriptions ──────────────────────────────────────────────────

    def _on_controller_config(self, msg):
        try:
            config = json.loads(msg.data)
            if (type(config) is not dict or type(config.get("max_speed")) not in (int, float)
                    or not math.isfinite(config["max_speed"]) or config["max_speed"] < 0
                    or not isinstance(config.get("steering"), str) or not isinstance(config.get("speed"), str)):
                raise ValueError("invalid controller settings")
            self._logic.bench_config = config
            self._logic.bench_config_time = time.monotonic()
            if self._bench_zero_ack and config["max_speed"] == 0:
                self._bench_zeroed = self._logic.bench_initialized = True
        except (ValueError, TypeError):
            self._logic.bench_config = None

    def _on_bench_mode(self, msg):
        try:
            enabled = json.loads(msg.data)
        except (ValueError, TypeError):
            enabled = None
        self._logic.set_bench_mode(enabled, time.monotonic())
        self._publish_state()

    def _on_bench_throttle(self, msg):
        try:
            percent = json.loads(msg.data)
        except (ValueError, TypeError):
            percent = None
        now = time.monotonic()
        reason = self._logic.validate_bench_throttle(percent, now)
        if reason:
            self._logic.bench_notice = reason
            return
        self._apply_bench_speed(self._logic.bridge_scale * percent / 100)

    def _apply_bench_speed(self, speed):
        if not self._controller_params.service_is_ready():
            self._logic.bench_notice = "Controller parameter service unavailable"
            return
        self._logic.bench_pending = True
        self._logic._arm_requested = False
        if self._logic.state != AS_EMERGENCY:
            self._logic.logic.state = AS_OFF
        self._bench_request_time = time.monotonic()
        req = SetParameters.Request()
        req.parameters = [Parameter("max_speed", value=float(speed)).to_parameter_msg()]
        def done(future):
            try:
                if len(future.result().results) != 1 or not future.result().results[0].successful:
                    raise ValueError("Controller refused throttle")
                if speed == 0:
                    self._bench_zero_ack = True
                self._logic.bench_notice = ""
            except Exception:
                self._logic.set_bench_mode(False, time.monotonic())
                self._logic.bench_notice = "Throttle update failed; bench mode OFF"
            self._logic.bench_pending = False
            self._logic._arm_requested = self._logic.bench_mode_enabled
        self._controller_params.call_async(req).add_done_callback(done)

    def _bench_poll(self):
        now = time.monotonic()
        if self._logic.bench_pending and now - self._bench_request_time > 2:
            self._logic.set_bench_mode(False, now)
            self._logic.bench_notice = "Throttle update timed out; bench mode OFF"
            # Do not permit a second write while the first service call is pending.
        if self._bridge_params.service_is_ready():
            req = GetParameters.Request(); req.names = ["max_speed"]
            def done(future):
                try:
                    scale = future.result().values[0].double_value
                    if not math.isfinite(scale) or scale <= 0:
                        raise ValueError("invalid bridge scale")
                    self._logic.bridge_scale = scale
                    self._logic.bridge_scale_time = time.monotonic()
                except Exception:
                    self._logic.bridge_scale = None
            self._bridge_params.call_async(req).add_done_callback(done)
        if not self._bench_zeroed and not self._logic.bench_pending and self._logic.state in (AS_OFF, AS_EMERGENCY):
            self._apply_bench_speed(0.0)

    def _on_mission(self, msg: String):
        """@brief Callback for mission selection from the dashboard. Triggers state transitions.

        @param msg String message with the mission name.
        """
        old_mission = self._logic.mission
        old_state = self._logic.state
        new_state = self._logic.on_mission(msg.data)
        if old_mission == self._logic.mission and new_state is None:
            return
        self.get_logger().info(f"Mission: {old_mission} → {self._logic.mission}")
        if new_state is not None:
            self._log_transition(old_state, new_state)
            self._publish_state()
        else:
            self._publish_mission_frame()

    def _on_state_cmd(self, msg: String):
        """@brief Callback for state commands (start, stop, ebs, finish, reset).

        @param msg String message with the command.
        """
        old_state = self._logic.state
        new_state, force_pid = self._logic.on_state_cmd(msg.data, time.monotonic())
        if new_state is None:
            self.get_logger().warn(
                f"Ignored cmd '{msg.data}' in state {STATE_NAMES[old_state]}"
            )
            return
        self._log_transition(old_state, new_state)
        if force_pid:
            # None steering stays unpowered even during the Start transition.
            self._publish_steer_mode(self._logic.driving_steer_mode())
        self._publish_state()

    def _on_controller_mode(self, msg: Frame):
        mode = msg.payload[0] if len(msg.payload) == 1 else None
        self._logic.on_controller_mode(mode, time.monotonic())

    def _on_controller_safety(self, msg: String):
        self._logic.on_controller_safety(msg.data, time.monotonic())

    def _on_safety(self, msg: Frame):
        self._logic.on_safety(list(msg.payload), time.monotonic())

    def _on_auto_cmd(self, msg: Twist):
        """@brief Callback for autonomous cmd_vel. Stores latest command for muxing."""
        self._last_auto_cmd = msg
        self._logic.note_command("auto", (msg.linear.x, msg.angular.z), time.monotonic())

    def _on_manual_cmd(self, msg: Twist):
        """@brief Callback for manual (remote control) cmd_vel. Stores latest command for muxing."""
        self._last_manual_cmd = msg
        self._logic.note_command("manual", (msg.linear.x, msg.angular.z), time.monotonic())

    # ── Muxing (100 Hz) ───────────────────────────────────────────────

    def _mux_tick(self):
        """@brief Timer callback (100 Hz): mux autonomous or manual cmd_vel based on mission and state."""
        now = time.monotonic()
        old_state = self._logic.state
        self._logic.tick(now)
        if self._logic.state != old_state:
            self._log_transition(old_state, self._logic.state)
            self._publish_state()
        linear_x, angular_z = self._logic.mux(
            (self._last_auto_cmd.linear.x, self._last_auto_cmd.angular.z),
            (self._last_manual_cmd.linear.x, self._last_manual_cmd.angular.z),
            now,
        )
        out = Twist()
        out.linear.x = linear_x
        out.angular.z = angular_z
        self._muxed_pub.publish(out)

    # ── Publishers ─────────────────────────────────────────────────────

    def _log_transition(self, old_state: int, new_state: int):
        """@brief Log an AS state transition."""
        self.get_logger().info(
            f"State: {STATE_NAMES[old_state]} → {STATE_NAMES[new_state]}"
        )

    def _publish_state(self):
        """@brief Publish current AS state, as a name to /kart/state and as a Frame to the ESP32.

        Called on every transition and from the 10 Hz timer. The Frame used to go
        out only on transitions, which was fine while nothing acted on it. The
        ESP32 now gates its shutdown circuit on this value — it closes the chain
        only while the state is AS_READY or AS_DRIVING — so a single dropped frame
        would have left the firmware's copy wrong until the next transition, with
        no way for either side to notice. Re-sending it continuously means the
        firmware's view expires and is refreshed rather than being latched from one
        lucky delivery.
        """
        msg = String()
        msg.data = STATE_NAMES[self._logic.state]
        self._state_pub.publish(msg)
        self._publish_state_frame()
        # The ESP32 boots in Manual. Repeat the selected mission so reconnects
        # and lost selection frames cannot leave its throttle mux on the pedal.
        self._publish_mission_frame()
        bench = String()
        bench.data = json.dumps(self._logic.bench_snapshot(time.monotonic()))
        self._bench_status_pub.publish(bench)
        reason = String()
        reason.data = self._logic.reason(time.monotonic())
        self._safety_reason_pub.publish(reason)
        if (self._logic.pending_reset is not None
                and time.monotonic() - self._logic.reset_time >= 0.2):
            reset = Frame()
            reset.type = Frame.ORIN_SAFETY_RESET
            reset.payload = [self._logic.pending_reset]
            self._safety_reset_pub.publish(reset)

        # Hold the steering actuator unpowered while armed but not driving:
        # direct-PWM mode makes the mux's zero Twist mean "no drive" instead of
        # "drive to centre and hold". Re-asserted at 10 Hz so a dashboard mode
        # toggle cannot silently re-power the motor before Start.
        idle_mode = self._logic.heartbeat_steer_mode()
        if idle_mode is not None:
            self._publish_steer_mode(idle_mode)

    def _publish_state_frame(self):
        """@brief Publish current AS state as a Frame to /orin/machine_state for the ESP32."""
        frame = Frame()
        frame.type = Frame.ORIN_MACHINE_STATE
        frame.payload = [self._logic.state]
        self._machine_state_pub.publish(frame)

    def _publish_steer_mode(self, mode: int):
        """@brief Publish steering mode to cmd_vel_bridge. 0=PID, 1=direct PWM."""
        frame = Frame()
        frame.type = 0x29  # ORIN_STEER_MODE
        frame.payload = [mode]
        self._steer_mode_pub.publish(frame)
        if mode != self._last_forced_steer_mode:  # called at 10 Hz — log changes only
            self._last_forced_steer_mode = mode
            self.get_logger().info(f"Steer mode forced: {'PID' if mode == 0 else 'PWM'}")

    def _publish_mission_frame(self):
        """@brief Publish current mission ID as a Frame to /orin/mission for the ESP32."""
        mission_id = self._logic.mission_id
        frame = Frame()
        frame.type = Frame.ORIN_MISION
        frame.payload = [mission_id]
        self._mission_pub.publish(frame)


def main():
    """@brief Entrypoint for the state machine node."""
    rclpy.init()
    node = StateMachineNode()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
