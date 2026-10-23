"""Exported binaries, so a .sal goes through Logic 2 once and never again.

Key = (absolute path, size, mtime, key version).  A capture edited or replaced
on disk gets a new key.  An entry is only valid once ``complete.json`` exists,
which is written after every file was checked against its own header -- a
crashed or half-finished export is never mistaken for a good one.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import time
from contextlib import contextmanager
from pathlib import Path

from app.constants import C, path as cpath

_MARK = "complete.json"


def root() -> Path:
    return cpath("cache_dir")


def key(src: Path) -> str:
    st = src.stat()
    ident = f"{src.resolve()}|{st.st_size}|{st.st_mtime_ns}|{C.cache.key_version}"
    return hashlib.sha1(ident.encode("utf-8")).hexdigest()[:20]


def lookup(src: Path) -> Path | None:
    d = root() / key(src)
    mark = d / _MARK
    if mark.is_file():
        mark.touch()                    # LRU: last use
        return d
    return None


@contextmanager
def slot(src: Path):
    """A temp folder to export into; promoted to the cache only on success."""
    final = root() / key(src)
    tmp = root() / f".{final.name}.partial"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        yield tmp
        (tmp / _MARK).write_text(json.dumps({"source": str(src), "at": time.time()}))
        shutil.rmtree(final, ignore_errors=True)
        tmp.rename(final)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    evict()


def size_bytes() -> int:
    return sum(p.stat().st_size for p in root().rglob("*") if p.is_file())


def evict(max_bytes: float | None = None) -> int:
    """Drop least-recently-used entries until the cache fits.  Returns entries removed."""
    limit = C.cache.max_gb * 1e9 if max_bytes is None else max_bytes
    entries = []
    for d in root().iterdir():
        mark = d / _MARK
        if d.is_dir() and mark.is_file():
            size = sum(p.stat().st_size for p in d.rglob("*") if p.is_file())
            entries.append((mark.stat().st_mtime, size, d))
    total = sum(e[1] for e in entries)
    removed = 0
    for _, size, d in sorted(entries):
        if total <= limit:
            break
        shutil.rmtree(d, ignore_errors=True)
        total -= size
        removed += 1
    return removed


def clear() -> None:
    for d in root().iterdir():
        shutil.rmtree(d, ignore_errors=True) if d.is_dir() else d.unlink(missing_ok=True)
