"""Pure protocol helpers — no ROS dependencies.

Payload encoding: int32 arrays. Each value is a signed 32-bit integer.
Floats are scaled to int32 before transmission:
  - Steering angle: radians x 1000  (milliradians)
  - Throttle/braking effort: x 255  (0-255 range)
  - Speed: m/s x 1000
  - Acceleration: m/s^2 x 1000

The framing (SOF/LEN/TYPE/CRC) is unchanged — only payload encoding changed.
"""
import math
import threading
import time

from kb_dashboard.hall_speed import HallSpeed

# Frame type constants (from kb_interfaces/msg/Frame)
ESP_ACT_SPEED = 0x01
ESP_ACT_ACCELERATION = 0x02
ESP_ACT_BRAKING = 0x03
ESP_ACT_STEERING = 0x04
ESP_HEARTBEAT = 0x08
ESP_HEALTH_STATUS = 0x0B
ESP_STEER_PID = 0x0D
ORIN_TARG_THROTTLE = 0x20
ORIN_TARG_BRAKING = 0x21
ORIN_TARG_STEERING = 0x22
ORIN_STEER_MODE = 0x29
ORIN_COMPRESSOR_DISABLE = 0x2A
ORIN_STEER_PID = 0x2B

# Steering-PID tuning bounds, mirroring PID_REMOTE_MAX_* in kart-medulla's main.c.
# The firmware clamps independently and its clamp is the one that matters — these
# exist so the dashboard can reject a typo before it travels, and so the input
# fields can show the range. If the firmware constants change, change these too;
# the two drifting apart shows up as a value that is accepted here and silently
# reduced there, which the ESP_STEER_PID echo will reveal but only if someone looks.
PID_MAX_KP = 20.0
PID_MAX_KI = 10.0
PID_MAX_KD = 5.0
# Deliberately below the actuator's own 1.0 ceiling: this caps steering PWM that
# can be set remotely, and the steering gear teeth have been stripped once already.
PID_MAX_PWM_LIMIT = 0.60

MISSIONS = {
    "manual": 0,
    "remote_control": 7,
    "autonomous": 8,
    "acceleration": 1,
    "skidpad": 2,
    "autocross": 3,
    "trackdrive": 4,
    "ebs_test": 5,
    "inspection": 6,
}


# ── Decoders (ESP32 → Orin) ─────────────────────────────────────────

def decode_steering(payload) -> float:
    """@brief Decode steering payload (1 int32: angle_rad x 1000) to float radians.

    @param payload List of int32 values from the Frame.
    @return Steering angle in radians.
    """
    if len(payload) < 1:
        return 0.0
    return payload[0] / 1000.0


def decode_steering_raw(payload) -> tuple:
    """@brief Decode steering payload to (angle_rad, raw_encoder, pid_pwm).

    Payload: [angle_rad x 1000, raw_encoder, pid_out x 1000, valid].

    The 4th field was appended when the steering sensor moved from an AS5600 on
    I2C to an MT6701 read over PWM: 1 = the angle is a real measurement, 0 = the
    firmware has no angle and the first field is a sentinel, not a reading.

    @param payload List of int32 values from the Frame.
    @return Tuple of (angle in radians, raw 12-bit encoder value,
            PID output -1.0 to 1.0, angle-is-valid bool).
    """
    if len(payload) < 1:
        return 0.0, 0, 0.0, False
    angle_rad = payload[0] / 1000.0
    raw_encoder = payload[1] if len(payload) >= 2 else 0
    pid_pwm = payload[2] / 1000.0 if len(payload) >= 3 else 0.0
    # Field 4 is the firmware's own verdict on whether the angle is real. When it
    # says no, the angle field carries INT32_MIN rather than a plausible number,
    # so both checks below catch it. Firmware predating the field sends 3 values;
    # treat that as valid, since it had no way to say otherwise.
    if len(payload) >= 4:
        valid = bool(payload[3]) and payload[0] != -(2 ** 31)
    else:
        valid = payload[0] != -(2 ** 31)
    return angle_rad, raw_encoder, pid_pwm, valid


def decode_speed(payload) -> float:
    """@brief Decode speed payload (1 int32: speed_mps x 1000) to float m/s.

    @param payload List of int32 values from the Frame.
    @return Speed in m/s.
    """
    if len(payload) < 1:
        return 0.0
    return payload[0] / 1000.0


def decode_accel(payload) -> tuple:
    """@brief Decode acceleration payload to (lateral, longitudinal) in m/s^2.

    Payload: [lateral x 1000, longitudinal x 1000].

    @param payload List of int32 values from the Frame.
    @return Tuple of (lateral, longitudinal) acceleration in m/s^2.
    """
    if len(payload) < 2:
        return 0.0, 0.0
    return payload[0] / 1000.0, payload[1] / 1000.0


def decode_braking(payload) -> float:
    """@brief Decode braking payload (1 int32: effort x 255) to float 0.0-1.0.

    @param payload List of int32 values from the Frame.
    @return Braking effort normalized to 0.0-1.0.
    """
    if len(payload) < 1:
        return 0.0
    return payload[0] / 255.0


def decode_throttle(payload) -> float:
    """@brief Decode throttle payload (1 int32: effort x 255) to float 0.0-1.0.

    @param payload List of int32 values from the Frame.
    @return Throttle effort normalized to 0.0-1.0.
    """
    if len(payload) < 1:
        return 0.0
    return payload[0] / 255.0


def decode_pedals(payload) -> dict:
    """@brief Decode driver-pedal telemetry (ESP_PEDALS, 0x0E, ~20 Hz) to dict.

    Payload (4 int32 fields): [acc_mv, brake_mv, acc_effort, brake_effort].
    - acc_mv / brake_mv: eFuse-calibrated millivolts at the ADC pin (GPIO 4 /
      GPIO 5 on the ESP32-S3). The pedal outputs 0-5 V; the board halves it
      with a 10k/10k divider (R14/R15 accel, R16/R17 brake, dv-hardware
      kart-medulla_P1 schematic), so full pedal is ~2500 mV at the pin.
    - acc_effort / brake_effort: 0-255 normalized in firmware from PROVISIONAL
      min/max constants (main.c PEDAL_MIN_MV/PEDAL_MAX_MV) — approximate until
      the pedal endpoints are measured on the kart. The mV fields are the
      trustworthy ones.

    Fields are only ever appended, so shorter payloads still decode; missing
    fields come back as 0.0/0 rather than crashing.

    @param payload List of int32 values from the Frame.
    @return Dict with esp32_throttle / esp32_brake_pedal (0.0-1.0 efforts)
            and esp32_pedal_acc_mv / esp32_pedal_brake_mv (raw pin mV).
    """
    out = {
        "esp32_pedal_acc_mv": 0,
        "esp32_pedal_brake_mv": 0,
        "esp32_throttle": 0.0,
        "esp32_brake_pedal": 0.0,
    }
    if len(payload) >= 2:
        out["esp32_pedal_acc_mv"] = payload[0]
        out["esp32_pedal_brake_mv"] = payload[1]
    if len(payload) >= 4:
        out["esp32_throttle"] = payload[2] / 255.0
        out["esp32_brake_pedal"] = payload[3] / 255.0
    return out


def decode_health_flags(payload) -> dict:
    """@brief Decode the flags-only health payload from /esp32/health/flags.

    kb_coms_micro splits the ESP32's health frame across two topics: this one
    carries a single int32 of flag bits, and /esp32/health/data carries the
    numbers. Decoding either half with decode_health() does not work — it needs
    4 fields and returns all-False for anything shorter, which is exactly how the
    dashboard's health pills silently read "bad" while the ESP32 was healthy.

    @param payload List with one int32 of flag bits.
    @return Dict of the boolean health fields only.
    """
    flags = payload[0] if len(payload) >= 1 else 0
    return {
        "health_magnet_ok": bool(flags & 0x01),
        "health_i2c_ok": bool(flags & 0x02),
        "health_heap_ok": bool(flags & 0x04),
        "health_steer_ok": bool(flags & 0x08),
        "health_steer_trip": bool(flags & 0x10),
    }


def decode_health_data(payload) -> dict:
    """@brief Decode the numeric health payload from /esp32/health/data.

    kb_coms_micro forwards every field of the firmware's health frame except the
    leading flags word. It used to copy exactly three, which silently dropped the
    steering frame counters the firmware had been sending all along; it now
    copies the whole tail, so anything the firmware appends arrives here.

    Each field is optional and decoded positionally, so an older firmware sending
    a shorter frame still decodes — the keys for the missing tail are simply
    absent rather than wrong.

    @param payload List of int32: [agc, heap_kb, i2c_errors, steer_frames,
                   steer_rejects, steer_trip_age_s].
    @return Dict of the numeric health fields present.
    """
    out = {}
    if len(payload) >= 1:
        out["health_agc"] = payload[0]
    if len(payload) >= 2:
        out["health_heap_kb"] = payload[1]
    if len(payload) >= 3:
        out["health_i2c_errors"] = payload[2]
    if len(payload) >= 4:
        out["health_steer_frames"] = payload[3]
    if len(payload) >= 5:
        out["health_steer_rejects"] = payload[4]
    if len(payload) >= 6:
        # Seconds since the steering fault latched, or -1 while it is clear. The
        # latch survives until the ESP32 reboots, so the trip flag alone cannot
        # distinguish a live fault from one latched during an earlier boot — the
        # ambiguity that made a stale latch look like a sensor failure on
        # 2026-08-08. None here means the firmware predates the field.
        out["health_steer_trip_age_s"] = payload[5]
    # The ROS numeric health topic omits the firmware's leading flags word.
    if len(payload) >= 14:
        out.update({
            "hall_init_err": payload[6],
            "hall_bits": payload[7],
            "hall_edges": [v & 0xFFFFFFFF for v in payload[8:11]],
            "hall_age_ms": payload[11],
            "hall_interval_us": payload[12],
            "hall_multi_changes": payload[13] & 0xFFFFFFFF,
        })
    return out


def decode_hall(payload) -> dict:
    """Decode the dedicated eight-word Hall capture frame, preserving validity."""
    if len(payload) != 8:
        return {"hall_init_err": None, "hall_bits": None, "hall_edges": None}
    return {
        "hall_init_err": payload[0], "hall_bits": payload[1],
        "hall_edges": [v & 0xFFFFFFFF for v in payload[2:5]],
        "hall_age_ms": payload[5], "hall_interval_us": payload[6],
        "hall_multi_changes": payload[7] & 0xFFFFFFFF,
    }


def decode_health(payload) -> dict:
    """@brief Decode health payload to dict.

    Payload: [flags, agc, heap_kb, i2c_errors, steer_frames, steer_rejects].
    Flags: bit0=magnet_ok, bit1=i2c_ok, bit2=heap_ok, bit3=steer_ok,
           bit4=steer_trip.

    bit0/bit1 and `agc` are AS5600-over-I2C facts. On the ESP32-S3 kart board the
    firmware no longer polls an AS5600 — the sensor is an MT6701 read over PWM —
    so those two bits are permanently False there and must NOT be used as
    "is the steering sensor working". bit3 is that answer. bit4 means the
    steering fault latched: the EBS fired and throttle is refused until reboot.

    steer_frames/steer_rejects separate the failure modes: frames flat at 0 means
    no signal edges at all, rejects climbing with frames flat means edges are
    arriving at the wrong rate.

    @param payload List of int32 values from the Frame.
    @return Dict with health_magnet_ok, health_i2c_ok, health_heap_ok, etc.
    """
    if len(payload) < 4:
        return {
            "health_magnet_ok": False,
            "health_i2c_ok": False,
            "health_heap_ok": False,
            "health_steer_ok": False,
            "health_steer_trip": False,
            "health_agc": 0,
            "health_heap_kb": 0,
            "health_i2c_errors": 0,
            "health_steer_frames": 0,
            "health_steer_rejects": 0,
        }
    flags = payload[0]
    return {
        "health_magnet_ok": bool(flags & 0x01),
        "health_i2c_ok": bool(flags & 0x02),
        "health_heap_ok": bool(flags & 0x04),
        "health_steer_ok": bool(flags & 0x08),
        "health_steer_trip": bool(flags & 0x10),
        "health_agc": payload[1],
        "health_heap_kb": payload[2],
        "health_i2c_errors": payload[3],
        "health_steer_frames": payload[4] if len(payload) >= 5 else 0,
        "health_steer_rejects": payload[5] if len(payload) >= 6 else 0,
    }


# Tank pressure. Sensor = Festo SDE5-D10 (567465): 0-10 bar -> 0-10 V, so 1 V/bar
# with a 0 V zero offset (datasheet in ~/dv/datasheets/). On the board it goes
# through three equal 10 k resistors, R11/R12/R13 -- net PRESSURE_n__0_10V ->
# PRESSURE_n__0_3V3 -- so the pin sees a third of it. Hence:
#
#     bar = 3 * V_pin
#
# That is the whole conversion, and every term in it is a documented property of
# the sensor or the schematic. There is no fitted constant and nothing to anchor.
#
# The firmware sends the pin voltage in MILLIVOLTS (ESP_PNEUMATIC fields 8 and 9),
# converted on-chip through the ESP32's eFuse ADC calibration. That is deliberate:
# a raw count only becomes a voltage if you know the ADC's real full scale, which
# is neither the 3.3 V rail nor a number worth guessing, and guessing it is what
# made this dial disagree with the firmware. The chip knows its own answer, so it
# is the chip's job.
#
# History, so nobody re-derives the wrong thing. Until 2026-07-26 this file used
# bar = 3.0 * (raw/4095 * 3.3). It was then briefly replaced by an "anchor" of
# 7.5/2679, taken from a line in a 2026-07-18 commit message, and a divider ratio of
# ~4:1 was invented to make that number fit. Both are withdrawn. R11/R12/R13 are all
# 10 k, so the divider is exactly 3:1, and the 7.5 figure is unusable: there is no
# mechanical gauge on this kart, the wiring and firmware have changed since, a
# regulator may sit between that measurement point and this sensor, and the two
# numbers may not be simultaneous. It records a number, not a measurement. Full
# write-up in kart-medulla .agents/error-log.md 2026-07-27.
#
# RANGE CEILING, worth knowing: the divider maps the sensor's 0-10 V onto 0-3.33 V,
# but the ESP32-S3 ADC at 11 dB is only good to about 2900 mV. Readings saturate
# around 8.7 bar, so anything at or above that is out of range rather than a
# pressure. The dial's 10 bar top end is therefore unreachable by measurement.
BAR_PER_PIN_VOLT = 3.0      # three equal 10 k resistors: the pin sees V_sensor / 3

# Fallback only, for firmware too old to send millivolts. Same shape as the map
# this file used before 2026-07-26, and it inherits that map's unverified 3.3 V
# full-scale assumption -- which is precisely why the firmware now sends mV. Any
# bar figure derived through here is approximate.
ADC_VREF_V_ASSUMED = 3.3
ADC_FULL_SCALE = 4095.0     # 12-bit
SENSOR_MAX_BAR = 10.0       # SDE5-D10 span; above this the reading is out of range
ADC_CEILING_MV = 2900.0     # 11 dB effective range; readings at/above are saturated


def _bar_from_mv(mv):
    """@brief Pin millivolts -> bar, or None when the reading is not trustworthy.

    bar = 3 * V_pin: the SDE5 gives 1 V/bar and the board divides by three. Returns
    None rather than a number when the ADC is saturated or the result exceeds the
    sensor's span, because a pegged channel is indistinguishable from a fault and
    must not render as a confident pressure.
    """
    if mv is None or mv < 0 or mv >= ADC_CEILING_MV:
        return None
    bar = BAR_PER_PIN_VOLT * mv / 1000.0
    return round(bar, 2) if bar <= SENSOR_MAX_BAR else None


def _bar_from_raw_approx(adc):
    """@brief Fallback for firmware too old to send mV. Approximate — see above."""
    if adc is None or adc < 0 or adc >= ADC_FULL_SCALE:
        return None
    return round(BAR_PER_PIN_VOLT * (adc / ADC_FULL_SCALE * ADC_VREF_V_ASSUMED), 2)


def decode_pneumatic(payload) -> dict:
    """@brief Decode pneumatics telemetry to dict.

    Payload (8 fields): [pres1_adc, compressor_duty, pres2_adc, compressor_state,
    control_iters, ledc_readback, gpio_init_err, sdc_level]. pres1_adc is
    PRESSURE_1, the tank sensor. pres2_adc is PRESSURE_2, the piston/brake-line
    sensor. Both are raw 12-bit ADC (0-4095). compressor_duty is 0-255 with
    0 = MOSFET off. compressor_state is 0 idle / 1 running / 2 cooldown /
    3 disabled by the operator / 4 pumping
    latched off after a full burst produced no pressure rise (dead sensor, dead
    compressor or a large leak) / 5 tank reading pegged over-range. sdc_level is the shutdown-circuit pin read back
    off the pin itself: 1 = chain closed, 0 = emergency asserted, -1 = no such
    pin on that build.

    Fields are only ever appended, so shorter payloads from older firmware still
    decode. Anything absent comes back None (or 0 for compressor_state) so it
    renders as "--" rather than as a confident wrong value — a missing sdc_level
    must not display as "chain open", which is a real and different condition.

    PRESSURE_3 exists on the board (GPIO 1) but is shared with the steering
    sensor's PWM-angle input and has no sensor fitted, so it is deliberately not sent.

    @param payload List of int32 values from the Frame.
    @return Dict with pneu_tank_bar, pneu_piston_bar (floats or None),
            esp32_compressor_on (bool), esp32_compressor_duty (int 0-255),
            esp32_compressor_state (int) and esp32_sdc_closed (bool or None).
    """
    if len(payload) < 2:
        return {
            "pneu_tank_bar": None,
            "pneu_piston_bar": None,
            "esp32_compressor_on": False,
            "esp32_compressor_duty": 0,
            "esp32_compressor_state": 0,
            "esp32_sdc_closed": None,
        }
    pressure_adc, comp_duty = payload[0], payload[1]
    # PRESSURE_2 sits behind an identical 3:1 divider (same three-10k pattern in the
    # schematic) and uses the same conversion. No sensor is fitted to it yet, so the
    # channel normally floats — which is why the saturation guard below matters.
    # A channel pinned at full scale is NOT a reading. An unconnected ADC input floats to the
    # rail, and converting that gives a confident-looking 9.9 bar - which is what PRESSURE_2
    # displayed on 2026-07-25 with no sensor fitted. A genuine sensor pegged at full scale is
    # equally out of range and equally untrustworthy, so both collapse to None and the panel
    # shows "-- bar". Never emit a number that cannot be distinguished from a fault.
    pres2_adc = payload[2] if len(payload) >= 3 else None
    # -1 means the build has no SDC pin; anything else is the measured level.
    sdc_raw = payload[7] if len(payload) >= 8 else None
    # Fields 8/9 are the calibrated pin voltages. Prefer them whenever present:
    # they need no assumption about the ADC's full scale, only the divider ratio.
    # Negative calibrated values mean invalid: never fall back to a stale raw count.
    pres1_mv = payload[8] if len(payload) >= 9 else None
    pres2_mv = payload[9] if len(payload) >= 10 else None
    tank_bar = _bar_from_mv(pres1_mv) if pres1_mv is not None else _bar_from_raw_approx(pressure_adc)
    piston_bar = _bar_from_mv(pres2_mv) if pres2_mv is not None else _bar_from_raw_approx(pres2_adc)
    return {
        "pneu_tank_bar": tank_bar,
        "pneu_piston_bar": piston_bar,
        "pneu_tank_mv": pres1_mv if tank_bar is not None else None,
        "pneu_calibrated": pres1_mv is not None,
        "esp32_compressor_on": comp_duty > 0,
        "esp32_compressor_duty": comp_duty,
        "esp32_compressor_state": payload[3] if len(payload) >= 4 else 0,
        "esp32_sdc_closed": (sdc_raw == 1) if sdc_raw is not None and sdc_raw >= 0 else None,
    }


def decode_heartbeat(payload) -> int:
    """@brief Decode heartbeat payload to uptime in milliseconds.

    @param payload List of int32 values from the Frame.
    @return Uptime in milliseconds.
    """
    if len(payload) < 1:
        return 0
    return payload[0]


# ── Encoders (Orin → ESP32) ─────────────────────────────────────────

def encode_steering(angle_rad: float) -> list:
    """@brief Encode steering target as [angle_rad x 1000].

    @param angle_rad Steering angle in radians.
    @return List with one int32 element (milliradians).
    """
    return [int(angle_rad * 1000)]


def encode_throttle(effort: float) -> list:
    """@brief Encode throttle target as [effort x 255].

    @param effort Throttle effort 0.0-1.0.
    @return List with one int32 element (0-255).
    """
    return [int(effort * 255)]


def encode_braking(effort: float) -> list:
    """@brief Encode braking target as [effort x 255].

    @param effort Braking effort 0.0-1.0.
    @return List with one int32 element (0-255).
    """
    return [int(effort * 255)]


def encode_steer_mode(mode: int) -> list:
    """@brief Encode steering mode as [mode].

    @param mode 0=PID (angle target), 1=direct PWM.
    @return List with one int32 element.
    """
    return [int(mode)]


def encode_compressor_disable(disabled: bool) -> list:
    """@brief Encode the EBS compressor operator latch as [disabled].

    Sent as "disabled" rather than "enabled" to match the firmware's object
    store, where every value starts at 0 and 0 therefore has to mean "compressor
    runs normally" (see COMPRESSOR_DISABLED in kart-medulla's km_objects.h).

    @param disabled True to stop the compressor and force the shutdown circuit open.
    @return List with one int32 element (0 = run normally, 1 = disabled).
    """
    return [1 if disabled else 0]


def encode_steer_pid(kp: float, ki: float, kd: float, pwm_limit: float,
                     override: bool = True) -> list:
    """@brief Encode a live steering-PID tuning request.

    Gains travel as integers scaled x1000, matching the ESP32's object store.
    Values are clamped to the PID_MAX_* bounds before sending; the firmware
    clamps again on arrival and its clamp is the authoritative one. Clamping
    rather than raising keeps a fat-fingered "150" from becoming a dropped
    command that looks identical to a dead link.

    @param kp Proportional gain, clamped to [0, PID_MAX_KP].
    @param ki Integral gain, clamped to [0, PID_MAX_KI].
    @param kd Derivative gain, clamped to [0, PID_MAX_KD].
    @param pwm_limit Steering actuator output ceiling, clamped to [0, PID_MAX_PWM_LIMIT].
    @param override False restores the gains compiled into the firmware and makes
           the other four values irrelevant.
    @return List of five int32 elements: [override, kp, ki, kd, pwm_limit] x1000.
    """
    def _clamp(v, hi):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return 0.0
        if v != v:  # NaN
            return 0.0
        return max(0.0, min(hi, v))

    return [
        1 if override else 0,
        int(round(_clamp(kp, PID_MAX_KP) * 1000)),
        int(round(_clamp(ki, PID_MAX_KI) * 1000)),
        int(round(_clamp(kd, PID_MAX_KD) * 1000)),
        int(round(_clamp(pwm_limit, PID_MAX_PWM_LIMIT) * 1000)),
    ]


def decode_steer_pid(payload) -> dict:
    """@brief Decode the ESP32's report of the steering gains it is running.

    This is the firmware's own read-back after its clamping, not an echo of what
    was requested — so it is what the dashboard should display. A short or empty
    payload yields None values rather than zeros: a gain of 0.0 is a real setting
    the firmware can be running, so it must not double as "no answer yet".

    @param payload List of int32 values: [override, kp, ki, kd, pwm_limit] x1000.
    @return Dict with keys pid_override (bool|None), pid_kp, pid_ki, pid_kd,
            pid_pwm_limit (float|None each).
    """
    if len(payload) < 5:
        return {
            "pid_override": None,
            "pid_kp": None,
            "pid_ki": None,
            "pid_kd": None,
            "pid_pwm_limit": None,
        }
    return {
        "pid_override": bool(payload[0]),
        "pid_kp": payload[1] / 1000.0,
        "pid_ki": payload[2] / 1000.0,
        "pid_kd": payload[3] / 1000.0,
        "pid_pwm_limit": payload[4] / 1000.0,
    }


# ── Encoders (ESP32 → Orin, used by sim node) ───────────────────────

def encode_act_steering(angle_rad: float, raw_encoder: int = 0) -> list:
    """@brief Encode steering feedback as [angle_rad x 1000, raw_encoder].

    @param angle_rad Steering angle in radians.
    @param raw_encoder Raw AS5600 encoder value (12-bit).
    @return List of two int32 elements.
    """
    return [int(angle_rad * 1000), raw_encoder]


def encode_act_speed(speed_mps: float) -> list:
    """@brief Encode speed feedback as [speed_mps x 1000].

    @param speed_mps Speed in meters per second.
    @return List with one int32 element.
    """
    return [int(speed_mps * 1000)]


def encode_act_accel(lateral: float, longitudinal: float) -> list:
    """@brief Encode acceleration feedback as [lateral x 1000, longitudinal x 1000].

    @param lateral Lateral acceleration in m/s^2.
    @param longitudinal Longitudinal acceleration in m/s^2.
    @return List of two int32 elements.
    """
    return [int(lateral * 1000), int(longitudinal * 1000)]


def encode_act_braking(effort: float) -> list:
    """@brief Encode braking feedback as [effort x 255].

    @param effort Braking effort 0.0-1.0.
    @return List with one int32 element (0-255).
    """
    return [int(effort * 255)]


def encode_act_throttle(effort: float) -> list:
    """@brief Encode throttle feedback as [effort x 255].

    @param effort Throttle effort 0.0-1.0.
    @return List with one int32 element (0-255).
    """
    return [int(effort * 255)]


def encode_heartbeat(uptime_ms: int = 0) -> list:
    """@brief Encode heartbeat as [uptime_ms].

    @param uptime_ms Uptime in milliseconds.
    @return List with one int32 element.
    """
    return [uptime_ms]


def encode_health(magnet_ok, i2c_ok, heap_ok, agc, heap_kb, i2c_errors) -> list:
    """@brief Encode health status as [flags, agc, heap_kb, i2c_errors].

    @param magnet_ok Whether the AS5600 magnet is detected.
    @param i2c_ok Whether I2C communication is healthy.
    @param heap_ok Whether free heap is above threshold.
    @param agc AS5600 automatic gain control value.
    @param heap_kb Free heap in kilobytes.
    @param i2c_errors Cumulative I2C error count.
    @return List of four int32 elements.
    """
    flags = 0
    if magnet_ok:
        flags |= 0x01
    if i2c_ok:
        flags |= 0x02
    if heap_ok:
        flags |= 0x04
    return [flags, int(agc), int(heap_kb), int(i2c_errors)]


class DashboardState:
    """Thread-safe telemetry state."""

    def __init__(self):
        """@brief Initialize with default telemetry values and a threading lock."""
        self.lock = threading.Lock()
        self.data = {
            "esp32_heartbeat": False,
            "esp32_heartbeat_age": -1.0,
            "esp32_steering_rad": 0.0,
            "esp32_speed": None,
            "speed_source": "unavailable",
            "esp32_accel_lat": 0.0,   # lateral acceleration (m/s^2), positive = right
            "esp32_accel_lon": 0.0,   # longitudinal acceleration (m/s^2), positive = forward
            "esp32_throttle": 0.0,    # accelerator pedal 0.0-1.0 (ESP_PEDALS effort)
            "esp32_braking": 0.0,     # brake 0.0-1.0 (brake-pressure channel)
            "esp32_brake_pedal": 0.0, # brake PEDAL 0.0-1.0 (ESP_PEDALS effort)
            "esp32_pedal_acc_mv": 0,  # accelerator pedal, mV at the ADC pin
            "esp32_pedal_brake_mv": 0,# brake pedal, mV at the ADC pin
            "orin_cmd_throttle": 0.0, # target throttle 0.0-1.0
            "orin_cmd_brake": 0.0,    # target brake 0.0-1.0
            "esp32_steering_raw": 0,
            "esp32_steering_valid": False,
            "orin_cmd_steering_rad": 0.0,
            "health_magnet_ok": False,
            "health_i2c_ok": False,
            "health_heap_ok": False,
            "health_steer_ok": False,   # firmware's own verdict on the steering sensor
            "health_steer_trip": False, # steering fault latched: EBS fired, throttle refused
            "health_agc": 0,
            "health_heap_kb": 0,
            "health_i2c_errors": 0,
            "health_steer_frames": 0,
            "health_steer_rejects": 0,
            "pneu_tank_bar": None,          # tank pressure (bar); None → dial shows "-- bar"
            "pneu_piston_bar": None,        # piston/brake-line pressure (bar); None → "-- bar"
            "esp32_compressor_on": False,   # EBS compressor MOSFET on/off
            "esp32_compressor_duty": 0,     # compressor PWM duty 0-255 (soft-start ramp)
            "esp32_compressor_state": 0,    # 0 idle / 1 running / 2 cooldown / 3 operator-disabled
            "compressor_disabled": False,   # the dashboard's own latch, echoed back to the UI
            # Shutdown circuit read back off the ESP32's Q3 gate pin: True = chain closed,
            # False = emergency asserted, None = firmware too old to report it. Not wired to
            # anything downstream yet (2026-07-26), so this reports the firmware's intent and
            # the pin's real level, and nothing about whether the kart would actually brake.
            "esp32_sdc_closed": None,
            # EBS (emergency brake system) — no signal reaches the Orin yet, nothing publishes
            # these. None means "not wired" and the dashboard renders it as NOT WIRED in grey.
            # Keep None as the default rather than a boolean: a safety indicator that reads
            # healthy because a field defaulted to False is worse than one that reads unknown.
            "ebs_state": None,              # None | "unavailable" | "armed" | "activated" (FS 2026 T 14.8)
            "ebs_valve_on": None,           # None | bool — electrovalve energised, i.e. brakes held off
            "yolo_fps": 0.0,
            "esp_fps": 0.0,
            "cones_3d_ground": [],  # [{"x": float, "z": float, "c": str}, ...] from /perception/cones_3d_ground
            "mission": "manual",
            "state": "idle",  # idle | running | ebs
            "safety_reason": "Waiting for safety status",
            "bench_mode_available": False,
            "bench_mode_enabled": False,
            "bench_mode_active": False,
            "bench_mode_reason": "Waiting for bench status",
            "bench_throttle_cap_percent": None,
            "bench_throttle_percent": None,
            "bench_pressure_fault": None,
            "steer_mode": "pid",  # "pid" or "pwm"
            # Steering PID gains as last reported by the ESP32 over ESP_STEER_PID.
            # None means the firmware has not reported yet — either it predates the
            # frame or nothing is connected. The UI shows "--" for None rather than
            # a plausible-looking 0.0, because 0.0 is a gain the firmware can really
            # be running and the two must not look the same.
            "pid_override": None,     # None | bool — False = running the flashed defaults
            "pid_kp": None,
            "pid_ki": None,
            "pid_kd": None,
            "pid_pwm_limit": None,
            "controller_type": "geometric",  # geometric | pure_pursuit | neural_v2 | mpc
            "speed_controller_type": "curve_factor",  # curve_factor | constant | neural_v2
            "target_speed": 0.28,  # m/s setpoint for constant_speed (1 km/h)
        }
        self._heartbeat_time = 0.0
        self._safety_time = None
        self._bench_time = None
        self._hall = None

    def update_mission(self, payload):
        if len(payload) != 1 or type(payload[0]) is not int or payload[0] not in MISSIONS.values():
            raise ValueError("Malformed mission feedback")
        self.update("mission", next(name for name, value in MISSIONS.items() if value == payload[0]))

    def update_controller_config(self, config):
        if (type(config) is not dict or not isinstance(config.get("steering"), str)
                or not isinstance(config.get("speed"), str)
                or type(config.get("max_speed")) not in (int, float)
                or not math.isfinite(config["max_speed"]) or config["max_speed"] < 0):
            raise ValueError("Malformed controller feedback")
        self.update("controller_type", config["steering"])
        self.update("speed_controller_type", config["speed"])

    def update_bench_status(self, status):
        fields = ("bench_mode_available", "bench_mode_enabled", "bench_mode_reason",
                  "bench_throttle_cap_percent", "bench_throttle_percent", "bench_pressure_fault")
        if (not isinstance(status, dict) or any(key not in status for key in fields)
                or type(status["bench_mode_available"]) is not bool
                or type(status["bench_mode_enabled"]) is not bool
                or not isinstance(status["bench_mode_reason"], str)
                or (status["bench_pressure_fault"] is not None and type(status["bench_pressure_fault"]) is not bool)
                or status["bench_throttle_cap_percent"] not in (None, 30)):
            raise ValueError("Malformed bench status")
        for key in ("bench_throttle_cap_percent", "bench_throttle_percent"):
            value = status[key]
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                raise ValueError("Malformed bench throttle status")
        # Older publishers cannot establish that a requested bypass is effective.
        active = status.get("bench_mode_active", False)
        if type(active) is not bool or (active and not status["bench_mode_enabled"]):
            raise ValueError("Malformed bench active status")
        with self.lock:
            self.data.update({key: status[key] for key in fields})
            self.data["bench_mode_active"] = active
            self._bench_time = time.monotonic()

    def configure_hall_speed(self, edges_per_metre=0.0, enabled=True):
        """Select the display's speed source; zero scale means uncalibrated."""
        tracker = HallSpeed(edges_per_metre) if enabled else None
        if tracker is not None:
            tracker.captured_timing = False
        with self.lock:
            self._hall = tracker
            self._hall_dedicated = False
            self.data["speed_source"] = "motor_halls" if enabled else "legacy"

    def hall_sample(self, sample, now=None, dedicated=False):
        with self.lock:
            if self._hall is not None:
                if dedicated:
                    self._hall_dedicated = True
                    self._hall.stale_s = 0.25
                    self._hall.captured_timing = True
                elif self._hall_dedicated:
                    return  # Slow health packets cannot overwrite fast capture or hide its loss.
                self._hall.ingest(sample, time.monotonic() if now is None else now)

    def update(self, key, value):
        """@brief Thread-safe update of a single telemetry field.

        @param key Data key name (e.g. "esp32_speed").
        @param value New value to store.
        """
        with self.lock:
            self.data[key] = value
            if key == "safety_reason":
                self._safety_time = time.monotonic()

    def heartbeat(self):
        """@brief Record a heartbeat reception, updating the timestamp."""
        with self.lock:
            self._heartbeat_time = time.time()
            self.data["esp32_heartbeat"] = True

    def snapshot(self) -> dict:
        """@brief Return a thread-safe copy of all telemetry data with computed heartbeat age.

        @return Dict with all telemetry fields plus esp32_heartbeat_age.
        """
        with self.lock:
            d = dict(self.data)
            if self._bench_time is None or time.monotonic() - self._bench_time > 0.5:
                d.update(bench_mode_available=False, bench_mode_enabled=False, bench_mode_active=False,
                         bench_mode_reason="Bench status missing or stale",
                         bench_throttle_cap_percent=None, bench_throttle_percent=None,
                         bench_pressure_fault=None)
            if self._safety_time is not None and time.monotonic() - self._safety_time > 2.0:
                d["safety_reason"] = "Safety status stale — waiting for the state machine"
            if self._hall is not None:
                d.update(self._hall.snapshot(time.monotonic()))
                d["esp32_speed"] = d["hall_speed_mps"]
            if self._heartbeat_time > 0:
                d["esp32_heartbeat_age"] = round(time.time() - self._heartbeat_time, 1)
                d["esp32_heartbeat"] = d["esp32_heartbeat_age"] < 3.0
            return d
