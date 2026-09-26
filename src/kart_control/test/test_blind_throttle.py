"""Exercise production detection/timeout callbacks without requiring ROS."""

import ast
import math
from pathlib import Path
from types import SimpleNamespace

import pytest


class Twist:
    def __init__(self):
        self.linear = SimpleNamespace(x=0.0)
        self.angular = SimpleNamespace(z=0.0)


class Stamp:
    def __init__(self, seconds):
        self.nanoseconds = int(seconds * 1e9)

    def __sub__(self, other):
        return Stamp((self.nanoseconds - other.nanoseconds) / 1e9)


@pytest.fixture
def node():
    # Compile the actual methods, not a copy of their logic. ROS annotations are
    # postponed so this regression runs on laptops without ROS installed.
    path = Path(__file__).parents[1] / "scripts" / "cone_follower_node.py"
    source = ast.parse(path.read_text())
    cls = next(n for n in source.body if isinstance(n, ast.ClassDef)
               and n.name == "ConeFollowerNode")
    names = {"_on_detections", "_compute_speed", "_safety_check"}
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[
        ast.alias(name="annotations")], level=0), *methods], type_ignores=[])
    namespace = {"Twist": Twist, "math": math}
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    callback_class = type("Callbacks", (), {name: namespace[name] for name in names})
    instance = callback_class()
    instance.controller_type = "none"
    instance.max_speed = 2.625
    instance.speed_controller_type = "constant_throttle_blind"
    instance.no_cone_timeout = 0.5
    instance.last_detection_time = Stamp(0)
    instance.now = 1.0
    instance.get_clock = lambda: SimpleNamespace(now=lambda: Stamp(instance.now))
    instance.commands = []
    instance.cmd_pub = SimpleNamespace(publish=instance.commands.append)
    return instance


def detection(z, results=True):
    result = SimpleNamespace(hypothesis=SimpleNamespace(class_id="blue_cone"),
                             pose=SimpleNamespace(pose=SimpleNamespace(
                                 position=SimpleNamespace(x=0.0, z=z))))
    return SimpleNamespace(results=[result] if results else [])


@pytest.mark.parametrize("detections", [[], [detection(0.2)], [detection(2, False)]])
@pytest.mark.parametrize("mode,expected", [
    ("constant_throttle_blind", 2.625), ("constant_throttle", 0.0), ("zero", 0.0)])
def test_live_empty_perception(node, detections, mode, expected):
    node.speed_controller_type = mode
    for _ in range(5):
        node.now += 0.1
        node._on_detections(SimpleNamespace(detections=detections))
        node._safety_check()
    assert len(node.commands) == 5
    assert all(cmd.linear.x == expected and cmd.angular.z == 0 for cmd in node.commands)


@pytest.mark.parametrize("mode,expected", [
    ("constant_throttle_blind", 2.625), ("constant_throttle", 0.0), ("zero", 0.0)])
def test_perception_silence(node, mode, expected):
    node.speed_controller_type = mode
    node._safety_check()
    assert len(node.commands) == 1
    assert node.commands[0].linear.x == expected
    assert node.commands[0].angular.z == 0


def test_blind_throttle_with_visible_cones(node):
    node._on_detections(SimpleNamespace(detections=[detection(2)]))
    assert node.commands[0].linear.x == node.max_speed


@pytest.mark.parametrize("coordinate", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_perception_stops_before_controller_math(node, coordinate):
    node._on_detections(SimpleNamespace(detections=[detection(coordinate)]))
    assert node._detection_invalid
    assert node.commands[-1].linear.x == 0
    assert node.commands[-1].angular.z == 0
