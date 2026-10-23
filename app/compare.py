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

from dataclasses import asdict, dataclass, field, replace

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
    index: int                  # channel number in A (in B when the channel is only in B)
    index_b: int | None         # channel number in B -- differs from index when matched by name
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
    match: str                  # number | name
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


def _norm(name: str) -> str:
    return " ".join(name.split()).casefold()


def pair_channels(cap_a: Capture, cap_b: Capture, match: str) -> tuple[list[tuple[int | None, int | None]], list[str]]:
    """[(index in A, index in B)], A order first, then channels only in B.

    number: D3 with D3.
    name:   same name, ignoring case and extra spaces.  A name used twice in one
            capture is paired in channel order (first with first), with a note.
    """
    notes: list[str] = []
    a_idx = [c.index for c in cap_a.channels]
    b_idx = [c.index for c in cap_b.channels]
    if match == "number":
        pairs = [(i, i if i in b_idx else None) for i in a_idx] + [(None, i) for i in b_idx if i not in a_idx]
        renamed = [f"D{i}: {cap_a.channel(i).name} / {cap_b.channel(i).name}"
                   for i in a_idx if i in b_idx and _norm(cap_a.channel(i).name) != _norm(cap_b.channel(i).name)]
        if renamed:
            notes.append("Channels are matched by number; these have different names "
                         "(try matching by name): " + "; ".join(renamed))
        return pairs, notes
    if match != "name":
        raise ValueError(f"channel matching must be one of {K.match_modes}, not {match!r}")
    groups_b: dict[str, list[int]] = {}
    for c in cap_b.channels:
        groups_b.setdefault(_norm(c.name), []).append(c.index)
    seen_a: dict[str, int] = {}
    dupes = set()
    pairs: list[tuple[int | None, int | None]] = []
    used_b: set[int] = set()
    for c in cap_a.channels:
        key = _norm(c.name)
        k = seen_a.get(key, 0)
        seen_a[key] = k + 1
        cands = groups_b.get(key, [])
        if k > 0 or len(cands) > 1:
            dupes.add(c.name)
        if k < len(cands):
            pairs.append((c.index, cands[k]))
            used_b.add(cands[k])
        else:
            pairs.append((c.index, None))
    pairs += [(None, c.index) for c in cap_b.channels if c.index not in used_b]
    if dupes:
        notes.append("Names used more than once were paired in channel order: " + ", ".join(sorted(dupes)))
    moved = [f"{cap_a.channel(a).name} D{a}→D{b}" for a, b in pairs if a is not None and b is not None and a != b]
    if moved:
        notes.append("Matched by name; on a different channel in B: " + "; ".join(moved))
    return pairs, notes


def _settings_for_b(s: Settings, pairs: list[tuple[int | None, int | None]]) -> tuple[Settings, str | None]:
    """A's settings, with channel numbers translated to B's (reference, exclusions)."""
    a_to_b = {a: b for a, b in pairs if a is not None}
    sb = replace(s, excluded=[a_to_b[i] for i in s.excluded if a_to_b.get(i) is not None])
    if s.ref_mode != "manual" or s.ref_channel is None:
        return sb, None
    b_ref = a_to_b.get(int(s.ref_channel))
    if b_ref is None:
        return sb, "missing"
    return replace(sb, ref_channel=b_ref), None


def compare(cap_a: Capture, cap_b: Capture, s: Settings, tol: Tolerance | None = None,
            span_s: float | None = None, match: str = K.default_match) -> Comparison:
    tol = tol or Tolerance()
    pairs, notes = pair_channels(cap_a, cap_b, match)
    sb, ref_problem = _settings_for_b(s, pairs)
    ra = run(cap_a, s)
    if ref_problem:
        # B has no channel matching A's reference: B gets a result with no reference.
        rb = Result(file=str(cap_b.path), reference=None, resolution_s=cap_b.resolution_s, settings=sb,
                    channels=[ChannelResult(index=c.index, name=c.name, status="no_edge", edges_total=c.edge_count)
                              for c in cap_b.channels], notes=list(cap_b.notes))
        notes.append(f"Capture B has no channel named like A's reference "
                     f"({cap_a.channel(int(s.ref_channel)).name}) — only levels can be compared.")
    else:
        rb = run(cap_b, sb)
    res_values = [r for r in (cap_a.resolution_s, cap_b.resolution_s) if r]
    res = max(res_values) if res_values else None
    if cap_a.sample_rate and cap_b.sample_rate and cap_a.sample_rate != cap_b.sample_rate:
        notes.append(f"Sample rates differ ({cap_a.sample_rate:.4g} vs {cap_b.sample_rate:.4g} S/s): "
                     f"times compared to the coarser ±{fmt_time(res)}.")
    if s.ref_mode == "auto" and ra.reference and rb.reference:
        pair_of_ref = dict((a, b) for a, b in pairs if a is not None).get(ra.reference.channel)
        if pair_of_ref != rb.reference.channel:
            notes.append(f"First channel to change differs: {ra.reference.name} in A, "
                         f"{rb.reference.name} in B — each capture is aligned on its own.")

    span = None
    if ra.reference and rb.reference:
        room = min(cap_a.end - ra.reference.time, cap_b.end - rb.reference.time)
        span = room if span_s is None else min(span_s, room)
        if span_s is not None and span_s > room:
            notes.append(f"Span shortened to {fmt_time(room, res)}: the shorter capture ends there.")
    elif not ref_problem:
        missing = [n for n, r in (("A", ra), ("B", rb)) if r.reference is None]
        notes.append(f"No reference edge in capture {' and '.join(missing)} — only levels can be compared. "
                     "Check the reference settings.")

    by_a = {c.index: c for c in ra.channels}
    by_b = {c.index: c for c in rb.channels}
    t_ref_a = ra.reference.time if ra.reference else cap_a.begin
    t_ref_b = rb.reference.time if rb.reference else cap_b.begin
    rows: list[Row] = []
    for ia, ib in pairs:
        ca = by_a.get(ia) if ia is not None else None
        cb = by_b.get(ib) if ib is not None else None
        row = Row(index=ia if ia is not None else ib, index_b=ib,
                  name_a=ca.name if ca else None, name_b=cb.name if cb else None,
                  verdict="missing", a=None, b=None)
        rows.append(row)
        if ca is None or cb is None:
            what = "name" if match == "name" else "channel number"
            row.reasons.append(Reason("channel", "changed",
                                      f"No channel with this {what} in {'B' if cb is None else 'A'}"))
            continue
        if ia in s.excluded and not (ra.reference and ra.reference.channel == ia):
            row.verdict = "excluded"
            continue
        row.a = metrics_for(cap_a.channel(ia), ca, t_ref_a, span, s.glitch_s)
        row.b = metrics_for(cap_b.channel(ib), cb, t_ref_b, span, s.glitch_s)
        _compare_row(row, tol, res)

    summary = {v: sum(1 for r in rows if r.verdict == v) for v in K.verdicts}
    return Comparison(
        file_a=str(cap_a.path), file_b=str(cap_b.path),
        reference_a=asdict(ra.reference) if ra.reference else None,
        reference_b=asdict(rb.reference) if rb.reference and not ref_problem else None,
        span_s=span, resolution_s=res, tolerance=tol, settings=s, match=match, rows=rows,
        summary=summary, notes=notes + ra.notes + [n for n in rb.notes if n not in ra.notes],
        result_a=ra, result_b=rb)
