"""Integration test: the real state_machine node, exercised over ROS topics.

Needs ROS 2 — runs on the VM or Orin, not the Mac:
    cd ~/kart-brain && colcon build --packages-select kart_control kb_interfaces
    source install/setup.bash
    python3 -m pytest src/kart_control/test/test_state_machine_launch.py -q

Unlike test_state_logic.py (pure logic, runs anywhere), this checks the
plumbing: that the dashboard-facing topics reach the logic and that the frames
kb_coms_micro relays to the ESP32 — /kart/cmd_vel_muxed, /orin/steer_mode —
actually carry what the logic decided. The scenario is the 2026-08-10 incident:
select an autonomous mission, do NOT press Start, and verify nothing that
powers the steering motor goes out.
"""

import subprocess
import sys
import time
from pathlib import Path

import pytest

rclpy = pytest.importorskip("rclpy", reason="needs ROS 2 (run on the VM or Orin)")

from geometry_msgs.msg import Twist  # noqa: E402
from std_msgs.msg import String  # noqa: E402
from kb_interfaces.msg import Frame  # noqa: E402

NODE_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "state_machine_node.py"
STEER_MODE_PWM = 1


class Harness:
    """Publishes as the dashboard, records what the node emits."""

    def __init__(self, node):
        self.node = node
        self.mission_pub = node.create_publisher(String, "/dashboard/mission", 10)
        self.cmd_pub = node.create_publisher(String, "/dashboard/state_cmd", 10)
        self.auto_pub = node.create_publisher(Twist, "/kart/cmd_vel", 10)
        self.mission_id = 8
        self.controller_mode = 0
        self.safety_enabled = True
        self.safety_faults = 0
        self.safety_latch = 0
        self.reset_ack = 0
        self.auto_cmd = None
        self.safety_pub = node.create_publisher(Frame, "/esp32/safety", 10)
        self.mode_pub = node.create_publisher(Frame, "/kart/controller_steer_mode", 10)
        self.controller_pub = node.create_publisher(String, "/kart/controller_safety", 10)
        node.create_subscription(Frame, "/orin/safety_reset", self.reset, 10)
        node.create_timer(0.05, self.feed_inputs)
        self.muxed = []
        self.steer_modes = []
        self.states = []
        node.create_subscription(Twist, "/kart/cmd_vel_muxed", self.muxed.append, 10)
        node.create_subscription(Frame, "/orin/steer_mode", self.steer_modes.append, 10)
        node.create_subscription(String, "/kart/state", self.states.append, 10)

    def reset(self, msg):
        if not self.safety_faults:
            self.reset_ack = msg.payload[0]
            self.safety_latch = 0

    def feed_inputs(self):
        if self.safety_enabled:
            frame = Frame()
            frame.type = Frame.ESP_SAFETY_STATUS
            flags = 2 if self.safety_latch else (0 if self.safety_faults else 1)
            frame.payload = [1, self.safety_faults, self.safety_latch, flags, self.reset_ack, self.mission_id]
            self.safety_pub.publish(frame)
            self.controller_pub.publish(String(data=""))
            mode = Frame()
            mode.type = Frame.ORIN_STEER_MODE
            mode.payload = [self.controller_mode]
            self.mode_pub.publish(mode)
        if self.auto_cmd is not None:
            self.auto_pub.publish(self.auto_cmd)

    def spin(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            rclpy.spin_once(self.node, timeout_sec=0.05)

    def wait_until(self, predicate, timeout=8.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.spin(0.05)
            if predicate():
                return
        assert predicate(), "ROS discovery or expected state timed out"

    def send(self, pub, msg_type, data):
        msg = msg_type()
        msg.data = data
        pub.publish(msg)


@pytest.fixture
def harness():
    rclpy.init()
    proc = subprocess.Popen([sys.executable, str(NODE_SCRIPT)])
    node = rclpy.create_node("state_machine_test_harness")
    h = Harness(node)
    h.wait_until(lambda: bool(h.states) and h.mission_pub.get_subscription_count() > 0
                 and h.cmd_pub.get_subscription_count() > 0)
    yield h
    proc.terminate()
    proc.wait(timeout=5)
    node.destroy_node()
    rclpy.shutdown()


def test_mission_select_without_start_keeps_steering_unpowered(harness):
    # A live autonomous command is present the whole time — it must be gated.
    auto = Twist()
    auto.linear.x = 3.0
    auto.angular.z = 0.5
    harness.auto_cmd = auto

    harness.send(harness.mission_pub, String, "autonomous")
    harness.muxed.clear()
    harness.steer_modes.clear()
    harness.spin(1.0)

    assert harness.muxed, "mux is not publishing at all"
    for twist in harness.muxed:
        assert twist.linear.x == 0.0 and twist.angular.z == 0.0
    # The 10 Hz heartbeat must be holding direct-PWM (unpowered) steer mode.
    pwm_frames = [f for f in harness.steer_modes if list(f.payload) == [STEER_MODE_PWM]]
    assert len(pwm_frames) >= 5, "heartbeat is not asserting PWM steer mode"
    assert "AS_READY" in [s.data for s in harness.states]


def test_start_passes_commands_and_mission_change_stops_them(harness):
    auto = Twist()
    auto.linear.x = 3.0
    auto.angular.z = 0.5
    harness.auto_cmd = auto

    harness.send(harness.mission_pub, String, "autonomous")
    harness.wait_until(lambda: harness.states[-1].data == "AS_READY")
    harness.send(harness.cmd_pub, String, "start")
    harness.spin(0.3)
    harness.auto_cmd = auto
    harness.muxed.clear()
    harness.spin(0.5)
    assert any(t.linear.x == 3.0 for t in harness.muxed), "driving does not pass cmd_vel"

    # Mission change without stop → emergency, output back to zero.
    harness.send(harness.mission_pub, String, "trackdrive")
    harness.spin(0.3)
    harness.muxed.clear()
    harness.spin(0.5)
    for twist in harness.muxed:
        assert twist.linear.x == 0.0 and twist.angular.z == 0.0
    assert "AS_EMERGENCY" in [s.data for s in harness.states]


def test_sensor_trip_requires_acknowledged_reset(harness):
    auto = Twist()
    auto.linear.x = 2.0
    harness.auto_cmd = auto
    harness.send(harness.mission_pub, String, "autonomous")
    harness.wait_until(lambda: harness.states[-1].data == "AS_READY")
    harness.send(harness.cmd_pub, String, "start")
    harness.spin(.3)
    harness.safety_faults = harness.safety_latch = 1
    harness.spin(.3)
    harness.muxed.clear()
    harness.spin(.2)
    assert harness.states[-1].data == "AS_EMERGENCY"
    assert all(m.linear.x == 0 and m.angular.z == 0 for m in harness.muxed)
    harness.safety_faults = 0
    harness.send(harness.cmd_pub, String, "stop")
    harness.spin(.2)
    assert harness.states[-1].data == "AS_EMERGENCY"
    harness.send(harness.cmd_pub, String, "reset")
    harness.spin(.6)
    assert harness.reset_ack > 0
    assert harness.states[-1].data == "AS_OFF"


def test_missing_firmware_blocks_start(harness):
    harness.safety_enabled = False
    harness.spin(.6)
    harness.send(harness.mission_pub, String, "autonomous")
    harness.spin(.2)
    harness.send(harness.cmd_pub, String, "start")
    harness.spin(.2)
    assert harness.states[-1].data == "AS_OFF"


def test_safety_report_silence_stops_while_commands_continue(harness):
    harness.auto_cmd = Twist()
    harness.auto_cmd.linear.x = 2.0
    harness.send(harness.mission_pub, String, "autonomous")
    harness.wait_until(lambda: harness.states[-1].data == "AS_READY")
    harness.send(harness.cmd_pub, String, "start")
    harness.wait_until(lambda: harness.states[-1].data == "AS_DRIVING")
    harness.safety_enabled = False
    harness.wait_until(lambda: harness.states[-1].data == "AS_EMERGENCY")
    harness.muxed.clear()
    harness.spin(.2)
    assert harness.muxed and all(m.linear.x == 0 for m in harness.muxed)


def test_none_steering_start_never_emits_pid(harness):
    harness.controller_mode = 1
    harness.auto_cmd = Twist()
    harness.auto_cmd.linear.x = 2.0
    harness.send(harness.mission_pub, String, "autonomous")
    harness.wait_until(lambda: harness.states[-1].data == "AS_READY")
    harness.spin(.2)
    harness.steer_modes.clear()
    harness.send(harness.cmd_pub, String, "start")
    harness.wait_until(lambda: harness.states[-1].data == "AS_DRIVING")
    harness.spin(.2)
    assert harness.steer_modes
    assert all(list(m.payload) == [1] for m in harness.steer_modes)
