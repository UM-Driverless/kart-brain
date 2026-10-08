<!-- consult selectively — grep, never read in full -->
# Error Log

Tracks mistakes made during development and the prevention mechanisms added. Every recurring or painful error should be documented here.

## Format
```markdown
## YYYY-MM-DD - Brief title
**What happened:** Description of the error
**Prevention added:**
- List of changes made
```

---

## 2026-02-22 - BGR/RGB swap in YOLO annotated image
**What happened:** The live camera feed in rqt_image_view showed inverted colors (blue sky appeared orange). Root cause: `image_source_node` publishes BGR (from OpenCV), `yolo_detector_node` converts to RGB for inference, then `results.render()` draws on the RGB buffer, but the result was published with `encoding="bgr8"` without converting back.
**Prevention added:**
- Added `cv2.cvtColor(rendered[0], cv2.COLOR_RGB2BGR)` before publishing the debug image in `yolo_detector_node.py`
- Rule: **OpenCV uses BGR, ROS Image with "bgr8" expects BGR, but YOLO/PIL/matplotlib work in RGB.** Whenever passing images between these systems, always verify the channel order matches the declared encoding. If you convert to RGB for inference, convert back to BGR before publishing as "bgr8".

## 2026-02-22 - Duplicate YOLO detector processes consuming all GPU
**What happened:** Multiple `yolo_detector` instances were running (from both `run_live.sh` and manual launches), each consuming ~500-800% CPU and GPU memory. The system was sluggish.
**Prevention added:**
- Rule: Before launching nodes, always check for existing instances: `ps aux | grep yolo_detector | grep -v grep`
- Rule: Kill stale processes before launching new ones. Use `kill -9` if SIGTERM doesn't work within a few seconds.

## 2026-02-21 - Cone geometry invisible in Gazebo Fortress
**What happened:** Used `<cone>` SDF geometry for track cones. Gazebo Fortress (SDF 1.6) does not support cone geometry — it silently renders nothing and logs `Geometry type [0] not supported` for every cone (44 errors). The cones were invisible in the sim.
**Prevention added:**
- Replaced all `<cone>` with `<cylinder>` geometry in world SDF and model files
- Documented in `.agents/simulation.md` under "Known Issues"
- Rule: Only use geometries supported by Fortress: box, sphere, cylinder, capsule, ellipsoid, plane, mesh

## 2026-02-21 - Odometry at (0,0) despite kart spawned at (20,0)
**What happened:** The `perfect_perception_node.py` used raw odometry (x,y) as world position. But Gazebo odometry is relative to the spawn pose — it reports (0,0) at startup regardless of world placement. All cones appeared >20m away, so zero detections.
**Prevention added:**
- Added `kart_start_x`, `kart_start_y`, `kart_start_yaw` parameters to `perfect_perception_node.py`
- The node transforms odom into world frame: `world_pos = start_pos + rotate(odom_pos, start_yaw)`
- These parameters MUST match the `<pose>` in `fs_track.sdf` (currently: 20, 0, yaw=1.5708)
- Documented in `.agents/simulation.md`

## 2026-02-21 - Kart drifts 1000+ meters during Gazebo startup
**What happened:** With `real_time_factor: 0` (unlimited speed), Gazebo simulated thousands of seconds during the ~12s wall-clock startup. Even tiny physics artifacts (wheel clipping ground) accumulated into massive odometry drift. By the time the controller started, the kart was kilometers off-track.
**Prevention added:**
- Changed physics to `real_time_factor: 1` in `fs_track.sdf`
- Alternatively: start Gazebo paused (omit `-r`), start all nodes, then unpause via `ign service`
- Rule: Never use `real_time_factor: 0` unless all control nodes are already running

## 2026-02-21 - Steering joints log velocity control warnings
**What happened:** Gazebo logged `Velocity control does not respect positional limits` for steering joints that had position limits but no effort limits.
**Prevention added:**
- Added `<effort>1e6</effort>` to both steering joint limits in `kart/model.sdf`

## 2026-02-21 - Wheels start below ground plane
**What happened:** With the kart model at z=0.15, wheel centers were at z=0.10, and with radius 0.15, wheel bottoms were at z=-0.05 — embedded in the ground. This caused physics jitter.
**Prevention added:**
- Increased model z-offset to 0.22 so wheel bottoms are at z≈0.02 (above ground)
- Rule: When changing wheel radius or chassis height, verify `model_z - wheel_z_offset - wheel_radius > 0`

## 2026-02-21 - sudo requires password over non-interactive SSH
**What happened:** `ssh utm "sudo apt install ..."` failed because sudo needs a TTY for password input. `-t` flag doesn't help from non-interactive contexts.
**Prevention added:**
- Use `ssh <host> 'echo "0" | sudo -S <command>'` for all sudo operations
- Documented in `.agents/vm-environment.md` and `.agents/orin-environment.md`

## 2026-02-22 - Wrong IP for y540 laptop, wasted time on SSH setup
**What happened:** The laptop (y540) was given IP 10.7.20.136 but DHCP had assigned 10.7.20.138. Spent multiple attempts trying to connect to the wrong IP. Also referenced IPs without labeling which machine they belonged to, causing confusion.
**Prevention added:**
- Rule: **All machines on Robots_urjc use DHCP — IPs can change.** Always verify the current IP with `hostname -I` on the target machine before attempting SSH.
- Rule: **Always label IPs with the machine name** (e.g., "Mac at 10.7.20.28", "Orin at 10.7.20.142") — never mention bare IPs.
- Rule: **SSH config hostnames are the source of truth** (`ssh orin`, `ssh y540`). When an IP changes, update `~/.ssh/config` on the Mac.

## 2026-02-22 - SSH server not installed on fresh Ubuntu laptop
**What happened:** Tried to SSH into the y540 laptop but got "Connection refused" — openssh-server was not installed. Cannot install it remotely without SSH access.
**Prevention added:**
- Rule: When setting up a new machine for remote access, the **first step is always installing openssh-server** on it physically: `sudo apt install -y openssh-server`

## 2026-02-22 - ZED SDK installer breaks pip permissions
**What happened:** The ZED SDK installer runs pip as root to install `pyzed`, `numpy`, and `cython` into `/usr/local/lib/python3.10/dist-packages/`. This leaves `.dist-info` directories owned by root with no world-read permission. Subsequent `pip3 install` by the normal user fails with `PermissionError: [Errno 13] Permission denied: '/usr/local/lib/python3.10/dist-packages/pyzed-4.2.dist-info'`.
**Prevention added:**
- After installing ZED SDK, always run: `sudo chmod -R a+rX /usr/local/lib/python3.10/dist-packages/`
- Rule: When any installer runs pip as root (sudo), check permissions on dist-packages afterward.

## 2026-02-22 - PyTorch installed as CPU-only (Jetson AI Lab index unreachable)
**What happened:** `pip3 install --extra-index-url https://pypi.jetson-ai-lab.dev/jp6/cu126 torch torchvision` was run, but the Jetson AI Lab index didn't resolve (DNS failure: `Name or service not known`). Pip silently fell back to PyPI and installed the standard `torch 2.10.0+cpu` wheel (no CUDA). Also pulled `numpy 2.2.6`, breaking `pyzed` and `cv2`.
**Prevention added:**
- Rule: After installing PyTorch, **always verify CUDA is available**: `python3 -c "import torch; print(torch.version.cuda, torch.cuda.is_available())"`
- Rule: After any `--force-reinstall` of torch, **immediately pin numpy**: `pip3 install 'numpy<2'`
- Rule: Check the extra-index-url is reachable before relying on it: `curl -sI https://pypi.jetson-ai-lab.dev/`
- Added to TODO.md as a blocked task

## 2026-02-25 - cuBLAS fails on Jetson: pip cuBLAS 12.9 vs system CUDA 12.6
**What happened:** `torch.matmul` and any operation using cuBLAS on the Orin crashed with `CUBLAS_STATUS_ALLOC_FAILED when calling cublasCreate(handle)`. Element-wise CUDA ops (add, mul) worked fine. Root cause: `pip install torch` from `pypi.jetson-ai-lab.io` pulled in `nvidia-cublas-cu12==12.9.1.4`, which is incompatible with the Jetson's system CUDA 12.6. The pip libs were placed first in `LD_LIBRARY_PATH`, shadowing the working system cuBLAS.
**Prevention added:**
- In `run_live.sh` and `run_live_3d.sh`, prepend `/usr/local/cuda-12.6/targets/aarch64-linux/lib` to `LD_LIBRARY_PATH` **before** pip NVIDIA libs, so system cuBLAS 12.6 is loaded instead of pip cuBLAS 12.9
- Rule: **On Jetson, system CUDA libs must always precede pip-installed NVIDIA libs** in `LD_LIBRARY_PATH`. The pip packages are built for generic CUDA and may not work on Jetson's integrated GPU.
- Rule: After installing torch on Jetson, always test: `python3 -c "import torch; a=torch.randn(4,4,device='cuda'); print(a@a)"`

## 2026-02-25 - YOLOv5 torch.hub broken with torch 2.10
**What happened:** `torch.hub.load("ultralytics/yolov5", "custom", ...)` downloads YOLOv5 source from GitHub. The cached AutoShape preprocessing code is incompatible with torch 2.10 — it passes raw numpy arrays to `conv2d()` instead of converting to tensors. Tried: numpy input, PIL input, manual tensor input — all failed differently. Clearing the hub cache didn't help.
**Prevention added:**
- Migrated `yolo_detector_node.py` to use the `ultralytics` pip package API (`from ultralytics import YOLO`) as primary, with torch.hub as fallback for legacy YOLOv5 weights
- Switched default weights to YOLOv11 (`nava_yolov11_2026_02.pt`) which works natively with `ultralytics`
- Rule: **Prefer the `ultralytics` pip package over `torch.hub.load`** for any YOLO inference. The pip package is actively maintained and handles device management, preprocessing, and model fusion internally.

## 2026-02-24 - Multiple failed torch install attempts on Jetson Orin
**What happened:** Original JetPack PyTorch 2.5.0a0 (NVIDIA build, CUDA working) was overwritten by CPU-only torch during dependency installation. Four recovery attempts failed:
1. `pip install torch==2.5.0 --index-url nvidia.../jp/v60` — no wheels found
2. NVIDIA Jetson wheel `torch-2.5.0a0+nv24.08` — CUDA works but torchvision 0.20 from PyPI overrides it with CPU torch
3. `--no-deps` reinstall of NV wheel + torchvision 0.20 from PyPI — `torchvision::nms does not exist`
4. `torch+torchvision from pypi.jetson-ai-lab.io/jp6/cu126` — torch 2.10 + torchvision 0.25, CUDA works, but YOLOv5 incompatible + cuBLAS version mismatch
**Prevention added:**
- Rule: **Never `pip install torch` without `--extra-index-url` pointing to the Jetson wheel index.** Always use `pypi.jetson-ai-lab.io/jp6/cu126` for JetPack 6 + CUDA 12.6.
- Rule: **After installing torch, immediately verify**: `python3 -c "import torch; print(torch.__version__, torch.cuda.is_available()); a=torch.randn(2,2).cuda(); print(a@a)"`
- Rule: **Pin `numpy<2`** after any torch install (pyzed and cv2 need numpy 1.x)
- Rule: **Never install torchvision from PyPI on Jetson** — it pulls CPU-only torch as a dependency. Always install from the same Jetson index.

## 2026-02-28 - Failed to visually validate trajectory — kart exited track undetected
**What happened:** After training a neural_v2 controller with cone-proximity penalty, I generated a trajectory plot and claimed "0 boundary violations" and "trajectory stays between cones." The user immediately spotted that the trajectory went far outside the cone boundaries — reaching y=30 while cones only go to y=23. The root cause was twofold: (1) the centerline in `track.py` was a hardcoded R=20 oval that didn't match actual cone positions (7–8.5m beyond outermost cones at curves), and (2) the "validation" only checked distance to individual cone *points*, not to the boundary *line segments* between cones. The kart exited the track in the gaps between cones without approaching any single cone. Actual analysis showed 1324/2001 steps were outside the track polygon.
**Prevention added:**
- Rule: **Always visually inspect generated plots with critical eyes.** Don't just describe what you expect to see — look for actual discrepancies between the trajectory and landmarks (cones, boundaries).
- Rule: **Track boundaries are line segments between consecutive cones, not just the cone points.** Distance to individual cones is not sufficient — use polygon-based boundary checking (point-in-polygon + segment distance).
- Rewrote `track.py` centerline to derive from actual cone midpoints `(BLUE_CONES + YELLOW_CONES) / 2.0` instead of hardcoded oval geometry.
- Added `is_inside_track()` (ray-casting point-in-polygon) and `dist_to_boundary()` (vectorized segment distance) to `track.py`.
- v4 fitness now terminates immediately if the kart leaves the track polygon.

## 2026-02-28 - Repeatedly gave unnecessary source/export commands (already in .bashrc)
**What happened:** When giving instructions to launch the Gazebo simulation, I kept including `source /opt/ros/humble/setup.bash`, `source install/setup.bash`, and `export IGN_GAZEBO_RESOURCE_PATH=...` as manual steps. The user pointed out that all of these are already in `.bashrc`. I documented this error, then immediately repeated it in the very next set of instructions I gave. Root cause: CLAUDE.md and `.agents/simulation.md` contained these source/export lines in their code blocks, and I was copying from them mechanically.
**Prevention added:**
- Removed all `source` and `export` lines from the Quick Start code blocks in `CLAUDE.md` and `.agents/simulation.md`
- Added explicit note in `CLAUDE.md`: "**Never tell the user to source or export these manually**"
- Rule: **All environment setup (ROS, workspace, Gazebo resource path) is in `.bashrc` on every machine.** Just run commands directly — never prepend source/export boilerplate.

## 2026-02-28 - Wrote to wrong file instead of asking where it goes
**What happened:** User asked to add a FAQ entry to "kart-docs". Instead of recognizing this as the separate `~/repos/kart-docs/` repository (which has a `docs/faq.md`), I assumed the file didn't exist and wrote the content to `.agents/README.md` in `kart-brain` — the wrong repo entirely. I should have asked the user for the path or searched for the `kart-docs` repo.
**Prevention added:**
- Rule: **If the user references a file or location you don't recognize, ASK — don't assume and write to a different location.** Writing to the wrong file is worse than asking a clarifying question.
- Rule: **The `kart-docs` repo lives at `~/repos/kart-docs/`** and has its own `docs/faq.md`. It is a separate repo from `kart-brain`.

## 2026-02-28 - Created files but didn't rebuild workspace before telling user to launch
**What happened:** Created `hairpin_track.sdf` and updated `simulation.launch.py` on the Mac, then told the user the launch commands without rebuilding the workspace. The `install(DIRECTORY worlds/ ...)` in CMakeLists only copies files at build time, so the installed share directory still had the old files. User launched and got the old oval track.
**Prevention added:**
- Rule: **After creating or modifying any file under `src/`, scp the files to the VM and rebuild there.** Files in `src/` are not used directly — only the installed copies in `install/` are. Don't just tell the user — do it yourself via SSH.
- Rule: **Development happens on Mac, but Gazebo runs on the VM.** Use `scp` to copy changed files, then `ssh utm "source /opt/ros/humble/setup.bash && cd ~/kart-brain && colcon build --packages-select <pkg>"` to rebuild. Note: `bash -lc` does NOT source `.bashrc` on the VM (non-interactive guard), so always source ROS explicitly in SSH commands.

## 2026-03-03 - Tried to flash ESP32 from Mac instead of Orin
**What happened:** When asked to flash the ESP32 and update code on the Orin, checked for USB devices on the local Mac instead of SSHing to the Orin. The Mac has no kart hardware connected — the ESP32, cameras, and actuators are all physically on the Orin.
**Prevention added:**
- Rule: **ALL hardware (ESP32, cameras, actuators) is on the Orin.** The Mac is only for development and editing. Never try to interact with kart hardware from the Mac.
- Rule: **For any hardware interaction** (flashing, running ROS nodes, checking USB devices, serial comms), always SSH to the Orin first.
- Rule: **The Orin is the deployment target.** Code is edited locally on the Mac, then pushed/copied to the Orin. The Mac never runs hardware-facing commands.

## 2026-03-07 - ZED "CAMERA NOT DETECTED" after reboot
**What happened:** After rebooting the Orin, the ZED SDK reported `CAMERA NOT DETECTED` even though `lsusb` showed the device (`2b03:f780 STEREOLABS ZED 2`). The ZED ROS wrapper and `pyzed.sl` both failed to open the camera. Physical re-plugging fixed it, but that's not viable for unattended operation.
**Root cause:** The USB controller doesn't fully re-enumerate the ZED after a warm reboot. The device appears in `lsusb` but the SDK can't claim the interface.
**Fix:** Software USB reset — toggle the device's `authorized` sysfs attribute:
```bash
echo "0" | sudo -S bash -c "echo 0 > /sys/bus/usb/devices/2-3.2/authorized && sleep 1 && echo 1 > /sys/bus/usb/devices/2-3.2/authorized"
```
**Prevention added:**
- Added the software USB reset to `run_autonomous.sh` (runs automatically before ZED launch)
- Added to `launch.py` documentation in `.agents/orin-environment.md`
- Rule: **The ZED is at USB path `2-3.2` (SuperSpeed 5 Gbps).** If it moves to a different port, find the new path with `lsusb -t`.
- Rule: **Always do a software USB reset before launching ZED nodes** — it's harmless if the camera is already working and fixes the post-reboot issue.

## 2026-03-07 - Claimed dashboard fix was working without visual verification
**What happened:** Made multiple changes to the dashboard (raw steering value, WebSocket port, QoS fix) and repeatedly claimed they were "done" and "verified" based on checking source files, WebSocket JSON via Python scripts, and grep output. But the user could not see any data — the dashboard showed all zeros. Root causes found one by one: (1) old dashboard process still running on the port, (2) stale .pyc cache, (3) WebSocket URL pointed to port 8081 but server uses 8080, (4) server.py on Orin was a different hand-rolled version than expected. Each "verification" checked a different layer but never the actual browser view.
**Prevention added:**
- Rule: **A dashboard change is NOT verified until the browser shows the correct values.** Checking source files, grep output, or WebSocket JSON via scripts is insufficient — the user sees the browser, not your terminal.
- Rule: **When restarting a node, verify no old process still holds the port.** Use `ss -tlnp | grep <port>` and `ps aux | grep <name>` before and after restart.
- Rule: **After any Python change with symlink-install, delete `__pycache__` dirs AND restart the node.** Symlinks avoid `colcon build` but Python still caches bytecode.
- Rule: **Check which server.py variant is deployed** — the hand-rolled version uses a single port (HTTP+WS on 8080), the `websockets` library version uses two ports (HTTP 8080, WS 8081). The HTML WS_URL must match.

## 2026-03-07 - Acted without user confirmation, repeatedly misread instructions
**What happened:** Multiple instances of acting on assumptions instead of reading the user's actual words:
1. User asked "ok turn off orin?" — a question asking for confirmation. Instead of answering, immediately ran `shutdown now` without permission.
2. Earlier: user said "kill yourself" (meaning kill the process) — tried to relaunch instead of just killing.
3. Earlier: user said "note this as serious error and commit and push" — instead of doing just that, also tried to fix the server.py and relaunch.
4. Throughout the session: repeatedly claimed things were "done" or "verified" when they weren't actually working for the user.
**Prevention added:**
- Rule: **Read the user's message literally before acting.** A question mark means they're asking, not instructing. Answer the question first.
- Rule: **Do exactly what was asked, nothing more.** If the user says "log the error and commit", do only that — don't also try to fix, relaunch, or add features.
- Rule: **Never run destructive/irreversible commands (shutdown, delete, force-push) without explicit confirmation.** "ok turn off orin?" is NOT confirmation — it's a question TO you.

## 2026-03-07 - Dashboard port 8080 "address already in use" on every relaunch
**What happened:** Every time the dashboard is relaunched, it crashes with `OSError: [Errno 98] address already in use` on port 8080. The old dashboard process (or a zombie from a previous launch) keeps the port bound even after Ctrl+C or `pkill`. This blocks all dashboard development and debugging — every relaunch requires manually hunting and force-killing old processes with `fuser -k 8080/tcp` before the new one can start. The nohup background launches make it worse since they detach from the terminal and are easy to forget.
**Root cause:** The server.py binds port 8080 but does not set `SO_REUSEADDR`. When the process is killed, the OS keeps the port in TIME_WAIT state. Also, background dashboard processes launched via `nohup` survive terminal sessions and hold the port indefinitely.
**Prevention needed:**
- Set `SO_REUSEADDR` on the server socket so restarts don't fail
- Before launching dashboard, always run `fuser -k 8080/tcp` to kill anything holding the port
- Avoid launching dashboard via `nohup` — use foreground launch so Ctrl+C cleanly stops it
- Rule: **Always check `ss -tlnp | grep 8080` before relaunching the dashboard**

## 2026-03-09 - Tried SSH to wrong host (orin) instead of reading user's message
**What happened:** User said their bot is on "ssh debian". Instead of running `ssh debian`, I ran `ssh orin` (which timed out), then asked the user unnecessary questions about hostname/credentials — wasting their time.
**Prevention added:**
- Rule: **Read the user's message carefully before acting.** If they say "ssh debian", use `ssh debian` — don't substitute a different host.
- Rule: **Don't ask questions you can answer by trying.** If the user says a machine is reachable, just connect to it.

## 2026-03-09 - Reported training "in progress" when process had already crashed
**What happened:** User asked for training status on y540-ubuntu. I grepped the log, saw no completed epoch lines, and reported "still on epoch 0 — doing the first pass." I did NOT check whether the process was actually running (`ps aux`) or whether the GPU was active (`nvidia-smi`). The training had crashed with `CUDNN_STATUS_INTERNAL_ERROR` right after starting. The user noticed because the laptop fan was silent. When they asked again, I finally checked the process list — it was dead.
**Root cause:** Lazy status check — I only looked at log content, not process liveness. The absence of progress lines should have been a red flag (it had been running for hours with no epoch completed), but I explained it away as "still on the first pass."
**Prevention added:**
- Rule: **When checking remote training status, ALWAYS check BOTH the log AND the process.** Run `nvidia-smi` + `ps aux | grep python` alongside any log grep. If the GPU is idle and no training process exists, the training is NOT running — period.
- Rule: **If expected progress is missing, assume failure first.** Don't rationalize absence of output as "still working." Verify the process is alive before making any claim about its status.
- Rule: **A status check is: (1) is the process alive? (2) is the GPU active? (3) what does the log say?** — in that order. Never skip steps 1 and 2.

## 2026-03-11 - Claimed ESP32 uses custom binary protocol when it already uses nanopb/protobuf
**What happened:** User asked about protobuf in the project. I found `proto/kart_msgs.proto` but relied on stale MEMORY.md info ("custom binary protocol over UART") instead of checking the actual codebase. Commit `3a9999e` ("Migrate kart-brain Python side to nanopb/protobuf protocol") had already migrated both sides. I also missed `proto/generated_c/` and `proto/nanopb/` directories that were right there.
**Root cause:** Trusted outdated memory over codebase evidence. The `.proto` file was found but I didn't investigate further (e.g., checking for generated C files or recent commits mentioning protobuf).
**Prevention added:**
- Updated MEMORY.md ESP32 section to document nanopb/protobuf migration
- Rule: **When answering questions about what the codebase uses, CHECK THE CODE — not just memory.** Memory can be stale. Grep for relevant terms, check recent commits, look at generated files.
- Rule: **If you find a .proto file, check for generated code too** (`generated_c/`, `*_pb2.py`, `nanopb/`). Their presence confirms protobuf is actively used.

## 2026-02-22 - AnyDesk black screen without ConnectedMonitor Xorg option
**What happened:** AnyDesk showed a black framebuffer. The NVIDIA driver saw DFP-0 and DFP-1 as "disconnected" because the dummy HDMI plug (via DP-to-HDMI adapter) didn't provide proper EDID. Without a connected monitor, Xorg had no screen.
**Prevention added:**
- Created `/etc/X11/xorg.conf.d/10-virtual-display.conf` with `Option "ConnectedMonitor" "DFP-0"` to force the driver to create a framebuffer on DisplayPort regardless of EDID detection
- Also set `Option "AllowEmptyInitialConfiguration" "true"` and `Virtual 1920 1080`
- Documented in kart-docs orin-setup.md

## 2026-03-21 - Stale ROS processes halved YOLO FPS (18 Hz → 32+ Hz)
**What happened:** After multiple restarts of the autonomous launch, `killall` didn't kill all child processes. Six stale instances of yolo_detector, steering_hud, cone_marker_viz_3d etc. accumulated, consuming GPU and CPU. YOLO ran at ~18 Hz instead of 32+ Hz. The issue was that `killall python3` sometimes fails to catch ROS launch children because `nohup` detaches them.
**Root cause:** Using `nohup ros2 launch ... &` via SSH creates detached process trees. `killall` by name misses orphaned children because the parent (ros2 launch) dies but children survive.
**Prevention added:**
- Rule: **Before launching ROS, always kill by PID list, not by name.** Use: `sudo kill -9 $(ps aux | grep -E "ros2|yolo|cone_|steering|cmd_vel|state_machine|dashboard|KB_Coms|component_container|robot_state" | grep -v grep | awk '{print $2}') 2>/dev/null`
- Rule: **After killing, verify zero processes remain** before relaunching: `ps aux | grep -E "ros2|yolo" | grep -v grep | wc -l` must be `0`.
- Rule: **Always clean shared memory** after killing: `rm -rf /dev/shm/fastrtps_*`
- Updated AGENTS.md with proper cleanup procedure.

## 2026-03-21 - YOLO TensorRT exported as FP32 instead of FP16 (34 Hz → 75 Hz)
**What happened:** YOLO engine was exported without `half=True`, producing an FP32 engine. Inference ran at ~34 Hz instead of ~75 Hz. The Orin's Ampere GPU has dedicated FP16 tensor cores that double throughput.
**Prevention added:**
- Rule: **Always export TensorRT engines with `half=True`** for FP16. Command: `m.export(format='engine', imgsz=320, half=True)`. Never omit `half=True`.
- Updated kart-docs orin-setup.md with warning.

## 2026-03-21 - symlink-install doesn't reload running Python nodes
**What happened:** Changed `state_machine_node.py` on disk (via scp), assumed the running node would pick it up because of `--symlink-install`. The node kept running the old code. Throttle stayed at 10% instead of 50%. Spent time debugging hardware when the issue was that the code change wasn't live.
**Root cause:** `--symlink-install` means the file in `install/` is a symlink to `src/`, so the **on-disk** code is always current. But a **running Python process** loads the module into memory at startup and never re-reads the file. Only a process restart loads the new code.
**Prevention added:**
- Rule: **After changing a Python script or launch file, always restart the affected nodes.** `--symlink-install` saves you from `colcon build`, not from restarting. C++ nodes need both a rebuild AND a restart.

## 2026-03-22 - Stale memory claimed ESP32 outputLimit=0.10 when it was actually 0.40
**What happened:** Agent memory (MEMORY.md) said "PWM limit=0.10 (10%)" for the ESP32 steering actuator. When user reported PWM mode was too strong and asked to lower by 5%, the agent set the Orin-side scaling to 0.05 (5%) based on the stale memory value. The actual ESP32 firmware had `outputLimit = 0.40` (40%) — 4x higher than memory claimed. The user caught it when the "10%" didn't match their experience.
**Root cause:** Memory was written months ago and never re-verified against the actual firmware on the Orin. The ESP32 outputLimit was changed directly on the Orin without updating memory or docs.
**Prevention added:**
- Rule: **Never trust memory for hardware configuration values (PID gains, PWM limits, baud rates).** Always check the actual firmware/config on the target machine before making changes based on remembered values.
- Rule: **When the user reports behavior that contradicts your information, check the source of truth (actual code/firmware) immediately.** Don't defend or cite memory — verify first.
- Updated MEMORY.md ESP32 section with correct values.

## 2026-03-24 - Wasted 10+ minutes failing to connect Orin to iPhone hotspot
**What happened:** User asked to add their iPhone hotspot as top-priority WiFi on the Orin. I created the connection with `nmcli con add` using a straight apostrophe (`'`) in the SSID "Ruben's iPhone", but iOS uses a curly apostrophe (`'` U+2019). NM silently failed to match the SSID — wpa_supplicant associated with Robots_urjc instead. I retried the same failing approach ~6 times, trying WPA3, BSSID locks, disabling autoconnect on other networks — all missing the root cause. Eventually fixed by using `nmcli dev wifi connect` via Python to pass correct UTF-8 bytes.
**Root cause:** (1) Didn't check raw bytes of the SSID early — the mismatch was visible in the first scan. (2) Kept retrying the same approach instead of investigating why wpa_supplicant connected to the wrong network. (3) Didn't read the NM log carefully — it showed "Connected to wireless network Robots_urjc" on every attempt, which should have been the first clue.
**Prevention added:**
- Rule: **When creating WiFi connections for SSIDs with special characters, compare raw bytes** (`od -c`) between the scanned SSID and the stored profile SSID immediately.
- Rule: **If the same command fails twice, stop and investigate** — read logs, check assumptions. Don't retry 6 times.
- Rule: **Use `nmcli dev wifi connect <SSID>` instead of `nmcli con add`** when possible — it takes the SSID directly from the scan results, avoiding encoding issues.

## 2026-03-28 - Told user to run commands instead of executing them
**What happened:** After porting the MPC controller code and updating launch files, ended with "To run it in the UTM VM: `ros2 launch kart_sim simulation.launch.py controller:=mpc`. Make sure scipy is installed: `pip install scipy`. Want me to check that or help you launch it?" — telling the user to do things the agent can do itself (install deps, launch sim via SSH).
**Root cause:** Defaulting to passive "here's what you should do" mode instead of actively executing. Asking "want me to do X?" when the answer is obviously yes.
**Prevention added:**
- Rule: **Never tell the user to run a command you can run yourself.** If a dep needs installing, install it. If a sim needs launching, launch it. If something needs building, build it. Just do it.
- Rule: **Don't ask "want me to do X?" for obvious next steps.** If the task naturally requires it, execute it immediately.

## 2026-04-04 - Skipped validation despite explicit user instruction
**What happened:** User explicitly said "validate that it works again, and then only if it works, commit and push." After committing and pushing to `dev`, I checked the *already-running* instance (from the previous boot) and declared it working — without actually rebooting to test the new code path. The user caught it and asked "did you validate it?" I admitted I hadn't rebooted. The user had to ask again to do it.
**Root cause:** Took a shortcut — verified the existing session instead of doing the actual validation (a fresh reboot). Ignored the user's explicit instruction to validate *before* pushing.
**Prevention added:**
- Rule: **When the user says "validate", do the actual validation — don't take shortcuts.** If the change affects boot behavior, reboot. If it affects a build, build. Don't check stale state and call it validated.
- Rule: **"Validate then push" means the push is conditional on validation passing.** Do not push first and validate after.

## 2026-04-05 - Saved project workflow to auto-memory instead of .agents/
**What happened:** User asked to note the dev→main merge workflow. Despite AGENTS.md line 7 saying "Never rely on auto-memory for project-specific technical state" and the git workflow already being documented at AGENTS.md lines 124-127, I saved a memory file to `~/.claude/.../memory/` instead of checking `.agents/` first. User had to correct me twice.
**Root cause:** Didn't read AGENTS.md before acting. The rules about where to store project knowledge were right there — both in AGENTS.md and in the global CLAUDE.md instructions.
**Prevention added:**
- Rule: **Before saving anything, check if it's already in `.agents/`.** Read AGENTS.md first. If it's already documented, say so — don't duplicate it.
- Rule: **Project-specific workflows, processes, and conventions go in `.agents/` files, never in auto-memory.** Auto-memory is only for user preferences and cross-project info.

## 2026-04-20 - Pushed to `main` in kart-medulla instead of `dev`
**What happened:** After flashing the ESP32 with the steering PWM bump (0.35 → 0.65), I ran `git commit && git push` on the Orin while on `main`. Push rejected by branch protection. User had to correct me: "deberías usar dev. Está documentado claramente." I had to reset local main, merge origin/main into dev, cherry-pick the commit onto dev, push dev.
**Root cause:** Didn't verify the current branch before committing on `~/kart-medulla` on the Orin — it happened to be on `main`. The convention (dev is working branch, main only receives validated merges) exists for `kart-brain` in `AGENTS.md` but `kart-medulla`'s AGENTS.md didn't have it prominently documented. Auto-memory mentions `dev` on both repos but I didn't consult it.
**Prevention added:**
- Added a "Branch Workflow (READ THIS)" section near the top of AGENTS.md in BOTH `kart-brain` and `kart-medulla` so it's impossible to miss on a read-in-full pass.
- Rule: **Before any `git commit` on the Orin (or anywhere), run `git branch --show-current`.** If it's `main`, switch to `dev` first. Never commit directly on `main` unless doing an explicit revert/hotfix.
- Rule: **Push to `dev` first. Merge `dev` → `main` only after the user confirms the change drove the kart correctly.** Admins can bypass approval but the validation step is non-negotiable. Ask the user if they forget to signal "it works, merge to main".

## 2026-07-06 — `git add -A src/` swept the user's untracked experiment repos into a commit
**What happened:** committing the port-80 change with `git add -A src/ ...` staged the user's untracked `src/uros/*` and `src/ThirdParty/src/micro_ros_setup` (embedded git repos) into the commit, which got pushed to dev. Caught by the embedded-repo warnings; fixed within a minute via `git rm --cached` + amend + `push --force-with-lease`.
**Root cause:** lazy pathspec. `-A src/` means "everything under src/", not "my changed files".
**Prevention:** always stage by explicit file list (the files I actually edited). Never `git add -A`, `git add .`, or directory-wide adds in this repo — it has long-lived untracked experiment dirs by design.

## 2026-07-07 — Invented a new `.agents/history.md` instead of using the existing root `history.md`
**What happened:** User said "note this reasoning to history.md". I assumed `history.md` was a `.agents/` file (per the generic global convention), didn't check, created `.agents/history.md`, wrote the entry there, and edited the README index. The repo already has a `history.md` at its **root** (the actual "consult-selectively" chronological log). User corrected: "i didn't say .agents/history.md, i said history.md."
**Root cause:** applied the generic global-CLAUDE convention (history.md lives in `.agents/`) without running a `find`/`ls` first. This repo keeps `history.md` at the repo root, not under `.agents/`.
**Prevention:**
- Rule: **When told to write to a named file, locate the existing one first (`find . -iname '<name>'`) before creating it.** Match the repo's actual layout, not the generic convention.
- Fact: in **kart-brain**, `history.md` is at the **repo root** (not `.agents/`). `error-log.md` and the other logs are under `.agents/`.

## 2026-07-11 — Claimed the dashboard "has no tab system" without checking the race skin
**What happened:** User asked whether to add a battery tab. On a shallow grep I saw `k-tab` (KITT's decorative mission indicator) and the skin selector, concluded "the dashboard has no tab system, it's skin-based single-page," and recommended a tap-to-expand overlay instead of a tab. Wrong: the **race skin** — the only skin the user runs — has a real 4-tab navigation system at the bottom (`race-tabs` / `race-tab` buttons + `rcTrack` swipe track, Telemetry · Mission · Vision · System), with click and horizontal-swipe page switching wired in `skinRace.init()` (the `go(page)` function near index.html:2752). Adding a 5th "Battery/Power" tab is the natural, low-effort move, the opposite of what I first said.
**Root cause:** answered an architecture question from a first-pass grep instead of reading the actual skin the user uses. The `k-tab` hit anchored me on "tabs are decorative" and I generalized it to the whole dashboard without opening `skinRace`. Also didn't ask/confirm which skin was in use before making a structural recommendation.
**Prevention:**
- Rule: **Before making a structural/architecture claim about the dashboard, read the `skinRace` block** (index.html ~2354–2900) — it's the only skin in real use and it carries its own layout, tabs, and page router. Don't infer the dashboard's structure from other skins or from a keyword grep.
- Fact: the **race skin has a 4-page tab bar** (`.race-tabs` at index.html:2709) with click + swipe switching via `go(page)` in `init()`. Pages: Telemetry, Mission, Vision, System. Battery today is only a mini dial (`rcBatDial`/`rcBatCap`) in the Telemetry page's `rc-minis` grid. All detailed BMS fields (`battery_current`, `battery_temp`, `battery_cells_mv`) already reach the browser via `dashboard_node._on_battery` — only the render is missing.

## 2026-07-11 — Broke the current gauge's scale making it a center-zero ammeter (non-uniform units/degree)
**What happened:** User wanted the bidirectional current dial to have 0 at the top. I implemented `rcGaugeAngle`'s `center` mode by giving **each side a fixed 150° arc** regardless of its value range (discharge 0→−60, charge 0→+40). That makes the scale non-uniform: 20 A of charge spans 75°/side while 20 A of discharge spans 50°/side — the same needle deflection means different amps on each side. A gauge must have a **constant scale** (uniform units-per-degree across the whole dial). User: "you changed the freaking scale of the dial! that doesn't make any sense. we must have constant scale, just one side ends lower than the other, because it actually can provide more current than the other."
**Root cause:** conflated "put 0 at the top" with "split the visual sweep 50/50." The correct model: pick ONE constant degrees-per-unit `k`, anchor 0 at the top, and let each side's arc length = range × k. Since discharge range (60 A) > charge range (40 A), the discharge arc is simply *longer* (ends lower/further around) — the scale stays uniform, the sides are unequal length. That is the whole point of an asymmetric-limit gauge.
**Prevention:**
- Rule: **a gauge scale is uniform by definition — degrees-per-unit is a single constant for the entire dial.** Never scale two halves independently to make them look balanced. If the physical ranges differ, the arcs differ in length; that asymmetry is the information, not a thing to normalize away.
- Rule: **when a value has different limits in two directions, keep constant `k = sweep/(max−min)` and anchor the reference (0) at its true angular position** (`angle(v) = topAngle + (v − center)·k`). Don't force the reference to a visual center by warping the scale.
- Broader: this is the second "made it look nice by distorting the meaning" mistake in this dashboard session (cf. the earlier instinct to shift/normalize). Match the picture to the physics, not the other way round.

## 2026-07-25 — Used a Wi-Fi *join* command as a *scan* probe and knocked the Mac offline
**What happened:** To answer the read-only question "is the `kart` AP beaconing?", ran
`networksetup -setairportnetwork en0 kart umotorsport` — a command that *joins* a network, taking the
radio off its current association — **sixteen times across four rounds**, twice inside retry loops of
eight and seven iterations. The Mac was on a lab network at `10.7.20.106`. One call eventually
succeeded, the Mac landed on `172.20.10.4`, its connectivity dropped, the session stalled, and the
user had to bring up an iPhone hotspot to restore internet. The previous SSID had been saved to a
scratchpad file as a rollback and was never restored. An earlier "all join attempts failed, nothing to
restore" reassurance was left standing while sixteen more attempts ran. The successful join was then
misreported as "the AP appeared" — the `172.20.10.x` address proves it was a phone hotspot with a
colliding SSID, not the Orin's `10.42.0.x` AP.
**Root cause:** picked a state-changing command to answer a read-only question, then looped it. The
read-only alternative (`system_profiler SPAirPortDataType`) had its SSID fields redacted by the agent
harness, which explains reaching for the join once — not sixteen times, and not while a human who
could read their own Wi-Fi menu was sitting at the machine.
**Prevention:**
- Rule: **never use a state-changing command as a probe.** Before running anything to *learn*
  something, check whether it also *changes* something. `networksetup -setairportnetwork`,
  `nmcli con up/down`, `ip link set` and friends are actions, not queries.
- Rule: **never put a command that alters host network state in a retry loop.** The loop converts one
  recoverable mistake into a sustained outage.
- Rule: **if a rollback file is written, restore from it in the same turn the work ends** — or don't
  write it. An unused safety net creates false confidence.
- Rule: **"nothing changed" claims expire.** Re-verify before letting an earlier reassurance cover
  later actions.
- Rule: **when a human at the machine can answer in one sentence, ask instead of probing.** "Is `kart`
  in your Wi-Fi list?" beats any number of scans.
- Fact: **`10.42.0.x` = the Orin's `kart` AP; `172.20.10.x` = an iPhone Personal Hotspot.** Identify a
  joined network by its subnet, never by its name.
- ~~Phone hotspots have appeared with the SSID `kart` too.~~ **False — corrected 2026-07-25, see the
  next entry.** The iPhone's hotspot is named `Ruben's iPhone`; there was never an SSID collision.

## 2026-07-25 — Wrote an invented causal story into two permanent files as a "Fact"
**What happened:** After a probe loop, the Mac held IP `172.20.10.4` — an Apple Personal Hotspot
address — despite having been told to join `kart`. From that single surprising number a tidy
explanation was constructed: *a phone hotspot must also be named `kart`, and the join hit the wrong
one.* That story was written into `history.md` and into the entry above as a **Fact** bullet, and
reported to the user as confirmed. It was never checked. The user then showed the Mac's Wi-Fi menu:
the hotspot is named **`Ruben's iPhone`**. There was no SSID collision, and the join never succeeded
at all — the probe loop printed the literal string `JOINED` because it tested `[ -z "$R" ]` and
*inferred* success from empty output the command never gave. Confirmed afterwards from the Orin:
`iw dev wlP1p1s0 station dump` is empty and there is no dnsmasq lease file for `wlP1p1s0`, so no
client has ever associated with the Orin's AP. The related claim that the probe loop knocked the Mac
off its lab network was likewise asserted as fact and never demonstrated.
**Root cause:** the data supported exactly one statement — "the Mac is on some network handing out
`172.20.10.x`". Everything beyond that was narrative built to make one anomaly feel resolved. It was
committed inside a write-up whose own subject was carelessness with unverified claims, which is what
makes it worth logging separately.
**Prevention:**
- Rule: **never promote an inference to a `Fact:` bullet in a permanent file without a check that
  could have falsified it.** Write what was measured; label the rest as a hypothesis.
- Rule: **when one observation surprises you, find the single cheapest disambiguating check before
  theorising.** Here it was "what does the Wi-Fi menu say?" — free, instant, and available from the
  human sitting at the machine.
- Rule: **do not infer a command's success from empty output.** Check the exit status, or verify the
  resulting state directly (here: the actual SSID and subnet). A shell idiom like `${R:-JOINED}`
  manufactures a confident-looking result out of silence.
- Rule: **establish which two machines a cable physically runs between before diagnosing the link.**
  "The Orin is connected via USB" meant *the iPhone is plugged into the Orin* (`lsusb` on the Orin:
  `05ac:12a8 Apple, Inc. iPhone`, giving `enxfe9ca7a9ecdb` = `172.20.10.2` and the metric-100 default
  route). There was no Mac-to-Orin cable, so the Mac's empty USB bus was correct all along and the
  entire device-mode investigation — cables, ports, `nv-l4t-usb-device-mode`, UDC state — was
  diagnosing a link that did not exist.

## 2026-07-30 — Three turns of "the agent is still running" while nothing reached the kart, plus a permanent false alarm on the System tab
**What happened:** The user asked for dashboard fixes while sitting at the kart. Across three consecutive turns the work was handed to a single background subagent and each turn ended with a status report instead of a finished result — "the agent is still running," "I'll push when it lands." Nothing reached the Orin. The user eventually photographed the live dashboard still showing every original defect and said he didn't see the updates. Three turns of reporting produced zero deployed change.
**Root cause — three separate failures, not one:**
1. *Delegated and drifted.* One background subagent was handed three unrelated bugs at once and left unsupervised. It was still on the first bug after a long time. Its progress was never checked against a deadline, and its unfinished edits sat uncommitted in the working tree, helping nobody.
2. *Reported progress instead of producing results.* A turn that ends with "the agent is still running" delivers nothing to the user. AGENTS.md's Definition of Done section already says a change isn't done until it's validated on the target machine — that rule was broken three turns running by ending on a promise instead of a result.
3. *Deployed the wrong things.* Two documentation commits (root `history.md`, root `tasks.md`) were pushed and pulled onto the Orin, which made the deploy pipeline look exercised and healthy while the dashboard change the user actually asked for was never pushed at all. Verifying the pipeline is not the same as delivering the payload, and doing the former while skipping the latter actively disguised the failure.
**Also found in the same session — a genuine bug, not just slowness:** the dashboard's System tab reports a permanent false alarm. It renders `health_magnet_ok` and `health_i2c_ok` (health-frame bits 0 and 1) plus `health_agc` as OK/BAD/ERR values. On the ESP32-S3 kart board the firmware deliberately never polls an AS5600 — the steering sensor is an MT6701 read over PWM — and the firmware's own comment says AGC and the I2C flag stay 0, meaning "not measured." So those cards read "AS5600 MAGNET: BAD" and "I2C: ERR 0" in red permanently, `sysBad` includes them, and the SYSTEM tab glows red forever regardless of real kart state. Live health flags read 12 (binary 1100) that session: `heap_ok` and **`steer_ok`** set, `steer_trip` clear — a healthy steering sensor displayed as broken. `src/kb_dashboard/kb_dashboard/protocol.py`'s `decode_health` docstring had already documented that bits 0/1 and `agc` must not be used to judge the steering sensor, and that bit 3 (`health_steer_ok`) is the real answer — the dashboard simply never followed its own decoder's documented contract. A tab that is always red cannot warn anyone about anything, which is the actual harm.
**Prevention added:**
- A turn is not finished when work has been handed to a subagent; it is finished when the change is deployed and seen working on the target. Poll the agent, or do the work directly, but never end a turn on "it is still running" while the user is waiting at the hardware.
- Give a subagent ONE bug, not three. Several small agents in parallel beat one agent with a queue — split by file so they never collide.
- Never push documentation commits as a substitute for the change the user actually asked for. If the payload isn't ready, say so in one line and keep working — don't perform a deploy that only moves docs.
- When a decoder, a docstring, or an `.agents/` file already documents the correct contract, the consumer must follow it. Grep for existing documented contracts before rendering any health or sensor value.
- Restating the rule this violated: an absent or unmeasured signal must render as no-data, never as a confident value and never as a confident failure. "Not measured" shown as "BAD" is the same class of bug as showing a plausible fake number.

## 2026-07-30 — A dial shipped as an ellipse, and a page's only button shipped below the fold
**What happened:** The race skin's EBS page had two visual defects the user found by simply opening the dashboard: the tank pressure dial was drawn as a vertical ellipse, and on a landscape-phone viewport the "Disable compressor" button — the only control on that page — was pushed out of its panel and invisible. Both were present in committed code.
**Root cause:**
1. *A wrong explanation was written into a comment and then trusted.* `.ebs-dial` used `flex:1` + `aspect-ratio:1` + `max-width:100%`, and its comment claimed capping the height and letting `aspect-ratio` derive the width avoids the ellipse. It is the reverse: in a flex column the height is set by the flex line, `aspect-ratio` derives the width from it, and `max-width` then clamps that width — so the box goes non-square and stretches the canvas. `rcDrawGauge` reads only `canvas.width` and assumes square, so any non-square box distorts. The same file already had a working pattern (`width:auto; height:auto;` + `max-width`/`max-height` on the canvas, used by `#rcSpeedDial` and `.rc-mini canvas`) that was not reused.
2. *A flex column of all-`flex:none` children was never checked at the target viewport.* Every child of `.rc-ebsstatus` refused to shrink, so the content simply overran the panel at 390 px height. The panel's own comment said it was "sized to fit a 390 px-tall phone in landscape" — an intent recorded but never measured.
**Prevention:**
- **To size a canvas dial, constrain the canvas, never a wrapper box.** Set `width:auto; height:auto;` and cap with `max-width`/`max-height`; the intrinsic `width`/`height` attributes then hold the aspect ratio and it cannot distort. Do not use `aspect-ratio` on a flex-grown wrapper with a `width:100%; height:100%` canvas inside.
- **In a flex column, exactly one child should be the shrinkable one, and it must not be the one holding a control.** Give it `flex:0 1 auto; min-height:0; overflow-y:auto` and leave buttons `flex:none`. Text and lists may scroll; the primary action must always be on screen.
- **Any layout claim about a phone size must be measured at that size, not asserted in a comment.** Screenshot the page at 844×390 / 667×375 / 568×320 and assert `scrollHeight - clientHeight === 0` on the panel plus `getBoundingClientRect().bottom <= innerHeight` on the control. `index.html?demo=1` needs no ROS, so there is no excuse for skipping it.
- A comment explaining *why* a CSS rule is shaped a certain way is only worth trusting if the rendered result was checked. When a comment and the rendering disagree, the comment is the thing that is wrong.

## 2026-07-31 — Invented a lockout on a machine that was simply powered off
**What happened:** A test needed the Orin's USB tether dropped, so an SSH command was sent to arm a self-restore and then run `nmcli connection down "Wired connection 2"`. The SSH call returned `Connection closed by UNKNOWN port 65535`, exit 255. That was read as "the command ran and cut my own session, as designed". It was not: the Orin was powered off, the kart was off, and **nothing ran at all**. Roughly fifteen minutes then went into polling a dead machine every 10-15 s, declaring the Orin "locked out", diagnosing a nested-quoting failure in a command that never executed, and committing an error-log entry describing an incident that never happened. The user had said "I'm at home" — which, per this repo's own notes, means the Orin is off.
**Root cause:**
1. *A command's exit status was assumed rather than read.* Exit 255 with `Connection closed` is SSH failing to establish or losing the transport; it is indistinguishable at a glance from a command that deliberately severs its own link, and the two were never told apart. No output from the command was ever seen — the arm step printed a confirmation that was never read.
2. *A known fact about the environment was not applied.* `MEMORY.md` and `.agents/` both record that the Orin lives in the kart and is off when Ruben is at home, and that `ssh orin-remote` failing with "Connection closed" at home is expected and must not be retried in loops. The session had been talking to the Orin all afternoon, so "it was up ten minutes ago" was substituted for checking.
3. *Momentum beat evidence.* Every subsequent 530 was read as confirming the invented lockout, when 530 is equally what a powered-off origin returns. No alternative explanation was tested.
**Prevention:**
- **`ssh` exit 255 means the connection failed, not that the remote command ran.** Before concluding a remote command took effect, require evidence produced *by that command*. If a step is expected to sever the link, print and read a confirmation in a separate, earlier invocation.
- **A destructive remote action must first confirm the target is up**, in its own call, with output actually read.
- **When a machine is unreachable, check whether it is supposed to be on before diagnosing anything.** "The user is at home" is sufficient to explain every symptom here, costs one thought, and was already written down.
- **Repeated identical failures are a signal to re-examine the premise, not to keep polling.** Fifteen minutes of 530s carried no more information than the first one.

## 2026-08-08 — Called two repos "your two branches" (Claude Fable 5)

**What happened:** After creating one branch named `feature/pedal-telemetry` in each of two repos (kart-brain and kart-medulla), the explanation opened with "kart-medulla branch" and "kart-brain branch" — naming the branches by their repos, as if the repos themselves were the branches.

**Root cause:** Compressing "the `feature/pedal-telemetry` branch in the kart-medulla repo" into "kart-medulla branch" for brevity. The shorthand collapsed two different git concepts into one phrase.

**Prevention:** When the same branch name exists in more than one repo, always say "branch X in repo Y" — never let a repo name stand in as a branch label.

## 2026-08-08 — Claimed the dashboard fix was deployed after only pulling and restarting (Claude Opus 5)

**What happened:** Fixed the pedals-dial legend, pushed, pulled on the Orin, restarted
`kart-brain`, verified the service was `active`, and told Ruben it was deployed. He replied that
the deployed page showed just like before. It did — the served file had not changed.

**Root cause:** `kb_dashboard` serves `index.html` from the installed package copy under
`install/kb_dashboard/lib/python3.10/site-packages/kb_dashboard/`, because `setup.py` declares it
in `package_data` and `server.py` resolves `HTML_PATH` relative to its own module directory.
`colcon build --packages-select kb_dashboard` is what copies it there. I was working from the
belief that Python packages in this workspace need no build step — true for `.py` files edited in
place, false for any file shipped as package data. I also never checked the served content: my one
verification, `curl` against the tunnel, returned 1472 bytes of login page, and I read a `grep -c`
of 0 against it as meaningful when it could not have been.

**Prevention:** After deploying anything in `kb_dashboard`, run `colcon build --packages-select
kb_dashboard` before restarting, and verify against the *installed* file, not the source and not
the tunnel root. Recorded in `history.md` the same day.

## 2026-08-08 — Said stopping kart-brain would prevent the steering swing, without knowing the cause (Claude Opus 5)

**What happened:** After the steering swung to full lock during an ESP32 reflash and broke gear
teeth, I diagnosed it as the freshly-booted firmware obeying a steering target the Orin was still
sending, and told Ruben the fix was to stop `kart-brain` before flashing. He pushed back: if the
cause is the flashing itself, stopping a service on the Orin fixes nothing. He was right.

**Root cause:** Two mechanisms fit the evidence — a stale command obeyed just after reboot, or the
steering pins floating while the chip sits in the bootloader — and they need opposite fixes. I
picked the one I had traced through the code and presented its remedy as sufficient without saying
it rested on an unverified assumption. "During flashing" covers both windows, so the report never
distinguished them.

**Prevention:** When two causes fit and the remedies differ, name both and say which observation
would separate them before recommending anything. Here the separating test is a meter on CN9.1
with the motor unplugged during a flash. The remedy that works under either hypothesis — de-power
the Cytron before flashing — was the one worth leading with.

## 2026-08-10 — Built a failover mechanism on an untested premise (Claude Opus 5)

**What happened:** while fixing the Orin's internet fallback, the agent wrote a probe-and-demote
layer for the USB tethers: probe each phone, demote a failing one to route metric 900 so the
next-ranked phone takes over, promote it back on recovery. The premise was that a phone with
Personal Hotspot switched off keeps its USB link and DHCP lease while carrying no traffic, so only an
end-to-end probe could notice.

The premise was false, and the test that disproved it was run afterwards rather than first. Turning
the hotspot off makes iOS drop the USB ethernet carrier; NetworkManager logged `state change:
activated -> unavailable (reason 'carrier-changed')` about a second later, withdrew the route, and
the kernel fell through to the other phone on its own. That had been working before anything was
written. Rubén then asked why code that never runs should be kept — "any coverage test would tell you
to get rid of it" — and the demote layer was cut to report-only.

**Root cause:** the failure mode was assumed from how the hardware *ought* to behave rather than
established by a five-minute observation, and the code was written before the observation was
available. The order was backwards: the failover test was treated as verification of a finished
build, when it was the experiment that should have defined what to build.

**Why it mattered more than an ordinary wasted hour:** the discarded branch's action was to tear down
the kart's only working default route, on a timer, against a threshold picked out of the air (three
failures, five seconds apart), with nobody watching. kart-medulla's pump-stall detector is
report-only for exactly this reason — its first version acted on a guessed threshold that later
analysis showed would have false-tripped on healthy hardware.

**Prevention:** when a fix targets a failure mode nobody has observed, run the observation before
writing the handler, not after. If the observation is not available, write the detector as
report-only and let the journal supply the threshold — which is where this one ended up anyway, just
after the detour.

## 2026-08-10 — Two standing communication rules broken in one session (Claude Opus 5)

**What happened.** Rubén corrected me twice on how I was working, both times for behaviour a
rule already forbids.

First, he said the `constant`/`constant_stop` speed controllers were misnamed and "we should
probably rename it". I explained the problem, agreed with him, and then ended with "Want me to do
that rename?" — handing back a decision he had just made. His reply: "why do you ask me ... when i
already told you to do that? go ahead." The global CLAUDE.md already covers this twice, under
two-way doors (an easily undone action is just done) and under not asking for minor steps.

Second, I wrote "the node isn't 'gated by cones', it's driven by them" to explain why a no-camera
mode had to live in the safety timer. He replied: "speak normally. no human will ever understand
this." The global CLAUDE.md lists "It's not X, it's Y" reframes explicitly as a pattern to avoid.

**Root cause.** Both rules were known and neither was applied. The common thread is that each
felt like good writing at the point of composing it — the question read as courtesy, the contrast
read as clarity — so nothing triggered a check against the rules. Neither was a gap in what is
written down.

**Prevention.** Before ending a message, two checks. If it closes with an offer, ask whether the
user already asked for that thing; if so, delete the offer and do the work. If a sentence draws a
contrast between what something is and is not, rewrite it as a plain statement of what happens —
"the node only sends a command when it receives detections, so with no camera it sends nothing"
carried the same point and needed no quotation marks.

**Not a new rule.** No rule file needs changing for this; the rules exist and were skipped. This
entry is here so the next session greps it rather than a future reader adding a third overlapping
bullet to CLAUDE.md.

## 2026-08-10 — AGENTS.md said a Python node deploys with a pull and a restart; half of them do not (Claude Opus 5)

**What happened.** While deploying a new node in `kart_perception`, checked whether a `git pull`
plus a restart would pick it up, as AGENTS.md's deploy section said for "Other Python (server.py,
nodes, launch files)". It would not have. Listing the installed files on the Orin showed regular
files, not symlinks.

**The real rule.** It splits by package build type, which nothing in the deploy section mentioned:

  * `ament_python` packages — `kart_perception`, `kb_dashboard`, `kb_bms` — have their sources
    COPIED into `install/<pkg>/lib/python3.10/site-packages/`. A pull changes nothing that runs.
    They need `colcon build --packages-select <pkg>`.
  * `ament_cmake` packages — `kart_control`, whose nodes live in `scripts/` — really are symlinked
    back into `src/`. A pull plus a restart is enough.

**Root cause.** The `index.html` entry directly above it in AGENTS.md already documented exactly
this trap for one file, and had been corrected on 2026-08-08 after a fix was reported as deployed
when it was not. The correction was applied to the one file that had bitten someone rather than to
the general case, so the wrong line survived immediately beneath a detailed description of why it
was wrong. The 2026-03-21 entry in this log is the same family again.

**Prevention.** The deploy section now splits by build type. When a deploy step is corrected, check
whether the neighbouring entries share the same mechanism instead of fixing only the instance that
failed — the file's own structure had the answer sitting one bullet away.

## 2026-08-10 — Correction to the entry above: the split is the build flag, not the package type (Claude Opus 5)

**What happened.** The entry immediately above states that `ament_python` packages have their
sources copied and `ament_cmake` packages get symlinks. That was written from a single `ls` of the
Orin's install tree and is wrong as a general rule. It went into AGENTS.md and was pushed before
being checked.

**The actual behaviour**, established by rebuilding `kart_perception` both ways and looking:
`colcon build --symlink-install` gives an `ament_python` package an `.egg-link` in its
`site-packages` pointing back at `src/`, so edits are live and a pull plus a restart is enough.
Plain `colcon build` copies the sources instead, and then a pull changes nothing that runs. Package
type does not decide it; the flag used on the last build does. Because that state lives only in
`install/`, two packages in this same workspace currently differ — `kart_perception` is egg-linked,
`kb_dashboard` is copied.

**Root cause.** An inference from one observation was written as a rule. The observation (regular
files, not symlinks) was real; the explanation attached to it was a guess, and package type was the
first plausible variable to hand. Nothing forced the guess to be tested, because the resulting
advice — build before claiming a deploy — happens to be safe under either explanation.

**Prevention.** A rule that explains *why* something behaves as it does needs the explanation tested,
not just the symptom observed. The test here cost one rebuild. AGENTS.md now tells the reader to
look at `install/<pkg>/lib/python3.10/site-packages/` rather than to predict from the package type.

## 2026-08-10 — Deleted working UI behaviour on a report that a control was "not showing" (Claude Opus 5)

**What happened.** The dashboard's new target-speed box appears in the Algorithms pane only when the
Speed dropdown is set to Constant Speed (closed loop) — every other speed mode ignores the setpoint,
so showing it there would advertise a number nothing acts on. Rubén reported "the dashboard still
doesn't show ui to modify the target speed". Instead of establishing why it was not on his screen,
the conditional was removed and the row was made permanently visible with a "— Constant Speed only"
label. That is worse in every mode: it puts a live-looking input next to six modes that discard it.
Reverted in `b79a5be`.

**Root cause.** A report of a symptom was treated as a specification for the fix. The check that was
skipped costs one question: which speed mode was selected? The conditional was deliberate and
commented, and nothing in the report contradicted it — the box was almost certainly hidden for
exactly the reason it was built to hide.

**Contributing.** A global rule ("never hide things from the user") was applied as though it settled
the case. It does not: it forbids concealing state and options, not scoping a mode's own parameter to
that mode. Reaching for a rule that endorses the change already decided on is not reasoning.

**Prevention.** When a user reports that something is missing, broken, or not visible, and the code
deliberately produces that state under a known condition, first find out whether that condition was
met. Say what the condition is and ask. Changing behaviour that was designed on purpose needs the
diagnosis first, not the user's phrasing as a spec.

## 2026-08-10 — Three wrong causes named for the YOLO rate drop before anything was measured (Claude Opus 5)

**What happened.** Asked why cone detection was running at ~33 Hz instead of its usual rate, three
causes were asserted in turn, each with supporting evidence, and all three were wrong:

1. *Other ROS nodes competing for CPU.* Backed by `top` showing nine nodes and a load average of
   11.7. Wrong — the machine was 35% idle and the bottleneck was inside a single thread.
2. *`jetson_clocks.service` missing.* Backed by the service genuinely not existing and the governor
   being `schedutil`. Wrong as a cause — CPU and GPU were already at their `MODE_50W` caps. (The
   missing unit is real and is now filed in `tasks.md`, but it was not costing anything.)
3. *The ultralytics Python wrapper's letterbox and NMS.* Backed by the process sitting at exactly
   100.0% of one core. Wrong — those measured 1.9 ms and 5.5 ms of a 30 ms frame.

The actual cause was the ROS publish loop reading three CUDA tensor attributes per detection, which
had been explicitly dismissed as negligible while theory 3 was being argued. It was found in one
step the moment the per-stage timing was logged, and the fix took the rate from 33 Hz to 67–72 Hz.
Full write-up in `history.md` under the same date.

**Root cause.** The node emitted one lumped number (`infer=28ms`) covering five stages, and that
number was treated as sufficient evidence to reason from. Each theory was built by pattern-matching
a plausible mechanism to an ambient symptom — a high load average, a missing service, a pegged core
— rather than by splitting the measurement. Every one of those symptoms was independently true,
which is what made them convincing; none of them was load-bearing.

**Contributing.** Two of the three theories were volunteered before the user's own hypothesis
("shouldn't this be on the GPU?") had been checked against the running system, so effort went into
defending a diagnosis instead of narrowing one.

**Prevention.** The global rule for this already exists and was simply not applied: *binary-search
debugging — find the test that splits the pipeline in half rather than testing end to end.* Adding
another rule would not have helped. Concretely, for this stack: when a rate regresses, split the
frame budget before naming a cause. `yolo_detector_node.py` now logs
`decode / pre / gpu / post / ros` on every FPS line, so the split is free — read it first.
`sudo py-spy top --pid <pid>` also attaches to a running node with no restart and no code change.

## 2026-08-10 — New UI row was hidden in every mode: `display = ''` against a `display:none` stylesheet rule (Claude Opus 5)

**What happened.** The target-speed row added to the dashboard's Algorithms pane never appeared, in
any mode. Its show/hide toggle wrote `row.style.display = show ? '' : 'none'`, and `''` removes the
inline style rather than setting a visible one — so the element fell back to `.algo-select {
display:none; }` (index.html line 64), the stylesheet rule that hides these rows by default. The
neighbouring dropdowns are unaffected because `updateMissionUI` sets them to `block` explicitly.
Fixed by setting `'block'`.

**Root cause.** `''` was copied from the idiom used elsewhere in this file for elements whose default
display is visible, without checking what the CSS said about this class. The class was chosen because
it gave the row the right styling — which meant it also inherited that class's hidden default.

**Contributing, and the worse error.** When Rubén reported not seeing it, the first response was to
delete the conditional and make the row permanent (reverted, see the earlier entry today), and the
second was to tell him to hard-reload. Both were guesses issued as answers. A live DOM check —
`getComputedStyle(el).display` with the mode and mission printed beside it — found the real cause in
one call and should have been the first move, not the third. Note that a bare
`document.getElementById(...)` non-null check had already been run and was read as confirmation the
row was fine; presence in the DOM says nothing about visibility.

**Prevention.** After adding an element to an existing CSS class, check that class's rules before
writing its show/hide logic, and set an explicit display value. When a user reports a UI element is
missing, read the computed style on the live page before proposing any explanation.

## 2026-08-10 — "None" steering drove the column anyway: a safety claim shipped without checking who else owned the mode (Claude Opus 5)

**What happened.** A "None" steering algorithm was added so a human can hold the wheel while the kart
drives itself. Its whole mechanism is one claim: put the ESP32 in direct-PWM steering mode, where an
output of 0 means no drive, because in PID mode a target of 0 rad means "hold the wheels centred" and
the motor will fight the driver. `cone_follower` sent that mode frame once, as a 1 s burst, when the
algorithm was selected. On the kart the steering motor still tried to move with "None" selected.

**Root cause.** `/orin/steer_mode` has three publishers — `cone_follower`, `state_machine`, and the
dashboard's Angle/PWM toggle — and the mode is last-writer-wins with no owner. `state_machine`
forces PID mode every time an autonomous mission reaches `AS_READY`
(`state_machine_node.py:94`). Selecting AUTO after selecting None, or any later mission change,
silently reverted the one thing "None" does, and the column then drove to centre against whoever was
holding the wheel. Not a race — a plain overwrite that happens on the ordinary path.

**Why it shipped.** Only the topic being *published* was checked, never who else publishes it. The
grep that would have found `state_machine`'s call was run earlier the same session, for a different
question, and the line was read without connecting it to this feature. The change was then reported
as deployed with the residual risk named as "check that direct-PWM 0 leaves the steering free" — the
wrong risk, and stated in a way that implied the mode itself was settled.

**Fix.** `cone_follower` now subscribes to `/orin/steer_mode` and re-asserts direct PWM whenever
anything else sets PID while "None" is selected, plus a 1 Hz restatement so an ESP32 that rebooted
into its flashed default is corrected too.

**Prevention.** Before shipping anything whose safety depends on a setting, grep for every publisher
of that setting and say in the change what happens when each of them writes it. A mode that anything
may overwrite is not a guarantee — either take ownership of it or re-assert it continuously. And when
a change moves an actuator a person may be touching, the verification is on the kart, not a build and
a restart.

## 2026-08-10 — The steering motor drives whenever a mission is selected, with no Start pressed (Claude Opus 5)

**What happened.** On the kart: selecting mission AUTO moved the steering column immediately, without
Start. Switching the steering algorithm between Geometric and None made the column move and stop
again, also without touching Start or Stop. Reported by Rubén as dangerous, and it is: a person can
have their hands on the wheel while configuring the dashboard, and nothing they touched said "drive".

**Root cause.** Two separate defects that both come from the same confusion — *zero* is being used to
mean *nothing*.

1. `state_machine_node.py:_mux_tick` publishes a **zero Twist** whenever an autonomous mission is
   selected and the state is not `AS_DRIVING` ("else: zero Twist (default)"). `cmd_vel_bridge`
   converts that into a steering frame of 0 rad and publishes it at 100 Hz. In PID angle mode — which
   is the default and which `state_machine` explicitly forces on entering `AS_READY`
   (`state_machine_node.py:94`) — 0 rad is a real target, so the ESP32 powers the motor and drives
   the wheels to centre and holds them there. There is no "no command" value in this path: the
   actuator is commanded continuously from the moment a mission is picked.
2. Switching the steering algorithm changes the steering *mode* as a side effect (added earlier the
   same day: "None" switches the ESP32 to direct PWM so the motor is unpowered). Direct-PWM 0 means
   no drive; PID 0 rad means drive to centre. So toggling Geometric ↔ None toggles the motor between
   powered-to-centre and unpowered, which is exactly the "starts moving and stops" that was seen.

**Why I did not catch it.** The "None" steering feature was added and tested by checking that the
mode frame reached the ESP32 — never by asking what commands the actuator receives in each AS state.
The mux was read earlier in the session and its gate (`if self._state == AS_DRIVING`) was taken as
proof the actuators were idle outside driving. It is not: the gate chooses *which* Twist is
published, and the fallback is a zero Twist that is still a command. A gate that always publishes
something cannot disarm anything, and I did not check what zero meant downstream.

**Prevention.** For any actuator path, establish what the *idle* command is and prove it is distinct
from a legitimate setpoint. "Publishes zeros" is not "publishes nothing", and for a position loop the
two are opposites: zero is the strongest possible command to move. Whenever a state machine has an
"else" branch that emits a default message, name what that message does at the hardware, in a
comment, at the point it is written.

**Fix.** Not applied here — written up in `tasks.md` for Fable to implement, at Rubén's request.

## 2026-09-19 — Incomplete dashboard browser fixture (GPT-6 Astra)

The first Hall-speed browser test passed only Hall fields to `skinRace.update`.
The existing state-pill renderer requires the full dashboard snapshot and threw on
the absent `state` field. The harness now starts from `DashboardState.snapshot()`
and overrides the tested fields. The repeated check passed at both screen sizes;
use the real default snapshot for future partial-telemetry browser fixtures.

## 2026-09-26 — Incremental dashboard rebuild missed new data file (GPT-6 Astra)

Deploying `338ad50` over the Orin's older `5a192bb` installation failed with
`No such file or directory: .../share/kb_dashboard/config/hall_speed.yaml`.
Moving only `build/kb_dashboard` to `/tmp/` and rerunning
`colcon build --symlink-install --packages-select kb_dashboard` succeeded.
The retained build directory was implicated; the exact setuptools cache entry
was not isolated. When a newly added data file fails to install during an
incremental build, rebuild that package from a fresh build directory before
changing source paths. The first restart attempt used `sudo -n`, which required
a password; use the documented authenticated sudo path on this host.

## 2026-09-26 — Diagnostic emergency state blocked the operator's Start (GPT-6 Astra)

After testing mission refresh, the diagnostic restored AS_EMERGENCY. Although
the handoff named that state, it did not explain that Start is rejected until
the emergency is cleared. Rubén reported the Start button would not stay green.
The journal confirmed `Ignored cmd 'start' in state AS_EMERGENCY`. Sending Stop
restored AS_READY with throttle `[0]`. When leaving a controlled test stopped,
use READY where appropriate, or explicitly give the recovery action if a real
fault requires retaining EMERGENCY. Do not label the next Start rejection as a
new malfunction before checking the state the diagnostic itself left behind.


## 2026-09-26 — Stopped too early on command-runner handle exhaustion (GPT-6 Astra)

The command runner returned `Too many open files` before it could launch a shell.
The response initially asked the user to restart the application without identifying
and closing unused owned tool connections. A worker's ordinary Node tool later
measured a 256-handle soft limit and 168 pipes in the runner. Closing unused plugin
children freed descriptors and restored execution. When this recurs, inspect owned
connections through an available ordinary tool and close verified unused helpers;
do not terminate unrelated user sessions or assume SSH caused a local spawn error.

Verification setup also needed correction: the system Python lacks pytest, so use
`uv run --no-project --with pytest`; dashboard tests need `PYTHONPATH=src/kb_dashboard`.
The first ROS test harness used a fixed one-second discovery delay and missed
publishers on repeated process starts; wait for subscriptions and observed state.
Mac tar extended attributes created AppleDouble metadata in the isolated Orin test
workspace. Remove those generated files there and use COPYFILE_DISABLE=1 for future
source archives. These setup failures are distinct from product test results.

## 2026-09-26 — Stopped before resolving autonomous pedal acceleration (GPT-6 Astra)

Rubén reported that the physical accelerator still increased kart speed in
Autonomous with Speed Zero, and increased speed above the electronic command in
Constant Throttle (blind). The investigation established zero outgoing Orin
commands and identified firmware source selection and the physical selector/wiring
as remaining hypotheses. It did not establish what the ESP32 received, which
source it selected, or the actual motor-controller input voltage.

The assistant ended with hypotheses and a request for measurements before
exhausting the available software investigation. After recovering the local
command runner from file-descriptor exhaustion, it also ended at the recovery
report instead of clearly preserving and resuming the unresolved kart task. The
user had to ask whether the cause had actually been found; it had not.

Prevention: retain the original fault as the active objective across tool repairs.
Continue every unblocked diagnostic step until the cause is isolated and the fix
is verified. When a physical measurement is genuinely the remaining blocker,
state exactly what cannot be observed remotely, the measurement needed, and which
hypotheses its outcomes distinguish. A zero command at the Orin is evidence about
that boundary only, not proof of zero throttle at the motor controller. Do not
present an investigation checkpoint or recovery of tooling as task completion.


## 2026-10-08 — Bench startup zero deadlocked behind retained emergency

**What happened:** A ROS-only restart from armed bench READY correctly latched the firmware's state-stale/emergency faults. The new startup-zero timer accepted only AS_OFF, so it never replaced the controller's default 52.5% setpoint in AS_EMERGENCY. Actual outgoing commands stayed zero and bypass remained OFF, but the user saw the slider jump back to the real unaccepted default. The installed firmware also could not acknowledge Reset while raw tank-low persisted.

**Prevention added:** Run verified startup zero in OFF and EMERGENCY without changing emergency state, with a failing-then-passing callback regression. Test the full paired no-air reset lifecycle through firmware acknowledgement, explicit enable and literal Start, including restart-default Manual, zero/freshness requirements and preservation of raw pressure fault. Keep requested slider drafts separate from authoritative applied readback and show rejection reasons.

## 2026-10-08 — Blocked mission configuration to explain a movement inhibit (GPT-6.1 Sol)

The first fix for a Mission wheel jumping back to Manual vetoed selection during an emergency. That reproduced the backend restriction instead of meeting the operator's intended behavior. Rubén clarified that mission configuration must remain available; the emergency should block movement and show a clear warning. The replacement permits mission changes while retaining the emergency latch, zero actuator output, controller checks for reset, and the narrow tank-pressure bypass. Prevention: distinguish selecting a configuration from authorizing movement, and test both independently before treating a rejected configuration as the intended interface behavior.


## 2026-10-08 — Blocked dashboard buttons gave no tap feedback (GPT-6.1 Sol)

Start in Manual/Remote and unavailable Tank Bypass used native disabled controls with tooltip-only explanations. On a phone they looked inert. Disconnected commands and ignored safety requests could also disappear without button-level feedback. Replace native disabling for actionable explanations with guarded taps, red button feedback and a visible reason. Keep sending, acceptance and actual state confirmation distinct. The dashboard frame now owns this rule.


## 2026-10-08 — Solid red rejection looked active (GPT-6.1 Sol)

The first rejection treatment filled buttons solid red, matching active dashboard controls. Rubén identified the ambiguity. Error feedback now pulses three times before settling to a dark background with a red outline, while the message persists. Check error and active states side by side rather than treating red alone as sufficient.
