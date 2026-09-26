"""Pure decision logic for the AS state machine — no ROS imports.

state_machine_node.py wraps this class: every safety-relevant decision (state
transitions, what the mux outputs, when the steering actuator may be powered)
lives here so it can be tested with plain pytest on any machine, without ROS.
The node's job is reduced to plumbing: subscribe, call, publish.

Safety invariants this module guarantees (enforced by test/test_state_logic.py):
  - AS_DRIVING is reachable only by a literal "start" command from AS_READY.
  - Outside AS_DRIVING, an autonomous mission commands zero output AND direct-PWM
    steer mode, where zero means the steering motor is unpowered. In PID mode a
    zero steering command is a real "go to centre" target — that combination
    powered the steering motor at mission select on 2026-08-10.
  - Changing mission while AS_DRIVING goes to AS_EMERGENCY: a mission change
    mid-drive is never intentional, so it is treated as an operator error, not
    a reconfiguration. Recovery requires "reset".
"""

# AS (Autonomous System) states, Formula Student conventions
AS_OFF = 0
AS_READY = 1
AS_DRIVING = 2
AS_FINISHED = 3
AS_EMERGENCY = 4

STATE_NAMES = {
    AS_OFF: "AS_OFF",
    AS_READY: "AS_READY",
    AS_DRIVING: "AS_DRIVING",
    AS_FINISHED: "AS_FINISHED",
    AS_EMERGENCY: "AS_EMERGENCY",
}

# Missions that count as "autonomous"
AUTONOMOUS_MISSIONS = {
    "autonomous", "acceleration", "skidpad", "autocross", "trackdrive",
    "ebs_test", "inspection", "throttle_test",
}

MANUAL_MISSIONS = {"manual", "remote_control"}

STEER_MODE_PID = 0   # ESP32 closes an angle loop; 0 rad = drive to centre and hold
STEER_MODE_PWM = 1   # direct PWM; 0 = motor unpowered

# throttle_test: fixed throttle for hardware debugging, no perception needed.
# 2.5 / max_speed(5.0) = 50% throttle.
THROTTLE_TEST_SPEED = 2.5

ZERO_CMD = (0.0, 0.0)  # (linear_x, angular_z)


class StateLogic:
    """AS state machine + command mux, as pure functions of (mission, state)."""

    def __init__(self):
        self.state = AS_OFF
        self.mission = "manual"

    # ── Inputs ─────────────────────────────────────────────────────────

    def on_mission(self, mission: str):
        """Handle a mission selection. Returns the new state, or None if unchanged.

        Re-sending the current mission is a no-op: the dashboard repeats the
        mission message for 1 s to ensure delivery, and a repeat must never
        trigger a transition (especially not the mid-drive emergency below).
        """
        if self.state == AS_EMERGENCY:
            return None
        old = self.mission
        if old == mission:
            return None

        # Mission change while driving is never intentional — stop must come
        # first. Latch emergency; "reset" is the only way out.
        if self.state == AS_DRIVING:
            return self._set_state(AS_EMERGENCY)

        self.mission = mission
        if mission not in AUTONOMOUS_MISSIONS and self.state != AS_OFF:
            return self._set_state(AS_OFF)
        if mission in AUTONOMOUS_MISSIONS and self.state in (AS_OFF, AS_FINISHED):
            # Arm only. PID steer mode is NOT forced here: arming the position
            # loop is a driving action (see module docstring).
            return self._set_state(AS_READY)
        return None

    def on_state_cmd(self, cmd: str):
        """Handle start/stop/ebs/finish/reset.

        Returns (new_state, force_pid): new_state is None when the command was
        ignored; force_pid is True only on the start of an autonomous mission,
        the one moment the steering position loop may be armed.
        """
        s = self.state
        if cmd == "start" and s == AS_READY:
            return self._set_state(AS_DRIVING), self.mission in AUTONOMOUS_MISSIONS
        if cmd == "stop" and s in (AS_READY, AS_DRIVING, AS_FINISHED):
            # Stay armed (AS_READY) if an autonomous mission is selected,
            # matching real FS behavior: stop driving ≠ deselect mission.
            if self.mission in AUTONOMOUS_MISSIONS:
                return self._set_state(AS_READY), False
            return self._set_state(AS_OFF), False
        if cmd == "ebs" and s != AS_OFF:
            return self._set_state(AS_EMERGENCY), False
        if cmd == "finish" and s == AS_DRIVING:
            return self._set_state(AS_FINISHED), False
        if cmd == "reset" and s in (AS_FINISHED, AS_EMERGENCY):
            return self._set_state(AS_OFF), False
        return None, False

    # ── Outputs ────────────────────────────────────────────────────────

    def mux(self, auto_cmd, manual_cmd):
        """Select the (linear_x, angular_z) to command, given the stored latest
        autonomous and manual Twists. Called at 100 Hz."""
        if self.mission in MANUAL_MISSIONS:
            # A latched emergency (EBS pressed, or mission changed mid-drive)
            # overrides the driver until "reset" — otherwise switching to a
            # manual mission would bypass the stop.
            if self.state == AS_EMERGENCY:
                return ZERO_CMD
            return manual_cmd
        if self.mission in AUTONOMOUS_MISSIONS and self.state == AS_DRIVING:
            if self.mission == "throttle_test":
                return (THROTTLE_TEST_SPEED, 0.0)
            return auto_cmd
        # Zero output. Safe only because heartbeat_steer_mode() holds direct-PWM
        # mode in exactly these situations, where 0 means unpowered.
        return ZERO_CMD

    def heartbeat_steer_mode(self):
        """Steer mode to assert from the 10 Hz heartbeat, or None.

        Holds the steering actuator unpowered (direct-PWM, zero output) whenever
        an autonomous mission is selected but the kart is not driving.
        Re-asserted continuously so a dashboard mode toggle cannot silently
        re-power the motor before Start.
        """
        if self.mission in AUTONOMOUS_MISSIONS and self.state != AS_DRIVING:
            return STEER_MODE_PWM
        return None

    # ── Internal ───────────────────────────────────────────────────────

    def _set_state(self, new_state: int) -> int:
        self.state = new_state
        return new_state
