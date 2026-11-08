#!/usr/bin/env python3
"""Install a new Edge Bench version over this one -- and keep the old one.

Standard library only: it runs before the packages are installed, and the
version being installed runs its *own* copy of this file, so each release
decides how it is installed.

  python updater.py                      install the newest zip in the update folder
  python updater.py PACKAGE [--force]    install that zip or unzipped folder
  python updater.py --rollback           go back to the version before the last update
  python updater.py --prepare            launcher: finish/undo an interrupted update,
                                         packages match requirements.txt, UI built
  (internal) --apply DIR --into DIR [--wait-pid N] [--restart] [--pause-on-error]

The install folder holds only replaceable program files plus .venv and .update;
everything a user makes (settings, logs, caches) lives in the home folder, so
an update never touches it.  An update:

  1. stage   unzip into .update/staged; check it is Edge Bench and newer
  2. stop    ask the running app to quit; wait until its process has ended
  3. swap    move the program files to .update/previous, the new ones in --
             renames on one disk, journalled in .update/journal.json, so a
             crash half-way is undone at the next start (--prepare)
  4. deps    pip install only if requirements.txt changed (offline from wheels/)
  5. check   main.py --check of the new version; any failure puts the old back
  6. start   the new version opens in the browser
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
C = json.loads((HERE / "shared" / "constants.json").read_text(encoding="utf-8"))
U = C["update"]
WINDOWS = platform.system() == "Windows"
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # local only, never a proxy
_ENV = {**os.environ, "PYTHONUTF8": "1"}


class UpdateError(RuntimeError):
    """Why an update cannot go ahead, written for the user."""


def _home(key: str) -> Path:
    return Path(C["paths"][key]).expanduser()


def say(msg: str) -> None:
    """Progress for the console (or the log when there is none) and the update log."""
    try:
        print(msg, flush=True)
    except (OSError, ValueError, AttributeError):
        pass
    try:
        log = _home("log_dir")
        log.mkdir(parents=True, exist_ok=True)
        with open(log / U["log_name"], "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


# ---------------------------------------------------------------- versions

def vtuple(v: str) -> tuple[int, ...]:
    if not re.match(U["version_regex"], v or ""):
        raise UpdateError(f"{v!r} is not a version number")
    return tuple(int(x) for x in v.split("."))


def installed_version(root: Path) -> str:
    return json.loads((root / "shared" / "constants.json").read_text(encoding="utf-8"))["app"]["version"]


_HEAD = re.compile(r"^##\s+(\d+(?:\.\d+)*)\s*(?:[—–-]+\s*(.*))?$")


def release_notes(text: str, after: str, upto: str) -> list[dict]:
    """CHANGELOG sections for versions in (after, upto], newest first."""
    out, cur = [], None
    lo, hi = vtuple(after), vtuple(upto)
    for line in text.splitlines():
        m = _HEAD.match(line.strip())
        if m:
            v = m.group(1)
            cur = {"version": v, "date": (m.group(2) or "").strip(), "items": []} if lo < vtuple(v) <= hi else None
            if cur is not None:
                out.append(cur)
        elif cur is not None and line.strip().startswith(("- ", "* ")):
            cur["items"].append(line.strip()[2:].strip())
        elif cur is not None and cur["items"] and line.startswith("  ") and line.strip():
            cur["items"][-1] += " " + line.strip()
    return out


# ---------------------------------------------------------------- packages

@dataclass
class Package:
    source: Path
    version: str
    offline: bool
    notes: list[dict] = field(default_factory=list)
    newer: bool = True

    def public(self) -> dict:
        d = asdict(self)
        d["source"] = str(self.source)
        d["name"] = self.source.name
        return d


def _zip_root(names: list[str]) -> str:
    for n in names:
        if n.endswith("shared/constants.json"):
            prefix = n[: -len("shared/constants.json")]
            if prefix.count("/") <= 1 and prefix + "main.py" in names:
                return prefix
    raise UpdateError("it has no Edge Bench program inside (main.py, shared/constants.json)")


def _folder_root(src: Path) -> Path:
    if (src / "main.py").is_file():
        return src
    for d in sorted(src.iterdir()):
        if d.is_dir() and (d / "main.py").is_file():
            return d
    raise UpdateError("it has no Edge Bench program inside (main.py)")


def read_package(src: Path, current: str) -> Package:
    """What a zip (or unzipped folder) holds, without unpacking it."""
    src = Path(src)
    try:
        if src.is_dir():
            root = _folder_root(src)
            consts = (root / "shared" / "constants.json").read_text(encoding="utf-8")
            log = (root / U["changelog"]).read_text(encoding="utf-8") if (root / U["changelog"]).is_file() else ""
            offline, has_updater = (root / "wheels").is_dir(), (root / "updater.py").is_file()
        elif zipfile.is_zipfile(src):
            with zipfile.ZipFile(src) as z:
                names = z.namelist()
                prefix = _zip_root(names)
                consts = z.read(prefix + "shared/constants.json").decode("utf-8")
                log = z.read(prefix + U["changelog"]).decode("utf-8") if prefix + U["changelog"] in names else ""
                offline = any(n.startswith(prefix + "wheels/") for n in names)
                has_updater = prefix + "updater.py" in names
        else:
            raise UpdateError("it is not a zip file or a folder")
        app = json.loads(consts)["app"]
    except UpdateError as exc:
        raise UpdateError(f"{src.name}: {exc}.") from exc
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
        raise UpdateError(f"{src.name} is not an {C['app']['name']} package ({exc}).") from exc
    if app.get("id") != C["app"]["id"]:
        raise UpdateError(f"{src.name} is {app.get('name', 'something else')}, not {C['app']['name']}.")
    if not has_updater:
        raise UpdateError(f"{src.name} is too old to install itself; unzip it by hand instead.")
    v = app.get("version", "")
    vtuple(v)
    return Package(src, v, offline, release_notes(log, current, v), vtuple(v) > vtuple(current))


# ------------------------------------------------------------ update folder

def settings_file() -> Path:
    return _home("config_dir") / "settings.json"


def update_folder() -> str | None:
    try:
        return json.loads(settings_file().read_text(encoding="utf-8")).get(U["settings_key"]) or None
    except (OSError, ValueError):
        return None


def set_update_folder(path: str | None) -> None:
    f = settings_file()
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data[U["settings_key"]] = path or None
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, indent=2), encoding="utf-8")


def find_update(folder: str | Path, current: str) -> Package | None:
    """The newest package in `folder` above `current` (the -offline zip when both exist)."""
    rx = re.compile(U["package_regex"])
    try:
        files = list(Path(folder).iterdir())
    except OSError as exc:
        raise UpdateError(f"Cannot read the update folder {folder} ({exc.strerror or exc}).") from exc
    best = None
    for f in files:
        m = rx.match(f.name)
        if not m:
            continue
        key = (vtuple(m.group(1)), bool(m.group(2)))
        if key[0] > vtuple(current) and (best is None or key > best[0]):
            best = (key, f)
    return read_package(best[1], current) if best else None


# ---------------------------------------------------------- install folder

def work(install: Path) -> Path:
    return install / U["work_dir"]


def is_source_checkout(install: Path) -> bool:
    return (install / ".git").exists()


def previous_version(install: Path) -> str | None:
    try:
        return installed_version(work(install) / "previous")
    except (OSError, ValueError, KeyError):
        return None


def last_result(install: Path) -> dict | None:
    try:
        return json.loads((work(install) / "last.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _record(install: Path, **kw) -> None:
    w = work(install)
    w.mkdir(exist_ok=True)
    (w / "last.json").write_text(json.dumps({**kw, "time": time.time()}), encoding="utf-8")


def _rmtree(p: Path) -> None:
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)


def _move(a: Path, b: Path) -> None:
    """A rename, retried: on Windows a virus scanner may hold a fresh file for a moment."""
    deadline = time.time() + U["move_retry_s"]
    while True:
        try:
            os.replace(a, b)
            return
        except PermissionError:
            if time.time() > deadline:
                raise
            time.sleep(0.2)


def stage(src: Path, install: Path, force: bool = False) -> Path:
    """Unpack a package into .update/staged; returns its program folder."""
    if is_source_checkout(install):
        raise UpdateError("This is a source folder (git): update it with  git pull  instead.")
    current = installed_version(install)
    pkg = read_package(src, current)
    if not pkg.newer and not force:
        raise UpdateError(f"{Path(src).name} is version {pkg.version}; {current} is already installed.")
    recover(install)
    dest = work(install) / "staged"
    _rmtree(dest)
    dest.mkdir(parents=True)
    say(f"Unpacking {Path(src).name} ...")
    if Path(src).is_dir():
        root = _folder_root(Path(src))
        shutil.copytree(root, dest / root.name, ignore=shutil.ignore_patterns(*U["keep"], "__pycache__"))
        return dest / root.name
    with zipfile.ZipFile(src) as z:
        prefix = _zip_root(z.namelist())
        z.extractall(dest)                      # member paths are sanitised by zipfile
    return dest / prefix.rstrip("/") if prefix else dest


# -------------------------------------------------------------------- swap

def _journal_path(install: Path) -> Path:
    return work(install) / "journal.json"


def _write_journal(install: Path, j: dict) -> None:
    tmp = _journal_path(install).with_suffix(".tmp")
    tmp.write_text(json.dumps(j), encoding="utf-8")
    os.replace(tmp, _journal_path(install))


def _entries(root: Path) -> list[str]:
    m = root / U["manifest"]
    if m.is_file():
        names = json.loads(m.read_text(encoding="utf-8"))["entries"]
    else:
        names = [p.name for p in root.iterdir()]
    return [n for n in names if n not in U["keep"]]


def swap(src: Path, install: Path, backup: Path, journal: dict) -> dict:
    """Move the program files to `backup` and `src`'s in.  Journalled step by step."""
    new = [n for n in (p.name for p in src.iterdir()) if n not in U["keep"]]
    old_listed = _entries(install) if (install / U["manifest"]).is_file() else []
    old = sorted({n for n in old_listed + new if (install / n).exists()})   # never a user's own file
    _rmtree(backup)
    backup.mkdir(parents=True)
    journal.update(src=str(src), backup=str(backup), old=old, new=new)
    for state, names, a, b in (("moving-old", old, install, backup), ("moving-new", new, src, install)):
        journal["state"] = state
        _write_journal(install, journal)
        for n in names:
            _move(a / n, b / n)
    journal["state"] = "installed"
    _write_journal(install, journal)
    return journal


def restore(install: Path, journal: dict) -> None:
    """Undo a swap, finished or not.  Safe to run twice."""
    src, backup = Path(journal["src"]), Path(journal["backup"])
    src.mkdir(parents=True, exist_ok=True)
    for n in journal.get("new", []):
        if (install / n).exists() and not (src / n).exists() and (n not in journal["old"] or (backup / n).exists()):
            _move(install / n, src / n)
    for n in journal.get("old", []):
        if (backup / n).exists() and not (install / n).exists():
            _move(backup / n, install / n)
    _journal_path(install).unlink(missing_ok=True)


def recover(install: Path) -> bool:
    """An update that stopped half-way (power cut, killed) is undone."""
    j = _journal_path(install)
    if not j.is_file():
        return False
    journal = json.loads(j.read_text(encoding="utf-8"))
    if journal.get("state") == "done":
        j.unlink()
        return False
    say(f"An interrupted update ({journal.get('from')} -> {journal.get('to')}) is being undone.")
    restore(install, journal)
    _record(install, ok=False, kind=journal.get("kind", "update"), **{"from": journal.get("from"),
            "to": journal.get("to"), "error": "The update was interrupted; the previous version was put back."})
    return True


# ------------------------------------------------------- packages and check

def venv_python(install: Path) -> Path | None:
    p = install / ".venv" / ("Scripts/python.exe" if WINDOWS else "bin/python3")
    return p if p.exists() else None


def _python(install: Path) -> str:
    """The interpreter to check and start with: the install's venv, else this one."""
    p = venv_python(install)
    if p is not None:
        return str(p)
    exe = sys.executable
    if WINDOWS and exe.lower().endswith("pythonw.exe"):
        exe = exe[: -len("pythonw.exe")] + "python.exe"
    return exe


def _pip(cmd: list[str]) -> int:
    return subprocess.run(cmd, env=_ENV).returncode


def ensure_deps(install: Path) -> bool:
    """pip install only when requirements.txt changed.  True if it installed."""
    py = venv_python(install)
    if py is None:                                     # no venv here (development): not ours
        return False
    req = install / "requirements.txt"
    stamp = install / ".venv" / U["deps_stamp"]
    want = hashlib.sha256(req.read_bytes()).hexdigest()
    if stamp.is_file() and stamp.read_text(encoding="utf-8").strip() == want:
        return False
    tag = subprocess.run([str(py), "-c", "import sys;print(f'cp{sys.version_info[0]}{sys.version_info[1]}')"],
                         capture_output=True, text=True, encoding="utf-8").stdout.strip()
    wheels = install / "wheels" / tag
    cmd = [str(py), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(req)]
    if wheels.is_dir():
        cmd[4:4] = ["--no-index", "--find-links", str(wheels)]
        say(f"Installing Python packages from {wheels} (offline) ...")
    else:
        say("Installing Python packages from the internet (pypi.org) ...")
    if _pip(cmd) != 0:
        hint = "" if wheels.is_dir() else " Without internet, use the -offline zip."
        raise UpdateError(f"Installing the Python packages failed.{hint}")
    stamp.write_text(want, encoding="utf-8")
    return True


def ensure_ui(install: Path) -> bool:
    """Source folder only: rebuild ui/dist when the UI sources are newer (after a git pull)."""
    ui = install / "ui"
    if not (ui / "src").is_dir():
        return False
    dist = ui / "dist" / "index.html"
    sources = [ui / "package.json", ui / "index.html", install / "shared" / "constants.json",
               install / "shared" / "themes.json", *(ui / "src").rglob("*")]
    newest = max((p.stat().st_mtime for p in sources if p.is_file()), default=0)
    if dist.is_file() and dist.stat().st_mtime >= newest:
        return False
    npm = shutil.which("npm")
    if npm is None:
        if dist.is_file():
            say("The user interface sources changed, but Node.js is not installed to rebuild it.")
            return False
        raise UpdateError("The user interface (ui/dist) is missing and Node.js is not installed to build it. "
                          "Use the release zip, or install Node.js from https://nodejs.org.")
    say("Building the user interface ...")
    for args in (["install", "--no-audit", "--no-fund"], ["run", "build"]):
        if subprocess.run([npm, *args], cwd=ui, env=_ENV).returncode != 0:
            raise UpdateError(f"npm {' '.join(args)} failed (see above).")
    return True


def check(install: Path) -> None:
    """The installed version must pass its own start-up check."""
    try:
        r = subprocess.run([_python(install), "main.py", "--check"], cwd=install, env=_ENV,
                           capture_output=True, text=True, encoding="utf-8",
                           timeout=U["check_timeout_s"])
    except subprocess.TimeoutExpired as exc:
        raise UpdateError(f"The start-up check did not finish within {U['check_timeout_s']} s.") from exc
    if r.returncode != 0:
        tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-6:])
        raise UpdateError(f"The new version did not pass its start-up check:\n{tail}")
    say(r.stdout.strip())


def migrate(old: str, install: Path) -> None:
    """One-off steps for data outside the install folder, run by the NEW version.

    Add a step here when a release changes something stored in the home folder
    (settings.json keys, cache layout ...).  It runs once, after the new version
    passed its check.  A rollback does not undo it, so keep the result readable
    by the previous version.  Nothing to migrate so far.
    """
    return None


# ------------------------------------------------------- the running app

def wait_for_exit(pid: int, timeout: float) -> bool:
    if WINDOWS:
        import ctypes
        k32 = ctypes.WinDLL("kernel32")
        k32.OpenProcess.restype = ctypes.c_void_p
        h = k32.OpenProcess(0x00100000, False, pid)                 # SYNCHRONIZE
        if not h:
            return True
        try:
            return k32.WaitForSingleObject(ctypes.c_void_p(h), int(timeout * 1000)) == 0
        finally:
            k32.CloseHandle(ctypes.c_void_p(h))
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass
        time.sleep(0.1)
    return False


def stop_running() -> int | None:
    """Ask a running Edge Bench to quit; its process id, or None if none runs."""
    try:
        port = int((_home("state_dir") / "port").read_text(encoding="utf-8").strip())
        base = f"http://{C['server']['host']}:{port}/api/"
        with _DIRECT.open(base + "whoami", timeout=2) as r:
            who = json.load(r)
        if who.get("token") != C["server"]["whoami_token"]:
            return None
        req = urllib.request.Request(base + "quit", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        with _DIRECT.open(req, timeout=5):
            pass
    except (OSError, ValueError, urllib.error.URLError):
        return None
    say("Edge Bench is running; asked it to quit.")
    return who.get("pid")


def start(install: Path) -> None:
    py = _python(install)
    if WINDOWS:
        pyw = Path(py).with_name("pythonw.exe")
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen([str(pyw if pyw.exists() else py), "main.py"], cwd=install, env=_ENV,
                         creationflags=flags, close_fds=True)
    else:
        subprocess.Popen([py, "main.py"], cwd=install, env=_ENV, start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ------------------------------------------------------------ the update

def _install(src: Path, install: Path, backup: Path, kind: str, *, wait_pid: int | None,
             restart: bool, pause: bool, after_ok) -> int:
    """Swap `src` in, check it, keep or undo.  Shared by update and rollback."""
    try:
        if wait_pid and not wait_for_exit(wait_pid, U["server_exit_timeout_s"]):
            raise UpdateError("Edge Bench did not stop. Close it (App tab -> Quit) and try again.")
        recover(install)
        old, new = installed_version(install), installed_version(src)
    except Exception as exc:                         # nothing has changed yet
        return _failed(install, kind, None, None, exc, restart, pause, None)
    say(f"{'Updating' if kind == 'update' else 'Rolling back'} {C['app']['name']} {old} -> {new} ...")
    journal = {"kind": kind, "from": old, "to": new}
    try:
        swap(src, install, backup, journal)
        ensure_deps(install)
        check(install)
        if kind == "update":
            migrate(old, install)
    except BaseException as exc:                     # incl. Ctrl+C: never leave a half-made install
        try:
            restore(install, journal) if "src" in journal else None
            ensure_deps(install)
        except Exception:
            say(traceback.format_exc())
        return _failed(install, kind, old, new, exc, restart, pause, journal)
    journal["state"] = "done"
    _write_journal(install, journal)
    after_ok()
    _journal_path(install).unlink(missing_ok=True)
    _record(install, ok=True, kind=kind, **{"from": old, "to": new, "error": None})
    say(f"{C['app']['name']} {new} is installed{f' ({old} kept for a rollback)' if kind == 'update' else ''}.")
    if restart:
        start(install)
    return 0


def _failed(install, kind, old, new, exc, restart, pause, journal) -> int:
    msg = str(exc) if isinstance(exc, UpdateError) else f"{type(exc).__name__}: {exc}"
    say(f"{'Update' if kind == 'update' else 'Rollback'} failed: {msg}")
    if journal is not None:
        say(f"{C['app']['name']} {old} was put back.")
    _record(install, ok=False, kind=kind, **{"from": old, "to": new, "error": msg})
    if restart:
        start(install)
    if pause and sys.stdin and sys.stdin.isatty():
        input("Press Enter to close this window.")
    return 1


def apply(src: Path, install: Path, **kw) -> int:
    """Run by the NEW version's updater: install the staged `src` over `install`."""
    staged_dir = work(install) / "staged"
    return _install(src, install, work(install) / "previous", "update",
                    after_ok=lambda: _rmtree(staged_dir), **kw)


def rollback(install: Path, **kw) -> int:
    prev, replaced = work(install) / "previous", work(install) / "replaced"
    if previous_version(install) is None:
        say("There is no previous version to go back to.")
        return 1

    def keep_replaced() -> None:                    # the version just replaced becomes "previous"
        _rmtree(prev)
        _move(replaced, prev)

    return _install(prev, install, replaced, "rollback", after_ok=keep_replaced, **kw)


def launch(install: Path, *, staged: Path | None, wait_pid: int | None) -> None:
    """Start the installer as its own process (the app quits meanwhile).

    An update runs the staged version's updater.py; a rollback this one.  On
    Windows it gets a console window, so the user sees it work.
    """
    script = (staged / "updater.py") if staged else (install / "updater.py")
    args = [_python(install), str(script), "--into", str(install), "--restart", "--pause-on-error"]
    args += ["--apply", str(staged)] if staged else ["--rollback"]
    if wait_pid:
        args += ["--wait-pid", str(wait_pid)]
    w = work(install)
    w.mkdir(exist_ok=True)
    if WINDOWS:
        subprocess.Popen(args, cwd=w, env=_ENV, creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    else:
        log_dir = _home("log_dir")
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / U["log_name"], "a", encoding="utf-8") as out:
            subprocess.Popen(args, cwd=w, env=_ENV, start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT)


def prepare(install: Path) -> int:
    """The launcher, before every start."""
    try:
        recover(install)
        ensure_deps(install)
        ensure_ui(install)
    except UpdateError as exc:
        say(str(exc))
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="updater.py", description="Install or roll back an Edge Bench version.")
    ap.add_argument("package", nargs="?", help="zip or unzipped folder (default: newest in the update folder)")
    ap.add_argument("--force", action="store_true", help="also install the same or an older version")
    ap.add_argument("--rollback", action="store_true", help="go back to the version before the last update")
    ap.add_argument("--prepare", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--apply", type=Path, help=argparse.SUPPRESS)
    ap.add_argument("--into", type=Path, help=argparse.SUPPRESS)
    ap.add_argument("--wait-pid", type=int, help=argparse.SUPPRESS)
    ap.add_argument("--restart", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--no-restart", action="store_true", help="do not start Edge Bench afterwards")
    ap.add_argument("--pause-on-error", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    install = (a.into or HERE).resolve()
    kw = dict(pause=a.pause_on_error)

    if a.prepare:
        return prepare(install)
    if a.apply:
        return apply(a.apply.resolve(), install, wait_pid=a.wait_pid, restart=a.restart, **kw)
    if a.rollback:
        if a.into:                                   # started by the app, which is quitting
            return rollback(install, wait_pid=a.wait_pid, restart=a.restart, **kw)
        pid = stop_running()
        return rollback(install, wait_pid=pid, restart=not a.no_restart, **kw)
    try:
        if a.package:
            src = Path(a.package)
        else:
            folder = update_folder()
            if not folder:
                say("No update folder is set (App tab -> Updates), and no package was given.\n"
                    "Drag an EdgeBench-...-windows.zip onto 'Update Edge Bench.bat'.")
                return 1
            pkg = find_update(folder, installed_version(install))
            if pkg is None:
                say(f"No newer version in {folder}; {installed_version(install)} is up to date.")
                return 0
            src = pkg.source
        staged = stage(src, install, force=a.force)
    except UpdateError as exc:
        say(str(exc))
        return 1
    pid = stop_running()
    cmd = [_python(install), str(staged / "updater.py"), "--apply", str(staged), "--into", str(install)]
    cmd += ["--wait-pid", str(pid)] if pid else []
    cmd += [] if a.no_restart else ["--restart"]
    return subprocess.run(cmd, cwd=work(install), env=_ENV).returncode


if __name__ == "__main__":
    raise SystemExit(main())
