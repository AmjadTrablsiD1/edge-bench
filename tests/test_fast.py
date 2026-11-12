"""The optional compiled indexing (app/readers/sal_fast.py, numba).

Its one promise: it can make opening a .sal faster, never different.  So the
compiled loops are held against the numpy code on real captures and on random
bytes, and every way they can be missing, wrong or broken must end in the
numpy code doing the work.
"""
from __future__ import annotations

import builtins
import json
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import cache
from app.constants import C
from app.readers import sal, sal_fast, sal_native as N
from server.api import api
from tests import salgen

FIX = Path(__file__).parent / "fixtures"
CORPUS = sorted((FIX / "sal_corpus").glob("*.sal")) + [FIX / "sim_logic8.sal", FIX / "board_a.sal"]
compiled = pytest.mark.skipif(not __import__("importlib").util.find_spec("numba"), reason="numba is not installed")


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    """Each test starts with nothing loaded, its own settings file and its own cache."""
    monkeypatch.setattr(sal_fast, "SETTINGS", tmp_path / "settings.json")
    monkeypatch.setattr(cache, "root", lambda: tmp_path / "cache")
    (tmp_path / "cache").mkdir()
    sal_fast.reset()
    yield
    sal_fast.reset()


def unpacked(path: Path, dest: Path) -> dict:
    dest.mkdir()
    N.unpack(path, dest, None)
    return {p.name: (p.read_bytes() if p.suffix == ".raw" else N.Index.load(p).__dict__)
            for p in sorted(dest.iterdir()) if p.suffix in (".raw", ".npz")}


def same(a: dict, b: dict) -> None:
    assert a.keys() == b.keys()
    for name in a:
        if isinstance(a[name], bytes):
            assert a[name] == b[name], name
        else:
            for k, v in a[name].items():
                assert np.array_equal(v, b[name][k]), (name, k)


# ----------------------------------------------------- same results, always

@compiled
@pytest.mark.parametrize("path", CORPUS, ids=[p.stem for p in CORPUS])
def test_compiled_index_is_the_numpy_index(path, tmp_path, monkeypatch):
    """Every recorded capture: the index built by the compiled loops is the numpy one, array for array."""
    assert sal_fast.kernels() is not None, sal_fast.status()
    fast = unpacked(path, tmp_path / "fast")
    monkeypatch.setattr(sal_fast, "kernels", lambda: None)
    same(fast, unpacked(path, tmp_path / "numpy"))


@compiled
def test_random_bytes_get_the_same_answer_or_the_same_refusal():
    """Anything at all, valid or not: both read it alike, or both refuse it."""
    kn = sal_fast.kernels()
    rng = np.random.default_rng(12)
    agreed = refused = 0
    for _ in range(4000):
        n = int(rng.integers(1, 40))
        kind = rng.integers(0, 4)
        if kind == 0:                                  # any bytes
            p = rng.integers(0, 256, n).astype(np.uint8)
        elif kind == 1:                                # mostly starts and ends, few continuations
            p = rng.choice(np.array([3, 0x3F, 0x40, 0x41, 0x7F, 0x80, 0xFF], np.uint8), n, p=[.25, .1, .2, .2, .1, .1, .05])
        else:                                          # a valid stream, sometimes damaged
            p = np.frombuffer(salgen.encode_runs(np.where(rng.random(n) < 0.2, 2 ** rng.integers(6, 62, n), rng.integers(1, 300, n))), np.uint8).copy()
            if kind == 3:
                p[rng.integers(0, p.size)] ^= 1 << rng.integers(0, 8)
        cuts = np.unique(np.r_[0, rng.integers(0, p.size, int(rng.integers(0, 4)))]).astype(np.int64)
        got = want = None
        try:
            want = N._tokens_numpy(p, cuts)
        except N.NativeFormatError:
            pass
        try:
            got = N._tokens_fast(kn, p, cuts)
        except N.NativeFormatError:
            pass
        assert (got is None) == (want is None), (p.tolist(), cuts.tolist())
        if want is None:
            refused += 1
            with pytest.raises(N.NativeFormatError):
                N._index_block_fast(kn, p, cuts, cuts + 50, True, 3)
            continue
        agreed += 1
        at = np.arange(want[1].size) if want[0] is None else want[0]
        assert np.array_equal(at, got[0]) and np.array_equal(want[1].astype(np.int64), got[1]), p.tolist()
        for first in (True, False):
            a = N._index_block_numpy(p, cuts, cuts + 50, first, 3)
            b = N._index_block_fast(kn, p, cuts, cuts + 50, first, 3)
            assert all(np.array_equal(x, y) for x, y in zip(a, b)), (p.tolist(), cuts.tolist(), first)
    assert agreed > 800 and refused > 800              # both kinds were really tried


@compiled
def test_a_wrong_file_cannot_make_the_compiled_loops_leave_their_arrays():
    """Bounds are checked inside the loops: a pstart beyond the data is an error, not a read past the end."""
    kn = sal_fast.kernels()
    p = np.array([5, 6, 7], np.uint8)
    with pytest.raises((N.NativeFormatError, IndexError)):
        N._tokens_fast(kn, p, np.array([0, 9], np.int64))


# ------------------------------------------- missing, wrong, broken, off

def opens_right(tmp_path) -> None:
    cap = sal.load(FIX / "board_a.sal")
    assert [c.edge_count for c in cap.channels][:5] == [40000, len(cap.channels[1].segments[0].times), cap.channels[2].edge_count, 9600, 1]
    assert float(cap.channels[4].segments[0].times[0]) == 0.01


def test_without_numba_the_numpy_code_is_used(tmp_path, monkeypatch):
    real = builtins.__import__

    def no_numba(name, *a, **k):
        if name == "numba" or name.startswith("numba.") or name == "app.readers._sal_kernels":
            raise ImportError("No module named 'numba'")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_numba)
    monkeypatch.delitem(__import__("sys").modules, "app.readers._sal_kernels", raising=False)
    assert sal_fast.kernels() is None
    st = sal_fast.status()
    assert st == {**st, "wanted": True, "ready": True, "active": False, "installed": False}
    assert "not installed" in st["text"]
    opens_right(tmp_path)


@compiled
def test_loops_that_fail_their_self_test_are_not_used(tmp_path, monkeypatch):
    from app.readers import _sal_kernels
    real = _sal_kernels.tokens

    def off_by_one(p, pstart, max_bytes):                      # wrong only on long runs
        code, n, at, runs = real(p, pstart, max_bytes)
        return code, n, at, runs + (runs > 5000)

    monkeypatch.setattr(_sal_kernels, "tokens", off_by_one)
    assert sal_fast.kernels() is None
    assert "self-test failed (tokens differ)" in sal_fast.status()["text"] and sal_fast.status()["installed"]
    opens_right(tmp_path)


@compiled
def test_a_loop_that_breaks_while_running_hands_over_to_numpy(tmp_path, monkeypatch, caplog):
    kn = sal_fast.kernels()
    calls = []

    class Broken:
        tokens = staticmethod(kn.tokens)

        @staticmethod
        def index_block(*a):
            calls.append(1)
            raise SystemError("LLVM went away")

    monkeypatch.setitem(sal_fast._state, "kernels", Broken)
    opens_right(tmp_path)
    assert 1 <= len(calls) <= N.workers()                      # only the blocks already under way; then off
    assert sal_fast.kernels() is None and "SystemError" in sal_fast.status()["text"]


@compiled
def test_a_file_the_reader_refuses_does_not_switch_the_loops_off(tmp_path):
    bad = salgen.channel_file(1e8, [(0, 0, np.array([5, 9]))]) + b"\x01"       # trailing byte
    path = salgen.write_sal(tmp_path / "bad.sal", {0: bad}, 1e8)
    (tmp_path / "n").mkdir()
    with pytest.raises(N.NativeFormatError):
        N.unpack(path, tmp_path / "n", None)
    assert sal_fast.kernels() is not None and sal_fast.status()["active"]


def test_the_switch_is_kept_and_obeyed(tmp_path):
    assert sal_fast.wanted()                                   # default: on
    sal_fast.set_wanted(False)
    assert json.loads((tmp_path / "settings.json").read_text(encoding="utf-8")) == {"fast_index": False}
    assert not sal_fast.wanted() and sal_fast.kernels() is None
    assert sal_fast.status()["text"] == "Off" and not sal_fast.status()["active"]
    opens_right(tmp_path)
    (tmp_path / "settings.json").write_text('{"logic2_path": "x", "fast_index": false}', encoding="utf-8")
    sal_fast.set_wanted(True)                                  # other settings stay
    assert json.loads((tmp_path / "settings.json").read_text(encoding="utf-8")) == {"logic2_path": "x", "fast_index": True}


def test_status_and_switch_over_http(tmp_path):
    client = TestClient(api)
    off = client.post("/api/settings/fast", json={"enabled": False}).json()
    assert off["wanted"] is False and off["active"] is False and off["text"] == "Off"
    assert client.get("/api/status").json()["fast"]["wanted"] is False
    on = client.post("/api/settings/fast", json={"enabled": True}).json()
    assert on["wanted"] is True
    sal_fast.kernels()                                         # what the background warm-up does
    st = client.get("/api/status").json()["fast"]
    assert st["ready"] and st["active"] == st["installed"] and (st["text"].startswith("On") or "not installed" in st["text"])


@compiled
def test_loops_that_cannot_be_checked_are_not_used(tmp_path, monkeypatch):
    """Anything going wrong while loading or checking (here: the compiler) means numpy, not a failed capture."""
    from app.readers import _sal_kernels

    def boom(*a):
        raise RuntimeError("cannot compile on this PC")

    monkeypatch.setattr(_sal_kernels, "index_block", boom)
    assert sal_fast.kernels() is None
    assert "RuntimeError: cannot compile on this PC" in sal_fast.status()["text"]
    opens_right(tmp_path)
