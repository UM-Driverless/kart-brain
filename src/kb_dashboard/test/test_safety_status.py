"""Safety feedback remains explicit when its publisher stops."""
from kb_dashboard.protocol import DashboardState


def test_safety_reason_expires_and_recovers(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr('kb_dashboard.protocol.time.monotonic', lambda: clock[0])
    state = DashboardState()
    assert state.snapshot()['safety_reason'] == 'Waiting for safety status'
    state.update('safety_reason', 'Steering sensor invalid')
    assert state.snapshot()['safety_reason'] == 'Steering sensor invalid'
    state.update('safety_reason', '')
    assert state.snapshot()['safety_reason'] == ''
    clock[0] += 2.01
    assert 'stale' in state.snapshot()['safety_reason']
    state.update('safety_reason', 'Reset required')
    assert state.snapshot()['safety_reason'] == 'Reset required'


def test_bench_feedback_expires_and_never_optimistically_enables(monkeypatch):
    clock=[10.0];monkeypatch.setattr('kb_dashboard.protocol.time.monotonic',lambda:clock[0])
    state=DashboardState()
    assert not state.snapshot()['bench_mode_enabled']
    status=dict(bench_mode_available=True,bench_mode_enabled=True,bench_mode_reason='ON',
                bench_throttle_cap_percent=30,bench_throttle_percent=22,bench_pressure_fault=True)
    state.update_bench_status(status)
    assert state.snapshot()['bench_throttle_percent']==22
    clock[0]+=.51
    assert not state.snapshot()['bench_mode_available']
    assert not state.snapshot()['bench_mode_enabled']
    assert state.snapshot()['bench_throttle_percent'] is None


def test_bench_websocket_input_validation():
    import pytest
    from kb_dashboard.server import validate_bench_action
    for value in (None,0,1,'true',[],{}):
        with pytest.raises(ValueError):validate_bench_action(dict(action='set_bench_mode',enabled=value))
    for value in (None,True,'20',[],{},float('nan'),float('inf'),-1,31):
        with pytest.raises(ValueError):validate_bench_action(dict(action='set_bench_throttle',percent=value))
    assert validate_bench_action(dict(action='set_bench_mode',enabled=False)) is False
    assert validate_bench_action(dict(action='set_bench_throttle',percent=20))==20
