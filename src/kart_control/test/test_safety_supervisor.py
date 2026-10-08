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

@pytest.mark.parametrize("payload", [[], [1], [2,0,0,1,0,8], [1,-1,0,1,0,8], [1,0,0,32,0,8], [1,0,0,1,-1,8], [1,0,0,1,0,99], [1,0,0,1,0,8,0]])
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


def bench_ready(enabled=True, mode=1, mission="autonomous", flags=17, faults=4):
    s = SafetySupervisor(bench_throttle=enabled)
    s.on_mission(mission)
    s.on_safety([1,faults,0,flags,0,8],0)
    s.on_controller_safety("",0)
    s.on_controller_mode(mode,0)
    s.note_command("auto",(.25,0),0)
    s.bench_initialized = True
    s.bench_config = dict(steering="none",speed="constant_throttle_blind",max_speed=.25)
    s.bench_config_time = s.bridge_scale_time = 0
    s.bridge_scale = 5.0
    s.set_bench_mode(True,0)
    s.tick(0)
    return s


def test_bench_throttle_needs_literal_start_and_preserves_tank_notice():
    s=bench_ready()
    assert s.state == AS_READY
    assert s.mux((.25,0),(0,0),0) == (0,0)
    assert "BENCH THROTTLE: 30% electrical cap" in s.reason(0)
    assert "tank pressure too low" in s.reason(0)
    assert s.on_state_cmd("start",0)[0] == AS_DRIVING
    assert s.mux((.25,0),(0,0),0) == (.25,0)
    s.on_state_cmd("stop",0)
    assert s.mux((.25,0),(0,0),0) == (0,0)


@pytest.mark.parametrize("kwargs", [dict(enabled=False),dict(mode=0),
    dict(mission="throttle_test"),dict(flags=1),dict(flags=33),
    *[dict(faults=4|(1<<bit)) for bit in (0,1,3,4,5,6,7,8)]])
def test_bench_throttle_rejects_unpaired_identity_mode_or_other_fault(kwargs):
    s=bench_ready(**kwargs)
    assert not s.ready(0)
    assert s.on_state_cmd("start",0)[0] is None
    assert s.mux((.25,0),(0,0),0) == (0,0)


@pytest.mark.parametrize("failure", ["status", "command", "controller", "emergency"])
def test_bench_throttle_timeouts_and_emergency_stop(failure):
    s=bench_ready()
    assert s.on_state_cmd("start",0)[0] == AS_DRIVING
    if failure != "status":
        s.on_safety([1,4,0,25,0,8],.6)
    if failure != "command":
        s.note_command("auto",(.25,0),.6)
    if failure != "controller":
        s.bench_config_time = s.bridge_scale_time = .6
        s.on_controller_safety("",.6)
        s.on_controller_mode(1,.6)
    if failure == "emergency":
        s.on_state_cmd("ebs",.6)
    s.tick(.6)
    assert s.state == AS_EMERGENCY
    assert s.mux((.25,0),(0,0),.6) == (0,0)


def test_bench_identity_visible_even_with_healthy_pressure():
    s=bench_ready(faults=0)
    assert s.ready(0)
    assert "BENCH THROTTLE: 30% electrical cap" in s.reason(0)
    assert "elevated wheels only" in s.reason(0)


def test_bench_runtime_requires_paired_firmware_even_with_healthy_pressure():
    s=bench_ready(flags=1, faults=0)
    assert not s.ready(0)
    assert s.on_state_cmd("start",0)[0] is None


def test_bench_capability_does_not_enable_on_boot():
    s=SafetySupervisor(bench_throttle=True)
    assert not s.bench_mode_enabled
    assert not s.bench_snapshot(0)["bench_mode_enabled"]


@pytest.mark.parametrize("bad", [None,0,1,"true",[],{},float("nan")])
def test_bench_enable_rejects_bad_types(bad):
    s=bench_ready();s.set_bench_mode(False,0)
    assert not s.set_bench_mode(bad,0)
    assert not s.bench_mode_enabled


def test_bench_disable_stops_driving_without_clearing_emergency():
    s=bench_ready();s.on_state_cmd("start",0)
    assert s.state == AS_DRIVING
    assert s.set_bench_mode(False,0)
    assert s.state == AS_OFF and s.mux((1,0),(0,0),0)==(0,0)
    assert s.on_state_cmd("start",0)[0] is None
    s.logic.state=AS_EMERGENCY
    s.set_bench_mode(False,0)
    assert s.state == AS_EMERGENCY


@pytest.mark.parametrize("bad", [None,True,False,"20",[],{},float("nan"),float("inf"),-1,30.1])
def test_bench_throttle_rejects_invalid_requests(bad):
    s=bench_ready()
    assert s.validate_bench_throttle(bad,0)


def test_bench_throttle_uses_actual_scale_and_stopped_only():
    s=bench_ready();s.bridge_scale=10;s.bench_config['max_speed']=2
    assert s.bench_snapshot(0)['bench_throttle_percent']==20
    assert not s.validate_bench_throttle(30,0)
    s.on_state_cmd('start',0)
    assert s.validate_bench_throttle(20,0)
    assert not s.set_bench_mode(True,0)
    assert s.validate_bench_throttle(20,.6)


def test_bench_enable_requires_fresh_config_and_rejects_other_speed_modes():
    s=bench_ready();s.set_bench_mode(False,0)
    s.bench_config['speed']='constant_speed'
    assert not s.set_bench_mode(True,0)
    s.bench_config['speed']='constant_throttle_blind'
    assert not s.set_bench_mode(True,.6)


def test_bench_throttle_requires_actual_none_config_not_generic_pwm_mode():
    s=bench_ready();s.bench_config["steering"]="geometric"
    assert s.validate_bench_throttle(20,0)
    assert not s.ready(0)


def test_bench_enable_waits_for_verified_startup_zero():
    s=bench_ready();s.set_bench_mode(False,0);s.bench_initialized=False
    assert not s.set_bench_mode(True,0)
    assert "verified zero" in s.bench_notice


def bench_emergency(mission="autonomous"):
    s=bench_ready()
    s.set_bench_mode(False,0)
    s.logic.mission=mission
    s.bench_config['max_speed']=0
    s.note_command('auto',(0,0),0)
    s.on_safety([1,4,192,18,0,s.mission_id],0)
    s.tick(0)
    return s


def test_explicit_bench_request_prepares_auto_without_clearing_emergency():
    s=bench_emergency('manual')
    assert not s.set_bench_mode(True,0)
    assert s.mission=='autonomous' and s.state==AS_EMERGENCY
    assert not s.bench_mode_enabled and not s._arm_requested
    assert 'Reset Safety' in s.bench_notice
    assert not s.reset_inputs_healthy(0) # Firmware must acknowledge mission8 first.
    s.on_safety([1,4,192,18,0,8],0)
    assert s.reset_inputs_healthy(0)


def test_bench_reset_ack_retains_pressure_off_and_requires_new_enable_start():
    s=bench_emergency()
    s.on_state_cmd('reset',0)
    token=s.pending_reset
    assert token==1 and s.state==AS_EMERGENCY
    s.on_safety([1,4,0,17,token,8],.1)
    assert s.state==AS_OFF and s.pending_reset is None
    assert s._reset_hold and not s.bench_mode_enabled
    assert s.mux((1,0),(0,0),.1)==(0,0)
    assert s.on_state_cmd('start',.1)[0] is None
    assert s.status[1]==4
    assert s.set_bench_mode(True,.1)
    assert not s._reset_hold and s.state==AS_OFF
    s.tick(.1); assert s.state==AS_READY
    s.bench_config['max_speed']=.9
    s.note_command('auto',(.9,0),.1)
    s.on_state_cmd('start',.1)
    assert s.state==AS_DRIVING and s.mux((.9,0),(0,0),.1)==(.9,0)


@pytest.mark.parametrize('failure', ['capability','identity','mission','mode','config','speed',
    'nonzero','command','stale_command','status','controller','other_fault','armed','startup'])
def test_bench_reset_refuses_unsafe_inputs_and_never_queues_rejected_attempt(failure):
    s=bench_emergency()
    if failure=='capability': s.bench_throttle=False
    elif failure=='identity': s.status=(1,4,192,2,0,8)
    elif failure=='mission': s.logic.mission='manual';s.status=(1,4,192,18,0,0)
    elif failure=='mode': s.controller_mode=0
    elif failure=='config': s.bench_config['steering']='geometric'
    elif failure=='speed': s.bench_config['speed']='constant_speed'
    elif failure=='nonzero': s.bench_config['max_speed']=.1
    elif failure=='command': s.note_command('auto',(.1,0),0)
    elif failure=='stale_command': s.command_times['auto']=-1
    elif failure=='status': s.status_time=-1
    elif failure=='controller': s.controller_reason='Perception missing'
    elif failure=='other_fault': s.status=(1,6,192,18,0,8)
    elif failure=='armed': s.status=(1,4,192,26,0,8)
    elif failure=='startup': s.bench_initialized=False
    s.on_state_cmd('reset',0)
    assert s.pending_reset is None and s.state==AS_EMERGENCY
    assert not s.set_bench_mode(True,0) and not s.bench_mode_enabled


def test_zero_setting_allowed_in_bench_emergency_but_nonzero_rejected():
    s=bench_emergency();s.bench_config['max_speed']=.9
    assert not s.validate_bench_throttle(0,0)
    assert s.validate_bench_throttle(18,0)
    assert s.state==AS_EMERGENCY


def test_bypass_request_is_not_effective_in_remote_or_emergency():
    s = bench_ready()
    assert s.bench_snapshot(0)["bench_mode_active"]
    s.logic.mission = "remote_control"
    s.on_safety([1, 4, 132, 18, 0, 7], 0)
    snapshot = s.bench_snapshot(0)
    assert snapshot["bench_mode_enabled"]
    assert not snapshot["bench_mode_active"]
    assert snapshot["bench_pressure_fault"]
    assert "requested but inactive" in snapshot["bench_mode_reason"]
    assert "select Autonomous" in snapshot["bench_mode_reason"]
    assert s.blocking_faults(0) == 4
    assert not s.bench_snapshot(1)["bench_mode_active"]


def test_active_pressure_override_keeps_raw_fault_visible():
    s = bench_ready()
    snapshot = s.bench_snapshot(0)
    assert snapshot["bench_mode_enabled"] and snapshot["bench_mode_active"]
    assert snapshot["bench_pressure_fault"] and s.status[1] == 4
    assert "bench override active" in s.reason(0)
    s.set_bench_mode(False, 0)
    assert not s.bench_snapshot(0)["bench_mode_active"]
