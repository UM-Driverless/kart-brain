"""The state heartbeat must recover a mission frame lost during USB disconnect."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace


def test_state_heartbeat_repeats_current_mission_without_a_selection_change():
    path = Path(__file__).parents[1] / 'scripts' / 'state_machine_node.py'
    cls = next(n for n in ast.parse(path.read_text()).body
               if isinstance(n, ast.ClassDef) and n.name == 'StateMachineNode')
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                  and n.name == '_publish_state')
    namespace = {'json': json, 'String': SimpleNamespace, 'STATE_NAMES': {4: 'AS_EMERGENCY'}, 'time': SimpleNamespace(monotonic=lambda: 0)}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), 'exec'), namespace)
    missions, states = [], []
    node = SimpleNamespace(
        _logic=SimpleNamespace(state=4, mission='autonomous', heartbeat_steer_mode=lambda: None, reason=lambda now: '', pending_reset=None, bench_snapshot=lambda now: {}),
        _state_pub=SimpleNamespace(publish=states.append),
        _safety_reason_pub=SimpleNamespace(publish=lambda msg: None),
        _bench_status_pub=SimpleNamespace(publish=lambda msg: None),
        _publish_state_frame=lambda: None,
        _publish_mission_frame=lambda: missions.append(node._logic.mission),
    )
    # A receiver reconnects after the first heartbeat and must receive the same mission.
    namespace['_publish_state'](node)
    missions.clear()
    namespace['_publish_state'](node)
    assert missions == ['autonomous']
    assert all(msg.data == 'AS_EMERGENCY' for msg in states)
