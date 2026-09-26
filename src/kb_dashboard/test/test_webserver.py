"""Tests for the HTTP/WebSocket server (no ROS dependency)."""
import asyncio
import base64
import hashlib
import json
import os
import socket
import threading
import time

import pytest

from kb_dashboard import server
from kb_dashboard.protocol import DashboardState
from kb_dashboard.server import run_websocket_server


class FakeNode:
    """Minimal mock replacing DashboardNode for server tests."""

    def __init__(self):
        self.published_missions = []
        self.published_state_commands = []

    def publish_mission(self, mission):
        self.published_missions.append(mission)

    def publish_state_cmd(self, command):
        self.published_state_commands.append(command)

    def get_logger(self):
        return self

    def info(self, msg):
        pass

    def warn(self, msg):
        pass

    def error(self, msg):
        pass


class ServerFixture:
    """Manages a server running in a background thread."""

    def __init__(self):
        self.port = None          # set by the server once it has actually bound
        self.state = DashboardState()
        self.node = FakeNode()
        self._loop = asyncio.new_event_loop()
        self._task = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise RuntimeError("Server did not start in time")

    def _on_ready(self, bound_port):
        # Binding port 0 and learning the port here removes the race that made these tests
        # flaky: picking a "free" port first and binding it later let two fixtures choose the
        # same one, so a test could end up talking to another test's server.
        self.port = bound_port
        self._ready.set()

    def stop(self):
        # Cancelling the task only *asks* the server to stop, so join the thread: without it
        # every finished test leaks a live server thread for the rest of the session.
        if self._task:
            self._loop.call_soon_threadsafe(self._task.cancel)
        self._thread.join(timeout=5)

    def _run(self):
        asyncio.set_event_loop(self._loop)

        async def _start():
            await run_websocket_server(self.state, self.node, 0, ready_callback=self._on_ready)

        self._task = self._loop.create_task(_start())
        try:
            self._loop.run_until_complete(self._task)
        except asyncio.CancelledError:
            pass


@pytest.fixture()
def srv():
    s = ServerFixture()
    s.start()
    yield s
    s.stop()


def _blocking_http_get(port, path="/"):
    """Plain blocking HTTP GET using raw sockets."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect(("127.0.0.1", port))
    s.sendall(f"GET {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n".encode())
    data = b""
    while True:
        try:
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
        except socket.timeout:
            break
    s.close()
    return data.decode(errors="replace")


def _blocking_ws_connect(port):
    """Blocking WebSocket handshake, returns the raw socket."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect(("127.0.0.1", port))
    key = "dGhlIHNhbXBsZSBub25jZQ=="
    req = (
        f"GET /ws HTTP/1.1\r\n"
        f"Host: localhost:{port}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"\r\n"
    )
    s.sendall(req.encode())
    # Read EXACTLY the HTTP response headers. recv(1024) would also swallow any WebSocket
    # frames sharing the same TCP segment, and the server sends its welcome immediately
    # after the handshake — discarding it makes any later read wait for a message that has
    # already gone.
    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = s.recv(1)
        if not chunk:
            break
        resp += chunk
    assert b"101 Switching Protocols" in resp, f"Bad handshake: {resp}"

    expected_accept = base64.b64encode(
        hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
    ).decode()
    assert expected_accept.encode() in resp
    return s


def _ws_read_frame(sock):
    """Read one WebSocket frame from a blocking socket, return payload bytes."""
    header = sock.recv(2)
    if len(header) < 2:
        return None
    length = header[1] & 0x7F
    if length == 126:
        length = int.from_bytes(sock.recv(2), "big")
    elif length == 127:
        length = int.from_bytes(sock.recv(8), "big")
    payload = b""
    while len(payload) < length:
        chunk = sock.recv(length - len(payload))
        if not chunk:
            break
        payload += chunk
    return payload


def _ws_send_text(sock, text: str):
    """Send a masked WebSocket text frame."""
    data = text.encode()
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    frame = bytearray([0x81, 0x80 | len(data)])
    frame.extend(mask)
    frame.extend(masked)
    sock.sendall(bytes(frame))


# ── HTTP Tests ─────────────────────────────────────────────────────────

class TestHTTP:
    def test_serves_html(self, srv):
        resp = _blocking_http_get(srv.port)
        assert "HTTP/1.1 200 OK" in resp
        assert "UM Driverless" in resp
        assert "text/html" in resp

    def test_404_for_unknown_path(self, srv):
        resp = _blocking_http_get(srv.port, "/nonexistent")
        assert "404" in resp


# ── WebSocket Tests ────────────────────────────────────────────────────

class TestWebSocket:
    def test_handshake(self, srv):
        s = _blocking_ws_connect(srv.port)
        s.close()

    def test_receives_telemetry(self, srv):
        srv.state.update("esp32_steering_rad", 0.314)
        s = _blocking_ws_connect(srv.port)
        # Not necessarily the first frame: the server registers the client in the broadcast
        # set before sending the {"your_id": ...} welcome, so either can arrive first. Skip
        # anything that is not a telemetry snapshot rather than assuming an order.
        msg = None
        deadline = time.time() + 5.0
        while time.time() < deadline:
            s.settimeout(deadline - time.time())
            payload = _ws_read_frame(s)
            if payload is None:
                break
            candidate = json.loads(payload)
            if "esp32_steering_rad" in candidate:
                msg = candidate
                break
        assert msg is not None, "no telemetry snapshot received"
        assert abs(msg["esp32_steering_rad"] - 0.314) < 1e-6
        assert msg["mission"] == "manual"
        assert msg["state"] == "idle"
        s.close()

    def test_mission_command(self, srv):
        s = _blocking_ws_connect(srv.port)
        _ws_send_text(s, json.dumps({"action": "set_mission", "mission": "autocross"}))
        time.sleep(0.3)
        assert "autocross" in srv.node.published_missions
        assert srv.state.snapshot()["mission"] == "autocross"
        s.close()

    @pytest.mark.parametrize("initial, requested, command", [
        ("idle", "running", "start"), ("ebs", "running", "start"),
        ("ebs", "reset", "reset"), ("running", "idle", "stop"),
    ])
    def test_state_command_waits_for_confirmation(self, srv, initial, requested, command):
        srv.state.update("state", initial)
        s = _blocking_ws_connect(srv.port)
        _ws_send_text(s, json.dumps({"action": "set_state", "state": requested}))
        time.sleep(0.3)
        assert srv.node.published_state_commands == [command]
        assert srv.state.snapshot()["state"] == initial
        s.close()

    def test_invalid_mission_ignored(self, srv):
        s = _blocking_ws_connect(srv.port)
        _ws_send_text(s, json.dumps({"action": "set_mission", "mission": "nonexistent"}))
        time.sleep(0.2)
        assert srv.node.published_missions == []
        assert srv.state.snapshot()["mission"] == "manual"
        s.close()

    def test_invalid_state_ignored(self, srv):
        s = _blocking_ws_connect(srv.port)
        _ws_send_text(s, json.dumps({"action": "set_state", "state": "invalid"}))
        time.sleep(0.2)
        assert srv.state.snapshot()["state"] == "idle"
        s.close()


# ── Power off ──────────────────────────────────────────────────────────

def _ws_await_key(sock, key, timeout=10.0):
    """Return the first JSON frame carrying `key`, or None if none arrives in time.

    Telemetry snapshots are broadcast continuously, so a reply is almost never the very
    next frame on the wire.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        sock.settimeout(max(0.1, deadline - time.time()))
        try:
            payload = _ws_read_frame(sock)
        except socket.timeout:
            return None
        if payload is None:
            return None
        try:
            msg = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            continue          # binary HUD frame
        if isinstance(msg, dict) and key in msg:
            return msg
    return None


class TestPowerOff:
    """The browser cannot see the difference between an Orin that powered off and a button
    that did nothing, so the server has to say both which request it accepted and when the
    power-off came back refused. POWEROFF_CMD is swapped out here — the real one would take
    the machine running the tests down."""

    def test_acknowledges_request(self, srv, monkeypatch):
        monkeypatch.setattr(server, "POWEROFF_CMD", "true")
        s = _blocking_ws_connect(srv.port)
        _ws_send_text(s, json.dumps({"action": "shutdown_orin"}))
        msg = _ws_await_key(s, "shutdown_orin")
        assert msg is not None, "no acknowledgement for shutdown_orin"
        assert msg["shutdown_orin"] == "starting"
        # Let the (stubbed) poweroff finish before the fixture tears the loop down, so the
        # run does not end on a "Task was destroyed but it is pending" notice.
        time.sleep(0.3)
        s.close()

    def test_reports_failure(self, srv, monkeypatch):
        monkeypatch.setattr(
            server, "POWEROFF_CMD", "echo 'sudo: a password is required' >&2; exit 1")
        s = _blocking_ws_connect(srv.port)
        _ws_send_text(s, json.dumps({"action": "shutdown_orin"}))
        assert _ws_await_key(s, "shutdown_orin")["shutdown_orin"] == "starting"
        failed = _ws_await_key(s, "error")
        assert failed is not None, "poweroff failed without telling the browser"
        assert failed["shutdown_orin"] == "failed"
        assert "password is required" in failed["error"]
        s.close()

    def test_success_stays_silent(self, srv, monkeypatch):
        """A command that exits 0 has queued the shutdown; the machine going away is the
        only confirmation there is, so nothing further should be reported as an error."""
        monkeypatch.setattr(server, "POWEROFF_CMD", "true")
        s = _blocking_ws_connect(srv.port)
        _ws_send_text(s, json.dumps({"action": "shutdown_orin"}))
        assert _ws_await_key(s, "shutdown_orin")["shutdown_orin"] == "starting"
        assert _ws_await_key(s, "error", timeout=2.0) is None
        s.close()
