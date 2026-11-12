"""Optional: the direct .sal reader's two loops, compiled for this PC's CPU.

With numba installed (requirements-fast.txt), indexing a .sal runs several
times faster and on every core.  Everything here is built so that it can
only ever make things faster, never different:

  - numba missing, too old, or failing to compile  -> the numpy code is used;
  - before first use the compiled loops must reproduce the numpy code's
    results, bit for bit, on a set of byte patterns that covers the whole
    grammar (`_self_test`) -- otherwise they are not used;
  - any unexpected error while running switches them off for this session,
    and the block that failed is done again by the numpy code;
  - the user can switch them off (App tab; settings.json "fast_index").

sal_native asks `kernels()` before each block; None means "use numpy".
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time

import numpy as np

from app.constants import C, path as cpath

log = logging.getLogger(__name__)
K = C.sal_native
SETTINGS = cpath("config_dir") / "settings.json"
_lock = threading.Lock()
_state: dict = {"tried": False, "kernels": None, "detail": "", "load_s": None, "version": None}


def wanted() -> bool:
    """The user's switch (default on)."""
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8")).get(K.fast_setting, True) is not False
    except (OSError, ValueError):
        return True


def set_wanted(on: bool) -> None:
    try:
        data = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data[K.fast_setting] = bool(on)
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _load():
    """Import, compile (or load from the cache) and check the kernels.  Returns (module, detail)."""
    # numba keeps compiled code beside the source by default; the install folder may be read-only
    os.environ.setdefault("NUMBA_CACHE_DIR", str(cpath("cache_dir") / K.fast_cache_dir))
    try:
        import numba
        from app.readers import _sal_kernels as kernels
    except ImportError as exc:
        return None, f"not installed ({exc})"
    _state["version"] = numba.__version__
    problem = _self_test(kernels)
    if problem:
        return None, f"switched off: its self-test failed ({problem})"
    return kernels, f"numba {numba.__version__}"


def _self_test(kernels) -> str | None:
    """The compiled loops against the numpy code, on every kind of token.  None when identical."""
    from app.readers import sal_native as N
    rng = np.random.default_rng(K.fast_test_seed)
    lengths = np.concatenate([rng.integers(1, 65, 3000), rng.integers(65, 8193, 3000),
                              2 ** rng.integers(13, 62, 300), np.full(500, 200), np.full(500, 130)])
    rng.shuffle(lengths)
    parts, cuts = [], [0, 1, 900, 901, 4000, lengths.size]
    for a, b in zip(cuts[:-1], cuts[1:]):
        parts.append(_encode(lengths[a:b]))
    pstart = np.cumsum([0, *[len(q) for q in parts[:-1]]]).astype(np.int64)
    data = b"".join(parts)
    off = pstart + 1000
    for p in (np.frombuffer(data, np.uint8), np.frombuffer(data, np.uint8).copy()):     # read-only and writable
        for first in (True, False):
            for every in (7, int(K.mark_every)):
                want = N._index_block_numpy(p, pstart, off, first, every)
                got = N._index_block_fast(kernels, p, pstart, off, first, every)
                if len(want) != len(got) or not all(np.array_equal(w, g) for w, g in zip(want, got)):
                    return f"index block differs (first={first}, every={every})"
        w_at, w_runs = N._tokens_numpy(p, pstart)
        g_at, g_runs = N._tokens_fast(kernels, p, pstart)
        if not (np.array_equal(w_at, g_at) and np.array_equal(w_runs.astype(np.int64), g_runs)):
            return "tokens differ"
    # what the grammar does not allow must be refused by both loops, exactly like numpy does
    refused = (([0x05, 0x41], [0]), ([0x05, 0x41, 0x80], [0]), ([0x05, 0x80, 0x01], [0]), ([0x80, 0x01], [0]),
               ([0x41] + [0x80] * 8 + [0x01], [0]), ([0x05, 0x41, 0x07, 0x07], [0, 2]), ([0x05, 0x06], [0, 2]))
    for bad, cuts in refused:
        p, cuts = np.array(bad, np.uint8), np.array(cuts, np.int64)
        for name, call in (("tokens", lambda: N._tokens_fast(kernels, p, cuts)),
                           ("index block", lambda: N._index_block_fast(kernels, p, cuts, cuts, False, 7))):
            try:
                call()
                return f"{name}: bytes {bad} were not refused"
            except N.NativeFormatError:
                pass
    longest = np.array([0x41] + [0x80] * 7 + [0x01, 0x02], np.uint8)     # nine bytes is allowed
    if N._tokens_fast(kernels, longest, np.zeros(1, np.int64))[1].tolist() != [(1 << 56) + 2, 3]:
        return "the longest token is read wrongly"
    if N._index_block_fast(kernels, longest, np.zeros(1, np.int64), np.zeros(1, np.int64), False, 7)[1].tolist() != [(1 << 56) + 5]:
        return "the longest token is added up wrongly"
    return None


def _encode(runs) -> bytes:
    """Run lengths -> bytes (only for the self-test; the tests have their own encoder)."""
    out = bytearray()
    for r in runs.tolist():
        v = r - 1
        if v < 0x40:
            out.append(v)
            continue
        digits = []
        while v >= 0x40 or not digits:
            digits.append(v & 0x7F)
            v >>= 7
        out.append(0x40 | v)
        out += bytes(d | 0x80 for d in reversed(digits[1:]))
        out.append(digits[0])
    return bytes(out)


def kernels():
    """The compiled kernels, or None: not wanted, not installed, or not trusted."""
    if not _state["tried"]:
        with _lock:
            if not _state["tried"]:
                t = time.perf_counter()
                try:
                    _state["kernels"], _state["detail"] = _load()
                except Exception as exc:                  # a compiler problem must never stop a capture
                    log.exception("compiled indexing could not be loaded")
                    _state["kernels"], _state["detail"] = None, f"switched off: {type(exc).__name__}: {exc}"
                _state["load_s"] = time.perf_counter() - t
                _state["tried"] = True
                log.info("compiled indexing: %s (%.1f s)", _state["detail"], _state["load_s"])
    return _state["kernels"] if wanted() else None


def failed(exc: BaseException) -> None:
    """A compiled loop raised something unexpected: use numpy for the rest of this session."""
    log.exception("compiled indexing failed; using the numpy code from here on", exc_info=exc)
    _state["kernels"], _state["detail"] = None, f"switched off: {type(exc).__name__}: {exc}"


def reset() -> None:
    """Forget what was loaded (after installing numba, and in tests)."""
    with _lock:
        _state.update(tried=False, kernels=None, detail="", load_s=None)


def warm() -> None:
    """Load in the background at start-up, so the first capture does not wait for the compiler."""
    if wanted():
        threading.Thread(target=kernels, name="fast-warm", daemon=True).start()


def status() -> dict:
    """For the App tab.  Does not load anything."""
    on = wanted()
    ready = _state["tried"]
    active = bool(on and _state["kernels"] is not None)
    if not on:
        text = "Off"
    elif not ready:
        text = "Starting…"
    elif active:
        text = f"On — compiled for this PC ({_state['detail']})"
    else:
        text = f"Not active — {_state['detail']}"
    return {"wanted": on, "ready": ready, "active": active, "text": text,
            "installed": ready and not _state["detail"].startswith("not installed"),
            "load_s": _state["load_s"]}
