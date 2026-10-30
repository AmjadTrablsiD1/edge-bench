"""Never take the PC down.

A 64 GB Windows PC was brought to a standstill by the first CSV reader (it held
every cell as a Python string).  The reader is fixed; this is the safety net for
whatever comes next: a watchdog thread reads the process's *private* memory --
not the memory-mapped capture files, which the OS can always drop -- and when it
passes C.guard.max_private_frac of the physical RAM, Edge Bench says why and
exits instead of dragging the machine into swap.

Windows (the work PCs) and Linux are measured; on macOS the guard is off.
"""
from __future__ import annotations

import ctypes
import logging
import os
import platform
import subprocess
import sys
import threading
import time
from typing import Callable

from app.constants import C, path

log = logging.getLogger(__name__)
_SYSTEM = platform.system()


def _win_memory() -> tuple[int, int] | None:
    from ctypes import wintypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                    ("PrivateUsage", ctypes.c_size_t)]

    k32 = ctypes.WinDLL("kernel32")
    ms = MEMORYSTATUSEX(dwLength=ctypes.sizeof(MEMORYSTATUSEX))
    if not k32.GlobalMemoryStatusEx(ctypes.byref(ms)):
        return None
    pmc = PROCESS_MEMORY_COUNTERS_EX(cb=ctypes.sizeof(PROCESS_MEMORY_COUNTERS_EX))
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    if not k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
        return None
    return int(pmc.PrivateUsage), int(ms.ullTotalPhys)


def _linux_memory() -> tuple[int, int] | None:
    with open("/proc/self/status", encoding="utf-8") as f:
        anon = next((int(ln.split()[1]) * 1024 for ln in f if ln.startswith("RssAnon:")), None)
    total = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    return (anon, total) if anon is not None else None


def memory() -> tuple[int, int] | None:
    """(private bytes of this process, physical RAM bytes), or None where not measured."""
    try:
        if _SYSTEM == "Windows":
            return _win_memory()
        if _SYSTEM == "Linux":
            return _linux_memory()
    except Exception:                       # a guard must never be the thing that crashes
        log.exception("memory reading failed; guard off")
    return None


def tripped(used: int, total: int, frac: float = C.guard.max_private_frac) -> bool:
    return used > frac * total


def _tell(msg: str) -> None:
    """Show a message from a separate process, so this one can exit at once."""
    if _SYSTEM != "Windows":
        return
    exe = sys.executable
    flags = getattr(subprocess, "DETACHED_PROCESS", 0)
    script = "import ctypes,sys; ctypes.windll.user32.MessageBoxW(None, sys.argv[1], 'Edge Bench', 0x10)"
    try:
        subprocess.Popen([exe, "-c", script, msg], creationflags=flags, close_fds=True)
    except OSError:
        pass


def start(doing: Callable[[], str], on_trip: Callable[[str], None] | None = None,
          read: Callable[[], tuple[int, int] | None] = memory) -> threading.Thread | None:
    """Watch this process; `doing()` names the work in progress for the message."""
    if read() is None:
        return None

    def trip(used: int, total: int) -> None:
        msg = (f"{C.app.name} stopped itself: it was using {used / 2**30:.1f} GB of the PC's "
               f"{total / 2**30:.0f} GB of RAM{doing()}.\n\nThe PC is safe. Please send "
               f"crash.log from {C.paths.log_dir} so this can be fixed.")
        log.critical(msg)
        if on_trip is not None:
            on_trip(msg)
            return
        try:
            with open(path("log_dir") / "crash.log", "a", encoding="utf-8") as f:
                f.write(f"\n==== {time.strftime('%Y-%m-%d %H:%M:%S')}\n{msg}\n")
        except OSError:
            pass
        _tell(msg)
        os._exit(3)

    def watch() -> None:
        while True:
            m = read()
            if m is not None and tripped(*m):
                trip(*m)
                return
            time.sleep(C.guard.interval_s)

    th = threading.Thread(target=watch, name="memory-guard", daemon=True)
    th.start()
    return th
