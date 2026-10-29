#!/usr/bin/env python3
"""Start Edge Bench and open it -- the entry point every launcher calls.

  * never hard-code a port (bind 0, ask the OS what it gave us),
  * reuse a server that is already running instead of starting a second one,
  * only open the browser once *our* server answers -- verified by token,
  * NEVER fail silently: on Windows the app runs under pythonw.exe with no
    console, so a crash at start-up must become a message box and a log file.
    (The first Windows run died invisibly on a cp1252 decode of constants.json.)

  python main.py            run (opens the browser)
  python main.py --check    import everything, check the UI is built, exit 0/1
"""
from __future__ import annotations

import json
import os
import platform
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Fallback log folder, usable even when the app's own constants cannot be read.
FALLBACK_LOG = Path.home() / ".local" / "state" / "edge-bench" / "logs"
# Local probes must never go through a proxy (Windows urllib reads the system
# proxy from the registry, and a corporate proxy cannot reach 127.0.0.1).
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def fatal(text: str) -> None:
    """Show a start-up failure where the user will actually see it."""
    try:
        FALLBACK_LOG.mkdir(parents=True, exist_ok=True)
        log = FALLBACK_LOG / "crash.log"
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"\n==== {time.strftime('%Y-%m-%d %H:%M:%S')}\n{text}\n")
    except OSError:
        log = None
    msg = f"Edge Bench could not start.\n\n{text.strip().splitlines()[-1]}\n\nDetails: {log}"
    if platform.system() == "Windows" and (sys.stdout is None or "pythonw" in sys.executable.lower()):
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, msg, "Edge Bench", 0x10)   # MB_ICONERROR
            return
        except Exception:
            pass
    print(msg if sys.stderr is None else text, file=sys.stderr or sys.__stdout__)


def probe(port: int, token: str, host: str, timeout: float = 0.4) -> bool:
    """True only if the thing on this port is us."""
    try:
        with _DIRECT.open(f"http://{host}:{port}/api/whoami", timeout=timeout) as r:
            return json.load(r).get("token") == token
    except (urllib.error.URLError, OSError, ValueError):
        return False


def check() -> int:
    """Everything a start needs, without starting.  Run by the Windows launcher."""
    from app.constants import C
    import server.api  # noqa: F401  -- imports the whole app
    ui = HERE / "ui" / "dist" / "index.html"
    if not ui.is_file():
        print(f"The user interface is not built: {ui} is missing.")
        return 1
    from app import logic2
    exe = logic2.find_executable()
    print(f"{C.app.name} {C.app.version}: OK  (Python {platform.python_version()}, "
          f"Logic 2: {exe or 'not found - only CSV / binary exports will open'})")
    return 0


def run() -> int:
    from app.constants import C, path

    port_file = path("state_dir") / "port"

    def already_running() -> int | None:
        try:
            port = int(port_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None
        return port if probe(port, C.server.whoami_token, C.server.host) else None

    def open_when_ready(port: int) -> None:
        deadline = time.time() + C.server.startup_timeout_s
        while time.time() < deadline:
            if probe(port, C.server.whoami_token, C.server.host):
                time.sleep(C.server.browser_delay_s)
                webbrowser.open(f"http://{C.server.host}:{port}/")
                return
            time.sleep(C.server.probe_interval_s)
        fatal(f"{C.app.name} started but did not answer within {C.server.startup_timeout_s} s "
              f"on http://{C.server.host}:{port}/ - open that address in the browser yourself.")

    import logging
    import logging.handlers
    handler = logging.handlers.RotatingFileHandler(
        path("log_dir") / f"{C.app.id}.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])

    # An explicit --port means "serve here" (dev and the e2e suite), never "reuse".
    pinned = "--port" in sys.argv
    running = None if pinned else already_running()
    if running is not None:
        print(f"{C.app.name} is already running on port {running} - opening it.")
        if "--no-browser" not in sys.argv:
            webbrowser.open(f"http://{C.server.host}:{running}/")
        return 0

    import uvicorn
    from server.api import api

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    wanted = int(sys.argv[sys.argv.index("--port") + 1]) if pinned else C.server.port
    sock.bind((C.server.host, wanted))
    port = sock.getsockname()[1]
    sock.listen()

    if not pinned:
        port_file.write_text(str(port), encoding="utf-8")
    print(f"{C.app.name} {C.app.version} on http://{C.server.host}:{port}/")
    logging.info("serving on %s (Python %s, %s)", port, platform.python_version(), platform.platform())

    if "--no-browser" not in sys.argv:
        threading.Thread(target=open_when_ready, args=(port,), daemon=True).start()

    server = uvicorn.Server(uvicorn.Config(api, log_level="warning"))
    api.state.server = server            # lets /api/quit stop the app (and its Logic 2)
    server.run(sockets=[sock])
    try:
        if port_file.read_text(encoding="utf-8").strip() == str(port):
            port_file.unlink()
    except OSError:
        pass
    return 0


def main() -> int:
    os.chdir(HERE)
    # pythonw.exe (the Windows double-click) has no console: stdout/stderr are
    # None, and uvicorn's log handlers would fail on the first write.
    if sys.stdout is None or sys.stderr is None:
        FALLBACK_LOG.mkdir(parents=True, exist_ok=True)
        sink = open(FALLBACK_LOG / "console.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or sink
        sys.stderr = sys.stderr or sink
    try:
        return check() if "--check" in sys.argv else run()
    except SystemExit:
        raise
    except BaseException:
        fatal(traceback.format_exc())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
