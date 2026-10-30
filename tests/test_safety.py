"""The app must never take the PC down.

The first Windows run that got past start-up loaded a large work CSV: the reader
held every cell as a Python string (~6x the file in RAM, 50 s per 250 MB), the
user retried, and a 64 GB PC ran out of memory at 99 % CPU.  These tests pin
the fixes: a reader whose memory follows the edges, not the file; cancel and
no double loads; one instance per user; a memory guard; Logic 2 cleaned up.
"""
from __future__ import annotations

import csv
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

import main
from app import guard, logic2
from app.constants import C
from app.readers import csv_digital
from server import api as srv

ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------ CSV reader

def _write_csv(path: Path, rows: int, nch: int = 16, seed: int = 1) -> None:
    """Logic 2 layout: one row per instant at which one channel changes."""
    rng = np.random.default_rng(seed)
    t = np.cumsum(rng.integers(1, 50, rows)) * 1e-8
    flips = np.zeros((rows, nch), np.int8)
    flips[np.arange(rows), rng.integers(0, nch, rows)] = 1
    levels = (np.cumsum(flips, axis=0) % 2).astype("U1")
    body = np.char.add(np.char.mod("%.9f", t), ",")
    body = np.char.add(body, np.apply_along_axis(",".join, 1, levels))
    with open(path, "w", encoding="utf-8") as f:
        f.write("Time [s]," + ",".join(f"Channel {i}" for i in range(nch)) + "\n")
        f.write("\n".join(body.tolist()) + "\n")


@pytest.fixture(scope="module")
def big_csv(tmp_path_factory) -> Path:
    p = tmp_path_factory.mktemp("big") / "big.csv"
    _write_csv(p, 1_000_000)
    return p


_PEAK = r"""
import resource, sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
import pandas, numpy
from app.readers import csv_digital
if sys.argv[2] != "-":
    cap = csv_digital.load(Path(sys.argv[2]))
    print(sum(len(s.times) for c in cap.channels for s in c.segments))
peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
print(peak if sys.platform == "darwin" else peak * 1024)
"""


@pytest.mark.skipif(sys.platform == "win32", reason="peak RSS via resource (POSIX)")
def test_csv_memory_follows_edges_not_the_file(big_csv):
    def run(arg: str) -> list[int]:
        out = subprocess.run([sys.executable, "-c", _PEAK, str(ROOT), arg],
                             capture_output=True, text=True, encoding="utf-8", check=True)
        return [int(x) for x in out.stdout.split()]

    (baseline,) = run("-")                        # python + pandas + numpy, no file
    edges, peak = run(str(big_csv))
    size = big_csv.stat().st_size
    assert edges == 1_000_000 - 1                  # every row but the first is one edge
    # the old reader needed ~5.5x the file; now it is the edges (8 B each) plus one chunk
    assert peak - baseline < 2 * size, f"{(peak - baseline) / 2**20:.0f} MB for a {size / 2**20:.0f} MB CSV"


def _naive(path: Path) -> list[list[tuple]]:
    """Row by row, the definition: a segment is a run without X; edges are level changes."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))[1:]
    t = [float(r[0]) for r in rows]
    out = []
    for k in range(len(rows[0]) - 1):
        segs, cur, prev = [], None, None
        for i, r in enumerate(rows):
            s = r[k + 1].strip()
            v = 0 if s == "0" else 1 if s == "1" else None
            if v is None:
                if cur:
                    segs.append((cur[0], cur[1], t[i], cur[2]))
                cur = None
            elif cur is None:
                cur = (v, t[i], [])
            elif v != prev:
                cur[2].append(t[i])
            prev = v
        if cur:
            segs.append((cur[0], cur[1], t[-1], cur[2]))
        out.append(segs)
    return out


def test_csv_reader_matches_row_by_row_definition(tmp_path, monkeypatch):
    rng = np.random.default_rng(11)
    for trial in range(80):
        n = int(rng.integers(1, 300))
        vals = rng.choice(["0", "1", "X", " 1", "0 ", ""], size=(n, 3), p=rng.dirichlet(np.ones(6)))
        f = tmp_path / f"t{trial}.csv"
        f.write_text("Time [s],A,B,C\n" + "\n".join(
            f"{i * 1e-6:.9f}," + ",".join(r) for i, r in enumerate(vals)) + "\n", encoding="utf-8")
        monkeypatch.setitem(C.files, "csv_chunk_rows", int(rng.integers(1, 40)))   # many chunk joins
        got = [[(s.initial, s.begin, s.end, list(s.times)) for s in ch.segments]
               for ch in csv_digital.load(f).channels]
        assert got == _naive(f), f"trial {trial}"


def test_csv_load_reports_progress_and_can_be_stopped(big_csv, monkeypatch):
    monkeypatch.setitem(C.files, "csv_chunk_rows", 100_000)
    seen = []

    def progress(msg):
        seen.append(msg)
        if len(seen) == 3:
            raise srv.Cancelled

    with pytest.raises(srv.Cancelled):
        csv_digital.load(big_csv, progress=progress)
    assert seen[0].startswith("Reading CSV") and seen[-1].endswith("%")


# --------------------------------------------------------- no double loads

def test_retry_is_refused_while_loading_and_remove_cancels():
    client = TestClient(srv.api)
    e = srv.Entry(id=next(srv.S.ids), path=Path("busy.csv"), kind="csv", status="loading")
    srv.S.entries[e.id] = e
    try:
        r = client.post(f"/api/files/{e.id}/retry")
        assert r.status_code == 409 and "still loading" in r.json()["detail"]
        assert "busy.csv" in srv.busy_text()
        client.delete(f"/api/files/{e.id}")
        assert e.cancelled and e.id not in srv.S.entries
    finally:
        srv.S.entries.pop(e.id, None)


def test_cancelled_load_stops_at_the_next_step(big_csv, monkeypatch):
    monkeypatch.setitem(C.files, "csv_chunk_rows", 50_000)
    e = srv.Entry(id=0, path=big_csv, kind="csv")
    steps = []

    def load(path, logic2=None, progress=None):
        progress("Reading CSV 1 %")
        steps.append(1)
        e.cancelled = True
        progress("Reading CSV 2 %")
        steps.append(2)

    monkeypatch.setattr(srv.readers, "load", load)
    srv._load(e)
    assert steps == [1] and e.capture is None and e.error is None


# ------------------------------------------------------------ one instance

_HOLD = r"""
import sys, time
sys.path.insert(0, sys.argv[1])
import main
lock = main.single_instance(__import__("pathlib").Path(sys.argv[2]))
print("held" if lock else "busy", flush=True)
time.sleep(float(sys.argv[3]))
"""


def test_only_one_instance_holds_the_lock(tmp_path):
    lock = tmp_path / "instance.lock"
    first = subprocess.Popen([sys.executable, "-c", _HOLD, str(ROOT), str(lock), "10"],
                             stdout=subprocess.PIPE, text=True)
    try:
        assert first.stdout.readline().strip() == "held"
        assert main.single_instance(lock) is None            # a second start cannot have it
    finally:
        first.kill()
        first.wait()
    again = main.single_instance(lock)                         # the OS released it with the process
    assert again is not None
    again.close()


def test_wait_reports_the_running_address(tmp_path, monkeypatch, capsys):
    import app.constants as consts
    monkeypatch.setattr(consts, "path", lambda key: tmp_path)
    monkeypatch.setitem(C.server, "startup_timeout_s", 1)
    (tmp_path / "port").write_text("1", encoding="utf-8")
    monkeypatch.setattr(main, "probe", lambda port, *a, **k: port == 4321)
    assert main.wait_ready() == 1 and "did not answer" in capsys.readouterr().out
    (tmp_path / "port").write_text("4321", encoding="utf-8")
    assert main.wait_ready() == 0 and "http://127.0.0.1:4321/" in capsys.readouterr().out


# ------------------------------------------------------------- memory guard

def test_guard_trips_above_the_limit_and_names_the_work():
    assert not guard.tripped(40, 100, 0.5) and guard.tripped(60, 100, 0.5)
    readings = iter([(10 * 2**30, 64 * 2**30), (40 * 2**30, 64 * 2**30)])
    got = []
    th = guard.start(lambda: " while loading huge.csv", on_trip=got.append,
                     read=lambda: next(readings, (40 * 2**30, 64 * 2**30)))
    th.join(5)
    assert got and "40.0 GB of the PC's 64 GB" in got[0] and "huge.csv" in got[0]


def test_guard_reads_this_platform_or_stays_off():
    m = guard.memory()
    if sys.platform in ("win32", "linux"):
        used, total = m
        assert 0 < used < total
    else:
        assert m is None


# ------------------------------------------------------------------ Logic 2

def test_logic2_that_exits_at_once_is_cleaned_up(tmp_path, monkeypatch):
    quitter = tmp_path / "Logic"
    quitter.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
    quitter.chmod(0o755)
    monkeypatch.setattr(logic2, "find_executable", lambda: quitter)
    monkeypatch.setattr(logic2, "_port_open", lambda port: False)
    l2 = logic2.Logic2()
    with pytest.raises(logic2.Logic2Error, match=r"exited straight away \(code 7\)"):
        l2._ensure()
    assert l2._proc is None and l2._job is None and l2.mode == "idle"


def test_windows_job_is_only_made_on_windows():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    try:
        job = logic2._job_for(p)
        assert (job is not None) == (sys.platform == "win32")
    finally:
        p.wait()
        logic2._close_job(job)

