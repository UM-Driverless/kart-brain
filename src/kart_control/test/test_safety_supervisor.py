"""Fault injection through the production supervisor, without ROS or hardware."""
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from safety_supervisor import SafetySupervisor
from state_logic import AS_OFF, AS_READY, AS_DRIVING, AS_EMERGENCY

@pytest.fixture
def s():
    return SafetySupervisor()

def healthy(s, now=0.0, mission=8, ack=0):
    s.on_safety([1, 0, 0, 1, ack, mission], now)
    s.on_controller_safety("", now)
    s.on_controller_mode(0, now)

def driving(s):
    s.on_mission("autonomous")
    healthy(s)
    s.note_command("auto", (1.0, 0.2), 0.0)
    s.tick(0.0)
    assert s.on_state_cmd("start", 0.0)[0] == AS_DRIVING


def test_no_sensor_status_inhibits_start(s):
    s.on_mission("autonomous")
    s.note_command("auto", (1.0, 0.2), 0)
    assert s.state == AS_OFF
    assert s.on_state_cmd("start", 0)[0] is None


def test_health_arrival_arms_without_driving(s):
    s.on_mission("autonomous")
    healthy(s)
    s.tick(0)
    assert s.state == AS_READY
    assert s.mux((1, .2), (0, 0), 0) == (0, 0)

@pytest.mark.parametrize("payload", [[], [1], [2,0,0,1,0,8], [1,-1,0,1,0,8], [1,0,0,16,0,8], [1,0,0,1,-1,8], [1,0,0,1,0,99], [1,0,0,1,0,8,0]])
def test_bad_status_trips_running_kart(s, payload):
    driving(s)
    s.on_safety(payload, .1)
    s.tick(.1)
    assert s.state == AS_EMERGENCY
    assert s.mux((1,.2),(1,.2),.1) == (0,0)

@pytest.mark.parametrize("bit", range(9))
def test_each_firmware_fault_trips_and_never_self_clears(s, bit):
    driving(s)
    s.on_safety([1,1<<bit,1<<bit,2,0,8], .1)
    s.tick(.1)
    assert s.state == AS_EMERGENCY
    healthy(s,.2)
    s.tick(.2)
    assert s.state == AS_EMERGENCY
    s.on_state_cmd("stop",.2)
    s.on_mission("manual")
    assert s.state == AS_EMERGENCY


def test_silent_firmware_trips_even_with_fresh_commands(s):
    driving(s)
    s.note_command("auto", (1,.2), .6)
    s.tick(.6)
    assert s.state == AS_EMERGENCY


def test_silent_controller_trips_even_with_fresh_firmware(s):
    driving(s)
    healthy(s,.6)
    s.tick(.6)
    assert s.state == AS_EMERGENCY

@pytest.mark.parametrize("cmd", [(float("nan"),0), (1,float("inf"))])
def test_invalid_command_trips(s,cmd):
    driving(s)
    s.note_command("auto",cmd,.1)
    s.tick(.1)
    assert s.state == AS_EMERGENCY


def test_reset_waits_for_matching_ack_and_does_not_restart(s):
    driving(s)
    s.on_safety([1,0,1,2,0,8],.1)
    s.tick(.1)
    s.on_state_cmd("reset",.1)
    token=s.pending_reset
    assert token and s.state == AS_EMERGENCY
    healthy(s,.2,ack=token+1)
    s.tick(.2)
    assert s.state == AS_EMERGENCY
    healthy(s,.3,ack=token)
    s.tick(.3)
    assert s.state == AS_OFF
    s.tick(.4)
    assert s.state == AS_OFF
    assert s.on_state_cmd("start",.4)[0] is None


def test_reset_refused_while_hardware_unhealthy(s):
    driving(s)
    s.on_safety([1,1,1,2,0,8],.1)
    s.tick(.1)
    s.on_state_cmd("reset",.1)
    assert s.pending_reset is None
    assert s.state == AS_EMERGENCY


def test_wrong_mission_status_cannot_arm(s):
    s.on_mission("autonomous")
    healthy(s,mission=7)
    s.tick(0)
    assert s.state == AS_OFF


def test_remote_bench_allows_only_steering(s):
    s.on_mission("remote_control")
    s.on_safety([1,0,0,4,0,7],0)
    s.note_command("manual", (2,.3),0)
    s.tick(0)
    assert s.mux((0,0),(2,.3),0) == (0,.3)


def test_start_requires_a_fresh_command(s):
    s.on_mission("autonomous")
    healthy(s)
    s.tick(0)
    assert s.on_state_cmd("start",0)[0] is None


def test_required_perception_fault_trips_even_when_commands_continue(s):
    driving(s)
    s.on_controller_safety("Perception missing or stale", .1)
    s.note_command("auto", (1,.2), .1)
    s.tick(.1)
    assert s.state == AS_EMERGENCY


def test_controller_status_cannot_silently_die(s):
    driving(s)
    s.on_safety([1,0,0,1,0,8],.6)
    s.note_command("auto", (1,.2), .6)
    s.tick(.6)
    assert s.state == AS_EMERGENCY


def test_reset_cannot_resume_remote_joystick(s):
    s.on_mission("remote_control")
    s.on_safety([1,0,1,2,0,7],0)
    s.tick(0)
    s.on_state_cmd("reset",0)
    token=s.pending_reset
    s.on_safety([1,0,0,4,token,7],.1)
    s.note_command("manual", (2,.3),.1)
    assert s.mux((0,0),(2,.3),.1) == (0,0)


def test_rejected_reset_retry_uses_new_token(s):
    driving(s)
    s.on_safety([1,0,1,2,0,8],.1)
    s.tick(.1)
    s.on_state_cmd("reset",.1)
    first=s.pending_reset
    s.on_safety([1,0,1,2,0,8],2.2)
    s.on_controller_safety("",2.2)
    s.on_controller_mode(0,2.2)
    s.tick(2.2)
    s.on_state_cmd("reset",2.2)
    assert s.pending_reset > first


def test_none_steering_never_forces_position_loop_at_start(s):
    s.on_mission("autonomous")
    healthy(s)
    s.on_controller_mode(1,0)
    s.note_command("auto",(1,0),0)
    s.tick(0)
    assert s.on_state_cmd("start",0)[0] == AS_DRIVING
    assert s.driving_steer_mode() == 1


@pytest.mark.parametrize("bit", [3,5,6,8])
def test_bench_requires_healthy_comms_state_and_outputs(s,bit):
    s.on_mission("remote_control")
    s.on_safety([1,1<<bit,0,4,0,7],0)
    s.note_command("manual", (1,.3),0)
    assert s.mux((0,0),(1,.3),0) == (0,0)


def test_unsafe_mission_change_retains_original_firmware_mode(s):
    driving(s)
    s.on_mission("manual")
    assert s.state == AS_EMERGENCY
    assert s.mission == "autonomous"
