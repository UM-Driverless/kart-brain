<!-- reference — implementation and verification record; read when changing safety code -->
# Sensor safety and emergency recovery

The firmware's `km_safety` module grants actuator permission on every control
iteration. The health task reports diagnostics; it does not decide whether the
kart may drive. The Orin mirrors firmware faults and checks its own controller
inputs. Neither side needs the dashboard to remain connected to stop.

## Required inputs

Autonomous driving and closed-loop remote driving require valid steering feedback,
a valid tank-pressure sample above the existing pressure interlock threshold,
fresh actuator commands, a fresh Orin state heartbeat, enabled compressor control,
and working safety GPIO initialization/output calls. Firmware rejects unknown
mission, state and steering-mode values. The MT6701 driver owns its 50 ms feedback
validity window. Tank voltage is sampled synchronously for each decision;
negative/failed/saturated samples cannot be interpreted as pressure.

On the Orin, firmware safety status, controller sensor status, desired steering
mode and selected controller commands expire after 0.5 seconds. Autonomous modes
require perception unless steering is None and speed control is Zero or Blind
throttle. Closed-loop speed control also requires a fresh finite speed estimate.
Empty but valid cone frames are not a sensor failure; silence and nonfinite cone
coordinates are. The explicit throttle-test mission bypasses controller input
requirements, but retains the firmware's physical sensor interlocks.

Remote steering in direct motor-control mode is a bench mode: it may move steering
without feedback or tank pressure, but cannot command propulsion or close the
shutdown circuit. It still requires fresh commands/state and working safety
outputs. Autonomous None steering retains throttle permission when its required
sensors are healthy, with the steering motor unpowered. Plain manual operation
uses the physical pedal; a latched emergency retains electronic throttle ownership
so changing mission cannot bypass the latch.

Battery display, pedal telemetry, motor-Hall display and an unfitted piston sensor
are not silently made prerequisites for autonomous operation. Their diagnostics
remain separate from the required-input checks above.

## Startup, faults and reset

Missing required inputs prevent arming. Once armed, a required-input fault latches
emergency. Restoring a cable or receiving healthy readings does not resume motion.
Stop and mission selection cannot clear an emergency. An unsafe mission change
retains the original mission and requests emergency before sending other state.

The dashboard displays the reason and provides **Reset safety**. Reset requires
healthy inputs and zero throttle/steering targets. The Orin sends a positive,
increasing attempt token after allowing zero commands to reach the firmware;
it remains in emergency until the firmware echoes that accepted token with no
active or latched faults. A rejected/timed-out attempt requires another press
with a new token. Successful reset returns to OFF and requires mission selection
and, for autonomous driving, Start. A reset does not restart a held remote command.

## Wire contract

Firmware `ESP_SAFETY_STATUS` (`0x0F`) publishes at up to 20 Hz:

`[1, active_faults, latched_faults, flags, accepted_reset_token, mission_id]`

The communications task sends only newly generated control-loop snapshots.
Health logging cannot keep a stopped control loop's safety report fresh.
`ORIN_SAFETY_RESET` (`0x2C`) carries one positive int32 token. The firmware consumes
each strictly increasing token once, including rejected attempts. Unknown versions,
invalid fields and malformed status frames cannot grant permission.

| Fault bit | Meaning |
|---|---|
| 0 | Steering feedback invalid or stale |
| 1 | Tank-pressure sample invalid |
| 2 | Tank pressure too low |
| 3 | Actuator commands stale |
| 4 | Compressor disabled |
| 5 | Invalid operating mode |
| 6 | Orin state heartbeat stale |
| 7 | Orin emergency request (latched) |
| 8 | Safety GPIO initialization/output error |

Flags: bit 0 inputs ready, bit 1 emergency latched, bit 2 bench mode, bit 3 armed.
The C++ bridge forwards this frame on `/esp32/safety` and reset on
`/orin/safety_reset`. `/kart/safety_reason` is the human-readable summary consumed
by the dashboard; a missing summary is displayed as stale, never healthy.

## Verification and remaining physical limits

Pure Python tests inject missing, stale, malformed and nonfinite inputs, every
firmware fault bit, mission changes, reset acknowledgment races and stale cached
commands. Native C tests exercise the firmware permission decisions. The ROS
integration harness runs on an isolated ROS domain and checks the actual node's
subscriptions, state transitions and emitted commands without driving hardware.

Software checks do not establish that the emergency brake physically operates.
The shutdown-circuit connection remains a separate on-kart validation item.
Flashing requires the steering motor disconnected or its controller unpowered:
steering pins float during reset. An independent hardware-safe watchdog for a
stopped firmware control loop remains unfinished; a reboot-only watchdog would
reintroduce that floating-pin hazard. Do not claim complete loop-stall protection.

A plausible frozen sensor value cannot always be distinguished from a real steady
reading. A zero-output pressure sensor cannot distinguish a broken wire from an
empty tank; both inhibit drive through low pressure. Freshly republished perception
with old source timestamps is not yet checked here; arrival-age checks alone cannot
prove that a camera image is new. These are diagnostic limitations, not promises
that every physical sensor failure is detectable by this software.
