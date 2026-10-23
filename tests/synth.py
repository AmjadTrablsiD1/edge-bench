"""Test-only helpers: write Saleae binary files, and a deliberately naive
sample-by-sample reference implementation to check the fast engine against.

The naive side never uses searchsorted, parity, or the Segment model: it walks
an array of 0/1 samples one sample at a time.  It shares no code with app/, so
the two cannot be wrong the same way (rule 15).
"""
from __future__ import annotations

import struct
from pathlib import Path

import numpy as np


def write_v0(path: Path, initial: int, begin: float, end: float, times) -> None:
    times = np.asarray(times, dtype="<f8")
    with open(path, "wb") as f:
        f.write(b"<SALEAE>" + struct.pack("<ii", 0, 0))
        f.write(struct.pack("<IddQ", initial, begin, end, len(times)))
        f.write(times.tobytes())


def write_v1(path: Path, chunks, rate: float) -> None:
    """chunks: list of (initial, begin, end, times)."""
    with open(path, "wb") as f:
        f.write(b"<SALEAE>" + struct.pack("<ii", 1, 0) + struct.pack("<Q", len(chunks)))
        for initial, begin, end, times in chunks:
            times = np.asarray(times, dtype="<f8")
            f.write(struct.pack("<IdddQ", initial, rate, begin, end, len(times)))
            f.write(times.tobytes())


def samples_to_edges(samples: np.ndarray, rate: float) -> tuple[int, list[float]]:
    """Naive: an edge at sample k whenever samples[k] != samples[k-1]."""
    out = []
    for k in range(1, len(samples)):
        if samples[k] != samples[k - 1]:
            out.append(k / rate)
    return int(samples[0]), out


def naive_first_edge(samples, rate, t, kind="any", glitch_samples=0, skip=0):
    """Sample-domain search.  With a filter: a level counts once it has held for
    >= glitch_samples samples; the reported time is where that level began.
    Returns (time, rising) or None."""
    n = len(samples)
    k0 = int(np.ceil(t * rate - 1e-9))
    if glitch_samples <= 0:
        for k in range(max(1, k0), n):
            if samples[k] != samples[k - 1]:
                rising = samples[k] == 1
                if kind == "any" or (kind == "rising") == rising:
                    if skip == 0:
                        return k / rate, rising
                    skip -= 1
        return None
    # Deglitched sample stream: run lengths, keep runs >= glitch_samples (or the last run).
    runs = []                                   # (start, level, length)
    start = 0
    for k in range(1, n + 1):
        if k == n or samples[k] != samples[start]:
            runs.append((start, int(samples[start]), k - start))
            start = k
    stable = None
    for i, (s0, lvl, ln) in enumerate(runs):
        is_last = i == len(runs) - 1
        if stable is None:
            stable = lvl                         # the initial level is taken as stable
            continue
        if lvl != stable and (ln >= glitch_samples):
            stable = lvl
            if s0 >= k0:
                rising = lvl == 1
                if kind == "any" or (kind == "rising") == rising:
                    if skip == 0:
                        return s0 / rate, rising
                    skip -= 1
        _ = is_last
    return None


def random_samples(rng, n: int, p_toggle: float, glitch_p: float = 0.0, glitch_len=(1, 3)):
    """A random 0/1 sample stream with optional short glitches."""
    s = np.zeros(n, dtype=np.int8)
    lvl = int(rng.integers(0, 2))
    k = 0
    while k < n:
        run = int(rng.geometric(p_toggle))
        s[k:k + run] = lvl
        k += run
        if glitch_p and rng.random() < glitch_p and k < n:
            g = int(rng.integers(glitch_len[0], glitch_len[1] + 1))
            s[k:k + g] = 1 - lvl
            k += g
            if k < n:
                s[k:k + 1] = lvl          # back to the old level before toggling
                k += 1
        lvl = 1 - lvl
    return s
