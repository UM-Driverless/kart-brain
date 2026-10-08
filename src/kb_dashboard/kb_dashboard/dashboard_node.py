#!/usr/bin/env python3
"""
kb_dashboard — Phone dashboard for kart telemetry and mission control.

Runs a WebSocket server alongside a ROS2 node. Any phone/browser on the
same network can open http://<orin-ip>/ to see live sensor values
and send commands (mission select, start/stop, EBS).
"""

import asyncio
import json
import threading
import time

import cv2
import numpy as np
import rclpy
from cv_bridge import CvBridge
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy

from kb_interfaces.msg import Frame
from sensor_msgs.msg import BatteryState, Image, Imu
from std_msgs.msg import Float32, String
from vision_msgs.msg import Detection3DArray

from geometry_msgs.msg import Twist

from kb_dashboard.protocol import (
    DashboardState,
    ORIN_COMPRESSOR_DISABLE,
    ORIN_STEER_MODE,
    ORIN_STEER_PID,
    decode_steering,
    decode_steering_raw,
    decode_speed,
    decode_accel,
    decode_braking,
    decode_throttle,
    decode_pedals,
    decode_health_flags,
    decode_health_data,
    decode_pneumatic,
    decode_steer_pid,
    encode_compressor_disable,
    encode_steer_mode,
    encode_steer_pid,
)
from kb_dashboard.server import run_websocket_server


class DashboardNode(Node):
    """@brief ROS2 node that bridges kart telemetry to a WebSocket-based dashboard.

    Subscribes to ESP32 telemetry, ZED IMU, YOLO FPS, HUD images, and state
    machine feedback, forwarding them to the dashboard web UI via DashboardState.
    Also publishes mission and manual control commands from the dashboard.
    """

    def __init__(self, state: DashboardState):
        """@brief Initialize the dashboard node with subscriptions and publishers.

        @param state Shared DashboardState instance for thread-safe telemetry exchange.
        """
        super().__init__("kb_dashboard")
        self.state = state
        # Port 80 so URLs need no port suffix. Non-root binding of ports <1024
        # requires: net.ipv4.ip_unprivileged_port_start=80 (sysctl, one line —
        # see .agents/orin-environment.md). Override with the ROS "port" param.
        self.declare_parameter("port", 80)
        self.declare_parameter("password", "0")
        self.port = self.get_parameter("port").value
        self.password = self.get_parameter("password").value
        self.declare_parameter("use_hall_speed", True)
        self.declare_parameter("hall_edges_per_metre", 0.0)
        self._use_halls = self.get_parameter("use_hall_speed").value
        self.state.configure_hall_speed(
            self.get_parameter("hall_edges_per_metre").value, self._use_halls)

        qos_reliable = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE)
        qos_best_effort = QoSProfile(
            depth=10, reliability=ReliabilityPolicy.BEST_EFFORT
        )

        # ESP32 → Orin telemetry
        self.create_subscription(
            Frame, "/esp32/heartbeat", self._on_heartbeat, qos_reliable
        )
        self.create_subscription(
            Frame, "/esp32/steering", self._on_esp_steering, qos_reliable
        )
        self.create_subscription(
            Frame, "/esp32/speed", self._on_esp_speed, qos_reliable
        )
        self.create_subscription(
            Frame, "/esp32/acceleration", self._on_esp_accel, qos_reliable
        )
        self.create_subscription(
            Frame, "/esp32/pedals", self._on_esp_pedals, qos_reliable
        )
        self.create_subscription(
            Frame, "/esp32/braking", self._on_esp_braking, qos_reliable
        )
        # kb_coms_micro splits the ESP32's health frame across these two topics.
        # Nothing publishes a bare /esp32/health, which is what this used to
        # subscribe to — so every health_* field sat at its default forever.
        self.create_subscription(
            Frame, "/esp32/health/flags", self._on_esp_health_flags, qos_reliable
        )
        self.create_subscription(
            Frame, "/esp32/health/data", self._on_esp_health_data, qos_reliable
        )
        self.create_subscription(
            Frame, "/esp32/pneumatic", self._on_esp_pneumatic, qos_reliable
        )
        self.create_subscription(
            Frame, "/esp32/steer_pid", self._on_esp_steer_pid, qos_reliable
        )

        # ZED2 IMU — uses BEST_EFFORT to match the ZED ROS2 wrapper's default QoS
        self.create_subscription(
            Imu, "/zed/zed_node/imu/data", self._on_zed_imu, qos_best_effort
        )

        # Battery — smart BMS over BLE (kb_bms node). Feeds the BATT gauge
        # independently of the ESP32 link.
        self.create_subscription(
            BatteryState, "/battery/state", self._on_battery, qos_reliable
        )
        # The same charge, estimated independently: kb_bms corrects the BMS's coulomb
        # count with pack voltage and publishes the result here. Both figures reach
        # the browser, which flags them when they disagree — see rcBatRingSub in
        # index.html for why they are shown side by side rather than merged.
        self.create_subscription(
            Float32,
            "/battery/soc_fused",
            lambda m: self.state.update("battery_soc_fused", round(m.data, 3)),
            qos_reliable,
        )

        # Pitch/roll-corrected cones from ground_plane_localizer_node — drives the
        # top-down (cenital) view in the default skin.
        self.create_subscription(
            Detection3DArray,
            "/perception/cones_3d_ground",
            self._on_cones_3d_ground,
            qos_reliable,
        )
        # Raw depth-localized cones from the YOLO pipeline. Fallback for the
        # top-down view when ground_plane_localizer isn't running (it only
        # exists in the ZED-OD validation launch) — same x=lateral, z=forward
        # reading in the optical frame.
        self.create_subscription(
            Detection3DArray,
            "/perception/cones_3d",
            self._on_cones_3d_raw,
            qos_reliable,
        )
        self._ground_seen_t = 0.0

        # Orin → ESP32 commands (to show what we're sending)
        self.create_subscription(
            Frame, "/orin/throttle", self._on_orin_throttle, qos_reliable
        )
        self.create_subscription(
            Frame, "/orin/brake", self._on_orin_brake, qos_reliable
        )
        self.create_subscription(
            Frame, "/orin/steering", self._on_orin_steering, qos_reliable
        )

        # ZED VIO speed (from cone_follower odom extraction)
        self.create_subscription(
            Float32, "/kart/speed", self._on_kart_speed, qos_reliable
        )

        # YOLO FPS
        self.create_subscription(
            Float32, "/perception/yolo/fps", self._on_yolo_fps, qos_reliable
        )

        # ESP32 steering-frame rate (Orin↔ESP USB-serial link health)
        self.create_subscription(
            Float32, "/esp32/fps", self._on_esp_fps, qos_reliable
        )

        # HUD image stream (JPEG bytes stored for WebSocket binary broadcast)
        self._bridge = CvBridge()
        self._hud_jpeg: bytes | None = None
        self.create_subscription(Image, "/perception/hud", self._on_hud_image, 1)

        # State machine feedback
        self.create_subscription(
            String, "/kart/safety_reason",
            lambda msg: self.state.update("safety_reason", msg.data), qos_reliable
        )
        self.create_subscription(
            String, "/kart/state", self._on_kart_state, qos_reliable
        )

        self.create_subscription(String, "/kart/bench_status", self._on_bench_status, qos_reliable)
        self._bench_mode_pub = self.create_publisher(String, "/dashboard/bench_mode", 10)
        self._bench_throttle_pub = self.create_publisher(String, "/dashboard/bench_throttle", 10)
        self._pending_bench_commands = []

        # Publishers for mission commands
        self.mission_pub = self.create_publisher(String, "/dashboard/mission", 10)
        self.state_cmd_pub = self.create_publisher(String, "/dashboard/state_cmd", 10)

        # Publisher for manual remote control (Twist for now)
        self.manual_cmd_pub = self.create_publisher(Twist, "/kart/cmd_vel_manual", 10)
        # Steering mode publisher (Frame to ESP32 via kb_coms_micro)
        self.steer_mode_pub = self.create_publisher(Frame, "/orin/steer_mode", 10)
        self._steer_mode = 0  # 0=PID, 1=direct PWM
        # Pending commands set from asyncio thread, published by ROS timer
        self._pending_manual_cmd = None
        self._manual_cmd_time = 0.0  # monotonic timestamp of last WS manual_control
        self._pending_mission = None
        self._pending_mission_count = 0
        self._pending_state_cmd = None
        self._pending_state_cmd_count = 0
        self._pending_steer_mode = None
        self._pending_steer_mode_count = 0
        # Live steering-PID tuning (Frame to ESP32 via kb_coms_micro)
        self.steer_pid_pub = self.create_publisher(Frame, "/orin/steer_pid", 10)
        self._pending_steer_pid = None
        self._pending_steer_pid_count = 0
        # EBS compressor operator latch (Frame to ESP32 via kb_coms_micro)
        self.compressor_disable_pub = self.create_publisher(Frame, "/orin/compressor_disable", 10)
        self._pending_compressor_disable = None
        self._pending_compressor_disable_count = 0
        self._compressor_disabled = False
        self._compressor_refresh_tick = 0
        # Controller type publishers (String to cone_follower)
        self.controller_type_pub = self.create_publisher(String, "/dashboard/controller_type", 10)
        self._pending_controller_type = None
        self._pending_controller_type_count = 0
        self.speed_controller_type_pub = self.create_publisher(String, "/dashboard/speed_controller_type", 10)
        self._pending_speed_controller_type = None
        self._pending_speed_controller_type_count = 0
        # Setpoint for the closed-loop speed controller, in m/s.
        self.target_speed_pub = self.create_publisher(Float32, "/dashboard/target_speed", 10)
        self._pending_target_speed = None
        self._pending_target_speed_count = 0
        self.create_timer(0.01, self._publish_pending)  # 100 Hz

        # One-shot self-test after 2 seconds
        self._selftest_timer = self.create_timer(2.0, self._selftest)

        self.get_logger().info(f"Dashboard node started, web UI on port {self.port}")

    def _on_bench_status(self, msg):
        try:
            self.state.update_bench_status(json.loads(msg.data))
        except (ValueError, TypeError):
            self.get_logger().warn("Invalid bench status")

    def publish_bench_mode(self, enabled):
        self._pending_bench_commands.append((self._bench_mode_pub, json.dumps(enabled)))

    def publish_bench_throttle(self, percent):
        self._pending_bench_commands.append((self._bench_throttle_pub, json.dumps(percent)))

    def _selftest(self):
        """@brief One-shot self-test: log subscriber counts for all publishers."""
        self._selftest_timer.cancel()
        pubs = {
            "/dashboard/mission": self.mission_pub,
            "/dashboard/state_cmd": self.state_cmd_pub,
            "/kart/cmd_vel_manual": self.manual_cmd_pub,
        }
        for topic, pub in pubs.items():
            subs = pub.get_subscription_count()
            if subs == 0:
                self.get_logger().warn(f"Self-test: {topic} has 0 subscribers")
            else:
                self.get_logger().info(f"Self-test: {topic} OK ({subs} subs)")

    def _on_heartbeat(self, msg: Frame):
        """@brief Callback for ESP32 heartbeat frames. Updates heartbeat timestamp.
        Also publishes any pending commands — this callback runs on the ROS thread
        so publish() is guaranteed to work (unlike timers which may not fire).
        """
        self.state.heartbeat()
        self._flush_pending()

    def _on_esp_steering(self, msg: Frame):
        """@brief Callback for ESP32 steering frames."""
        self._flush_pending()
        p = list(msg.payload)
        angle_rad, raw_encoder, pid_pwm, valid = decode_steering_raw(p)
        # Only publish an angle the firmware vouches for. When it does not, the
        # previous good value is left in place and the validity flag is what the
        # gauge keys off — so a dropout hides the needle rather than freezing it
        # at a stale reading that still looks live.
        self.state.update("esp32_steering_valid", valid)
        if valid:
            self.state.update("esp32_steering_rad", angle_rad)
            if raw_encoder > 0:
                self.state.update("esp32_steering_raw", raw_encoder)
        self.state.update("esp32_steering_pwm", pid_pwm)

    def _on_esp_speed(self, msg: Frame):
        """@brief Callback for ESP32 speed frames. Decodes speed in m/s."""
        if msg.payload and not self._use_halls:
            self.state.update("esp32_speed", decode_speed(list(msg.payload)))

    def _on_kart_speed(self, msg):
        """@brief Callback for ZED VIO speed (Float32, m/s). Updates same state key as ESP32 speed."""
        if not self._use_halls:
            self.state.update("esp32_speed", round(msg.data, 2))

    def _on_esp_accel(self, msg: Frame):
        """@brief Callback for ESP32 acceleration frames. Decodes lateral and longitudinal acceleration."""
        p = list(msg.payload)
        if p:
            lat, lon = decode_accel(p)
            self.state.update("esp32_accel_lat", lat)
            self.state.update("esp32_accel_lon", lon)

    def _on_esp_pedals(self, msg: Frame):
        """@brief Callback for ESP_PEDALS frames (accelerator + brake pedal ADCs).

        Feeds esp32_throttle (accelerator effort 0.0-1.0), esp32_brake_pedal,
        and the raw pin millivolts. See protocol.decode_pedals for the layout.
        """
        for key, value in decode_pedals(list(msg.payload)).items():
            self.state.update(key, value)

    def _on_esp_braking(self, msg: Frame):
        """@brief Callback for ESP32 braking frames. Decodes braking effort 0.0-1.0."""
        self.state.update("esp32_braking", decode_braking(list(msg.payload)))

    def _on_zed_imu(self, msg: Imu):
        """@brief Callback for ZED2 IMU data. Extracts linear acceleration (REP-103 convention)."""
        # ZED2 ROS2 wrapper uses REP-103: x=forward, y=left, z=up
        self.state.update("esp32_accel_lon", msg.linear_acceleration.x)
        self.state.update(
            "esp32_accel_lat", -msg.linear_acceleration.y
        )  # flip: y=left → positive=right

    @staticmethod
    def _det3d_to_cone_list(msg: Detection3DArray) -> list:
        """@brief Flatten a Detection3DArray to [{"x": lateral_m, "z": forward_m,
        "c": class_name}, ...] for the browser-side top-down renderer."""
        cones = []
        for det in msg.detections:
            if not det.results:
                continue
            p = det.bbox.center.position
            cones.append(
                {
                    "x": float(p.x),
                    "z": float(p.z),
                    "c": det.results[0].hypothesis.class_id,
                }
            )
        return cones

    def _on_cones_3d_ground(self, msg: Detection3DArray):
        """@brief Callback for IMU-corrected cone detections. Feeds the cenital view."""
        self._ground_seen_t = time.monotonic()
        self.state.update("cones_3d_ground", self._det3d_to_cone_list(msg))

    def _on_cones_3d_raw(self, msg: Detection3DArray):
        """@brief Callback for raw depth-localized cones. Feeds the cenital view
        only while the ground-corrected topic is silent (>1 s), so the corrected
        data always wins when ground_plane_localizer is running."""
        if time.monotonic() - self._ground_seen_t < 1.0:
            return
        self.state.update("cones_3d_ground", self._det3d_to_cone_list(msg))

    def _on_esp_health_flags(self, msg: Frame):
        """@brief Callback for the flag-bits half of the ESP32 health frame."""
        for k, v in decode_health_flags(list(msg.payload)).items():
            self.state.update(k, v)

    def _on_esp_health_data(self, msg: Frame):
        """@brief Callback for the numeric half of the ESP32 health frame."""
        decoded = decode_health_data(list(msg.payload))
        for k, v in decoded.items():
            self.state.update(k, v)
        self.state.hall_sample(decoded if "hall_init_err" in decoded else None)

    def _on_esp_steer_pid(self, msg: Frame):
        """@brief Callback for the ESP32's 1 Hz report of the steering gains in force.

        Purely a display path — nothing here re-sends or corrects anything. If the
        ESP32 reboots it comes back on the gains compiled into its firmware, and
        this node deliberately lets that stand rather than re-pushing the tuning
        (unlike the compressor latch, where the re-assert is what keeps the kart
        quiet). Reverting to the flashed gains is the safe direction, so the right
        behaviour is to show the operator it happened and let them decide.
        """
        for k, v in decode_steer_pid(list(msg.payload)).items():
            self.state.update(k, v)

    def _on_esp_pneumatic(self, msg: Frame):
        """@brief Callback for ESP32 pneumatics frames. Updates tank pressure + compressor state."""
        fields = decode_pneumatic(list(msg.payload))
        for k, v in fields.items():
            self.state.update(k, v)
        # Adopt a disable the ESP32 reports but this node does not know about.
        #
        # The two latches live in different places and die at different times: the ESP32
        # holds COMPRESSOR_DISABLED in RAM until it reboots, while this node holds its copy
        # until the kart-brain service restarts. Restart the service alone and the ESP32 is
        # still dutifully holding the compressor off while this node believes it is running.
        # The visible symptom is a button captioned "Disable compressor" sitting next to a
        # row reading DISABLED. The dangerous part is invisible: the 1 Hz re-assert below
        # only fires while _compressor_disabled is True, so in that state an ESP32 reboot
        # would quietly start the compressor with nobody having asked for it.
        #
        # Adoption is deliberately one-way. Telemetry may turn the latch ON, never OFF.
        # Clearing it on a non-3 state would break the opposite recovery: right after an
        # ESP32 reboot the firmware reports "running" precisely because it has forgotten the
        # operator's instruction, and that is the moment the re-assert has to insist, not
        # give up. So each side heals the other, and neither path can re-enable a compressor
        # that a human switched off — only pressing the button does that.
        if fields.get("esp32_compressor_state") == 3 and not self._compressor_disabled:
            self._compressor_disabled = True
            self.state.update("compressor_disabled", True)
            self.get_logger().info(
                "Compressor reported DISABLED by the ESP32 while this node thought it was "
                "enabled — adopting the latch so the button matches and the re-assert runs"
            )

    def _on_battery(self, msg: BatteryState):
        """@brief Callback for smart-BMS battery state (kb_bms over BLE).

        Feeds the Telemetry BATT gauge (voltage number + SOC dial) and the
        dedicated Battery tab (per-cell strip + current/temp/charge readouts).
        """
        self.state.update("battery_voltage", round(msg.voltage, 2))
        self.state.update("battery_soc", round(msg.percentage * 100.0))
        self.state.update("battery_current", round(msg.current, 2))
        self.state.update("battery_charge", round(msg.charge, 2))  # remaining Ah
        if msg.cell_temperature:
            temps = [round(t, 1) for t in msg.cell_temperature]
            self.state.update("battery_temp", temps[0])   # kept for existing gauge
            self.state.update("battery_temps", temps)     # all NTCs, Battery tab
        if msg.cell_voltage:
            self.state.update(
                "battery_cells_mv", [round(v * 1000.0) for v in msg.cell_voltage]
            )

    def _on_orin_throttle(self, msg: Frame):
        """@brief Callback for Orin-to-ESP32 throttle command echo."""
        self.state.update("orin_cmd_throttle", decode_throttle(list(msg.payload)))

    def _on_orin_brake(self, msg: Frame):
        """@brief Callback for Orin-to-ESP32 brake command echo."""
        self.state.update("orin_cmd_brake", decode_throttle(list(msg.payload)))

    def _on_orin_steering(self, msg: Frame):
        """@brief Callback for Orin-to-ESP32 steering command echo."""
        self.state.update("orin_cmd_steering_rad", decode_steering(list(msg.payload)))

    def _on_kart_state(self, msg: String):
        """@brief Callback for AS state machine updates. Maps AS states to dashboard state names."""
        # Map AS state names to dashboard state names for the UI
        as_to_dash = {
            "AS_OFF": "idle",
            "AS_READY": "ready",
            "AS_DRIVING": "running",
            "AS_FINISHED": "finished",
            "AS_EMERGENCY": "ebs",
        }
        self.state.update("state", as_to_dash.get(msg.data, "idle"))
        self.state.update("as_state", msg.data)

    def _on_yolo_fps(self, msg: Float32):
        """@brief Callback for YOLO inference FPS updates."""
        self.state.update("yolo_fps", round(msg.data, 1))

    def _on_esp_fps(self, msg: Float32):
        """@brief Callback for ESP32 steering-frame rate updates."""
        self.state.update("esp_fps", round(msg.data, 1))

    def _on_hud_image(self, msg: Image):
        """@brief Callback for HUD image. Converts to JPEG for WebSocket binary broadcast."""
        img = self._bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
        _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 60])
        self._hud_jpeg = buf.tobytes()

    def get_hud_jpeg(self) -> bytes | None:
        """@brief Get the latest HUD JPEG frame for WebSocket broadcast.

        @return JPEG bytes or None if no HUD image has been received yet.
        """
        return self._hud_jpeg

    def publish_mission(self, mission: str):
        """@brief Queue a mission selection for the ROS timer to publish."""
        msg = String()
        msg.data = mission
        self._pending_mission = msg
        self._pending_mission_count = 100  # publish for 1 second (100 Hz timer)
        self.get_logger().info(f"Mission set: {mission}")

    def publish_state_cmd(self, cmd: str):
        """@brief Queue a state command for the ROS timer to publish."""
        msg = String()
        msg.data = cmd
        self._pending_state_cmd = msg
        self._pending_state_cmd_count = 100
        self.get_logger().info(f"State cmd: {cmd}")

    def _publish_pending(self):
        """@brief Timer callback (100 Hz): publish pending commands from ROS thread."""
        self._flush_pending()

    def _flush_pending(self):
        """@brief Publish any pending commands. Safe to call from any ROS callback."""
        pending = getattr(self, "_pending_bench_commands", [])
        self._pending_bench_commands = []
        for pub, value in pending:
            msg = String(); msg.data = value; pub.publish(msg)
        cmd = self._pending_manual_cmd
        if cmd is not None:
            # If no WS input for 500ms, publish zeros (safe stop)
            if time.monotonic() - self._manual_cmd_time > 0.5:
                self._pending_manual_cmd = Twist()  # zero, not None — keep publishing zeros
            self.manual_cmd_pub.publish(self._pending_manual_cmd)
        if self._pending_mission is not None and self._pending_mission_count > 0:
            self.mission_pub.publish(self._pending_mission)
            self._pending_mission_count -= 1
        if self._pending_state_cmd is not None and self._pending_state_cmd_count > 0:
            self.state_cmd_pub.publish(self._pending_state_cmd)
            self._pending_state_cmd_count -= 1
        if self._pending_steer_mode is not None and self._pending_steer_mode_count > 0:
            self.steer_mode_pub.publish(self._pending_steer_mode)
            self._pending_steer_mode_count -= 1
        if self._pending_steer_pid is not None and self._pending_steer_pid_count > 0:
            self.steer_pid_pub.publish(self._pending_steer_pid)
            self._pending_steer_pid_count -= 1
        if self._pending_compressor_disable is not None and self._pending_compressor_disable_count > 0:
            self.compressor_disable_pub.publish(self._pending_compressor_disable)
            self._pending_compressor_disable_count -= 1
        # Keep re-asserting the disable at 1 Hz for as long as it is set, rather
        # than sending it once. The firmware's copy of this latch clears on reboot
        # (its object store zero-initialises, and 0 has to mean "compressor runs"
        # so a kart that reboots alone does not end up with no air). Without a
        # refresh, an ESP32 reset while someone is working on the kart would bring
        # the compressor back with no warning. Only the disable is repeated —
        # re-enabling is the firmware's own default, so it needs no upkeep.
        self._compressor_refresh_tick += 1
        if self._compressor_disabled and self._compressor_refresh_tick >= 100:  # 100 Hz timer → 1 Hz
            self._compressor_refresh_tick = 0
            frame = Frame()
            frame.type = ORIN_COMPRESSOR_DISABLE
            frame.payload = encode_compressor_disable(True)
            self.compressor_disable_pub.publish(frame)
        if self._pending_controller_type is not None and self._pending_controller_type_count > 0:
            self.controller_type_pub.publish(self._pending_controller_type)
            self._pending_controller_type_count -= 1
        if self._pending_speed_controller_type is not None and self._pending_speed_controller_type_count > 0:
            self.speed_controller_type_pub.publish(self._pending_speed_controller_type)
            self._pending_speed_controller_type_count -= 1
        if self._pending_target_speed is not None and self._pending_target_speed_count > 0:
            self.target_speed_pub.publish(self._pending_target_speed)
            self._pending_target_speed_count -= 1

    def publish_steer_mode(self, mode: int):
        """@brief Publish steering mode change to ESP32.

        @param mode 0=PID, 1=direct PWM.
        """
        self._steer_mode = mode
        self.state.update("steer_mode", "pwm" if mode else "pid")
        frame = Frame()
        frame.type = ORIN_STEER_MODE
        frame.payload = encode_steer_mode(mode)
        self._pending_steer_mode = frame
        self._pending_steer_mode_count = 100  # publish for 1s to ensure delivery
        self.get_logger().info(f"Steer mode: {'PWM' if mode else 'PID'}")

    def publish_steer_pid(self, kp: float, ki: float, kd: float, pwm_limit: float,
                          override: bool = True):
        """@brief Push steering PID gains to the ESP32 so a tune needs no reflash.

        Repeated for 1 s like the other one-shot commands, because a single frame
        can be lost and there is no acknowledgement. That is a repeat of one
        request, not a standing re-assert: once the burst ends nothing keeps
        sending, so an ESP32 that reboots later comes back on its flashed gains
        and stays there. The dashboard finds out from the ESP_STEER_PID echo.

        @param kp Proportional gain.
        @param ki Integral gain.
        @param kd Derivative gain.
        @param pwm_limit Steering actuator output ceiling, 0.0-1.0.
        @param override False restores the gains compiled into the firmware.
        """
        frame = Frame()
        frame.type = ORIN_STEER_PID
        frame.payload = encode_steer_pid(kp, ki, kd, pwm_limit, override)
        self._pending_steer_pid = frame
        self._pending_steer_pid_count = 100  # publish for 1s to ensure delivery
        if override:
            self.get_logger().info(
                f"Steering PID: kp={kp:.3f} ki={ki:.3f} kd={kd:.3f} limit={pwm_limit:.2f}"
            )
        else:
            self.get_logger().info("Steering PID: restoring firmware defaults")

    def publish_compressor_disable(self, disabled: bool):
        """@brief Publish the EBS compressor operator latch to the ESP32.

        Setting it stops the compressor and, via the firmware interlock, opens the
        shutdown circuit — the kart cannot be quiet and armed at the same time.

        @param disabled True to stop the compressor and force emergency.
        """
        self._compressor_disabled = disabled
        self.state.update("compressor_disabled", disabled)
        frame = Frame()
        frame.type = ORIN_COMPRESSOR_DISABLE
        frame.payload = encode_compressor_disable(disabled)
        self._pending_compressor_disable = frame
        self._pending_compressor_disable_count = 100  # publish for 1s to ensure delivery
        self.get_logger().warn(
            "EBS compressor DISABLED — shutdown circuit forced open" if disabled
            else "EBS compressor re-enabled"
        )

    def publish_controller_type(self, ctrl_type: str):
        """@brief Publish controller type change to cone_follower.

        @param ctrl_type One of: geometric, pure_pursuit, stanley, neural_v2, mpc,
               none ("none" leaves the steering motor unpowered so a human steers).
        """
        msg = String()
        msg.data = ctrl_type
        self._pending_controller_type = msg
        self._pending_controller_type_count = 100
        self.get_logger().info(f"Controller type: {ctrl_type}")

    def publish_speed_controller_type(self, speed_type: str):
        """@brief Publish speed controller type change to cone_follower.

        @param speed_type One of: curve_factor, constant_throttle,
               constant_throttle_stop, neural_v2, zero.
        """
        msg = String()
        msg.data = speed_type
        self._pending_speed_controller_type = msg
        self._pending_speed_controller_type_count = 100
        self.get_logger().info(f"Speed controller type: {speed_type}")

    def publish_target_speed(self, speed_mps: float):
        """@brief Publish the closed-loop speed setpoint to cone_follower.

        @param speed_mps Setpoint in m/s. The dashboard shows km/h and converts.
        """
        msg = Float32()
        msg.data = float(speed_mps)
        self._pending_target_speed = msg
        self._pending_target_speed_count = 100
        self.get_logger().info(f"Target speed: {speed_mps:.2f} m/s")

    def publish_manual_control(
        self, steer: float, steer_type: str, throttle: float, brake: float
    ):
        """@brief Store remote control command for publishing by the ROS timer.

        Called from the asyncio thread. The actual publish happens in _publish_pending_manual
        on the ROS spin thread to avoid thread-safety issues.

        @param steer Steering input, -1.0 (left) to 1.0 (right).
        @param steer_type Steering mode string (e.g. "angle", "pwm").
        @param throttle Throttle input, 0.0 to 1.0.
        @param brake Brake input, 0.0 to 1.0.
        """
        NOMINAL_MAX_SPEED = 5.0
        NOMINAL_MAX_STEER = 1.222  # radians (~70 deg)

        cmd = Twist()
        cmd.linear.x = (throttle - brake) * NOMINAL_MAX_SPEED
        if self._steer_mode == 1:
            # Direct PWM mode: pass joystick [-1, 1] through; ESP32 outputLimit clamps
            cmd.angular.z = steer
        else:
            # PID mode: scale to radians
            cmd.angular.z = steer * NOMINAL_MAX_STEER
        self._pending_manual_cmd = cmd
        self._manual_cmd_time = time.monotonic()


# ── Entrypoint ─────────────────────────────────────────────────────────


def main(args=None):
    """@brief Entrypoint: spins ROS in a background thread and runs the async web server."""
    rclpy.init(args=args)
    state = DashboardState()
    node = DashboardNode(state)

    # Run ROS spinning in a background thread
    spin_thread = threading.Thread(target=rclpy.spin, args=(node,), daemon=True)
    spin_thread.start()

    # Run the async web server in the main thread
    import signal

    def _shutdown_on_signal(*_):
        """Stop the asyncio event loop so the process exits on SIGTERM."""
        try:
            loop = asyncio.get_running_loop()
            loop.call_soon_threadsafe(loop.stop)
        except RuntimeError:
            pass

    signal.signal(signal.SIGTERM, _shutdown_on_signal)
    try:
        asyncio.run(run_websocket_server(state, node, node.port, password=node.password))
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
