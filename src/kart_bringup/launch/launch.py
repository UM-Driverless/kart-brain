import glob
import os

import subprocess

from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    IncludeLaunchDescription,
    SetEnvironmentVariable,
)
from launch.conditions import IfCondition, UnlessCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    perception_arg = DeclareLaunchArgument(
        "perception",
        default_value="true",
        description="Launch ZED camera + perception stack (set false for remote-control only).",
    )
    perception = LaunchConfiguration("perception")

    steering_gain_arg = DeclareLaunchArgument(
        "steering_gain",
        default_value="3.0",
        description="Gain applied to lateral cone angle before sending to steering.",
    )
    steering_gain = LaunchConfiguration("steering_gain")

    use_zed_od_arg = DeclareLaunchArgument(
        "use_zed_od",
        default_value="false",
        description="Use ZED SDK built-in object detection (true) or custom YOLO node (false).",
    )
    use_zed_od = LaunchConfiguration("use_zed_od")


    # Read SVO path from /tmp/kart_svo_path if it exists (set by dashboard)
    _svo_default = "live"
    _svo_file = "/tmp/kart_svo_path"
    if os.path.isfile(_svo_file):
        with open(_svo_file) as f:
            _val = f.read().strip()
            if _val:
                _svo_default = _val

    svo_path_arg = DeclareLaunchArgument(
        "svo_path",
        default_value=_svo_default,
        description="Path to an SVO file for playback, or 'live' for camera.",
    )
    svo_path = LaunchConfiguration("svo_path")

    # System CUDA libs must precede pip NVIDIA libs to avoid cuBLAS version mismatch
    # (pip installs cuBLAS 12.9 which is incompatible with Jetson's CUDA 12.6)
    cuda_sys = "/usr/local/cuda-12.6/targets/aarch64-linux/lib"
    pip_nvidia_dirs = glob.glob(
        os.path.expanduser("~/.local/lib/python3.10/site-packages/nvidia/*/lib")
    )
    ld_path = ":".join(
        [cuda_sys] + pip_nvidia_dirs + [os.environ.get("LD_LIBRARY_PATH", "")]
    )
    set_ld_path = SetEnvironmentVariable("LD_LIBRARY_PATH", ld_path)

    # Kill stale processes from previous runs BEFORE any nodes start
    subprocess.run(
        "pkill -9 -f 'yolo_detector|cone_follower|steering_hud|state_machine|"
        "cmd_vel_bridge|KB_Coms_micro|dashboard_node|cone_depth' 2>/dev/null; "
        "killall -q rviz2 rqt_image_view 2>/dev/null; "
        "fuser -k 80/tcp 9090/tcp 2>/dev/null; "
        "rm -rf /dev/shm/fastrtps_*; "
        "sleep 0.5",
        shell=True,
    )

    bringup_share = get_package_share_directory("kart_bringup")
    perception_share = get_package_share_directory("kart_perception")
    zed_share = get_package_share_directory("zed_wrapper")

    zed_overrides = os.path.join(bringup_share, "config", "zed_overrides.yaml")
    zed_overrides_od = os.path.join(bringup_share, "config", "zed_overrides_od.yaml")

    # --- Perception (conditional) ---

    zed_camera_with_od = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(zed_share, "launch", "zed_camera.launch.py")
        ),
        launch_arguments={
            "camera_model": "zed2",
            "ros_params_override_path": zed_overrides_od,
            "svo_path": svo_path,
        }.items(),
        condition=IfCondition(use_zed_od),
    )

    zed_camera_no_od = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(zed_share, "launch", "zed_camera.launch.py")
        ),
        launch_arguments={
            "camera_model": "zed2",
            "ros_params_override_path": zed_overrides,
            "svo_path": svo_path,
        }.items(),
        condition=UnlessCondition(use_zed_od),
    )

    perception_zed_od = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(perception_share, "launch", "perception_zed_od.launch.py")
        ),
        condition=IfCondition(use_zed_od),
    )

    perception_custom = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(perception_share, "launch", "perception_3d.launch.py")
        ),
        condition=UnlessCondition(use_zed_od),
    )

    cone_follower = Node(
        package="kart_control",
        executable="cone_follower_node.py",
        name="cone_follower",
        output="screen",
        parameters=[
            {
                "controller_type": "geometric",
                "speed_controller_type": "curve_factor",
                "steering_gain": steering_gain,
                "max_speed": 2.625,
            }
        ],
        condition=IfCondition(perception),
    )


    # --- Always launched ---

    steering_hud = Node(
        package="kart_perception",
        executable="steering_hud",
        name="steering_hud",
        output="screen",
    )

    # Estimates forward speed from how fast detected cones approach. The kart has no
    # speed sensor, so this is the only speed figure available until the hall-sensor
    # pins are free on a future PCB. Gated on perception because it needs the cone
    # detections, and UNVALIDATED — it feeds the dashboard readout only, nothing
    # steers or brakes on it. See src/kart_perception/kart_perception/speed_model.py.
    speed_estimator = Node(
        package="kart_perception",
        executable="speed_estimator",
        name="speed_estimator",
        output="screen",
        condition=IfCondition(perception),
    )

    state_machine = Node(
        package="kart_control",
        executable="state_machine_node.py",
        name="state_machine",
        output="screen",
    )

    cmd_vel_bridge = Node(
        package="kart_control",
        executable="cmd_vel_bridge_node.py",
        name="cmd_vel_bridge",
        output="screen",
    )

    comms = Node(
        package="kb_coms_micro",
        executable="KB_Coms_micro",
        name="kb_coms_micro",
        output="screen",
        sigterm_timeout="3",
        sigkill_timeout="2",
    )

    dashboard = Node(
        package="kb_dashboard",
        executable="dashboard",
        name="kb_dashboard",
        parameters=[os.path.join(get_package_share_directory("kb_dashboard"),
                                 "config", "hall_speed.yaml"), {"port": 80}],
        output="screen",
        sigterm_timeout="3",
        sigkill_timeout="2",
    )

    # Reads the pack's JBD smart BMS over the Orin's Bluetooth and publishes
    # /battery/state (sensor_msgs/BatteryState). Independent of the ESP32 link.
    bms = Node(
        package="kb_bms",
        executable="bms_node",
        name="kb_bms",
        output="screen",
        sigterm_timeout="3",
        sigkill_timeout="2",
    )

    return LaunchDescription(
        [
            perception_arg,
            steering_gain_arg,
            use_zed_od_arg,
            svo_path_arg,
            set_ld_path,
            # Perception (only when perception:=true)
            zed_camera_with_od,
            zed_camera_no_od,
            perception_zed_od,
            perception_custom,
            steering_hud,
            cone_follower,
            speed_estimator,
            # Always
            state_machine,
            cmd_vel_bridge,
            comms,
            dashboard,
            bms,
        ]
    )
