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
