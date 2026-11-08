"""Test-only: write Logic 2 .sal files from edge lists.

An encoder written independently of app/readers/sal_native.py (rule 15: the two
cannot be wrong the same way).  It is itself checked against Logic 2: re-encoding
every chunk of the recorded corpus must give Logic 2's bytes exactly.  With it
the tests reach what Logic 2's simulated devices never produce: runs needing 4 to
9 bytes, trim windows, chunks big enough to be split, and millions of edges.
"""
from __future__ import annotations

import json
import struct
import zipfile
from pathlib import Path

import numpy as np

META_FROM = Path(__file__).parent / "fixtures" / "sim_logic8.sal"


def encode_runs(runs) -> bytes:
    """Run lengths -> bytes.  v = run - 1; 00vvvvvv if v < 64, else 01.. 1.. 0.. (big-endian)."""
    v = np.asarray(runs, dtype=np.uint64) - np.uint64(1)
    if v.size == 0:
        return b""
    # bytes needed: 1 for v < 2**6, else n with v < 2**(6 + 7 (n - 1))
    n = np.ones(v.size, np.int64)
    for k in range(2, 10):
        n[v >= np.uint64(1) << np.uint64(6 + 7 * (k - 2))] = k
    tok = np.repeat(np.arange(v.size), n)
    pos = np.arange(tok.size) - np.repeat(np.cumsum(n) - n, n)       # 0 .. n-1 inside a token
    shift = (7 * (n[tok] - 1 - pos)).astype(np.uint64)
    digit = (v[tok] >> shift) & np.uint64(0x7F)
    first, last, single = pos == 0, pos == n[tok] - 1, n[tok] == 1
    out = np.where(first, (digit & np.uint64(0x3F)) | np.uint64(0x40), digit)
    out = np.where(~first & ~last, digit | np.uint64(0x80), out)
    out = np.where(single, v[tok] & np.uint64(0x3F), out)
    return out.astype(np.uint8).tobytes()


def chunks_from_edges(edges, level0: int, start: int, end: int, span: int) -> list[tuple[int, int, np.ndarray]]:
    """Cut [start, end) into chunks of about `span` samples, each later one starting on an edge."""
    edges = np.asarray(edges, dtype=np.int64)
    cuts = [start]
    while True:
        i = int(np.searchsorted(edges, cuts[-1] + span, side="left"))
        if i >= edges.size:
            break
        cuts.append(int(edges[i]))
    cuts.append(end)
    out = []
    for a, b in zip(cuts[:-1], cuts[1:]):
        inside = edges[np.searchsorted(edges, a, side="right"):np.searchsorted(edges, b, side="left")]
        bounds = np.concatenate(([a], inside, [b]))
        level = level0 ^ (int(np.searchsorted(edges, a, side="right")) & 1)
        out.append((a, level, np.diff(bounds)))
    return out


def channel_file(rate: float, chunks, begin: int | None = None, end: int | None = None,
                 version: int = 4, kind: int = 100) -> bytes:
    head = b"<SALEAE>" + struct.pack("<iiBdQd", version, kind, 1, rate, 1_792_787_920_273, 0.5)
    for opt in (begin, end):
        head += struct.pack("<B", 0) if opt is None else struct.pack("<BQ", 1, opt)
    head += struct.pack("<Q", len(chunks))
    body = bytearray()
    for a, level, runs in chunks:
        payload = encode_runs(runs)
        body += struct.pack("<QQBBQ", a, a + int(np.sum(runs)), level, 0, len(payload)) + payload
    return head + bytes(body)


def write_sal(path: Path, files: dict[int, bytes], rate: float, *, trigger_s: float | None = None,
              glitch: bool = False) -> Path:
    """A .sal zip: the fixture's meta.json, adjusted, plus one channel file per entry."""
    with zipfile.ZipFile(META_FROM) as z:
        meta = json.loads(z.read("meta.json"))
    d = meta["data"]
    d["legacySettings"]["enabledChannels"] = [{"type": "Digital", "index": c} for c in sorted(files)]
    d["legacySettings"]["sampleRate"]["digital"] = rate
    d["legacySettings"]["glitchFilter"]["enabled"] = glitch
    d["timeManager"] = {"t0": {"type": "trigger" if trigger_s is not None else "startOfCapture"}}
    d["digitalTriggerTime"] = trigger_s if trigger_s is not None else -1
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("meta.json", json.dumps(meta))
        for c, data in files.items():
            z.writestr(f"digital-{c}.bin", data)
    return path


def random_edges(rng, n: int, start: int = 0, long_every: int = 0, long_run: int = 0) -> np.ndarray:
    """n edge samples after `start`: mostly short runs, every `long_every`-th one `long_run` long."""
    runs = rng.geometric(1 / 40, n).astype(np.int64)
    if long_every:
        runs[::long_every] = long_run
    return start + np.cumsum(runs)
