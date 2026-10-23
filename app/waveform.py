"""What the viewport draws: a window of every channel, reduced to its pixels.

Few edges in view -> the exact edge times ("exact").
More than ~2 per pixel -> one bucket per pixel: the level at the bucket start
and how many edges fall inside ("buckets").  The UI draws a busy bucket as a
filled block, which is what a scope does, and it can never miss an edge: a
bucket with one edge is still marked.

Everything is searchsorted on the memory-mapped times: the cost depends on the
pixel count, not on how many edges the capture holds.
"""
from __future__ import annotations

import numpy as np

from app.capture import Capture, Channel
from app.constants import C


def _channel(ch: Channel, t0: float, t1: float, px: int) -> dict:
    out = []
    for seg in ch.segments:
        a, b = max(t0, seg.begin), min(t1, seg.end)
        if a >= b:
            continue
        times = seg.times
        i0 = int(np.searchsorted(times, a, side="left"))
        i1 = int(np.searchsorted(times, b, side="right"))
        n = i1 - i0
        seg_px = max(1, int(round(px * (b - a) / (t1 - t0))))
        start_level = seg.level_after(i0 - 1)
        if n <= seg_px * C.waveform.max_exact_per_px:
            out.append({"mode": "exact", "begin": a, "end": b, "level": start_level,
                        "times": np.asarray(times[i0:i1], dtype=float).tolist()})
        else:
            grid = np.linspace(a, b, seg_px + 1)
            idx = np.searchsorted(times, grid, side="left")
            counts = np.diff(idx)
            levels = seg.initial ^ (idx[:-1] & 1)
            out.append({"mode": "buckets", "begin": a, "end": b,
                        "levels": levels.astype(int).tolist(),
                        "counts": np.minimum(counts, 2).astype(int).tolist()})
    return {"index": ch.index, "name": ch.name, "segments": out}


def decimate(cap: Capture, t0: float, t1: float, px: int,
             channels: list[int] | None = None) -> dict:
    px = int(min(max(px, C.waveform.min_px), C.waveform.max_px))
    if not t1 > t0:
        raise ValueError("waveform window must have t1 > t0")
    wanted = cap.channels if channels is None else [c for c in cap.channels if c.index in channels]
    return {"t0": t0, "t1": t1, "px": px,
            "channels": [_channel(c, t0, t1, px) for c in wanted]}
