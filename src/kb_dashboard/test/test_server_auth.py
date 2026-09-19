"""Tests for the dashboard password authentication feature."""
import asyncio
import socket
import threading
import urllib.parse

import pytest

from kb_dashboard.protocol import DashboardState
from kb_dashboard.server import HTML_PATH, run_websocket_server


class FakeNode:
    """Minimal mock replacing DashboardNode for server tests."""

    def __init__(self):
        self.published_missions = []

    def publish_mission(self, mission):
        self.published_missions.append(mission)

    def get_logger(self):
        return self

    def info(self, msg):
        pass

    def warn(self, msg):
        pass


class AuthServerFixture:
    """Manages a server running in a background thread with optional password."""

    def __init__(self, password=""):
        self.port = None          # set by the server once it has actually bound
        self.state = DashboardState()
        self.node = FakeNode()
        self.password = password
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
            await run_websocket_server(
                self.state,
                self.node,
                0,
                ready_callback=self._on_ready,
                password=self.password,
            )

        self._task = self._loop.create_task(_start())
        try:
            self._loop.run_until_complete(self._task)
        except asyncio.CancelledError:
            pass


def _http_request(port, method="GET", path="/", headers=None, body=None):
    """Send a raw HTTP request and return (status_line, headers_dict, body_str)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect(("127.0.0.1", port))

    extra_headers = headers or {}
    lines = [f"{method} {path} HTTP/1.1", "Host: localhost", "Connection: close"]
    if body is not None:
        encoded_body = body.encode() if isinstance(body, str) else body
        lines.append(f"Content-Length: {len(encoded_body)}")
        lines.append("Content-Type: application/x-www-form-urlencoded")
    for k, v in extra_headers.items():
        lines.append(f"{k}: {v}")
    lines.append("")
    lines.append("")
    request = "\r\n".join(lines).encode()
    if body is not None:
        request += encoded_body

    s.sendall(request)
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

    text = data.decode(errors="replace")
    # Parse status line
    head, _, resp_body = text.partition("\r\n\r\n")
    head_lines = head.split("\r\n")
    status_line = head_lines[0] if head_lines else ""
    resp_headers = {}
    for line in head_lines[1:]:
        if ": " in line:
            k, v = line.split(": ", 1)
            resp_headers[k.lower()] = v
    return status_line, resp_headers, resp_body


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture()
def srv_no_password():
    s = AuthServerFixture(password="")
    s.start()
    yield s
    s.stop()


@pytest.fixture()
def srv_with_password():
    s = AuthServerFixture(password="secret123")
    s.start()
    yield s
    s.stop()


# ── Tests ─────────────────────────────────────────────────────────────


class TestNoPassword:
    """When password is empty, auth is disabled — dashboard served directly."""

    def test_serves_html_directly(self, srv_no_password):
        status, headers, body = _http_request(srv_no_password.port)
        assert "200 OK" in status
        # Should serve the actual dashboard HTML, not the login page
        assert body == HTML_PATH.read_text()
        assert "text/html" in headers.get("content-type", "")


class TestPasswordRequired:
    """When password is set, unauthenticated requests get the login page."""

    def test_get_root_returns_login_page(self, srv_with_password):
        status, headers, body = _http_request(srv_with_password.port)
        assert "200 OK" in status
        assert "Kart Dashboard" in body
        assert 'action="/login"' in body

    def test_wrong_password_shows_error(self, srv_with_password):
        form_body = urllib.parse.urlencode({"password": "wrong"})
        status, headers, body = _http_request(
            srv_with_password.port,
            method="POST",
            path="/login",
            body=form_body,
        )
        assert "200 OK" in status
        assert "Wrong password" in body

    def test_correct_password_redirects_with_cookie(self, srv_with_password):
        form_body = urllib.parse.urlencode({"password": "secret123"})
        status, headers, body = _http_request(
            srv_with_password.port,
            method="POST",
            path="/login",
            body=form_body,
        )
        assert "303" in status
        assert headers.get("location") == "/"
        set_cookie = headers.get("set-cookie", "")
        assert "kart_session=" in set_cookie

    def test_authenticated_request_serves_dashboard(self, srv_with_password):
        # First, log in to get the cookie
        form_body = urllib.parse.urlencode({"password": "secret123"})
        _, login_headers, _ = _http_request(
            srv_with_password.port,
            method="POST",
            path="/login",
            body=form_body,
        )
        # Extract the session token from Set-Cookie
        set_cookie = login_headers.get("set-cookie", "")
        # Parse "kart_session=<token>; Path=/; ..."
        token = set_cookie.split("kart_session=")[1].split(";")[0]

        # Now request / with the session cookie
        status, headers, body = _http_request(
            srv_with_password.port,
            headers={"Cookie": f"kart_session={token}"},
        )
        assert "200 OK" in status
        # Should serve the actual dashboard, not the login page
        assert 'action="/login"' not in body

    def test_unauthenticated_websocket_returns_403(self, srv_with_password):
        """WebSocket upgrade without a valid session cookie gets 403."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect(("127.0.0.1", srv_with_password.port))
        key = "dGhlIHNhbXBsZSBub25jZQ=="
        req = (
            f"GET /ws HTTP/1.1\r\n"
            f"Host: localhost:{srv_with_password.port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        )
        s.sendall(req.encode())
        resp = s.recv(1024)
        s.close()
        assert b"403 Forbidden" in resp
