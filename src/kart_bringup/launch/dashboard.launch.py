# Minimal launch file for safe firmware testing.
# Starts only KB_Coms_micro (serial comms) and kb_dashboard (web UI).
# No perception, controller, or joystick nodes — no commands are sent to the kart.

from launch import LaunchDescription
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory
from pathlib import Path


def generate_launch_description():
    comms = Node(
        package="kb_coms_micro",
        executable="KB_Coms_micro",
        name="kb_coms_micro",
        output="screen",
    )

    dashboard = Node(
        package="kb_dashboard",
        executable="dashboard",
        name="kb_dashboard",
        parameters=[str(Path(get_package_share_directory("kb_dashboard")) /
                        "config/hall_speed.yaml"), {"port": 80}],
        output="screen",
    )

    return LaunchDescription([
        comms,
        dashboard,
    ])
