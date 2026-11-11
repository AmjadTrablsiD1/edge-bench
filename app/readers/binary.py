"""Saleae Logic 2 digital binary export, versions 0 and 1.

Layout (little-endian, IEEE-754), from Saleae's published spec:

    v0:  "<SALEAE>" int32 version=0  int32 type=0
         uint32 initial_state  double begin  double end  uint64 n  double[n] times
    v1:  "<SALEAE>" int32 version=1  int32 type=0  uint64 chunk_count
         per chunk: uint32 initial_state  double sample_rate  double begin
                    double end  uint64 n  double[n] times

v0 is what Logic 8 / Pro 8 / Pro 16 write (verified on Logic 2.4.48); v1 is
the newer devices'.  Gaps between v1 chunks are missing data and stay gaps.

The edge times are memory-mapped, never read into Python lists.  A v0 header
is 44 bytes, so v0 times are not 8-byte aligned; see ``MappedTimes``.
"""
from __future__ import annotations

import re
import struct
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.capture import Capture, Channel, Segment, pulse_stats
from app.constants import C

MAGIC = C.binary.magic.encode("ascii")
_SUMMARY_LOCK = threading.Lock()
_HEAD = struct.Struct("<8sii")          # magic, version, type      -> 16 bytes
_V0 = struct.Struct("<IddQ")            # initial, begin, end, n    -> 28 bytes
_V1_COUNT = struct.Struct("<Q")         # chunk_count               ->  8 bytes
_V1_CHUNK = struct.Struct("<IdddQ")     # initial, rate, begin, end, n -> 36 bytes
_F8 = 8


class BinaryFormatError(ValueError):
    pass


@dataclass
class BinHeader:
    version: int
    sample_rate: float | None
    segments: list[tuple[int, float, float, int, int]]  # initial, begin, end, n, byte offset
    expected_size: int


def read_header(path: Path) -> BinHeader:
    """Parse the header(s) and work out the exact size the file must have.

    The size check is what catches a half-written export (Logic 2 returns from
    export_raw_data_binary before the files are complete).
    """
    size = path.stat().st_size
    with open(path, "rb") as f:
        raw = f.read(_HEAD.size)
        if len(raw) < _HEAD.size:
            raise BinaryFormatError(f"{path.name}: too short to be a Saleae binary file")
        magic, version, kind = _HEAD.unpack(raw)
        if magic != MAGIC:
            raise BinaryFormatError(f"{path.name}: not a Saleae binary export (no <SALEAE> marker)")
        if kind != C.binary.type_digital:
            raise BinaryFormatError(f"{path.name}: type {kind} is not digital")
        if version not in C.binary.supported_versions:
            raise BinaryFormatError(f"{path.name}: binary format version {version} is not supported "
                                    f"(supported: {C.binary.supported_versions})")
        segments: list[tuple[int, float, float, int, int]] = []
        rate: float | None = None
        if version == 0:
            raw = f.read(_V0.size)
            if len(raw) < _V0.size:
                raise BinaryFormatError(f"{path.name}: header truncated")
            initial, begin, end, n = _V0.unpack(raw)
            off = _HEAD.size + _V0.size
            segments.append((int(initial), begin, end, int(n), off))
            expected = off + n * _F8
        else:
            raw = f.read(_V1_COUNT.size)
            if len(raw) < _V1_COUNT.size:
                raise BinaryFormatError(f"{path.name}: header truncated")
            (count,) = _V1_COUNT.unpack(raw)
            off = _HEAD.size + _V1_COUNT.size
            for _ in range(int(count)):
                f.seek(off)
                raw = f.read(_V1_CHUNK.size)
                if len(raw) < _V1_CHUNK.size:
                    raise BinaryFormatError(f"{path.name}: chunk header truncated")
                initial, chunk_rate, begin, end, n = _V1_CHUNK.unpack(raw)
                rate = rate or (chunk_rate or None)
                off += _V1_CHUNK.size
                segments.append((int(initial), begin, end, int(n), off))
                off += int(n) * _F8
            expected = off
    if size < expected:
        raise BinaryFormatError(f"{path.name}: file is {size} bytes, header promises {expected} "
                                f"-- export incomplete or truncated")
    return BinHeader(version, rate, segments, expected)


class MappedTimes(np.memmap):
    """Memory-mapped edge times whose ``searchsorted`` never copies the file.

    numpy's searchsorted copies an unaligned array whole on every call -- a 2 GB
    channel cost 2 GB of RAM and seconds per search (found on Windows; same on
    macOS).  Unaligned times get a vectorised bisection instead, which reads
    about log2(n) values per time wanted.  Aligned ones use numpy as usual.
    Arithmetic on these returns plain arrays (np.memmap's own rule).
    """

    def searchsorted(self, v, side="left", sorter=None):
        if self.flags.aligned or sorter is not None:
            return np.ndarray.searchsorted(self, v, side=side, sorter=sorter)
        if side not in ("left", "right"):
            raise ValueError(f"side must be 'left' or 'right', not {side!r}")
        wanted = np.asarray(v, dtype=np.float64)
        flat = wanted.reshape(-1)
        lo = np.zeros(flat.shape, dtype=np.intp)
        hi = np.full(flat.shape, len(self), dtype=np.intp)
        plain = self.view(np.ndarray)
        while (live := np.flatnonzero(lo < hi)).size:
            mid = (lo[live] + hi[live]) // 2
            at = plain[mid]                      # fancy index: reads only these values
            go = at < flat[live] if side == "left" else at <= flat[live]
            lo[live[go]] = mid[go] + 1
            hi[live[~go]] = mid[~go]
        return lo.reshape(wanted.shape) if wanted.ndim else lo[0]

    _summary = None                                  # per-block pulse summary, built on first use

    def pulses(self, i0: int, i1: int):
        """capture.pulse_stats for edges i0 .. i1 - 1.

        A long span is answered from a summary of the whole channel (three
        numbers per summary_block_edges edges), built once by reading the
        file through in chunks -- after that any span, reference or tolerance
        costs nothing.  A short span is simply read.
        """
        if i1 - i0 < C.compare.summary_min_edges:
            return pulse_stats(self, i0, i1)
        with _SUMMARY_LOCK:
            if self._summary is None:
                size = int(C.compare.summary_block_edges)
                k = (len(self) - 1) // size
                odd, total, low = np.empty(k), np.empty(k), np.empty(k)
                rows = max(1, int(C.compare.span_chunk_edges) // size)
                for a in range(0, k, rows):
                    b = min(a + rows, k)
                    gaps = np.diff(np.asarray(self[a * size:b * size + 1], dtype=float)).reshape(b - a, size)
                    odd[a:b], total[a:b], low[a:b] = gaps[:, ::2].sum(axis=1), gaps.sum(axis=1), gaps.min(axis=1)
                self._summary = (np.arange(k + 1, dtype=np.int64) * size, odd, total, low)
        return pulse_stats(self, i0, i1, self._summary)


def load_segments(path: Path) -> tuple[list[Segment], BinHeader]:
    head = read_header(path)
    segs = []
    for initial, begin, end, n, off in head.segments:
        times = (MappedTimes(path, dtype="<f8", mode="r", offset=off, shape=(n,))
                 if n else np.empty(0, dtype="<f8"))
        segs.append(Segment(initial=initial & 1, begin=begin, end=end, times=times))
    return segs, head


_INDEX = re.compile(C.files.bin_index_regex, re.IGNORECASE)


def channel_index(path: Path) -> int | None:
    m = _INDEX.search(path.name)
    return int(m.group(1)) if m else None


def bin_files(folder: Path) -> list[tuple[int, Path]]:
    found = []
    for p in folder.glob(C.files.bin_glob):
        idx = channel_index(p)
        if idx is not None:
            found.append((idx, p))
    return sorted(found)


def load_folder(folder: Path, names: dict[int, str] | None = None,
                sample_rate: float | None = None, device: str | None = None,
                source_path: Path | None = None, source: str = "binary") -> Capture:
    files = bin_files(folder)
    if not files:
        raise BinaryFormatError(f"no {C.files.bin_glob} files in {folder}")
    names = names or {}
    channels, versions, rate = [], set(), sample_rate
    for idx, p in files:
        segs, head = load_segments(p)
        versions.add(head.version)
        rate = rate or head.sample_rate
        channels.append(Channel(index=idx, name=names.get(idx, f"Channel {idx}"), segments=segs))
    cap = Capture(path=source_path or folder, source=source, channels=channels,
                  sample_rate=rate, device=device,
                  format_version=max(versions) if versions else None)
    if rate is None:
        cap.notes.append("Sample rate unknown (binary v0 does not store it): "
                         "Δt resolution cannot be stated.")
    return cap


def sniff(path: Path) -> bool:
    if path.is_dir():
        return bool(bin_files(path))
    return path.suffix.lower() == ".bin"


def load(path: Path, **_) -> Capture:
    """A .bin file means its whole export folder; a folder means its digital_*.bin."""
    folder = path if path.is_dir() else path.parent
    return load_folder(folder, source_path=path)
