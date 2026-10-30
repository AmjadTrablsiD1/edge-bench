"""Logic 2 digital CSV -- the slow fallback, kept because it is what people have.

Format (Saleae, 2025 update): header ``Time [s],<name>,<name>,...``; one row per
instant at which *any* channel changes; values 0, 1 or X (no data / gap).

Memory is bounded by the edges, not by the file: the CSV is read in chunks of
C.files.csv_chunk_rows rows, each channel column is parsed as a category (one
byte per cell instead of a Python string per cell) and turned straight into
change times.  The first version read the whole file with dtype=str -- about
6x the file size in RAM -- and a multi-GB work CSV took a 64 GB PC down.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Callable

import numpy as np

from app.capture import Capture, Channel, Segment
from app.constants import C

_DEFAULT_NAME = re.compile(r"Channel (\d+)")
_NONE = np.int8(-1)                      # X, empty cell, or "no row yet"


def sniff(path: Path) -> bool:
    if path.suffix.lower() != ".csv" or not path.is_file():
        return False
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        return f.readline().strip().startswith(C.files.csv_time_header)


def _levels(col) -> np.ndarray:
    """A categorical column -> int8 levels: 0, 1, or -1 for X / empty."""
    cats = [str(c).strip() for c in col.cat.categories]
    lut = np.array([0 if c == "0" else 1 if c == "1" else -1 for c in cats] + [-1], dtype=np.int8)
    return lut[col.cat.codes.to_numpy()]            # code -1 (missing) picks the trailing -1


class _Track:
    """One channel, fed chunk by chunk; keeps only change times."""

    def __init__(self) -> None:
        self.prev = _NONE
        self.segs: list[Segment] = []
        self.cur: tuple[int, float, list[np.ndarray]] | None = None   # initial, begin, time parts

    def feed(self, t: np.ndarray, v: np.ndarray) -> None:
        ext = np.empty(len(v) + 1, dtype=np.int8)
        ext[0], ext[1:] = self.prev, v
        ch = np.flatnonzero(ext[1:] != ext[:-1])         # row i differs from the row before it
        if ch.size:
            a, b = ext[ch], v[ch]
            level = ch[(a >= 0) & (b >= 0)]               # 0 <-> 1 inside a valid run
            pos = 0
            for i in ch[(a < 0) | (b < 0)]:               # a valid run starts or ends (rare)
                k = int(np.searchsorted(level, i))
                if self.cur is not None and k > pos:
                    self.cur[2].append(t[level[pos:k]])
                pos = k
                if v[i] < 0:
                    self._close(float(t[i]))              # the run ends where X begins
                else:
                    self.cur = (int(v[i]), float(t[i]), [])
            if self.cur is not None and pos < level.size:
                self.cur[2].append(t[level[pos:]])
        self.prev = v[-1]

    def _close(self, end: float) -> None:
        initial, begin, parts = self.cur
        times = np.concatenate(parts).astype("<f8") if parts else np.empty(0, "<f8")
        self.segs.append(Segment(initial=initial, begin=begin, end=end, times=times))
        self.cur = None

    def finish(self, end: float) -> list[Segment]:
        if self.cur is not None:
            self._close(end)
        return self.segs


def _rows_estimate(path: Path) -> int:
    with open(path, "rb") as f:
        sample = f.read(C.files.csv_sample_bytes)
    lines = max(sample.count(b"\n"), 1)
    return max(int(path.stat().st_size / (len(sample) / lines)), 1)


def load(path: Path, progress: Callable[[str], None] | None = None, **_) -> Capture:
    import pandas as pd

    say = progress or (lambda _m: None)
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        header = next(csv.reader(f))
    names = header[1:]
    if not names:
        raise ValueError(f"{path.name}: CSV has a time column but no channels")
    dtypes = {header[0]: "float64", **{n: "category" for n in names}}
    tracks = [_Track() for _ in names]
    est, rows, end = _rows_estimate(path), 0, None
    with pd.read_csv(path, dtype=dtypes, engine="c", encoding="utf-8-sig",
                     chunksize=C.files.csv_chunk_rows) as reader:
        for chunk in reader:
            t = chunk.iloc[:, 0].to_numpy(np.float64)
            for k, tr in enumerate(tracks):
                tr.feed(t, _levels(chunk.iloc[:, k + 1]))
            rows += len(t)
            end = float(t[-1])
            say(f"Reading CSV {min(99, 100 * rows // est)} %")
    if end is None:
        raise ValueError(f"{path.name}: CSV has a header but no rows")

    # "Channel 5" keeps index 5 so a CSV and a .sal of the same capture line up;
    # renamed channels fall back to their column position.
    parsed = [int(m.group(1)) if (m := _DEFAULT_NAME.fullmatch(n)) else None for n in names]
    use_parsed = None not in parsed and len(set(parsed)) == len(parsed)
    channels = [Channel(index=parsed[k] if use_parsed else k, name=name, segments=tr.finish(end))
                for k, (name, tr) in enumerate(zip(names, tracks))]
    cap = Capture(path=path, source="csv", channels=channels)
    cap.notes.append("CSV does not carry the sample rate: Δt resolution cannot be stated.")
    cap.notes.append("CSV ends at the last change; the true capture end is unknown.")
    return cap
