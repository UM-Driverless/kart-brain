# Agent Quick Reference

<!-- Keep this file under ~150 lines. It is a routing layer with essential inline context. -->

## .agents/ File System

**These files are the source of truth.** Always consult them before answering technical questions or making changes. Never rely on auto-memory for project-specific technical state.

**Read in full** before working (keep concise, < 150 lines):
- **`README.md`** — System overview and file relationships.
- **`tasks.md`** (repo ROOT, not .agents/) — Current work items and status.

**Consult selectively** (search/grep — these grow over time):
- **`error-log.md`** — Append-only log of past mistakes and preventions.
- **`notes.md`** — Design decisions and rationale.
- **`history.md`** — Chronological audit trail of significant findings and decisions (append at the end, oldest first).
- **`scratchpad.md`** — Permanent scratchpad. Random notes, no cleanup needed.

**Reference** (read when relevant):
- **`architecture.md`** — System architecture, packages, topic map, message types.
- **`src/kb_dashboard/frame.md`** — Dashboard interaction and design rules; read before dashboard UI changes.
- **`dashboard-testing.md`** — How to run and test the dashboard with no hardware, and the traps in it. Read before editing `src/kb_dashboard/kb_dashboard/index.html`.
- **`orin-environment.md`** — Jetson Orin hardware setup and versions.
- **`vm-environment.md`** — UTM VM setup.
- **`simulation.md`** — Gazebo Fortress simulation details.
- **`adding-messages.md`** — How to add new ROS message types.
- **`orin-flash-guide.md`** — Flashing the Jetson Orin.

## Environments

### Jetson Orin (Real Hardware)
- **Connection:** `ssh orin-local` (join the "kart" Wi-Fi AP, pwd `umotorsport` → 10.42.0.1) or `ssh orin-remote` (Cloudflare Tunnel, needs the USB-tethered phone or other internet) or AnyDesk. Offline dashboard: `http://10.42.0.1/` on the kart AP (port 80 — the default `port` param and all three launch files set 80; it was 9090 before commit `6756a39`).
- **Dashboard:** `kart.rubenayla.xyz` (password: `0`, configurable via ROS param `password`)
- **Workspace:** `~/kart-brain` (renamed from `~/kart_brain` on 2026-07-06; `.bashrc` + systemd unit updated, workspace clean-rebuilt)
- **Camera:** ZED 2 stereo (USB)
- **sudo password:** `0`
- **Full details:** `.agents/orin-environment.md`

### UTM VM (Simulation)
- **Connection:** `ssh utm` (192.168.64.3, static IP)
- **Workspace:** `~/kart-brain/`
- **Simulator:** Gazebo Fortress (headless, CPU rendering)
- **sudo password:** `0`
- **Full details:** `.agents/vm-environment.md`

## Working Style
- **Add discovered work to `tasks.md` (repo root) immediately, without asking.** Any gap noticed mid-session — missing feature, pending validation, half-done rename, stale doc — becomes a task entry the same turn it's discovered. The board is the memory, not the conversation.
- **Open a hardware session by writing the bench state into `history.md`** — what is connected, powered, and
  deliberately absent. More time is lost here diagnosing unplugged hardware than to real bugs.
- **Use subagents for parallel work.** When tasks are independent (e.g., reading multiple files, searching codebase, running builds while editing), use the Agent tool to delegate to subagents. This keeps the main context clean and speeds up work.
- **Use background tasks for long-running ops.** Builds (~30s–100s), flashing, serial reads, SSH commands — run these in the background and check results later.
- **Don't block on things you can parallelize.** If you need to edit 3 files and deploy, edit them all, then deploy. If you need info from 2 different places, query both at once.

## Branch Workflow (READ THIS)

**All day-to-day work happens on `dev`.** `main` is a protected release branch — it only receives merges from `dev` (or feature branches) *after* the change has been physically validated on the kart. This applies to every UM-Driverless repo (`kart-brain`, `kart-medulla`, `kart-docs`, etc.).

- **Default working branch on the Mac, the Orin, and the VM is `dev`.** Every `git checkout` / `git pull` you do should be on `dev` unless you have a specific reason (e.g. inspecting `main`).
- **Commit and push to `dev` first**, every time. Never push directly to `main`, even for "trivial" changes — `main`'s protection will reject you anyway, and a rejected push after a merge/cherry-pick creates annoying recovery work.
- **Merge `dev` → `main` only after validating** the change drives the kart without regressions. Open a PR (`gh pr create --base main --head dev`), get at least 1 approval from a teammate (the admin-bypass setting lets you self-merge but peer review is strongly preferred), then merge.
- **Branch-protected repos** (e.g. kart-medulla): open with `gh pr create`, merge with `gh pr merge`.
- **Feature branches off `dev`** for exploratory work (`feature/xyz`), merged back into `dev` via PR or fast-forward. `main` is not the target for in-progress work.
- **`dev` is expected to be a tiny bit ahead of `main` most of the time.** If you discover `main` is *ahead of* `dev` (e.g. someone pushed straight to main), merge `main` into `dev` immediately before adding new commits so `dev` remains the "latest + in-progress" snapshot.

## Deploying — Never Ask, Just Do It

**Push, pull on the Orin, and restart whatever the change touched. Every time, without asking.**
"Say the word and I'll push", "shall I deploy?", "let me know if you want this on the kart" are all
wasted turns — the answer is always yes. A commit that sits unpushed is invisible to the Orin, the VM,
and every other machine, so committing without pushing is a half-finished job, not a cautious one.

What each kind of change needs after `git pull` on the Orin:
- **`index.html`** — same rule as the Python bullet below, because the page served is
  whatever `server.py` resolves next to its own module. Check
  `install/kb_dashboard/lib/python3.10/site-packages/`: an `.egg-link` means the page comes
  straight from `src/` and a pull plus restart is enough; a real `kb_dashboard/` directory means
  the page was copied at build time and needs `colcon build --symlink-install --packages-select
  kb_dashboard` first. It was copied until 2026-08-10 and is egg-linked now. Either way restart
  and hard-refresh the browser. Verify by grepping the file the egg-link or the install dir
  actually points at — never the tunnel root, which returns the login page. This line claimed
  "nothing" until 2026-08-08 and caused a fix to be reported as deployed when it was not.
- **Other Python (nodes, `server.py`, launch files)** — whether a pull is enough depends on how that
  package was **last built**, which is invisible from `src/` and differs per package. Check before
  claiming a deploy:

      ls ~/kart-brain/install/<pkg>/lib/python3.10/site-packages/

  An `.egg-link` there means the package was built with `--symlink-install` and resolves back to
  `src/`, so a pull plus `sudo systemctl restart kart-brain` is enough. A real directory means the
  sources were **copied** at build time and a pull changes nothing that runs — rebuild with
  `colcon build --symlink-install --packages-select <pkg>`, then restart. As of 2026-08-10
  `kart_perception` is egg-linked and `kb_dashboard` is copied, so the two behave differently right
  now. `kart_control`'s nodes live in `scripts/` under `ament_cmake` and are symlinked. A restart is
  always required either way: the running process already imported the old module.
- **C++** — `colcon build --symlink-install --packages-select <pkg>`, then restart.
- **ESP32 firmware** — flash it. **Stop `kart-brain` first**: `KB_Coms_micro` holds `/dev/ttyACM0`, and
  esptool fails with "device reports readiness to read but returned no data (device disconnected or
  multiple access on port?)" if you skip it. Confirm the port is free with `fuser /dev/ttyACM0`, flash,
  then start the service again.
  **Also de-power the Cytron or unplug the steering motor before flashing.** Steering is the one
  actuator with no hardware safe state — `CMD_STEER_PWM` runs from the ESP32 straight to the Cytron
  with no pull resistor, so the pins are undriven for the whole bootloader window, and the Cytron is
  fed permanently from the 48 V pack. On 2026-08-08 the steering swung to full lock during a reflash
  and broke teeth off the steering gears. Stopping `kart-brain` is not a substitute: it does nothing
  while the chip is in the bootloader. Full account in kart-medulla `history.md`, same date.

Then say in one line what was deployed and what you saw come back. Do not report a push as if it were
a deployment — the two commits that only moved documentation onto the Orin, while the dashboard fix
the user was waiting for stayed unpushed, are recorded in `.agents/error-log.md` (2026-07-30).

## Definition of Done
A change is NOT done until it's **validated on the target machine**:
- Code pushed to `dev`? → **Pull on Orin/VM too.**
- ESP32 firmware? → **Flash it.**
- Python/launch change? → **Restart the affected nodes.**
- Never claim something is fixed if you only pushed — deploy and verify.
- **A build result is not evidence, nor is "the code looks right".** Evidence: firmware → a measurement off the
  device; dashboard → the rendered value, seen; logic → a test. Builds can succeed having compiled nothing.
- **Only then merge `dev` → `main` via PR.** `main` represents "validated on the kart", not "code looks good to me".

## Critical Rules
- **Coordinate frame: x forward, y left, z up (ROS REP 103), right-handed.** Yaw is rotation about z, so a **positive steering angle is a LEFT turn** — by the right-hand rule a positive rotation about +z swings the nose toward +y, which is left. This holds everywhere: controller output, `cmd.angular.z`, the ESP32 setpoint, and every dashboard widget. A UI that renders positive to the right is a bug, not a "visual convention" — screen x and SVG `rotate()` are clockwise-positive, so display code must flip the sign when converting to screen space, never the data. See the 2026-07-18 entry in `history.md`.
- **An absent or out-of-range sensor must read as no-data, never as a number** — a plausible false reading is
  indistinguishable from a real one and will be acted on. Represent invalidity at the **source** (NaN or a
  validity flag in firmware), not at the display: the dashboard is one consumer, the PID is another. Test by
  unplugging the sensor and looking, not by reading code. Cases: `history.md` 2026-07-25.
- **State only what you measured.** A claim in a report or committed file carries the command that produced it,
  or is labelled a hypothesis. When a number surprises you, the next action is the measurement that would
  *falsify* your favourite explanation — not the explanation.
- **Don't trust auto-memory for technical state.** Auto-memory (`~/.claude/.../memory/`) goes stale fast — file paths, parameter values, launch files, firmware settings all change between conversations. **Always read the actual file or SSH to check** before quoting any value. Treat memory as "might have been true once" not "is true now". `.agents/` docs and the code itself are the source of truth.
- **Environment is in `.bashrc`** — ROS, workspace, and `IGN_GAZEBO_RESOURCE_PATH` are all sourced in `.bashrc` on every machine. **Never tell the user to source or export these manually.**
- **After creating/modifying files under `src/`, scp them to the VM and rebuild via SSH — don't just tell the user.** Use: `scp <files> utm:~/kart-brain/...` then `ssh utm "source /opt/ros/humble/setup.bash && cd ~/kart-brain && colcon build --symlink-install --packages-select <pkg>"`. Note: `.bashrc` is NOT sourced in non-interactive SSH — always source ROS explicitly.
- **Gazebo Fortress uses `ign` CLI**, not `gz`. Message types are `ignition.msgs.*`, not `gz.msgs.*`.
- **No `<cone>` geometry** in SDF — use `<cylinder>` instead (Fortress limitation).
- **Odom is relative to spawn** — always account for the kart's initial world position.
- **No hardware GPU on the VM** — CPU rendering via llvmpipe (OpenGL 4.5). Gazebo GUI works on `DISPLAY=:0` but headless EGL fails. Keep camera resolution at 640x360.
- **Kill ROS properly before relaunching.** Never use `killall python3` — it misses orphaned children. Use: `sudo kill -9 $(ps aux | grep -E "ros2|yolo|cone_|steering|cmd_vel|state_machine|dashboard|KB_Coms|component_container|robot_state" | grep -v grep | awk '{print $2}') 2>/dev/null` then verify `0` processes remain, then `rm -rf /dev/shm/fastrtps_*`. Stale processes eat GPU and halve FPS.
- **Clean up after yourself** — delete temporary files, screenshots, debug artifacts, and tool-generated directories before finishing. Don't leave untracked trash in the repo.
- When something goes wrong, document it in `.agents/error-log.md`.

## Build & Run
```bash
# Build everything (always use --symlink-install so Python/launch edits take effect without rebuilding)
cd ~/kart-brain && colcon build --symlink-install

# Build single package
colcon build --symlink-install --packages-select kart_perception

# Live perception on Orin
~/kart-brain/run_live.sh

# Simulation in VM
ros2 launch kart_sim simulation.launch.py
```

## Key Paths
| Path | Description |
|---|---|
| `src/kart_perception/` | Perception pipeline (YOLO + depth + viz) |
| `src/kart_sim/` | Gazebo simulation package |
| `src/kart_bringup/` | Launch files and config for real hardware |
| `src/msgs_to_micro/` | ESP32 serial comms (C++) |
| `models/perception/yolo/best_adri.pt` | YOLO weights |

## Cone Class IDs
Used everywhere — YOLO class names, Detection messages, visualization:
- `blue_cone` — left track boundary
- `yellow_cone` — right track boundary
- `orange_cone` — start/finish markers
- `large_orange_cone` — large start/finish markers

## Documentation Rules
- **Document every decision.** When a version is chosen, a workaround is found, or an approach is selected over alternatives, write it down in the relevant `.agents/` file with the date and reasoning.
- **Document every error.** When something breaks or doesn't work as expected, add it to `.agents/error-log.md` with what happened and the prevention rule.
- **Document every version.** Software versions, SDK versions, wheel sources, compatibility notes — all go in `.agents/orin-environment.md` or the relevant environment file.
- **Official docs live in kart-docs.** The `.agents/` directory is for AI agent workflow. Official project documentation goes to https://github.com/UM-Driverless/kart-docs.

## Task Management
- **`TODO.md`** — Human-curated roadmap. High-level goals and priorities. Agents read this for context but do **NOT edit it** unless explicitly asked.
- **`tasks.md`** (repo root) — The task board. Concrete, actionable work items derived from TODO.md. There is intentionally NO `.agents/tasks.md` in this repo — never create one. Holds only live work — see the done-archive convention below.
- **`tasks/done-archive.md`** — Completed work, moved out of `tasks.md` once closed. Holds nothing actionable; read only when you need the history of a shipped item. `tasks/archive_done.py` does the split mechanically (dry run by default, `--apply` to write).
- When starting work, check `tasks.md` first. If empty or stale, derive tasks from `TODO.md`.
- Status lives in an inline marker while a task is open — claiming or blocking a task means flipping the marker in place, never moving the line: `- [ ]` ready, `- [→ YYYY-MM-DD agent:id]` in progress, `- [⏸ reason]` blocked. Existing `## Ready` / `## In Progress` / `## Blocked` / `## Done` headers are loose thematic grouping, not a requirement to relocate an open task when its status changes.
- **Done items do not stay on the board.** When an item closes, move it — with its date and closing note, verbatim, no rewording — to `tasks/done-archive.md`. **Exception: a done step of a task that is still open stays on the board.** A `- [x]` bullet nested under a `###` cluster heading is a finished step, not a finished task — it stays until the whole cluster's last step closes, because archiving it strips the remaining open step of the context that says what was already settled. Only a `- [x]` sitting directly under a `##` heading (standalone, not nested in a cluster) moves on its own.

### Subagent Task Protocol
When the user says "work on tasks" (or similar), launch subagents to execute tasks from `tasks.md`:

1. **Pick** a `- [ ]` (ready) task. Each subagent picks a different task.
2. **Claim** it in place: flip the marker to `- [→ YYYY-MM-DD agent:id]`. No move.
3. **Do the work.** Read relevant `.agents/` docs first. Follow all project conventions.
4. **If blocked** (needs user input, hardware access, unclear requirements): flip the marker to `- [⏸ reason: <specific question>]`, in place, then stop and return the question.
5. **If done**: commit the changes, flip the marker to `- [x YYYY-MM-DD]`, in place, then return a summary.
6. **Independent tasks can run in parallel** — launch multiple subagents simultaneously. Tasks that touch the same files must run sequentially.
7. **Never skip steps** — always update the task's marker before and after work.

## Commit Protocol
1. `git status` — check what will be committed
2. `git diff --cached` — review changes
3. Build and verify before committing
4. If a mistake occurred, document it in `.agents/error-log.md`
5. If recurring, create a detailed write-up in `.agents/errors/`
