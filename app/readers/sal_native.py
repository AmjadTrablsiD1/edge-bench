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
Indexing it once sets a mark every C.sal_native.mark_every edges (byte offset
and sample of that edge's token), so any edge is found by decoding at most
that many tokens; decoded stretches share a cache of decoded_cache_mb.  A
billion-edge channel costs its marks (~4 MB) and what is in view -- the way
Logic 2 does it, not 8 GB of float64.
"""
from __future__ import annotations

import json
import re
import struct
import threading
import zipfile
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from app.capture import Capture, Channel, Segment
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

def token_starts(p: np.ndarray, reset: np.ndarray) -> np.ndarray:
    """Which bytes start a run-length token.  `reset[i]`: a token starts at i.

    Vectorised parse of  00vvvvvv | 01vvvvvv 1vvvvvvv* 0vvvvvvv.  Bytes >= 0x80
    only continue a token; the byte after them ends it.  Of the remaining
    ("free") bytes, a stretch of 01xxxxxx always starts at a token start and
    pairs up two by two; when it is odd, its last byte takes the next byte
    with it (continuation bytes, or one 00xxxxxx as the end of a 2-byte token).
    Raises NativeFormatError on any byte sequence that grammar does not allow.
    """
    n = p.size
    if n == 0:
        return np.zeros(0, bool)
    hi = p >= 0x80
    linked = ~reset                                   # byte i continues from byte i - 1
    prev_hi = np.zeros(n, bool)
    prev_hi[1:] = hi[:-1]
    prev_hi &= linked
    term = ~hi & prev_hi                              # the byte that ends continuation bytes
    free = ~hi & ~term
    l1 = free & (p >= 0x40)
    l0 = free & (p < 0x40)

    prev_l1 = np.zeros(n, bool)
    prev_l1[1:] = l1[:-1]
    prev_l1 &= linked
    idx = np.arange(n, dtype=np.int64)
    run_first = np.maximum.accumulate(np.where(l1 & ~prev_l1, idx, 0))
    even = l1 & ((idx - run_first) % 2 == 0)          # first of a pair, or a long token's start
    next_l1 = np.zeros(n, bool)
    next_l1[:-1] = l1[1:] & linked[1:]
    run_last = l1 & ~next_l1

    takes_next = np.flatnonzero(run_last & even)      # odd stretch: its last byte needs a partner
    nxt = takes_next + 1
    if nxt.size and (nxt[-1] >= n or reset[nxt[nxt < n]].any()):
        raise NativeFormatError("a run-length token is cut off at the end of a chunk")
    consumed = np.zeros(n, bool)
    consumed[nxt[~hi[nxt]]] = True                    # a 00xxxxxx closing a 2-byte token

    # continuation bytes must follow a token start and end inside the chunk
    first_hi = np.flatnonzero(hi & ~prev_hi)
    if first_hi.size:
        before = first_hi - 1
        if before[0] < 0 or (~linked[first_hi]).any() or not (run_last[before] & even[before]).all():
            raise NativeFormatError("continuation bytes without a token start")
    last_hi = np.flatnonzero(hi & ~np.append(hi[1:] & linked[1:], False))
    if last_hi.size and (last_hi[-1] + 1 >= n or reset[last_hi + 1].any()):
        raise NativeFormatError("a run-length token is cut off at the end of a chunk")
    return even | (l0 & ~consumed)


def token_values(p: np.ndarray, starts: np.ndarray) -> np.ndarray:
    """The value v of every token (run length - 1), uint64."""
    s = np.flatnonzero(starts)
    if s.size == 0:
        return np.zeros(0, np.uint64)
    if s[0] != 0:
        raise NativeFormatError("payload does not start with a token")
    lens = np.diff(np.append(s, p.size))
    if lens.max() > K.max_token_bytes:
        raise NativeFormatError(f"a {lens.max()}-byte token (at most {K.max_token_bytes} expected)")
    tok = np.repeat(np.arange(s.size), lens)
    k = np.arange(p.size) - s[tok]
    bits = np.where(k == 0, p & 0x3F, p & 0x7F).astype(np.uint64)
    shift = (7 * (lens[tok] - 1 - k)).astype(np.uint64)
    return np.add.reduceat(bits << shift, s)


# ----------------------------------------------------------------- pieces

def _safe_split(p: np.ndarray, lo: int, hi: int) -> int | None:
    """A byte position in [lo, hi) where a token must start, or None.

    i starts a token when byte i-1 closes one for sure: it is < 0x40 and byte
    i-2 is not 01xxxxxx (a lone 00xxxxxx, or the end of continuation bytes).
    """
    lo = max(lo, 2)
    if hi <= lo:
        return None
    w = p[lo - 2:hi]
    ok = (w[1:-1] < 0x40) & ((w[:-2] < 0x40) | (w[:-2] >= 0x80))
    hits = np.flatnonzero(ok)
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


@dataclass
class Index:
    """Everything needed to find and decode any edge of one channel."""
    rate: float
    level0: int                  # level at the start of the data window
    first: int                   # first and last sample of the window
    last: int
    skip: int                    # decoded edges before the window
    count: int                   # edges inside the window
    total: int                   # edges decoded from the file
    p_off: np.ndarray            # per piece: payload byte offset, length, sample at its first
    p_len: np.ndarray            #   run's start, and whether that run start is not an edge
    p_base: np.ndarray           #   (only the data start)
    p_nofirst: np.ndarray
    m_off: np.ndarray            # every mark_every-th edge: byte offset of its token, its
    m_sample: np.ndarray         #   sample, and its piece -- decoding can start at any of them
    m_piece: np.ndarray

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


def _runs(buf: np.ndarray, off: np.ndarray, ln: np.ndarray):
    """Pieces -> (runs, each token's piece, tokens per piece, each piece's first token, token byte offsets)."""
    off, ln = np.asarray(off, np.int64), np.asarray(ln, np.int64)
    total = int(ln.sum())
    pstart = np.cumsum(ln) - ln
    if off.size == 1:
        p = np.asarray(buf[int(off[0]):int(off[0]) + total])
    else:                                              # gather the payloads, skipping chunk headers
        lo, hi = int(off[0]), int(off[-1] + ln[-1])
        p = np.asarray(buf[lo:hi])[np.repeat(off - lo - pstart, ln) + np.arange(total)]
    reset = np.zeros(total, bool)
    reset[pstart] = True
    starts = token_starts(p, reset)
    runs = token_values(p, starts).astype(np.int64) + 1
    s = np.flatnonzero(starts)
    tok_piece = np.repeat(np.arange(off.size), ln)[s]
    per_piece = np.bincount(tok_piece, minlength=off.size)
    if (per_piece == 0).any():
        raise NativeFormatError("a chunk with an empty payload")
    first_tok = np.cumsum(per_piece) - per_piece
    tok_off = off[tok_piece] + (s - pstart[tok_piece])
    return runs, tok_piece, per_piece, first_tok, tok_off


def _positions(runs, tok_piece, first_tok, base, nofirst) -> tuple[np.ndarray, np.ndarray]:
    """(edge samples, which tokens are edges): every run starts at an edge but the data start."""
    cs = np.cumsum(runs)
    before = np.where(first_tok > 0, cs[np.maximum(first_tok - 1, 0)], 0)
    run_start = base[tok_piece] + (cs - runs - before[tok_piece])
    keep = np.ones(runs.size, bool)
    keep[first_tok[np.asarray(nofirst, bool)]] = False
    return run_start[keep], keep


def build_index(raw: Path, say: Callable[[str], None] = lambda _m: None) -> Index:
    buf = np.memmap(raw, dtype=np.uint8, mode="r")
    h = read_header(bytes(buf[:128]))
    # walk the chunk headers (dozens per second of capture), straight into arrays
    if h.chunks == 0:
        raise NativeFormatError("no chunks")
    ch_a = np.empty(h.chunks, np.int64)
    ch_b = np.empty(h.chunks, np.int64)
    ch_off = np.empty(h.chunks, np.int64)
    ch_nb = np.empty(h.chunks, np.int64)
    off = h.size
    mv = memoryview(buf)
    try:
        for i in range(h.chunks):
            a, b, level, zero, nb = _CHUNK.unpack_from(mv, off)
            if zero != 0 or level > 1 or b <= a:
                raise NativeFormatError(f"chunk header ({a}, {b}, {level}, {zero}) not understood")
            if i == 0:
                level_first = level
            ch_a[i], ch_b[i], ch_off[i], ch_nb[i] = a, b, off + _CHUNK.size, nb
            off += _CHUNK.size + nb
    except struct.error as exc:
        raise NativeFormatError(f"chunk headers run past the end of the file ({exc})") from exc
    finally:
        mv.release()
    if off != buf.size:
        raise NativeFormatError(f"{buf.size - off} bytes after the last chunk")
    gap = np.flatnonzero(ch_a[1:] != ch_b[:-1])
    if gap.size:
        raise NativeFormatError(f"gap between chunks ({ch_b[gap[0]]} -> {ch_a[gap[0] + 1]})")

    # pieces: a chunk each, big chunks cut at token starts
    big = np.flatnonzero(ch_nb > K.piece_bytes)
    if big.size == 0:
        p_off, p_len, p_chunk = ch_off.copy(), ch_nb.copy(), np.arange(h.chunks, dtype=np.int64)
    else:
        parts = {int(c): _pieces(buf, int(ch_off[c]), int(ch_nb[c])) for c in big}
        reps = np.ones(h.chunks, np.int64)
        reps[big] = [len(parts[int(c)]) for c in big]
        p_chunk = np.repeat(np.arange(h.chunks, dtype=np.int64), reps)
        p_off, p_len = np.repeat(ch_off, reps), np.repeat(ch_nb, reps)
        first = np.cumsum(reps) - reps
        for c, ps in parts.items():
            p_off[first[c]:first[c] + len(ps)] = [o for o, _n in ps]
            p_len[first[c]:first[c] + len(ps)] = [n for _o, n in ps]
    n_pieces = p_off.size
    p_nofirst = np.zeros(n_pieces, bool)
    p_nofirst[0] = True                                   # the data start is not an edge
    p_base = np.zeros(n_pieces, np.int64)

    # one pass, about block_bytes at a time: decode, learn where each piece starts,
    # set a mark every mark_every edges, check that every chunk adds up
    blocks = np.flatnonzero(np.diff(np.cumsum(p_len) // K.block_bytes, prepend=-1)) if n_pieces else []
    bounds = [*np.asarray(blocks).tolist(), n_pieces]
    ch_len = ch_b - ch_a
    ch_sum = np.zeros(h.chunks, np.int64)             # samples covered so far, per chunk
    first_s = h.begin if h.begin is not None else int(ch_a[0])
    last_s = h.end if h.end is not None else int(ch_b[-1])
    skip = tail = seen = 0
    level0 = level_first
    every = int(K.mark_every)
    m_off, m_sample, m_piece = [], [], []
    for f, to in zip(bounds[:-1], bounds[1:]):
        runs, tok_piece, _per, first_tok, tok_off = _runs(buf, p_off[f:to], p_len[f:to])
        sums = np.add.reduceat(runs, first_tok)
        ch = p_chunk[f:to]
        # a piece starts where the earlier pieces of its chunk (this block or before) ended
        excl = np.cumsum(sums) - sums
        new = np.r_[True, ch[1:] != ch[:-1]]
        group_first = np.maximum.accumulate(np.where(new, np.arange(ch.size), 0))
        p_base[f:to] = ch_a[ch] + ch_sum[ch] + (excl - excl[group_first])
        np.add.at(ch_sum, ch, sums)
        ed, keep = _positions(runs, tok_piece, first_tok, p_base[f:to], p_nofirst[f:to])
        mark = (seen + np.arange(ed.size)) % every == 0
        m_off.append(tok_off[keep][mark])
        m_sample.append(ed[mark])
        m_piece.append((f + tok_piece)[keep][mark])
        before = int(np.searchsorted(ed, first_s, side="right"))
        skip += before
        level0 ^= before & 1
        tail += int(ed.size - np.searchsorted(ed, last_s, side="left"))
        seen += ed.size
        say(f"Indexing {100 * to // n_pieces} %")
    bad = np.flatnonzero(ch_sum != ch_len)
    if bad.size:
        c = int(bad[0])
        raise NativeFormatError(f"chunk {c}: runs add up to {ch_sum[c]} samples, not {ch_len[c]}")
    return Index(rate=h.rate, level0=level0, first=int(first_s), last=int(last_s), skip=skip,
                 count=seen - skip - tail, total=seen, p_off=p_off, p_len=p_len, p_base=p_base,
                 p_nofirst=p_nofirst, m_off=np.concatenate(m_off), m_sample=np.concatenate(m_sample),
                 m_piece=np.concatenate(m_piece))


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
        self._mark_t = (index.m_sample - shift) / index.rate
        self.shape = (index.count,)
        self.dtype = np.dtype(np.float64)
        self.ndim = 1

    def __len__(self) -> int:
        return self.ix.count

    def _span(self, m0: int, m1: int) -> tuple[list[int], list[int], list[int]]:
        """Payload pieces (offset, length, base sample) holding decoded edges [m0, m1) * mark_every."""
        ix = self.ix
        j0, o0, base0 = int(ix.m_piece[m0]), int(ix.m_off[m0]), int(ix.m_sample[m0])
        if m1 < ix.m_off.size:
            j1, o1 = int(ix.m_piece[m1]), int(ix.m_off[m1])
        else:
            j1 = ix.p_off.size - 1
            o1 = int(ix.p_off[j1] + ix.p_len[j1])
        if j0 == j1:
            return [o0], [o1 - o0], [base0]
        off = [o0, *ix.p_off[j0 + 1:j1 + 1].tolist()]
        ln = [int(ix.p_off[j0] + ix.p_len[j0]) - o0, *ix.p_len[j0 + 1:j1].tolist(), o1 - int(ix.p_off[j1])]
        base = [base0, *ix.p_base[j0 + 1:j1 + 1].tolist()]
        if ln[-1] == 0:
            off, ln, base = off[:-1], ln[:-1], base[:-1]
        return off, ln, base

    def _decode_spans(self, spans: list[tuple[int, int]]) -> list[np.ndarray]:
        """Times for several [m0, m1) mark ranges, decoded in one vectorised pass."""
        if self._buf is None:
            self._buf = np.memmap(self.raw, dtype=np.uint8, mode="r")
        ix, every = self.ix, int(K.mark_every)
        off, ln, base, want = [], [], [], []
        for m0, m1 in spans:
            o, n, b = self._span(m0, m1)
            off += o
            ln += n
            base += b
            want.append(min(m1 * every, ix.total) - m0 * every)
        runs, tok_piece, _per, first_tok, _o = _runs(self._buf, np.array(off), np.array(ln))
        samples, _keep = _positions(runs, tok_piece, first_tok, np.array(base, np.int64),
                                    np.zeros(len(off), bool))
        if samples.size != sum(want):
            raise NativeFormatError(f"decoded {samples.size} edges between marks, expected {sum(want)}")
        times = (samples - self.shift) / ix.rate
        return np.split(times, np.cumsum(want)[:-1])

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
        n, every, skip = self.ix.count, int(K.mark_every), self.ix.skip
        if isinstance(key, slice):
            start, stop, step = key.indices(n)
            if step != 1:
                raise IndexError("LazyTimes supports contiguous slices only")
            if stop <= start:
                return np.empty(0, np.float64)
            m0, m1 = (start + skip) // every, (stop - 1 + skip) // every + 1
            if m1 - m0 <= K.cache_span_marks:
                got = self._marks(range(m0, m1))
                arr = np.concatenate([got[m] for m in range(m0, m1)])
            else:                                     # a long stretch: in batches, not cached
                step = int(K.batch_marks)
                arr = np.concatenate([self._decode_spans([(m, min(m + step, m1))])[0]
                                      for m in range(m0, m1, step)])
            return arr[start + skip - m0 * every:stop + skip - m0 * every].copy()
        i = int(key)
        if i < 0:
            i += n
        if not 0 <= i < n:
            raise IndexError(f"edge {key} of {n}")
        d = i + skip
        return self._mark(d // every)[d % every]

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
            every = int(K.mark_every)
            need = np.unique(marks[marks >= 0]).tolist()
            got = {}
            for i in range(0, len(need), int(K.batch_marks)):
                got.update(self._marks(need[i:i + int(K.batch_marks)]))
            for m in need:
                sel = marks == m
                out[sel] = m * every + np.searchsorted(got[m], vals[sel], side=side)
        out = np.clip(out - self.ix.skip, 0, self.ix.count)
        return int(out[0]) if scalar else out

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


def unpack(sal: Path, dest: Path, wanted: list[int] | None,
           say: Callable[[str], None] = lambda _m: None) -> None:
    """Inflate each channel file once and index it (constant memory)."""
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
        total = sum(found[c].file_size for c in chans)
        done = 0
        for c in chans:
            info = found[c]
            out = dest / f"digital-{c}.raw"
            with z.open(info) as src, open(out, "wb") as dst:
                while True:
                    block = src.read(K.copy_chunk_bytes)
                    if not block:
                        break
                    dst.write(block)
                    done += len(block)
                    say(f"Unpacking {done / 1e6:,.0f} of {total / 1e6:,.0f} MB")
            ix = build_index(out, say)
            ix.save(dest / f"digital-{c}.{_INDEX}")
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
