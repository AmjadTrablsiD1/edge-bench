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
import shutil
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


def _job_for(proc: subprocess.Popen, memory_limit: int | None = None):
    """Windows: put Logic 2 in a job that dies with its handle (and with us).

    With `memory_limit` the job may commit at most that many bytes in total, so
    a capture too big for this PC makes Logic 2 fail instead of the PC freezing.

    Logic.exe is Electron: the main process starts GPU, renderer and helper
    processes.  terminate() ends only the main one, so without a job every
    failed start or quit could leave a headless Logic 2 behind, holding memory.
    Returns None elsewhere or if the job cannot be made (then only the main
    process is stopped, as before).
    """
    if platform.system() != "Windows":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", ctypes.c_uint64 * 6),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        job = k32.CreateJobObjectW(None, None)
        if not job:
            raise OSError(ctypes.get_last_error(), "CreateJobObject")
        info = EXTENDED()
        info.BasicLimitInformation.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if memory_limit:
            info.BasicLimitInformation.LimitFlags |= 0x200      # JOB_OBJECT_LIMIT_JOB_MEMORY
            info.JobMemoryLimit = int(memory_limit)
        if not (k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))   # extended limits
                and k32.AssignProcessToJobObject(job, int(proc._handle))):
            err = ctypes.get_last_error()
            k32.CloseHandle(job)
            raise OSError(err, "job setup")
        return job
    except Exception as exc:
        log.warning("Logic 2 job object not set up (%s); only its main process will be stopped", exc)
        return None


def _close_job(job) -> None:
    if job:
        import ctypes
        ctypes.WinDLL("kernel32").CloseHandle(job)      # kills every process still in the job


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
        self._job = None
        self.memory_limit: int | None = None     # bytes Logic 2 may commit (Windows job)
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
            from app import guard
            ram = guard.memory()
            self.memory_limit = int(C.logic2.job_memory_frac * ram[1]) if ram else None
            self._job = _job_for(self._proc, self.memory_limit)
            if self._job is None:
                self.memory_limit = None
            deadline = time.time() + C.logic2.connect_timeout_s
            while time.time() < deadline and not _port_open(port):
                code = self._proc.poll()
                if code is not None:
                    self._drop()              # its helper processes may still be running
                    raise Logic2Error(
                        f"Logic 2 exited straight away (code {code}). If another "
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
        _close_job(self._job)                 # and Logic 2's helper processes
        self._proc, self._job, self._port, self.mode = None, None, None, "idle"

    def close(self) -> None:
        with self._lock:
            self._drop()

    # -- export ------------------------------------------------------------
    def _stop_own(self) -> None:
        """End an export early.  Only a Logic 2 we started can be stopped."""
        if self.mode == "own":
            log.warning("Stopping our Logic 2 to end the export")
            self._drop()

    def export_binary(self, sal: Path, out_dir: Path, channels: list[int] | None,
                      progress: Callable[[str], None] | None = None) -> list[Path]:
        say = progress or (lambda _m: None)
        _check_disk(out_dir)
        with self._lock:
            say("Connecting to Logic 2")
            mgr = self._ensure()
            say("Logic 2 is opening the capture")
            try:
                cap = mgr.load_capture(str(sal.resolve()))
            except Exception as exc:
                raise Logic2Error(f"Logic 2 could not open {sal.name}: {exc}{self._limit_note()}") from exc
            watch = ExportWatch(out_dir, say, self._stop_own)
            try:
                say("Exporting edges (binary)")
                tried = [channels] if channels else [list(range(n)) for n in C.logic2.probe_channels]
                last: Exception | None = None
                for chans in tried:
                    try:
                        cap.export_raw_data_binary(str(out_dir.resolve()), digital_channels=chans)
                        last = None
                        break
                    except Exception as exc:     # wrong channel set for this device -- or stopped
                        watch.check()
                        last = exc
                if last is not None:
                    raise Logic2Error(f"Logic 2 refused the export: {last}{self._limit_note()}") from last
            except BaseException:
                watch.close()
                raise
            finally:
                try:
                    cap.close()
                except Exception:
                    pass
        try:
            say("Checking exported files")
            return wait_complete(out_dir, len(channels) if channels else None, check=watch.check)
        finally:
            watch.close()

    def _limit_note(self) -> str:
        if not self.memory_limit:
            return ""
        return (f" (Logic 2 may use at most {self.memory_limit / 2**30:.0f} GB of RAM here, so that a "
                "capture too big for this PC cannot freeze it.)")


def _check_disk(folder: Path) -> None:
    free = shutil.disk_usage(folder).free
    if free < C.logic2.min_free_disk_gb * 1e9:
        raise Logic2Error(f"Only {free / 1e9:.1f} GB free on the disk of {folder}; exporting a "
                          f"capture needs more than {C.logic2.min_free_disk_gb} GB. Free some space "
                          "or clear the export cache (App tab).")


def _bytes_in(folder: Path) -> int:
    try:
        return sum(p.stat().st_size for p in folder.iterdir() if p.is_file())
    except OSError:
        return 0


class ExportWatch:
    """Watches an export folder while Logic 2 writes it.

    Shows how much is written, and stops the export when the disk is nearly
    full or the capture was removed from the list -- a binary export holds
    8 bytes per edge, so a busy channel can fill a disk Logic 2's own .sal
    fitted on easily.
    """

    def __init__(self, folder: Path, say: Callable[[str], None], stop: Callable[[], None]) -> None:
        self.folder, self.say, self.stop = folder, say, stop
        self.reason: str | None = None
        self.cancelled: BaseException | None = None
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._run, name="export-watch", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._done.wait(C.logic2.watch_interval_s):
            written, free = _bytes_in(self.folder), shutil.disk_usage(self.folder).free
            try:
                self.say(f"Logic 2 is writing the export: {written / 1e9:.1f} GB "
                         f"({free / 1e9:.0f} GB free)")
            except Exception as exc:                 # the capture was removed: stop the export
                self.cancelled = exc
                self.stop()
                return
            if free < C.logic2.min_free_disk_gb * 1e9:
                self.reason = (f"The disk is nearly full: the binary export had reached "
                               f"{written / 1e9:.1f} GB with {free / 1e9:.1f} GB left on the disk "
                               f"of {self.folder}, so it was stopped.")
                self.stop()
                return

    def check(self) -> None:
        """Raise why the export was stopped, if it was."""
        if self.cancelled is not None:
            raise self.cancelled
        if self.reason is not None:
            raise Logic2Error(self.reason)

    def close(self) -> None:
        self._done.set()
        self._thread.join(C.logic2.watch_interval_s * 2)


def wait_complete(folder: Path, expected: int | None,
                  check: Callable[[], None] | None = None) -> list[Path]:
    """Block until every exported file is as long as its header says.

    Logic 2 returns from export_raw_data_binary before writing has finished
    (reported on the Saleae forum), so 'the call returned' proves nothing.
    """
    deadline = time.time() + C.logic2.export_complete_timeout_s
    stable, last_sizes = 0, None
    while time.time() < deadline:
        if check is not None:
            check()
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
