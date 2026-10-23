"""Time as text, for sentences the core writes (comparison reasons, Excel notes).

Mirrors ui/src/format.ts `measured()`: as many decimals as one sample period
resolves, 1 ns when the rate is unknown (Logic 2's CSV precision).
"""
from __future__ import annotations

import math

from app.constants import C

_UNITS = [(u["label"], u["scale"]) for u in C.units.time]      # s, ms, µs, ns


def unit_for(seconds: float) -> tuple[str, float]:
    a = abs(seconds)
    for label, scale in _UNITS:
        if a >= scale:
            return label, scale
    return _UNITS[-1]


def fmt_time(seconds: float | None, resolution: float | None = None, signed: bool = False) -> str:
    if seconds is None or not math.isfinite(seconds):
        return "—"
    res = resolution if resolution and resolution > 0 else 1e-9
    label, scale = unit_for(seconds if seconds else res)
    decimals = max(0, min(6, math.ceil(math.log10(scale / res) - 1e-9)))
    text = f"{seconds / scale:.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in ("-0", ""):
        text = "0"
    if signed and seconds > 0:
        text = "+" + text
    return f"{text} {label}"


def fmt_pct(fraction: float | None, signed: bool = False) -> str:
    if fraction is None or not math.isfinite(fraction):
        return "—"
    v = fraction * 100
    text = f"{v:.1f}".rstrip("0").rstrip(".")
    return f"{'+' if signed and v > 0 else ''}{text} %"
