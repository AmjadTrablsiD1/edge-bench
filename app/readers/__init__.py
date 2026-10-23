"""Capture formats, as a registry.  Add a format = add a module + one entry here.

Each module exposes ``sniff(path) -> bool`` and ``load(path, **kw) -> Capture``.
The first entry whose sniff() is true wins, so put the cheapest checks first.
"""
from __future__ import annotations

from pathlib import Path

from app.capture import Capture
from app.readers import binary, csv_digital, sal

REGISTRY = {
    "sal": sal,
    "binary": binary,
    "csv": csv_digital,
}


def detect(path: Path) -> str | None:
    for name, mod in REGISTRY.items():
        if mod.sniff(path):
            return name
    return None


def load(path: Path, **kw) -> Capture:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist")
    kind = detect(path)
    if kind is None:
        raise ValueError(f"{path.name}: not a Saleae capture (.sal), a Logic 2 binary export "
                         f"(digital_N.bin) or a Logic 2 digital CSV")
    return REGISTRY[kind].load(path, **kw)
