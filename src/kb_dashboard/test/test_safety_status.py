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
