"""The one in-memory shape every reader produces.

A channel is a list of segments.  Each segment is a stretch of continuous data:
its level before the first edge, and the sorted edge times.  Saleae defines the
level to flip at every edge, so edge *i* of a segment is rising exactly when
``(i + initial) % 2 == 0`` -- direction is never stored, it is parity.

Times are float64 seconds: a read-only ``np.memmap`` onto a binary export, or
a ``sal_native.LazyTimes`` that decodes a .sal block by block.  Code that reads
them uses only len, [i], [a:b] and ``times.searchsorted`` -- never
``np.searchsorted(times, ...)`` or ``np.asarray(times)``, which would decode
(and hold) every edge of the channel.  Statistics over a span of edges go
through ``pulse_stats``, which both kinds answer from a per-block summary
instead of reading every edge.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from app.constants import C


def pulse_stats(times, i0: int, i1: int, blocks=None) -> tuple[float, float, float | None]:
    """The pulses between edges i0 .. i1 - 1: each one runs from an edge to the next.

    Returns (time in the 1st, 3rd, 5th ... pulse, time in all of them, the
    shortest), or (0, 0, None) with fewer than two edges.  Levels alternate,
    so "every second pulse" is the time spent at the level after edge i0.

    `blocks` = (bounds, odd, total, shortest): a summary where block k holds
    the pulses that start at edges bounds[k] .. bounds[k+1] - 1, `odd` being
    its 1st, 3rd ... pulse.  Whole blocks inside the span are added up from
    it; only the ends of the span are read from `times`.  Without a summary
    the span is read in chunks, so memory stays flat either way.
    """
    g0, g1 = int(i0), int(i1) - 1                    # pulse g starts at edge g
    if g1 <= g0:
        return 0.0, 0.0, None
    step = max(1, int(C.compare.span_chunk_edges))

    def read(a: int, b: int) -> tuple[float, float, float | None]:
        odd = total = 0.0
        low = None
        for j in range(a, b, step):
            e = np.asarray(times[j:min(j + step, b) + 1], dtype=float)
            gaps = np.diff(e)
            odd += float(gaps[(j - a) & 1::2].sum())          # the 1st, 3rd ... counted from a
            total += float(gaps.sum())
            m = float(gaps.min())
            low = m if low is None else min(low, m)
        return odd, total, low

    if blocks is None:
        return read(g0, g1)
    bounds, b_odd, b_total, b_low = blocks
    ka = int(np.searchsorted(bounds, g0, side="left"))
    kb = int(np.searchsorted(bounds, g1, side="right")) - 1
    if kb <= ka:
        return read(g0, g1)
    odd, total, low = read(g0, int(bounds[ka]))
    flip = ((bounds[ka:kb] - g0) & 1).astype(bool)   # a block that starts on a 2nd, 4th ... pulse
    odd += float(np.where(flip, b_total[ka:kb] - b_odd[ka:kb], b_odd[ka:kb]).sum())
    total += float(b_total[ka:kb].sum())
    lows = [float(b_low[ka:kb].min())] + ([low] if low is not None else [])
    t_odd, t_total, t_low = read(int(bounds[kb]), g1)
    odd += t_total - t_odd if (int(bounds[kb]) - g0) & 1 else t_odd
    total += t_total
    if t_low is not None:
        lows.append(t_low)
    return odd, total, min(lows)


@dataclass
class Segment:
    initial: int            # level (0/1) before the first edge of this segment
    begin: float            # first time covered by data, seconds
    end: float              # last time covered by data, seconds
    times: np.ndarray       # edge times, float64, ascending

    def __len__(self) -> int:
        return int(self.times.shape[0])

    def contains(self, t: float) -> bool:
        return self.begin <= t <= self.end

    def level_after(self, i: int) -> int:
        """Level right after edge i (i = -1 means before any edge)."""
        return self.initial ^ ((i + 1) & 1)

    def is_rising(self, i: int) -> bool:
        return ((i + self.initial) & 1) == 0

    def level_at(self, t: float) -> int:
        """Level at time t (an edge exactly at t has already happened)."""
        n = int(self.times.searchsorted(t, side="right"))
        return self.level_after(n - 1)


@dataclass
class Channel:
    index: int
    name: str
    segments: list[Segment]

    @property
    def edge_count(self) -> int:
        return sum(len(s) for s in self.segments)

    def segment_at(self, t: float) -> Segment | None:
        for s in self.segments:
            if s.contains(t):
                return s
        return None

    def level_at(self, t: float) -> int | None:
        s = self.segment_at(t)
        return None if s is None else s.level_at(t)

    @property
    def begin(self) -> float:
        return min(s.begin for s in self.segments) if self.segments else 0.0

    @property
    def end(self) -> float:
        return max(s.end for s in self.segments) if self.segments else 0.0


@dataclass
class Capture:
    path: Path
    source: str                         # "sal" | "binary" | "csv"
    channels: list[Channel]
    sample_rate: float | None = None    # digital samples per second, when known
    device: str | None = None
    format_version: int | None = None
    notes: list[str] = field(default_factory=list)
    via: str | None = None              # "logic2-export": a .sal the direct reader did not know

    @property
    def begin(self) -> float:
        return min((c.begin for c in self.channels), default=0.0)

    @property
    def end(self) -> float:
        return max((c.end for c in self.channels), default=0.0)

    @property
    def resolution_s(self) -> float | None:
        """One sample period: the quantisation of every edge time."""
        return 1.0 / self.sample_rate if self.sample_rate else None

    def channel(self, index: int) -> Channel:
        for c in self.channels:
            if c.index == index:
                return c
        raise KeyError(f"channel {index} is not in this capture "
                       f"(has {[c.index for c in self.channels]})")

    def summary(self) -> dict:
        return {
            "source": self.source,
            "device": self.device,
            "sample_rate": self.sample_rate,
            "resolution_s": self.resolution_s,
            "format_version": self.format_version,
            "via": self.via,
            "begin": self.begin,
            "end": self.end,
            "channels": [
                {"index": c.index, "name": c.name, "edges": c.edge_count}
                for c in self.channels
            ],
            "notes": self.notes,
        }
