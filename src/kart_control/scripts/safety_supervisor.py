"""Orin safety supervision; actuator permission is independently enforced by Medulla.

All ages use monotonic seconds. Firmware status is a 20 Hz six-int32 frame:
[version, active_faults, latched_faults, flags, accepted_reset_token, mission].
Unknown/malformed reports cannot grant permission. Reset never grants Start.
"""
import math

from state_logic import (
    StateLogic, AS_OFF, AS_READY, AS_DRIVING, AS_FINISHED, AS_EMERGENCY,
    AUTONOMOUS_MISSIONS, ZERO_CMD, STEER_MODE_PWM,
)

MISSION_IDS = {"manual": 0, "acceleration": 1, "skidpad": 2,
               "autocross": 3, "trackdrive": 4, "ebs_test": 5,
               "inspection": 6, "remote_control": 7, "autonomous": 8,
               "throttle_test": 8}
FAULT_NAMES = ("Steering sensor invalid or stale", "Tank pressure sensor invalid",
               "Tank pressure too low", "Actuator commands stale",
               "Compressor disabled", "Invalid operating mode",
               "State heartbeat stale", "Emergency requested", "Safety output hardware error")
STATUS_TIMEOUT = 0.5
COMMAND_TIMEOUT = 0.5
RESET_TIMEOUT = 2.0


class SafetySupervisor:
    def __init__(self, bench_throttle=False):
        self.bench_throttle = bench_throttle is True
        self.bench_initialized = not self.bench_throttle
        self.bench_mode_enabled = False
        self.bench_config = None
        self.bench_config_time = None
        self.bridge_scale = None
        self.bridge_scale_time = None
        self.bench_pending = False
        self.bench_notice = ""
        self.logic = StateLogic()
        self.status = None
        self.status_time = None
        self.command_times = {"auto": None, "manual": None}
        self.command_valid = {"auto": False, "manual": False}
        self.controller_mode = None
        self.controller_mode_time = None
        self.controller_time = None
        self.controller_reason = "Waiting for controller sensor status"
        self._reset_hold = False
        self._last_reset_attempt = 0
        self.pending_reset = None
        self.reset_time = None
        self._arm_requested = False
        self._remote_active = False
        self._trip_reason = ""
        self._notice = ""
        self._now = 0.0

    @property
    def state(self):
        return self.logic.state

    @property
    def mission(self):
        return self.logic.mission

    @property
    def mission_id(self):
        return MISSION_IDS.get(self.mission, -1)

    def fresh_status(self, now):
        return (self.status is not None and self.status_time is not None
                and 0 <= now - self.status_time <= STATUS_TIMEOUT
                and self.status[5] == self.mission_id)

    def controller_ready(self, now):
        if self.mission not in AUTONOMOUS_MISSIONS or self.mission == "throttle_test":
            return True
        return (self.controller_time is not None
                and 0 <= now - self.controller_time <= STATUS_TIMEOUT
                and not self.controller_reason
                and self.controller_mode in (0, 1)
                and self.controller_mode_time is not None
                and 0 <= now - self.controller_mode_time <= STATUS_TIMEOUT)

    def on_controller_mode(self, mode, now):
        self.controller_mode = mode if mode in (0, 1) else None
        self.controller_mode_time = now

    def driving_steer_mode(self):
        return 1 if self.mission == "throttle_test" or self.controller_mode != 0 else 0

    def on_controller_safety(self, reason, now):
        self.controller_reason = reason
        self.controller_time = now

    def bench_available(self, now):
        return (self.bench_throttle and self.fresh_status(now)
                and bool(self.status[3] & 16))

    def bench_config_fresh(self, now):
        return (self.bench_config is not None and self.bench_config_time is not None
                and 0 <= now - self.bench_config_time <= STATUS_TIMEOUT
                and self.bridge_scale is not None and self.bridge_scale > 0
                and self.bridge_scale_time is not None
                and 0 <= now - self.bridge_scale_time <= 2.0)

    def bench_percent(self, now):
        if not self.bench_config_fresh(now):
            return None
        return self.bench_config["max_speed"] / self.bridge_scale * 100

    def bench_eligibility(self, now):
        if not self.bench_available(now):
            return "Fresh paired bench firmware required"
        if not self.bench_initialized:
            return "Waiting for verified zero throttle at startup"
        if self.status[2] or self.status[1] & ~4:
            return "Other safety faults must be repaired"
        if self.mission != "autonomous" or self.controller_mode != 1:
            return "Select Autonomous and steering None"
        if not self.bench_config_fresh(now):
            return "Waiting for controller and bridge settings"
        if (self.bench_config["steering"] != "none" or self.bench_config["speed"]
                not in ("constant_throttle", "constant_throttle_blind", "constant_throttle_stop")):
            return "Select steering None and constant throttle"
        if not self.controller_ready(now):
            return "Controller inputs must be fresh and healthy"
        percent = self.bench_percent(now)
        if percent is None or not math.isfinite(percent) or not 0 <= percent <= 30:
            return "Throttle must be between 0 and 30%"
        if self.bench_pending:
            return "Applying throttle setting"
        return ""

    def set_bench_mode(self, enabled, now):
        if type(enabled) is not bool:
            self.bench_notice = "Bench enable must be a boolean"
            return False
        if not enabled:
            self.bench_mode_enabled = False
            self._arm_requested = self._remote_active = False
            if self.state != AS_EMERGENCY:
                self.logic.state = AS_OFF
            self.bench_notice = "No-air bench mode OFF; propulsion inhibited"
            return True
        reason = ("Stop before enabling bench mode" if self.state not in (AS_OFF, AS_READY)
                  else self.bench_eligibility(now))
        if reason:
            self.bench_notice = reason
            return False
        self.bench_mode_enabled = True
        self._arm_requested = True
        self.bench_notice = ""
        return True

    def validate_bench_throttle(self, percent, now):
        if type(percent) not in (int, float) or not math.isfinite(percent) or not 0 <= percent <= 30:
            return "Throttle must be a finite number from 0 to 30%"
        if not self.bench_initialized and percent != 0:
            return "Waiting for verified zero throttle at startup"
        if self.state not in (AS_OFF, AS_READY):
            return "Stop before changing throttle"
        if not self.bench_available(now) or not self.bench_config_fresh(now):
            return "Fresh paired bench firmware and settings required"
        if (self.mission != "autonomous" or self.controller_mode != 1
                or self.bench_config["steering"] != "none" or self.bench_config["speed"] not in (
                "constant_throttle", "constant_throttle_blind", "constant_throttle_stop")):
            return "Select Autonomous, steering None and constant throttle"
        if self.status[2] or self.status[1] & ~4:
            return "Other safety faults must be repaired"
        if self.bench_pending:
            return "Applying throttle setting"
        return ""

    def bench_snapshot(self, now):
        available = self.bench_available(now)
        return dict(bench_mode_available=available,
                    bench_mode_enabled=self.bench_mode_enabled and available,
                    bench_mode_reason=self.bench_notice or self.bench_eligibility(now) or
                        ("No-air bench mode ON" if self.bench_mode_enabled else "No-air bench mode OFF"),
                    bench_throttle_cap_percent=30 if available else None,
                    bench_throttle_percent=self.bench_percent(now) if available else None,
                    bench_pressure_fault=bool(self.status[1] & 4) if available else None)

    def bench_throttle_authorized(self, now):
        return (self.bench_mode_enabled and not self.bench_pending
                and not self.bench_eligibility(now) and self.mission == "autonomous"
                and self.controller_mode == 1 and self.fresh_status(now)
                and bool(self.status[3] & 16))

    def blocking_faults(self, now):
        faults = self.status[1]
        return faults & ~4 if self.bench_throttle_authorized(now) else faults

    def ready(self, now):
        return (self.fresh_status(now) and self.blocking_faults(now) == 0
                and ((not self.bench_throttle and not self.status[3] & 16)
                     or self.bench_throttle_authorized(now))
                and self.status[2] == 0 and bool(self.status[3] & 1)
                and self.controller_ready(now))

    def bench(self, now):
        return (self.mission == "remote_control" and self.fresh_status(now)
                and self.status[2] == 0 and bool(self.status[3] & 4)
                and not self.status[1] & ((1 << 3) | (1 << 5) | (1 << 6) | (1 << 8)))

    def command_fresh(self, kind, now):
        stamp = self.command_times[kind]
        return (self.command_valid[kind] and stamp is not None
                and 0 <= now - stamp <= COMMAND_TIMEOUT)

    def note_command(self, kind, command, now):
        self.command_times[kind] = now
        self.command_valid[kind] = all(math.isfinite(v) for v in command)

    def on_safety(self, payload, now):
        valid = (len(payload) == 6 and all(isinstance(v, int) for v in payload)
                 and payload[0] == 1 and 0 <= payload[1] <= 511
                 and 0 <= payload[2] <= 511 and 0 <= payload[3] <= 31
                 and 0 <= payload[4] <= 2147483647 and 0 <= payload[5] <= 8
                 and bool(payload[3] & 2) == bool(payload[2]))
        if not valid:
            self.status = None
            self._notice = "Invalid firmware safety status"
            return
        self.status = tuple(payload)
        self.status_time = now
        self._notice = ""
        if (self.pending_reset is not None and payload[4] == self.pending_reset
                and payload[1] == 0 and payload[2] == 0
                and payload[5] == self.mission_id
                and now - self.reset_time <= RESET_TIMEOUT):
            self.logic.state = AS_OFF
            self._reset_hold = True
            self.pending_reset = None
            self._arm_requested = False
            self._remote_active = False
            self._trip_reason = ""
            self.command_times = {"auto": None, "manual": None}
            self.command_valid = {"auto": False, "manual": False}

    def _trip(self, reason):
        if self.state != AS_EMERGENCY:
            self._trip_reason = reason
        self.logic.state = AS_EMERGENCY
        self._arm_requested = False

    def tick(self, now):
        self._now = now
        if self.pending_reset is not None and now - self.reset_time > RESET_TIMEOUT:
            self.pending_reset = None
            self._notice = "Reset not acknowledged; check inputs and press Reset again"
        # A firmware latch is authoritative even if the operator changed mission.
        if self.status is not None and self.status[2]:
            self._trip(self._fault_text(self.status[2]))
        elif self.state in (AS_READY, AS_DRIVING) and not self.ready(now):
            self._trip(self.reason(now))
        elif self._remote_active and not (self.ready(now) or self.bench(now)):
            self._trip(self.reason(now))
        if self.state == AS_DRIVING and self.mission != "throttle_test":
            if not self.command_fresh("auto", now):
                self._trip("Controller command missing, invalid or stale")
        if self._remote_active and not self.command_fresh("manual", now):
            self._trip("Remote command missing, invalid or stale")
        if self._arm_requested and self.state == AS_OFF and self.ready(now):
            self.logic.state = AS_READY
        return self.state

    def on_mission(self, mission):
        old = self.state
        if mission not in MISSION_IDS:
            self._trip("Unknown mission")
            return self.state
        if self.state == AS_EMERGENCY:
            # Do not change the required sensor set to evade a latched fault.
            return None
        if mission == self.mission and self.state != AS_OFF:
            return None
        self._reset_hold = False
        self.logic.on_mission(mission)
        if self.state == AS_EMERGENCY:
            self._trip_reason = "Mission changed while driving"
        self._remote_active = False
        self._arm_requested = mission in AUTONOMOUS_MISSIONS and self.state != AS_EMERGENCY
        if self._arm_requested:
            self.logic.state = AS_READY if self.ready(self._now) else AS_OFF
        return self.state if self.state != old else None

    def on_state_cmd(self, cmd, now):
        self.tick(now)
        if cmd == "reset" and self.state in (AS_EMERGENCY, AS_FINISHED):
            if self.pending_reset is None:
                if self.fresh_status(now) and self.status[1] == 0 and self.controller_ready(now):
                    token = max(self._last_reset_attempt, self.status[4]) + 1
                    if token > 2147483647:
                        self._notice = "Reset token exhausted; firmware restart required"
                        return None, False
                    self.pending_reset = self._last_reset_attempt = token
                    self.reset_time = now
                    self._arm_requested = False
                    if self.state == AS_FINISHED:
                        self.logic.state = AS_OFF
                else:
                    self._notice = "Reset refused: required inputs must be healthy"
            return None, False
        if cmd == "ebs":
            self._trip("Emergency requested")
            return AS_EMERGENCY, False
        if self.state == AS_EMERGENCY:
            return None, False
        if cmd == "start" and (not self.ready(now) or (
                self.mission != "throttle_test" and not self.command_fresh("auto", now))):
            self._notice = "Start refused: " + (self.reason(now) or "Waiting for a fresh controller command")
            return None, False
        # A reset is acknowledged through on_safety, never a local transition.
        if cmd == "reset":
            return None, False
        self._notice = ""
        return self.logic.on_state_cmd(cmd)

    def mux(self, auto_cmd, manual_cmd, now):
        if self.state == AS_EMERGENCY or self.pending_reset is not None or self._reset_hold:
            return ZERO_CMD
        if self.mission == "remote_control":
            if not self.command_fresh("manual", now):
                return ZERO_CMD
            if self.bench(now):
                self._remote_active = True
                return (0.0, manual_cmd[1])
            if self.ready(now):
                self._remote_active = True
                return manual_cmd
            return ZERO_CMD
        if self.mission == "manual":
            return ZERO_CMD  # Physical pedal ownership is decided by the firmware.
        if not self.ready(now):
            return ZERO_CMD
        return self.logic.mux(auto_cmd, manual_cmd)

    def heartbeat_steer_mode(self):
        if self.state == AS_EMERGENCY:
            return STEER_MODE_PWM
        return self.logic.heartbeat_steer_mode()

    @staticmethod
    def _fault_text(bits):
        return "; ".join(name for bit, name in enumerate(FAULT_NAMES) if bits & (1 << bit))

    def reason(self, now):
        if self.pending_reset is not None:
            return "Reset requested; waiting for firmware acknowledgment"
        if self._notice:
            return self._notice
        if self.state == AS_EMERGENCY and self._trip_reason:
            return self._trip_reason + "; repair the fault, then press Reset"
        if not self.fresh_status(now):
            return "Waiting for fresh firmware safety status for this mission"
        if self.bench_throttle_authorized(now) and self.ready(now):
            return "BENCH THROTTLE: 30% electrical cap; steering disabled; " + (
                "tank pressure too low (bench override active)"
                if self.status[1] & 4 else "elevated wheels only")
        if self.status[3] & 16 and not self.bench_mode_enabled:
            return "No-air bench mode OFF; propulsion inhibited; " + self._fault_text(self.status[1] | self.status[2])
        faults = self.status[1] | self.status[2]
        if faults:
            return self._fault_text(faults)
        if not self.controller_ready(now):
            if self.controller_time is None or now - self.controller_time > STATUS_TIMEOUT:
                return "Controller sensor status missing or stale"
            return self.controller_reason or "Controller steering mode missing or stale"
        if self._reset_hold:
            return "Reset complete; select a mission before starting"
        if self.bench(now):
            return "Bench steering only; propulsion inhibited"
        if not self.ready(now):
            return "Firmware has not granted drive permission"
        return ""
