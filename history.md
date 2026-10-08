<!-- consult selectively — grep, never read in full -->
# History

Chronological audit trail: *what* we found, *when*, *why* we decided things. Append new entries at the END (chronological, oldest first). Never rewrite past entries — correct via a new one.

---

## 2026-04-20 — Recreated `dev` branch from `origin/main`

`dev` had diverged oddly: zero unique commits, but a working-tree difference in `src/kb_dashboard/kb_dashboard/dashboard_node.py` (still had the `pwm_limit` param that `main` commit `99eacf6` had deliberately removed) and in `README.md`. Safest cleanup: `git branch -D dev` + `git push origin --delete dev` + recreate from `origin/main`. Nothing meaningful discarded since `dev` had no unique commits.

---

## 2026-04-20 — colcon --symlink-install is a misnomer for ament_python

Stale dashboard UI after `git pull + systemctl restart kart-brain` pointed to a misunderstood build behavior. With `--symlink-install`, `ament_cmake` packages (and Python scripts installed via CMake, like `kart_control`) *do* get symlinked — editing `src/` propagates without rebuild. But `ament_python` packages like `kb_dashboard` do **not**: `build/<pkg>/<pkg>/` is a symlink to src (good) but `setup.py develop` + the colcon plugin still require a rebuild step for `package_data` / changes to be served. The egg-link points to `build/<pkg>`, which houses copies, not live symlinks.

Fast rebuild loop documented in `.agents/notes.md`: `colcon build --symlink-install --packages-select kb_dashboard && sudo systemctl restart kart-brain`. Commit `e86dd63`.

---

## 2026-04-20 — Added "Constant + Stop" speed controller + "Shutdown Orin" button

Constant + Stop: copies the Constant speed controller but returns 0 m/s when any detected cone has class `orange_cone` or `large_orange_cone` (for acceleration-style runs where the kart must halt at the finish marker). Commit `42f5feb`. Tested and working on the kart.

Shutdown button: added to all 5 dashboard skins alongside Restart; red, double-confirmation, backend runs `sleep 3 && sudo -S poweroff` so the WS ack reaches the browser before power cuts. Commit `42e1a31`. **Known issue, logged as urgent task:** `updateMissionUI()` (`index.html:287-299`) hides the entire `.ctrl-row` when `currentMission` isn't autonomous. In Manual (the default), the Start/Stop/EBS/Restart/Shutdown row is `display:none` in the DOM. Shutdown needs to be always-visible since powering off the Orin is orthogonal to autonomous control.

---

## 2026-04-20 — Removed ZED-VIO-derived speed; disabled ZED pos_tracking

Decision: remove `/kart/speed` entirely. Commented out `_on_odom` callback and the odom subscription + publisher in `cone_follower_node.py`; replaced `self._actual_speed` usages in MPC (now plans at `max_speed`) and neural_v2 (input set to 0.0). Then disabled `pos_tracking_enabled` in `src/kart_bringup/config/zed_overrides.yaml` since nobody consumes the ZED odometry anymore — frees CPU/GPU for YOLO. Commits `316b5cd` + `02eda4d`. Why: the kart has no actual speed sensor, the VIO-derived value was unreliable, and the prior `TODO.md` item "Measure kart speed without hall sensor PCB" was closed by a feature that didn't actually work well enough to be trusted.

---

## 2026-04-20 — Pure pursuit arrow/wheel mismatch: architectural fix, not yet re-tested

User reported: with pure_pursuit, dashboard green arrow pointed right while the physical steering wheel turned left. Initial investigation of `_control_pure_pursuit` (`src/kart_control/scripts/cone_follower_node.py:484-582`) found no sign bug — same `(fwd, left = -pos.x)` convention as the working geometric controller. Ruled out the downstream pipeline too: `cmd_vel_bridge_node.py:67` applies no sign flip, and geometric works end-to-end through it.

User's architectural observation: the HUD arrow and the controller shouldn't be computed independently — they should come from the same endpoint. Agreed and refactored: each controller now stores `self._last_target` after picking its aim point; `_on_detections` publishes it to `/kart/target` (`PointStamped`, camera optical frame); `steering_hud_node.py` subscribes and projects that point to draw the arrow. Arrow now *cannot* disagree with whatever the controller decided. Commit `316b5cd`.

**Not yet verified on the kart** after that change. If the symptom persists, the mismatch is between `cmd.angular.z` and the actuator — which should be impossible given geometric works the same path. Open suspects, should PP still misbehave: (a) far-lookahead target on curve-entry opposite side, (b) adaptive-lookahead positive-feedback loop using `_last_steer`, (c) `steering_gain=3.0` saturating PP's already-valid angle. Full write-up in `tasks.md`.

---

## 2026-04-20 — GitHub org & branch-protection config after Alberto self-merged to main

Alberto merged his own PR #3 (Stanley controller) straight into `main` without review. Root cause was the org's **Base permissions = Admin**, which meant every org member had admin on every repo, including the ability to bypass any branch rule. Audit-trail of what's now configured:

**Branch protection (`main` only):**
- Require a pull request before merging — **on**
- Required approving reviews — **1**
- Dismiss stale approvals on new commits — **on**
- `require_code_owner_reviews` — off
- `require_last_push_approval` — off
- Enforce for administrators — **off** (admins can self-merge and push directly; intentional)
- Force push / branch deletion — blocked for non-admins
- No status-check requirements (no CI yet)

**Other branches (`dev`, `MPC_controller`, `autoresearch/mar15`, `feature/microros-integration`):** unprotected. Contributors can push / force-push / rebase freely.

**Org roles (`UM-Driverless`):** 20 members total. Owners visible in the People page sweep: `93Urbano`, `Alvar0P`, `rubenayla`, likely others. All rest are Members. Owners are the only people who can override branch protection regardless of repo-level role.

**Org Base permissions:** at time of writing still **Admin** — user was about to change it to **Write**, which will make the branch-protection rule actually effective for non-owners. Re-verify with `gh api repos/UM-Driverless/kart-brain/collaborators` after the change to confirm members drop to Write.

**Why this setup:** user is OK with self-merging *their own* PRs as admin, but wants at least peer review among contributors (two non-admins can approve each other's PRs — GitHub doesn't require approvers to be admins). No automatic notification is sent to users whose role changes, so any downgrade should be announced in team chat.

**Also this session:** Alberto's Stanley commit was cherry-picked onto `dev` (preserving him as author, commit `7e66399`) and the merge was reverted on `main` via `git revert -m 1 d33dd4a` (commit `c200e56`), which keeps PR #3 recorded as merged-then-reverted rather than force-rewriting history.

---

## 2026-04-20 — Dashboard offline-access: decided on phone-hotspot + LAN URL

Problem: the Orin loses WiFi from the shop router whenever the kart moves, and the team was opening `kart.rubenayla.xyz` through their phone's 4G hotspot — which silently routes via Cloudflare → cellular → back, burning mobile data even for what is physically a 2-metre link.

Explored three options:

1. **Phone hotspot + use the LAN URL** — phones on hotspot speak L2 to the Orin over WiFi-direct, no cellular round-trip, zero data cost. Requires using `http://<orin-ip>:9090` (not `kart.rubenayla.xyz`). Added a ⓘ popover in the dashboard topbar today that surfaces the live LAN IPs + clickable URLs, and a README section, so nobody has to memorize IPs. **Chosen solution.**
2. **USB WiFi dongle → Orin emits its own "kart-local" AP** with `hostapd` + `dnsmasq`. Fully offline, no phone/router needed. ~€10 hardware, ~30 min config. Kept on the shelf — not worth the setup cost right now given the hotspot path works.
3. **Built-in WiFi in AP+STA concurrent mode** — theoretically possible on the Orin's radio but driver-flaky on Realtek/MediaTek chips. Rejected as unreliable.

**Why hotspot wins for us today**: no hardware to buy, no driver babysitting, phone follows the kart so range is tied to how close the user stands, and the ⓘ button solves the "remember the IP" friction. Data cost is zero as long as everyone uses the LAN URL (the Cloudflare URL stays as a from-anywhere backup).

**Recommendation for stable LAN URL**: give the Orin a static DHCP reservation *on the phone's hotspot* (some phones support it, some don't) or a static IP via NetworkManager on the Orin. Otherwise the IP changes per session and the user has to re-open ⓘ.

Re-open this decision if: (a) hotspot reliability in the field proves worse than expected, (b) a team member actually wants to run without a phone nearby. Then pursue option 2.

---

## 2026-04-21 — Steering sun-gear failure root-cause: nylon creep on a D-flat is fundamentally wrong

The planetary reducer's sun gear keeps failing, and the failure mode is not tooth shear — it's the **nylon bore rounding out around the motor shaft's D-flat** until the sun spins freely on the shaft. Fails preferentially on sudden direction reversals.

Why nylon + D-flat is a bad pairing, independent of PID tuning:

- A D-flat transmits torque through a tiny line-contact along the flat edge. Stress concentrates sharply on the nylon at that edge.
- Nylon **creeps** under sustained stress, and creeps fast under *repeated impulsive* stress. Every direction reversal with any backlash in the coupling (which a shallow D-flat always has) loads that edge with an impact, not a smooth torque.
- Over thousands of reversals the bore plastically deforms: the flat wears round, backlash grows, impacts get harder, damage accelerates. Classic runaway failure.
- This is why software mitigations (slew limits, output deadband, D-term LPF) can *slow* the failure but not prevent it — the root cause is material + geometry, not control.

**Constraint**: the sun gear is very small — no room for a hybrid solution (steel insert pressed into the nylon bore, brass hub with nylon teeth, etc.). Whatever we use has to be a monolithic part in basic sun-gear geometry. That rules out the usual "metal hub + plastic teeth" compromise.

**Material mismatch — actually works in our favour**: the planets and ring are also nylon, so a brass sun will be the hardest part in the stack. This sounds like it migrates the problem, but the geometry makes it a genuine upgrade:

- The **sun is the highest-duty part** in a planetary (rotates fastest, every tooth engages every revolution). Making the highest-duty part the durable one is exactly correct.
- **Three planets share the load**, each rotating slower than the sun. Wear spreads over ~3× more teeth rotating several× less often per unit time → roughly an order of magnitude gentler per-tooth duty than the sun sees.
- The **ring gear is shielded** — it only meshes with nylon planets, never directly with the brass sun. Its interface stays nylon-on-nylon, same as today.
- **Planets are simple parts**: plain spur gears on a smooth bore pin, **no D-flat, no keyway, no critical torque-transfer feature**. 3D-printable, easy to batch as spares, trivially swappable. The failures we're avoiding on the sun come from the D-flat interface specifically — planets don't have that problem.
- **Failure-mode upgrade**: today, sun bore rounds out → spins freely on the shaft → sudden total loss of steering with no warning. After: planet teeth wear gradually → backlash grows → audible/visible well before functional failure → scheduled maintenance.

**Decision**: if the rest of the steering stack (firmware PID, actuator, comms) proves out well enough to be worth committing to, we CNC a **brass sun** in the same geometry. Drop-in replacement, shifts wear from a catastrophic surface to a graceful one.

**Gate on doing the CNC work**: electronic steering must demonstrate it actually works (reliable target tracking, no runaway, usable under mission modes) before investing the machining hours. Until then, software mitigations + careful operation buy test time on the current nylon parts.

Re-open if: planet wear turns out to be surprisingly fast in practice (unlikely but measure it), or the CNC access falls through and we need a plan B.

---

## 2026-06-06 — ZED coordinate system + live orientation relative to gravity (verified on Orin)

Cone 3D positions flow through `/perception/cones_3d` in the **optical frame** (`zed_left_camera_optical_frame`): **X = right, Y = down, Z = forward (depth)**, right-handed. This is fixed by `cone_depth_localizer_node.py:114-127` using `image_geometry.PinholeCameraModel.projectPixelTo3dRay` (always optical convention) — *not* a per-run/config setting. `frame_id` is inherited from the ZED `camera_info` header. So a cone dead ahead reads `(x≈0, y≈+, z=distance)`. The ROS *body* frame `zed_camera_link` uses the other REP-103 convention (X-fwd, Y-left, Z-up), but cone messages are NOT in that frame. Dashboard top-down view (`/perception/cones_3d_ground`) plots `{x, z}` (x lateral, z forward), dropping the down axis.

Live verification (`ros2 topic echo /perception/cones_3d --once`): frame_id `zed_left_camera_optical_frame`; sample cones `(x=+0.59, y=+0.49, z=+1.55)` and `(x=-0.98, y=+0.34, z=+0.97)` — signs consistent with right/down/forward.

**Orientation relative to gravity** (from `/zed/zed_node/imu/data`, kart stationary): accel `(−0.54, −0.20, 9.76) m/s²` (|a|≈9.78≈g), fused quaternion `(−0.011, 0.028, −0.001, 0.9996)` → total tilt `2·acos(w) ≈ 3.4°`. Gravity re-expressed in the optical frame ≈ `(−0.2, +9.76, +0.54)`, i.e. points almost exactly down the **+Y axis**. The camera is mounted essentially **level**: optical **+Y ≈ gravity (down)** within ~3.4°, with **~3° nose-down pitch** (small +Z/forward gravity component — sensible for looking at cones on the ground) and **~1.3° roll** (right side slightly up). Note: IMU publishing must be enabled to read this live — `zed_overrides.yaml` had `sensors_pub_rate: 0.0`; the `imu/data` topic was active during this check.

Access gotcha hit during this session: the public Cloudflare hostnames (`kart.rubenayla.xyz` HTTP 530, `orin.rubenayla.xyz` SSH `websocket: bad handshake`) intermittently fail when the Orin's `cloudflared` daemon drops its connection — a restart of `cloudflared` on the Orin fixes it.

---

## 2026-07-06 — Landscape "Race" dashboard skin (phone holder in the kart is horizontal)

Context: a phone holder was mounted in the kart that holds the phone **horizontally**. The dashboard was designed portrait-first (480px column, vertical scroll), which no longer matches how it's physically used on the kart. Decision: redesign for landscape, and replace vertical scrolling with **horizontal panel swiping** — while driving/testing you flick between full-screen pages instead of scrolling a column.

**Style source**: an HTML mockup (`kart-dashboard.html`, made outside the repo with simulated data) with a Formula-Student racing look — carbon-fibre weave background, Ü MOTORSPORT red `#e2001a`, Orbitron/Rajdhani/Titillium Web fonts, clip-path chamfered panels with rivets and a red corner accent, hazard-stripe divider, steering needle gauge (±90° SVG), huge central speed readout, throttle/brake pedal bars, status-dot warn strip, portrait "rotate your device" guard. Copied verbatim into the repo as the design reference: `src/kb_dashboard/reference/horizontal-race-mockup.html`.

**Implementation decision**: added as a new skin (`race`) in the existing skin system in `src/kb_dashboard/kb_dashboard/index.html`, not a rewrite — the portrait skins (Default/KITT/Tesla/HUD/Artemis) stay available from the same dropdown, and all the shared plumbing (WebSocket feed, mission/state helpers, `updatePedals`/`updatePills`/`drawCenital`, HUD JPEG stream) is reused.

**Data mapping — mockup fakes vs what the kart actually publishes**:
- Mockup's battery % / motor temp / motor RPM **don't exist on the kart** (no sensors). The right-hand telemetry stack shows real values instead: YOLO inference Hz, ESP32 link Hz, ESP32 free heap kB — same card style, same bars.
- Speed hero ← `esp32_speed` (m/s → km/h). Steering gauge needle ← `esp32_steering_rad`; a second thin amber needle shows the commanded target (`orin_cmd_steering_rad`) so PID tracking error is visible at a glance.
- Accel bar under the speed ← `esp32_accel_lon` (shown in g, green forward / red braking), caption also shows lateral g.
- Pedal bars ← `esp32_throttle` / `esp32_braking`; warn strip dots ← MAG/I2C/HEAP health flags + ESP32 heartbeat (replacing the mockup's BMS/INVERSOR/TELEMETRÍA).
- Steering sign: positive rad renders to the **left**, matching the data convention (REP 103 yaw, positive = left turn). Originally the gauge and the steer bars rendered positive to the right, contradicting the control pipeline; corrected 2026-07-18 — see the entry at the end of this file.

**Panel layout (swipe left/right, or tap the tabs in the bottom bar)**:
1. **TELE** — the mockup's main screen: steering gauge | speed hero | YOLO/ESP/heap stack.
2. **MISSION** — mission grid, steering/speed algorithm dropdowns, Start/Stop/EBS/Restart (reuses `misGrid`/`.ctrl-btn` classes so `updateMissionUI` gating works unchanged), state/mission/HB pills. The remote-control joystick widget is *moved* into this page while the skin is active (DOM node relocation, restored on skin switch) so its nipplejs listeners survive.
3. **VISION** — live HUD camera stream (the global `#hudStream` img node is likewise relocated into this page) + cenital top-down cone view.
4. **SYSTEM** — full health readout: magnet, I2C errors, heap, AGC, YOLO Hz, ESP Hz, heartbeat age, AS state.

Top bar is the global one restyled (brand red Orbitron title, live pulse dot, skin selector, ⓘ net info, power off) with the hazard stripe under it; bottom bar is persistent: pedals left, page tabs center, warn dots + fullscreen (with landscape orientation lock) right. Portrait shows a "rotate the phone" guard but keeps the top bar visible so you can still switch skins. All UI text is English (the mockup was Spanish; user asked for English — matches the rest of the dashboard).

---

## 2026-07-06 — Top-down cone view live on the kart: dashboard falls back to raw cones_3d

Why the top-down (cenital) view was empty while the HUD showed detections: the dashboard only listened to `/perception/cones_3d_ground` from `ground_plane_localizer`, but that node (a) only runs in the separate `perception_zed_od_ground.launch.py`, not the systemd launch, and (b) consumes **ZED built-in OD** (`ObjectsStamped`) — it sits idle under the default YOLO pipeline even if launched. The 2D HUD path (`steering_hud` ← `/perception/cones_2d`) is independent and was always fine.

Fix (commit `df3a5c9`): `dashboard_node` also subscribes to raw `/perception/cones_3d` and feeds the view from it whenever the ground-corrected topic has been silent >1 s. Corrected data automatically wins when the workshop validation launch is running. Verified live on the kart: workshop cone at (0.84 m left, 0.74 m fwd) renders in the Race skin's top-down panel.

Also merged `feature/imu-corrected-ground-plane` into `dev` (merge `f604ff3`): ground-plane localizer + validation launch, Race skin, cenital view, kart_brain→kart-brain path renames. Deployed on the Orin (`dev`), all nodes up. **`main` untouched — waits for the kart physically driving with visual validation, per AGENTS.md.**

---

## 2026-07-06 — Kart Wi-Fi AP is live and is now the DEFAULT operating mode

The Orin's Wi-Fi (`wlP1p1s0`, RTL8822CE) now runs as its own access point instead of joining lab/phone networks: NM connection `kart-ap`, SSID **`kart`**, WPA2 password **`umotorsport`**, `ipv4.method shared`, autoconnect priority 200 (wins over every client profile on boot). Verified working by the user in person: the network appears (the Mac's Wi-Fi list just took a while to refresh — the AP had been beaconing fine on channel 1/2.4 GHz all along), and **internet sharing to AP clients works** through the chain iPhone (USB-C tether, Personal Hotspot) → Orin → NAT (`MASQUERADE 10.42.0.0/24`) → Wi-Fi clients.

**Default operating mode from now on:**
- Dashboard for everyone at the kart: join Wi-Fi `kart` → `http://10.42.0.1:9090`. Zero internet required.
- SSH at the kart: `ssh orin-local` (now pointing at `10.42.0.1` in the Mac's `~/.ssh/config`).
- Internet for the Orin and remote access (`orin-remote` / Cloudflare tunnel / `kart.rubenayla.xyz`): plug Ruben's iPhone in via USB-C with Personal Hotspot on. Without it, everything local still works.
- Expect double NAT and cellular speeds for AP clients' internet — fine for the dashboard and browsing.

Rollback if ever needed: `sudo nmcli connection down kart-ap` and `sudo nmcli connection up "<old wifi>"`. Full details in `.agents/orin-environment.md`.

---

## 2026-07-06 — Race skin: cockpit dials, targets on the instruments, Control page removed

Series of UI decisions from today's experimentation session (all local, `?demo=1` simulated telemetry; Orin off — deploy pending):

- **Telemetry page became an aircraft-style instrument cluster**: speed is a round 270° dial (0–50 km/h after the user corrected the kart's ~45 top speed; redline from 45) with a small digital readout kept below it; the old YOLO/ESP/heap bar-cards became mini round gauges with green/amber/red range arcs; an analog clock fills the fourth cell (experimental — swap for a lap timer if it earns nothing). New reusable canvas painter `rcDrawDial`. **Dials must never get a percentage `height`** — only max-width/max-height, or they squash into ellipses on odd-shaped screens (bug found on a 704×562 window).
- **Targets ride the same instruments instead of a separate panel**: the steering gauge already had the amber command needle; the pedal bars got amber target ticks (`orin_cmd_throttle/brake`); steering PID PWM is a small readout under the gauge. With that, the "Target vs Actual" XY pad + TGT/ACT/Δ table was redundant and was deleted.
- **Control page removed entirely** (back to 4 pages): its G-G diagram moved into the telemetry mini-cluster, replacing the ESP32-link dial — rationale (user): ESP32 link rate should be high and boring, it's health data, not telemetry; it stays on the System page only.
- **Health consolidated into a self-alarming System tab**: every legacy health-bar field is a System-page card with its explanation printed on it (AGC folded into the magnet card; new Stack card — shows `--` until the firmware sends per-task stack minima, task filed in `tasks.md`). The System tab pulses red when anything is unhealthy; the four MAG/I2C/HEAP/HB bottom-bar dots were removed.
- **`?demo=1` mode added**: simulated random-walk telemetry (speed, steering, pedals, drifting cones) so UI work needs no kart. This is the standing way to iterate on the dashboard from home.

---

## 2026-07-07 — Why the dashboard telemetry broadcast stays at 10 Hz (don't raise it)

Question: is it worth updating the dashboard faster than 10 Hz?

**Where the rates live:**
- Telemetry broadcast to browsers: `broadcast_loop()` in `src/kb_dashboard/kb_dashboard/server.py:417`, set by `await asyncio.sleep(0.1)` → 10 Hz. This is an asyncio loop, **not** a ROS timer.
- Command publish (joystick → kart): `create_timer(0.01, self._publish_pending)` in `dashboard_node.py:180` → 100 Hz, a real ROS timer on the spin thread (cross-thread-safe bridge from the asyncio WS handler).
- HUD JPEG stream already throttled to 20 Hz to avoid stealing CPU from YOLO.

**Decision: keep the broadcast at 10 Hz.** Reasoning:
1. **Binding constraint is Orin CPU, not bandwidth.** YOLO runs at 70–84 Hz and is the critical job; each broadcast serializes state to JSON and sends to every connected client. Tripling the rate triples that cost for near-zero benefit. The JSON snapshot is small — bandwidth was never the limit; the JPEG HUD frames are.
2. **Human perception saturates ~10–15 Hz** for reading numbers/gauges — the eye can't read digits faster.
3. **Source-rate ceiling** — if the ESP/IMU data doesn't arrive faster than 10 Hz, a faster WebSocket just resends the same value.

**Where faster *would* help, and the cheaper fix:**
- Visual smoothness of moving elements (G-G dot, needles) looks "steppy" at 10 Hz. Fix in the **browser for free**: interpolate in JS between received values and paint at 60 Hz with `requestAnimationFrame`. Separate data rate (10 Hz, cheap) from render rate (60 Hz, smooth). Don't touch the network rate.
- Fast transients (accel/G spikes on impact, vibration) alias at 10 Hz. Capture those in a **log on the Orin at the sensor's source rate**, not the live dashboard. Dashboard = human live monitoring; fine-grained analysis = separate recording.

---

## 2026-07-08 — ZED Tracking vs. SLAM architectural decision

**Context:** Designing the algorithmic roadmap for transitioning the kart from a reactive controller (pure pursuit/geometric) to a global trajectory planner capable of mapping the track and running multi-lap optimizations.

**Decision & Architectural Clarity:**
We need to clearly separate what the ZED SDK does internally versus what we need to build in ROS 2. 

1. **ZED Object Tracking (Enabled):**
   - **What it is:** A Kalman Filter / Nearest Neighbor tracker that assigns IDs to cones (e.g., "Cone #5").
   - **Why we need it:** It acts as an "invisible shield" against YOLO dropping frames due to glare or motion blur, and it filters out 1-frame ghost detections. It does **not** perform loop closures or map the track, but it dramatically cleans up the data sent to the downstream nodes.
   - **Gotcha:** Never assume this builds a map. If you see Cone #5 again a minute later on a second lap, the ZED will think it's a brand new cone.

2. **ZED Positional Tracking (VIO / Area Memory):**
   - **What it is:** The camera's internal visual odometry (which we already use for `/kart/speed`). If Area Memory is enabled, it *does* run an internal GraphSLAM on visual features (buildings, trees) to correct odometry drift.
   - **Gotcha:** It corrects the kart's trajectory but **does not map the cones**. We cannot rely on the ZED to give us a track map.

3. **Our ROS 2 SLAM Node (The Missing Piece):**
   - **What we need to build:** A lightweight GraphSLAM or EKF node that consumes the ZED's high-quality odometry and the ZED's tracked 3D cones.
   - **Why:** This node will perform the actual "springs" matching (Data Association + Least Squares optimization) on the cones themselves, detect when we cross the start/finish line (Loop Closure), and output the final, globally consistent track map for the trajectory planner.

---

## 2026-07-08 — Dashboard dead after reboot: empty sysctl persist file reverted the port-80 bind

**Symptom:** `kart.rubenayla.xyz` and `http://10.42.0.1/` stopped serving after an Orin reboot, even though `cloudflared` was active, the Orin had internet (iPhone tether up), and `systemctl is-active kart-brain` returned `active`. Nothing was listening on port 80.

**The two facts that pinned it:**
- `net.ipv4.ip_unprivileged_port_start = 1024` — the sysctl had reverted to the kernel default.
- `/etc/sysctl.d/99-kart-dashboard.conf` was **empty**.

**Root cause:** The dashboard was migrated from port 9090 to port 80 (commit "Dashboard binds port 80 directly — no iptables redirect"). Binding a port <1024 as a non-root process requires `net.ipv4.ip_unprivileged_port_start=80`. That value had only ever been set *live* (`sysctl -w`); the attempt to persist it wrote an empty file. The persist command was malformed — `echo "net…=80" | (echo 0 | sudo -S tee /etc/sysctl.d/99-kart-dashboard.conf)` — the inner `echo 0` (the sudo password) hijacked `tee`'s stdin, so `tee` wrote nothing. The live value held until the next reboot; then `systemd-sysctl` found an empty file, left the default 1024 in place, and the dashboard's non-root bind to :80 failed with permission denied and the node exited. Remote access was silently lost — nothing in the UI or `systemctl` state hinted at it, because the ROS launch as a whole stayed "active".

**Fix (permanent, verified):** Rewrote the file with `echo 0 | sudo -S sh -c 'echo net.ipv4.ip_unprivileged_port_start=80 > /etc/sysctl.d/99-kart-dashboard.conf'` (password on stdin, file content inside the command string — no stdin collision). Verified boot-proof: `sudo sysctl --system` (the exact path `systemd-sysctl.service` takes at boot) yields `net.ipv4.ip_unprivileged_port_start = 80`; `systemd-sysctl.service` is `static`/active and ordered before `kart-brain`, so the setting is in place before the dashboard binds. Dashboard back on :80, `kart.rubenayla.xyz` → 200.

**Prevention:**
- Never combine a `sudo -S` password pipe with a content pipe into the same command — they fight over stdin and the content silently loses. Use `sudo -S sh -c '...'` and put the file content inside the command string.
- After writing any `/etc/sysctl.d/*.conf`, verify with `sudo sysctl --system` (reproduces boot behaviour), not just `sysctl -w` — that catches an empty or ineffective file immediately.
- Note for reference docs: the dashboard is now on **port 80** (needs this sysctl), not `:9090`. `.agents/orin-environment.md` still says `:9090` in places — stale.

---

## 2026-07-08 — Battery BMS is readable over Bluetooth (JBD/Xiaoxiang protocol)

The 13S4P Molicel P42A pack has a **JBD/Xiaoxiang smart BMS** that advertises over BLE, and the Orin's built-in Bluetooth (`hci0`, IMC Networks radio) connects to it directly — no ESP32/CAN needed. This is a clean, independent source for the dashboard's battery gauge (which was showing `--` because it only reads battery from the now-dead `/esp32/health` link).

**How it was found:** `bluetoothctl scan on` from the Orin. Most hits are phones with randomized MACs and no name; the BMS is the one device advertising a **model-code name**: `SP22S003BP21S100A` at address **`A5:C2:37:39:58:5D`**.

**How to read it (reproducible):**
- Tooling: Python **bleak** (`pip3 install --user bleak`; `gatttool`/`bluetoothctl` also present). Orin BT service active, `hci0 UP RUNNING`.
- GATT: standard JBD layout — service `0000ff00`, **notify** char `0000ff01`, **write** char `0000ff02` (write-without-response).
- Protocol (JBD): write a command to `ff02`, read the reply as notifications on `ff01`. Frames are `DD <reg> <status> <len> <payload…> <chk> <chk> 77`, big-endian.
  - Basic info: send `DD A5 03 00 FF FD 77`. Payload: volt=`u16/100` V, current=`s16/100` A, remain=`u16/100` Ah, nominal=`u16/100` Ah, cycles=`u16`, protection=`u16@16`, **SOC=`byte@19` %**, cells=`byte@21`, ntc count=`byte@22`, then each temp `u16` in 0.1 K (`(t-2731)/10` °C).
  - Cell voltages: send `DD A5 04 00 FF FC 77`. Payload = N × `u16` mV.
- Reader script archived at `.agents/imports/` idea / lives on the Orin at `/tmp/read_bms.py` (to be promoted into a ROS node — see below).

**First reading (idle pack):** 52.84 V, SOC 99 %, 0.00 A, 13.26/16.80 Ah, 6 cycles, temps 28.9/25.9/27.2 °C, protection 0x0000; 13 cells 4056–4066 mV, **10 mV spread** (well balanced). Confirms 13S4P (16.8 Ah = 4 × 4.2 Ah P42A).

**Next step (organize/autostart):** fold this into the one clean startup rather than a side script. `kart-brain.service` → `ros2 launch kart_bringup launch.py` is the single entry point; add a **`kb_bms` ROS node** (bleak reader in a thread + ROS timer publishing `sensor_msgs/BatteryState`) to `launch.py`, and have the dashboard subscribe to it. Then it autostarts with everything else under the same service.

---

## 2026-07-11 — AS5600 steering encoder: decided on PWM-out (sensor mounted far), OTP burn justified

**Context / goal:** the **AS5600** magnetic rotary encoder (12-bit, addr `0x36`) is the kart's **steering-angle sensor**, mounted off-board on the steering shaft. It will sit **< 2 m** from the medulla PCB. Decision this session: read it as **PWM on the `OUT` pin**, *not* over I²C, because **I²C won't survive the cable run** to where the sensor is mounted (the handoff doc itself warns I²C doesn't tolerate long/noisy runs). `kart_medulla` (ESP32-S3 firmware) is the consumer. Handoff doc: `~/Downloads/as5600-esp32s3-handoff.md`.

**Bench wiring already done (4 wires, I²C — for configuration only):** `SCL→CN4-1`, `SDA→CN4-2`, `VCC→CN1-1 (+3V3)`, `GND→CN10-3`. **Powered at 3.3 V, not 5 V** (the module's 10 kΩ SDA/SCL pull-ups would otherwise push 5 V into the S3 GPIOs). GND on CN10-3 (CN1-3 occupied); CN9-3 avoided on purpose (steering/hydraulic actuator ground = noisy). USB alone powers 3V3 for bench work — no kart battery needed. On the S3 the I²C bus is **`SDA = GPIO 8`, `SCL = GPIO 9`** (per `kart_medulla/.agents/esp32s3-pinmap.md`, from the KiCad netlist); shared with on-board **PCF8574 `U25` at `0x20`**, so an I²C scan should show both `0x20` and `0x36`.

**Why PWM + why burn (the key decision — reverses my earlier "do not burn"):** OUTS defaults to `00` = analog. PWM is enabled by writing **CONF** (`0x08`, low byte) with **OUTS bits 5:4 = `10`** and **PWMF bits 7:6** = frequency. But CONF in RAM is **volatile** — and since I²C won't reach the sensor in the field, the ESP32 **cannot re-apply it every boot**. So the config must be made permanent by **burning the OTP** (`BURN_SETTING`, `0x40`, **once** — burns CONF+MANG). This is exactly the standalone-PWM-without-a-configuring-micro case that justifies burning. Burn procedure (irreversible): **on the bench**, 3.3 V with a **10 µF cap on VDD3V3→GND**, supply resistance **< 1 Ω** (short/thick wires), magnet present + detected (`STATUS 0x0B` bit5 MD=1); write CONF → `BURN_SETTING 0x40` → wait ≥1 ms → **power-cycle** → re-read CONF to confirm `OUTS==0b10`. Optional `BURN_ANGLE 0x80` (ZPOS/MPOS, ≤3× per ZMCO) to set a steering zero — not required; offset/scale can live in firmware instead.
```c
uint8_t lo = readReg8(0x08);
lo &= ~0xF0; lo |= (0b00 << 6) /*PWMF=115 Hz, robust over cable*/ | (0b10 << 4) /*OUTS=PWM*/;
writeReg8(0x08, lo);   // then BURN_SETTING to make it permanent
```

**Where the PWM lands on medulla — checked against the `dv-hardware` schematic:** **no free CN terminal** exists that reaches a PWM-capable ESP32 GPIO (the `EXP_P*` pins on CN3/CN5 are **PCF8574 expander outputs**, not raw GPIOs — useless for reading a pulse). The only unconstrained free S3 GPIOs are **38, 39, 13(MISO)**, and they come out on the **dev-module LEFT_HEADER, not on any CN**. GPIO 38 is earmarked for the EBS compressor PWM, so:
> **Route `OUT` → GPIO 39 (LEFT_HEADER Pin 14).** Solder to the header pin; flag hardware to break a spare GPIO out to a CN in the next PCB rev.

**Electrical (distance < 2 m):** PWM push-pull 3.3 V goes **direct** — no buffer needed at this length. Still: twist the OUT wire with GND, add a ~100–330 Ω series resistor at the source, and use the **low PWM frequency (115 Hz)** — edges degrade less over cable and steering needs almost no bandwidth. (Only past ~10 m would a Schmitt buffer / differential driver / I²C extender like P82B715 be worth it.)

**Firmware still to write (does not exist):** `km_medulla/components/km_sdir` is the existing AS5600 driver but it is **I²C-only** (reads `RAW_ANGLE 0x0C/0x0D`) and hardcodes the **classic-ESP32 pins SDA=21/SCL=22** in `KM_SDIR_ResetI2C`. For the PWM path we need **new pulse-width-capture** code — cleanest on the S3 is an **MCPWM capture unit** timestamping both edges → high-time / period → duty → angle. Calibrate duty→angle empirically (absorbs the steering zero). **Also blocking:** the **S3 build doesn't exist yet** — `platformio.ini` has only `esp32dev`/`native`, `km_gpio.h` is still the WROOM-32E map, and (per `.agents/error-log.md` 2026-07-10) that classic map puts `STEER_PWM` on GPIO 18, which on the S3 board is the safety-critical SDC MOSFET gate — so porting must guard the pin map with `#if CONFIG_IDF_TARGET_ESP32S3`, never flat-reuse it.

### 2026-07-11 (addendum, Rubén + Claude) — plan for the OTP burn session (tomorrow)

Continuing the entry above. Agreed procedure and open points so tomorrow's flash session has context:

- **Tomorrow's physical step:** solder a **10 µF cap VDD3V3→GND** right at the sensor module, then Rubén pings me to walk through the flash. The cap is a local energy reservoir so VDD3V3 doesn't droop during the ~1 ms burn current pulse (OTP is one-shot — a mid-burn voltage dip = corrupted, unrecoverable config). It is **not** about the ESP32's current capacity — the S3 devkit 3V3 LDO (hundreds of mA) is plenty; the risk is voltage droop from *wire/connector resistance*, which the local cap + short thick wires (<1 Ω source) fix. Do the burn with the sensor on **short bench wires next to the S3**, not through the 1.2–2 m harness.

- **Test-in-RAM-then-burn (mandatory ordering, because burn is irreversible):** first write `OUTS`=PWM to the **volatile CONF register only** and confirm real PWM is coming out of the `OUT` pin (scope / logic analyzer — does NOT need the not-yet-written MCPWM capture firmware). Only once PWM output is verified do we send `BURN_SETTING 0x40` to make it permanent. Then power-cycle and re-read CONF to confirm `OUTS==0b10`.

- **PWM-mode burn vs angle-zero are independent commands** — `BURN_SETTING (0x40)` burns CONFIG (holds `OUTS`) + MANG; `BURN_ANGLE (0x80)` burns ZPOS/MPOS (the zero/range). Burning PWM mode does not touch or require the angle zero. **OPEN QUESTION to resolve before we burn:** *where does the "good" steering zero currently live?* Rubén says the zero "came good, don't change it," but it matters which of these it is:
  - **(a) firmware software-offset** (Jorge's stated plan) → not in the chip at all, burn can't affect it, nothing extra to do. Clean case.
  - **(b) AS5600 `ZPOS` register set over I²C but never burned** → that's *volatile RAM too*, so in PWM-standalone (no I²C at boot) it is **lost on power-cycle** and PWM output reverts to raw magnet position. Would need to *also* `BURN_ANGLE`, or move the zero into firmware.
  - Regardless: the "good zero" was calibrated on the **I²C** read path. The PWM path is new firmware (duty→angle), so the zero must be **re-verified against the PWM reading** once that firmware exists — the number won't necessarily carry over 1:1. Note MANG can only be written if ZPOS/MPOS were never burned, so if we go firmware-zero we simply never touch BURN_ANGLE and avoid that interaction entirely.

- **Pre-burn sanity checks:** I²C scan should show `0x20` (PCF8574 U25) + `0x36` (AS5600); confirm magnet detected (`STATUS 0x0B` bit5 MD=1) before burning; read ZMCO to see if any angle burn was already done.

- Supply at **3.3 V not 5 V** (module ties VDD3V3/VDD5V; also its 10 kΩ SDA/SCL pull-ups would push 5 V into S3 GPIOs). USB alone powers 3V3 for bench work — no kart battery needed.

- **Pin correction 2026-07-11:** the entry above says "GPIO 38 earmarked for compressor, so steering → GPIO 39." The steering→39 conclusion stands, but the premise is stale — the compressor PWM was finalized on **CN8.2 / GPIO 3** (ex-buzzer MOSFET), so GPIO 38 is *also* free. GPIO 39 (LEFT_HEADER pin 14) is a **header solder** for the prototype, not on a CN. Cleaner permanent option (next PCB rev): repurpose a spare **pressure** input CN — we over-provisioned (3 pneumatic + 2 hydraulic; keeping both hydraulics, freeing one pneumatic). Best candidate **PRESSURE_3 (CN5.2, GPIO 1)** — straight to the ESP32, divider is 10 kΩ series + 10 k∥10 k pulldown, so **keep the 10 kΩ series as the PWM series resistor, remove the pulldown**. Full analysis + designators in `dv/kart/steering/as5600-pwm-burn-runbook.md`.

Hardware-side companion note lives in the DV vault `kart/steering/history.md` (2026-07-11) — the two entries are the hardware and firmware halves of the same decision.

## 2026-07-11 — Dashboard Battery tab + pack current limits

Added a 5th tab to the race skin (Telemetry · Mission · Vision · System · **Battery**). All BMS fields already reached the browser via `dashboard_node._on_battery`; the tab just renders them (plus I added `battery_charge` = remaining Ah and `battery_temps` = all NTCs to the forward). Layout: SOC progress ring + bidirectional current gauge side by side, 3 stat readouts (V / Ah / Temp), and a compact per-cell "battery-pill" grid (green level-fill with the voltage printed inside, min cell outlined red / max amber, min/max/Δ header, `13S` count shown so a dead cell channel is obvious).

**Pack current (from `~/dv/kart/README.md`) — the dial is whole-PACK current, not per-cell:** pack is **13S4P Molicel P42A**. Sign per the JBD BMS: **positive = charge, negative = discharge** (the dial states this itself: "PACK AMPS" + a dynamic CHARGE/DISCHARGE/IDLE caption). Discharge: 2000 W motor / (13 × 3.2 V) ≈ **48 A pack** (12 A/cell) at full power, nickel strips sized right at that peak → gauge amber −40…−50, red beyond −50. Charge: **~8 A/cell max** (Ruben's internal figure — this P42A charges unusually well for a Li-ion cell, still less than discharge) × 4P ≈ **32 A pack** → gauge amber +24…+32 (6–8 A/cell), red beyond +32. Capacity 13S4P ≈ 16.8 Ah / 786 Wh. Charge-side numbers are still an internal estimate — pin to the BMS charge-current limit if it's ever specified.

**Design direction — Ferrari-Luce hybrid gauge, now the standard for the race skin.** `rcDrawGauge`: tick scale + coloured zone arcs + short rim needle on the ring, big digital value in the CENTRE (only amber/red danger zones tint the number/needle — a green "ok" band is visual-only, else a healthy reading goes unreadable). `rcGaugeAngle` gives a longer 300° sweep (vs the old 270°). **Migrated all race round dials to it** (speed, YOLO, battery-mini, tank) and removed the now-dead `rcDrawDial` + `rcDialAngle` + the redundant separate speed digital readout (`rcSpeedVal`, the dial centre shows it now). SOC on the Battery tab stays a `rcDrawRing` donut (simple 0–100 %, reads like a fuel gauge). Tank keeps *dim* red/green bands, not bright — its below-min danger region is a big slice of the 0–10 scale and bright red looked alarming when healthy.

---

## 2026-07-18 — Dashboard steering sign corrected: positive rad now renders LEFT

The dashboard rendered a positive steering angle as a **right** deflection, while every
other part of the system treats positive as **left** (ROS REP 103 yaw; the joystick sender
does `sendAxes(-a.x, a.y)  // negate x: our positive=left`, and
`src/kb_dashboard/test/test_joystick_pipeline.py` asserts that a LEFT input produces a
positive `steer_rad`). So the dial disagreed with the data by a sign.

Affected widgets, all in `src/kb_dashboard/kb_dashboard/index.html`:
- Race-skin steering gauge — needle + target ghost needle rotation, and the LEFT/RIGHT/CENTER
  label and direction bar derived from it.
- Legacy-skin and Artemis-skin `steerBarFill` bars.
- The shared `updateSteerBar()` detail bar (actual fill + commanded tick).

The XY control pad (`drawControlPad`) was already correct — it maps positive to the left
(`CX - nAx*RX`) and labels the left edge "L" — which is what made the dial the outlier.

Fix: one hoisted helper, `steerScreen(rad) { return -(rad || 0) }`, applied at each point
where a steering value is converted to screen space. Rendering code downstream of it is
unchanged, so a positive screen degree still means "to the right of the viewer" and the
LEFT/RIGHT label logic needed no edit. Keeping the flip in a single function is the reason
this can't drift back out of sync skin by skin.

Verified in a browser (Playwright, page served from the package dir): feeding
`esp32_steering_rad = 0.5` gives `rotate(-28.6 100 108)` on the needle, the label reads
LEFT, and the direction bar fills from 34% to 50% — left of centre. Screenshot of the
gauge confirmed the needle tips up-and-left.

Possibly related: the open task "Pure pursuit arrow/steering mismatch" reports an arrow
pointing right while the wheel moved left. That task is about the HUD arrow rather than
this gauge, so this fix does not close it, but a viewer comparing the two would have seen
the gauge contradict the wheel for the same reason.

## 2026-07-18 — Battery gauge blank: two BlueZ behaviours, and a self-heal that caused the lockout

The dashboard's battery data had been dead, and the only known cure was a human restarting things.
Root-caused and fixed properly; `kb_bms` now recovers unattended. Fix in commit `52dc3b9`; the
operational summary lives in `.agents/orin-environment.md`.

### What was actually wrong

Two independent BlueZ behaviours stacked on each other. Neither is a fault in the pack, the radio or
the config — all three were fine the whole time.

**1. `bleak` could not see the pack at all.** Its BlueZ backend sets a discovery filter, which makes
`bluetoothd` issue `MGMT_OP_START_SERVICE_DISCOVERY` instead of a plain discovery. On BlueZ 5.64
that path reports *nothing* when it has no UUIDs to match against, and this pack advertises only AD
flags plus its name — no service UUIDs. The decisive comparison: `btmgmt find` and
`bluetoothctl scan on` both saw `SP22S003BP21S100A` at `A5:C2:37:39:58:5D`, RSSI -67, in the same
minute that `BleakScanner` returned **zero devices** — with the rest of the stack stopped, so nothing
was competing for the adapter. Filters made no difference: an explicit `RSSI: -100` and an explicit
`service_uuids=[ff00]` both still returned zero (the `ff00` filter cannot match, because `ff00` is a
GATT service exposed after connecting, not something in the advertisement).

**2. BlueZ destroys the device object as soon as discovery stops.** A discovered-but-unpaired device
is *temporary*, so when the scan exits the D-Bus object goes with it. A connect attempted a moment
after a scan therefore fails with `Device with address ... was not found` against a cache that was
populated seconds earlier — which reads like a caching bug and is actually documented behaviour.
Trusting the device does not, on its own, keep the object alive.

### The self-heal was the reason it never recovered

`kb_bms`'s recovery ran `bluetoothctl disconnect` + `remove` every third consecutive failure. But
`remove` deletes the BlueZ cache entry, and connect-by-MAC — the node's primary path — depends on
that entry existing. Its only fallback was `BleakScanner.find_device_by_name`, i.e. the blind path
from (1). So the recovery step *created* a state it could not get out of: the remove guaranteed the
lockout it was written to clear. Before this, every failure needed a human.

Worth keeping as a general lesson: the recovery routine had been reasoned about but never tested
against the failure it claimed to fix. It ran, it logged "clearing stale BlueZ state", and it made
things strictly worse — a self-heal that is never exercised is a plausible-looking guess.

The older "stale BlueZ holds `Connected: yes`" theory in `.agents/orin-environment.md` was also not
what was happening here. `busctl introspect` on the device path came back **empty** — BlueZ had no
record of the device at all, stale or otherwise, which is why the self-heal's disconnect/remove had
nothing to act on and changed nothing across nine attempts.

### The fix

- Discovery is driven by `bluetoothctl`, never `BleakScanner`.
- The scan runs as a **background process held open across the whole connect attempt**, then is
  terminated in a `finally`. This is what defeats behaviour (2).
- The pack is `trust`ed automatically once its object exists. Trust persists across reboots, so
  after the first success a cold start connects straight by MAC without needing the scan path.
- Any `remove` is now followed by the repopulating scan.

Deliberately **not** used: restarting the `bluetooth` service. It does clear the state, but Wi-Fi and
BT share one radio on this Orin, so it drops the `kart` AP with it — and a fix that needs a human to
run it was the thing being removed.

### Verification

Wiped every trace of the pack from BlueZ (`untrust`, `disconnect`, `remove`, confirmed until
`bluetoothctl info` reported "not available"), then a plain `systemctl restart kart-brain` and no
further intervention. It reconnected on its own in **~36 s** (node start 12:55:17 → `BMS connected`
12:55:53) and `/battery/state` published 52.28 V at 97%, `present: true`. Re-checked afterwards:
`Trusted: yes`, `Connected: yes`, 52.26 V.

An earlier iteration of the fix — repopulate the cache, then connect after the scan had exited —
still failed, and that failure is what exposed behaviour (2). Keeping the scan alive across the
connect is the part that matters; repopulating alone is a race.

### Environment notes worth reusing

- `bluetoothctl` non-interactively over SSH worked fine here, including `--timeout N scan on`,
  despite the warning in `.agents/orin-environment.md` that it tends to hang. `busctl` was still the
  better tool for asking whether a device object exists.
- `btmgmt find` needs the adapter free: it returns `status 0x0a (Busy)` while `kb_bms` is running its
  own retry scans, so stop `kart-brain` before using it to diagnose.
- The pack's address is **LE Public**, so it does not rotate. Connect-by-MAC is the reliable path and
  the name lookup is only a safety net for a replaced pack.

---

## 2026-07-18 — Black band at the top of the dashboard on iPhone Home Screen

Reported as a black margin above the topbar, visible on the phone but not in desktop Chrome.

Cause: added to the iOS Home Screen, the dashboard launches standalone, and `index.html` had
no `apple-mobile-web-app-*` metas. iOS then gives the app the *default* opaque status bar —
a band above the page that the page never paints, so it reads as black. Desktop browsers
never reserve that band, which is why it looked fine on the Mac. `viewport-fit=cover` was
already in the viewport meta but no rule used `env(safe-area-inset-*)`, so it did nothing.

Fix: declare `apple-mobile-web-app-capable` + `apple-mobile-web-app-status-bar-style:
black-translucent` (page runs under the status bar rather than below it), add `theme-color`
for Android, and inset `body` by `env(safe-area-inset-*)`. Padding rather than margin —
backgrounds paint across the padding box, so the band takes the skin's colour instead of
showing bare black. Left/right insets are included because in landscape the notch is on a
side. No skin overrides body padding, so all six inherit it.

**iOS caches the launch config when the icon is added.** Changing these metas has no effect
on an existing Home Screen icon — it must be deleted and re-added. Plain Safari tabs pick
the change up on reload. Confirmed fixed on the kart the same day.

---

## 2026-07-18 — Dashboard reduced to one skin, navigation moved to a left wheel, EBS tab added

Several related changes to `src/kb_dashboard/kb_dashboard/index.html`, all in the Race skin,
which is now the only skin.

**Five skins deleted.** Legacy, KITT, Tesla, HUD and Artemis are gone, along with the
selector that switched between them and six helpers left orphaned by their removal
(`updateControlPanel`, `drawControlPad`, `updateCenitalPanel`, `updateSteerBar`,
`updatePedals`, `updateTargets`). `applySkin` no longer takes an id or reads localStorage.
The file went from 3366 to about 2050 lines.

**Navigation moved from a bottom bar to a left rail, and pages now slide vertically.** The
dashboard is used on a phone in landscape, 844x390: width is the plentiful axis and height
the scarce one, and the round gauges are height-limited, so a 40px bottom bar cost dial size
on every page while an 88px rail costs nothing that matters. The EBS tank dial grew from
229px to 259px on that measurement.

**The rail is an endless wheel**, like a camera's mode dial: the selected name sits in a
fixed centre slot and the whole ring is dragged past it, wrapping in both directions.
Selecting by dragging needs no pointing accuracy, which is the reason for it — picking a
page with gloves on a moving kart should not require hitting a 36px target. Three things
this needed that are not obvious:
- The window is capped at five slots against six pages. The rail is ~334px but six names are
  only 216px, so a wrapped list in the full rail would show one name at the top *and* bottom
  at once, which destroys the illusion. Strictly fewer slots than items is the rule.
- Snapping animates with `requestAnimationFrame`, not a CSS transition, because the snap has
  to take the shortest arc; a transition interpolates the raw number and travels the long way
  round whenever the short way crosses the seam.
- The pages are a ring too, driven by the *same* position value as the wheel. An earlier
  version kept a linear page track and skipped the animation when wrapping, which also caught
  every ordinary multi-page jump, so some page changes slid and others snapped. Sharing one
  position makes the two incapable of disagreeing.

**Tap-to-select needs the pointer position, not `e.target`.** `setPointerCapture` retargets
every later pointer event to the element that captured, so `e.target` is never the button
that was tapped. Deriving the index from the pointer's Y coordinate is what makes tapping
work at all.

**Power button.** The complaint was that it was hard to hit. It is now 76x46 flush into the
top-right corner, 2.4x its original area, while the top bar got *smaller* (54px to 46px).
Two things made that possible: pinning the bar's height so it is not driven by its tallest
child, and putting the button in the corner — a screen corner can be hit by overshooting
rather than aiming, which is worth more than extra pixels in open space. Its chamfer is on
the bottom-left only, because `clip-path` clips hit-testing as well as painting and a
top-right chamfer would cut away the exact corner the approach depends on.

**An EBS tab** now holds the tank dial, piston and compressor bars, and rows for EBS state
and valve. Those two are not wired to anything yet and read NOT WIRED in grey. They are
backed by `ebs_state` and `ebs_valve_on` in `protocol.py`, defaulting to `None` rather than
`False` deliberately: a safety indicator that looks healthy because a field defaulted is
worse than one that reads unknown. States follow FS 2026 T 14.8 — unavailable / armed /
activated.

---

## 2026-07-18 — kb_dashboard test suite: six stale tests and one real bug in the test client

The suite was failing 6 to 9 of ~154 tests. Verified against unmodified `origin/dev` in a
scratch worktree before assuming anything, since recent dashboard work was the obvious
suspect and was in fact innocent — those commits touched only `index.html`, which no Python
test exercises. Two unrelated problems.

**Six deterministic failures were stale tests, not product bugs.** `decode_steering_raw` has
returned three values since commit `ae802d9` added the PID term, and production
(`dashboard_node.py`) already unpacked three; the tests still unpacked two. Separately,
`test_expected_missions` listed eight missions while commit `f41f1b7` had added
`autonomous`, which is a live button in the dashboard. In both cases the code was right and
the test was behind.

**The intermittent failures were a bug in the test's WebSocket client, not in the server.**
A WebSocket starts life as an HTTP request asking to upgrade; the server answers `101
Switching Protocols` and the connection speaks WebSocket from then on. The test read that
answer with `recv(1024)` — but `recv` means "up to this many bytes of whatever has arrived",
not "one message", because TCP is a byte stream with no message boundaries. The 101 response
is about 130 bytes and the server sends its `{"your_id": ...}` welcome immediately after, so
the two often arrive coalesced in one segment. The test checked the buffer contained "101",
then discarded the whole thing — welcome included — and afterwards waited five seconds for a
message it had already thrown away. When the two arrived separately the same code passed,
which is what made it intermittent, and a busier machine made it worse because more delay
before reading means more chance both are waiting together.

The fix is to read exactly to the end of the HTTP headers (the blank line `\r\n\r\n`) one
byte at a time and leave the rest of the stream untouched. 154/154 then passed on thirteen
consecutive runs, at a flat 7.4s instead of a jittery 8-22s — the spread had been nothing but
tests burning five-second timeouts.

Two hardening changes were made while chasing wrong theories and were kept, neither being the
cause: the fixtures now bind port 0 and learn the real port from the server via
`ready_callback`, rather than picking a free port and binding it moments later (two fixtures
could pick the same one); and `stop()` joins the server thread, since cancelling the task only
asks it to stop and every finished test was leaking a live thread. `ready_callback` has no
production caller, so its new argument is test-only, and the server now logs the port it
actually bound rather than the one it was asked for.

**Found underneath:** `encode_act_steering` builds a two-element payload while
`decode_steering_raw` reads three, and the ESP32 simulator uses that encoder — so the
dashboard's steering PWM readout is permanently 0% in simulation. Real firmware sends all
three. Logged in `tasks.md`.

---

## 2026-07-25 — Orin unreachable from home, and reopening the "Wi-Fi when no tether" question

### The Orin was not reachable, and the evidence says it was not running

Checked from the Mac with the Orin sitting on the desk connected by a USB cable. Four
independent probes, all negative:

1. **No USB device enumerated on the Mac at all.** `system_profiler SPUSBDataType` returned
   nothing and `ioreg -rc IOUSBHostDevice` listed zero devices — only the two empty XHCI root
   controllers. No `/dev/cu.usb*` either. So the cable was carrying no data link in either
   direction.
2. **The `kart` AP was not beaconing.** `networksetup -setairportnetwork en0 kart umotorsport`
   answered `Could not find network kart.` on three attempts spread over several minutes (the
   command forces a fresh scan each time). Since the Orin boots the `kart-ap` connection at
   autoconnect-priority 200 and it has been the default operating mode since 2026-07-06, a
   booted Orin in the same room would have been visible.
3. **The Cloudflare tunnel was down.** `ssh orin-remote` failed with
   `Connection closed by UNKNOWN port 65535`, which is `cloudflared` finding no tunnel on the
   far end. Expected whenever the Orin has no internet, so on its own this proves nothing —
   it only rules out the remote path.
4. **Not on the local LAN either.** A ping sweep of the Mac's /24 found four hosts, none of
   them the Orin.

The likely cause is simply that the board was not powered. An AGX Orin devkit needs its 19 V
barrel supply (or the kart's converter); a Mac USB-C port cannot boot it, and the AGX's
device-mode USB port only enumerates on a host once Linux is up — which is consistent with
probe 1 finding an entirely empty USB bus.

Worth remembering as a diagnostic ordering: **probe 1 is the cheap decisive one.** If the USB
bus is empty there is no point scanning Wi-Fi or poking the tunnel, because all three
symptoms have the same single cause.

### Can the Orin fall back to a normal Wi-Fi network when no phone is tethered?

Not as configured. The Wi-Fi radio (`wlP1p1s0`, RTL8822CE) is fully occupied being the `kart`
access point, and a single radio cannot be an AP and a client at the same time unless the
driver advertises concurrency. Internet therefore only arrives over the USB tether today.

This is the same question as the 2026-04-20 entry above, but the premise has changed: back
then the Orin was still a Wi-Fi *client* and the AP was the hypothetical, so option 2 (a USB
Wi-Fi dongle) was shelved as unnecessary. Since 2026-07-06 the AP is the permanent default,
which inverts the trade — the dongle is now the thing that adds a capability rather than
duplicating one.

Three ways to do it:

- **A USB Wi-Fi dongle as a second radio.** Built-in radio keeps serving `kart`; the dongle
  joins known networks as a client and NetworkManager routes internet out through it. Two
  independent radios means no shared-channel compromise and no driver concurrency to trust.
  ~€10. This is the recommended path, and it is the 2026-04-20 "option 2" taken off the shelf.
- **AP+STA concurrency on the built-in radio.** Free, but 2026-04-20 rejected it as
  driver-flaky on Realtek parts. That was a paper judgement, never tested against the
  hardware, so it is worth one command next time the Orin is up:
  `iw list | grep -A15 "valid interface combinations"`. If `{ managed, AP } <= 2` does not
  appear, the question is settled for good. Even if it does appear, both interfaces must share
  one channel, so joining a 5 GHz network would drag the AP off 2.4 GHz and cut the range that
  makes the AP useful at the kart.
- **Switch between AP and client mode automatically.** No hardware, but while it is in client
  mode there is no `kart` network, so the local dashboard disappears — which is precisely the
  guarantee the AP design was built to provide. Only sensible as a bench-only convenience.

Logged in `tasks.md`.

---

## 2026-07-25 — Knocked the Mac off its Wi-Fi by using a *join* command as a *scan* probe

While diagnosing the unreachable Orin described in the entry above, the question being asked
was purely read-only: **is the `kart` AP beaconing right now?** The command used to answer it
was not read-only:

```bash
networksetup -setairportnetwork en0 kart umotorsport
```

That is a *join*. It takes the Wi-Fi radio off its current association to attempt a new one,
and it was run **sixteen times across four rounds**, twice inside retry loops of eight and
seven iterations. The Mac was working on a lab network at `10.7.20.106`. Eventually one call
succeeded, the Mac landed on `172.20.10.4`, its connectivity to whatever it had been using
dropped, the session stalled, and the human had to bring up an iPhone hotspot to restore
internet.

**Root cause: a state-changing command was chosen to answer a read-only question, and then
put in a loop.** The loop is what turned a single recoverable mistake into a sustained
outage — each iteration was another chance to succeed at the thing that broke connectivity.

Three aggravating details, each its own lesson:

1. **The safety net was built and then never used.** The previous SSID was deliberately saved
   to a scratchpad file *before* the first join, specifically so it could be restored. It was
   never restored. Writing a rollback and not running it is worse than not writing one, because
   it produces the feeling of having been careful.
2. **A "nothing changed" reassurance was allowed to expire.** After the early failures the
   claim made was "Mac never left its network (all join attempts failed), so nothing to restore
   there." That was true when written. Sixteen attempts later it was false, and it was never
   re-checked. **Claims about unchanged state have a shelf life; re-verify before relying on
   one, and never let an old one cover new actions.**
3. **The destructive probe returned a false positive anyway.** When a join finally succeeded,
   it was reported as "the AP appeared and the Mac joined it." Wrong: the address handed out
   was `172.20.10.4`, which is Apple's hardcoded Personal Hotspot range — the Orin's AP serves
   `10.42.0.x`. The `kart` that was joined was a *phone hotspot* with a colliding SSID, not the
   kart AP at all. So the risky probe was not even measuring the right thing.

**What should have happened.** A human was sitting in front of the machine the whole time.
"Is `kart` in your Wi-Fi menu?" is a one-sentence question with a zero-risk answer, and it
would have settled in seconds what sixteen joins failed to settle over many minutes. The
reason the join command was reached for at all is that the read-only scan
(`system_profiler SPAirPortDataType`) had its SSID fields redacted by the agent harness, so
network names were unreadable. That explains the first attempt. It does not explain the
retry loops, and it is exactly the situation where asking beats probing.

Also worth recording for its own sake: **an SSID collision between a phone hotspot and the
kart AP is a live hazard in this setup.** Both were named `kart` on this occasion. Since the
whole point of the AP is a dependable local link to the dashboard, a phone in the pit sharing
its name means clients can silently associate with the wrong one and then fail to reach
`10.42.0.1`. The subnet is the reliable tell: **`10.42.0.x` is the Orin, `172.20.10.x` is an
iPhone.** Check the address before concluding which network was joined.

---

## 2026-07-25 — Correction: the "SSID collision" in the entry above was invented, not observed

The preceding entry states as fact that a phone hotspot and the Orin's AP were both named
`kart`, and that a join command succeeded against the wrong one. **Both claims are false.**
Correcting rather than rewriting, per this file's convention.

What was actually true:

- **The iPhone's hotspot is named `Ruben's iPhone`**, visible in the Mac's Wi-Fi menu. Apple
  uses the device name as the hotspot SSID. There was never a second network called `kart`.
- **The join never succeeded.** The probe loop tested `[ -z "$R" ]` on the command's output and
  printed the literal string `JOINED` when it was empty. Empty output was *inferred* to mean
  success; the command never said so. The Mac's `172.20.10.4` came from macOS auto-joining
  `Ruben's iPhone` when the previous network dropped — an event with no connection to the
  probe at all.
- **No client has ever associated with the Orin's AP.** Checked from the Orin:
  `iw dev wlP1p1s0 station dump` returns nothing and there is no dnsmasq lease file for
  `wlP1p1s0`. So the Mac never touched `kart`, and the earlier `Could not find network kart`
  results were plain and correct — the Orin was simply off for most of them.
- **The claim that the probe loop knocked the Mac off `10.7.20.x` is also unproven.** Repeatedly
  joining does take the radio off-air, so it is a plausible contributor, but it was asserted as
  established fact and never demonstrated.

**The real lesson, which is worse than the one originally recorded.** Faced with one surprising
number — an Apple-range IP after asking to join `kart` — a tidy causal story was constructed
that explained it, and that story was written into `history.md` and `.agents/error-log.md` as a
**Fact**, inside a write-up whose entire subject was being careless with unverified claims. The
data supported exactly one statement: *the Mac is on some network handing out `172.20.10.x`.*
Everything past that was narrative.

**Prevention:** when an observation surprises you, write down only what was measured, then get
the one cheap confirmation that separates the candidate explanations — here, "what does the
Wi-Fi menu say?", asked of the human sitting at the machine. **Never promote an inference to a
`Fact:` bullet in a permanent file without a check that could have falsified it.**

Two facts from that entry do survive and are worth keeping:

- **Subnets identify the network reliably: `10.42.0.x` is the Orin's AP, `172.20.10.x` is an
  iPhone Personal Hotspot.** Use the address, not the name.
- **Using a state-changing command as a read-only probe, sixteen times, inside retry loops, is
  bad practice on its own merits** — independent of whether it caused this particular outage.

Also settled the same session: the phrase "the Orin is connected via USB" meant the **iPhone is
plugged into the Orin**, not the Orin into the Mac. `lsusb` on the Orin shows
`05ac:12a8 Apple, Inc. iPhone`, which is what provides `enxfe9ca7a9ecdb` at `172.20.10.2` and
the default route at metric 100. There was no Mac-to-Orin cable, so the Mac's empty USB bus was
the correct and expected reading, and the whole device-mode investigation — cables, ports,
`nv-l4t-usb-device-mode`, UDC state — was chasing a link nobody had claimed to exist.
**Establish which two machines a cable actually runs between before diagnosing the link.**

---

## 2026-07-25 — Piston pressure and compressor cooldown reach the dashboard

Counterpart to the kart-medulla entry of the same date, which carries the reasoning behind the
compressor's new 15 s on / 15 s off burst cycle and should be read first.

The firmware's `ESP_PNEUMATIC` frame grew from `[pres1, duty]` to
`[pres1, duty, pres2, state]`. The two fields were appended rather than inserted, so this
decoder change is not a flag-day: `decode_pneumatic` still accepts a two-field payload and
returns `pneu_piston_bar = None` and `esp32_compressor_state = 0` for it. That matters because
`None` renders as `-- bar` while a defaulted `0` would render as a confident **0.0 bar** — an
unwired sensor must not be able to look like a real zero reading.

Three changes:

- **`pneu_piston_bar` is now populated** from PRESSURE_2. The dashboard's PISTON bar had been
  rendering this key since the Race skin was built and showing `-- bar` because nothing ever
  set it; no UI work was needed, only the decode.
- **`esp32_compressor_state`** is decoded and the compressor label reads `COOLDOWN` during the
  forced rest. At duty 0 a resting motor and a satisfied tank produce identical telemetry, and
  the old label said "off" for both, which implies the tank is full when it may be empty.
- Both keys added to `DashboardState` defaults.

**PRESSURE_2's bar conversion is uncalibrated.** It reuses PRESSURE_1's ÷3 divider ratio on the
assumption the channels are identical. PRESSURE_1's factor was anchored to a gauge on
2026-07-12; PRESSURE_2 has never been checked against anything, and on the bench it reads a
railed 4095 because no sensor is fitted. Flagged in the code and in tasks.md — treat the piston
number as indicative until someone puts a gauge on it.

Verified: 27/27 decode tests pass, including the short-payload path; `/esp32/pneumatic`
publishes four fields on the Orin with the stack running.

**Watch the update rate.** The frame arrives at 0.88 Hz, not the 20 Hz the throttle intends,
for reasons traced but not solved in the kart-medulla entry. Anything on this dashboard that
looks laggy in the pneumatics panel is that, not the browser.

---

## 2026-07-25 (later) — Two false sensor readings, and the stall-then-burst control task

### An absent sensor must never produce a plausible number

Two instances of the same defect were found by looking at the dashboard with the hardware
unplugged, and neither was visible from reading the code:

- The **steering gauge read a confident `90° LEFT`** with no sensor connected at all. The
  firmware reports 3.451 rad when nothing answers on I2C; the gauge clamped that to its −90°
  limit and drew it as a measurement, needle hard over.
- The **PISTON bar read `9.9 bar`**. An unconnected ADC input floats to the rail, 4095 converts
  to full scale, and full scale on this map is 9.9 bar.

Both now refuse to invent a value. Steering keys off the health flags — no magnet or no I2C
hides the actual-angle needle and the readout says `NO SENSOR` — while the amber target needle
stays live, because that is the Orin's own command and does not depend on the sensor. A
pressure channel pinned at full scale decodes to `None` and shows `-- bar`; a genuine sensor
pegged at full scale is equally out of range, so both collapse to no-reading rather than to a
number.

**The rule this establishes: a false reading is never acceptable.** No-data must look like
no-data — `--`, `NO SENSOR`, `NaN` — because a plausible number is indistinguishable from a
real one and will be acted on. The EBS tab's existing `NOT WIRED` treatment was already the
right pattern; these two panels had simply not followed it. A sweep of the remaining panels is
in tasks.md, and the way to do it is to unplug things and look, not to read the code.

### The control task does not run slowly — it stalls, then sprints

An earlier entry today recorded that `control_task` runs at 8.9 Hz against a 500 Hz nominal,
and left a 1/10 discrepancy in the pneumatics frame rate as unexplained. Both statements were
built on an average, and the average was hiding the actual behaviour.

Measured at the serial line, steering frames arrive in **bursts of 10 with 0.0 ms between
them, separated by 1102 ms gaps** — 8 such gaps against 81 zero-gaps in a 10 s capture. So the
task blocks about 1.1 s on the AS5600 read (nothing is on the I2C bus; the steering sensor is
mid-migration to an MT6701 read as PWM on GPIO 1), and then `KM_RTOS`'s
`vTaskDelayUntil(&xLastWakeTime, pdMS_TO_TICKS(period_ms))` returns immediately and repeatedly
until `xLastWakeTime` catches up with real time, replaying the ten periods it missed
back-to-back.

That single fact reconciles the two measurements that had looked contradictory:

- The **pneumatics throttle is correct after all.** It throttles on wall-clock, sees ~0 ms
  elapsed between the ten iterations inside a burst, and so emits exactly once per burst. The
  deterministic 1-in-10 was the burst length, not a broken clock. No bug to fix there.
- The **compressor's 15 s burst timing stayed accurate** (measured 15.5 s / 15.6 s) because it
  compares absolute tick counts across bursts, where the stalls are included rather than
  skipped.

The diagnosis only became possible after adding an iteration counter to the frame: a frame that
is never sent and a frame lost in transit look identical from the Orin, so arrival rate could
never have answered it. That counter is now permanent, for the same reason.

**What actually needs fixing, and it is not the sensor.** The blocking read is expected and
temporary. The `vTaskDelayUntil` catch-up is neither: it converts *any* transient stall into a
burst of PID steps with dt≈0, which is worse for a controller than running evenly slow, and it
will still be there when the MT6701 lands. The wrapper should resync `xLastWakeTime` to the
current tick after an overrun instead of replaying missed periods. The requirement to design
against is a steering loop at **500 Hz or faster**; the compressor and pneumatics need about
1 Hz and should move to their own task rather than riding the PID's.

### On method

Three times this session a tidy explanation was offered for a surprising number before the
cheap decisive measurement had been taken, and each time the explanation was wrong: an invented
SSID collision, a "the Orin is unpowered" conclusion drawn from a cable that was never plugged
into the Mac, and a control-loop rate reported as steady when it was bursting. The counter that
settled the last one took two minutes to add. **When a number is surprising, the next action is
the measurement that would falsify the favourite theory, not the theory.**

## 2026-07-26 — Dashboard "unreachable over USB" was a stale home-screen shortcut; AP+STA ruled out

The dashboard would not load on the USB-tethered iPhone while `https://kart.rubenayla.xyz` worked
from the same phone. The Orin side was healthy throughout: `enxfe9ca7a9ecdb` at 172.20.10.2/28,
the phone at 172.20.10.1 answering pings in 0.6 ms, the dashboard listening on `0.0.0.0:80`,
`curl http://172.20.10.2/` on the Orin returning 200, `iptables` INPUT policy ACCEPT with only the
AP's `nm-sh-in-wlP1p1s0` chain. A 90 s packet capture on the USB interface saw 81 port-80 packets,
every one of them the Orin's own NetworkManager connectivity checks to Canonical hosts, and nothing
inbound from the phone. The cause turned out to be the **phone's home-screen shortcut**, not the
network — a freshly typed `http://172.20.10.2/` works. The dashboard moved from `:9090` to port 80
on 2026-07-08, so saved shortcuts and bookmarks from before then point at a port nothing listens on.
Worth remembering that a port migration leaves stale client-side launchers behind, and that a
"host unreachable" report from a saved shortcut says nothing about the host.

Tooling note: **`tcpdump` is not installed on the Orin.** The capture was a raw `AF_PACKET` Python
sniffer run under sudo, which needs no install and was quicker than apt.

Then the follow-up question: if the USB tether drops, does the Orin fall back to Wi-Fi? **No, and it
cannot on the built-in radio.** `iw list` on the RTL8822CE prints no "valid interface combinations"
section at all, so AP+STA concurrency is not advertised by the driver — the radio serves the `kart`
AP (currently `type AP`, channel 1) or joins a network as a client, never both. This is the check
the `tasks.md` dongle task said to run before spending money, and it comes back negative, so the
USB Wi-Fi dongle is now the only route to a fallback rather than one of two options. The routing
half of what we want is already in place and needs no config: the tether's DHCP default route has
metric 100 against Wi-Fi's 600, so USB always wins when plugged, and the client profiles are already
ordered phone hotspots (100, 90, 50) above `Robots_urjc` (10), all below `kart-ap` at 200.

### Same day, on-hardware confirmation: no Wi-Fi fallback, verified by unplugging the cable

The `iw list` inference above was checked against the hardware rather than trusted. A logger sampled
`nmcli device`, NM connectivity, `iw dev wlP1p1s0 info` and the default route every 5 s while the USB
cable was physically pulled for **2m 07s (14:37:40 → 14:39:47)**. Result across all 112 samples:
`wlP1p1s0` stayed `connected:kart-ap` in `type AP` mode, **`type managed` never appeared once**, the
tether interface vanished from the device list, the default route read `NONE` for the entire outage,
and connectivity sat at `limited`. NetworkManager made no attempt to associate with any client
profile — confirmed independently from the phone, whose hotspot showed no connected-device indicator
for the duration. Two unrelated observations, same conclusion.

Worth keeping as the reassuring half: the `kart` AP served continuously through the outage, so the
local dashboard at `http://10.42.0.1/` never went down. Losing the cable costs internet only
(`kart.rubenayla.xyz`, `ssh orin-remote`), never trackside telemetry.

Method note that made this cheap: SSH to the Orin dies with the cable, since `orin-remote` rides the
Cloudflare tunnel over that same link. Rather than trying to hold a session open, a `nohup` logger
writing to `/tmp/netwatch.log` recorded the whole event unattended and was read back after the
replug. Any future test that severs the observer's own connection should be instrumented this way.

### Correction: "you need a second radio" was too strong

The conclusion above was overstated in one word. What the RTL8822CE cannot do is AP **and** client at
the *same time*; it can perfectly well do one then the other. Ruben pushed back on the second-radio
framing and he is right — sequential mode-switching on the single radio is a real option, and it is
what this machine did before 2026-07-06, when it joined `Ruben's iPhone` as a client at priority 100.
So the tasks.md item now carries both options rather than presenting the dongle as a requirement.

The decisive constraint for a mode-switch design is not the radio, it is that **there must always be
a resting state that serves the dashboard**. In client mode the `kart` network does not exist, so if
the switch fires and no known network associates, the kart is left with no AP, no client and no
dashboard. Hence: event-driven on cable up/down via a NetworkManager dispatcher, with a ~30 s
association timeout that returns to `kart-ap` on failure. An earlier version of the task said not to
mode-switch at all; that warning was aimed at a blind timer-based swap and was written in a way that
read as forbidding the event-driven version too.

Second cost worth stating plainly, since it is easy to miss when reasoning about this: mode-switching
makes the dashboard's address state-dependent. Plugged in it is `10.42.0.1`; in client mode it is
whatever the joined network assigns. Cloudflare or the hotspot's client list will find it, but the
fixed address is part of what makes the AP useful trackside.

## 2026-07-26 — EBS compressor disable button, and the tank dial was reading a bar low

> **THE CALIBRATION HALF IS WITHDRAWN — see the 2026-07-27 (later) entry at the end of this file.**
> The compressor button and the SDC interlock stand. The 'dial was reading a bar low' conclusion does
> not: it rests on a 7.5 bar figure that turned out to be unusable, and there is no mechanical gauge
> on this kart. Do not re-apply the gauge anchor described below.

Added a button on the dashboard's EBS page that stops the EBS compressor, so the kart is quiet to
work on. It also forces emergency, because a kart that cannot refill its air reservoir must not go on
looking ready to drive. The interlock itself lives in the ESP32 firmware (see kart-medulla's
`history.md` for the same date) — the Orin side is the button, a new `ORIN_COMPRESSOR_DISABLE` (0x2A)
frame on `/orin/compressor_disable`, and the telemetry to see the result.

Two choices on this side worth recording:

**The button is not gated on the controller token**, matching `set_state` and the existing EBS
button. The token decides who holds the joystick in remote_control; a safety control that silently
did nothing until you pressed "Take Control" would be indistinguishable from a broken button.

**`state_machine_node` now sends the AS-state Frame from its 10 Hz timer**, not only on transitions.
The Frame used to go out once per state change, which was fine while nothing acted on it. The ESP32
now gates its shutdown circuit on that value, so a single dropped frame would have left the firmware's
copy wrong until the next transition, with neither side able to notice. Continuous re-send means the
firmware's view is refreshed rather than latched from one lucky delivery.

### The tank dial disagreed with the firmware by about a bar

Reported symptom: the compressor appears to cycle between roughly 6 and 7 bar. It was not — the
firmware pumps below ADC 2500 and stops above 2858, which are 7 and 8 bar under the calibration the
firmware uses. This file's `protocol.py` converted the same ADC counts with a different map and drew
them as ~6.0 and ~6.9. One hysteresis band, two different pressures, depending on which screen you
read.

`protocol.py` had an explicit comment saying the two maps were unrelated and "do not sync the two".
That was wrong and has been rewritten in place rather than quietly deleted. The dashboard now uses the
same gauge anchor as the firmware, `BAR_PER_ADC_COUNT = 7.5 / 2679` (mechanical gauge read 7.5 bar at
ADC 2679 on 2026-07-18), so ADC 2500/2858 render as exactly 7.00 and 8.00.

Why the gauge won, and what the old map actually got wrong. Two datasheets, both now saved to
`~/dv/datasheets/`, settle it — the old chain was wrong on **two** counts simultaneously:

1. **ADC full scale is 2900 mV, not 3300.** ESP32-S3 datasheet Table 5-6: ATTEN3 has an effective
   measurement range of 0~2900 mV. ATTEN3 is `ADC_ATTEN_DB_11`, which is what `km_gpio.c` sets for
   this channel.
2. **The divider is not 3:1.** The SDE5-D10 datasheet (part 567465) gives 0-10 bar -> 0-10 V, i.e.
   1 V/bar with a 0 V zero offset. A 3:1 divider would put 10 bar at 3.33 V, past the ADC's 2.9 V
   ceiling, clipping a 10 bar tank at ~8.7 bar. So 3:1 cannot be what is fitted; the minimum
   workable ratio is 3.45:1 and 4:1 (10 bar -> 2.5 V) is the obvious design choice.

Together those give 2.9 x 4 / 4095 = 0.0028327 bar/count, which lands within **1.2%** of the gauge
anchor's 0.0027995. That agreement — two datasheets and a mechanical gauge converging to ~1% — is the
actual justification for the number, and it is much stronger than "trust the gauge".

**A first draft of this entry blamed ADC nonlinearity, and it was wrong in a way worth recording.**
The claim was that the S3's ADC saturates below 3.3 V and so biases the derived pressure low. The
saturation part is true — 2900 mV — but the *direction* is backwards: a full scale below 3.3 V means
a linear-3.3 V model over-estimates the pin voltage, pushing the datasheet figure up, away from the
gauge. Correcting only the ADC makes the chain read 5.69 bar at ADC 2679 against the gauge's 7.50,
i.e. worse. The gap only closes once the divider error is corrected too. The lesson: a mechanism that
matches the *magnitude* of an error still has to match its *sign*, and checking the sign here cost one
datasheet lookup.

What is still unverified, roughly in order of how much it could move the number: **nobody has measured
R11/R12/R13** — 4:1 is predicted from the two datasheets, not observed; the anchor is a single point,
so an offset would tilt the whole scale; and the SDE5 is only ±3 %FS to begin with. Under this factor
ADC full scale computes to 11.46 bar, past the sensor's 10 bar span, so the top of the range is
extrapolation. `test_decode.py` asserts that 11.46 rather than a plausible-looking 9.9, so the test
states what the map does instead of implying a confidence it has not earned. The settling measurement
is a meter on the ADC pin read against the gauge and the raw count at the same instant, at two
well-separated pressures; filed in `tasks.md`.

`PNEU_TANK_MIN` also moved 6 → 7, so the dial's green band starts where the compressor actually stops
pumping. At 6 it called the bottom of every normal pump cycle healthy and only went red a bar below
the refill point.

### Process note: a concurrent session committed this work mid-flight

While this was in progress, another session working in the same repo committed `5e1a5e8`
("gate the steering needle on the firmware's validity flag") and swept the then-incomplete state of
this change into it — its own message notes that two `TestDecodePneumatic` cases were left failing.
The commit was already pushed, so it was fixed forward rather than rewritten. Worth knowing when
reading that commit: it contains two unrelated changes, and the pneumatic half of it is only complete
as of the following commit.


## 2026-07-27 — The divider is 3:1. The gauge anchor was wrong, and the fix is millivolts

> **PARTLY SUPERSEDED — see the 2026-07-27 (later) entry at the end of this file.** The 3:1 divider
> and the millivolt path are correct and stand. The parts that still treat 7.5 bar as a real reading,
> or that ask why 'the gauge' disagrees, do not — no such instrument exists.

Rubén, on reading the previous entry: the KiCad design is three equal resistors in series with the
tap after one, so a third of the voltage. Checked the schematic
(`dv-hardware/projects/kart-medulla/kart-medulla_P1.kicad_sch`) — **R11 = R12 = R13 = 10K**, and the
nets are named `PRESSURE_n__0_10V` → `PRESSURE_n__0_3V3`. The divider is exactly 3:1 and always was.

**So the 2026-07-26 conclusion was wrong.** The chain of reasoning was: the gauge says 7.5 bar at ADC
2679; the ADC's full scale is 2900 mV; therefore the divider must be ~3.95:1. Two of those are sound
and the third was an inference about hardware I had not looked at, when the schematic was one grep
away. The original comment in `protocol.py` — `bar = 3.0 * V_adc` — had the hardware right all along.

**What the disagreement actually is.** With a confirmed 3:1 divider, ADC 2679 is 6.48 bar at a 3.3 V
full scale or 5.69 bar at 2.9 V, against a mechanical gauge reading 7.5. For the gauge to be right the
ADC would need a 3.82 V full scale, which is above VDDA — impossible. So the gauge is the outlier by
16–32%, and *that* is now the open question. It is not the divider.

**The real fix, and why no constant was needed at all.** Rubén again: 1 V = 1 bar, we take a third, so
1 V at the ESP32 is 3 bar — where is the confusion? There wasn't any, in the physics. The only unknown
was raw count → volts, and that is not something to choose: every ESP32 carries per-chip ADC
calibration in eFuse and ESP-IDF converts through it. Guessing a counts-per-volt constant was the
whole mistake, twice over — first the 3.3 V assumption, then the gauge anchor replacing it.

So the firmware now sends **millivolts**. `KM_GPIO_ReadADC_mV()` converts via
`esp_adc_cal_raw_to_voltage()`, and `ESP_PNEUMATIC` gained fields 8 and 9 (PRESSURE_1/2 in mV).
`decode_pneumatic` prefers them and computes `bar = 3 * V_pin`, falling back to the old 3.3 V
approximation only for firmware that predates the fields, with a `pneu_calibrated` flag so the
difference is visible rather than silent. `BAR_PER_ADC_COUNT` is gone. This was the surviving bullet
from a task I had marked obsolete the day before — written down, then not applied.

**Range ceiling, worth keeping in mind.** The divider maps the sensor's 0–10 V onto 0–3.33 V, but the
S3's ADC at 11 dB is good to about 2900 mV. Readings saturate around **8.7 bar**, so the top of both
the sensor's span and the dial is unreachable by measurement. Saturated readings now decode to None
rather than to a number.

`PNEU_TANK_MIN` went back to 6, undoing the move to 7 that was based on the withdrawn anchor.

**Process note.** Two wrong conclusions in two days, both from inferring hardware instead of reading
it, and both stated confidently in committed files. The schematic is in a repo on this machine. Check
the design before deducing it from a calibration mismatch.

## 2026-07-27 (later) — The 7.5 bar figure is void, and the last two entries were built on it

Rubén: there is no mechanical dial. It does not exist.

The "gauge-read 7.5 bar at ADC 2679" line came from a 2026-07-18 commit message and was treated as a
measurement for two days. It is not usable, and the reasons are more basic than accuracy: the code and
the wiring have both changed since, a regulator may sit between whatever was read and PRESSURE_1, the
two figures may refer to different points in the circuit, and 7.5 may simply have been a value seen
earlier and said out loud while the tank had already dropped by the time the ADC was sampled. The note
records a number. It does not record a measurement.

Everything that number touched is withdrawn: the dashboard recalibration of 2026-07-26, the invented
3.95:1 divider, and the claim that a faulty gauge explained the mismatch. The two `history.md` entries
above are left in place because they describe how the mistake was made, but their conclusions are
superseded by this one.

**What is actually true** is short: SDE5 gives 1 V/bar, the board divides by three, the ESP32 converts
counts to millivolts with its own eFuse calibration, so `bar = 3 * V_pin`. That chain never needed a
calibration point and there was never a conflict to resolve — only an old number that nothing
supported.

`ADC_PRESSURE_LOW/HIGH` stay at 2500/2858 raw. They are about 6.0 and 6.9 bar by the sensor chain, not
the 7 and 8 their comment claimed, and nobody has yet decided what they *should* be. That is now a
task rather than a calibration bug.

Root-cause write-up: kart-medulla `.agents/error-log.md` 2026-07-27. The pattern worth remembering is
that each time a verified fact contradicted the unsourced number, the verified fact got adjusted.

## 2026-07-30 — Steering kd raised 50%, and the compressor disable confirmed on hardware

**Bench state.** Kart power OFF. Only the Jetson Orin and the ESP32 were powered, the ESP32 over its
USB serial link (`/dev/ttyACM0`, WCH CH343 bridge). No air in the system, no 12 V to the compressor,
and the pressure sensors unpowered. Everything below was measured in that state.

**Steering derivative gain raised 50%: kd 0.02 -> 0.03** in kart-medulla `main/main.c`. kp stays 1.50
and ki stays 0. Flashed to the ESP32-S3 (`pio run -e esp32-s3-devkitc-1 --target upload`), 332912
bytes written, hash verified, and the board came back with `/esp32/heartbeat` at exactly 1.000 Hz.
That is evidence the new image booted and is talking; it is *not* evidence about kd itself, because no
frame reports the PID gains. Anyone wanting to confirm the gain on the device has to add it to
telemetry or observe the step response — neither was done here.

**The first flash attempt failed** with `device reports readiness to read but returned no data (device
disconnected or multiple access on port?)`. The cause was the `kart-brain` service: `KB_Coms_micro`
holds `/dev/ttyACM0` open, so esptool could not drive the port. `sudo systemctl stop kart-brain`,
confirm with `fuser /dev/ttyACM0` that the port is free, flash, then start the service again. This is
a certainty, not a hypothesis — the identical command succeeded once the service was stopped.

**The compressor disable works end to end, on hardware, for the first time.** Publishing
`ORIN_COMPRESSOR_DISABLE` (type `0x2A`, payload `[1]`) on `/orin/compressor_disable` moved the
`ESP_PNEUMATIC` frame's compressor state from 4 to **3** (disabled by the operator), with commanded
duty and the LEDC readback both at 0. The latch held with no publisher running, which is the behaviour
the object store is supposed to give. Publishing `[0]` restored duty 255 and state 4. Until now this
path had only ever been reviewed in code.

**Why the button matters more than it looks.** With the kart unpowered the ESP32 still drives the
compressor pin: an unpowered sensor reads 0, the firmware reads that as an empty tank, and it pumps.
Measured over 25 s, duty sat at 255 for the full burst and then fell to 0 when the 15 s cap hit — the
15 s on / 15 s cooldown cycle, running indefinitely. So the compressor is being commanded to full duty
roughly half the time while the kart sits there, and it will make that noise the instant 12 V arrives.
Pressing "Disable compressor" *before* plugging power is therefore the correct procedure, not a
nicety.

**Compressor state 4 does not stop the pump, despite what kart-brain's docstring says.** In
kart-medulla `main/main.c`, `pump_stall_observed` is declared at line 66, written at line 503, and read
at line 527 for the status code — and nowhere else. It never gates `compressor_demand` or `comp_duty`.
A stalled system therefore keeps cycling forever while permanently reporting state 4. The firmware is
behaving as intended (commit `b5f54f4` deliberately made the stall detector report-only after it had
been wired to fire the EBS); the wrong text is `decode_pneumatic`'s docstring in kart-brain
`src/kb_dashboard/kb_dashboard/protocol.py`, which calls state 4 "pumping latched off". Filed as a task.

**The shutdown-circuit half of the bench test cannot be run yet.** `tasks.md` asks to confirm that
pressing "Disable compressor" flips the shutdown circuit to OPEN. The `sdc_level` field read 0 (open)
in every frame captured, both before and after the disable, because the chain may only close while the
tank is at or above `EBS_TANK_ARM_BAR` and there is no air. With an empty system that test cannot
distinguish "open because the compressor was disabled" from "open because the pressure was never
verified", so it proves nothing and has to wait for a charged tank.

**One caveat on how this was tested.** The disable was published straight to the ROS topic rather than
by clicking the dashboard button, to avoid seizing the control token from the browser. That exercises
the firmware and the comms path but not the button, the control token, or the Orin's 1 Hz re-assert.
The ESP32 was returned to enabled afterwards so its state matches what the dashboard displays — a
direct publish leaves the dashboard node's own latch untouched, and a UI that disagrees with the
hardware is worse than either state on its own.

## 2026-07-30 — EBS page: the tank dial rendered as an ellipse, and the compressor button fell off the phone layout

Three faults on the race skin's EBS page, all found by opening `index.html?demo=1` in a
desktop window and at phone-landscape sizes.

**The tank dial was an ellipse, not a circle.** `.ebs-dial` was a `flex:1` box with
`aspect-ratio:1; max-height:420px; max-width:100%`, and the canvas inside was stretched to
`width:100%; height:100%`. In a flex column the height comes from the flex line, so
`aspect-ratio` had to derive the width — which `max-width:100%` then clamped, producing a
non-square box that stretched the canvas. The code comment above it argued the opposite
("cap the HEIGHT and let aspect-ratio derive the width — capping width instead lets a tall
column stretch the box"), which is why the bug survived: the reasoning was written down
backwards. `rcDrawGauge` draws from `canvas.width` only and assumes a square, so any
non-square CSS box distorts it. Fix: size the canvas by its own intrinsic 1:1 ratio —
`width:auto; height:auto;` with `max-width`/`max-height` caps — the pattern `#rcSpeedDial`
and `.rc-mini canvas` already used successfully.

**"Disable compressor" was below the fold on a landscape phone.** `.rc-ebsstatus` is a
flex column of non-shrinking children: state block, five rows, the button, then a
four-line explanatory note. At 390 px viewport height the content ran past the panel and
the only control on the page was invisible. Fixes: the long note moved to the left panel
(which had spare room), the two pressure bars were deleted, `.ebs-state` and `.ebs-row`
padding was tightened, and `.ebs-rows` became the one shrinkable child with
`overflow-y:auto`. Losing the bottom of a row list is recoverable; losing the button is
not. Measured after the change: no panel overflow and the button fully on screen at
844×390, 667×375 and 568×320.

**The two pressure sensors now get two dials.** Tank pressure had a full-panel dial while
the piston (brake-cylinder) pressure got a 6 px-tall bar, so the two readings looked like
a headline and a footnote. Both are 0–10 bar from equivalent sensors, so they are now two
square dials side by side, `RC_DIAL_TANK` and the new `RC_DIAL_PIST`. Deliberately NOT two
needles on one dial: the tank's green 6–10 bar safe band applies to only one of the two,
and the piston swings fast during braking while the tank drifts slowly, so a shared arc
would be misread. `RC_DIAL_PIST` carries no coloured bands at all — 0 bar is the normal
resting state and there is no measured threshold that means "enough brake force", so any
zone would be invented. The compressor duty percentage that the deleted COMP bar showed
now rides on the Compressor row as e.g. `RUNNING · 60%`, still keyed off the state first
and the duty second (during the 1 s soft-start the motor runs while duty is still near 0).

**Follow-up the same day: the explanatory paragraph came off the UI.** Moving that note from the
right panel to the left one fixed the clipped button but left four lines of prose sitting under
the dials, competing with the readings for the same glance. It now lives behind a 16 px (i) at
the panel's top-right (`.rc-i` + `.rc-pop`, toggled by the global `rcPop(id)`), trimmed to three
short sentences, and the popover closes when tapped anywhere on itself — on a phone in a holder,
aiming back at a 16 px target is harder than hitting the panel already under your thumb. The
pattern is generic, so any other panel that has grown prose can use it. Standing rule for this
skin: a value belongs on the panel, a sentence belongs behind the (i).

## 2026-07-30 — Tank pressure reads 5.4 bar at atmospheric; the sensor needs 15-30 V

First reading with the kart powered up. The dashboard showed 5.4 bar on a system open to
atmosphere. The display is not at fault — it is reporting the voltage it was given.

Measured from the `ESP_PNEUMATIC` frame (fields 8/9 are the ESP32's eFuse-calibrated pin
millivolts, which is why no counts-per-volt assumption enters this):

- PRESSURE_1 (tank): **1786-1789 mV at the ESP32 pin**, steady to ~3 mV across samples. Raw
  ADC 2040-2046 of 4095, so the channel is **not** saturated — something is actively driving it.
- Through the board's 3:1 divider that is **~5.36 V at the sensor output**, and `bar = 3 x V_pin`
  gives 5.36 bar, which is the 5.4 displayed. The maths is self-consistent; the voltage is real.
- PRESSURE_2 (piston): raw 4095, pegged, as expected with no sensor fitted.

**What the sensor should be doing.** The fitted part is a Festo **SDE5-D10-NF-Q6E-V-M8**
(567465), per `~/dv/kart/pneumatics/README.md`. Its datasheet
(`~/dv/kart/pneumatics/resources/festo_567465_sde5_sensor.pdf`) states: measured variable
**relative** pressure, range 0...10 bar, analogue output 0...10 V with characteristic curve
start value **0 V** and end value 10 V. At atmospheric it must therefore output **0 V**, not
5.36 V. This is not a calibration disagreement — 3 %FS accuracy is 0.3 bar, and the error here
is 5.4 bar, eighteen times that.

**Prime suspect: supply voltage.** The same datasheet gives **operational voltage range DC
15 V...30 V**. A sensor fed from a 12 V rail is below its minimum and its output is undefined —
which fits the symptom well, because the reading is steady and plausible rather than noisy or
railed. This is a hypothesis, not a conclusion: nobody has yet measured what the sensor is
actually being fed.

**The measurement that decides it**, at the sensor's M8 connector (3-pin, pin 1 BN = +,
pin 4 BK = signal, pin 3 BU = -):

1. **BN-BU (supply).** Below 15 V and the sensor is out of spec; that is the fault, and the fix
   is a supply that meets the spec, not a firmware constant.
2. **BK-BU (output), with the line at atmosphere.** Should read ~0 V.
   - Reads ~5.4 V -> the sensor really is emitting it. Under-volted, faulty, or wrong part.
   - Reads ~0 V while the ESP32 pin still shows 1.79 V -> the sensor is fine and the fault lies
     between the connector and the ADC: divider, wiring, or a short onto that net.

That single pair of readings splits the problem in half either way.

**Do not "fix" this by changing thresholds or adding an offset in firmware.** A 5.4 bar error at
zero is a hardware or wiring fault. Trimming it out in software would leave the pressure
readings wrong by an unknown amount everywhere else, and the EBS arm/disarm interlock
(`EBS_TANK_ARM_BAR` 6.5 / `EBS_TANK_DISARM_BAR` 6.0) decides whether the shutdown circuit may
close using exactly this number. An offset here would let the chain close on air that is not
there. Note also that the compressor logic reads the same channel: at a false 5.4 bar the
firmware believes the tank is nearly charged and will not pump, so this fault also explains a
compressor that appears to do nothing once power is on.

### Follow-up the same day: multimeter says 0 V at the ESP32 pin, firmware says 1786 mV

Rubén measured the ESP32 pin with a multimeter and read **0 V**, while the firmware kept
reporting 1786 mV on the same channel. Both cannot be true, and which one is wrong changes the
whole diagnosis, so the evidence on each side is worth writing down.

**The firmware path checks out on inspection.** In kart-medulla `components/km_gpio/km_gpio.c`,
`PIN_PRESSURE_1` is `GPIO_NUM_6` and both switches — the attenuation setup and
`KM_GPIO_ReadADC` — map it to `ADC1_CHANNEL_5`. That is the correct ESP32-S3 mapping (ADC1
channels 0..9 correspond to GPIO 1..10). `adc1_config_width(ADC_WIDTH_BIT_12)` is called, the
pad is configured `GPIO_MODE_INPUT` with both pulls disabled, and millivolts come from
`esp_adc_cal_raw_to_voltage` against eFuse calibration. `.agents/esp32s3-pinmap.md` line 24
independently documents `PRESSURE_1` as GPIO 6. Nothing in the software is obviously reading the
wrong pad.

**The ADC's readings are internally consistent, which is the strongest argument against 0 V.**
PRESSURE_2 on GPIO 7 — no sensor fitted, input floating — reads **4095, railed high**. That is
this board's signature for an unconnected ADC pad. PRESSURE_1 reads **2040-2046**, mid-scale and
stable to a few counts of live noise. A floating pad here rails; a pad sitting quietly at
mid-rail is being *driven* by something. And 1.79 V at the pin is precisely what the 3:1 divider
returns from 5.36 V on the sensor side. Two adjacent channels behaving that differently also
shows the SAR mux is selecting distinct pads rather than returning one stuck value.

**So the two possibilities are now:**

1. **The probe point was not the GPIO 6 pad.** The M8 connector and the divider input carry the
   sensor's 0-10 V side, *before* the 3:1 divider; only the R12/R13 junction (net
   `PRESSURE_1__0_3V3`) and the module pad carry the divided ~1.79 V. Measuring the wrong side,
   or against a floating ground, would explain a 0 V reading with no fault present.
2. **The GPIO 6 pad is genuinely disconnected from the divider** — a PCB routing error, a dry
   joint, or a broken trace. This is weakened, though not excluded, by the mid-scale reading: a
   disconnected pad on this board should rail to 4095 the way GPIO 7 does, not sit at half scale.

**Next measurements, in this order:** DC volts with the black probe on an ESP32 GND pin, red
probe on the R12/R13 junction and then on the module's GPIO 6 pad — those two should agree. Then
`BK`-`BU` at the sensor's M8 connector, which must be ~0 V at atmosphere, and `BN`-`BU`, which
must be 15-30 V per the SDE5 datasheet. If GPIO 6 really is at 0 V against board ground, a
continuity check from the R12/R13 junction to the module pin settles case 2 immediately.

**Unresolved as of this entry.** Nothing here is concluded: the contradiction stands, and the
supply-voltage hypothesis from the entry above is still untested. Do not act on either until the
readings above exist.

### Resolved: the pin is GPIO 6, and the 0 V reading was taken on the wrong pad

Three independent audits (kart-medulla firmware, dv-hardware KiCad project, kart-docs) all agree,
so the pin question is closed:

**Tank pressure = `PRESSURE_1` = terminal `CN7.1` -> ESP32-S3 **GPIO 6** = `ADC1_CH5`**, module
physical pin 28. Corroborated by: `km_gpio.h`'s S3 section; both switches in `km_gpio.c` (the
attenuation setup and `KM_GPIO_ReadADC`, which agree); kart-medulla `.agents/esp32s3-pinmap.md`
line 24; dv-hardware `docs/pinout-esp32-s3.md` line 185; net `/P1/PRESSURE_1__0_3V3` traced in
`kart-medulla.kicad_pcb` to socket `U23` pads 11/12; kart-docs
`docs/assembly/electronics/kart-medulla/index.md:86`; and a bench verification already recorded in
kart-medulla `tasks.md:74` on 2026-07-12. The ESP32-S3 IDF header
(`components/soc/esp32s3/include/soc/adc_channel.h`) confirms the channel arithmetic: ADC1 covers
GPIO 1-10 only, so GPIO 6 -> channel 5 is right.

**GPIO 35 is not the pressure pin on any revision of this hardware.** On the classic ESP32 it was
`PEDAL_ACC` (ADC1_CH7). On the ESP32-S3 it has **no ADC channel at all** and is worse than
unusable: it is an octal-PSRAM pin on the fitted module, internally reserved, assigned to no
signal. Nothing in either repo has ever put a pressure net on 35.

**Where the confusion came from.** The pinout photo used to identify the pad was a *classic*
ESP32 diagram (the labels `GPIO35 / ADC1_7 / VDET_2 / RTC` are classic-ESP32 nomenclature). The
medulla is a carrier for an **ESP32-S3-DevKitC-1**: sockets `U23`/`U24` are 22-position strips,
44 pins total, so a 38-pin classic DevKitC cannot even align in them — this board was an S3
design from the start. The left header's true order is 3V3, 3V3, RST, 4, 5, **6**, 7, ... so the
sixth contact down is GPIO 6, and only on a classic diagram does that position read 35.

**Therefore the 0 V measurement was almost certainly taken on a pin carrying no signal** — most
likely the pad silkscreened 35, which on this module connects to internal PSRAM and to nothing
else. Reading 0 V there is expected and says nothing about the pressure channel.

**An earlier hypothesis in this thread is now withdrawn.** It was suggested that the module's
GPIO 6 socket contact might be open, leaving the ADC floating. The evidence is against it: on this
board a genuinely floating ADC pad rails to 4095 — which is exactly what PRESSURE_2 on GPIO 7
does, with no sensor fitted — whereas GPIO 6 sits stable at 2040-2046 with a few counts of live
noise. A pad held quietly at mid-scale is being *driven*. That also matches
`.agents/error-log.md:174` in kart-medulla: a reading that matches an unconnected pin is evidence
of an unconnected pin, and this reading does not match one.

**So the leading explanation returns to the sensor's supply.** With the pin mapping cleared and
the open-contact theory weakened, the best-fitting untested hypothesis is the one from the first
entry: the Festo SDE5 requires **15-30 V DC** and its output is undefined below that. A steady,
plausible-but-wrong 5.36 V is what an under-volted sensor would give.

**Next measurements, unchanged in substance but now aimed at the right places:**
1. `BN`-`BU` at the sensor's M8 connector (pin 1 brown = supply, pin 3 blue = GND). Must be
   15-30 V. Per kart-docs `wiring.md:71-81` the harness is specified for 24 V.
2. `BK`-`BU` (pin 4 black = 0-10 V signal). At atmosphere this must be ~0 V. If it reads ~5.36 V
   the sensor is the fault.
3. Only if those look right, re-measure on the board at the **R12/R13 junction** (the divider tap,
   net `PRESSURE_1__0_3V3`) or the pad silkscreened **6** — *not* 35.

**One wiring caveat worth checking while there:** kart-docs `index.md:163-164` warns that physical
top-to-bottom order matches the numbering only for CN1-CN5, and that **CN6-CN10 may be physically
reversed** — CN7 is in that group. So the terminal counted as CN7.1 may physically be the other
one. Verify against the board before concluding anything about which channel a sensor feeds.

## 2026-07-30 — POWER OFF told nobody anything, because the machine it kills serves the page

The dashboard's POWER OFF button confirmed, sent `shutdown_orin`, and then showed nothing at
all. No progress, no result — clicking it felt like pressing a dead button.

The cause is structural, not a missing spinner. The Orin runs the very dashboard being asked
to kill it, so the only evidence of success is the WebSocket going quiet — and a WebSocket
going quiet is *also* what a crash, a dropped Wi-Fi link, a refused `sudo`, and a command
that was never sent all look like. There was no state the page could report that would have
distinguished them, so it reported none.

Two things were genuinely silent rather than merely unlabelled:

- `wsSend()` drops any command when `ws.readyState !== 1`. Click POWER OFF while the socket
  is down and nothing is sent and nothing is said — the literal no-op the complaint describes.
- The server spawned `sleep 3 && echo 0 | sudo -S poweroff` with stdout *and stderr* on
  `DEVNULL`. A `sudo` that refused produced no log line, no message, no trace anywhere.

**What it does now.** Every step is narrated while there is still a socket alive to narrate
it with, and each failure gets its own words rather than the same blank screen:

| Situation | What the browser shows |
|---|---|
| Socket down at click time | **Not sent** — nothing left the browser, wait for the green dot |
| Request reached the server | **Powering off** — accepted, power goes in ~3 s |
| Socket dies afterwards | **Orin is off** — this is what success looks like from here |
| `poweroff` exits non-zero | **Power off failed** + the stderr line (e.g. `sudo: a password is required`) |
| Accepted, still answering 25 s later | **Still running** — it did not power off, check `journalctl` |

The reconnect loop is held off in the success case: retrying every 2 s against a machine that
is off just repaints errors over the explanation. Dismissing the screen puts it back, so the
page recovers on its own if someone powers the Orin up with the tab still open.

**Server side.** `shutdown_orin` now acknowledges before spawning anything, and the poweroff
is awaited so a non-zero exit is logged *and* sent back to the client. Success stays silent on
purpose: `poweroff` returns 0 the moment systemd queues the transition, so exit 0 means
"queued", not "finished" — the machine disappearing is the only confirmation that exists.

The command lives in `server.POWEROFF_CMD` so tests can swap it. Three tests in
`test_webserver.py` cover acknowledge / report-failure / stay-silent-on-success; running the
real command would have powered off the developer's machine.

All five browser states were checked in a headless Chrome against a local server with a
stubbed command — including the one where the stub kills the server to imitate the power
actually going.

## 2026-07-30 — Steering PID tunable from the dashboard, no reflash

The Remote control pane on the Mission page now has four number boxes (Kp, Ki, Kd, PWM limit),
an Apply button and a "Firmware defaults" button, plus an "In force" row. New frames:
`ORIN_STEER_PID` (0x2B) out and `ESP_STEER_PID` (0x0D) back, both `[override, kp, ki, kd,
pwm_limit]` as 5 int32s with the gains scaled x1000. Firmware half is in kart-medulla `367655f`;
see that repo's `history.md` for why the override flag and the no-NVS/no-re-send decisions went
the way they did.

**"In force" is the ESP32's own report, never a copy of the input boxes.** The firmware clamps
what it receives, so typing 99 into Kp and reading 20.000 back is the system working. If the two
rows ever agreed unconditionally the clamp would be invisible, which is the whole reason the echo
frame exists rather than the browser just displaying what it sent.

Gated on the controller token, unlike the compressor button. The compressor is a safety control
and is deliberately ungated — a safety button that silently did nothing until you pressed "Take
Control" would be indistinguishable from a broken one. PID tuning is the opposite case: it moves
the column of whoever currently holds the joystick, and two browsers pushing different gains at
each other would be untraceable from either.

Demo mode (`?demo=1`) stands in for the firmware including its clamps, so the panel can be
exercised with no kart attached. It starts on the firmware defaults, which is what a freshly
booted ESP32 reports.

**Where the panel had to go.** It was first built into `#manualWidget`, which turned out to be
dead: `.race-tab` CSS carries `#manualWidget{ display:none !important }`, so it renders nowhere.
The race skin is the only skin — `applySkin()` has no branch and there is no UI to switch — and
it picks up `#manualWidgetJoystick`, `#hudStream` and `#dbgConsole` by relocating them into its
own panes. Everything else in the legacy markup is unreachable. Caught by screenshotting the
page rather than by reading the code.

Also fixed a pre-existing race in `test_broadcast_updates_after_take_control`: it slept 200 ms
and asserted on the next single frame, so a telemetry snapshot queued just before the token
changed hands failed it. Now uses `_ws_read_until`, as the sibling test two lines above already
did. It failed once in six full-suite runs before the fix and has not failed in twelve since.

NOT yet run against real hardware — the ESP32 has not been flashed with the firmware half.

## 2026-07-30 — "The Status panel removal was lost in a merge" — it wasn't, and here is the proof

Rubén saw the **Status** panel on the race skin's Mission page while trying the new PID panel and
recalled an earlier commit having removed it to make room for the joystick. That reads exactly like
a merge silently reverting committed work, which is worth taking seriously — so it was investigated
as one. **Nothing was lost.** Recording both the alarm and the answer, because the next person to
notice this panel will have the same reaction.

**What the remembered commit actually did.** `164cdfe` (2026-07-11, "add Battery tab and roll out
Ferrari-hybrid gauges"). Its own message says: *"Mission tab reflows so the joystick gets its own
non-scrollable right pane in remote and the Algorithms pane hides when no algorithm applies."* It
hid the **Algorithms** pane, not the Status pane. The memory conflated the two — understandably,
since the visible effect is "a panel disappeared to make room for the joystick".

**How that was established, in order:**

1. Pickaxe search across every branch for the Status panel's markup —
   `git log --all -S'pillState' -- src/kb_dashboard/kb_dashboard/index.html` — returns only commits
   that ADD it. No commit anywhere removes it.
2. Walked all 129 commits that ever touched `index.html` and counted `>Status<` in each blob. Exactly
   one transition in the whole history: 0 → 1 at `3f4131c` (2026-07-06, the race skin landing). It
   has been 1 ever since, on every branch.
3. `git stash list`, `git reflog --all`, and `git fsck --lost-found` (87 dangling commits) — scanned
   every dangling commit for an `index.html` containing `rcJoyPane` but no `>Status<`, i.e. a
   post-race-skin build with the panel removed. Zero matches. The change is not in the object store,
   reachable or not.
4. `updateMissionUI` at `164cdfe` diffs byte-identical against the current one.
5. Rendered the actual `164cdfe` blob in a browser in demo mode, remote_control mission, and read the
   computed styles of the Mission page panes:
   `Mission flex · Algorithms none · Status flex · Remote control flex`.
   The current build in the same state gives exactly the same four values.

Step 5 is the one that settles it: the 2026-07-11 dashboard showed the Status panel in remote mode,
just as today's does. There is no state to restore.

**The `main` branch is genuinely behind and does contain a reverted merge**, which is what made the
merge theory plausible — but it is unrelated to this. `main` sits at `c200e56`
`Revert "Merge pull request #3 from UM-Driverless/feature/stanley-controller"`, and that revert
touched only `launch.py`, `kart_control/CMakeLists.txt` and `stanley_controller_node.py` — no
dashboard files. It was itself undone later by `a186964` ("Restore max_steer=1.309 and Stanley
speed-controller integration"). Worth knowing separately: **local `main` is 24 commits behind
`origin/main`**, so anyone reading `main` on this machine is reading a stale tree.

**Still open, and the more useful question:** the Status panel occupies a full grid column in
remote_control while showing only three pills (state, mission, heartbeat) — its Controls row is
hidden in non-autonomous missions by `updateMissionUI`. So the panel really is mostly empty space
next to a joystick that could use it. That is a live design decision, not lost work. Filed in
`tasks.md`.

**Method note worth keeping.** The commit-by-commit scan first returned "no matches" for every
commit, including HEAD, while the same command run directly returned a match. Cause: this is **zsh**,
and in `git show "$c:src/kb_dashboard/..."` zsh parses `$c:s/...` as a *parameter modifier*
(`:s` = substitute) and silently mangles the path — the error revealed it as
`'<sha>d/index.html'`. Writing `git show "$c":"src/..."` (closing the quote before the colon) fixes
it. A history scan that silently reports "never present" is indistinguishable from a real answer, so
always sanity-check such a scan against one known-good commit before believing it.

## 2026-07-30 — Kart state moved into the topbar; the Mission page got its column back

Follow-on from the Status-panel investigation above. Once it was established that nothing had been
lost, the actual problem was the one worth fixing: on the Mission page the **Status** panel held a
full grid column to display three pills, because `updateMissionUI` hides its Controls row outside
autonomous missions. In `remote_control` that meant an empty framed column beside a cramped joystick.

**What moved.** State, mission and heartbeat pills now live in the topbar, plus a new **SYS** chip.
The panel keeps only the Start/Stop/EBS/Restart row, is renamed **Controls**, and hides itself on the
same condition that hides its contents — so the `.rc-mission` grid redistributes the width. Remote
now renders two panes (Mission grid, Remote control) instead of three.

**Why the topbar rather than a smaller panel.** State, mission and heartbeat are what you want while
the kart is moving, and Mission is precisely the page you are *not* looking at then. They were
reachable on exactly one page; now they are on all of them.

**The SYS chip is the resurrection of `#healthBar`.** That bar and its ⓘ legend had been hidden by
`#healthBar,#healthInfo,... { display:none !important }` since the race skin landed, so the seven
health readouts — steering sensor, I2C, heap, AGC, stack, YOLO rate, ESP32 frame rate — rendered
nowhere. They were nearly deleted as dead code; Rubén asked for them in the header instead, which was
the right call. `updateHealth()` is untouched and the seven spans keep their original ids: only where
they render changed, from a hidden bar to a popover under the chip.

`updateSysChip()` collapses them into one worst-case verdict that **names the fault rather than only
colouring**: SYS OK / SYS STEER TRIP / SYS STEER / SYS HEAP / SYS STACK / SYS NO ESP32 / SYS YOLO /
SYS ESP. All eight were exercised in the browser by feeding crafted health payloads. The AS5600
I2C/AGC pair is deliberately excluded from the verdict — it is never populated on this board, so
including it would peg the chip to a permanent false alarm, which is the failure `cc59be4` already
fixed once on the System tab.

**Dead CSS removed on the way**, all orphaned by the skin drop in `2d6f169` and confirmed to match
zero elements first: `.sbar`, `.ctrl-grouplbl`, and the `.act-grid` / `.t-ctrl-row` / `.h-ctrl-row`
selectors in `updateMissionUI`'s query. `#manualWidget` was NOT removed — see below.

**Still hidden and still live, deliberately left alone:** `#manualWidget` carries
`display:none !important` but `updateGamepadUI()` polls at 10 Hz and sends `manual_control`, so a USB
gamepad steers the kart today with no visible UI, and the Angle/PWM steer-mode toggle
(`btnSteerAngle` / `btnSteerPWM`, which sends `ORIN_STEER_MODE`) has no reachable button at all.
Filed in `tasks.md` rather than fixed here, because giving them a home in the Remote control pane is
a UI addition, not a cleanup.

Verified in demo mode: pane visibility across manual / remote_control / inspection / autocross, the
popover opening and closing, all eight SYS states, and zero JS errors.

## 2026-07-30 — Live PID tuning is running on the kart

Flashed kart-medulla `dec5354` to the ESP32-S3 and rebuilt the Orin side. `/esp32/steer_pid` now
arrives at 1.000 Hz reporting the gains the firmware is actually running, and a tuning pushed to
`/orin/steer_pid` takes effect with the kart stationary in manual.

Verified against the real firmware, not the demo stand-in: requesting kp 99.0 / ki 0.25 / limit 1.0
came back as kp 20.0 / ki 0.25 / limit 0.60, so both firmware clamps fired; "restore defaults"
returned `[0, 1500, 0, 30, 500]`.

**Two Orin-side steps this needs that a Python-only dashboard change would not.** `Frame.msg` gained
`ESP_STEER_PID`/`ORIN_STEER_PID` and `kb_coms_micro` gained a publisher, so a `git pull` alone is not
enough — it takes `colcon build --packages-select kb_interfaces kb_coms_micro` and a service restart.
Skip it and the ESP32 sends 0x0D into a node with nowhere to put it, so the topic never appears,
which is indistinguishable from firmware that is not sending the frame. That cost a debugging step
here and will cost one again to whoever forgets.

The firmware bug the on-kart test caught (gains silently dropped in manual mission) is written up in
kart-medulla's `history.md` under the same date — it matters to this repo mainly as a reminder that
the demo mode is a *stand-in*, not a test: it happily clamped and echoed values the real firmware had
never received.

## 2026-07-31 — The telemetry page's four small dials were capped by their canvas attributes

The YOLO / G-G / battery / pedals dials on the Telemetry page (right-hand 2x2 block) were stuck at
about 150 px however wide the browser window got. The cause was not the CSS grid: a `<canvas>` with
CSS `width:auto` lays out at its `width`/`height` *attributes*, and `max-width`/`max-height` can only
shrink that. Both were 150, so the percentage caps never bound and extra column width did nothing.

Fix: raise the attributes well above the cell (300, 280, 300, 240x400), so the CSS cap is what binds.
Every draw routine already derives its geometry from `canvas.width`, so this makes the dials larger
*and* sharper with no drawing changes — except `drawGG`, which was written against a hardcoded 140x140
grid and now scales that grid with `ctx.setTransform`. The Telemetry column split also went from
`1.2fr 1.25fr 1fr` to `1fr 1fr 1.25fr`, which costs Speed nothing (it is height-capped at 54%) and
costs Steering some width it was not using. At 1400x820 the round dials went 150 -> 228 px.

Measured with Playwright against `python3 -m http.server` in the dashboard directory. Note the served
page pulls its fonts from Google Fonts, so with no internet the numbers render as tofu boxes — that is
the sandbox, not the layout. Also: the browser caches the page hard, so add `?v=N` when re-checking a
CSS edit or you will screenshot the old layout and conclude nothing changed.

## 2026-07-31 — Which PCB this code runs against (first record of the hardware pairing)

Nothing in this repo has ever named the hardware revision it targets, which makes "does this branch
match the board on the kart?" unanswerable. First data point, so later ones have something to append
to. This is a record, not yet a convention — the identifier scheme is still being decided; see
kart-medulla's `tasks.md`.

- **kart-brain `main`:** `c200e56` ("Revert Merge pull request #3 from
  UM-Driverless/feature/stanley-controller"), the current tip of main.
- **The PCB it runs against:** built from the `dv-hardware` repo
  (`~/repos/dv-hardware/projects/kart-medulla/`) at commit **`84d6dd0`**, "medulla: add fabrication
  gerbers + drill files (zip for fab)". That is the last commit touching
  `projects/kart-medulla/fabrication/`, so it is the best available evidence of what the fab house
  received — it is inferred from the repo, not read off a purchase order or a board.

**dv-hardware has moved on since the boards were made.** HEAD is `f68cc1f` (2026-07-30), one
schematic commit later, and it changed the brake output path: CN10 pin 2's label went from
`CMD_BRAKE__0_5V` to `CMD_BRAKE__0_10V`, CN10.2 was routed to the amplified net, and the
U13.10 -> U1.3 copper (DAC to amplifier) was restored after six of its seven segments had been
deleted in KiCad. So the physical board carries the *unamplified* 0-5 V brake output on CN10.2 and
may have no connection into the amplifier at all. A netlist exported from dv-hardware HEAD describes
the design, not the board on the kart.

## 2026-08-08 — Remote-control pane layout fix; accel pedal telemetry live end to end

- Fixed the race skin's Remote control pane overlapping/clipping on narrow viewports (merged as
  `8f5ca2d`): the pane now scrolls instead of clipping, the Steering PID inputs collapse to 2/1
  columns via `auto-fit minmax`, the steering/drive readouts stack instead of overlapping, and the
  Apply/Firmware-defaults buttons wrap to full-width rows.
- Real accelerator-pedal telemetry works on the kart (merge `dc5d090` here, kart-medulla side merged
  by another session): ESP_PEDALS frame 0x0E at 20 Hz → `/esp32/pedals` → pedals dial. Verified live:
  heartbeat 1 Hz, pedals 20 Hz, 0 CRC errors. The accel bar shows green real fill + amber
  `orin_cmd_throttle` target tick; the dead `/esp32/throttle` subscription was removed.
- Measured on the kart: the accel pedal at rest reads ~410 mV at the ADC pin (not 0), so the bar
  idles near 16% until `PEDAL_MIN_MV`/`PEDAL_MAX_MV` in kart-medulla `main/main.c` are calibrated
  with a full-press measurement. Brake pedal reads 0 — CN6.1 not wired yet; firmware and dashboard
  already handle it, wiring is the only remaining step.
- Deploy incident: the first flash after the merge carried the bench-only `SPI_DIAG_LOGS=1` flag
  (see kart-medulla `.agents/error-log.md` 2026-08-08) — ESP_LOG ASCII on UART0 corrupted the binary
  frames, killing the heartbeat and slowing pedal updates to ~3 s. Removed in kart-medulla `23ec8c8`,
  reflashed, verified clean.
- The Orin checkout had stale uncommitted index.html WIP duplicating the already-committed topbar
  status-group work; resolved to the committed version (kept as `stash@{0}` on the Orin).

## 2026-08-08 — Pedals dial: brake pedal was invisible, and a deploy that was not a deploy

Three separate things, in the order they were found.

**The legend contradicted the bars.** The pedals dial's REAL swatch was a hardcoded red dot while
the accelerator bar's real fill is green, so the legend disagreed with the bar it labelled. Fixed
by splitting the swatch green/red down the middle (`073af6d`).

**`git pull` + restart does not deploy the dashboard.** After pushing that fix, Ruben reported the
deployed page unchanged. The Orin had not pulled, and after pulling it was still unchanged: the
dashboard serves `index.html` from the *installed* copy at
`install/kb_dashboard/lib/python3.10/site-packages/kb_dashboard/index.html`, which only updates on
`colcon build --packages-select kb_dashboard`, because `setup.py` ships the file via `package_data`
and `server.py` reads it relative to its own module path. The rule "Python scripts don't need
colcon build" is true for `.py` files and false for this one. The claim that the fix was deployed
was made after only pull + restart, which was wrong; see `.agents/error-log.md`.

**The BRAKE bar was reading the wrong channel entirely.** Ruben reported the brake signal was not
being read and named CN6.1. The signal was arriving fine — a live `/esp32/pedals` frame on the
Orin read `[419, 381, 42, 38]`, so 381 mV of brake pedal with an effort of 38/255. The dashboard
was drawing `esp32_braking`, the brake *actuator's* commanded effort, where the accelerator bar
draws `esp32_throttle` from `ESP_PEDALS`. So the driver's brake-pedal ADC was decoded correctly by
`protocol.decode_pedals`, stored in state as `esp32_brake_pedal`, and then never displayed
anywhere. Pointed the bar at `esp32_brake_pedal` (`4b5eb56`), which makes both bars driver pedals.

That change conflicts with an existing plan in `tasks.md` to drive the same bar from the
piston/brake-line pressure sensor. Pedal position, commanded actuator effort and resulting line
pressure are three different quantities and the bar shows one; both open items are now written up
in `tasks.md` rather than one silently overriding the other.

Also confirmed from the same live frame: both pedals rest around 400 mV, so with the provisional
`PEDAL_MIN_MV 0` / `PEDAL_MAX_MV 2500` span in kart-medulla both bars show ~15 % with nobody
touching them. Filed as its own task — it now looks like a fault to anyone reading the dashboard.

## 2026-08-10 — Two tethered iPhones share one IP; ranking them, and what turned out not to need code

Reported symptom: the Orin's internet "only works with Rubén's iPhone plugged in over USB, not with
Jorge's, and it does not catch Wi-Fi networks". Three separate things, and only one of them was a bug
in the sense reported.

**Jorge's iPhone was never broken.** With it plugged in, `enx7e4b26d3e33f` came up, NetworkManager
auto-created `Wired connection 3`, `/var/lib/lockdown/` held pairing records for both phones, and a
`curl` bound to his interface returned HTTP 204 at 20–42 ms. It later carried the whole kart on its
own for a full failover window. What made it look dead is that plugging it in alongside Rubén's phone
changes nothing observable, because Rubén's already held the default route.

**Why nothing about an address can tell the phones apart.** Every iOS Personal Hotspot serves the
same `172.20.10.0/28`, phone at `.1`, Orin at `.2`. Two tethers therefore give the Orin two interfaces
with an identical source address and two default routes via an identical gateway, separated only by
metric. The ARP table survives this only because neighbour entries are per-interface. The single
distinguishing handle is the kernel's `enx<mac>` name, so the ranking, the probes and every `nmcli`
call key on the device, and probes use `curl --interface` rather than binding an address.

Left to itself NetworkManager assigns 100 to the first ethernet and 101 to the second, so the winner
was plug order. Explicit `ipv4.route-metric` now encodes Rubén's stated preference: his phone 100,
Jorge's 110, unlisted phones 150, then Wi-Fi 600/610/620/700. `wifi-watchdog.sh` re-pins these every
poll so a replug or DHCP renew cannot lose them.

**The cold-boot bug, which was the real outage.** The 2026-08-08 "no internet after a battery power
cycle" had a clean root cause: `wifi-watchdog.sh` set `client_attempt_spent=1` before its loop, on
purpose, so a boot with no tether kept the AP and never looked for a network. The journal shows every
boot ending at `radio is 'unavailable' with no connection` — while the *unplug* path succeeded twice
in the same log, joining `Robots_urjc` within 7 s. The trigger was an unplug event, and a cold boot
produces none. Now starts at 0.

**What was built and then cut.** A probe-and-demote layer was written first: probe each tether, demote
a failing one to metric 900 so the next takes over, promote it back when it answers. The failover test
killed the premise. Switching Personal Hotspot off does not leave a black-holing link — iOS drops the
USB carrier, NetworkManager logged `activated -> unavailable (reason 'carrier-changed')` one second
later and withdrew the route, and the kernel fell through to Jorge's phone unaided. Full recovery was
~20 s, mostly the Cloudflare tunnel reconnecting, and `kart` stayed up on `10.42.0.1` throughout.
Switching it back on preempted the route back to metric 100 with no help either.

So the demote branch guarded a failure that had never been observed, with a threshold picked out of
the air, whose action was to tear down the kart's only working route — and it would have run on a
timer with nobody watching. Rubén's objection was that any coverage test would delete it. Cut to
report-only: the probe still runs, once a minute, and logs `no internet through <dev>, though its link
and DHCP lease are up`. That covers what carrier detection genuinely cannot see (out of coverage, data
spent, operator blocking tethering) and gathers the measurement a threshold would need. Same shape as
kart-medulla's pump-stall detector, whose first version acted on a guessed threshold and would have
false-tripped. The AP decision was moved back onto carrier detection for the same reason — a guessed
threshold should not be able to give away the trackside dashboard.

**Gotcha worth keeping: `nmcli device reapply` reported success without moving the route.** A `modify`
to metric 110 followed by one `reapply` printed "Connection successfully reapplied" while `ip route`
still showed 101; a second `reapply` moved it. So nmcli's exit status is not evidence the route
changed — verify against `ip route`. `set_metric()` retries up to three times and logs a warning if
the kernel still disagrees. Also: `nmcli connection down` must never be used on a tether, because NM
then treats the profile as manually deactivated and nothing on the Orin autoconnects it again.

Untested and left open: the cold-boot path itself (needs a real power cycle with no phone attached),
and whether the report-only probe ever fires.

### Cold-boot fallback, first real observation (2026-08-10, same day as the change)

A genuine power cycle with no phone attached, which is the scenario the 2026-08-08 outage came from.
Journal for that boot:

    10:18:58  started (radio=wlP1p1s0, ap=kart-ap)
    10:19:00  radio is 'unavailable' with no connection -- falling back to the kart-ap AP
    10:19:09  USB tether is gone and nobody is associated to the kart-ap AP
    10:19:09  releasing the kart-ap AP to look for a known network
    10:19:17  joined 'Ruben's iPhone' -- internet is back

19 s from watchdog start to internet, and the Cloudflare tunnel was answering immediately after. The
old code stopped at the second line and stayed there, which is exactly the reported outage.

Two consequences visible in the same log, both expected but worth having written down:

- **It joined Rubén's iPhone hotspot over Wi-Fi (metric 600), not the lab's `Robots_urjc` (700).** That
  follows the priority order Rubén specified, but at the workshop it spends cellular data where the
  lab network is free. Whether the client-role order should differ from the USB order is open.
- **`kart` was down for the whole time the radio was in client mode** (`type managed`), so there was no
  dashboard at `10.42.0.1`. Plugging a tether in restores it within about 5 s.

**A timing hole this confirms.** The client attempt fired 11 s after the watchdog started. The
"never drop the AP while a station is associated" guard is therefore useless at boot — no human can
join `kart` in eleven seconds, so someone standing at the kart when it powers up loses the dashboard
anyway. A boot grace period of 60-90 s before spending the attempt would close it, at the cost of a
bench boot getting remote access a minute later. Not yet implemented.

**Boot grace period: proposed and dropped, same day.** The observation above stands — the client
attempt fires ~11 s after startup, so the "never drop the AP while a station is associated" guard
cannot protect someone standing at the kart when it powers up. The proposed fix was to wait 60-90 s
before spending the attempt. Rubén rejected it: if the phone is plugged and the hotspot on before
power-up, `tether_present` is true from the first poll, no attempt ever fires and the AP simply
stays, so the delay only buys something in a scenario he does not intend to be in. It also costs a
minute of no remote access on every tetherless boot. Not implemented, and not on the board — enable
the hotspot before booting instead.

## 2026-08-10 — Renamed the constant-speed controllers to constant-throttle

The speed controller options `constant` and `constant_stop` were named as if they held a speed.
Nothing in the stack can do that: the kart has no speed sensor, and `cmd_vel_bridge_node.py` turns
the `linear.x` value from `cone_follower_node.py` into a throttle fraction by plain division
(`throttle_effort = linear.x / max_speed`), open loop. So these modes hold a fixed PWM, not a fixed
speed. Renamed to `constant_throttle` and `constant_throttle_stop` (dashboard labels "Constant
Throttle" and "Constant Throttle + Stop"). The old strings are still accepted via
`ConeFollowerNode.SPEED_CONTROLLER_ALIASES` so a browser tab left open across the rename does not
silently fall back to `curve_factor`.

This also answers the question that prompted the rename: there is no separate test mission needed
for "fixed PWM plus a real steering algorithm". Any autonomous mission with the speed algo set to
Constant Throttle does it, and the steering algo dropdown stays free (geometric, Stanley, MPC...).
The throttle level is `max_speed` on the cone_follower node (2.625 in
`kart_bringup/launch/launch.py`) divided by `max_speed` on the cmd_vel_bridge node (default 5.0),
so 52.5% today. Neither is exposed on the dashboard, so changing it means editing the launch file
and restarting.

Separately, the `throttle_test` mission (`state_machine_node.py`) hardcodes `linear.x = 2.5` and
bypasses perception entirely — it exercises the throttle path alone and leaves steering untouched,
which is a different job from the above.

## 2026-08-10 — Split constant throttle into a cones-required and a blind mode

`constant_throttle` was not actually constant. `cone_follower_node.py` is driven by the detection
callback rather than merely gated by it: with no camera running, the detection callback never fires
and the only thing that publishes is the `_safety_check` timer, which zeroed `cmd_vel` after
`no_cone_timeout`. So the mode held a fixed throttle only while perception was feeding it cones,
and commanded zero otherwise.

Now there are two modes. `constant_throttle` keeps the old behaviour — fixed throttle while cones
are visible, zero when perception goes quiet, which is what a driving run wants. `constant_throttle_blind`
makes the safety timer publish the fixed throttle with steering centred instead of zero, so the
kart moves on a bench with no ZED, no cones and no perception nodes. That is the throttle-wiring
and ESP32-path check.

Note this puts the blind bench mode behind the normal autonomous gating (an autonomous mission plus
AS_DRIVING), unlike the older `throttle_test` mission in `state_machine_node.py`, which applies
throttle as soon as it is selected and never checks the state. `throttle_test` is left in place and
unchanged for now; whether to gate it, lower it, or delete it is still open.

## 2026-08-10 — Forward speed estimated from cone range rates

The kart has no speed sensor. The motor hall sensors would be the direct answer but
their pins are taken on the current PCB, so this is blocked until a board revision.
The ZED SDK's visual-inertial odometry used to fill the gap and was turned off in
`02eda4d` (19 Apr 2026) because it ran on the GPU that YOLO needs.

Cones do not move, so the rate at which a detected cone's distance shrinks is caused
entirely by the kart's own motion, and YOLO already finds those cones every frame for
steering. Built `src/kart_perception/kart_perception/speed_model.py` (geometry plus a
one-state Kalman filter) and `speed_estimator_node.py` (ROS glue, publishes
`/kart/speed`, launched under the `perception` condition in `kart_bringup`).

The useful piece of geometry: for a static cone at (x forward, y left), range
r = sqrt(x^2+y^2), with the kart at speed v and yaw rate w,

    dr/dt = (x*(-v + w*y) + y*(-w*x)) / r = -v * (x/r)

The yaw terms cancel exactly — rotation cannot change a distance. So each cone gives
v = -(dr/dt)/(x/r) with no yaw rate input needed, and the estimate stays correct
through corners, which is where a "how much did the forward coordinate change" method
would fail. Cones near 90 degrees off the nose are discarded because x/r approaches
zero there and dividing by it amplifies depth noise without limit.

Per-cone estimates are combined with a median rather than a mean, because the failure
that matters is a wrong frame-to-frame match producing one arbitrary value. The spread
of the estimates becomes the measurement noise handed to the filter, which is what
makes it self-tuning: close, plentiful, dead-ahead cones agree and the filter trusts
them; distant or sparse ones disagree and it coasts instead.

The filter is one state. A second state for accelerometer bias would need the ZED IMU,
and removing gravity from that reading needs pitch to about a degree — over a bumpy
circuit that error would inject more drift than it removed. Detections arrive at tens
of hertz, leaving little gap for an acceleration term to fill.

A review agent checked the derivation independently and found three things worth
recording. First, a process-model bug: uncertainty was grown by `(accel*dt)^2` per
call, so calling predict more often produced less growth for the same elapsed time and
the filter became more confident the faster its timer ran. It now grows the standard
deviation by `accel*dt`, which depends only on elapsed time. Second, a constants bug:
`MAX_FRAME_GAP_S` was 0.5 s while the match gate was 1.5 m, and at 12 m/s the kart
covers 6 m in that time — far enough to match a cone to its NEIGHBOUR, which every
cone in view does at once so the median cannot reject it. The gap is now 0.12 s, which
is 1.5 m at top speed. Third, and worst, feeding camera optical coordinates (x right,
z forward) where the kart frame (x forward, y left) is expected produced a rock-steady
0.00 m/s at a true 5 m/s, with many cones and a tight spread — a confident false
reading rather than a crash. A guard now refuses any frame where fewer than 60% of
cones are ahead of the kart.

Simulated against stereo depth noise: with 0.1 m of error at 10 m the estimate lands
within 0.1 m/s; with 0.4 m at 10 m it over-reads by about 0.4 m/s. That over-reading is
a bias, not noise — the median protects against errors affecting one cone, and is
powerless against an error every cone shares. The same applies to sideways motion read
as forward motion, to the camera sitting ahead of the axle so cornering swings it
sideways, and to anything misclassified as a cone that actually moves. All are listed
in the module docstring.

Nothing steers or brakes on this. It feeds the dashboard readout only, and wants
measuring against a GPS trace or a timed run before `stanley_assumed_speed` is retired.

## 2026-08-10 — Speed estimator deployed; first live reading

Built, pushed and deployed to the Orin the same day (`colcon build --packages-select
kart_perception kart_bringup`, then `systemctl restart kart-brain`). It came up clean
and gave its first honest result immediately: with the kart stationary and cones in
view it read 0.008 m/s, and reported that 68% of 311 frames yielded a usable
measurement. Cone detections were arriving at 61 Hz and `/kart/speed` published at its
configured 20 Hz.

That 68% is worth remembering as the baseline. A large drop would mean cones are being
lost between frames — either the match gate or the range band no longer suits the
track, or perception itself has degraded.

Not yet done, and the thing that decides whether any of this is usable: a run at a
known speed. Nothing on the kart steers or brakes on this figure until then.

## 2026-08-10 — Removed the zero-speed update; the throttle does not say the kart stopped

Rubén challenged the zero-speed correction added earlier the same day: it injected a
"speed is 0" measurement once the throttle had been shut for two seconds, and he
pointed out that throttle off does not mean stopped — ten seconds might, two does not.

Checking it, the problem was worse than the timeout. `update_stationary` used a
stddev of 0.05 m/s against the cone measurements' 0.15 m/s floor, so the assumption
was nine times more trusted than the camera watching the kart move. The gain worked
out near 0.9 and it fired at 20 Hz, so a coasting kart with cones in full view would
have been pulled to zero within a tick or two — a confident false reading, which is
the exact failure the repo already has a standing rule about.

It was also incapable of helping. While cones are visible the real measurements are
strictly better than an assumption; once cones stop, the filter reaches its validity
limit and the node stops publishing after about 0.65 s, well before a two-second timer
could fire. Its usual purpose — pinning down accumulated accelerometer bias — does not
apply here at all, because the one-state filter has no IMU, nothing integrates, and so
nothing drifts. It had been carried over from the earlier design that did include an
IMU and kept without rechecking whether it still had a job.

So it was deleted rather than retuned, along with the node's `/kart/cmd_vel_muxed`
subscription and its `use_zero_speed_update` parameter. `speed_model.py` keeps a note
where it used to be, saying what it would need to be correct if an IMU is ever added:
evidence from cone ranges holding steady, not an inference from the throttle.

Worth correcting one earlier claim in this log: the stationary reading of about
0.01 m/s observed on deployment was produced by the cone measurements, not by this
correction. Cones were visible for 100% of frames in that sample, so the zero-speed
update was contributing nothing to the number it appeared to explain.

## 2026-08-10 — Decided against adding the IMU to the speed estimator for now

Asked whether to fuse the ZED IMU into the cone-based speed estimate, or leave it as a
single-source adaptive smoother. Decision: leave it, and revisit only after the estimate
has been measured against a known speed. ZED visual odometry was ruled out separately by
Rubén as too GPU-heavy, consistent with why it was disabled in `02eda4d`.

Three reasons. First, the only consumer of `/kart/speed` is the dashboard readout, so the
gap the IMU would fill — the 0.65 s from cones vanishing to the node going silent — costs
a flickering number on a screen and nothing more. Second, an IMU introduces a bias that
drifts, and bias is only observable if something anchors it; the design's one anchor was
just removed because the throttle cannot tell whether the kart stopped, so the IMU would
make long dropouts worse rather than better. Third, the estimate's accuracy is unmeasured,
and building a fusion layer over a number that might not survive validation is work spent
on something that may be deleted.

What would reverse it: a validation run showing the estimate is accurate but drops out
often enough to be unusable through corners. The node's 10-second log line already reports
the share of frames yielding a measurement, so that figure comes free from the same run.

The hall sensors, when the PCB frees their pins, change the design rather than adding to
it — they would become the primary drift-free source, and they are what would make an IMU
worth fusing, because they give the anchor that makes its bias observable. Cones would
then become a cross-check. So IMU work done now would be partly thrown away.

## 2026-08-10 — Closed-loop constant_speed mode, so a test run holds a speed rather than a PWM

Rubén pushed back on the estimate being read-only: the validation run itself is much
better done at a slow constant speed than at a fixed PWM, because a fixed PWM gives a
speed that varies with slope, surface and battery charge, so every repeat measures
something different.

The objection to closing a loop on an unvalidated estimate dissolves with a throttle
ceiling. `_constant_speed_throttle` in `cone_follower_node.py` caps its output at
`self.max_speed` — the exact value `constant_throttle` commands outright — so however
wrong the speed estimate is, the throttle can never exceed what the open-loop mode
would have applied. The worst case is the behaviour being replaced, not a new one.

The rest of the containment:

  * No throttle at all unless a reading arrived within `speed_stale_timeout` (0.4 s).
    speed_estimator stops publishing when its estimate is no longer backed by cones,
    so silence means no measurement, never zero speed. A frozen reading from before
    the cones were lost must not keep feeding the pedal.
  * The integral is dropped whenever the reading is stale, so nothing accumulates
    while blind and slams in when cones return.
  * The integral is clamped to the same ceiling, so a kart held back on a slope or
    against a chock cannot store full throttle and release it when it frees.
  * Throttle only, never brake. A negative command would reach the brake actuator
    through cmd_vel_bridge, and braking on an unvalidated speed is a different risk
    from lifting off. Overspeed coasts down instead.

10 tests in `src/kart_control/test/test_constant_speed.py`, run without ROS.

The gains (kp 0.6, ki 0.4) are guesses, and should be treated as such: the throttle
command is in the fake-m/s units cmd_vel uses, and how those map to real speed on this
kart has never been measured, which is the very thing the run is for. Default target is
2.0 m/s. If the first run is lively, lower `max_speed` on the cone_follower node in
`kart_bringup/launch/launch.py` — it is the ceiling as well as the open-loop level.

---

## 2026-08-10 — YOLO was at 33 Hz instead of ~70 Hz: three CUDA syncs per cone in the ROS publish loop

The Orin's cone detection had roughly halved against the 70–84 Hz recorded on 2026-07-07
(see the dashboard-broadcast-rate entry above). It ran at 32–35 Hz.

**Everything the obvious suspects would predict was already fine.** The camera published
`/zed/zed_node/rgb/image_rect_color` at 81 Hz at VGA. The node loaded the FP16 TensorRT
engine `ruben_yolov11n_2026_03_320_orin_trt10.engine` at `imgsz=320`, not the FP32 one
from the 2026-03-21 error-log entry. The ZED's own object detection was off
(`od_enabled=False`), point cloud disabled, depth on PERFORMANCE. No stale duplicate ROS
processes. Inference was on `cuda:0` — `device=""` means auto-detect, and auto-detect
picked CUDA.

**The misleading signal was `top`:** the `yolo_detector` process sat at exactly 100.0% of
one core while the machine as a whole was 35% idle, and the node's single log line
(`infer=28ms`) lumped every stage together, so it could not say which stage.

**What found it** was splitting that log line into `decode / pre / gpu / post / ros`,
using ultralytics' own `results[0].speed` dict for the middle three (commit `8678aa8`):

| Stage | Time | Runs on |
|---|---|---|
| cv_bridge decode + crop | 0.5 ms | CPU |
| preprocess (letterbox) | 1.9 ms | CPU |
| inference | 3.0 ms | GPU |
| postprocess (NMS) | 5.5 ms | CPU |
| Detection2DArray build + publish | 15–19 ms | CPU, blocked on GPU |

**Root cause:** the publish loop read `box.xyxy[0].tolist()`, `float(box.conf[0])` and
`int(box.cls[0])` per detection. Each of those is a separate device-to-host transfer off a
CUDA tensor, so it is three GPU syncs per cone, and each sync blocks the thread until the
GPU drains. At ~19 cones a frame that measured ~0.8 ms per cone. The CPU was not computing
— it was waiting on the GPU, one cone at a time.

**Fix (`ead57a3`):** `r.boxes.data.cpu().numpy()` once per frame, then loop over plain
floats. `ros=` fell from 15–19 ms to 0.9 ms and the rate went **33 Hz → 67–72 Hz**,
measured on the kart after `sudo systemctl restart kart-brain`.

**Two things worth carrying forward:**

1. **Twelve cores do not help a single-threaded pipeline.** Decode, preprocess, inference,
   NMS and message building all run in `_inference_loop`, so the frame period is the *sum*
   of every stage. That is why one core read 100.0% while the machine was a third idle.
   "CPU-bound" here meant the CPU was the limiting stage, not that the model ran on CPU —
   worth stating precisely, because the two readings suggest opposite fixes.
2. **Never read ultralytics box attributes per box in a loop.** Pull the whole
   `r.boxes.data` tensor across once. The per-box form is what the library's own examples
   show, and it is fine on desktop where the sync is cheap; on Jetson's shared-memory GPU
   it dominated the frame.

**Profiling note for next time:** `sudo py-spy top --pid <yolo_detector pid>` attaches to
the running node with no restart and no code change, and would have shown this directly.
`tegrastats` gives the system-level GPU/CPU/EMC picture; `nsys` (ships with JetPack) shows
the CUDA timeline against the CPU when the question is "who is waiting for whom".

**Not the cause, but noted while looking:** the Orin runs `MODE_50W` (GPU capped at
816 MHz, CPU at 1.4976 GHz), which is what `kart-docs` orin-setup.md prescribes. However
`jetson_clocks.service` **does not exist** on the machine even though that doc's checklist
(line 527) lists it as enabled, and the CPU governor is `schedutil`, not `performance`.
Clocks happened to be at their caps when sampled, so this did not cost anything here — but
the checklist item is not actually satisfied.

## 2026-09-19 — Hall-speed dashboard bench state

The ESP32-S3 is installed in the Medulla PCB and connected to the Mac through its
USB-UART bridge at `/dev/cu.usbmodem5C372070281`. Rubén confirms nothing else is
connected to the PCB. Firmware `f4ac188` captures the three Hall inputs and sends
eight diagnostic fields appended to the health frame. Bare-board telemetry was
verified in kart-medulla: initialization succeeds, states and counters remain zero.
There is no motor signal available for speed calibration in this setup.

Dashboard integration is being prepared on `dev`. SSH to `orin-remote` closed
before login, and `orin-local` and `utm` timed out; deployment requires those
machines to become reachable. The local untracked `live.png` predates this work.

The dashboard now derives speed magnitude from the sum of counter deltas divided
by host-monotonic elapsed time and `hall_edges_per_metre`. It uses health packets
(nominally 1 Hz), so this is a display average, not a motor-control feedback signal.
Calibration defaults to zero (unknown). All three channels must advance during
the sample interval; missing transitions, changed multi-bit counters, resets,
malformed fields and stale telemetry suppress the number. An unchanged cumulative
multi-bit counter does not permanently suppress later good samples. No direction
or six-state commutation sequence is assumed. Hardware launch files load the shared
config; simulation explicitly keeps its external speed source. Other speed topics
cannot overwrite the hardware Hall display. No control behavior was changed.

Local verification: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src/kb_dashboard uv run
--no-project --with pytest --with 'websockets==11.0.3' --with pyserial pytest -q
src/kb_dashboard/test` passed 219 tests. A headless browser check at 1280×800 and
844×390 exercised all six pages, a synthetic 3 m/s sample (dial rounds 10.8 km/h to
11), six unavailable states and the browser stale timeout, with no JavaScript errors.
Telemetry/System screenshots were visually inspected at both sizes. Changed Python
files parsed successfully and `git diff --check` passed. The authentication test's
old substring assertion incorrectly rejected an existing HTML comment containing
“login”; it now compares the served page with the actual dashboard file.

The live-board check could not run: `/dev/cu.*` listed only Bluetooth-Incoming-Port
and debug-console when checked during dashboard testing. No USB serial device was
available. ROS launch/build checks and deployment remain unverified because both
target machines were unreachable. Next physical check: reconnect the board and
motor Halls with actuator power isolated, measure summed edges per metre with the
drivetrain engaged, set the config, then compare indicated speed with timed travel.

## 2026-09-19 — Hall-speed engineering documentation

The README now links to kart-docs' firmware page, section `motor-hall-speed`, for
the end-to-end capture, reporting, speed formula, calibration and validity limits.
The physical Hall-power arrangement must be checked before a powered-sensor,
unpowered-actuator rolling calibration; that prerequisite is tracked in `tasks.md`.
No dashboard runtime or calibration value changed in this documentation update.

## 2026-09-26 — Blind-throttle investigation bench state

Rubén reports the kart is powered and authorizes movement for diagnosing the
dashboard's constant-throttle blind mode. `ssh orin-remote` confirms host `orin`,
active `kart-brain` service, and `/dev/ttyACM0` present. The local access point SSH
address timed out. Connected sensors and actuator power have not yet been measured;
no hardware is reported deliberately absent.

The controller defect is reproduced by executing its actual detection callback:
empty frames refresh `last_detection_time`, suppressing the silence fallback,
and then unconditionally zero throttle. The fix preserves throttle only in
`constant_throttle_blind`; empty frames still command zero steering, and other
modes still stop. Regression tests failed in the three blind/empty cases before
the fix. `uv run --no-project --with pytest python -m pytest -q
src/kart_control/test/test_blind_throttle.py
src/kart_control/test/test_state_logic.py
src/kart_control/test/test_constant_speed.py` passes all 184 tests afterwards.

Live checks found a second independent obstruction: `journalctl -u kart-brain`
repeatedly reports the configured CH343 USB-UART by-id path missing.
`ls -l /dev/serial/by-id/` instead exposes only the Espressif native USB debug
port (303a:1001); a 115200-baud read returns ESP-ROM boot text. The current
kart-medulla source initializes UART0 for binary kart traffic. The operator
was asked to reconnect through the UART connector. No serial default was changed.
The dashboard showed READY after a Stop command, with blind throttle and steering
None selected. The Orin initially ran commit `5a192bb`, with a copied dashboard
installation; deployment therefore requires a rebuild, not only a pull.

Deployment: pushed `338ad50` to `dev`, pulled it on `orin-remote`, rebuilt
`kart_control`, `kart_bringup`, `kb_coms_micro`, and `kb_dashboard` with
`--symlink-install`, then restarted `kart-brain`. The dashboard build needed a
fresh package build directory (see `.agents/error-log.md`). The running control
script resolves to `src/kart_control/scripts/cone_follower_node.py`; the dashboard
now uses an egg-link. Reloading the visible dashboard verified the updated page,
then AUTO, steering None, and Constant Throttle (blind) were selected.

A four-second ROS subscriber probe measured 356 empty detection arrays and 357
autonomous commands, all `[2.625, 0.0]`. In AS_READY, all 401 mux commands were
zero and all 402 throttle frames were `[0]`. A subsequent Start/Stop probe found
the system already driving before its own Start (the journal confirms earlier
Start commands); therefore it did not independently test the READY-to-DRIVING
transition. It measured `[133]` throttle while driving and `[0]` after Stop.
`ros2 topic echo /kart/state --once` confirmed AS_READY afterwards. No actuator
response is claimed: the USB-UART by-id path remained absent. Reconnecting the
UART cable and verifying movement remain blocked on physical access.
`ssh -o BatchMode=yes -o ConnectTimeout=6 utm hostname` timed out, so VM deployment
also remains open. Both follow-ups are in `tasks.md`, indexed from vault tasks.

## 2026-09-26 — UART reconnection and constant-throttle code review

Rubén reports peers moved the ESP32 cable from its USB connector to its UART
connector and that this resolved the signal failure. A subsequent read-only
`ssh orin-remote` check found the configured CH343 by-id path restored and
`ros2 topic echo /esp32/heartbeat --once` returned type 8, payload 905183.
The sampled state was AS_EMERGENCY; this review did not reset or drive the kart.

The fixed blind mode preserves `max_speed` for empty, filtered-empty, and absent
detections; normal constant throttle still requires cones. The configured
2.625 command divided by the bridge's 5.0 scale is 52.5% before byte quantization.
Stop gates the command to zero, not an active braking request. The 184 existing
blind-throttle, state-logic and constant-speed tests passed again.

Two additional gaps were recorded in tasks.md rather than silently changing
control behavior during a requested review. First, the state machine and bridge
retain commands without expiration. A local probe compiled the actual
`_mux_tick` method, supplied one 2.625 autonomous command, and called it 1000
times without new input: every output remained 2.625 until Stop made it zero.
This is a software reproduction, not an on-kart failure test. Second, the
hardware launch gates `cone_follower` on `perception`, so disabling perception
also disables the blind-mode command producer. A Luna documentation worker was
assigned the connector and related operating/troubleshooting documentation in
the separate kart-docs repository.

## 2026-09-26 — Pedal response despite Speed Zero

Rubén reports the ESP32 follows the physical accelerator with dashboard Speed
set to Zero; whether this describes motor/output voltage or only the green
pedal indicator is awaiting clarification. Live `/orin/throttle` was `[0]`
and `/kart/state` was AS_EMERGENCY. A later check found no
`/dev/serial/by-id/` directory, with the comms journal reporting the configured
CH343 device missing. No command or firmware change was made during this probe.

Both local and Orin firmware source explicitly select the physical pedal when
comms are stale or mission is Manual (`KM_GPIO_SetThrottleSource(false)`), and
select the electronic output otherwise. Thus zero commanded throttle does not
override pedal control during communication loss. The green dashboard bar reads
`esp32_throttle` from the pedal sensor; the amber marker is `orin_cmd_throttle`.
Neither is a measurement of the voltage reaching the motor controller.

A separate verified code gap is that mission frames are sent only on mission
changes; a reconnect or ESP32 restart can leave its zero-initialized Manual
mission unchanged. This is a candidate explanation for the reported behavior,
not a measured firmware mission value. It is recorded as a follow-up in tasks.md.

## 2026-09-26 — Repeat mission selection after ESP32 reconnect

After Rubén reconnected the ESP32, a six-second probe observed heartbeat uptime
116183–121183 ms, live pedal frames around `[434,390,44,39]`, health flags 12,
and zero commanded throttle throughout AS_EMERGENCY. No `/orin/mision` frame
arrived. The log's latest selection was Manual to Autonomous before this
reboot. The ESP32's actual mission is not echoed, so its value remains inferred.
A single mission frame (type 35, payload 8) was resent without changing state or
throttle; the operator was asked whether the motor-controller voltage stopped
following the pedal. That physical result is still pending.

A production-method regression reproduced the missing mission refresh. The
state heartbeat now republishes the current mission alongside state at 10 Hz.
The new regression plus state-logic and blind-throttle tests pass: 175 tests.
This repairs reconnect recovery without modifying firmware or pedal fallback
behavior during genuine communication loss.

Mission-refresh deployment: `1ba58ca` was pushed, pulled on `orin-remote`, and
`kart-brain` restarted. AUTO, Zero speed, None steering and AS_EMERGENCY were
restored via dashboard ROS command topics. A three-second capture measured
30 `/orin/mision` frames, all `[8]`, 300 `/orin/throttle` frames, all `[0]`,
30 AS_EMERGENCY states, and three advancing ESP32 heartbeats. This verifies
publication and a live return link, not the physical throttle selector voltage.
The operator's physical-output check remains pending. The VM SSH retry timed out.

## 2026-09-26 — Start rejected by the diagnostic emergency state

Live `/kart/state` was AS_EMERGENCY and the journal showed rejected Start
commands. This was the state deliberately left by the mission-refresh probe,
not evidence that its code failed. The dashboard server optimistically sets
`state=running` before the state-machine response, explaining a transient green
Start indication. A Stop command restored AS_READY; live throttle remained `[0]`.
No Start command was issued in this recovery, and physical pedal isolation is
still awaiting an operator measurement.


## 2026-09-26 — Sensor safety implementation and command-runner recovery

Rubén requested a fast safety module separate from the one-second health reporting
loop. Firmware work is in kart-medulla (`km_safety`); the Orin supervisor validates
its status, inhibits startup, latches runtime faults and requires an acknowledged
explicit reset. The dashboard reports inhibition/fault reasons and waits for
confirmed state instead of optimistically showing Start as accepted.
`.agents/sensor-safety.md` defines required sensors, operating modes and remaining
physical limits. The design preserves autonomous None steering with healthy
sensors while denying propulsion in remote open-loop bench steering.

Local `exec_command` repeatedly failed with `EMFILE` before SSH could start. The
worker measured the parent command runner at its soft 256-handle limit, with 168
pipe descriptors and duplicate plugin process suites. Terminating unused CapCut,
Fusion and search tool children released their pipes; `exec_command true` then
succeeded. This was a Mac-side resource failure, not an Orin connection limit.

Target access: `ssh orin-remote` returned hostname `orin` and active kart-brain;
the brain working tree had only its pre-existing modified ZED-wrapper submodule.
Physical sensor connections, actuator isolation and shutdown-circuit wiring were
not measured. No firmware flash is authorized by the unverified isolation state.
`ssh -o ConnectTimeout=8 utm` timed out at 192.168.64.3, so VM deployment remains open.

### 2026-09-26 — Additional command-runner recovery verification

The Codex worker PID 98858 had 251 numeric file descriptors in `lsof -nP -p 98858 -F pft`, including 168 pipes. `launchctl limit maxfiles` reported a soft limit of 256; a shell launched by the worker also reported `ulimit -n` as 256. Four sleeping `evaluate mcp` children of that worker (14681, 88459, 99125, 99247) were terminated with SIGTERM. Subsequent normal execution succeeded, including five sequential `/usr/bin/true` child launches. A later `lsof` count was 180 descriptors. Other concurrent cleanup may also have contributed to that reduction. This clears the immediate exhaustion but does not raise the worker limit or prove helper accumulation is permanently resolved. No kart service was restarted for this recovery.


Verification: `PYTHONPATH=src/kb_dashboard uv run --no-project --with pytest
--with numpy --with aiohttp --with pyyaml python -m pytest src/kart_control/test
src/kb_dashboard/test -q` passed 458 tests with one ROS-only module skipped on the
Mac. An isolated `/tmp/kart-safety-test` workspace on the Orin built five packages
(kb_interfaces, kb_serial_driver_lib, kb_coms_micro, kb_dashboard, kart_control).
`ROS_DOMAIN_ID=73 ROS_LOCALHOST_ONLY=1 python3 -m pytest
src/kart_control/test/test_state_machine_launch.py -q` passed six ROS tests, including
startup inhibition, sensor faults, status silence, explicit reset acknowledgment
and None steering never emitting a position-loop command on Start. Dashboard
screenshots were inspected at 844×390 and 1280×800. Firmware final evidence is
kart-medulla dev `a7386fb`: 74 native tests and both board builds passed.

A cold review caught and fixed consumed reset-token retries, critical bench faults,
mission/state packet ordering, nonfinite perception and a transient steering-mode
change on Start. Firmware now also refuses remote drive outside its explicit OFF
state and reserves physical pedal ownership for manual OFF without a latch.
`origin/main` had three historical merge/revert commits absent from dev but no
content difference against their merge base; merging it into dev succeeded without
changing the implementation before the safety commit.

### 2026-09-26 — Safety rollout staged on the Orin

`ssh orin-remote` verified brain revision `0b4693c`, firmware revision `a7386fb`,
and an active kart-brain service. The real workspace build log
`/tmp/kart-safety-install.log` reports four packages finished in 40.2 seconds:
kb_interfaces, kart_control, kb_dashboard and kb_coms_micro. The firmware was not
flashed and the running service was not restarted. Activation requires confirmed
steering motor isolation before flashing, followed by a coordinated service restart
and physical fault/braking/reset checks. The modified ZED-wrapper submodule on the
Orin was left unchanged. VM deployment remains blocked by the recorded SSH timeout.

## 2026-09-26 — Autonomous pedal interference: live receive test and selector defects

Rubén reported actual acceleration from the physical pedal in Autonomous + Zero, and additional acceleration when pressing the pedal during Constant Throttle (blind). This is a physical-output report, not merely the pedal telemetry baseline.

A five-second ROS capture on orin-remote observed 453 throttle frames with payload [0], 50 mission frames with payload [8], and 101 pneumatic frames whose GPIO initialization error field was 0. No ESP_MISION echo was received. A separate command-reception test ran in AS_EMERGENCY: ORIN_STEER_PID (0x2B) changed only the override flag with gains unchanged at [1000,0,50,500]. Three ESP_STEER_PID echoes reported [1,1000,0,50,500]; restoring the flag produced three [0,1000,0,50,500] echoes. This establishes live command reception and restoration, not proof of the received mission or selector voltage.

The fresh v1 netlist /tmp/throttle-v1-net.xml and the U14 footprint in repos/other/dv-hardware-v1/projects/kart-medulla/kart-medulla.kicad_pcb identify MAX4660EUA+T. Pin 4 is on +5V_REG; pins 3, 7 and exposed pad 9 are on GND. The authoritative v1 association is dv-hardware tag kart-medulla-v1, revision 84d6dd0, per project records. The manufacturer datasheet specifies +9 to +40 V single-supply operation and requires the exposed paddle to connect to V+ or remain unconnected. Both design connections therefore conflict with its requirements. Source: https://www.analog.com/MAX4660/datasheet (Rev 1, pages 1 and 6). The ground pad has no B.Paste layer in the PCB file, so actual solder contact cannot be inferred.

The undervoltage was already an open blocker in dv-hardware/projects/kart-medulla/tasks.md. That file records a downstream mechanical panel switch selecting pedal versus medulla output and a v2 decision to delete U14. No completed U14 repair on the assembled v1 board was established. These are confirmed design defects and a strong hardware hypothesis, not yet a measured cause of this kart's symptom. Confirmation requires the assembled U14 identity/presence, supply and selector levels, and selected-output voltage while moving the pedal with propulsion isolated. Requested these measurements from the operator. No firmware was flashed or kart service restarted. Manufacturer PDF download through curl failed with HTTP/2 error 92; the web tool successfully read the official PDF, and its URL is preserved here.

## 2026-10-08 — Public dashboard and remote SSH share an offline Cloudflare tunnel

Read-only checks at 19:55 Europe/Madrid found both
`https://kart.rubenayla.xyz` and `https://orin.rubenayla.xyz` resolving to
Cloudflare anycast addresses and returning HTTP 530 with Cloudflare error 1033.
Cloudflare documents 1033 as the edge finding no healthy `cloudflared`
connector for the configured tunnel. The identical response on the dashboard
and SSH hostnames places the observed failure before either origin service, so
it does not establish whether the dashboard process itself is healthy.

The Mac was on `10.7.20.135/24` through gateway `10.7.20.1`. The historical
Orin address `10.7.20.142` did not accept TCP connections on ports 22, 80 or
9090 during this check; its recorded SSH host key matches the kart access-point
address `10.42.0.1`, which also timed out. A bounded subnet check found port 22
only on the Mac and `10.7.20.20`; the latter presented a different host and was
not treated as the Orin. `10.7.20.129` refused ports 22, 80 and 9090. No service,
tunnel, network setting, firmware or actuator state was changed. Recovery and
origin checks remain deferred until the operator finishes the wiring work and
authorizes hardware access.

After Rubén reported the wiring ready, a fresh check at 19:56:53 CEST still
returned HTTP 530 from both public hostnames. Fresh SSH attempts to
`10.42.0.1`, `10.7.20.142` and `orin-remote` also failed. Wiring readiness did
not restore the Orin's network path; USB phone tethering or another verified
internet connection remains the next recovery step.

Rubén then power-cycled the Orin. LAN SSH returned at `10.7.20.142`, and the
dashboard process answered HTTP 200 on local port 80. The tunnel service was
stuck activating: outbound QUIC connections timed out, and its 15-second
systemd startup timeout repeatedly terminated it. Ordinary HTTPS and TCP port
7844 to Cloudflare both worked, isolating the failure to QUIC over UDP on the
current lab network. Added a systemd drop-in that starts only `cloudflared`
with `--protocol http2`; the existing ingress configuration validated before
the restart. The connector registered over HTTP/2, the service became active,
`https://kart.rubenayla.xyz` returned the dashboard login page with HTTP 200,
and a fresh `orin-remote` SSH connection returned hostname `orin`. No
kart-brain service, firmware, actuator or network setting was changed.

## 2026-10-08 — Paired elevated-wheel throttle bench runtime prepared

Added explicit startup opt-in `KART_BENCH_THROTTLE=1` for the state machine, disabled by default. With this option, drive readiness requires autonomous mission, fresh controller None mode, matching firmware mission and the dedicated firmware identity flag bit 4 (mask 16). Only the active tank-low fault may be ignored; the real fault remains visible with a BENCH THROTTLE safety reason naming the 5% cap and steering inhibit. Other faults, latches, firmware/controller freshness and literal Start/Stop remain enforced. Unknown flag bits invalidate status. An opted-in runtime rejects ordinary firmware even if pressure is healthy; ordinary runtime rejects the bench image. Reset still requires every raw input healthy. The throttle_test mission is excluded from this facility.

The dedicated Medulla image enforces the 5% cap at the final actuator call, always disables steering and physical-pedal ownership, and retains the tank-low telemetry. Existing READY shutdown-chain behavior is unchanged; pressure-independent bench readiness does not establish pneumatic braking. The image must be replaced with normal firmware and the runtime environment variable removed before normal kart operation. Firmware implementation, build hash and flash history live in kart-medulla/history.md.

Supervisor/callback/state tests passed 226 tests, including invalid/missing identity, wrong mode/mission, other faults, Start/Stop, emergency and stale command/controller/status. Medulla native safety tests passed 14 tests, and both normal and dedicated bench S3 images compiled. Review found no blocking policy defect. Orin remote SSH then returned websocket bad handshake and verified LAN 10.7.20.142 timed out. Neither source nor opt-in was deployed, no second flash occurred and no motor command was sent. The operator was asked whether Orin remained powered. Once access returns, deploy while stopped, flash with the physical steering switch manual, verify zero output and bench identity, coordinate auto selection, and run a short attended Hall/Stop pilot.

## 2026-10-08 — Bench runtime deployed; READY with zero output

After Rubén corrected a power issue, remote SSH returned and exact source commits Brain `f7c8ef8` and Medulla `fc46f8f` were pulled with fast-forward updates. The unrelated modified ZED submodule was preserved and install symlinks resolve to source. Deployed the dedicated bench firmware while the physical steering-motor switch remained manual; full build/flash hash evidence is in kart-medulla/history.md.

The temporary runtime override is `/run/systemd/system/kart-brain.service.d/bench-throttle.conf` with `Environment=KART_BENCH_THROTTLE=1`. It disappears on Orin reboot; the bench firmware remains. Service startup was verified AS_OFF with zero outgoing throttle/steering. Live bridge max_speed 5.0 was checked before setting controller max_speed 0.25, steering None, constant_throttle_blind and autonomous mission. Without Start, state reached AS_READY, safety `[1,4,0,25,0,8]`, outgoing throttle/steering zero, fresh controller command `[0.25,0]`, and the explicit bench safety label. A second worker independently confirmed the same state and dashboard HTTP 200. An additional seven-second observer still saw zero output and Hall counters 0/0/0.

The operator was asked to confirm physical Auto selection before a bounded three-second pilot. No Start or motor command has been sent at this checkpoint. Commanded Hall/Stop observation and restoration of the normal firmware plus removal of the runtime opt-in remain pending. The nominal 5% effort encodes as integer 12/255 (approximately 4.71%).

## 2026-10-08 — Five-second capped propulsion command; no Hall transitions

After Rubén explicitly confirmed the physical switch was autonomous and requested movement, ran one five-second Start/Stop pilot through `/dashboard/state_cmd`, the state-machine backend used by the dashboard. The first preflight observer received READY and zero commands but had not discovered the safety topic within its 1.6-second baseline, so it refused before Start. Its unused fallback timer was canceled and service remained active. Replaced the fixed discovery delay with a bounded wait for all required live frames; no motion was commanded by that failed preflight.

The actual pilot rechecked AS_READY, safety `[1,4,0,25,0,8]`, outgoing throttle/steering zero and Hall telemetry. Immediately before Start it scheduled an independent systemd timer to stop kart-brain after 12 seconds if the pilot process or connection failed. It then commanded five seconds, checked safety/command freshness and zero steering, published Stop in cleanup, observed 2.4 further seconds and canceled the unused guard after confirming AS_READY and zero outgoing throttle/steering. During the pilot state was AS_DRIVING and maximum outgoing throttle was integer 12/255 (approximately 4.71%). Steering stayed zero, active tank-low remained 4, and no fault latched.

Hall counters were 75/77/77 before, 75/77/77 at the end of commanded drive and 75/77/77 after Stop: delta 0/0/0. Bits stayed 5. Last-edge age rose from 20248 to 25274 to 27289 milliseconds; last interval stayed 80772 microseconds and multiple-bit-change counter stayed zero. Controller uptime snapshots were 318057, 323050 and 325052 milliseconds. Earlier movement had occurred before this pilot, but its cause is not established. These measurements establish command and Stop state transitions, not motor rotation or physical stopping; no Hall edge occurred during the commanded window. No increase was attempted.

Code maps 8-bit level 12 to MCP4922 code 192/4095. The physical v1 board (`84d6dd0`) has a 5 V reference and direct DAC-to-mux throttle path, so the expected voltage is approximately 0.234 V if the analog path works. The v2 amplifier gain does not apply. Existing generic pedal documentation calls 0–1 V released, but the actual supplied Kunray controller's input threshold is not recorded. Current telemetry has no analog throttle-output measurement; SPI write counters would not prove voltage at CN10.1 or the ESC. Thus a below-threshold command and an analog/selector/controller fault remain distinguishable hypotheses. Physical visible-wheel response was asked of the operator. AS_READY/zero was left in place; the bench image/runtime remain installed and must be restored before normal driving. No flash or reset occurred with the physical switch autonomous.

## 2026-10-08 — Wider bench electrical ceiling prepared for onset search

The five-percent pilot command was below the fitted pedal's idle signal: live pedal telemetry `[433,400,44,40]` gives approximately 0.866 V on the accelerator signal through the physical v1 board's 10k/10k divider, although pedal position was not independently confirmed. Historical released-pedal measurements imply approximately 0.82 V. These are measured pedal references, not a verified ESC start threshold. The five-percent DAC command nominally produces only 0.234 V.

Prepared a bench-only final electrical ceiling of 30% (nominally about 1.49 V after 8-bit encoding), with matching Orin safety label and tests. Production output limits and all fault/state/freshness gates are unchanged. Fourteen firmware native tests and 226 brain tests passed again; the changed bench image compiled. The plan is an attended, bounded onset search starting at 18%, followed only when directed by 2-percentage-point steps, stopping between pilots and at the first Hall response. The ceiling is not a motor torque/speed percentage or a target to reach automatically. The actual ESC threshold and analog output remain unmeasured. Flashing is held until the operator explicitly returns the physical switch to manual; the earlier five-percent image remains installed at this checkpoint.
