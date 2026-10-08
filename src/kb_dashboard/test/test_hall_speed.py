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


def test_slow_period_is_held_across_empty_publication_windows():
    hall = HallSpeed(NOMINAL_HALL_EDGES_PER_METRE)
    for i in range(5):
        result = hall.ingest(sample([1, 1, 1], hall_age_ms=i * 50,
                                    hall_interval_us=231000), 10 + i * .05)
        assert result["hall_speed_mps"] * 3.6 == pytest.approx(.1, rel=.001)


def test_period_does_not_depend_on_message_arrival_time():
    hall = HallSpeed(100)
    hall.ingest(sample([1, 1, 1], hall_age_ms=0, hall_interval_us=10000), 0)
    result = hall.ingest(sample([10, 10, 10], hall_age_ms=0,
                                hall_interval_us=10000), .073)
    assert result["hall_speed_mps"] == 1


def test_period_expires_and_restart_needs_two_new_edges():
    hall = HallSpeed(100)
    hall.ingest(sample([1, 1, 1], hall_age_ms=0, hall_interval_us=231000), 0)
    result = hall.ingest(sample([1, 1, 1], hall_age_ms=700,
                                hall_interval_us=231000), .7)
    assert result["hall_speed_mps"] is None
    assert result["hall_status"] == "no_edges"
    # First new edge spans the stopped interval and cannot measure moving speed.
    first = hall.ingest(sample([2, 1, 1], hall_age_ms=0,
                              hall_interval_us=1000000), 1)
    assert first["hall_speed_mps"] is None
    second = hall.ingest(sample([2, 2, 1], hall_age_ms=0,
                               hall_interval_us=231000), 1.231)
    assert second["hall_speed_mps"] == pytest.approx(1 / (100 * .231))


def test_fast_packets_allow_individual_channels_to_wait_at_low_speed():
    hall = HallSpeed(100)
    edges = [1, 1, 1]
    for i in range(19):
        if i and i % 4 == 0:
            edges[(i // 4) % 3] += 1
        result = hall.ingest(sample(list(edges), hall_age_ms=(i % 4) * 50,
                                   hall_interval_us=200000), i * .05)
        assert result['hall_status'] == 'moving'
        assert result['hall_speed_mps'] == .05


def test_dead_channel_is_detected_over_multiple_publications():
    hall = HallSpeed(100)
    for i in range(23):
        result = hall.ingest(sample([i, i, 0], hall_age_ms=0,
                                   hall_interval_us=10000), i * .05)
    assert result['hall_status'] == 'ambiguous'
    assert result['hall_speed_mps'] is None


def test_period_reset_and_bad_capture_cannot_reuse_old_interval():
    hall = HallSpeed(100)
    hall.ingest(sample([100, 100, 100], hall_age_ms=0, hall_interval_us=10000), 0)
    assert hall.ingest(sample([0, 0, 0], hall_age_ms=0,
                              hall_interval_us=10000), .05)['hall_status'] == 'counter_reset'
    assert hall.ingest(sample([0, 0, 0], hall_age_ms=50,
                              hall_interval_us=10000), .1)['hall_speed_mps'] is None
    assert hall.ingest(sample([1, 1, 0], hall_age_ms=0,
                              hall_interval_us=10000), .15)['hall_speed_mps'] == 1


def test_snapshot_expires_period_before_telemetry_goes_stale():
    hall = HallSpeed(100)
    hall.ingest(sample([1, 1, 1], hall_age_ms=0, hall_interval_us=10000), 0)
    assert hall.snapshot(.1)['hall_speed_mps'] == 1
    assert hall.snapshot(.151)['hall_status'] == 'no_edges'
    assert hall.snapshot(3)['hall_status'] == 'stale'


def test_period_unsigned_wrap_is_not_reset():
    hall = HallSpeed(100)
    hall.ingest(sample([0xffffffff] * 3, hall_age_ms=0, hall_interval_us=10000), 0)
    assert hall.ingest(sample([0] * 3, hall_age_ms=0,
                              hall_interval_us=10000), .05)['hall_speed_mps'] == 1
