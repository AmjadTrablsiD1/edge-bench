"""The measurement: one reference edge, then the first edge after it on every channel.

Reference
  manual -- the N-th `ref_edge` on `ref_channel`, at or after `search_from_s`.
  auto   -- the earliest `ref_edge` on any included channel after `search_from_s`
            ("the first signal that changes").  Ties go to the lowest index.

Every other channel
  the first `target_edge` at or after t_ref (an edge in the same sample counts:
  Δt = 0).  With a window, an edge later than t_ref + window is reported as
  out-of-window, not as a result.

Resolution: both edge times are quantised to one sample period, so Δt is good
to ±1 sample.  That figure travels with every result.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from app.capture import Capture
from app.constants import C
from app.edges import Edge, find_edge

ST = C.measure.statuses


@dataclass
class Settings:
    ref_mode: str = C.measure.default_ref_mode
    ref_channel: int | None = None
    ref_edge: str = C.measure.default_ref_edge
    occurrence: int = C.measure.default_occurrence           # 1-based
    search_from_s: float | None = None                        # None = capture start
    target_edge: str = C.measure.default_target_edge
    window_s: float | None = None
    glitch_s: float = 0.0
    excluded: list[int] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "Settings":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        s = cls(**known)
        s.validate()
        return s

    def validate(self) -> None:
        kinds = C.measure.edge_kinds
        if self.ref_mode not in C.measure.ref_modes:
            raise ValueError(f"reference mode must be one of {C.measure.ref_modes}")
        if self.ref_edge not in kinds or self.target_edge not in kinds:
            raise ValueError(f"edge kinds must be one of {kinds}")
        if not 1 <= int(self.occurrence) <= C.measure.max_occurrence:
            raise ValueError(f"occurrence must be 1 … {C.measure.max_occurrence}")
        if self.window_s is not None and self.window_s <= 0:
            raise ValueError("window must be positive (or empty for no window)")
        if self.glitch_s < 0:
            raise ValueError("glitch filter cannot be negative")
        if self.ref_mode == "manual" and self.ref_channel is None:
            raise ValueError("choose a reference channel")


@dataclass
class Reference:
    channel: int
    name: str
    edge: str
    time: float
    first_cross: float
    occurrence: int


@dataclass
class ChannelResult:
    index: int
    name: str
    status: str                 # key of constants.measure.statuses
    edge: str | None = None
    time: float | None = None   # edge time in capture, s
    dt: float | None = None     # edge - reference, s
    bounce: float | None = None # settled edge - first crossing, s (glitch filter only)
    next_dt: float | None = None  # for out_of_window: where the next edge actually is
    level_at_ref: int | None = None
    edges_total: int = 0


@dataclass
class Result:
    file: str
    reference: Reference | None
    channels: list[ChannelResult]
    resolution_s: float | None
    settings: Settings
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["status_labels"] = dict(ST)
        return d


def _reference(cap: Capture, s: Settings) -> tuple[Reference | None, list[str]]:
    start = cap.begin if s.search_from_s is None else s.search_from_s
    notes: list[str] = []
    if s.ref_mode == "manual":
        ch = cap.channel(int(s.ref_channel))
        e = find_edge(ch, start, s.ref_edge, s.glitch_s, skip=int(s.occurrence) - 1)
        if e is None:
            notes.append(f"{ch.name} has fewer than {s.occurrence} {s.ref_edge} edge(s) "
                         f"after {start:.9g} s — nothing to measure from.")
            return None, notes
        return Reference(ch.index, ch.name, e.kind, e.time, e.first_cross, int(s.occurrence)), notes

    best: tuple[Edge, int, str] | None = None
    for ch in cap.channels:
        if ch.index in s.excluded:
            continue
        e = find_edge(ch, start, s.ref_edge, s.glitch_s)
        if e is not None and (best is None or e.time < best[0].time):
            best = (e, ch.index, ch.name)
    if best is None:
        notes.append(f"No channel has a {s.ref_edge} edge after {start:.9g} s.")
        return None, notes
    e, idx, name = best
    return Reference(idx, name, e.kind, e.time, e.first_cross, 1), notes


def run(cap: Capture, s: Settings) -> Result:
    s.validate()
    ref, notes = _reference(cap, s)
    rows: list[ChannelResult] = []
    for ch in cap.channels:
        r = ChannelResult(index=ch.index, name=ch.name, status="excluded", edges_total=ch.edge_count)
        rows.append(r)
        if ch.index in s.excluded and not (ref and ref.channel == ch.index):
            continue
        if ref is None:
            r.status = "no_edge"
            continue
        r.level_at_ref = ch.level_at(ref.time)
        if ref.channel == ch.index:
            r.status, r.edge, r.time, r.dt = "reference", ref.edge, ref.time, 0.0
            r.bounce = ref.time - ref.first_cross if s.glitch_s > 0 else None
            continue
        if r.level_at_ref is None:
            r.status = "no_data"
            continue
        e = find_edge(ch, ref.time, s.target_edge, s.glitch_s)
        if e is None:
            r.status = "no_edge"
            continue
        dt = e.time - ref.time
        if s.window_s is not None and dt > s.window_s:
            r.status, r.next_dt = "out_of_window", dt
            continue
        r.status, r.edge, r.time, r.dt = "ok", e.kind, e.time, dt
        r.bounce = e.bounce_s if s.glitch_s > 0 else None
    if cap.resolution_s is None:
        notes.append("Sample rate unknown, so the ± resolution of Δt cannot be stated.")
    return Result(file=str(cap.path), reference=ref, channels=rows,
                  resolution_s=cap.resolution_s, settings=s, notes=notes + list(cap.notes))
