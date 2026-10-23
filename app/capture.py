"""The one in-memory shape every reader produces.

A channel is a list of segments.  Each segment is a stretch of continuous data:
its level before the first edge, and the sorted edge times.  Saleae defines the
level to flip at every edge, so edge *i* of a segment is rising exactly when
``(i + initial) % 2 == 0`` -- direction is never stored, it is parity.

Times are float64 seconds, usually a read-only ``np.memmap`` straight onto the
exported binary file, so nothing is loaded that is not looked at.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


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
        n = int(np.searchsorted(self.times, t, side="right"))
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
            "begin": self.begin,
            "end": self.end,
            "channels": [
                {"index": c.index, "name": c.name, "edges": c.edge_count}
                for c in self.channels
            ],
            "notes": self.notes,
        }
