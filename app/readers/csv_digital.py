"""Logic 2 digital CSV -- the slow fallback, kept because it is what people have.

Format (Saleae, 2025 update): header ``Time [s],<name>,<name>,...``; one row per
instant at which *any* channel changes; values 0, 1 or X (no data / gap).
pandas' C parser is used because it is an order of magnitude faster than the
csv module on files this size.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from app.capture import Capture, Channel, Segment
from app.constants import C

_DEFAULT_NAME = re.compile(r"Channel (\d+)")


def sniff(path: Path) -> bool:
    if path.suffix.lower() != ".csv" or not path.is_file():
        return False
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        return f.readline().strip().startswith(C.files.csv_time_header)


def _segments(t: np.ndarray, col: np.ndarray, end: float) -> list[Segment]:
    """Split one column at X runs, then keep only rows where the level changed."""
    valid = col != C.files.csv_no_data
    segs: list[Segment] = []
    if not valid.any():
        return segs
    v = np.where(valid, col, "0").astype(np.int8)
    # boundaries of contiguous valid runs
    edges = np.flatnonzero(np.diff(np.concatenate(([0], valid.view(np.int8), [0]))))
    for a, b in zip(edges[0::2], edges[1::2]):
        lv, tt = v[a:b], t[a:b]
        change = np.flatnonzero(np.diff(lv)) + 1
        seg_end = t[b] if b < len(t) else end
        segs.append(Segment(initial=int(lv[0]), begin=float(tt[0]), end=float(seg_end),
                            times=tt[change].astype("<f8")))
    return segs


def load(path: Path, **_) -> Capture:
    import pandas as pd

    df = pd.read_csv(path, dtype=str, engine="c", encoding="utf-8-sig")
    if df.empty:
        raise ValueError(f"{path.name}: CSV has a header but no rows")
    t = df.iloc[:, 0].astype(np.float64).to_numpy()
    end = float(t[-1])
    names = [str(n) for n in df.columns[1:]]
    # "Channel 5" keeps index 5 so a CSV and a .sal of the same capture line up;
    # renamed channels fall back to their column position.
    parsed = [int(m.group(1)) if (m := _DEFAULT_NAME.fullmatch(n)) else None for n in names]
    use_parsed = None not in parsed and len(set(parsed)) == len(parsed)
    channels = []
    for k, name in enumerate(names):
        col = df[name].str.strip().to_numpy(dtype=str)
        idx = parsed[k] if use_parsed else k
        channels.append(Channel(index=idx, name=name, segments=_segments(t, col, end)))
    cap = Capture(path=path, source="csv", channels=channels)
    cap.notes.append("CSV does not carry the sample rate: Δt resolution cannot be stated.")
    cap.notes.append("CSV ends at the last change; the true capture end is unknown.")
    return cap
