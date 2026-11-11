"""Read a Logic 2 .sal directly: no binary export, no Logic 2 process.

Saleae does not document this format.  It was decoded from Logic 2.4.48 files
and is checked against Logic 2's own binary export of the same captures
(tests/test_sal_native.py, scripts/make_sal_corpus.py).  Anything this module
does not recognise raises NativeFormatError, and the .sal goes through the
Logic 2 export as before -- never a guess.

Each ``digital-N.bin`` in the .sal zip (deflate-compressed):

    "<SALEAE>"  int32 version=4  int32 type=100  uint8 1
    double sample_rate  uint64 start_unix_ms  double start_fraction_ms
    opt<uint64> begin_sample   opt<uint64> end_sample    (opt = uint8 flag, value if 1)
    uint64 chunk_count
    per chunk: uint64 a  uint64 b  uint8 level  uint8 0  uint64 n_bytes  byte[n_bytes]

A chunk covers samples [a, b) with no gap to the next one.  Its payload is a
list of run lengths (samples between edges) that add up to exactly b - a --
the integrity check every chunk must pass.  `level` holds during the first
run.  Every run starts at an edge, except the first run of the first chunk
(the start of the data).  Each run length r is stored as v = r - 1:

    v < 2**6                 00vvvvvv
    else, big-endian 7-bit:  01vvvvvv  1vvvvvvv ...  0vvvvvvv    (as few bytes as fit)

A trimmed capture keeps the whole chunk around the trim point; only edges
inside [begin_sample, end_sample) belong to the capture.  A trigger capture
puts time 0 at meta.json's digitalTriggerTime, applied as a whole number of
samples so the trigger edge is exactly 0.

Times are sample / rate, correctly rounded.  Logic 2's export differs from
that by a few ULPs (~1e-19 s) on some edges -- it accumulates in floating
point; every edge is on the same sample.

Memory: the channel file is unpacked once into the export cache (about 1 to
3.5 bytes per edge on disk, against the binary export's 8) and memory-mapped.
Indexing it once sets a mark at most every C.sal_native.mark_every edges (byte
offset, sample and edge number of that edge's token), so any edge is found by
decoding at most that many tokens; decoded stretches share a cache of
decoded_cache_mb.  A billion-edge channel costs its marks (~12 MB) and what is
in view -- the way Logic 2 does it, not 8 GB of float64.

Speed: the index is the only pass over all the data, so it is built to be
cheap.  The file is cut into blocks of block_bytes that are decoded
independently on several threads (numpy releases the GIL); each block starts
its own stretch of marks, which is why the stretches are not all the same
length.  Per stretch the index also keeps two numbers -- the sum of every
second run and the shortest run -- from which Compare gets edge counts, time
high and the shortest pulse of any span without decoding it (`pulses`).
"""
from __future__ import annotations

import json
import os
import re
import struct
import threading
import zipfile
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from app.capture import Capture, Channel, Segment, pulse_stats
from app.constants import C

K = C.sal_native
_MEMBER = re.compile(K.member_regex)
_FIXED = struct.Struct("<8siiBdQd")          # magic, version, type, flag, rate, unix ms, fraction
_U8 = struct.Struct("<B")
_U64 = struct.Struct("<Q")
_CHUNK = struct.Struct("<QQBBQ")              # a, b, level, zero, n_bytes
_INDEX = "index.npz"


class NativeFormatError(ValueError):
    """This .sal is not in the layout this reader knows; use the Logic 2 export."""


class _Stopped(Exception):
    """Another channel of the same capture failed; this one stops too."""


# ------------------------------------------------------------------ header

@dataclass
class Header:
    rate: float
    begin: int | None                 # trim window, samples
    end: int | None
    chunks: int
    size: int                         # header bytes


def read_header(buf) -> Header:
    try:
        magic, version, kind, flag, rate, _ms, _frac = _FIXED.unpack_from(buf, 0)
        if magic != K.magic.encode("ascii") or version != K.version or kind != K.type or flag != K.header_flag:
            raise NativeFormatError(f"channel file is {magic!r} v{version} type {kind} flag {flag}, "
                                    f"not {K.magic} v{K.version} type {K.type}")
        if not rate > 0:
            raise NativeFormatError(f"sample rate {rate} in the channel file")
        off = _FIXED.size
        window = []
        for _ in range(2):
            (present,) = _U8.unpack_from(buf, off)
            off += 1
            if present not in (0, 1):
                raise NativeFormatError(f"optional flag {present} at byte {off - 1}")
            if present:
                window.append(_U64.unpack_from(buf, off)[0])
                off += 8
            else:
                window.append(None)
        (chunks,) = _U64.unpack_from(buf, off)
    except struct.error as exc:
        raise NativeFormatError(f"channel file header is truncated ({exc})") from exc
    return Header(rate, window[0], window[1], int(chunks), off + 8)


# ------------------------------------------------------------- run lengths

_ALT = (np.arange(1 << 20) & 1).astype(np.uint8)


def _alt(n: int) -> np.ndarray:
    """0, 1, 0, 1 ... (n of them)."""
    global _ALT
    if _ALT.size < n:
        _ALT = (np.arange(max(n, 2 * _ALT.size)) & 1).astype(np.uint8)
    return _ALT[:n]


def _tokens(p: np.ndarray, pstart: np.ndarray) -> tuple[np.ndarray | None, np.ndarray]:
    """Payload bytes -> (byte position of every token, its run length).

    `pstart`: where each piece starts in `p` (a token starts there).  The
    positions are None when every token is one byte (token i is byte i) -- the
    usual case on a fast clock, and by far the cheapest.  The runs come in the
    smallest integer type that holds them (uint8, uint16 or int64): most of
    the time here goes into moving arrays through memory, so add them up
    with dtype=np.int64.

    The grammar  00vvvvvv | 01vvvvvv 1vvvvvvv* 0vvvvvvv  is parsed without a
    loop over bytes.  A byte < 0x40 always ends a token, and so does the byte
    after continuation bytes (>= 0x80): the next byte is a token start for
    sure -- a "reset".  Between two resets the bytes are 01xxxxxx, and they
    pair up from the reset on (start, last byte, start, last byte ...), so a
    byte starts a token when it is an even number of bytes after its reset.
    Raises NativeFormatError on any byte sequence the grammar does not allow.
    """
    n = p.size
    if n == 0 or pstart[-1] >= n or (np.diff(pstart) == 0).any():
        raise NativeFormatError("a chunk with an empty payload")
    lt40 = p < 0x40
    if lt40.all():
        return None, p + np.uint8(1)
    hi = p >= 0x80
    any_hi = bool(hi.any())
    reset = np.empty(n, bool)
    reset[0] = True
    reset[1:] = lt40[:-1]
    if any_hi:
        term = np.zeros(n, bool)                          # the byte that ends continuation bytes
        np.logical_and(hi[:-1], ~hi[1:], out=term[1:])
        reset[1:] |= term[:-1]
    if pstart.size > 1:                                   # belt and braces: in a valid file the byte
        reset[pstart] = True                              # before a piece already ends a token
    start = _even_from_reset(reset)
    if any_hi:
        start &= ~hi
        start &= ~term
        first = np.flatnonzero(hi[1:] & ~hi[:-1]) + 1     # continuation bytes need a 01xxxxxx start
        if hi[0] or not (start[first - 1] & ~lt40[first - 1]).all():
            raise NativeFormatError("continuation bytes without a token start")
    last = np.append(pstart[1:], n) - 1                   # every piece ends where a token ends
    if (hi[last] | (start[last] & ~lt40[last])).any():
        raise NativeFormatError("a run-length token is cut off at the end of a chunk")

    s = np.flatnonzero(start)
    b = p[s]
    multi = np.flatnonzero(b >= 0x40)
    at = s[multi] + 1
    nb = p[at]
    if not any_hi:                                        # one or two bytes: a run is at most 2**13
        v = b.astype(np.uint16)
        v[multi] = ((b[multi] & np.uint8(0x3F)).astype(np.uint16) << np.uint16(7)) | nb
        return s, v + np.uint16(1)
    v = b.astype(np.int64)
    acc = ((b[multi] & 0x3F).astype(np.int64) << 7) | (nb & 0x7F)
    cur = np.flatnonzero(nb >= 0x80)                      # tokens of three bytes and more: rare
    at, size = at[cur] + 1, 2
    while cur.size:
        size += 1
        if size > K.max_token_bytes:
            raise NativeFormatError(f"a token longer than {K.max_token_bytes} bytes")
        nb = p[at]
        acc[cur] = (acc[cur] << 7) | (nb & 0x7F)
        go = nb >= 0x80
        cur, at = cur[go], at[go] + 1
    v[multi] = acc
    return s, v + 1


def _even_from_reset(reset: np.ndarray) -> np.ndarray:
    """True at every byte an even number of bytes after the last True of `reset` (changed in place).

    Most bytes are resets or one byte after one, so only the stretches of three
    bytes and more need work: a few rounds add their bytes 2, 4, 6 ... on; what
    is still longer after that (a steady clock with two-byte runs is one long
    stretch) is filled by slices, or all at once when there are very many.
    """
    n = reset.size
    start = reset
    at = np.flatnonzero(reset[:-2] & ~reset[1:-1] & ~reset[2:]) + 2 if n > 2 else np.zeros(0, np.int64)
    known = start.copy() if at.size else start             # `reset` as it was: `start` grows below
    for _ in range(int(K.pair_rounds)):
        if not at.size:
            return start
        start[at] = True
        at = at[at + 2 < n] + 2
        at = at[~known[at - 1] & ~known[at]]
    if not at.size:
        return start
    r = np.flatnonzero(known)
    if at.size > K.pair_slices:
        return np.repeat((r & 1).astype(np.uint8), np.diff(r, append=n)) == _alt(n)
    ends = np.append(r, n)[np.searchsorted(r, at, side="right")]
    for a, e in zip(at.tolist(), ends.tolist()):
        start[a:e:2] = True
    return start


# ----------------------------------------------------------------- pieces

def _safe_split(p: np.ndarray, lo: int, hi: int) -> int | None:
    """A byte position in [lo, hi) where a token starts for sure: right after a byte < 0x40."""
    lo = max(lo, 1)
    if hi <= lo:
        return None
    hits = np.flatnonzero(np.asarray(p[lo - 1:hi - 1]) < 0x40)
    return int(lo + hits[0]) if hits.size else None


def _pieces(buf: np.ndarray, off: int, nb: int) -> list[tuple[int, int]]:
    """Split one chunk payload into pieces of about piece_bytes, at token starts."""
    if nb <= K.piece_bytes:
        return [(off, nb)]
    out, start, end = [], off, off + nb
    while end - start > K.piece_bytes:
        cut = _safe_split(buf, start + K.piece_bytes, min(end, start + K.piece_bytes + K.split_window_bytes))
        if cut is None:
            break
        out.append((start, cut - start))
        start = cut
    out.append((start, end - start))
    return out


def _gather(buf: np.ndarray, off: np.ndarray, ln: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The payload bytes of these pieces as one array, and where each piece starts in it."""
    pstart = np.cumsum(ln) - ln
    lo, hi = int(off[0]), int(off[-1] + ln[-1])
    total = int(pstart[-1] + ln[-1])
    region = np.asarray(buf[lo:hi])
    if hi - lo == total:                                  # one piece, or pieces of one chunk
        return region, pstart
    if off.size <= 32:
        return np.concatenate([region[o:o + n] for o, n in zip((off - lo).tolist(), ln.tolist())]), pstart
    return region[np.repeat(off - lo - pstart, ln) + np.arange(total)], pstart      # skip the chunk headers


@dataclass
class Index:
    """Everything needed to find and decode any edge of one channel.

    "Edge d" counts every edge in the file; the capture's own edges are
    d = skip .. skip + count - 1 (a trimmed capture keeps whole chunks).
    Stretch m is the edges m_edge[m] .. m_edge[m+1] - 1, at most mark_every.
    """
    rate: float
    level0: int                  # level at the start of the data window
    first: int                   # first and last sample of the window
    last: int
    skip: int                    # edges in the file before the window
    count: int                   # edges inside the window
    total: int                   # edges in the file
    p_off: np.ndarray            # per piece: payload byte offset and length
    p_len: np.ndarray
    m_off: np.ndarray            # per mark: byte offset of its edge's token, its sample, its
    m_sample: np.ndarray         #   piece and its edge number -- decoding can start at any of them
    m_piece: np.ndarray
    m_edge: np.ndarray
    m_even: np.ndarray           # per stretch: samples in its 1st, 3rd, 5th ... run, and its
    m_min: np.ndarray            #   shortest run (a run = from one edge to the next)

    def save(self, path: Path) -> None:
        np.savez(path, **{k: np.asarray(v) for k, v in self.__dict__.items()})

    @classmethod
    def load(cls, path: Path) -> "Index":
        with np.load(path) as z:
            d = {k: z[k] for k in z.files}
        d["rate"] = float(d["rate"])
        for k in ("level0", "first", "last", "skip", "count", "total"):
            d[k] = int(d[k])
        return cls(**d)


def _span_pieces(ix: Index, m0: int, m1: int) -> tuple[list[int], list[int]]:
    """Payload pieces (offset, length) that hold stretches m0 .. m1 - 1."""
    j0, o0 = int(ix.m_piece[m0]), int(ix.m_off[m0])
    if m1 < ix.m_off.size:
        j1, o1 = int(ix.m_piece[m1]), int(ix.m_off[m1])
    else:
        j1 = ix.p_off.size - 1
        o1 = int(ix.p_off[j1] + ix.p_len[j1])
    if j0 == j1:
        return [o0], [o1 - o0]
    off = [o0, *ix.p_off[j0 + 1:j1 + 1].tolist()]
    ln = [int(ix.p_off[j0] + ix.p_len[j0]) - o0, *ix.p_len[j0 + 1:j1].tolist(), o1 - int(ix.p_off[j1])]
    if ln[-1] == 0:
        off, ln = off[:-1], ln[:-1]
    return off, ln


def decode_samples(buf: np.ndarray, ix: Index, spans: list[tuple[int, int]]) -> list[np.ndarray]:
    """Edge samples of several stretch ranges [m0, m1), decoded in one vectorised pass.

    Chunks follow each other without a gap and their runs add up (both checked
    when the index was built), so inside a range the edges are simply the
    running sum of the runs from the mark's own sample.
    """
    off, ln, firsts = [], [], []
    for m0, m1 in spans:
        o, n = _span_pieces(ix, m0, m1)
        firsts.append(len(off))
        off += o
        ln += n
    p, pstart = _gather(buf, np.array(off, np.int64), np.array(ln, np.int64))
    s, runs = _tokens(p, pstart)
    at = pstart[firsts] if s is None else np.searchsorted(s, pstart[firsts])
    at = [*at.tolist(), runs.size]
    cs = np.cumsum(runs, dtype=np.int64)
    out = []
    for (m0, m1), a, b in zip(spans, at[:-1], at[1:]):
        want = (int(ix.m_edge[m1]) if m1 < ix.m_edge.size else ix.total) - int(ix.m_edge[m0])
        if b - a != want:
            raise NativeFormatError(f"decoded {b - a} edges between marks, expected {want}")
        out.append(cs[a:b] - runs[a:b] + (int(ix.m_sample[m0]) - (int(cs[a - 1]) if a else 0)))
    return out


def _edges_before(buf: np.ndarray, ix: Index, sample: int, side: str) -> int:
    """np.searchsorted over every edge sample of the file, decoding one stretch."""
    m = int(np.searchsorted(ix.m_sample, sample, side=side)) - 1
    if m < 0:
        return 0
    return int(ix.m_edge[m]) + int(np.searchsorted(decode_samples(buf, ix, [(m, m + 1)])[0], sample, side=side))


def _index_block(p: np.ndarray, pstart: np.ndarray, off: np.ndarray, first: bool, every: int):
    """One block, on its own: its pieces' sums, and a mark every `every` edges from its start.

    `p`: the payload bytes of the block's pieces, `pstart` where each piece
    starts in it, `off` where each piece is in the channel file.  Nothing
    here depends on the blocks before it, so blocks run in parallel; the
    indexer adds the sample and the edge number each block starts at.
    """
    s, runs = _tokens(p, pstart)
    if off.size == 1:
        sums = np.array([runs.sum(dtype=np.int64)], np.int64)
    else:
        sums = np.add.reduceat(runs, pstart if s is None else np.searchsorted(s, pstart), dtype=np.int64)
    j0 = 1 if first else 0                                # the start of the data is not an edge
    body = runs[j0:]
    full = body.size // every
    rows = body[:full * every].reshape(full, every)
    st_sum = rows.sum(axis=1, dtype=np.int64)
    st_even = rows[:, ::2].sum(axis=1, dtype=np.int64)
    st_min = rows.min(axis=1).astype(np.int64) if full else np.zeros(0, np.int64)
    rest = body[full * every:]
    if rest.size:
        st_sum = np.append(st_sum, rest.sum(dtype=np.int64))
        st_even = np.append(st_even, rest[::2].sum(dtype=np.int64))
        st_min = np.append(st_min, np.int64(rest.min()))
    tok = j0 + np.arange(st_sum.size, dtype=np.int64) * every          # the marks' tokens
    rel = (int(runs[0]) if first else 0) + np.cumsum(st_sum) - st_sum    # their samples, from the block start
    pos = tok if s is None else s[tok]
    piece = np.searchsorted(pstart, pos, side="right") - 1
    return runs.size, sums, tok, rel, off[piece] + pos - pstart[piece], piece, st_even, st_min


class Indexer:
    """Index a channel file as its bytes arrive -- while it is still being unpacked.

    `feed` the file in order, in blocks of any size; `finish` returns the
    Index (without the trim window: see `build_index`).  Complete chunks are
    cut into pieces, pieces are collected into blocks of block_bytes, and
    each block is decoded on the pool; results are put together in file
    order.  At most a few blocks per worker wait at any time, so memory does
    not follow the file size, and feeding slows down to what the pool can do.
    """

    def __init__(self, pool: ThreadPoolExecutor | None = None,
                 done: Callable[[int], None] = lambda _n: None) -> None:
        self.pool, self.done = pool, done
        self.buf, self.pos, self.at = b"", 0, 0           # unread bytes, read position, file offset of buf[0]
        self.h: Header | None = None
        self.level_first = 0
        self.chunk = 0                                    # chunks read so far
        self.next_sample: int | None = None               # where the next chunk must start
        self.ch_len: list[int] = []
        self.block: list[tuple[bytes, int, int]] = []     # pieces waiting: (payload, file offset, chunk)
        self.block_bytes = 0
        self.first_block = True
        self.waiting: list = []                           # blocks being decoded, in file order
        self.limit = 4 * workers() if pool is not None else 1
        self.p_off: list[int] = []
        self.p_len: list[int] = []
        self.tokens = 0
        self.sample = 0
        self.sum_chunk, self.sum_so_far = 0, 0            # the chunk whose runs are being added up
        self.marks: list[tuple] = []

    # -- reading ----------------------------------------------------------
    def feed(self, data: bytes) -> None:
        self.at += self.pos
        self.buf = self.buf[self.pos:] + data
        self.pos = 0
        if self.h is None:
            if len(self.buf) < K.header_max_bytes:
                return                                    # finish() reads a shorter file's header
            self._header()
        self._chunks()

    def _header(self) -> None:
        self.h = read_header(self.buf)
        self.pos = self.h.size
        if self.h.chunks == 0:
            raise NativeFormatError("no chunks")

    def _chunks(self) -> None:
        buf, size = self.buf, _CHUNK.size
        while self.chunk < self.h.chunks and len(buf) - self.pos >= size:
            a, b, level, zero, nb = _CHUNK.unpack_from(buf, self.pos)
            if zero != 0 or level > 1 or b <= a:
                raise NativeFormatError(f"chunk header ({a}, {b}, {level}, {zero}) not understood")
            if nb == 0:
                raise NativeFormatError("a chunk with an empty payload")
            if nb > K.chunk_max_bytes:
                raise NativeFormatError(f"a chunk of {nb} bytes")
            if len(buf) - self.pos < size + nb:
                return                                    # the rest of this chunk is still to come
            if self.chunk == 0:
                self.level_first, self.sample = level, a
            elif a != self.next_sample:
                raise NativeFormatError(f"gap between chunks ({self.next_sample} -> {a})")
            self.next_sample = b
            self.ch_len.append(b - a)
            start = self.pos + size
            payload = np.frombuffer(buf, np.uint8, nb, start)
            for o, n in _pieces(payload, 0, nb):          # big chunks are cut at token starts
                self.block.append((buf[start + o:start + o + n], self.at + start + o, self.chunk))
                self.block_bytes += n
                if self.block_bytes >= K.block_bytes:
                    self._submit()
            self.pos = start + nb
            self.chunk += 1

    # -- blocks -------------------------------------------------------------
    def _submit(self) -> None:
        pieces, self.block, self.block_bytes = self.block, [], 0
        ln = np.array([len(q[0]) for q in pieces], np.int64)
        off = np.array([q[1] for q in pieces], np.int64)
        p = np.frombuffer(pieces[0][0] if len(pieces) == 1 else b"".join(q[0] for q in pieces), np.uint8)
        args = (p, np.cumsum(ln) - ln, off, self.first_block, int(K.mark_every))
        self.first_block = False
        job = self.pool.submit(_index_block, *args) if self.pool is not None else None
        self.waiting.append((job, args, off, ln, [q[2] for q in pieces]))
        while len(self.waiting) >= self.limit:
            self._collect()

    def _collect(self) -> None:
        job, args, off, ln, chunks = self.waiting.pop(0)
        n, sums, tok, rel, at, piece, even, low = job.result() if job is not None else _index_block(*args)
        for c, add in zip(chunks, sums.tolist()):         # every chunk's runs must add up to its length
            if c != self.sum_chunk:
                self._check_chunk()
                self.sum_chunk, self.sum_so_far = c, 0
            self.sum_so_far += add
        first_piece = len(self.p_off)
        self.p_off += off.tolist()
        self.p_len += ln.tolist()
        self.marks.append((at, self.sample + rel, first_piece + piece, self.tokens + tok - 1, even, low))
        self.tokens += n                                  # token t starts edge t - 1
        self.sample += int(sums.sum())
        self.done(int(ln.sum()))

    def _check_chunk(self) -> None:
        want = self.ch_len[self.sum_chunk]
        if self.sum_so_far != want:
            raise NativeFormatError(f"chunk {self.sum_chunk}: runs add up to {self.sum_so_far} samples, not {want}")

    def finish(self) -> Index:
        if self.h is None:
            self._header()
            self._chunks()
        if self.chunk < self.h.chunks:
            raise NativeFormatError(f"chunk headers run past the end of the file (chunk {self.chunk} of {self.h.chunks})")
        if len(self.buf) != self.pos:
            raise NativeFormatError(f"{len(self.buf) - self.pos} bytes after the last chunk")
        if self.block:
            self._submit()
        while self.waiting:
            self._collect()
        self._check_chunk()
        cat = lambda i: np.concatenate([m[i] for m in self.marks]).astype(np.int64)      # noqa: E731
        first = self.h.begin if self.h.begin is not None else self.next_sample - sum(self.ch_len)
        last = self.h.end if self.h.end is not None else self.next_sample
        return Index(rate=self.h.rate, level0=self.level_first, first=int(first), last=int(last), skip=0,
                     count=self.tokens - 1, total=self.tokens - 1,
                     p_off=np.array(self.p_off, np.int64), p_len=np.array(self.p_len, np.int64),
                     m_off=cat(0), m_sample=cat(1), m_piece=cat(2), m_edge=cat(3), m_even=cat(4), m_min=cat(5))


def set_window(raw: Path, ix: Index) -> Index:
    """The trim window: edges at or before its start set the level, edges from its end on are dropped."""
    buf = np.memmap(raw, dtype=np.uint8, mode="r")
    skip = _edges_before(buf, ix, ix.first, "right")
    ix.count = _edges_before(buf, ix, ix.last, "left") - skip
    ix.skip, ix.level0 = skip, ix.level0 ^ (skip & 1)
    return ix


def build_index(raw: Path, done: Callable[[int], None] = lambda _n: None,
                pool: ThreadPoolExecutor | None = None) -> Index:
    """Index an unpacked channel file.  `done(n)`: n more payload bytes are indexed."""
    indexer = Indexer(pool, done)
    with open(raw, "rb") as f:
        while block := f.read(K.copy_chunk_bytes):
            indexer.feed(block)
    return set_window(raw, indexer.finish())


# ----------------------------------------------------------- lazy times

class _Decoded:
    """Decoded stretches shared by every channel, bounded in bytes (LRU)."""

    def __init__(self, budget: int) -> None:
        self.budget, self.used = budget, 0
        self.items: OrderedDict[tuple, np.ndarray] = OrderedDict()
        self.lock = threading.Lock()

    def peek(self, key: tuple) -> np.ndarray | None:
        with self.lock:
            hit = self.items.get(key)
            if hit is not None:
                self.items.move_to_end(key)
            return hit

    def get(self, key: tuple, make: Callable[[], np.ndarray]) -> np.ndarray:
        with self.lock:
            hit = self.items.get(key)
            if hit is not None:
                self.items.move_to_end(key)
                return hit
        arr = make()
        with self.lock:
            if key not in self.items:
                self.items[key] = arr
                self.used += arr.nbytes
            while self.used > self.budget and len(self.items) > 1:
                _, old = self.items.popitem(last=False)
                self.used -= old.nbytes
        return arr


DECODED = _Decoded(int(K.decoded_cache_mb * 2**20))


class LazyTimes:
    """Edge times of one channel, decoded on demand from the nearest mark.

    Behaves like the sorted float64 array the rest of the app uses: len,
    shape, [i], [a:b], searchsorted.  Finding any edge decodes at most
    mark_every tokens, however many edges the channel holds.
    """

    def __init__(self, raw: Path, index: Index, shift: int = 0) -> None:
        self.raw, self.ix, self.shift = raw, index, shift          # shift: samples before t = 0
        self._buf: np.memmap | None = None
        self._edge = np.append(index.m_edge, index.total)          # edge number at each mark, then the end
        self._mark_t = (index.m_sample - shift) / index.rate
        self._blocks = None
        self.shape = (index.count,)
        self.dtype = np.dtype(np.float64)
        self.ndim = 1

    def __len__(self) -> int:
        return self.ix.count

    def _stretch(self, d: int) -> int:
        return int(np.searchsorted(self._edge, d, side="right")) - 1

    def _decode_spans(self, spans: list[tuple[int, int]]) -> list[np.ndarray]:
        """Times for several [m0, m1) stretch ranges."""
        if self._buf is None:
            self._buf = np.memmap(self.raw, dtype=np.uint8, mode="r")
        return [(s - self.shift) / self.ix.rate for s in decode_samples(self._buf, self.ix, spans)]

    def _marks(self, ms) -> dict[int, np.ndarray]:
        """Decoded stretches for these marks: cached ones reused, the rest decoded together."""
        out, todo = {}, []
        for m in ms:
            hit = DECODED.peek((str(self.raw), m))
            if hit is None:
                todo.append(m)
            else:
                out[m] = hit
        if todo:
            for m, arr in zip(todo, self._decode_spans([(m, m + 1) for m in todo])):
                out[m] = DECODED.get((str(self.raw), m), lambda arr=arr: arr)
        return out

    def _mark(self, m: int) -> np.ndarray:
        return self._marks([m])[m]

    def __getitem__(self, key):
        n, skip = self.ix.count, self.ix.skip
        if isinstance(key, slice):
            start, stop, step = key.indices(n)
            if step != 1:
                raise IndexError("LazyTimes supports contiguous slices only")
            if stop <= start:
                return np.empty(0, np.float64)
            m0, m1 = self._stretch(start + skip), self._stretch(stop - 1 + skip) + 1
            if m1 - m0 <= K.cache_span_marks:
                got = self._marks(range(m0, m1))
                arr = np.concatenate([got[m] for m in range(m0, m1)])
            else:                                     # a long stretch: in batches, not cached
                step = int(K.batch_marks)
                arr = np.concatenate([self._decode_spans([(m, min(m + step, m1))])[0]
                                      for m in range(m0, m1, step)])
            a = start + skip - int(self._edge[m0])
            return arr[a:a + stop - start].copy()
        i = int(key)
        if i < 0:
            i += n
        if not 0 <= i < n:
            raise IndexError(f"edge {key} of {n}")
        m = self._stretch(i + skip)
        return self._mark(m)[i + skip - int(self._edge[m])]

    def marks_in(self, grid: np.ndarray) -> np.ndarray:
        """Marks strictly inside each (grid[j], grid[j+1]): every one is an edge there.

        Lets the waveform mark a pixel busy without decoding anything (the UI
        fills a pixel with any edge in it), so a zoomed-out view of a busy
        channel costs nothing.  Strict bounds keep out edges before or after
        a trim window, which sit on or beyond its ends.
        """
        lo = np.searchsorted(self._mark_t, grid[:-1], side="right")
        hi = np.searchsorted(self._mark_t, grid[1:], side="left")
        return np.maximum(hi - lo, 0)

    def searchsorted(self, v, side: str = "left"):
        scalar = np.ndim(v) == 0
        vals = np.atleast_1d(np.asarray(v, dtype=np.float64))
        out = np.zeros(vals.size, np.int64)
        if self.ix.total:
            marks = np.searchsorted(self._mark_t, vals, side=side) - 1      # -1: before every edge
            order = np.argsort(marks, kind="stable")
            need, starts = np.unique(marks[order], return_index=True)
            ends = [*starts[1:].tolist(), order.size]
            got = {}
            wanted = need[need >= 0].tolist()
            for i in range(0, len(wanted), int(K.batch_marks)):
                got.update(self._marks(wanted[i:i + int(K.batch_marks)]))
            for m, a, b in zip(need.tolist(), starts.tolist(), ends):
                if m >= 0:
                    sel = order[a:b]
                    out[sel] = int(self._edge[m]) + np.searchsorted(got[m], vals[sel], side=side)
        out = np.clip(out - self.ix.skip, 0, self.ix.count)
        return int(out[0]) if scalar else out

    def pulses(self, i0: int, i1: int) -> tuple[float, float, float | None]:
        """capture.pulse_stats for edges i0 .. i1 - 1, from the index: whole
        stretches cost nothing, only the two ends are decoded."""
        if self._blocks is None:
            ix = self.ix
            self._blocks = (self._edge - ix.skip, ix.m_even / ix.rate,
                            np.diff(ix.m_sample, append=ix.m_sample[-1:]) / ix.rate, ix.m_min / ix.rate)
        return pulse_stats(self, i0, i1, self._blocks)

    def __array__(self, dtype=None, copy=None):
        arr = self[0:self.ix.count]
        return arr.astype(dtype) if dtype is not None else arr


# -------------------------------------------------------------- the .sal

def members(z: zipfile.ZipFile) -> dict[int, zipfile.ZipInfo]:
    out = {}
    for info in z.infolist():
        m = _MEMBER.match(info.filename)
        if m:
            out[int(m.group(1))] = info
    return out


def time_shift(meta_data: dict) -> float:
    """Seconds to subtract so that t = 0 is where Logic 2 puts it."""
    t0 = ((meta_data.get("timeManager") or {}).get("t0") or {}).get("type")
    if t0 in (None, "startOfCapture"):
        return 0.0
    if t0 == "trigger":
        trig = meta_data.get("digitalTriggerTime")
        if isinstance(trig, (int, float)) and trig >= 0:
            return float(trig)
    raise NativeFormatError(f"time origin {t0!r} is not known to this reader")


def workers() -> int:
    """Threads for unpacking and indexing: the PC's cores, up to max_workers."""
    return max(1, min(int(K.max_workers), os.cpu_count() or 1))


def unpack(sal: Path, dest: Path, wanted: list[int] | None,
           say: Callable[[str], None] = lambda _m: None) -> None:
    """Inflate each channel file once and index it (constant memory).

    Channels are inflated side by side (zlib releases the GIL) and every
    channel's index blocks go to one shared pool, so a PC's cores are used
    whether the capture has one busy channel or sixteen.  A channel is indexed
    while it is being inflated, so opening takes about as long as the
    inflating alone.
    """
    try:
        z = zipfile.ZipFile(sal)
    except zipfile.BadZipFile as exc:
        raise NativeFormatError(f"not a zip ({exc})") from exc
    with z:
        found = members(z)
        chans = sorted(found) if not wanted else [c for c in wanted if c in found]
        if not chans:
            raise NativeFormatError("no digital channel files in the .sal")
        meta = json.loads(z.read("meta.json")).get("data") or {}
    shift = time_shift(meta)
    glitch = ((meta.get("legacySettings") or meta.get("settings") or {}).get("glitchFilter") or {})
    if glitch.get("enabled"):
        raise NativeFormatError("Logic 2's glitch filter is on for this capture")

    total = 2 * sum(found[c].file_size for c in chans)        # every byte is inflated, then indexed
    lock = threading.Lock()
    state = {"done": 0, "shown": -1}

    stop = threading.Event()                                  # one channel failed: the others give up

    def progress(n: int) -> None:
        if stop.is_set():
            raise _Stopped
        with lock:
            state["done"] += n
            pct = 100 * state["done"] // max(total, 1)
            if pct == state["shown"]:
                return
            state["shown"] = pct
        say(f"Reading {pct} %  ({total / 2e6:,.0f} MB)")       # also where a cancelled load stops

    n = workers()
    blocks = ThreadPoolExecutor(n, thread_name_prefix="sal-index")

    def one(c: int) -> None:
        out = dest / f"digital-{c}.raw"
        indexer = Indexer(blocks, progress)
        with zipfile.ZipFile(sal) as own, own.open(found[c]) as src, open(out, "wb") as dst:
            while True:
                block = src.read(K.copy_chunk_bytes)
                if not block:
                    break
                dst.write(block)
                progress(len(block))
                indexer.feed(block)                           # indexed as it arrives, on the pool
        ix = indexer.finish()
        progress(found[c].file_size - sum(ix.p_len.tolist()))      # headers: not payload, but counted
        set_window(out, ix).save(dest / f"digital-{c}.{_INDEX}")

    failed: list[BaseException] = []

    def guarded(c: int) -> None:
        try:
            one(c)
        except _Stopped:
            pass
        except BaseException as exc:                          # unknown layout, cancelled, disk full ...
            failed.append(exc)
            stop.set()

    try:
        with ThreadPoolExecutor(min(n, len(chans)), thread_name_prefix="sal-unpack") as pool:
            list(pool.map(guarded, chans))
    finally:
        blocks.shutdown(wait=True, cancel_futures=True)
    if failed:
        raise failed[0]
    (dest / "native.json").write_text(json.dumps({"channels": chans, "shift_s": shift}), encoding="utf-8")


def open_folder(folder: Path) -> tuple[dict[int, tuple[Segment, Index]], float]:
    info = json.loads((folder / "native.json").read_text(encoding="utf-8"))
    out = {}
    for c in info["channels"]:
        ix = Index.load(folder / f"digital-{c}.{_INDEX}")
        shift = int(round(info["shift_s"] * ix.rate))
        times = LazyTimes(folder / f"digital-{c}.raw", ix, shift)
        seg = Segment(initial=ix.level0, begin=(ix.first - shift) / ix.rate,
                      end=(ix.last - shift) / ix.rate, times=times)
        out[c] = (seg, ix)
    return out, info["shift_s"]


def load_folder(folder: Path, names: dict[int, str], device: str | None, source_path: Path) -> Capture:
    segs, _shift = open_folder(folder)
    rate = next(iter(segs.values()))[1].rate
    channels = [Channel(index=c, name=names.get(c, f"Channel {c}"), segments=[seg])
                for c, (seg, _ix) in sorted(segs.items())]
    cap = Capture(path=source_path, source="sal", channels=channels, sample_rate=rate,
                  device=device, format_version=K.version)
    cap.notes.append("Read directly from the .sal (no Logic 2 export).")
    return cap
