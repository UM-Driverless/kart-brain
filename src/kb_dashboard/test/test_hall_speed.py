import pytest

from kb_dashboard.hall_speed import HallSpeed, NOMINAL_HALL_EDGES_PER_METRE


def sample(edges, **kwargs):
    return {"hall_init_err": 0, "hall_bits": 7, "hall_edges": edges, **kwargs}


def test_calibrated_speed_sums_three_channels():
    hall = HallSpeed(100)
    hall.ingest(sample([0, 0, 0]), 0.0)
    result = hall.ingest(sample([100, 100, 100]), 1.0)
    assert result["hall_status"] == "moving"
    assert result["hall_edge_rate_hz"] == 300
    assert result["hall_speed_mps"] == 3


def test_nominal_drivetrain_converts_five_wheel_turns_to_distance():
    # 5 turns × 7.6 motor turns × 3 complete pulses × 2 edges = 228/channel.
    hall = HallSpeed(NOMINAL_HALL_EDGES_PER_METRE)
    hall.ingest(sample([0, 0, 0]), 0.0)
    result = hall.ingest(sample([228, 228, 228]), 2.0)
    assert result["hall_status"] == "moving"
    assert result["hall_speed_mps"] == pytest.approx(5 * 0.8777609874129881 / 2)


def test_uncalibrated_and_single_dead_channel_are_not_speed():
    hall = HallSpeed()
    hall.ingest(sample([0, 0, 0]), 0)
    assert hall.ingest(sample([10, 10, 10]), 1)["hall_status"] == "uncalibrated"
    assert hall.ingest(sample([10, 10, 10]), 2)["hall_status"] == "no_edges"
    assert hall.ingest(sample([20, 20, 10]), 3)["hall_status"] == "ambiguous"


def test_stale_invalidates_next_delta_and_recovers():
    hall = HallSpeed(100)
    hall.ingest(sample([0, 0, 0]), 0)
    assert hall.snapshot(3)["hall_status"] == "stale"
    assert hall.ingest(sample([100, 100, 100]), 4)["hall_status"] == "stale"
    assert hall.ingest(sample([200, 200, 200]), 5)["hall_status"] == "moving"


def test_counter_reset_and_unsigned_wrap():
    hall = HallSpeed(100)
    hall.ingest(sample([0xFFFFFFF0] * 3), 0)
    assert hall.ingest(sample([0x10] * 3), 1)["hall_status"] == "moving"
    assert hall.ingest(sample([0x10 + (1 << 31)] * 3), 2)["hall_status"] == "counter_reset"


def test_cumulative_multi_change_recovers_on_next_clean_interval():
    hall = HallSpeed(100)
    hall.ingest(sample([0, 0, 0], hall_multi_changes=0), 0)
    assert hall.ingest(sample([100, 100, 100], hall_multi_changes=1), 1)["hall_status"] == "ambiguous"
    assert hall.ingest(sample([200, 200, 200], hall_multi_changes=1), 2)["hall_status"] == "moving"


def test_invalid_and_failed_init_clear_baseline_and_missing_fields_are_none():
    hall = HallSpeed(100)
    hall.ingest(sample([0, 0, 0]), 0)
    failed = hall.ingest({"hall_init_err": 1, "hall_bits": -1, "hall_edges": None}, 1)
    assert failed["hall_status"] == "capture_error"
    assert failed["hall_edges_per_metre"] == 100
    warmed = hall.ingest(sample([100, 100, 100]), 2)
    assert warmed["hall_status"] == "warming_up"
    unsupported = hall.ingest({"hall_init_err": None, "hall_bits": None}, 3)
    assert unsupported["hall_status"] == "unsupported"
    assert all(unsupported[key] is None for key in ("hall_edges", "hall_age_ms", "hall_interval_us", "hall_multi_changes"))


@pytest.mark.parametrize("bad", [False, {"hall_edges": [1, 2]}, sample([1, 2, -1]), sample([1, 2, 3], hall_age_ms=float("nan")), sample([1, 2, 3], hall_bits=8), sample([1, 2, 3], hall_multi_changes=-1)])
def test_malformed_payload_does_not_crash(bad):
    hall = HallSpeed()
    assert hall.ingest(bad, 0)["hall_status"] == "invalid"


def test_unsigned_multi_counter_wrap_is_ambiguous_then_recovers():
    hall = HallSpeed(100)
    hall.ingest(sample([0, 0, 0], hall_multi_changes=0xffffffff), 0)
    assert hall.ingest(sample([10, 10, 10], hall_multi_changes=0), 1)['hall_status'] == 'ambiguous'
    assert hall.ingest(sample([20, 20, 20], hall_multi_changes=0), 2)['hall_status'] == 'moving'


def test_invalid_packet_cannot_inflate_next_rate():
    hall = HallSpeed(100)
    hall.ingest(sample([0, 0, 0]), 0)
    assert hall.ingest(sample([5, 5, 5], hall_bits=99), 1)['hall_status'] == 'invalid'
    assert hall.ingest(sample([100, 100, 100]), 2)['hall_status'] == 'warming_up'
    assert hall.ingest(sample([110, 110, 110]), 3)['hall_speed_mps'] == 0.3


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), "100"])
def test_invalid_calibration_rejected(value):
    with pytest.raises(ValueError):
        HallSpeed(value)
