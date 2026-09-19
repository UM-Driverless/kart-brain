"""Firmware health-tail decoding through the dashboard's JSON snapshot."""
import json

from kb_dashboard.protocol import DashboardState, decode_health_data


def test_wire_health_tail_and_unsigned_counters():
    data = decode_health_data([30, 100, 0, 12, 0, -1, 0, 5, -1, -2, 3, 4, 5, -1])
    assert data['hall_bits'] == 5
    assert data['hall_edges'] == [4294967295, 4294967294, 3]
    assert data['hall_multi_changes'] == 4294967295
    assert 'hall_bits' not in decode_health_data([30, 100, 0, 12, 0, -1])


def test_speed_source_stale_and_old_firmware(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr('kb_dashboard.protocol.time.monotonic', lambda: clock[0])
    state = DashboardState()
    state.configure_hall_speed(100.0)
    assert state.snapshot()['esp32_speed'] is None
    sample = {'hall_init_err': 0, 'hall_bits': 5, 'hall_edges': [0, 0, 0]}
    state.hall_sample(sample)
    clock[0] += 1
    state.hall_sample({**sample, 'hall_edges': [100, 100, 100]})
    state.update('esp32_speed', 99)  # A competing value cannot replace Hall speed.
    result = state.snapshot()
    assert result['esp32_speed'] == 3.0
    assert result['speed_source'] == 'motor_halls'
    json.dumps(result, allow_nan=False)
    clock[0] += 3
    assert state.snapshot()['esp32_speed'] is None
    assert state.snapshot()['hall_status'] == 'stale'
    state.hall_sample(None)
    assert state.snapshot()['hall_status'] == 'unsupported'
    assert state.snapshot()['hall_edges'] is None


def test_simulation_can_keep_external_speed():
    state = DashboardState()
    state.configure_hall_speed(enabled=False)
    state.update('esp32_speed', 1.25)
    assert state.snapshot()['esp32_speed'] == 1.25
    assert state.snapshot()['speed_source'] == 'legacy'
