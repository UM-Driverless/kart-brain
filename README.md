# kart-brain

ROS workspace for the UM-Driverless kart software stack.

## Jetson Orin Access

Add this to your `~/.ssh/config`:

```ssh-config
# Jetson Orin via Cloudflare Tunnel — works from anywhere
Host orin-remote
    HostName orin.rubenayla.xyz
    User orin
    IdentityFile ~/.ssh/id_ed25519
    ProxyCommand cloudflared access ssh --hostname %h
    ControlMaster auto
    ControlPath ~/.ssh/sockets/%r@%h-%p
    ControlPersist 10m
```

Requires [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) and an SSH key authorized on the Orin.

- **AnyDesk ID**: `721489674` (GUI access)

### Dashboard access

| URL | Needs | Notes |
|---|---|---|
| `https://kart.rubenayla.xyz` | Internet | Default. Cloudflare Tunnel; works from anywhere. |
| `http://<orin-lan-ip>:9090` | Same WiFi/LAN as the Orin | Local fallback — works if internet dies. The ⓘ button in the dashboard topbar shows the Orin's current LAN IPs and one-click links. |

Default password: `0` (set via the `password` ROS param on `kb_dashboard`).

The hardware speed dial uses the three motor Hall counters from Medulla's extended
health frame. Set `hall_edges_per_metre` in `src/kb_dashboard/config/hall_speed.yaml`
to a measured value: sum the increase in all three counters over a known rolling
distance, then divide by metres travelled. Keep the drivetrain engaged, move in
one direction, and check that all three channels count without multiple-bit changes.
Rebuild `kb_dashboard` and restart the dashboard after changing this installed config.
The default `0.0` means uncalibrated, not zero speed. System → Motor Halls shows the
raw states, counters, rate and calibration. Missing/stale signals show `--` on the
dial; no transitions cannot distinguish a stopped motor from a disconnected sensor.
Speed is an unsigned, roughly one-second average using telemetry arrival times,
not direction or control feedback. All three channels must advance in an interval;
very slow movement can therefore show no speed. Simulation retains its existing source.

To make the LAN path reliable, give the Orin a **static DHCP reservation** on your router (or set a static IP via NetworkManager on the Orin) so the URL doesn't change between sessions.

## Hardware

| Component | Details |
|---|---|
| **Chassis** | Tony Kart Extreme (late 90s, 30/32mm CrMo tubing) |
| **Computer** | NVIDIA Jetson AGX Orin (JetPack 6.2.2, CUDA 12.6, 62 GB RAM) |
| **Camera** | ZED 2 stereo (USB 3.0) |
| **Microcontroller** | ESP32 "Kart Medulla" (UART 115200 baud) |
| **Gamepad** | USB/Bluetooth (deadman switch on R1) |
| **Actuators** | Steering motor (H-bridge) + throttle (DAC) via ESP32 |

See [kart-docs](https://um-driverless.github.io/kart-docs/assembly/) for full hardware documentation, wiring diagrams, and setup guides.

## Install (ROS 2 Humble)
Assumes Ubuntu 22.04 with ROS 2 Humble already installed and sourced.

```bash
./tools/install_deps.sh
```

## Build
```bash
source /opt/ros/humble/setup.bash
colcon build --symlink-install
source install/setup.bash
```

## Launch Files

### Real Hardware (Orin)

| Launch file | Command | Description | Nodes |
|---|---|---|---|
| **launch.py** | `ros2 launch kart_bringup launch.py` | Full pipeline — camera, perception, control, comms, and dashboard. Use `perception:=false` for remote-control only | ZED camera, perception_3d (YOLO + depth localizer), steering_hud, cone_follower, cmd_vel_bridge, KB_Coms_micro, kb_dashboard |
| **dashboard.launch.py** | `ros2 launch kart_bringup dashboard.launch.py` | Comms + web dashboard only — safe for firmware testing, sends no commands to the kart | KB_Coms_micro, kb_dashboard |
| **gui.launch.py** | `ros2 launch kart_bringup gui.launch.py` | HUD viewer window on Orin display (launch separately from autonomous) | hud_viewer |

### Simulation (VM)

| Launch file | Command | Description | Nodes |
|---|---|---|---|
| **simulation.launch.py** | `ros2 launch kart_sim simulation.launch.py` | Full Gazebo simulation with dashboard | Gazebo, ros_gz_bridge, camera_info_fix, perfect_perception (or YOLO pipeline), cone_follower, cone_marker_viz_3d, ackermann_to_vel, esp32_sim, kb_dashboard |

```bash
# Example: autocross track, geometric controller, with Gazebo GUI
ros2 launch kart_sim simulation.launch.py gui:=true controller:=geometric track:=autocross

# Check all available arguments:
ros2 launch kart_sim simulation.launch.py --show-args
```

Arguments: `track:=oval|hairpin|autocross`, `use_yolo:=true|false`, `gui:=true|false`, `controller:=geometric|neural|neural_v2`, `weights_json:=<path>`

### Perception (Testing)

| Launch file | Command | Description | Nodes |
|---|---|---|---|
| **perception_test.launch.py** | `ros2 launch kart_perception perception_test.launch.py source:=<path> weights:=<path>` | 2D YOLO on recorded images/video — no camera needed | image_source, yolo_detector, cone_marker_viz, static_tf |
| **perception_3d.launch.py** | `ros2 launch kart_perception perception_3d.launch.py` | Live 3D perception — YOLO + depth localization. Used as sub-launch by launch.py | yolo_detector, cone_depth_localizer, cone_marker_viz_3d |

### Standalone

| Launch file | Command | Description | Nodes |
|---|---|---|---|
| **kb_dashboard** | `ros2 launch kb_dashboard dashboard.launch.py` | Dashboard web UI only (no comms) | kb_dashboard |
| **display_zed_cam.launch.py** | `ros2 launch zed_display_rviz2 display_zed_cam.launch.py camera_model:=zed2` | ZED camera + RViz2 visualization | rviz2, zed_camera (optional) |

## FAQ

**Why ROS 2 and not ROS 1?**
ROS 1 (Noetic) reached end-of-life in May 2025. ROS 2 is the actively maintained version with better real-time support, native DDS communication, and lifecycle node management — all important for a safety-critical autonomous vehicle.

**Why Humble and not Jazzy?**
The Jetson Orin runs Ubuntu 22.04 (JetPack 6), and Humble is the LTS release that targets 22.04. Jazzy requires Ubuntu 24.04 which is not yet supported on the Jetson platform.

**Why C++ instead of Python?**
We use Python. The perception and control nodes are written in Python for faster prototyping and because PyTorch (used by YOLO) is a Python library. C++ nodes may be introduced later for performance-critical paths, but Python is the default.

**Why is the microcontroller code in a separate repo?**
The ESP32 firmware (`kart-medulla`) runs on bare metal with FreeRTOS — it has its own toolchain (PlatformIO/Arduino), its own flashing process, and no ROS dependency. Keeping it separate avoids coupling the embedded build with the ROS workspace.

**Which YOLO version does cone detection use?**
**YOLOv11**, trained in-house on our own cone dataset — the weights are `ruben_yolov11n_2026_03.pt` and it runs on the Jetson via TensorRT export. The earlier `adri_yolov5_2025.pt` weights are YOLOv5 and are kept as legacy: the detector node loads through the `ultralytics` package first and falls back to `torch.hub` for the v5 weights, so both formats still load. The legacy pre-ROS `driverless` stack was YOLOv5 throughout.

**What was the old Python repo (`driverless`) used for?**
That was the 2024 prototype: a monolithic Python script that handled ZED camera capture, YOLO inference, and CAN bus commands all in one process. It proved the concept but wasn't modular or maintainable. The current ROS 2 architecture splits those responsibilities into independent nodes.

**How do we get 3D cone positions from the camera?**
The ZED is a stereo camera — its SDK computes a depth map from the left/right image pair. We run YOLO on the 2D image to get bounding boxes, then look up the depth at each detection's center pixel to get the distance. With the depth value and the camera intrinsics, we back-project into 3D (x, y, z) in the camera frame. This is all handled by the `cone_depth_localizer` node. No custom stereo matching needed — the ZED SDK does that internally.

**Is image masking part of the YOLO model or done separately?**
Done separately. The YOLO model itself doesn't know which regions of the image to ignore (e.g. sky, dashboard, parts of the kart body). A mask is applied to the image before passing it to YOLO — this simplifies the input and avoids false detections in irrelevant areas. The mask is not baked into the trained weights.

**Why use a virtual machine (UTM) for development?**
The target hardware is a Jetson Orin Nano running Ubuntu 22.04 (aarch64). UTM lets us run the same OS and architecture on a Mac for development and testing without needing the physical board connected. SSH is configured so the VM is accessible as `ssh utm`.

**How do you run the system without the physical kart?**
The `perception_test.launch.py` file starts an `image_source` node that publishes images or video from local files onto the same ROS topics the ZED camera would use. This lets you test the full perception pipeline (YOLO detection, visualization, even 3D localization with recorded depth data) entirely offline.

## TODO
See [TODO.md](TODO.md) for open tasks. Done items are removed — check git history if needed.

## AI Automation

Three ways to let Claude Code work autonomously on this repo:

### `/tasks` skill (interactive, recommended)

From inside a Claude Code session, run `/tasks` (or `/tasks 3` for 3 in parallel). Claude launches subagents that pick tasks from `tasks.md` (repo root), update status, do the work, and commit — while you stay in the loop to unblock and steer. Also: `/tasks list` to check status, `/tasks add <description>` to add new tasks.

### Autopilot (unattended)

Same task board, but fully headless — runs `claude -p` in a loop from the terminal. Walk away and come back to commits. Blocked tasks need manual intervention (edit `tasks.md` in the repo root to answer and move back to Ready).

```bash
./scripts/autopilot.sh
```

### Autoresearch (experiment loop)

Inspired by [karpathy/autoresearch](https://github.com/karpathy/autoresearch). An autonomous optimization loop for the 2D kart simulator — the agent edits `tools/sim2d/strategy.py`, runs an experiment, logs results to `results.tsv`, and keeps or reverts the commit based on whether loss improved. See `tools/sim2d/program.md` for the full spec.

```bash
# Start a new autoresearch session (creates a branch, runs experiments in a loop)
claude -p "$(cat tools/sim2d/program.md)"

# Run a single evaluation with the current strategy
cd tools/sim2d && python evaluate.py

# Visualize the best controller on a track
cd tools/sim2d && python visualize.py

# Use the winning controller in Gazebo simulation (see best_geometric.json for params)
ros2 launch kart_sim simulation.launch.py controller:=geometric track:=autocross
```

Current best: **Geometric controller** — 28 laps in 60s, loss 8.95 (`tools/sim2d/best_geometric.json`).

## References
- Kart Docs: https://github.com/UM-Driverless/kart-docs
- Kart Docs site: https://um-driverless.github.io/kart-docs/
- ROS 2 installation: https://docs.ros.org/en/rolling/Installation.html
- ROS 2 Humble docs: https://docs.ros.org/en/humble/

## Test Media
Pulled from https://github.com/UM-Driverless/driverless and stored locally at
`tests/test_data/driverless_test_media`.

## YOLO Weights
Stored locally in `models/perception/yolo/`, one file per trained model — see the README there for
the full table. The current cone detector is **`ruben_yolov11n_2026_03.pt`** (YOLOv11, trained
in-house), with ONNX and TensorRT engine exports beside it for the Orin. `adri_yolov5_2025.pt` is
the legacy YOLOv5 model pulled from https://github.com/UM-Driverless/driverless and is kept so the
old weights still load.

## YOLO Quick Test
Run YOLO on a test image or video and save annotated output.

```bash
python3 tools/run_yolo_on_media.py \
  --source tests/test_data/driverless_test_media/cones_test.png \
  --weights models/perception/yolo/ruben_yolov11n_2026_03.pt \
  --output outputs/yolo
```

Passing the legacy `adri_yolov5_2025.pt` weights instead downloads the YOLOv5 code via Torch Hub on first run and caches it locally; the YOLOv11 weights load through the `ultralytics` package and need no download.

## Running ROS Nodes
Launch the image source and YOLO detector nodes on test media.

```bash
source /opt/ros/humble/setup.bash
colcon build --packages-select kart_perception
source install/setup.bash

ros2 launch kart_perception perception_test.launch.py \
  source:=tests/test_data/driverless_test_media/cones_test.png \
  weights:=models/perception/yolo/ruben_yolov11n_2026_03.pt
```
