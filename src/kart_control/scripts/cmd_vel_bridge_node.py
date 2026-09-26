#!/usr/bin/env python3
"""Bridge from Twist (cone_follower output) to ESP32 serial frames.

Subscribes to /kart/cmd_vel (Twist) and publishes kb_interfaces/Frame
messages on /orin/throttle, /orin/brake, /orin/steering — the topics
that kb_coms_micro subscribes to and relays over UART to the ESP32.

Payload encoding: int32 binary (steering x1000, throttle/brake x255).
"""

import math
import time

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from std_msgs.msg import String
from safety_supervisor import COMMAND_TIMEOUT
from kb_interfaces.msg import Frame
from kb_dashboard.protocol import encode_steering, encode_throttle, encode_braking, ORIN_STEER_MODE


class CmdVelBridgeNode(Node):
    """@brief Bridge from Twist commands to ESP32 Frame messages.

    Converts /kart/cmd_vel_muxed Twist into throttle, brake, and steering
    Frame messages on /orin/* topics for kb_coms_micro to relay to the ESP32.
    Publishes at a configurable rate (default 100 Hz).
    """

    def __init__(self):
        """@brief Initialize the bridge with parameters, publishers, and subscriber."""
        super().__init__("cmd_vel_bridge")

        self.declare_parameter("input_topic", "/kart/cmd_vel_muxed")
        self.declare_parameter("rate_hz", 100.0)
        self.declare_parameter("max_speed", 5.0)
        self.declare_parameter("max_steer", 1.222)

        in_topic = str(self.get_parameter("input_topic").value)
        rate = float(self.get_parameter("rate_hz").value)
        self.max_speed = float(self.get_parameter("max_speed").value)
        self.max_steer = float(self.get_parameter("max_steer").value)

        # Publish to the /orin/* topics that kb_coms_micro subscribes to
        self.throttle_pub = self.create_publisher(Frame, "/orin/throttle", 10)
        self.brake_pub = self.create_publisher(Frame, "/orin/brake", 10)
        self.steering_pub = self.create_publisher(Frame, "/orin/steering", 10)

        self.sub = self.create_subscription(Twist, in_topic, self._on_cmd, 10)
        self.create_subscription(Frame, "/orin/steer_mode", self._on_steer_mode, 10)

        self._last_cmd_time = None
        self._seen_command = False
        self._emergency_pub = self.create_publisher(String, "/dashboard/state_cmd", 10)
        self._mode_pub = self.create_publisher(Frame, "/orin/steer_mode", 10)
        self._throttle_effort = 0.0
        self._brake_effort = 0.0
        self._steer_rad = 0.0
        self._steer_mode = 0  # 0=PID, 1=direct PWM

        self.timer = self.create_timer(1.0 / rate, self._send_frames)
        self.get_logger().info(f"CmdVelBridge: {in_topic} @ {rate} Hz")

    def _on_steer_mode(self, msg: Frame):
        """@brief Callback for steer mode changes. Updates local mode flag."""
        if msg.payload:
            self._steer_mode = int(msg.payload[0])

    def _on_cmd(self, msg: Twist):
        """@brief Callback for incoming Twist commands. Splits into throttle/brake and clamps steering.

        @param msg Twist with linear.x as speed (m/s) and angular.z as steering (rad or PWM).
        """
        speed = msg.linear.x
        steer = msg.angular.z

        self._seen_command = True
        if not (math.isfinite(speed) and math.isfinite(steer)):
            self._last_cmd_time = None
            self._throttle_effort = self._brake_effort = self._steer_rad = 0.0
            return
        self._last_cmd_time = time.monotonic()

        # Throttle / brake from speed
        if speed >= 0:
            self._throttle_effort = min(1.0, speed / self.max_speed)
            self._brake_effort = 0.0
        else:
            self._throttle_effort = 0.0
            self._brake_effort = min(1.0, -speed / self.max_speed)

        if self._steer_mode == 1:
            # Direct PWM: clamp to [-1, 1]
            self._steer_rad = max(-1.0, min(1.0, steer))
        else:
            # PID angle: clamp to max_steer
            self._steer_rad = max(-self.max_steer, min(self.max_steer, steer))

    def _send_frames(self):
        """@brief Timer callback: publish current throttle, brake, and steering as Frame messages."""
        now = time.monotonic()
        if self._last_cmd_time is None or not 0 <= now - self._last_cmd_time <= COMMAND_TIMEOUT:
            self._throttle_effort = self._brake_effort = self._steer_rad = 0.0
            mode = Frame()
            mode.type = ORIN_STEER_MODE
            mode.payload = [1]  # zero must unpower steering, not steer to centre
            self._mode_pub.publish(mode)
            if self._seen_command:
                emergency = String()
                emergency.data = "ebs"
                self._emergency_pub.publish(emergency)

        throttle_frame = Frame()
        throttle_frame.type = Frame.ORIN_TARG_THROTTLE
        throttle_frame.payload = encode_throttle(self._throttle_effort)

        brake_frame = Frame()
        brake_frame.type = Frame.ORIN_TARG_BRAKING
        brake_frame.payload = encode_braking(self._brake_effort)

        steer_frame = Frame()
        steer_frame.type = Frame.ORIN_TARG_STEERING
        steer_frame.payload = encode_steering(self._steer_rad)

        self.throttle_pub.publish(throttle_frame)
        self.brake_pub.publish(brake_frame)
        self.steering_pub.publish(steer_frame)


def main():
    """@brief Entrypoint for the cmd_vel bridge node."""
    rclpy.init()
    node = CmdVelBridgeNode()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
