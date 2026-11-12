"""The two loops of the direct .sal reader, compiled by numba for this PC's CPU.

Imported only through app.readers.sal_fast, which checks them against the
numpy code in sal_native before they are used.  They read the same grammar

    00vvvvvv | 01vvvvvv 1vvvvvvv* 0vvvvvvv        (run length - 1, big-endian)

one byte at a time -- which compiled code does at several hundred MB/s per
core, and without the temporary arrays the numpy version moves around.  Both
release the GIL, so index blocks run on every core.

No Python objects, no allocation inside the loops, and every index is checked
against the array it goes into (boundscheck): a wrong file can make these
return an error code, never read or write outside their arrays.
"""
from __future__ import annotations

import numpy as np
from numba import njit

OK, CUT_OFF, NO_START, TOO_LONG, EMPTY = 0, 1, 2, 3, 4


@njit(nogil=True, cache=True, boundscheck=True)
def index_block(p, pstart, first, every, max_bytes):
    """sal_native._index_block's numbers for the payload bytes `p` of some pieces.

    Returns (code, tokens, sum per piece, marks, and per mark: byte position,
    piece, samples before it, samples in its stretch's 1st, 3rd ... run, its
    stretch's shortest run).
    """
    n, pieces = p.size, pstart.size
    sums = np.zeros(pieces, np.int64)
    cap = n // every + 2
    m_pos = np.empty(cap, np.int64)
    m_piece = np.empty(cap, np.int64)
    m_rel = np.empty(cap, np.int64)
    m_even = np.zeros(cap, np.int64)
    m_low = np.empty(cap, np.int64)
    tok = 0
    total = np.int64(0)
    k = -1
    fill = 0                                   # tokens in the current stretch
    skip = 1 if first else 0                   # the start of the data is not an edge
    code = OK
    for q in range(pieces):
        i = pstart[q]
        end = pstart[q + 1] if q + 1 < pieces else n
        if end <= i:
            code = EMPTY
            break
        piece_sum = np.int64(0)
        while i < end:
            b = p[i]
            at = i
            i += 1
            if b < 0x40:
                v = np.int64(b)
            elif b < 0x80:
                v = np.int64(b & 0x3F)
                size = 1
                while True:
                    if i >= end:
                        code = CUT_OFF
                        break
                    if size >= max_bytes:
                        code = TOO_LONG
                        break
                    nb = p[i]
                    i += 1
                    size += 1
                    v = (v << 7) | np.int64(nb & 0x7F)
                    if nb < 0x80:
                        break
                if code != OK:
                    break
            else:
                code = NO_START
                break
            run = v + 1
            if tok >= skip:
                if fill == 0:
                    k += 1
                    m_pos[k] = at
                    m_piece[k] = q
                    m_rel[k] = total
                    m_low[k] = run
                elif run < m_low[k]:
                    m_low[k] = run
                if fill & 1 == 0:
                    m_even[k] += run
                fill += 1
                if fill == every:
                    fill = 0
            total += run
            piece_sum += run
            tok += 1
        if code != OK:
            break
        sums[q] = piece_sum
    return code, tok, sums, k + 1, m_pos, m_piece, m_rel, m_even, m_low


@njit(nogil=True, cache=True, boundscheck=True)
def tokens(p, pstart, max_bytes):
    """sal_native._tokens: (code, tokens, byte position of each, run length of each)."""
    n, pieces = p.size, pstart.size
    at = np.empty(n, np.int64)
    runs = np.empty(n, np.int64)
    tok = 0
    code = OK
    for q in range(pieces):
        i = pstart[q]
        end = pstart[q + 1] if q + 1 < pieces else n
        if end <= i:
            code = EMPTY
            break
        while i < end:
            b = p[i]
            at[tok] = i
            i += 1
            if b < 0x40:
                v = np.int64(b)
            elif b < 0x80:
                v = np.int64(b & 0x3F)
                size = 1
                while True:
                    if i >= end:
                        code = CUT_OFF
                        break
                    if size >= max_bytes:
                        code = TOO_LONG
                        break
                    nb = p[i]
                    i += 1
                    size += 1
                    v = (v << 7) | np.int64(nb & 0x7F)
                    if nb < 0x80:
                        break
                if code != OK:
                    break
            else:
                code = NO_START
                break
            runs[tok] = v + 1
            tok += 1
        if code != OK:
            break
    return code, tok, at, runs
