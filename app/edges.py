"""Finding edges: binary search to a time, parity for direction.

``find_edge(channel, t, kind, glitch_s, skip)`` returns the (skip+1)-th edge of
the wanted kind at or after t.

Without a glitch filter it is O(log n): ``searchsorted`` to t, then at most one
step to fix the direction.

With a glitch filter (``glitch_s > 0``) it runs the same state machine as a
hardware deglitcher, but only over the handful of edges around t:
  * a new level is accepted only once it has held for >= glitch_s;
  * the reported edge time is the edge that *started* the accepted level -- after
    contact bounce, that is the last bounce, matching what Logic 2's own glitch
    filter shows;
  * ``first_cross`` keeps the first departure from the old level, so the bounce
    length is visible (``time - first_cross``);
  * a short pulse that returns to the old level is dropped entirely.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.capture import Channel, Segment
from app.constants import C


@dataclass(frozen=True)
class Edge:
    time: float
    rising: bool
    first_cross: float          # == time unless the glitch filter merged a bounce

    @property
    def kind(self) -> str:
        return "rising" if self.rising else "falling"

    @property
    def bounce_s(self) -> float:
        return self.time - self.first_cross


def _matches(rising: bool, kind: str) -> bool:
    return kind == "any" or (kind == "rising") == rising


def _fast(seg: Segment, i: int, kind: str, skip: int) -> tuple[Edge | None, int]:
    """No filter: from index i, the (skip+1)-th matching edge.  Returns (edge, skips left)."""
    n = len(seg)
    if i >= n:
        return None, skip
    if kind != "any" and not _matches(seg.is_rising(i), kind):
        i += 1
    step = 1 if kind == "any" else 2
    available = 0 if i >= n else (n - 1 - i) // step + 1
    if skip >= available:
        return None, skip - available
    j = i + skip * step
    t = float(seg.times[j])
    return Edge(t, seg.is_rising(j), t), 0


def _hold(seg: Segment, j: int) -> float:
    nxt = float(seg.times[j + 1]) if j + 1 < len(seg) else seg.end
    return nxt - float(seg.times[j])


def _filtered(seg: Segment, i: int, t: float, kind: str, glitch_s: float,
              skip: int, inclusive: bool) -> tuple[Edge | None, int]:
    """Deglitched search inside one segment, starting near index i (first edge >= t)."""
    times = seg.times
    limit = C.measure.glitch_walk_limit
    # 1. Stable level at t: walk back to the last edge whose level held >= glitch_s.
    j, walked = i - 1, 0
    while j >= 0 and _hold(seg, j) < glitch_s:
        j -= 1
        walked += 1
        if walked > limit:
            raise RuntimeError("Too many glitches before the reference to resolve the level; "
                               "lower the glitch filter or move the search start.")
    stable = seg.level_after(j)                 # j == -1 -> the segment's initial level
    # 2. Walk forward from there, accepting levels that hold.
    departure: float | None = None
    k, n, walked = j + 1, len(seg), 0
    while k < n:
        walked += 1
        if walked > limit:
            raise RuntimeError("Glitch filter walked too many edges; use a smaller occurrence "
                               "or a smaller glitch width.")
        tk = float(times[k])
        lvl = seg.level_after(k)
        held = _hold(seg, k) >= glitch_s
        if lvl != stable:
            if departure is None:
                departure = tk
            if held:
                stable = lvl
                edge = Edge(tk, lvl == 1, departure)
                departure = None
                after = tk >= t if inclusive else tk > t
                if after and _matches(edge.rising, kind):
                    if skip == 0:
                        return edge, 0
                    skip -= 1
        elif held:
            departure = None                    # returned and settled: that was a glitch
        k += 1
    return None, skip


def find_edge(ch: Channel, t: float, kind: str = "any", glitch_s: float = 0.0,
              skip: int = 0, inclusive: bool = True) -> Edge | None:
    """The (skip+1)-th edge of `kind` at (inclusive) or after t, across segments."""
    if kind not in C.measure.edge_kinds:
        raise ValueError(f"edge kind must be one of {C.measure.edge_kinds}, not {kind!r}")
    for seg in ch.segments:
        if seg.end < t or len(seg) == 0:
            continue
        start_t = max(t, seg.begin)
        i = int(np.searchsorted(seg.times, start_t, side="left" if inclusive else "right"))
        if glitch_s > 0:
            edge, skip = _filtered(seg, i, start_t, kind, glitch_s, skip, inclusive)
        else:
            edge, skip = _fast(seg, i, kind, skip)
        if edge is not None:
            return edge
    return None


def count_in(ch: Channel, t0: float, t1: float) -> int:
    """Raw edges in [t0, t1] -- for the waveform and the evidence panel."""
    total = 0
    for seg in ch.segments:
        a = int(np.searchsorted(seg.times, t0, side="left"))
        b = int(np.searchsorted(seg.times, t1, side="right"))
        total += max(0, b - a)
    return total
