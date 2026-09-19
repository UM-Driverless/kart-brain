from setuptools import setup

package_name = "kb_dashboard"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/launch", ["launch/dashboard.launch.py"]),
        ("share/" + package_name + "/config", ["config/hall_speed.yaml"]),
    ],
    # The icons ship beside index.html because the Home Screen icon cannot be a data: URI —
    # iOS ignores those for apple-touch-icon, so it has to be a real URL the server answers.
    package_data={package_name: ["index.html", "icon-180.png", "icon-512.png"]},
    install_requires=["setuptools"],
    zip_safe=True,
    entry_points={
        "console_scripts": [
            "dashboard = kb_dashboard.dashboard_node:main",
        ],
    },
)
