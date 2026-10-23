"""Baseline (A) against new (B): did the hardware signals change in a risky way?

Both captures get the *same* measurement settings.  Each is aligned on its own
reference edge, so "time" below means time after that capture's reference.
Over a common span after the reference (default: as long as the shorter
capture allows), every channel is described by a few numbers:

  dt           first edge after the reference (the measurement itself)
  edge         its direction
  level_at_ref the level when the reference fired
  first_pulse  width of the first pulse: first edge to the next edge
  edges        edges inside the span (activity)
  duty         fraction of the span spent high (compared only on channels
               with >= duty_min_edges edges: on a single transition it would
               just restate dt)
  min_pulse    shortest pulse inside the span (new glitches show up here)

and A is compared with B channel by channel:

  critical  an edge appeared or vanished, its direction flipped, the level at
            the reference differs, or the channel started / stopped toggling
  changed   a number moved beyond tolerance
  same      nothing beyond tolerance

A difference counts only if it exceeds "absolute or relative, whichever is
larger" -- separately for delays (default ±1 µs / ±5 %) and pulse widths
(±10 ns / ±10 %) -- and never less than `resolution_floor_samples` sample
periods, because each edge time is only good to ±1 sample.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

from app.capture import Capture, Channel
from app.constants import C
from app.edges import find_edge
from app.measure import ChannelResult, Result, Settings, run
from app.units import fmt_pct, fmt_time

K = C.compare


@dataclass
class Tolerance:
    time_abs_s: float = K.time_abs_s
    time_rel: float = K.time_rel
    pulse_abs_s: float = K.pulse_abs_s
    pulse_rel: float = K.pulse_rel
    count_abs: int = K.count_abs
    count_rel: float = K.count_rel
    duty_abs: float = K.duty_abs

    @classmethod
    def from_dict(cls, d: dict | None) -> "Tolerance":
        d = d or {}
        t = cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d and d[k] is not None})
        for k, v in asdict(t).items():
            if v < 0:
                raise ValueError(f"tolerance {k} cannot be negative")
        return t


@dataclass
class Metrics:
    status: str
    dt: float | None = None
    edge: str | None = None
    level_at_ref: int | None = None
    first_pulse: float | None = None
    edges: int | None = None
    duty: float | None = None
    min_pulse: float | None = None


@dataclass
class Reason:
    metric: str
    severity: str           # changed | critical
    text: str


@dataclass
class Row:
    index: int
    name_a: str | None
    name_b: str | None
    verdict: str            # same | changed | critical | excluded | missing
    a: Metrics | None
    b: Metrics | None
    reasons: list[Reason] = field(default_factory=list)
    dt_delta: float | None = None


@dataclass
class Comparison:
    file_a: str
    file_b: str
    reference_a: dict | None
    reference_b: dict | None
    span_s: float | None
    resolution_s: float | None
    tolerance: Tolerance
    settings: Settings
    rows: list[Row]
    summary: dict
    notes: list[str]
    result_a: Result
    result_b: Result

    def as_dict(self) -> dict:
        d = asdict(self)
        d["verdict_labels"] = dict(K.verdicts)
        d["status_labels"] = dict(C.measure.statuses)
        return d


# ------------------------------------------------------------------ metrics

def span_activity(ch: Channel, t0: float, t1: float) -> tuple[int, float | None, float | None]:
    """(edges in [t0, t1], fraction of covered time high, shortest pulse) -- vectorised."""
    count, high, covered = 0, 0.0, 0.0
    shortest: float | None = None
    for seg in ch.segments:
        a, b = max(t0, seg.begin), min(t1, seg.end)
        if a >= b:
            continue
        i0 = int(np.searchsorted(seg.times, a, side="left"))
        i1 = int(np.searchsorted(seg.times, b, side="right"))
        e = np.asarray(seg.times[i0:i1], dtype=float)
        count += e.size
        bounds = np.concatenate(([a], e, [b]))
        widths = np.diff(bounds)
        levels = seg.level_after(i0 - 1) ^ (np.arange(widths.size) & 1)
        high += float(widths[levels == 1].sum())
        covered += b - a
        if e.size >= 2:
            m = float(np.diff(e).min())
            shortest = m if shortest is None else min(shortest, m)
    return count, (high / covered if covered > 0 else None), shortest


def metrics_for(ch: Channel, r: ChannelResult, t_ref: float, span: float | None, glitch_s: float) -> Metrics:
    m = Metrics(status=r.status, dt=r.dt, edge=r.edge, level_at_ref=r.level_at_ref)
    if r.status == "ok" and r.time is not None:
        nxt = find_edge(ch, r.time, "any", glitch_s, inclusive=False)
        m.first_pulse = nxt.time - r.time if nxt else None
    if span is not None and span > 0:
        m.edges, m.duty, m.min_pulse = span_activity(ch, t_ref, t_ref + span)
    return m


# --------------------------------------------------------------- comparison

def _floor(res: float | None) -> float:
    return (res or 0.0) * K.resolution_floor_samples


def _time_limit(a: float, tol: Tolerance, res: float | None) -> float:
    """Allowed delay difference: ±abs or ±rel, whichever is larger."""
    return max(tol.time_abs_s, tol.time_rel * abs(a), _floor(res))


def _pulse_limit(a: float, tol: Tolerance, res: float | None) -> float:
    """Allowed pulse-width difference -- its own limits, ns-scale."""
    return max(tol.pulse_abs_s, tol.pulse_rel * abs(a), _floor(res))


def _compare_row(row: Row, tol: Tolerance, res: float | None) -> None:
    a, b = row.a, row.b
    reasons = row.reasons
    found_a, found_b = a.status in ("ok", "reference"), b.status in ("ok", "reference")

    def crit(metric: str, text: str) -> None:
        reasons.append(Reason(metric, "critical", text))

    def chg(metric: str, text: str) -> None:
        reasons.append(Reason(metric, "changed", text))

    if a.level_at_ref is not None and b.level_at_ref is not None and a.level_at_ref != b.level_at_ref:
        crit("level_at_ref", f"Level when the reference fired changed: {a.level_at_ref} → {b.level_at_ref}")
    if found_a and not found_b:
        crit("dt", f"Edge disappeared: {a.edge} at {fmt_time(a.dt, res, True)} in A, "
                   f"{C.measure.statuses[b.status].lower()} in B")
    elif found_b and not found_a:
        crit("dt", f"New edge: {b.edge} at {fmt_time(b.dt, res, True)} in B, "
                   f"{C.measure.statuses[a.status].lower()} in A")
    elif a.status == "ok" and b.status == "ok":
        if a.edge != b.edge:
            crit("edge", f"Edge direction flipped: {a.edge} → {b.edge}")
        d = b.dt - a.dt
        row.dt_delta = d
        if abs(d) > _time_limit(a.dt, tol, res):
            rel = f" ({fmt_pct(d / a.dt, True)})" if a.dt else ""
            chg("dt", f"Δt moved {fmt_time(d, res, True)}{rel}: {fmt_time(a.dt, res)} → {fmt_time(b.dt, res)}")
        if a.first_pulse is not None and b.first_pulse is not None:
            dp = b.first_pulse - a.first_pulse
            if abs(dp) > _pulse_limit(a.first_pulse, tol, res):
                chg("first_pulse", f"First pulse width {fmt_time(a.first_pulse, res)} → {fmt_time(b.first_pulse, res)}")

    if a.edges is not None and b.edges is not None:
        if (a.edges == 0) != (b.edges == 0):
            crit("edges", f"Activity {'started' if a.edges == 0 else 'stopped'}: "
                          f"{a.edges} → {b.edges} edges in the span")
        elif abs(b.edges - a.edges) > max(tol.count_abs, tol.count_rel * a.edges):
            chg("edges", f"Edge count {a.edges} → {b.edges} in the span")
    # Time high only means something on a channel that toggles: with one or two
    # edges it just restates the edge times, and would flag a delay twice.
    toggles = max(a.edges or 0, b.edges or 0) >= K.duty_min_edges
    if toggles and a.duty is not None and b.duty is not None and abs(b.duty - a.duty) > tol.duty_abs:
        chg("duty", f"Time high {fmt_pct(a.duty)} → {fmt_pct(b.duty)} of the span")
    if a.min_pulse is not None and b.min_pulse is not None:
        if a.min_pulse - b.min_pulse > _pulse_limit(a.min_pulse, tol, res):
            chg("min_pulse", f"Shorter pulses than baseline: shortest {fmt_time(a.min_pulse, res)} → "
                             f"{fmt_time(b.min_pulse, res)}")
    elif a.min_pulse is None and b.min_pulse is not None and a.edges == 1:
        chg("min_pulse", f"Pulses where A had none: shortest {fmt_time(b.min_pulse, res)}")

    row.verdict = ("critical" if any(r.severity == "critical" for r in reasons)
                   else "changed" if reasons else "same")


def compare(cap_a: Capture, cap_b: Capture, s: Settings, tol: Tolerance | None = None,
            span_s: float | None = None) -> Comparison:
    tol = tol or Tolerance()
    ra, rb = run(cap_a, s), run(cap_b, s)
    notes: list[str] = []
    res_values = [r for r in (cap_a.resolution_s, cap_b.resolution_s) if r]
    res = max(res_values) if res_values else None
    if cap_a.sample_rate and cap_b.sample_rate and cap_a.sample_rate != cap_b.sample_rate:
        notes.append(f"Sample rates differ ({cap_a.sample_rate:.4g} vs {cap_b.sample_rate:.4g} S/s): "
                     f"times compared to the coarser ±{fmt_time(res)}.")

    span = None
    if ra.reference and rb.reference:
        room = min(cap_a.end - ra.reference.time, cap_b.end - rb.reference.time)
        span = room if span_s is None else min(span_s, room)
        if span_s is not None and span_s > room:
            notes.append(f"Span shortened to {fmt_time(room, res)}: the shorter capture ends there.")
    else:
        missing = [n for n, r in (("A", ra), ("B", rb)) if r.reference is None]
        notes.append(f"No reference edge in capture {' and '.join(missing)} — only levels can be compared. "
                     "Check the reference settings.")

    by_a = {c.index: c for c in ra.channels}
    by_b = {c.index: c for c in rb.channels}
    rows: list[Row] = []
    renamed = []
    for idx in sorted(set(by_a) | set(by_b)):
        ca, cb = by_a.get(idx), by_b.get(idx)
        row = Row(index=idx, name_a=ca.name if ca else None, name_b=cb.name if cb else None,
                  verdict="missing", a=None, b=None)
        rows.append(row)
        if ca and cb and ca.name != cb.name:
            renamed.append(f"D{idx}: {ca.name} / {cb.name}")
        if ca is None or cb is None:
            row.reasons.append(Reason("channel", "changed",
                                      f"Channel only captured in {'A' if cb is None else 'B'}"))
            continue
        if idx in s.excluded and not (ra.reference and ra.reference.channel == idx):
            row.verdict = "excluded"
            continue
        row.a = metrics_for(cap_a.channel(idx), ca, ra.reference.time if ra.reference else cap_a.begin,
                            span, s.glitch_s)
        row.b = metrics_for(cap_b.channel(idx), cb, rb.reference.time if rb.reference else cap_b.begin,
                            span, s.glitch_s)
        _compare_row(row, tol, res)
    if renamed:
        notes.append("Channels are matched by number; these have different names: " + "; ".join(renamed))

    summary = {v: sum(1 for r in rows if r.verdict == v) for v in K.verdicts}
    return Comparison(
        file_a=str(cap_a.path), file_b=str(cap_b.path),
        reference_a=asdict(ra.reference) if ra.reference else None,
        reference_b=asdict(rb.reference) if rb.reference else None,
        span_s=span, resolution_s=res, tolerance=tol, settings=s, rows=rows,
        summary=summary, notes=notes + ra.notes + [n for n in rb.notes if n not in ra.notes],
        result_a=ra, result_b=rb)
