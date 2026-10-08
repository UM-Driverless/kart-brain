"""Safety invariants for the AS state machine (scripts/state_logic.py).

Runs with plain pytest on any machine — no ROS needed. These tests exercise the
real module the node imports, not a copy, so a change to the logic that breaks
an invariant fails here before it reaches the kart.

The invariants come from an incident on 2026-08-10 where selecting an
autonomous mission powered the steering motor before Start was pressed: the
mux's zero Twist was a real "go to centre" target in PID steer mode. Each
invariant states something the kart must never do, over every mission and
state combination, not just the path that failed once.
"""

import itertools
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from state_logic import (  # noqa: E402
    AS_OFF, AS_READY, AS_DRIVING, AS_FINISHED, AS_EMERGENCY,
    AUTONOMOUS_MISSIONS, MANUAL_MISSIONS,
    STEER_MODE_PWM, THROTTLE_TEST_SPEED, ZERO_CMD,
    StateLogic,
)

ALL_STATES = [AS_OFF, AS_READY, AS_DRIVING, AS_FINISHED, AS_EMERGENCY]
ALL_MISSIONS = sorted(AUTONOMOUS_MISSIONS | MANUAL_MISSIONS)
ALL_CMDS = ["start", "stop", "ebs", "finish", "reset", "garbage", ""]

AUTO_CMD = (3.0, 0.5)      # a live autonomous command, clearly nonzero
MANUAL_CMD = (1.0, -0.3)   # a live manual command


def make(mission, state):
    """A StateLogic forced into an arbitrary (mission, state) point."""
    logic = StateLogic()
    logic.mission = mission
    logic.state = state
    return logic


# ── The incident invariant ─────────────────────────────────────────────

@pytest.mark.parametrize(
    "mission,state",
    [(m, s) for m in sorted(AUTONOMOUS_MISSIONS) for s in ALL_STATES if s != AS_DRIVING],
)
def test_steering_unpowered_whenever_autonomous_and_not_driving(mission, state):
    """Outside AS_DRIVING, an autonomous mission must command zero output AND
    hold direct-PWM steer mode, where zero means the motor is unpowered.
    Either half alone re-creates the 2026-08-10 incident."""
    logic = make(mission, state)
    assert logic.mux(AUTO_CMD, MANUAL_CMD) == ZERO_CMD
    assert logic.heartbeat_steer_mode() == STEER_MODE_PWM


@pytest.mark.parametrize("mission", sorted(AUTONOMOUS_MISSIONS))
def test_selecting_autonomous_mission_does_not_power_steering(mission):
    """The literal incident: pick a mission, don't press Start."""
    logic = StateLogic()
    logic.on_mission(mission)
    assert logic.state == AS_READY
    assert logic.mux(AUTO_CMD, MANUAL_CMD) == ZERO_CMD
    assert logic.heartbeat_steer_mode() == STEER_MODE_PWM


def test_pid_mode_forced_only_at_start_of_autonomous_mission():
    """force_pid is the one moment the position loop may be armed."""
    for mission, state, cmd in itertools.product(ALL_MISSIONS, ALL_STATES, ALL_CMDS):
        logic = make(mission, state)
        _, force_pid = logic.on_state_cmd(cmd)
        expected = cmd == "start" and state == AS_READY and mission in AUTONOMOUS_MISSIONS
        assert force_pid == expected, (mission, state, cmd)


# ── AS_DRIVING is reachable only through "start" ───────────────────────

def test_no_command_but_start_reaches_driving():
    for mission, state, cmd in itertools.product(ALL_MISSIONS, ALL_STATES, ALL_CMDS):
        if cmd == "start":
            continue
        logic = make(mission, state)
        logic.on_state_cmd(cmd)
        if state != AS_DRIVING:
            assert logic.state != AS_DRIVING, (mission, state, cmd)


def test_no_mission_selection_reaches_driving():
    for mission, state, new_mission in itertools.product(ALL_MISSIONS, ALL_STATES, ALL_MISSIONS):
        if state == AS_DRIVING:
            continue
        logic = make(mission, state)
        logic.on_mission(new_mission)
        assert logic.state != AS_DRIVING, (mission, state, new_mission)


def test_start_works_only_from_ready():
    for state in ALL_STATES:
        logic = make("autonomous", state)
        new_state, _ = logic.on_state_cmd("start")
        assert (new_state == AS_DRIVING) == (state == AS_READY)


# ── Mission change mid-drive is an emergency ───────────────────────────

@pytest.mark.parametrize("mission", ALL_MISSIONS)
@pytest.mark.parametrize("new_mission", ALL_MISSIONS)
def test_mission_change_while_driving_is_emergency(mission, new_mission):
    """Changing mission without pressing Stop first is operator error: latch
    AS_EMERGENCY and zero the output. Re-sending the SAME mission is a no-op —
    the dashboard repeats its mission message for 1 s to ensure delivery."""
    logic = make(mission, AS_DRIVING)
    logic.on_mission(new_mission)
    if new_mission == mission:
        assert logic.state == AS_DRIVING
    else:
        assert logic.state == AS_EMERGENCY
        assert logic.mux(AUTO_CMD, MANUAL_CMD) == ZERO_CMD
        # Only reset recovers; start must be refused.
        _, force_pid = logic.on_state_cmd("start")
        assert logic.state == AS_EMERGENCY and not force_pid
        logic.on_state_cmd("reset")
        assert logic.state == AS_OFF


def test_repeated_mission_message_never_transitions():
    """The dashboard's 1 s repeat of the mission message must be inert in
    every state, not only AS_DRIVING."""
    for mission, state in itertools.product(ALL_MISSIONS, ALL_STATES):
        logic = make(mission, state)
        assert logic.on_mission(mission) is None
        assert logic.state == state


# ── Mux selection ──────────────────────────────────────────────────────

@pytest.mark.parametrize("mission", sorted(MANUAL_MISSIONS))
@pytest.mark.parametrize("state", ALL_STATES)
def test_manual_missions_pass_manual_cmd_except_in_emergency(mission, state):
    """Manual driving ignores the AS state — except a latched emergency, which
    must stop the kart even if the operator switches to a manual mission."""
    logic = make(mission, state)
    expected = ZERO_CMD if state == AS_EMERGENCY else MANUAL_CMD
    assert logic.mux(AUTO_CMD, MANUAL_CMD) == expected
    assert logic.heartbeat_steer_mode() is None


def test_driving_passes_autonomous_cmd():
    logic = make("autonomous", AS_DRIVING)
    assert logic.mux(AUTO_CMD, MANUAL_CMD) == AUTO_CMD
    assert logic.heartbeat_steer_mode() is None


def test_throttle_test_applies_throttle_only_while_driving():
    """throttle_test used to command 50% throttle at mission select; the fixed
    throttle now requires Start like any other autonomous mission."""
    logic = StateLogic()
    logic.on_mission("throttle_test")
    assert logic.mux(AUTO_CMD, MANUAL_CMD) == ZERO_CMD
    logic.on_state_cmd("start")
    assert logic.mux(AUTO_CMD, MANUAL_CMD) == (THROTTLE_TEST_SPEED, 0.0)
    logic.on_state_cmd("stop")
    assert logic.mux(AUTO_CMD, MANUAL_CMD) == ZERO_CMD


# ── Ordinary transitions still work ────────────────────────────────────

def test_nominal_session():
    """Select → start → finish → reset, the happy path end to end."""
    logic = StateLogic()
    assert logic.on_mission("trackdrive") == AS_READY
    new_state, force_pid = logic.on_state_cmd("start")
    assert new_state == AS_DRIVING and force_pid
    assert logic.on_state_cmd("finish") == (AS_FINISHED, False)
    assert logic.on_state_cmd("reset") == (AS_OFF, False)


def test_stop_stays_armed_for_autonomous_but_disarms_manual():
    logic = make("autonomous", AS_DRIVING)
    logic.on_state_cmd("stop")
    assert logic.state == AS_READY
    logic = make("manual", AS_READY)
    logic.on_state_cmd("stop")
    assert logic.state == AS_OFF


def test_ebs_from_any_state_but_off():
    for state in ALL_STATES:
        logic = make("autonomous", state)
        logic.on_state_cmd("ebs")
        expected = AS_OFF if state == AS_OFF else AS_EMERGENCY
        assert logic.state == expected


def test_deselecting_autonomous_mission_disarms():
    logic = make("autonomous", AS_READY)
    assert logic.on_mission("manual") == AS_OFF
    assert logic.heartbeat_steer_mode() is None


@pytest.mark.parametrize('mission', ALL_MISSIONS)
def test_emergency_allows_mission_selection_without_clearing_latch(mission):
    logic = make('manual', AS_EMERGENCY)
    assert logic.on_mission(mission) is None
    assert logic.mission == mission
    assert logic.state == AS_EMERGENCY
    assert logic.mux(AUTO_CMD, MANUAL_CMD) == ZERO_CMD
    assert logic.on_state_cmd('start') == (None, False)
