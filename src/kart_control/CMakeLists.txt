cmake_minimum_required(VERSION 3.8)
project(kart_control)

find_package(ament_cmake REQUIRED)

install(DIRECTORY config/ DESTINATION share/${PROJECT_NAME}/config)
install(PROGRAMS
  scripts/cone_follower_node.py
  scripts/cmd_vel_bridge_node.py
  scripts/state_machine_node.py
  DESTINATION lib/${PROJECT_NAME}
)

# Pure-logic modules imported by the scripts above. Installed next to them so
# the scripts' directory-relative imports resolve at runtime.
install(FILES
  scripts/state_logic.py
  scripts/safety_supervisor.py
  DESTINATION lib/${PROJECT_NAME}
)

ament_package()
