"""Talking to Saleae Logic 2 through its Automation API.

Order of preference:
  1. A Logic 2 the user already has open with the automation server enabled
     (Preferences -> Automation, port 10430).  We use it and never close it.
  2. Otherwise we start our own Logic 2 on a free port with ``--automation``
     and close it when the app exits.

``Manager.launch()`` is not used: it hard-codes the port, refuses macOS without
a path, and needs pywin32 job objects on Windows.  Starting the process
ourselves is three lines and behaves the same on every platform.
"""
from __future__ import annotations

import json
import logging
import os
import platform
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from app.constants import C, path as cpath
from app.readers import binary

log = logging.getLogger(__name__)
SETTINGS = cpath("config_dir") / "settings.json"


class Logic2Error(RuntimeError):
    pass


def _expand(p: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(p)))


def configured_path() -> str | None:
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8")).get("logic2_path") or None
    except (OSError, ValueError):
        return None


def save_path(p: str | None) -> None:
    try:
        data = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data["logic2_path"] = p or None
    SETTINGS.write_text(json.dumps(data, indent=2), encoding="utf-8")


def find_executable() -> Path | None:
    override = configured_path()
    if override:
        p = _expand(override)
        if p.suffix == ".app" and p.is_dir():          # macOS bundle picked in a dialog
            p = p / "Contents" / "MacOS" / "Logic"
        return p if p.exists() else None
    for cand in C.logic2.candidates.get(platform.system(), []):
        p = _expand(cand)
        if "%" not in str(p) and p.exists():
            return p
    return None


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((C.server.host, port)) == 0


def _free_port() -> int:
    for port in range(C.logic2.own_port_first, C.logic2.own_port_first + C.logic2.own_port_tries):
        if not _port_open(port):
            return port
    raise Logic2Error("No free port for Logic 2's automation server.")


class Logic2:
    """One shared connection, one job at a time (Logic 2 is a single GUI process)."""

    def __init__(self) -> None:
        self._mgr = None
        self._proc: subprocess.Popen | None = None
        self._port: int | None = None
        self._lock = threading.Lock()
        self.version: str | None = None
        self.mode: str = "idle"          # idle | user | own

    # -- connection -------------------------------------------------------
    def status(self) -> dict:
        exe = find_executable()
        return {"mode": self.mode, "port": self._port, "version": self.version,
                "executable": str(exe) if exe else None,
                "configured": configured_path(),
                "user_port_open": _port_open(C.logic2.user_port)}

    def _connect(self, port: int, timeout: float):
        from saleae import automation
        # Never through a proxy: grpc honours http(s)_proxy, which work PCs often set,
        # and a corporate proxy cannot reach Logic 2 on 127.0.0.1.
        return automation.Manager.connect(port=port, connect_timeout_seconds=timeout,
                                          grpc_channel_arguments=[("grpc.enable_http_proxy", 0)])

    def _ensure(self):
        if self._mgr is not None:
            try:
                self._mgr.get_app_info()
                return self._mgr
            except Exception:                       # Logic 2 was closed under us
                log.warning("Lost Logic 2 connection, reconnecting")
                self._drop()

        if _port_open(C.logic2.user_port):
            try:
                self._mgr = self._connect(C.logic2.user_port, 5)
                self._port, self.mode = C.logic2.user_port, "user"
            except Exception as exc:
                log.warning("Port %s open but not Logic 2 automation: %s", C.logic2.user_port, exc)
        if self._mgr is None:
            exe = find_executable()
            if exe is None:
                raise Logic2Error(
                    "Logic 2 was not found. Install it from saleae.com, or set its path in "
                    "Settings → Logic 2 path.")
            port = _free_port()
            flags = 0
            if platform.system() == "Windows":
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            log.info("Starting Logic 2: %s on port %s", exe, port)
            self._proc = subprocess.Popen([str(exe), C.logic2.cli_args[0], C.logic2.cli_args[1], str(port)],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                          creationflags=flags)
            deadline = time.time() + C.logic2.connect_timeout_s
            while time.time() < deadline and not _port_open(port):
                if self._proc.poll() is not None:
                    raise Logic2Error(
                        f"Logic 2 exited straight away (code {self._proc.returncode}). If another "
                        "Logic 2 window is open, enable its automation server (Preferences → "
                        "Automation) or close it, then try again.")
                time.sleep(C.logic2.export_poll_s)
            try:
                self._mgr = self._connect(port, max(1.0, deadline - time.time()))
            except Exception as exc:
                self._drop()
                raise Logic2Error(f"Logic 2 started but its automation server did not answer: {exc}") from exc
            self._port, self.mode = port, "own"
        self.version = self._mgr.get_app_info().app_version
        return self._mgr

    def _drop(self) -> None:
        try:
            if self._mgr is not None:
                self._mgr.close()
        except Exception:
            pass
        self._mgr = None
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc, self._port, self.mode = None, None, "idle"

    def close(self) -> None:
        with self._lock:
            self._drop()

    # -- export ------------------------------------------------------------
    def export_binary(self, sal: Path, out_dir: Path, channels: list[int] | None,
                      progress: Callable[[str], None] | None = None) -> list[Path]:
        say = progress or (lambda _m: None)
        with self._lock:
            say("Connecting to Logic 2")
            mgr = self._ensure()
            say("Logic 2 is opening the capture")
            try:
                cap = mgr.load_capture(str(sal.resolve()))
            except Exception as exc:
                raise Logic2Error(f"Logic 2 could not open {sal.name}: {exc}") from exc
            try:
                say("Exporting edges (binary)")
                tried = [channels] if channels else [list(range(n)) for n in C.logic2.probe_channels]
                last: Exception | None = None
                for chans in tried:
                    try:
                        cap.export_raw_data_binary(str(out_dir.resolve()), digital_channels=chans)
                        last = None
                        break
                    except Exception as exc:     # wrong channel set for this device
                        last = exc
                if last is not None:
                    raise Logic2Error(f"Logic 2 refused the export: {last}") from last
            finally:
                try:
                    cap.close()
                except Exception:
                    pass
        say("Checking exported files")
        return wait_complete(out_dir, len(channels) if channels else None)


def wait_complete(folder: Path, expected: int | None) -> list[Path]:
    """Block until every exported file is as long as its header says.

    Logic 2 returns from export_raw_data_binary before writing has finished
    (reported on the Saleae forum), so 'the call returned' proves nothing.
    """
    deadline = time.time() + C.logic2.export_complete_timeout_s
    stable, last_sizes = 0, None
    while time.time() < deadline:
        files = [p for _, p in binary.bin_files(folder)]
        ok = bool(files) and (expected is None or len(files) >= expected)
        if ok:
            for p in files:
                try:
                    binary.read_header(p)
                except (binary.BinaryFormatError, OSError):
                    ok = False
                    break
        sizes = tuple(p.stat().st_size for p in files)
        stable = stable + 1 if (ok and sizes == last_sizes) else 0
        last_sizes = sizes
        if stable >= C.logic2.export_stable_polls:
            return files
        time.sleep(C.logic2.export_poll_s)
    raise Logic2Error(f"Export did not complete within {C.logic2.export_complete_timeout_s} s "
                      f"({folder}).")
