"""Master launch file for the kart simulation.

Starts:
1. Gazebo server (headless or GUI)
2. ros_gz_bridge (topic bridging)
3. Perfect perception node OR YOLO perception pipeline
4. Cone follower control node
5. Cone marker visualization
6. CameraInfo fix node (when use_yolo=true)
7. ESP32 simulator node (fake telemetry)
8. Dashboard web UI (port 9090)

Launch arguments:
    track:=oval      -> Oval track (default)
    track:=hairpin   -> Hairpin track
    track:=autocross -> Autocross track
    use_yolo:=true   -> Launch YOLO pipeline (yolo_detector + cone_depth_localizer)
    use_yolo:=false  -> Launch perfect perception (ground truth from SDF) [default]
"""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    ExecuteProcess,
    IncludeLaunchDescription,
    OpaqueFunction,
    TimerAction,
)
from launch.conditions import IfCondition, UnlessCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

# Track configs: world_file_basename, world_name, (start_x, start_y, start_yaw)
_TRACKS = {
    "oval": ("fs_track.sdf", "fs_track", 20.0, 0.0, 1.5708),
    "hairpin": ("hairpin_track.sdf", "hairpin_track", 20.0, -5.5, 1.5708),
    "autocross": ("autocross_track.sdf", "autocross_track", 30.0, -7.5, 1.5708),
}


def _launch_setup(context):
    pkg_kart_sim = get_package_share_directory("kart_sim")
    model_path = os.path.join(pkg_kart_sim, "models")

    track_name = context.launch_configurations["track"]
    if track_name not in _TRACKS:
        raise RuntimeError(
            f"Unknown track '{track_name}'. Choose from: {list(_TRACKS.keys())}"
        )
    sdf_basename, world_name, start_x, start_y, start_yaw = _TRACKS[track_name]
    world_file = os.path.join(pkg_kart_sim, "worlds", sdf_basename)

    use_yolo = context.launch_configurations.get("use_yolo", "false")
    gui = context.launch_configurations.get("gui", "false")
    controller_type = context.launch_configurations.get("controller", "neural_v2")
    pkg_kart_control = get_package_share_directory("kart_control")
    default_weights = os.path.join(
        pkg_kart_control, "config", "neural_v2_weights.json"
    )
    weights_json = context.launch_configurations.get("weights_json", default_weights)

    # --- 1. Gazebo (headless by default, GUI with gui:=true) ---
    gazebo_headless = ExecuteProcess(
        cmd=[
            "ign", "gazebo", "-s", "-r", "--headless-rendering",
            world_file,
        ],
        output="screen",
        additional_env={
            "IGN_GAZEBO_RESOURCE_PATH": model_path,
        },
        condition=UnlessCondition(LaunchConfiguration("gui")),
    )
    gazebo_gui = ExecuteProcess(
        cmd=[
            "ign", "gazebo", "-r",
            world_file,
        ],
        output="screen",
        additional_env={
            "IGN_GAZEBO_RESOURCE_PATH": model_path,
        },
        condition=IfCondition(LaunchConfiguration("gui")),
    )

    # --- 2. ros_gz_bridge ---
    clock_topic = f"/world/{world_name}/clock"
    bridge = Node(
        package="ros_gz_bridge",
        executable="parameter_bridge",
        arguments=[
            f"{clock_topic}@rosgraph_msgs/msg/Clock[ignition.msgs.Clock",
            "/model/kart/odometry@nav_msgs/msg/Odometry[ignition.msgs.Odometry",
            "/model/kart/odom_gt@nav_msgs/msg/Odometry[ignition.msgs.Odometry",
            "/kart/vel_cmd@geometry_msgs/msg/Twist]ignition.msgs.Twist",
            "/kart/rgbd/image@sensor_msgs/msg/Image[ignition.msgs.Image",
            "/kart/rgbd/depth_image@sensor_msgs/msg/Image[ignition.msgs.Image",
            "/kart/rgbd/camera_info@sensor_msgs/msg/CameraInfo[ignition.msgs.CameraInfo",
        ],
        remappings=[
            ("/kart/rgbd/image", "/zed/zed_node/rgb/image_rect_color"),
            ("/kart/rgbd/depth_image", "/zed/zed_node/depth/depth_registered"),
            ("/kart/rgbd/camera_info", "/zed/zed_node/rgb/camera_info_raw"),
            (clock_topic, "/clock"),
        ],
        parameters=[{"use_sim_time": True}],
        output="screen",
    )

    # --- 2b. CameraInfo fix node (corrects Gazebo intrinsics) ---
    camera_info_fix = Node(
        package="kart_sim",
        executable="camera_info_fix_node.py",
        name="camera_info_fix",
        output="screen",
        parameters=[
            {
                "use_sim_time": True,
                "hfov": 1.396,
                "input_topic": "/zed/zed_node/rgb/camera_info_raw",
                "output_topic": "/zed/zed_node/rgb/camera_info",
            }
        ],
    )

    # --- 3a. Perfect perception node (when use_yolo=false) ---
    perfect_perception = Node(
        package="kart_sim",
        executable="perfect_perception_node.py",
        name="perfect_perception",
        output="screen",
        parameters=[
            {
                "use_sim_time": False,
                "world_sdf": world_file,
                "output_topic": "/perception/cones_3d",
                "max_range": 20.0,
                "fov_deg": 120.0,
                "publish_rate": 10.0,
                "kart_start_x": start_x,
                "kart_start_y": start_y,
                "kart_start_yaw": start_yaw,
            }
        ],
        condition=UnlessCondition(LaunchConfiguration("use_yolo")),
    )

    # --- 3b. YOLO perception pipeline (when use_yolo=true) ---
    weights_path = os.path.join(
        os.path.expanduser("~"), "kart-brain", "models", "perception", "yolo",
        "ruben_yolov11n_2026_03.pt",
    )
    try:
        pkg_perception = get_package_share_directory("kart_perception")
        perception_launch = IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                os.path.join(pkg_perception, "launch", "perception_3d.launch.py")
            ),
            launch_arguments={
                "image_topic": "/zed/zed_node/rgb/image_rect_color",
                "depth_topic": "/zed/zed_node/depth/depth_registered",
                "camera_info_topic": "/zed/zed_node/rgb/camera_info",
                "weights": weights_path,
            }.items(),
            condition=IfCondition(LaunchConfiguration("use_yolo")),
        )
    except Exception:
        perception_launch = ExecuteProcess(
            cmd=["echo", "ERROR: kart_perception package not found. Build it first."],
            output="screen",
            condition=IfCondition(LaunchConfiguration("use_yolo")),
        )

    # --- 4. Cone follower control node ---
    cone_follower = Node(
        package="kart_control",
        executable="cone_follower_node.py",
        name="cone_follower",
        output="screen",
        parameters=[
            {
                "use_sim_time": False,
                "detections_topic": "/perception/cones_3d",
                "cmd_vel_topic": "/kart/cmd_vel",
                "controller_type": controller_type,
                "speed_controller_type": context.launch_configurations.get("speed_controller", "curve_factor"),
                "weights_json": weights_json,
                "odom_topic": "/model/kart/odom_gt",
                "max_speed": float(context.launch_configurations.get("max_speed", "1000000.0")),
                "min_speed": float(context.launch_configurations.get("min_speed", "0.5")),
                "steering_gain": float(context.launch_configurations.get("steering_gain", "2.0")),
                "max_steer": float(context.launch_configurations.get("max_steer", "0.5")),
                "lookahead_max": float(context.launch_configurations.get("lookahead_max", "15.0")),
                "half_track_width": 1.5,
                "speed_curve_factor": float(context.launch_configurations.get("speed_curve_factor", "0.3")),
                "mpc_w_cte": float(context.launch_configurations.get("mpc_w_cte", "5.0")),
                "mpc_w_dsteer": float(context.launch_configurations.get("mpc_w_dsteer", "15.0")),
                "mpc_w_heading": float(context.launch_configurations.get("mpc_w_heading", "3.0")),
            }
        ],
    )

    # --- 5. Cone marker visualization ---
    try:
        get_package_share_directory("kart_perception")
        marker_viz = Node(
            package="kart_perception",
            executable="cone_marker_viz_3d",
            name="cone_marker_viz_3d",
            output="screen",
            parameters=[
                {
                    "use_sim_time": True,
                    "detections_topic": "/perception/cones_3d",
                    "markers_topic": "/perception/cones_3d_markers",
                }
            ],
        )
    except Exception:
        marker_viz = ExecuteProcess(cmd=["true"], output="log")

    # --- 6. Ackermann-to-velocity converter (steer angle → yaw rate) ---
    ackermann_to_vel = Node(
        package="kart_sim",
        executable="ackermann_to_vel.py",
        name="ackermann_to_vel",
        output="screen",
    )

    # --- 7. ESP32 simulator (fake telemetry) ---
    esp32_sim = Node(
        package="kart_sim",
        executable="esp32_sim_node.py",
        name="esp32_sim",
        output="screen",
    )

    # --- 8. Dashboard web UI ---
    dashboard = Node(
        package="kb_dashboard",
        executable="dashboard",
        name="kb_dashboard",
        parameters=[{"port": 9090, "use_hall_speed": False}],
        output="screen",
    )

    return [
        gazebo_headless,
        gazebo_gui,
        TimerAction(period=3.0, actions=[bridge]),
        TimerAction(period=4.0, actions=[camera_info_fix]),
        TimerAction(period=5.0, actions=[perfect_perception]),
        TimerAction(period=8.0, actions=[perception_launch]),
        TimerAction(period=6.0, actions=[cone_follower]),
        TimerAction(period=5.0, actions=[marker_viz]),
        TimerAction(period=4.0, actions=[ackermann_to_vel]),
        TimerAction(period=5.0, actions=[esp32_sim]),
        TimerAction(period=5.0, actions=[dashboard]),
    ]


def generate_launch_description():
    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "track",
                default_value="oval",
                description="Track to load: 'oval' or 'hairpin'.",
            ),
            DeclareLaunchArgument(
                "use_yolo",
                default_value="false",
                description="Use YOLO perception instead of perfect perception.",
            ),
            DeclareLaunchArgument(
                "gui",
                default_value="false",
                description="Launch Gazebo with GUI (for AnyDesk/display).",
            ),
            DeclareLaunchArgument(
                "controller",
                default_value="neural_v2",
                description="Controller type: 'geometric', 'neural', 'neural_v2', or 'mpc'.",
            ),
            DeclareLaunchArgument(
                "weights_json",
                default_value=os.path.join(
                    get_package_share_directory("kart_control"),
                    "config", "neural_v2_weights.json",
                ),
                description="Path to neural-net weights JSON (from sim2d GA trainer).",
            ),
            DeclareLaunchArgument("max_speed", default_value="1000000.0"),
            DeclareLaunchArgument("min_speed", default_value="0.5"),
            DeclareLaunchArgument("steering_gain", default_value="2.0"),
            DeclareLaunchArgument("max_steer", default_value="1.047"),
            DeclareLaunchArgument("lookahead_max", default_value="15.0"),
            DeclareLaunchArgument("speed_curve_factor", default_value="0.3"),
            OpaqueFunction(function=_launch_setup),
        ]
    )
