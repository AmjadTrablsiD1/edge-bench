#!/usr/bin/env python3
"""Start Edge Bench and open it -- the entry point every launcher calls.

  * never hard-code a port (bind 0, ask the OS what it gave us),
  * reuse a server that is already running instead of starting a second one,
  * only open the browser once *our* server answers -- verified by token.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

from app.constants import C, path

PORT_FILE = path("state_dir") / "port"


def _logging() -> None:
    handler = logging.handlers.RotatingFileHandler(
        path("log_dir") / f"{C.app.id}.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def probe(port: int, timeout: float = 0.4) -> bool:
    """True only if the thing on this port is us."""
    url = f"http://{C.server.host}:{port}/api/whoami"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.load(r).get("token") == C.server.whoami_token
    except (urllib.error.URLError, OSError, ValueError):
        return False


def already_running() -> int | None:
    try:
        port = int(PORT_FILE.read_text().strip())
    except (OSError, ValueError):
        return None
    return port if probe(port) else None


def open_when_ready(port: int) -> None:
    deadline = time.time() + C.server.startup_timeout_s
    while time.time() < deadline:
        if probe(port):
            time.sleep(C.server.browser_delay_s)
            webbrowser.open(f"http://{C.server.host}:{port}/")
            return
        time.sleep(C.server.probe_interval_s)
    logging.error("%s did not answer within %ss", C.app.name, C.server.startup_timeout_s)


def main() -> int:
    # pythonw.exe (the Windows double-click) has no console: stdout/stderr are
    # None, and uvicorn's log handlers would fail on the first write.
    if sys.stdout is None or sys.stderr is None:
        sink = open(path("log_dir") / "console.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or sink
        sys.stderr = sys.stderr or sink
    _logging()
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
        PORT_FILE.write_text(str(port))
    print(f"{C.app.name} {C.app.version} on http://{C.server.host}:{port}/")
    logging.info("serving on %s", port)

    if "--no-browser" not in sys.argv:
        threading.Thread(target=open_when_ready, args=(port,), daemon=True).start()

    server = uvicorn.Server(uvicorn.Config(api, log_level="warning"))
    api.state.server = server            # lets /api/quit stop the app (and its Logic 2)
    server.run(sockets=[sock])
    try:
        if PORT_FILE.read_text().strip() == str(port):
            PORT_FILE.unlink()
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
