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
                  extra_req: str = "", updater_src: Path | None = None) -> Path:
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
    r = run_updater(install, home=home)
    assert r.returncode == 1 and "No update folder is set" in r.stdout
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
    assert updater.ensure_deps(install) and calls[-1][4:7] == ["--no-index", "--find-links", str(install / "wheels" / tag)]
    (install / "requirements.txt").write_text("numpy==2.5.6\n", encoding="utf-8")
    monkeypatch.setattr(updater, "_pip", lambda cmd: 1)
    with pytest.raises(updater.UpdateError, match="Installing the Python packages failed"):
        updater.ensure_deps(install)
    stamp = (install / ".venv" / updater.U["deps_stamp"]).read_text(encoding="utf-8")
    assert stamp == hashlib.sha256(b"numpy==2.5.5\n").hexdigest()          # still the last good one


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
