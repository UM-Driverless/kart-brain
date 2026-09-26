<!-- read in full — kept under 150 lines -->
# Kart Brain Agent Documentation

This directory contains persistent AI-focused documentation for the kart-brain workspace. It follows a two-layer system: **memory** (what agents read) and **enforcement** (what prevents bad work).

## Why This Exists

LLMs have no persistent memory between sessions. Every conversation starts fresh. This directory captures hard-won knowledge about the system so it doesn't have to be rediscovered each time.

## File Index

### Layer 1: Memory (What Agents Read)

| File | Purpose |
|---|---|
| `architecture.md` | Package structure, node graph, message types, topic map, ESP32 UART routing & protocol |
| `sensor-safety.md` | Sensor safety policy, fault and reset protocol, verification and physical validation limits |
| `simulation.md` | Gazebo setup, known issues, rendering quirks, how to test |
| `dashboard-testing.md` | Running the dashboard offline (`?demo=1`), driving its UI from a harness, and its layout/stylesheet traps |
| `vm-environment.md` | UTM VM specifics: SSH, sudo, installed packages, limitations |
| `orin-environment.md` | Jetson Orin specifics: hardware, ZED camera, live pipeline, known issues |
| `orin-flash-guide.md` | Step-by-step guide for flashing Orin to NVMe with JetPack 6.2.2 |
| `error-log.md` | Running log of mistakes and prevention mechanisms |
| `errors/` | Detailed failure analysis for significant errors |

### Layer 2: Enforcement

| File | Purpose |
|---|---|
| `../tools/` | Workspace-level utility scripts & tools |

## Workflow

1. **Before starting:** Read `AGENTS.md` (root) → relevant `.agents/` files
2. **During work:** Follow architecture conventions from `architecture.md`
3. **Before commit:** Build, verify, `git status` + `git diff`
3b. **After commit: ALWAYS push immediately** — multiple machines (Mac, Orin) work on the same repos, so unpushed commits cause conflicts
3c. **Use background tasks for long-running ops** — flashing (~30s), building, serial reads. Don't block the conversation waiting for them.
4. **If something breaks:** Document in `error-log.md`, add prevention
5. **If recurring:** Create detailed write-up in `errors/`

## Environment-Specific Guides

| Environment | Doc | Connection |
|---|---|---|
| **Jetson Orin** (real hardware) | `orin-environment.md` | `ssh orin` (WiFi) or AnyDesk |
| **Jetson Orin** (flash/reinstall) | `orin-flash-guide.md` | USB-C from y540 to Orin |
| **y540 laptop** (flash host) | — | `ssh y540` (Robots_urjc WiFi, DHCP) |
| **UTM VM** (simulation) | `vm-environment.md` | `ssh utm` (192.168.65.2) |

## Conventions

- When the user says "your memory" or "your notes", they mean `AGENTS.md` and `.agents/` — NOT the Claude auto-memory directory

## Key Principles

- **Document what was painful** — If you spent time debugging something, write it down
- **Cone class IDs matter** — The whole pipeline depends on consistent string IDs (`blue_cone`, `yellow_cone`, etc.)
- **BGR vs RGB** — OpenCV uses BGR, YOLO/PIL use RGB. Always verify channel order matches the declared encoding when passing images between systems
- **Check for stale processes** — Before launching ROS nodes, check if instances are already running
- **Odom is relative** — Gazebo odometry starts at (0,0), not at the world pose
- **No GPU in VM** — Everything renders via LLVMpipe. Keep resolutions low, disable shadows
- **Hardware is on the Orin, not the Mac** — ESP32, ZED camera, actuators are all physically on the Orin. Always `ssh orin` for flashing, USB checks, ROS node launches, and any hardware interaction. The Mac is only for editing code.
