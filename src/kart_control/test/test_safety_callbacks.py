"""Run production ROS callbacks with fake publishers and controlled clocks."""
import ast
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).parents[1] / 'scripts'


def callbacks(filename, clsname, names, namespace):
    tree = ast.parse((SCRIPTS / filename).read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == clsname)
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), *methods], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), filename, 'exec'), namespace)
    return type('ProductionCallbacks', (), {name: namespace[name] for name in names})()


def test_bridge_expires_cached_effort_and_requests_emergency():
    clock = SimpleNamespace(now=0)
    class Frame:
        ORIN_TARG_THROTTLE=32
        ORIN_TARG_BRAKING=33
        ORIN_TARG_STEERING=34
    node = callbacks('cmd_vel_bridge_node.py', 'CmdVelBridgeNode', {'_on_cmd','_send_frames'}, {
        'math': math, 'time': SimpleNamespace(monotonic=lambda: clock.now),
        'Frame':Frame, 'String':SimpleNamespace, 'COMMAND_TIMEOUT':.5, 'ORIN_STEER_MODE':41,
        'encode_throttle':lambda x:[int(x*255)], 'encode_braking':lambda x:[int(x*255)],
        'encode_steering':lambda x:[int(x*1000)]})
    pubs={name:[] for name in ('throttle_pub','brake_pub','steering_pub','_mode_pub','_emergency_pub')}
    for name, messages in pubs.items():
        setattr(node,name,SimpleNamespace(publish=messages.append))
    node.max_speed=5
    node.max_steer=1.2
    node._steer_mode=0
    cmd=SimpleNamespace(linear=SimpleNamespace(x=2.5),angular=SimpleNamespace(z=.2))
    node._on_cmd(cmd)
    node._send_frames()
    assert pubs['throttle_pub'][-1].payload == [127]
    clock.now=.6
    node._send_frames()
    assert pubs['throttle_pub'][-1].payload == [0]
    assert pubs['steering_pub'][-1].payload == [0]
    assert pubs['_mode_pub'][-1].payload == [1]
    assert pubs['_emergency_pub'][-1].data == 'ebs'
    cmd.linear.x=float('nan')
    node._on_cmd(cmd)
    node._send_frames()
    assert pubs['throttle_pub'][-1].payload == [0]


@pytest.mark.parametrize('controller,speed,received,age,fresh,reason',[
    ('none','constant_throttle_blind',False,2,False,''),
    ('none','zero',False,2,False,''),
    ('geometric','constant_throttle_blind',False,2,False,'Perception missing or stale'),
    ('geometric','constant_throttle',True,.1,True,''),
    ('geometric','constant_throttle',True,2,True,'Perception missing or stale'),
    ('geometric','constant_speed',True,.1,False,'Speed measurement missing, invalid or stale'),
])
def test_required_controller_inputs(controller,speed,received,age,fresh,reason):
    node=callbacks('cone_follower_node.py','ConeFollowerNode',{'_publish_sensor_safety'}, {'String':SimpleNamespace, 'Frame':type('Frame', (), {'ORIN_STEER_MODE':41})})
    class Stamp:
        def __sub__(self,other): return SimpleNamespace(nanoseconds=int(age*1e9))
    node.get_clock=lambda:SimpleNamespace(now=Stamp)
    node.last_detection_time=Stamp()
    node.controller_type=controller
    node.speed_controller_type=speed
    node._detection_invalid=False
    node._received_detections=received
    node.no_cone_timeout=1.0
    node._speed_is_fresh=lambda:fresh
    messages=[]
    node._sensor_safety_pub=SimpleNamespace(publish=messages.append)
    node._desired_mode_pub=SimpleNamespace(publish=lambda msg: None)
    node._publish_sensor_safety()
    assert messages[-1].data == reason


def test_bench_parameter_update_never_reports_requested_percent_as_readback():
    import sys
    sys.path.insert(0,str(SCRIPTS))
    from safety_supervisor import SafetySupervisor
    s=SafetySupervisor(bench_throttle=True)
    s.bench_config=dict(steering='none',speed='constant_throttle_blind',max_speed=0)
    calls=[]
    class Parameter:
        def __init__(self,name,value):self.name=name;self.value=value
        def to_parameter_msg(self):return self
    future=SimpleNamespace(add_done_callback=lambda cb:calls.append(cb))
    node=callbacks('state_machine_node.py','StateMachineNode',{'_apply_bench_speed'}, {
        'AS_OFF':0,'AS_EMERGENCY':4,'Parameter':Parameter,'SetParameters':SimpleNamespace(Request=SimpleNamespace),
        'time':SimpleNamespace(monotonic=lambda:1)})
    node._logic=s
    node._controller_params=SimpleNamespace(service_is_ready=lambda:True,call_async=lambda req:future)
    node._apply_bench_speed(0)
    assert s.bench_pending and s.state==0
    assert s.bench_config['max_speed']==0
    calls[0](SimpleNamespace(result=lambda:SimpleNamespace(results=[SimpleNamespace(successful=True)])))
    assert not s.bench_pending and node._bench_zero_ack
    assert not s.bench_initialized
    assert s.bench_config['max_speed']==0 # Only controller heartbeat updates applied readback.


def test_bench_ros_input_validation_and_scale():
    import json,sys
    sys.path.insert(0,str(SCRIPTS))
    from safety_supervisor import SafetySupervisor
    s=SafetySupervisor(bench_throttle=True)
    s.bench_initialized=True
    s.on_mission('autonomous');s.on_safety([1,4,0,17,0,8],0)
    s.on_controller_mode(1,0);s.on_controller_safety('',0)
    s.bench_config=dict(steering='none',speed='constant_throttle_blind',max_speed=0)
    s.bench_config_time=s.bridge_scale_time=0;s.bridge_scale=10
    node=callbacks('state_machine_node.py','StateMachineNode',{'_on_bench_throttle'},
        {'json':json,'time':SimpleNamespace(monotonic=lambda:0)})
    node._logic=s;applied=[];node._apply_bench_speed=applied.append
    for data in ('NaN','true','[20]','"20"','31','bad'):
        node._on_bench_throttle(SimpleNamespace(data=data))
    assert applied==[]
    node._on_bench_throttle(SimpleNamespace(data='20'))
    assert applied==[2.0]
    s.logic.state=2;node._on_bench_throttle(SimpleNamespace(data='22'))
    assert applied==[2.0]


@pytest.mark.parametrize('state', [0, 4])
def test_startup_zero_runs_while_emergency_without_clearing_it(state):
    import sys
    sys.path.insert(0, str(SCRIPTS))
    from safety_supervisor import SafetySupervisor
    s = SafetySupervisor(bench_throttle=True)
    s.logic.state = state
    s.bench_config = dict(steering='geometric', speed='curve_factor', max_speed=2.625)
    class Parameter:
        def __init__(self, name, value): self.name=name; self.value=value
        def to_parameter_msg(self): return self
    requests=[]; callbacks_done=[]
    client=SimpleNamespace(service_is_ready=lambda: True,
        call_async=lambda req: (requests.append(req) or SimpleNamespace(add_done_callback=callbacks_done.append)))
    node=callbacks('state_machine_node.py', 'StateMachineNode', {'_bench_poll', '_apply_bench_speed'}, {
        'AS_OFF':0, 'AS_EMERGENCY':4, 'Parameter':Parameter,
        'SetParameters':SimpleNamespace(Request=SimpleNamespace),
        'time':SimpleNamespace(monotonic=lambda:1), 'math':math})
    node._logic=s; node._bench_zeroed=False; node._controller_params=client
    node._bridge_params=SimpleNamespace(service_is_ready=lambda:False)
    node._bench_poll()
    assert len(requests)==1 and requests[0].parameters[0].value==0
    assert s.state==state and s.bench_pending
    callbacks_done[0](SimpleNamespace(result=lambda:SimpleNamespace(results=[SimpleNamespace(successful=True)])))
    assert s.state==state and not s._arm_requested
    assert s.bench_config['max_speed']==2.625  # Await actual controller heartbeat.
