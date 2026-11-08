"""The direct .sal reader, against Logic 2 itself.

tests/fixtures/sal_corpus holds captures recorded by Logic 2.4.48's simulated
devices (scripts/make_sal_corpus.py) with Logic 2's own binary export of each:
three devices, 1 to 250 MS/s, a trigger capture and a trimmed one.  Every edge
the reader decodes must sit on the sample Logic 2 exports.  Synthetic files from
tests/salgen.py (an independent encoder, itself checked against Logic 2's bytes)
cover what the simulator never writes: long runs, trim windows inside a chunk,
split chunks, and millions of edges.
"""
from __future__ import annotations

import json
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest

from app import cache, measure, waveform
from app.capture import Capture, Channel
from app.constants import C
from app.readers import binary, sal, sal_native as N
from tests import salgen

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).parent / "fixtures"
CORPUS = FIX / "sal_corpus"
PAIRS = [(p, CORPUS / f"{p.stem}_bin") for p in sorted(CORPUS.glob("*.sal"))]
PAIRS += [(FIX / "sim_logic8.sal", FIX / "logic8_bin"), (FIX / "sim_pro16.sal", FIX / "pro16_bin")]
IDS = [p.stem for p, _ in PAIRS]


@pytest.fixture(autouse=True)
def tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "root", lambda: tmp_path / "cache")
    (tmp_path / "cache").mkdir()


def native(sal_path: Path, tmp: Path) -> dict:
    out = tmp / f"n_{sal_path.stem}"
    n = 0
    while out.exists():
        n += 1
        out = tmp / f"n_{sal_path.stem}_{n}"
    out.mkdir(parents=True)
    N.unpack(sal_path, out, None)
    return N.open_folder(out)[0]


def export(folder: Path, ch: int):
    segs, _ = binary.load_segments(folder / f"digital_{ch}.bin")
    return segs[0]


# --------------------------------------------------------------- vs Logic 2

@pytest.mark.parametrize("sal_path, exp", PAIRS, ids=IDS)
def test_every_edge_on_logic2s_sample(sal_path, exp, tmp_path):
    for ch, (seg, ix) in native(sal_path, tmp_path).items():
        ref = export(exp, ch)
        got, want = np.asarray(seg.times), np.asarray(ref.times)
        assert got.size == want.size, f"D{ch}"
        assert np.array_equal(np.rint(got * ix.rate), np.rint(want * ix.rate)), f"D{ch}"
        # times: ours are sample / rate; Logic 2's export rounds a few ULPs differently
        assert np.abs(got.view(np.int64) - want.view(np.int64)).max(initial=0) <= 16, f"D{ch}"
        assert seg.initial == ref.initial
        assert seg.begin == pytest.approx(ref.begin, abs=1e-12)
        assert seg.end == pytest.approx(ref.end, abs=1e-12)


def test_trigger_edge_is_time_zero(tmp_path):
    segs = native(CORPUS / "logic_8_25000k_trigger.sal", tmp_path)
    t, _ = segs[0]                                     # the trigger channel
    assert 0.0 in np.asarray(t.times) and t.begin < 0


def test_reencoding_gives_logic2s_bytes():
    """Our reading of the format, and salgen's encoder, reproduce every Logic 2 chunk byte for byte."""
    chunks = 0
    for sal_path, _ in PAIRS:
        with zipfile.ZipFile(sal_path) as z:
            for info in N.members(z).values():
                raw = z.read(info)
                h = N.read_header(raw)
                off = h.size
                for _ in range(h.chunks):
                    nb = struct.unpack_from("<Q", raw, off + 18)[0]
                    p = np.frombuffer(raw, np.uint8, nb, off + 26)
                    off += 26 + nb
                    reset = np.zeros(nb, bool)
                    reset[0] = True
                    runs = N.token_values(p, N.token_starts(p, reset)) + 1
                    assert salgen.encode_runs(runs) == p.tobytes()
                    chunks += 1
    assert chunks > 2000


@pytest.mark.parametrize("stem", ["logic_8_25000k_2s", "logic_pro_16_125000k_0.001s", "logic_8_25000k_trimmed"])
def test_measure_and_view_agree_with_the_export(stem, tmp_path):
    segs = native(CORPUS / f"{stem}.sal", tmp_path)
    rate = next(iter(segs.values()))[1].rate
    a = Capture(path=Path(stem), source="sal", sample_rate=rate,
                channels=[Channel(c, f"D{c}", [s]) for c, (s, _ix) in sorted(segs.items())])
    b = binary.load_folder(CORPUS / f"{stem}_bin", sample_rate=rate)
    for ref in (0, 3):
        for edge in ("rising", "falling"):
            s = measure.Settings(ref_channel=ref, ref_edge=edge)
            ra, rb = measure.run(a, s), measure.run(b, s)
            assert ra.reference.time == pytest.approx(rb.reference.time, abs=1e-15)
            for x, y in zip(ra.channels, rb.channels):
                assert x.status == y.status and x.edge == y.edge
                assert (x.dt is None) == (y.dt is None)
                if x.dt is not None:
                    assert x.dt == pytest.approx(y.dt, abs=1e-15)
    for t0, t1 in [(a.begin, a.end), (a.begin + (a.end - a.begin) * 0.4, a.begin + (a.end - a.begin) * 0.41)]:
        wa, wb = waveform.decimate(a, t0, t1, 900), waveform.decimate(b, t0, t1, 900)
        for ca, cb in zip(wa["channels"], wb["channels"]):
            for sa, sb in zip(ca["segments"], cb["segments"]):
                assert sa["mode"] == sb["mode"]
                if sa["mode"] == "exact":
                    assert sa["level"] == sb["level"] and np.allclose(sa["times"], sb["times"], rtol=0, atol=1e-15)
                else:                                  # the UI fills any pixel with an edge
                    busy_a, busy_b = np.array(sa["counts"]) > 0, np.array(sb["counts"]) > 0
                    assert np.array_equal(busy_a, busy_b)
                    quiet = ~busy_a
                    assert np.array_equal(np.array(sa["levels"])[quiet], np.array(sb["levels"])[quiet])


# ------------------------------------------------------------- synthetic

def _tiny(monkeypatch):
    """Pieces, blocks and marks so small that every boundary is crossed many times."""
    for k, v in {"piece_bytes": 64, "block_bytes": 200, "split_window_bytes": 48,
                 "mark_every": 7, "cache_span_marks": 2, "batch_marks": 3}.items():
        monkeypatch.setitem(C.sal_native, k, v)


@pytest.mark.parametrize("tiny", [False, True], ids=["default", "tiny-units"])
def test_lazy_times_behave_like_the_array(tiny, tmp_path, monkeypatch):
    if tiny:
        _tiny(monkeypatch)
    rng = np.random.default_rng(5)
    for sal_path, _ in PAIRS[:6]:
        for ch, (seg, _ix) in native(sal_path, tmp_path).items():
            full = np.asarray(seg.times)
            t = seg.times
            assert len(t) == full.size and t.shape == full.shape
            if not full.size:
                continue
            for i in rng.integers(0, full.size, 25).tolist() + [0, full.size - 1, -1]:
                assert t[i] == full[i]
            for _ in range(15):
                a, b = sorted(rng.integers(0, full.size + 1, 2).tolist())
                assert np.array_equal(t[a:b], full[a:b])
            probes = np.concatenate([full[rng.integers(0, full.size, 40)],
                                     rng.uniform(seg.begin - 1e-3, seg.end + 1e-3, 40)])
            for side in ("left", "right"):
                assert np.array_equal(t.searchsorted(probes, side=side), np.searchsorted(full, probes, side=side))
                assert t.searchsorted(float(probes[3]), side=side) == np.searchsorted(full, probes[3], side=side)
            grid = np.linspace(seg.begin, seg.end, 300)
            inside = np.diff(np.searchsorted(full, grid, side="left"))
            assert ((t.marks_in(grid) > 0) <= (inside > 0)).all()      # a mark always means an edge


@pytest.mark.parametrize("trim", [None, "begin", "both", "on-edge"])
def test_long_runs_trim_and_split_chunks(trim, tmp_path, monkeypatch):
    """Runs needing up to 7 bytes, a trim window inside chunks, chunks cut into pieces.

    "on-edge": the window starts exactly on an edge (one that is also a mark).
    That edge is not listed; it sets the start level.  (Logic 2's choice here
    is not visible in its exports; this keeps level and edges consistent.)
    """
    _tiny(monkeypatch)
    rng = np.random.default_rng(9)
    runs = rng.geometric(1 / 30, 3000).astype(np.int64)
    runs[::50] = rng.integers(2**20, 2**40, runs[::50].size)          # 4- to 7-byte tokens
    edges = 1000 + np.cumsum(runs)
    end = int(edges[-1]) + 77
    chunks = salgen.chunks_from_edges(edges, 1, 0, end, span=int(edges[-1] // 9))
    begin = stop = None
    if trim == "on-edge":
        begin = int(edges[399])                                       # 399 % mark_every == 0: a mark
    elif trim:
        begin = int(edges[400]) + 3                                   # inside a chunk
        stop = int(edges[2600]) - 2 if trim == "both" else None
    path = salgen.write_sal(tmp_path / "s.sal", {2: salgen.channel_file(5e8, chunks, begin, stop)}, 5e8)
    (seg, ix), = native(path, tmp_path).values()
    lo, hi = (begin if begin is not None else 0), (stop if stop is not None else end)
    want = edges[(edges > lo) & (edges < hi)]
    assert np.array_equal(np.rint(np.asarray(seg.times) * 5e8).astype(np.int64), want)
    assert seg.initial == 1 ^ (int(np.sum(edges <= lo)) & 1)
    assert seg.begin == lo / 5e8 and seg.end == hi / 5e8
    # a pixel is busy by a mark only if an edge of the window is in it
    first_gap = (lo + 1) / 5e8
    grid = np.r_[seg.begin, first_gap, np.linspace(first_gap, seg.end, 50)[1:]]
    exact = np.diff(np.searchsorted(np.asarray(seg.times), grid, side="left"))
    assert ((seg.times.marks_in(grid) > 0) <= (exact > 0)).all()


# --------------------------------------------------- unknown -> the export

def _rewrite(src: Path, dst: Path, channel=None, meta=None) -> Path:
    with zipfile.ZipFile(src) as z, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as out:
        for info in z.infolist():
            data = z.read(info)
            if info.filename == "meta.json" and meta:
                m = json.loads(data)
                meta(m["data"])
                data = json.dumps(m).encode()
            elif channel and N.members(z).get(0) is info:
                data = channel(bytearray(data))
            out.writestr(info.filename, bytes(data))
    return dst


def _set(buf: bytearray, off: int, fmt: str, value) -> bytearray:
    struct.pack_into(fmt, buf, off, value)
    return buf


def _first_payload(buf: bytearray) -> int:
    return N.read_header(bytes(buf)).size + 26


CORRUPT = {
    "version": dict(channel=lambda b: _set(b, 8, "<i", 5)),
    "runs do not add up": dict(channel=lambda b: _set(b, _first_payload(b), "<B", b[_first_payload(b)] ^ 0x01)),
    "gap between chunks": dict(channel=lambda b: _set(b, N.read_header(bytes(b)).size + 8, "<Q",
                                                      struct.unpack_from("<Q", b, N.read_header(bytes(b)).size + 8)[0] - 5)),
    "trailing bytes": dict(channel=lambda b: b + b"\x00\x01"),
    "glitch filter": dict(meta=lambda d: d["legacySettings"]["glitchFilter"].update(enabled=True)),
    "time origin": dict(meta=lambda d: d.update(timeManager={"t0": {"type": "marker"}})),
}


@pytest.mark.parametrize("case", list(CORRUPT))
def test_anything_unknown_goes_through_the_export(case, tmp_path):
    bad = _rewrite(FIX / "sim_logic8.sal", tmp_path / "bad.sal", **CORRUPT[case])
    with pytest.raises(N.NativeFormatError):
        out = tmp_path / "n"
        out.mkdir()
        N.unpack(bad, out, None)

    class ExportsFixture:                                  # stands in for Logic 2
        def export_binary(self, sal_path, out, chans, progress=None):
            for p in (FIX / "logic8_bin").glob("digital_*.bin"):
                (out / p.name).write_bytes(p.read_bytes())

    cap = sal.load(bad, logic2=ExportsFixture())
    assert any(n.startswith("Read through a Logic 2 export") for n in cap.notes)
    assert cap.channels[0].edge_count == export(FIX / "logic8_bin", 0).times.size


def test_a_sal_opens_without_logic2_and_is_cached(tmp_path):
    steps = []
    cap = sal.load(CORPUS / "logic_pro_8_250000k_0.5s.sal", logic2=None, progress=steps.append)
    assert any(s.startswith("Unpacking") for s in steps)
    assert "Read directly from the .sal (no Logic 2 export)." in cap.notes
    assert cap.sample_rate == 250e6 and len(cap.channels) == 8
    steps.clear()
    again = sal.load(CORPUS / "logic_pro_8_250000k_0.5s.sal", logic2=None, progress=steps.append)
    assert steps == [] and again.channels[3].edge_count == cap.channels[3].edge_count


# ------------------------------------------------------------- big files

_BIG = r"""
import resource, sys, tempfile
sys.path[:0] = [sys.argv[1], sys.argv[1] + "/tests"]
from pathlib import Path
import numpy as np
import salgen
from app import measure, waveform
from app.capture import Capture, Channel
from app.readers import sal_native as N
n, tmp = int(sys.argv[2]), Path(tempfile.mkdtemp())
if n:
    rng = np.random.default_rng(1)
    e = salgen.random_edges(rng, n)
    data = salgen.channel_file(1e8, salgen.chunks_from_edges(e, 0, 0, int(e[-1]) + 9, 2_000_000))
    path = salgen.write_sal(tmp / "big.sal", {0: data}, 1e8)
    del data, e, rng
    base = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    out = tmp / "n"; out.mkdir()
    N.unpack(path, out, None)
    (seg, ix), = N.open_folder(out)[0].values()
    cap = Capture(path=path, source="sal", sample_rate=1e8, channels=[Channel(0, "D0", [seg])])
    measure.run(cap, measure.Settings(ref_channel=0))
    waveform.decimate(cap, cap.begin, cap.end, 1600)
    assert len(seg.times) == n
    raw = (out / "digital-0.raw").stat().st_size
else:
    base = raw = 0
peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
scale = 1 if sys.platform == "darwin" else 1024
print(base * scale, peak * scale, raw)
"""


@pytest.mark.skipif(sys.platform == "win32", reason="peak RSS via resource (POSIX)")
def test_memory_does_not_follow_the_edge_count():
    """5 M edges: the old route held 8 bytes per edge (40 MB) in the export, and more as float64."""
    out = subprocess.run([sys.executable, "-c", _BIG, str(ROOT), "5000000"], capture_output=True,
                         text=True, encoding="utf-8", check=True)
    base, peak, raw = (int(x) for x in out.stdout.split())
    # the mapped channel file (resident on macOS, file-backed on Windows) plus a bounded working set
    assert peak - base < raw + 80 * 2**20, f"{(peak - base) / 2**20:.0f} MB for {raw / 2**20:.0f} MB of raw data"
