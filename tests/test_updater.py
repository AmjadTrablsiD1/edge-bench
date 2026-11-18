"""In-place updates: real packages built from this repo, installed, replaced, undone.

Every test works in a temporary install folder made by unzipping a package,
with HOME pointed at a temporary folder (settings, logs, the port file), so
nothing here touches the real installation.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import updater
from server import api as srv

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(not (ROOT / "ui" / "dist" / "index.html").is_file(), reason="UI not built")
PROGRAM = ["app", "server", "shared", "main.py", "updater.py", "requirements.txt", "CHANGELOG.md",
           "run-edge-bench.bat", "LICENSE"]


def build_package(out: Path, version: str, *, broken: bool = False, offline: bool = False,
                  extra_req: str = "", updater_src: Path | None = None, github: str = "http://127.0.0.1:9") -> Path:
    """An EdgeBench-<version>-windows.zip the way make_windows_package.sh lays it out."""
    stage = out / f"stage-{version}{'-offline' if offline else ''}" / "EdgeBench"
    shutil.rmtree(stage.parent, ignore_errors=True)
    for name in PROGRAM:
        src = ROOT / name
        if src.is_dir():
            shutil.copytree(src, stage / name, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            stage.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, stage / name)
    shutil.copytree(ROOT / "ui" / "dist", stage / "ui" / "dist")
    if updater_src:
        shutil.copy2(updater_src, stage / "updater.py")
    c = json.loads((stage / "shared" / "constants.json").read_text(encoding="utf-8"))
    c["app"]["version"] = version
    c["update"]["github"].update(site=github, raw=github, timeout_s=5)       # never the real GitHub
    (stage / "shared" / "constants.json").write_text(json.dumps(c, indent=2, ensure_ascii=False), encoding="utf-8")
    log = (stage / "CHANGELOG.md").read_text(encoding="utf-8")
    (stage / "CHANGELOG.md").write_text(log.replace("# Changelog\n", f"# Changelog\n\n## {version} — test\n"
                                                    f"- Feature of {version}.\n", 1), encoding="utf-8")
    if broken:                                        # imports fine for the updater, fails main.py --check
        with open(stage / "app" / "measure.py", "a", encoding="utf-8") as f:
            f.write("\nraise RuntimeError('broken build')\n")
    if extra_req:
        with open(stage / "requirements.txt", "a", encoding="utf-8") as f:
            f.write(extra_req + "\n")
    if offline:
        (stage / "wheels" / "cp313").mkdir(parents=True)
        (stage / "wheels" / "cp313" / "placeholder.txt").write_text("x", encoding="utf-8")
    entries = sorted({p.name for p in stage.iterdir()} | {"MANIFEST.json"})
    (stage / "MANIFEST.json").write_text(json.dumps({"app": "edge-bench", "version": version,
                                                     "entries": entries}), encoding="utf-8")
    z = out / f"EdgeBench-{version}-windows{'-offline' if offline else ''}.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(stage.rglob("*")):
            zf.write(p, p.relative_to(stage.parent).as_posix())
    return z


def install_from(z: Path, dest: Path) -> Path:
    with zipfile.ZipFile(z) as zf:
        zf.extractall(dest)
    root = dest / "EdgeBench"
    (root / "my notes.txt").write_text("the user's own file", encoding="utf-8")
    (root / ".venv").mkdir()
    (root / ".venv" / "marker").write_text("venv", encoding="utf-8")
    return root


def tree(root: Path) -> dict[str, str]:
    """Every file with its hash -- .update excluded."""
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and not rel.startswith(".update/") and "__pycache__" not in rel:
            out[rel] = hashlib.sha1(p.read_bytes()).hexdigest()
    return out


@pytest.fixture(autouse=True)
def no_real_github(monkeypatch):
    """No test reaches github.com: in-process code asks a port where nothing listens."""
    monkeypatch.setitem(updater.G, "site", "http://127.0.0.1:9")
    monkeypatch.setitem(updater.G, "raw", "http://127.0.0.1:9")
    monkeypatch.setitem(updater.G, "timeout_s", 2)


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    return h


@pytest.fixture
def pkgs(tmp_path):
    out = tmp_path / "pkgs"
    out.mkdir()
    return out


def run_updater(install: Path, *args, home: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "PYTHONUTF8": "1"}
    return subprocess.run([sys.executable, str(install / "updater.py"), *args, "--no-restart"],
                          cwd=install, env=env, capture_output=True, text=True, encoding="utf-8", timeout=300)


# ------------------------------------------------------------------ pieces

def test_release_notes_between_versions():
    text = "# Changelog\n\n## 1.2.0 — 2026-12-01\n- C\n  continued\n\n## 1.1.0 — 2026-11-01\n- B\n\n## 1.0.0\n- A\n"
    notes = updater.release_notes(text, "1.0.0", "1.2.0")
    assert [n["version"] for n in notes] == ["1.2.0", "1.1.0"]
    assert notes[0]["items"] == ["C continued"] and notes[0]["date"] == "2026-12-01"
    assert updater.release_notes(text, "1.2.0", "1.2.0") == []


def test_find_update_takes_the_newest_and_prefers_offline(pkgs):
    for v, off in [("1.0.5", False), ("1.2.0", False), ("1.2.0", True), ("0.9.0", False)]:
        build_package(pkgs, v, offline=off)
    (pkgs / "EdgeBench-9.9.9-notes.txt").write_text("not a package", encoding="utf-8")
    pkg = updater.find_update(pkgs, "1.0.0")
    assert pkg.version == "1.2.0" and pkg.offline and pkg.source.name.endswith("-offline.zip")
    assert [n["version"] for n in pkg.notes][0] == "1.2.0"
    assert updater.find_update(pkgs, "1.2.0") is None
    with pytest.raises(updater.UpdateError, match="Cannot read the update folder"):
        updater.find_update(pkgs / "nope", "1.0.0")


def test_not_every_zip_is_an_update(pkgs, tmp_path):
    (tmp_path / "x.zip").write_bytes(b"not a zip")
    with pytest.raises(updater.UpdateError, match="not a zip"):
        updater.read_package(tmp_path / "x.zip", "1.0.0")
    other = build_package(pkgs, "2.0.0")
    with zipfile.ZipFile(other) as z, zipfile.ZipFile(tmp_path / "other.zip", "w") as out:
        for i in z.infolist():
            data = z.read(i)
            if i.filename.endswith("shared/constants.json"):
                c = json.loads(data)
                c["app"].update(id="drone-lab", name="Drone Lab")
                data = json.dumps(c).encode()
            out.writestr(i, data)
    with pytest.raises(updater.UpdateError, match="Drone Lab, not Edge Bench"):
        updater.read_package(tmp_path / "other.zip", "1.0.0")
    with zipfile.ZipFile(other) as z, zipfile.ZipFile(tmp_path / "old.zip", "w") as out:
        for i in z.infolist():
            if not i.filename.endswith("updater.py"):
                out.writestr(i, z.read(i))
    with pytest.raises(updater.UpdateError, match="too old to install itself"):
        updater.read_package(tmp_path / "old.zip", "1.0.0")


def test_stage_refuses_same_older_and_a_git_folder(pkgs, tmp_path, home):
    install = install_from(build_package(pkgs, "1.0.0"), tmp_path / "inst")
    for v in ("1.0.0", "0.9.0"):
        with pytest.raises(updater.UpdateError, match="already installed"):
            updater.stage(build_package(pkgs, v), install)
    assert updater.stage(pkgs / "EdgeBench-0.9.0-windows.zip", install, force=True).name == "EdgeBench"
    (install / ".git").mkdir()
    with pytest.raises(updater.UpdateError, match="git pull"):
        updater.stage(build_package(pkgs, "2.0.0"), install)


# ------------------------------------------------------------ whole updates

def test_update_replaces_the_program_and_nothing_else(pkgs, tmp_path, home):
    install = install_from(build_package(pkgs, "1.0.0"), tmp_path / "inst")
    b = build_package(pkgs, "1.1.0")
    r = run_updater(install, str(b), home=home)
    assert r.returncode == 0, r.stdout + r.stderr
    assert updater.installed_version(install) == "1.1.0"
    with zipfile.ZipFile(b) as z:
        z.extractall(tmp_path / "b")
    expect = tree(tmp_path / "b" / "EdgeBench")
    got = tree(install)
    assert got.pop("my notes.txt") and got.pop(".venv/marker")          # the user's things stay
    assert got == expect                                                # and exactly B's program
    assert updater.previous_version(install) == "1.0.0"
    last = updater.last_result(install)
    assert last["ok"] and last["from"] == "1.0.0" and last["to"] == "1.1.0"
    assert not (install / ".update" / "staged").exists() and not (install / ".update" / "journal.json").exists()
    assert "Edge Bench 1.1.0: OK" in r.stdout


def test_a_broken_update_puts_the_old_version_back(pkgs, tmp_path, home):
    install = install_from(build_package(pkgs, "1.0.0"), tmp_path / "inst")
    before = tree(install)
    r = run_updater(install, str(build_package(pkgs, "1.1.0", broken=True)), home=home)
    assert r.returncode == 1
    assert tree(install) == before
    last = updater.last_result(install)
    assert not last["ok"] and "start-up check" in last["error"] and "broken build" in last["error"]
    assert "1.0.0 was put back" in r.stdout
    assert not (install / ".update" / "journal.json").exists()


def test_rollback_goes_back_and_forth(pkgs, tmp_path, home):
    install = install_from(build_package(pkgs, "1.0.0"), tmp_path / "inst")
    a = tree(install)
    assert run_updater(install, str(build_package(pkgs, "1.1.0")), home=home).returncode == 0
    b = tree(install)
    r = run_updater(install, "--rollback", home=home)
    assert r.returncode == 0, r.stdout + r.stderr
    assert tree(install) == a and updater.previous_version(install) == "1.1.0"
    assert run_updater(install, "--rollback", home=home).returncode == 0
    assert tree(install) == b and updater.previous_version(install) == "1.0.0"


def test_update_from_the_update_folder(pkgs, tmp_path, home):
    install = install_from(build_package(pkgs, "1.0.0"), tmp_path / "inst")
    share = tmp_path / "share"
    share.mkdir()
    build_package(share, "1.3.0")
    updater.set_github_enabled(False)
    r = run_updater(install, home=home)
    assert r.returncode == 1 and "No update folder is set and GitHub is switched off" in r.stdout
    updater.set_update_folder(str(share))
    assert run_updater(install, home=home).returncode == 0
    assert updater.installed_version(install) == "1.3.0"
    assert "is up to date" in run_updater(install, home=home).stdout


# -------------------------------------------------------------- crash safety

def _moves(install, staged) -> int:
    return len([n for n in os.listdir(staged)]) + len(
        [n for n in json.loads((install / "MANIFEST.json").read_text(encoding="utf-8"))["entries"]
         if (install / n).exists()])


class PowerCut(BaseException):
    pass


def test_an_update_cut_off_anywhere_is_undone(pkgs, tmp_path, home, monkeypatch):
    """Power lost after every possible number of file moves: the next start restores the old version."""
    a_zip, b_zip = build_package(pkgs, "1.0.0"), build_package(pkgs, "1.1.0")
    probe = install_from(a_zip, tmp_path / "probe")
    total = _moves(probe, updater.stage(b_zip, probe))
    real_move = updater._move
    for cut in range(total + 1):
        install = install_from(a_zip, tmp_path / f"i{cut}")
        before = tree(install)
        staged = updater.stage(b_zip, install)
        done = []

        def move(a, b):
            if len(done) == cut:
                raise PowerCut
            real_move(a, b)
            done.append(1)

        monkeypatch.setattr(updater, "_move", move)
        try:          # cut == total: every file moved, but the check never ran -- also undone
            updater.swap(staged, install, install / ".update" / "previous", {"from": "1.0.0", "to": "1.1.0"})
            assert cut == total
        except PowerCut:
            assert cut < total
        monkeypatch.setattr(updater, "_move", real_move)
        assert updater.prepare(install) == 0                 # what the launcher runs next
        assert tree(install) == before, f"cut after {cut} moves"
        assert not updater.last_result(install)["ok"]


def test_packages_follow_requirements(tmp_path, monkeypatch):
    if sys.platform == "win32":
        pytest.skip("the fake venv below is POSIX-shaped")
    install = tmp_path / "inst"
    (install / ".venv" / "bin").mkdir(parents=True)
    (install / ".venv" / "bin" / "python3").symlink_to(sys.executable)
    (install / "requirements.txt").write_text("numpy==2.5.4\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(updater, "_pip", lambda cmd: calls.append(cmd) or 0)
    assert updater.ensure_deps(install) and len(calls) == 1 and "--no-index" not in calls[0]
    assert not updater.ensure_deps(install) and len(calls) == 1              # stamp matches: nothing
    (install / "requirements.txt").write_text("numpy==2.5.5\n", encoding="utf-8")
    tag = f"cp{sys.version_info[0]}{sys.version_info[1]}"
    (install / "wheels" / tag).mkdir(parents=True)
    monkeypatch.setattr(updater, "WINDOWS", False)                          # win_amd64 wheels: never elsewhere
    assert updater.ensure_deps(install) and "--no-index" not in calls[-1]
    (install / "requirements.txt").write_text("numpy==2.5.55\n", encoding="utf-8")
    monkeypatch.setattr(updater, "WINDOWS", True)
    monkeypatch.setattr(updater, "venv_python", lambda root: root / ".venv" / "bin" / "python3")
    assert updater.ensure_deps(install) and calls[-1][4:7] == ["--no-index", "--find-links", str(install / "wheels" / tag)]
    (install / "requirements.txt").write_text("numpy==2.5.6\n", encoding="utf-8")
    monkeypatch.setattr(updater, "_pip", lambda cmd: 1)
    with pytest.raises(updater.UpdateError, match="Installing the Python packages failed"):
        updater.ensure_deps(install)
    stamp = (install / ".venv" / updater.U["deps_stamp"]).read_text(encoding="utf-8")
    assert stamp == hashlib.sha256(b"numpy==2.5.55\n").hexdigest()         # still the last good one


# ---------------------------------------------------------------- the app

@pytest.fixture
def client():
    yield TestClient(srv.api)
    if hasattr(srv.api.state, "server"):
        del srv.api.state.server


def test_update_panel_api(pkgs, tmp_path, home, monkeypatch, client):
    install = install_from(build_package(pkgs, "1.0.0"), tmp_path / "inst")
    monkeypatch.setattr(srv, "UPDATE_ROOT", install)
    monkeypatch.setitem(srv.C.app, "version", "1.0.0")
    share = tmp_path / "share"
    share.mkdir()
    s = client.get("/api/update").json()
    assert s["folder"] is None and s["available"] is None and not s["source_checkout"]
    assert client.post("/api/update/folder", json={"path": str(tmp_path / "nope")}).status_code == 422
    build_package(share, "1.4.0")
    s = client.post("/api/update/folder", json={"path": str(share)}).json()
    assert s["folder"] == str(share) and s["available"]["version"] == "1.4.0"
    assert s["available"]["notes"][0]["items"] == ["Feature of 1.4.0."]
    p = client.post("/api/update/inspect", json={"path": str(share / "EdgeBench-1.4.0-windows.zip")}).json()
    assert p["version"] == "1.4.0" and p["newer"]
    assert client.post("/api/update/inspect", json={"path": str(tmp_path)}).status_code == 422
    assert client.post("/api/update/rollback").status_code == 409          # not started by main.py


def test_install_hands_over_and_quits(pkgs, tmp_path, home, monkeypatch, client):
    install = install_from(build_package(pkgs, "1.0.0"), tmp_path / "inst")
    monkeypatch.setattr(srv, "UPDATE_ROOT", install)
    launched = []
    monkeypatch.setattr(updater, "launch", lambda root, **kw: launched.append((root, kw)))

    class Server:
        should_exit = False

    srv.api.state.server = Server()
    r = client.post("/api/update/install", json={"path": str(build_package(pkgs, "1.1.0"))})
    assert r.status_code == 200 and r.json()["version"] == "1.1.0"
    root, kw = launched[0]
    assert root == install and kw["staged"].name == "EdgeBench" and kw["wait_pid"] == os.getpid()
    time.sleep(0.5)
    assert srv.api.state.server.should_exit
    monkeypatch.setattr(srv, "UPDATE_ROOT", ROOT)                           # this repo is a git folder
    r = client.post("/api/update/install", json={"path": str(pkgs / "EdgeBench-1.1.0-windows.zip")})
    assert r.status_code == 422 and "git pull" in r.json()["detail"]


def _whoami(home: Path, timeout: float = 60) -> dict | None:
    deadline = time.time() + timeout
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.time() < deadline:
        try:
            port = int((home / ".local" / "state" / "edge-bench" / "port").read_text(encoding="utf-8"))
            with opener.open(f"http://127.0.0.1:{port}/api/whoami", timeout=1) as r:
                return {**json.load(r), "port": port}
        except (OSError, ValueError):
            time.sleep(0.2)
    return None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process handling in this test")
def test_the_running_app_updates_itself(pkgs, tmp_path, home):
    """The real thing: a running 1.0.0 is asked to install 1.1.0 and comes back as 1.1.0."""
    install = install_from(build_package(pkgs, "1.0.0"), tmp_path / "inst")
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "EDGE_BENCH_NO_BROWSER": "1"}
    app = subprocess.Popen([sys.executable, "main.py"], cwd=install, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        old = _whoami(home)
        assert old and old["version"] == "1.0.0"
        req = urllib.request.Request(f"http://127.0.0.1:{old['port']}/api/update/install",
                                     data=json.dumps({"path": str(build_package(pkgs, "1.1.0"))}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=60) as r:
            assert json.load(r)["version"] == "1.1.0"
        app.wait(30)                                          # the old app quits (and is reaped)
        deadline, new = time.time() + 120, None
        while time.time() < deadline:
            new = _whoami(home, timeout=2)
            if new and new["version"] == "1.1.0":
                break
            time.sleep(0.5)
        log = (home / ".local" / "state" / "edge-bench" / "logs" / "update.log")
        assert new and new["version"] == "1.1.0", log.read_text(encoding="utf-8") if log.exists() else "no log"
        assert updater.installed_version(install) == "1.1.0" and updater.last_result(install)["ok"]
    finally:
        if app.poll() is None:
            app.kill()
        who = _whoami(home, timeout=1)
        if who:
            try:
                os.kill(who["pid"], 15)
            except OSError:
                pass


def test_the_optional_speed_up_can_never_fail_an_update_or_a_start(tmp_path, monkeypatch):
    """requirements-fast.txt: installed with the main list as a constraint, tried once, never fatal."""
    if sys.platform == "win32":
        pytest.skip("the fake venv below is POSIX-shaped")
    install = tmp_path / "inst"
    (install / ".venv" / "bin").mkdir(parents=True)
    (install / ".venv" / "bin" / "python3").symlink_to(sys.executable)
    (install / "requirements.txt").write_text("numpy==2.5.4\n", encoding="utf-8")
    opt = install / updater.U["optional_requirements"]
    calls = []
    monkeypatch.setattr(updater, "_pip", lambda cmd: calls.append(cmd) or 0)
    assert updater.ensure_optional(install) == "skipped" and not calls          # an older package: no such file
    opt.write_text("numba==0.68.1\n", encoding="utf-8")
    assert updater.ensure_optional(install) == "installed"
    cmd = calls[-1]
    assert cmd[cmd.index("-c") + 1] == str(install / "requirements.txt") and cmd[cmd.index("-r") + 1] == str(opt)
    assert "--retries" in cmd and "--no-index" not in cmd                       # online: give up quickly
    assert updater.ensure_optional(install) == "skipped" and len(calls) == 1    # done: not again
    assert updater.ensure_optional(install, force=True) == "skipped" and len(calls) == 1

    # a new list, and pip fails (no internet, no wheel for this Python): said, remembered, not fatal
    opt.write_text("numba==0.69.0\n", encoding="utf-8")
    tag = f"cp{sys.version_info[0]}{sys.version_info[1]}"
    (install / "wheels" / tag).mkdir(parents=True)
    monkeypatch.setattr(updater, "WINDOWS", True)                               # the offline wheels are Windows'
    monkeypatch.setattr(updater, "venv_python", lambda root: root / ".venv" / "bin" / "python3")
    monkeypatch.setattr(updater, "_pip", lambda cmd: calls.append(cmd) or int("-c" in cmd))    # only the optional list fails
    assert updater.ensure_optional(install) == "failed"
    assert calls[-1][4:7] == ["--no-index", "--find-links", str(install / "wheels" / tag)] and "--retries" not in calls[-1]
    n = len(calls)
    assert updater.ensure_optional(install) == "skipped" and len(calls) == n    # not at every start
    assert updater.prepare(install) == 0 and len(calls) == n + 1                # prepare: main packages only
    assert updater.ensure_optional(install, force=True) == "failed" and len(calls) == n + 2      # the App tab's button
    monkeypatch.setattr(updater, "_pip", lambda cmd: calls.append(cmd) or 0)
    assert updater.ensure_optional(install, force=True) == "installed"

    # even pip itself blowing up is swallowed where an update or a start calls it
    opt.write_text("numba==0.70.0\n", encoding="utf-8")

    def explode(cmd):
        if "-c" in cmd:
            raise RuntimeError("pip is broken")
        return 0

    monkeypatch.setattr(updater, "_pip", explode)
    assert updater.prepare(install) == 0
    assert updater.main(["--optional", "--into", str(install)]) == 1


def test_an_update_still_succeeds_when_the_speed_up_cannot_be_installed(tmp_path, monkeypatch):
    install = install_from(build_package(tmp_path, "1.0.0"), tmp_path / "inst")
    new = build_package(tmp_path, "1.1.0")
    monkeypatch.setattr(updater, "ensure_optional", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    assert updater.apply(updater.stage(new, install), install, wait_pid=None, restart=False, pause=False) == 0
    assert updater.installed_version(install) == "1.1.0" and updater.last_result(install)["ok"]


# ---------------------------------------------------------------- GitHub

import http.server  # noqa: E402
import threading  # noqa: E402


class FakeGitHub:
    """github.com and raw.githubusercontent.com as the updater uses them, on 127.0.0.1."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.latest: str | None = None
        self.requests: list[str] = []
        self.slow = 0.0                                     # seconds per 64 kB, to cancel a download
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                fake.requests.append(self.path)
                repo = updater.G["repo"]
                if self.path == f"/{repo}/releases/latest":
                    self.send_response(302)
                    self.send_header("Location", f"{fake.url}/{repo}/releases" +
                                     (f"/tag/v{fake.latest}" if fake.latest else ""))
                    self.end_headers()
                    return
                body = fake.files.get(self.path)
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                for i in range(0, len(body), 65536):
                    if fake.slow:
                        time.sleep(fake.slow)
                    try:
                        self.wfile.write(body[i:i + 65536])
                    except OSError:
                        return

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def publish(self, pkgs: Path, version: str, *, tamper: str | None = None, sums: bool = True,
                offline: bool = False, **kw) -> None:
        """Release `version` the way scripts/release.sh --github does: zips, SHA256SUMS.txt, a tag."""
        repo, tag = updater.G["repo"], f"v{version}"
        names = [build_package(pkgs, version, github=self.url, **kw)]
        if offline:
            names.append(build_package(pkgs, version, offline=True, github=self.url, **kw))
        lines = []
        for z in names:
            data = z.read_bytes()
            lines.append(f"{hashlib.sha256(data).hexdigest()}  {z.name}")
            self.files[f"/{repo}/releases/download/{tag}/{z.name}"] = (data[:-10] + b"x" * 10) if tamper == z.name else data
        if sums:
            self.files[f"/{repo}/releases/download/{tag}/SHA256SUMS.txt"] = ("\n".join(lines) + "\n").encode()
        with zipfile.ZipFile(names[0]) as zf:
            self.files[f"/{repo}/{tag}/CHANGELOG.md"] = zf.read("EdgeBench/CHANGELOG.md")
        self.latest = version


@pytest.fixture
def github(monkeypatch, home):
    gh = FakeGitHub()
    monkeypatch.setitem(updater.G, "site", gh.url)
    monkeypatch.setitem(updater.G, "raw", gh.url)
    srv.UPD.__init__()                                     # nothing left from another test
    yield gh
    gh.server.shutdown()
    srv.UPD.__init__()


def test_github_finds_the_newest_release_and_asks_at_most_hourly(github, pkgs):
    assert updater.find_github("1.0.0", force=True) is None               # no release yet
    github.publish(pkgs, "1.2.0")
    assert updater.find_github("1.0.0") is None                           # asked within the hour: cached
    asked = len(github.requests)
    r = updater.find_github("1.0.0", force=True)
    assert r.version == "1.2.0" and r.notes[0]["items"] == ["Feature of 1.2.0."]
    assert len(github.requests) == asked + 2                              # latest + CHANGELOG, never the API
    assert all(not p.startswith("/repos/") for p in github.requests)
    assert updater.find_github("1.0.0").version == "1.2.0" and len(github.requests) == asked + 2
    assert updater.find_github("1.2.0") is None                           # not newer
    pub = r.public()
    assert pub["remote"] and pub["source"] == "github:v1.2.0" and pub["url"].endswith("/releases/tag/v1.2.0")


def test_github_unreachable_is_said_plainly(monkeypatch, home):
    with pytest.raises(updater.UpdateError, match="Cannot reach GitHub .*proxy"):
        updater.find_github("1.0.0", force=True)


def test_a_download_is_kept_only_when_it_matches_its_checksum(github, pkgs, tmp_path):
    install = install_from(build_package(pkgs, "1.0.0"), tmp_path / "inst")
    github.publish(pkgs, "1.1.0", tamper="EdgeBench-1.1.0-windows.zip")
    with pytest.raises(updater.UpdateError, match="did not arrive intact"):
        updater.fetch_github(install, "1.1.0")
    assert not any((updater.work(install) / "downloads").iterdir())       # nothing half-made left
    github.publish(pkgs, "1.1.1", sums=False)
    with pytest.raises(updater.UpdateError, match="404"):
        updater.fetch_github(install, "1.1.1")
    github.files[f"/{updater.G['repo']}/releases/download/v1.1.1/SHA256SUMS.txt"] = b"<html>not a checksum list</html>"
    with pytest.raises(updater.UpdateError, match="has no checksums"):
        updater.fetch_github(install, "1.1.1")
    github.publish(pkgs, "1.1.2")
    seen = []
    z = updater.fetch_github(install, "1.1.2", progress=lambda d, t: seen.append((d, t)))
    assert z.name == "EdgeBench-1.1.2-windows.zip" and updater.read_package(z, "1.0.0").version == "1.1.2"
    assert seen[-1][0] == seen[-1][1] == z.stat().st_size
    with pytest.raises(updater.Cancelled):
        updater.fetch_github(install, "1.1.2", cancelled=lambda: True)
    assert not any((updater.work(install) / "downloads").iterdir())


def test_the_offline_zip_only_when_a_version_needs_other_packages(github, pkgs, tmp_path, monkeypatch):
    install = install_from(build_package(pkgs, "1.0.0"), tmp_path / "inst")
    monkeypatch.setattr(updater, "WINDOWS", True)
    github.publish(pkgs, "1.1.0", offline=True)
    stamp = install / ".venv" / updater.U["deps_stamp"]
    stamp.write_text(hashlib.sha256((install / "requirements.txt").read_bytes()).hexdigest(), encoding="utf-8")
    assert updater.fetch_github(install, "1.1.0").name == "EdgeBench-1.1.0-windows.zip"          # same packages
    github.publish(pkgs, "1.2.0", offline=True, extra_req="tomli==2.2.1")
    assert updater.fetch_github(install, "1.2.0").name == "EdgeBench-1.2.0-windows-offline.zip"  # new ones
    monkeypatch.setattr(updater, "WINDOWS", False)                        # its wheels would not fit here
    assert updater.fetch_github(install, "1.2.0").name == "EdgeBench-1.2.0-windows.zip"


def test_update_edge_bench_bat_updates_from_github(github, pkgs, tmp_path, home):
    """What double-clicking 'Update Edge Bench.bat' does: no folder set -> GitHub -> installed."""
    install = install_from(build_package(pkgs, "1.0.0", github=github.url), tmp_path / "inst")
    r = run_updater(install, home=home)
    assert r.returncode == 0 and "No newer version on GitHub; 1.0.0 is up to date." in r.stdout
    github.publish(pkgs, "1.1.0")
    r = run_updater(install, home=home)
    assert r.returncode == 0, r.stdout + r.stderr
    assert updater.installed_version(install) == "1.1.0" and updater.previous_version(install) == "1.0.0"
    assert (install / "my notes.txt").read_text(encoding="utf-8") == "the user's own file"
    assert "Downloading Edge Bench 1.1.0 from GitHub" in r.stdout
    # a folder with a newer version wins; GitHub's newer one wins over an older folder
    share = tmp_path / "share"
    share.mkdir()
    build_package(share, "1.2.0", github=github.url)
    github.publish(pkgs, "1.3.0")
    updater.set_update_folder(str(share))
    assert run_updater(install, home=home).returncode == 0 and updater.installed_version(install) == "1.3.0"
    build_package(share, "1.5.0", github=github.url)                    # now the folder is ahead of GitHub (1.3.0)
    r = run_updater(install, home=home)
    assert r.returncode == 0 and updater.installed_version(install) == "1.5.0" and "from GitHub" not in r.stdout
    github.publish(pkgs, "1.5.0")                                       # a tie: the folder's, no download
    build_package(share, "1.6.0", github=github.url)
    github.publish(pkgs, "1.6.0")
    r = run_updater(install, home=home)
    assert r.returncode == 0 and updater.installed_version(install) == "1.6.0" and "from GitHub" not in r.stdout
    # GitHub gone: said, nothing changed
    github.server.shutdown()
    github.server.server_close()
    github.latest = "9.0.0"
    updater.set_update_folder(None)
    r = run_updater(install, "--github", home=home)
    assert r.returncode == 1 and "Cannot reach GitHub" in r.stdout and updater.installed_version(install) == "1.6.0"


def _wait(client, cond, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = client.get("/api/update").json()
        if cond(s):
            return s
        time.sleep(0.05)
    raise AssertionError(f"timed out: {s}")


def test_the_app_offers_downloads_and_installs_a_github_release(github, pkgs, tmp_path, monkeypatch, client):
    install = install_from(build_package(pkgs, "1.0.0"), tmp_path / "inst")
    monkeypatch.setattr(srv, "UPDATE_ROOT", install)
    monkeypatch.setitem(srv.C.app, "version", "1.0.0")
    launched = []
    monkeypatch.setattr(updater, "launch", lambda root, **kw: launched.append((root, kw)))

    class Server:
        should_exit = False

    srv.api.state.server = Server()
    s = _wait(client, lambda s: s["github"]["state"] == "done")          # the first look starts by itself
    assert s["available"] is None and s["github"]["enabled"] and s["github"]["checked"]
    github.publish(pkgs, "1.1.0")
    s = client.post("/api/update/check").json()
    s = _wait(client, lambda s: s["github"]["state"] == "done")
    pkg = s["available"]
    assert pkg["version"] == "1.1.0" and pkg["remote"] and pkg["notes"][0]["items"] == ["Feature of 1.1.0."]

    # a damaged download: said, nothing launched, the app keeps running
    github.publish(pkgs, "1.1.0", tamper="EdgeBench-1.1.0-windows.zip")
    client.post("/api/update/download", json={"version": "1.1.0"})
    s = _wait(client, lambda s: s["download"]["state"] == "error")
    assert "did not arrive intact" in s["download"]["error"] and not launched and not srv.api.state.server.should_exit

    # cancelled half-way
    github.publish(pkgs, "1.1.0")
    github.slow = 0.05
    monkeypatch.setitem(updater.G, "chunk_bytes", 16384)
    client.post("/api/update/download", json={"version": "1.1.0"})
    _wait(client, lambda s: s["download"]["done"] > 0)
    assert client.post("/api/update/download", json={"version": "1.1.0"}).status_code == 409   # one at a time
    client.post("/api/update/cancel")
    s = _wait(client, lambda s: s["download"]["state"] == "idle")
    assert not launched

    # the real thing: downloaded, verified, staged, handed over, the app quits
    github.slow = 0
    client.post("/api/update/download", json={"version": "1.1.0"})
    s = _wait(client, lambda s: s["download"]["state"] == "installing")
    root, kw = launched[0]
    assert root == install and updater.installed_version(kw["staged"]) == "1.1.0"
    time.sleep(0.5)
    assert srv.api.state.server.should_exit

    # switched off: no check, no offer
    s = client.post("/api/update/github", json={"enabled": False}).json()
    assert s["github"]["state"] == "off" and s["available"] is None and not updater.github_enabled()
    monkeypatch.setattr(srv, "UPDATE_ROOT", ROOT)                         # a git folder: git pull
    r = client.post("/api/update/download", json={"version": "1.1.0"})
    assert r.status_code == 422 and "git pull" in r.json()["detail"]
