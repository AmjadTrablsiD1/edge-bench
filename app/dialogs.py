"""Native file dialogs, run in a child process.

A browser never reveals a file's path, and Logic 2 needs one, so the server
opens the operating system's own dialog.  tkinter must own the main thread
(on macOS a dialog from a worker thread crashes), hence a short child process.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

from app.constants import C

_SCRIPT = r"""
import json, sys, tkinter as tk
from tkinter import filedialog
kind, title, initial, types, name = json.loads(sys.argv[1])
root = tk.Tk(); root.withdraw()
try: root.attributes("-topmost", True)
except Exception: pass
root.update()
kw = dict(parent=root, title=title, initialdir=initial or None)
if kind == "files":
    r = list(filedialog.askopenfilenames(filetypes=[tuple(t) for t in types], **kw))
elif kind == "folder":
    r = filedialog.askdirectory(mustexist=True, **kw); r = [r] if r else []
elif kind == "exe":
    r = filedialog.askopenfilename(**kw); r = [r] if r else []
else:
    r = filedialog.asksaveasfilename(defaultextension=".xlsx", initialfile=name,
        filetypes=[("Excel workbook", "*.xlsx")], **kw); r = [r] if r else []
root.destroy()
print(json.dumps(r))
"""


class DialogError(RuntimeError):
    pass


def ask(kind: str, title: str = C.files.dialog_title, initial: str | None = None,
        name: str | None = None) -> list[str]:
    """kind: files | folder | save | exe.  Returns [] when the user cancels."""
    exe = sys.executable
    if platform.system() == "Windows" and exe.lower().endswith("pythonw.exe"):
        console = exe[:-len("pythonw.exe")] + "python.exe"   # reliable stdout pipe; no window
        exe = console if Path(console).exists() else exe
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if platform.system() == "Windows" else 0
    arg = json.dumps([kind, title, initial, C.files.dialog_filetypes, name])
    try:
        out = subprocess.run([exe, "-c", _SCRIPT, arg], capture_output=True, text=True,
                             encoding="utf-8", env={**os.environ, "PYTHONUTF8": "1"},
                             timeout=C.files.dialog_timeout_s, creationflags=flags)
    except subprocess.TimeoutExpired as exc:
        raise DialogError("The file dialog was left open too long.") from exc
    if out.returncode != 0:
        tail = (out.stderr or "").strip().splitlines()[-1:] or ["unknown error"]
        raise DialogError(f"Could not open the file dialog: {tail[0]}")
    lines = [ln for ln in out.stdout.splitlines() if ln.strip()]
    return json.loads(lines[-1]) if lines else []
