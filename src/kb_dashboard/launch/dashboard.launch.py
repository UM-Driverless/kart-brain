from launch import LaunchDescription
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory
from pathlib import Path


def generate_launch_description():
    return LaunchDescription([
        Node(
            package="kb_dashboard",
            executable="dashboard",
            name="kb_dashboard",
            parameters=[str(Path(get_package_share_directory("kb_dashboard")) /
                            "config/hall_speed.yaml"), {"port": 80}],
            output="screen",
        ),
    ])
