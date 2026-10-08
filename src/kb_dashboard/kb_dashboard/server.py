"""HTTP + WebSocket server — no ROS dependencies.

Features:
- Single controller token: only one browser can send manual_control at a time.
  Others see who has control and can "Take Control" to steal it.
- Non-blocking broadcast: slow clients don't block the event loop.
"""

import asyncio
import base64
import hashlib
import json
import math
import secrets
from pathlib import Path
from http.cookies import SimpleCookie

from kb_dashboard.protocol import DashboardState, MISSIONS

HTML_PATH = Path(__file__).parent / "index.html"
# Same directory as the page itself: both ship as package_data, so they land wherever the
# installed package does and neither needs to know the workspace layout.
ICON_DIR = Path(__file__).parent
TELEMETRY_REFRESH_HZ = 60
HUD_FRAME_INTERVAL_S = 0.3

# Shell command that powers the Orin down. Named here rather than inlined so tests can
# swap it for something harmless — the flow around it (acknowledge, then report a refusal)
# is worth testing, and running the real one would take the developer's machine down.
# The delay gives the acknowledgement time to reach the browser before the network dies.
POWEROFF_CMD = "sleep 3 && echo 0 | sudo -S poweroff"

LOGIN_HTML = """\
<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Kart Dashboard — Login</title>
<!-- The same Home Screen icon the dashboard declares. It has to be repeated here because iOS
     reads the icon from whatever page is on screen when Add to Home Screen is tapped, and an
     expired session puts this page there — without these the app would be added with a
     screenshot of the login box as its icon. The web-app meta tags come along for the same
     reason: they decide how it launches, and the launch may well land on this page. -->
<link rel="apple-touch-icon" href="/icon-180.png">
<link rel="icon" type="image/png" sizes="512x512" href="/icon-512.png">
<link rel="manifest" href="/manifest.webmanifest">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Kart">
<meta name="theme-color" content="#111113">
<style>
  body { background: #0a0a0f; color: #e0e0e0; font-family: Inter, sans-serif;
         display: flex; justify-content: center; align-items: center; min-height: 100vh; }
  form { background: #14141f; padding: 32px; border-radius: 12px; text-align: center;
         box-shadow: 0 4px 24px rgba(0,0,0,0.5); }
  h1 { font-size: 18px; margin-bottom: 20px; }
  input { background: #1e1e2e; color: #fff; border: 1px solid #333; border-radius: 8px;
          padding: 10px 14px; font-size: 16px; width: 160px; text-align: center; }
  button { background: #2563eb; color: #fff; border: none; border-radius: 8px;
           padding: 10px 24px; font-size: 16px; cursor: pointer; margin-top: 12px; }
  button:active { background: #1d4ed8; }
  .err { color: #f87171; font-size: 13px; margin-top: 8px; }
</style>
</head><body>
<form method="POST" action="/login" autocomplete="on">
  <h1>Kart Dashboard</h1>
  <input type="text" name="username" id="username" value="kart" autocomplete="username"
         style="position:absolute;left:-9999px;width:1px;height:1px;overflow:hidden" tabindex="-1" aria-hidden="true">
  <input type="password" name="password" id="password" placeholder="Password" autocomplete="current-password" autofocus><br>
  <button type="submit">Enter</button>
  $error$
</form>
</body></html>
"""


def _parse_cookies(header_str: str) -> dict[str, str]:
    """Parse a Cookie header into a dict."""
    cookies = {}
    for item in header_str.split(";"):
        if "=" in item:
            k, v = item.strip().split("=", 1)
            cookies[k.strip()] = v.strip()
    return cookies


def validate_bench_action(cmd):
    if cmd.get("action") == "set_bench_mode":
        value = cmd.get("enabled")
        if type(value) is not bool:
            raise ValueError("Bench enabled must be a boolean")
    else:
        value = cmd.get("percent")
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 30:
            raise ValueError("Bench throttle must be a finite number from 0 to 30%")
    return value


async def run_websocket_server(
    state: DashboardState, node, port: int, ready_callback=None, password: str = ""
):
    """@brief HTTP + WebSocket server for the dashboard.

    @param state Shared DashboardState for telemetry snapshots.
    @param node ROS node with publish_mission(), publish_state_cmd(), publish_manual_control().
    @param port TCP port to listen on.
    @param ready_callback Optional callable invoked once the server is listening, receiving
           the port actually bound. Pass port=0 to let the OS choose one and learn it here —
           that is race-free, unlike picking a free port beforehand and binding it later.
    @param password If non-empty, require this password to access the dashboard.
    """
    # Derive session token from password so cookies survive server restarts.
    auth_token = hashlib.sha256(password.encode()).hexdigest()[:32]

    # Restore SVO selection from /tmp/kart_svo_path (survives restarts)
    _svo_path_file = Path("/tmp/kart_svo_path")
    if _svo_path_file.is_file():
        _svo_val = _svo_path_file.read_text().strip()
        if _svo_val:
            state.update("svo_file", Path(_svo_val).name)

    clients: dict[asyncio.StreamWriter, str] = {}  # writer → client_id
    controller: dict = {"holder": None, "id": None}  # who has manual control
    _next_id = [0]

    def _make_id():
        _next_id[0] += 1
        return f"browser-{_next_id[0]}"

    async def ws_accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        request = b""
        while True:
            line = await reader.readline()
            if not line:
                writer.close()
                return
            request += line
            if line == b"\r\n":
                break

        request_str = request.decode(errors="replace")
        first_line = request_str.split("\r\n")[0]
        parts = first_line.split(" ")
        method = parts[0] if parts else "GET"
        path = parts[1] if len(parts) > 1 else "/"

        headers = {}
        for line_str in request_str.split("\r\n")[1:]:
            if ": " in line_str:
                k, v = line_str.split(": ", 1)
                headers[k.lower()] = v.strip()

        # Detect whether the original client used HTTPS. The Orin server itself
        # speaks plain HTTP; TLS terminates at cloudflared, which forwards to
        # 127.0.0.1 and sets X-Forwarded-Proto. Only trust that header when the
        # peer is loopback — otherwise a LAN client could spoof it.
        peer = writer.get_extra_info("peername")
        peer_host = peer[0] if peer else ""
        trust_xfp = peer_host in ("127.0.0.1", "::1", "::ffff:127.0.0.1")
        is_https = trust_xfp and headers.get("x-forwarded-proto", "http").lower() == "https"
        cookie_secure = "; Secure" if is_https else ""

        # --- Auth check ---
        def _is_authenticated() -> bool:
            if not password:
                return True
            cookies = _parse_cookies(headers.get("cookie", ""))
            return cookies.get("kart_session") == auth_token

        # Handle login POST
        if path == "/login" and method == "POST":
            # Read POST body (Content-Length)
            body_len = int(headers.get("content-length", "0"))
            body_data = b""
            if body_len > 0:
                body_data = await reader.readexactly(body_len)
            # Parse form: password=xxx
            form = {}
            for pair in body_data.decode(errors="replace").split("&"):
                if "=" in pair:
                    fk, fv = pair.split("=", 1)
                    form[fk] = fv
            if form.get("password") == password:
                resp = (
                    f"HTTP/1.1 303 See Other\r\n"
                    f"Location: /\r\n"
                    f"Set-Cookie: kart_session={auth_token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=31536000{cookie_secure}\r\n"
                    f"Connection: close\r\n\r\n"
                ).encode()
            else:
                err_body = LOGIN_HTML.replace("$error$",'<p class="err">Wrong password</p>').encode()
                resp = (
                    f"HTTP/1.1 200 OK\r\n"
                    f"Content-Type: text/html; charset=utf-8\r\n"
                    f"Content-Length: {len(err_body)}\r\n"
                    f"Connection: close\r\n\r\n"
                ).encode() + err_body
            writer.write(resp)
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            return

        conn_header = headers.get("connection", "").lower()
        upgrade_header = headers.get("upgrade", "").lower()
        if "upgrade" in conn_header and "websocket" in upgrade_header:
            # Reject unauthenticated WebSocket upgrades
            if not _is_authenticated():
                writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
                await writer.drain()
                writer.close()
                await writer.wait_closed()
                return
            key = headers.get("sec-websocket-key", "").strip()
            accept = base64.b64encode(
                hashlib.sha1(
                    (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()
                ).digest()
            ).decode()
            writer.write(
                f"HTTP/1.1 101 Switching Protocols\r\n"
                f"Upgrade: websocket\r\n"
                f"Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n\r\n".encode()
            )
            await writer.drain()
            client_id = _make_id()
            clients[writer] = client_id
            node.get_logger().info(f"WS connected: {client_id}")
            # Send welcome with client ID
            ws_send(writer, json.dumps({"your_id": client_id}).encode())
            try:
                await handle_ws(reader, writer, client_id, state, node)
            finally:
                clients.pop(writer, None)
                if controller["holder"] is writer:
                    controller["holder"] = None
                    controller["id"] = None
                    node.get_logger().info(f"Controller released: {client_id} disconnected")
                node.get_logger().info(f"WS disconnected: {client_id}")
            return

        # Home Screen icon and web-app manifest. Deliberately BEFORE the auth gate: iOS
        # fetches apple-touch-icon while the user is adding the page to the Home Screen, and
        # it does not always carry the session cookie when it does — behind the gate the
        # request would be answered with the login page, and the icon would silently fall back
        # to a screenshot of the login screen. Nothing here is telemetry; it is a logo and a
        # few strings naming the app.
        #
        # The icons are real files rather than data: URIs because iOS ignores data: URIs for
        # apple-touch-icon. They carry the UMotorsport logo composited onto the skin's
        # background: the source PNG has an alpha channel, and iOS flattens transparency onto
        # black rather than onto the theme colour.
        if path in ("/icon-180.png", "/icon-512.png"):
            icon = ICON_DIR / path.lstrip("/")
            if icon.exists():
                body = icon.read_bytes()
                header = (
                    f"HTTP/1.1 200 OK\r\n"
                    f"Content-Type: image/png\r\n"
                    f"Content-Length: {len(body)}\r\n"
                    f"Connection: close\r\n"
                    f"Cache-Control: max-age=86400\r\n\r\n"
                ).encode()
                writer.write(header + body)
            else:
                writer.write(
                    b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                )
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            return

        if path == "/manifest.webmanifest":
            # Android/Chrome read this to install the page as an app. iOS uses the apple-*
            # meta tags in index.html instead and ignores most of what is here.
            manifest = json.dumps({
                "name": "Kart Dashboard",
                "short_name": "Kart",
                "start_url": "/",
                "display": "standalone",
                "orientation": "landscape",
                "background_color": "#111113",
                "theme_color": "#111113",
                "icons": [
                    {"src": "/icon-180.png", "sizes": "180x180", "type": "image/png"},
                    {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"},
                ],
            }).encode()
            header = (
                f"HTTP/1.1 200 OK\r\n"
                f"Content-Type: application/manifest+json\r\n"
                f"Content-Length: {len(manifest)}\r\n"
                f"Connection: close\r\n"
                f"Cache-Control: max-age=3600\r\n\r\n"
            ).encode()
            writer.write(header + manifest)
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            return

        # Serve login page if not authenticated
        if not _is_authenticated():
            body = LOGIN_HTML.replace("$error$","").encode()
            header = (
                f"HTTP/1.1 200 OK\r\n"
                f"Content-Type: text/html; charset=utf-8\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"Connection: close\r\n"
                f"Cache-Control: no-cache\r\n\r\n"
            ).encode()
            writer.write(header + body)
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            return

        # Serve HTML
        if path == "/" or path == "/index.html":
            body = HTML_PATH.read_bytes()
            header = (
                f"HTTP/1.1 200 OK\r\n"
                f"Content-Type: text/html; charset=utf-8\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"Connection: close\r\n"
                f"Cache-Control: no-cache\r\n\r\n"
            ).encode()
            writer.write(header + body)
        elif path == "/network-info":
            # Live read of the Orin's LAN addresses — used by the topbar (i)
            # tooltip so the fallback URL stays correct even if DHCP changes.
            import json as _json
            import socket as _socket
            import subprocess as _subprocess
            try:
                res = _subprocess.run(
                    ["ip", "-o", "-4", "addr", "show", "scope", "global"],
                    capture_output=True, text=True, timeout=2,
                )
                ifaces = []
                for line in res.stdout.strip().splitlines():
                    parts = line.split()
                    if len(parts) >= 4:
                        ifaces.append({"iface": parts[1], "ip": parts[3].split("/")[0]})
            except Exception:
                ifaces = []
            info_body = _json.dumps({
                "hostname": _socket.gethostname(),
                "interfaces": ifaces,
                "port": port,
            }).encode()
            header = (
                f"HTTP/1.1 200 OK\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(info_body)}\r\n"
                f"Connection: close\r\n"
                f"Cache-Control: no-cache\r\n\r\n"
            ).encode()
            writer.write(header + info_body)
        elif path == "/favicon.ico":
            # Browsers request this on every page load whether or not the document asks
            # for one, so without a route it logged a 404 in the console on every visit.
            # A real icon is declared inline in index.html's <head>; this route exists so
            # the automatic request is answered rather than left as noise that trains the
            # reader to ignore console errors. 204 No Content is the answer that says
            # "there is deliberately nothing here", which is true and costs one packet.
            writer.write(
                b"HTTP/1.1 204 No Content\r\nCache-Control: max-age=86400\r\n"
                b"Connection: close\r\n\r\n"
            )
        else:
            writer.write(
                b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
            )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def handle_ws(reader, writer, client_id, state, node):
        while True:
            try:
                header = await reader.readexactly(2)
            except (asyncio.IncompleteReadError, ConnectionError, OSError):
                break

            opcode = header[0] & 0x0F
            if opcode == 0x8:  # close
                break
            if opcode == 0x9:  # ping → pong
                ws_send(writer, b"", opcode=0xA)
                continue

            masked = bool(header[1] & 0x80)
            length = header[1] & 0x7F
            if length == 126:
                length = int.from_bytes(await reader.readexactly(2), "big")
            elif length == 127:
                length = int.from_bytes(await reader.readexactly(8), "big")

            if masked:
                mask = await reader.readexactly(4)
                raw = await reader.readexactly(length)
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(raw))
            else:
                data = await reader.readexactly(length)

            if opcode == 0x1:  # text
                try:
                    cmd = json.loads(data.decode())
                    handle_command(cmd, writer, client_id, state, node)
                except Exception as e:
                    node.get_logger().warn(f"WS cmd error from {client_id}: {e}")

    def handle_command(cmd: dict, writer, client_id, state, node):
        action = cmd.get("action")
        if action == "set_mission":
            mission = cmd.get("mission", "manual")
            if mission in MISSIONS:
                state.update("mission", mission)
                node.publish_mission(mission)
                # Release control token when switching away from remote_control
                if mission != "remote_control" and controller["holder"] is writer:
                    controller["holder"] = None
                    controller["id"] = None
        elif action == "set_state":
            new_state = cmd.get("state", "idle")
            if new_state in ("idle", "running", "ebs", "reset"):
                # Only /kart/state confirms a transition; Start can be rejected.
                cmd_map = {"idle": "stop", "running": "start", "ebs": "ebs", "reset": "reset"}
                if hasattr(node, "publish_state_cmd"):
                    node.publish_state_cmd(cmd_map[new_state])
        elif action in ("set_bench_mode", "set_bench_throttle"):
            value = validate_bench_action(cmd)
            if action == "set_bench_mode":
                node.publish_bench_mode(value)
            else:
                node.publish_bench_throttle(value)
        elif action == "take_control":
            old_id = controller["id"]
            controller["holder"] = writer
            controller["id"] = client_id
            if old_id and old_id != client_id:
                node.get_logger().info(f"Control taken by {client_id} (was {old_id})")
            else:
                node.get_logger().info(f"Control acquired by {client_id}")
        elif action == "release_control":
            if controller["holder"] is writer:
                controller["holder"] = None
                controller["id"] = None
                node.get_logger().info(f"Control released by {client_id}")
        elif action == "set_controller":
            ctrl_type = cmd.get("type", "geometric")
            state.update("controller_type", ctrl_type)
            if hasattr(node, "publish_controller_type"):
                node.publish_controller_type(ctrl_type)
        elif action == "set_target_speed":
            # Only the negative side is rejected. The upper clamp was removed on
            # request (Rubén, 2026-08-10) along with the controller's own ceiling;
            # a negative setpoint is still refused because it is a sign error, not
            # a slower kart — the loop commands throttle and cannot brake.
            speed = max(0.0, float(cmd.get("speed", 0.28)))
            state.update("target_speed", speed)
            if hasattr(node, "publish_target_speed"):
                node.publish_target_speed(speed)
        elif action == "set_speed_controller":
            speed_type = cmd.get("type", "curve_factor")
            state.update("speed_controller_type", speed_type)
            if hasattr(node, "publish_speed_controller_type"):
                node.publish_speed_controller_type(speed_type)
        elif action == "set_steer_mode":
            mode = cmd.get("mode", "pid")  # "pid" or "pwm"
            if hasattr(node, "publish_steer_mode"):
                node.publish_steer_mode(1 if mode == "pwm" else 0)
        elif action == "set_steer_pid":
            # Live steering PID tuning, so a gain can be tried without reflashing the
            # ESP32. Gated on the controller token: this moves the steering column of
            # whoever currently has the joystick, and two browsers pushing different
            # gains at each other would be untraceable from either one.
            #
            # No validation here beyond the float conversion. encode_steer_pid clamps
            # to the PID_MAX_* bounds, and the firmware clamps again on arrival — that
            # second clamp is the one that protects the gears, because it is the only
            # one that still applies when the command comes from something other than
            # this dashboard.
            # Auto-acquire when nobody holds control, exactly as manual_control does. Without
            # this, opening the page and pressing Apply did nothing at all and said nothing —
            # the token is invisible until you touch the joystick, so the button read as broken.
            if controller["holder"] is None:
                controller["holder"] = writer
                controller["id"] = client_id
                node.get_logger().info(f"Control auto-acquired by {client_id} (PID tuning)")
            if controller["holder"] is not writer:
                # Someone else is driving. Refuse, but say so on the wire rather than only in
                # the Orin's log, which the person pressing the button cannot see.
                node.get_logger().warn(
                    f"PID tuning from {client_id} refused — {controller['id']} holds control"
                )
                ws_send(writer, json.dumps({
                    "steer_pid_error": f"{controller['id']} holds control — press Take Control first"
                }).encode())
            elif hasattr(node, "publish_steer_pid"):
                restore = bool(cmd.get("restore_defaults", False))
                try:
                    node.publish_steer_pid(
                        kp=float(cmd.get("kp", 0.0)),
                        ki=float(cmd.get("ki", 0.0)),
                        kd=float(cmd.get("kd", 0.0)),
                        pwm_limit=float(cmd.get("pwm_limit", 0.0)),
                        override=not restore,
                    )
                except (TypeError, ValueError):
                    node.get_logger().warn(f"Malformed PID tuning from {client_id} ignored")
        elif action == "set_compressor":
            # Stops the EBS compressor so the kart is quiet to work on. Disabling it
            # also forces emergency, because a kart that cannot refill its air
            # reservoir must not look ready to drive. The interlock itself lives in
            # the ESP32 firmware, which opens the shutdown circuit whenever this
            # latch is set — that is what makes it survive an Orin restart or a
            # dropped frame. The AS state change below only keeps the Orin's own
            # state machine in step with what the firmware has already done.
            #
            # Not gated on the controller token, matching set_state / the EBS
            # button above. The token decides who holds the joystick in
            # remote_control; a safety control that silently did nothing until you
            # pressed "Take Control" would be indistinguishable from a broken button.
            disabled = bool(cmd.get("disabled", False))
            state.update("compressor_disabled", disabled)
            if hasattr(node, "publish_compressor_disable"):
                node.publish_compressor_disable(disabled)
            if disabled:
                node.get_logger().warn(f"Compressor disabled by {client_id} — forcing emergency")
                state.update("state", "ebs")
                if hasattr(node, "publish_state_cmd"):
                    node.publish_state_cmd("ebs")
            else:
                node.get_logger().info(f"Compressor re-enabled by {client_id}")
        elif action == "list_svo":
            import glob as _glob
            svo_dir = Path.home() / "kart-brain" / "data" / "svo"
            files = sorted(p.name for p in svo_dir.glob("*.svo")) if svo_dir.is_dir() else []
            resp = json.dumps({"svo_files": files}).encode()
            ws_send(writer, resp)
        elif action == "set_svo":
            svo_name = cmd.get("file", "live")
            svo_path_file = Path("/tmp/kart_svo_path")
            if svo_name == "live":
                try:
                    svo_path_file.unlink()
                except FileNotFoundError:
                    pass
            else:
                svo_full = str(Path.home() / "kart-brain" / "data" / "svo" / svo_name)
                svo_path_file.write_text(svo_full)
            state.update("svo_file", svo_name)
            node.get_logger().info(f"SVO set to: {svo_name} (restart to apply)")
        elif action == "restart":
            node.get_logger().warn(f"Restart requested by {client_id}")
            import subprocess
            subprocess.Popen("echo 0 | sudo -S systemctl restart kart-brain",
                             shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif action == "shutdown_orin":
            node.get_logger().warn(f"Orin shutdown requested by {client_id}")
            # Acknowledge before anything else. The browser cannot tell a machine that
            # powered off from a command that never arrived — both are just a socket that
            # stopped answering — so it needs to hear that the request landed while there
            # is still a server alive to say so.
            ws_send(writer, json.dumps({"shutdown_orin": "starting", "delay_s": 3}).encode())
            asyncio.get_running_loop().create_task(_poweroff(writer, node))
        elif action == "manual_control":
            # Auto-acquire control on first manual_control if nobody has it
            if controller["holder"] is None:
                controller["holder"] = writer
                controller["id"] = client_id
                node.get_logger().info(f"Control auto-acquired by {client_id}")
            # Only the controller can send manual commands
            if controller["holder"] is writer:
                if hasattr(node, "publish_manual_control"):
                    node.publish_manual_control(
                        steer=float(cmd.get("steering", 0.0)),
                        steer_type=cmd.get("steer_type", "angle"),
                        throttle=float(cmd.get("throttle", 0.0)),
                        brake=float(cmd.get("brake", 0.0)),
                    )

    async def _poweroff(writer, node):
        """Power the Orin off, and report back if it refuses.

        Reaching the end of this function at all means the machine is still up, because a
        successful power-off takes the process down with everything else. `poweroff` on a
        systemd host returns 0 as soon as the transition is queued, so only a non-zero exit
        is a failure — and that is the case worth reporting, since a failed sudo otherwise
        leaves the browser waiting forever for a shutdown that was never going to happen.
        """
        import subprocess
        # A plain Popen waited on in a worker thread, rather than
        # asyncio.create_subprocess_shell: the asyncio version installs a child watcher on
        # the event loop, and this command usually ends with the loop being destroyed
        # underneath it by the power cut. One idle thread for the few seconds it lasts is
        # the cheaper side of that trade.
        proc = subprocess.Popen(POWEROFF_CMD, shell=True,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        _, stderr = await asyncio.get_running_loop().run_in_executor(None, proc.communicate)
        if proc.returncode == 0:
            return
        # sudo -S echoes its password prompt to stderr even when it succeeds; the real
        # reason is whatever it said after that.
        lines = [ln.strip() for ln in (stderr or b"").decode(errors="replace").splitlines()
                 if ln.strip() and "password for" not in ln.lower()]
        reason = lines[-1] if lines else f"poweroff exited with code {proc.returncode}"
        node.get_logger().error(f"Orin poweroff failed: {reason}")
        try:
            ws_send(writer, json.dumps({"shutdown_orin": "failed", "error": reason}).encode())
        except Exception:
            pass  # client already gone — the log line above is the record

    def ws_send(writer: asyncio.StreamWriter, data: bytes, opcode=0x1):
        frame = bytearray()
        frame.append(0x80 | opcode)
        if len(data) < 126:
            frame.append(len(data))
        elif len(data) < 65536:
            frame.append(126)
            frame.extend(len(data).to_bytes(2, "big"))
        else:
            frame.append(127)
            frame.extend(len(data).to_bytes(8, "big"))
        frame.extend(data)
        try:
            writer.write(bytes(frame))
        except Exception:
            clients.pop(writer, None)

    async def broadcast_loop():
        loop = asyncio.get_running_loop()
        last_hud_at = loop.time()
        while True:
            await asyncio.sleep(1 / TELEMETRY_REFRESH_HZ)
            if not clients:
                continue

            # Build snapshot with controller info
            snap = state.snapshot()
            snap["controller"] = controller["id"]

            snapshot_bytes = json.dumps(snap).encode()
            dead = set()
            for w in list(clients):
                try:
                    ws_send(w, snapshot_bytes)
                    # Non-blocking drain with timeout to prevent slow clients from stalling
                    await asyncio.wait_for(w.drain(), timeout=0.5)
                except Exception:
                    dead.add(w)
            for w in dead:
                clients.pop(w, None)
                if controller["holder"] is w:
                    controller["holder"] = None
                    controller["id"] = None

            # Keep camera bandwidth independent of the telemetry refresh rate.
            if loop.time() - last_hud_at >= HUD_FRAME_INTERVAL_S and hasattr(node, "get_hud_jpeg"):
                last_hud_at = loop.time()
                jpeg = node.get_hud_jpeg()
                if jpeg:
                    for w in list(clients):
                        # Skip frame if client's write buffer is backed up (> 2 frames)
                        transport = w.transport
                        if transport and transport.get_write_buffer_size() > 2 * len(jpeg):
                            continue
                        try:
                            ws_send(w, jpeg, opcode=0x2)
                            await asyncio.wait_for(w.drain(), timeout=0.5)
                        except Exception:
                            clients.pop(w, None)

    import socket

    server = await asyncio.start_server(
        ws_accept,
        "0.0.0.0",
        port,
        reuse_address=True,
        start_serving=False,
    )
    for s in server.sockets:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    await server.start_serving()
    bound_port = server.sockets[0].getsockname()[1]
    node.get_logger().info(f"Web server listening on 0.0.0.0:{bound_port}")
    if ready_callback:
        ready_callback(bound_port)

    try:
        await asyncio.gather(server.serve_forever(), broadcast_loop())
    except asyncio.CancelledError:
        pass
    finally:
        server.close()
        await server.wait_closed()
