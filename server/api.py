"""HTTP only.  Every route is a thin call into app/; no arithmetic on edges here.

Captures load in one background worker (Logic 2 is a single GUI process, so
exports run one at a time).  The UI polls GET /api/files for progress.
"""
from __future__ import annotations

import itertools
import logging
import platform
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import cache, dialogs, excel, logic2, measure, readers, waveform
from app.capture import Capture
from app.constants import C
from app.readers import sal

log = logging.getLogger(__name__)
UI_DIST = Path(__file__).resolve().parent.parent / "ui" / "dist"


@dataclass
class Entry:
    id: int
    path: Path
    kind: str
    status: str = "queued"           # queued | loading | ready | error
    step: str = ""
    error: str | None = None
    meta: dict = field(default_factory=dict)
    capture: Capture | None = None
    added: float = field(default_factory=time.time)
    load_s: float | None = None

    def public(self) -> dict:
        d = {"id": self.id, "path": str(self.path), "name": self.path.name, "kind": self.kind,
             "status": self.status, "step": self.step, "error": self.error, "meta": self.meta,
             "load_s": self.load_s}
        if self.capture is not None:
            d["capture"] = self.capture.summary()
        return d


class State:
    def __init__(self) -> None:
        self.entries: dict[int, Entry] = {}
        self.ids = itertools.count(1)
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="loader")
        self.logic2 = logic2.Logic2()


S = State()


@asynccontextmanager
async def lifespan(_app):
    yield
    S.pool.shutdown(wait=False, cancel_futures=True)
    S.logic2.close()


api = FastAPI(title=C.app.name, version=C.app.version, lifespan=lifespan)


@api.exception_handler(Exception)
async def _unhandled(_req, exc: Exception):
    log.exception("unhandled")
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})


# ---------------------------------------------------------------- identity

@api.get("/api/whoami")
def whoami():
    return {"token": C.server.whoami_token, "name": C.app.name, "version": C.app.version}


@api.get("/api/status")
def status():
    return {"logic2": S.logic2.status(), "platform": platform.system(),
            "cache_bytes": cache.size_bytes(), "cache_dir": str(cache.root()),
            "cache_max_gb": C.cache.max_gb}


# ------------------------------------------------------------------- files

def _load(entry: Entry) -> None:
    entry.status, entry.step = "loading", "Reading"
    t = time.perf_counter()
    try:
        entry.capture = readers.load(entry.path, logic2=S.logic2,
                                     progress=lambda m: setattr(entry, "step", m))
        entry.status, entry.step = "ready", ""
    except Exception as exc:                              # shown to the user, logged in full
        log.exception("load failed: %s", entry.path)
        entry.status, entry.error, entry.step = "error", str(exc), ""
    entry.load_s = time.perf_counter() - t


def _add(p: Path) -> Entry | None:
    p = p.expanduser()
    with S.lock:
        for e in S.entries.values():
            if e.path == p and e.status != "error":
                return e
        kind = readers.detect(p) if p.exists() else None
        e = Entry(id=next(S.ids), path=p, kind=kind or "unknown")
        S.entries[e.id] = e
    if not p.exists():
        e.status, e.error = "error", "File not found."
        return e
    if kind is None:
        e.status, e.error = "error", ("Not a Saleae capture (.sal), Logic 2 binary export "
                                      "(digital_N.bin) or Logic 2 digital CSV.")
        return e
    if kind == "sal":
        try:
            e.meta = sal.read_meta(p).as_dict()
        except ValueError as exc:
            e.status, e.error = "error", str(exc)
            return e
    S.pool.submit(_load, e)
    return e


def _expand_folder(folder: Path) -> list[Path]:
    """A folder of captures -> its .sal/.csv; a binary export folder -> itself."""
    from app.readers import binary
    if binary.bin_files(folder):
        return [folder]
    out = [p for p in sorted(folder.iterdir())
           if p.suffix.lower() in (".sal", ".csv") and p.is_file()]
    return out


class AddReq(BaseModel):
    paths: list[str]


@api.post("/api/files")
def add_files(req: AddReq):
    added = []
    for raw in req.paths:
        p = Path(raw.strip().strip('"')).expanduser()
        targets = _expand_folder(p) if p.is_dir() else [p]
        if p.is_dir() and not targets:
            raise HTTPException(400, f"No .sal, .csv or digital_N.bin files in {p}")
        for t in targets:
            e = _add(t)
            if e:
                added.append(e.public())
    return {"added": added}


@api.get("/api/files")
def list_files():
    return {"files": [e.public() for e in sorted(S.entries.values(), key=lambda e: e.id)]}


@api.delete("/api/files/{fid}")
def remove_file(fid: int):
    with S.lock:
        S.entries.pop(fid, None)
    return {"ok": True}


@api.post("/api/files/{fid}/retry")
def retry(fid: int):
    e = _entry(fid, need_ready=False)
    e.status, e.error, e.capture = "queued", None, None
    S.pool.submit(_load, e)
    return e.public()


def _entry(fid: int, need_ready: bool = True) -> Entry:
    e = S.entries.get(fid)
    if e is None:
        raise HTTPException(404, "That capture is no longer in the list.")
    if need_ready and (e.status != "ready" or e.capture is None):
        raise HTTPException(409, f"{e.path.name} is not loaded yet ({e.status}).")
    return e


# ----------------------------------------------------------------- measure

class MeasureReq(BaseModel):
    file_id: int
    settings: dict


def _settings(d: dict) -> measure.Settings:
    try:
        return measure.Settings.from_dict(d)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


def _run(e: Entry, s: measure.Settings) -> measure.Result:
    try:
        return measure.run(e.capture, s)
    except KeyError as exc:
        raise HTTPException(422, f"{e.path.name}: {exc.args[0]}") from exc
    except RuntimeError as exc:
        raise HTTPException(422, f"{e.path.name}: {exc}") from exc


@api.post("/api/measure")
def run_measure(req: MeasureReq):
    e = _entry(req.file_id)
    t = time.perf_counter()
    res = _run(e, _settings(req.settings))
    d = res.as_dict()
    d["compute_s"] = time.perf_counter() - t
    return d


@api.get("/api/waveform")
def get_waveform(file_id: int, t0: float, t1: float, px: int):
    e = _entry(file_id)
    try:
        return waveform.decimate(e.capture, t0, t1, px)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


# ------------------------------------------------------------------ export

class ExportReq(BaseModel):
    file_ids: list[int]
    settings: dict
    unit: str = C.units.default_export_unit
    path: str


@api.post("/api/export")
def export(req: ExportReq):
    s = _settings(req.settings)
    results, skipped = [], []
    for fid in req.file_ids:
        e = S.entries.get(fid)
        if e is None or e.status != "ready":
            skipped.append(e.path.name if e else str(fid))
            continue
        results.append(_run(e, s))
    if not results:
        raise HTTPException(409, "None of the selected captures is loaded yet.")
    out = Path(req.path).expanduser()
    if out.suffix.lower() != ".xlsx":
        out = out.with_suffix(".xlsx")
    try:
        excel.write(results, out, unit=req.unit)
    except PermissionError as exc:
        raise HTTPException(409, f"Cannot write {out.name} — is it open in Excel? ({exc})") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"path": str(out), "captures": len(results), "skipped": skipped}


class RevealReq(BaseModel):
    path: str


@api.post("/api/reveal")
def reveal(req: RevealReq):
    p = Path(req.path)
    if not p.exists():
        raise HTTPException(404, "That file is gone.")
    system = platform.system()
    if system == "Darwin":
        subprocess.Popen(["open", "-R", str(p)])
    elif system == "Windows":
        subprocess.Popen(["explorer", f"/select,{p}"])     # one argument: "/select,<path>"
    else:
        subprocess.Popen(["xdg-open", str(p.parent)])
    return {"ok": True}


@api.post("/api/quit")
def quit_app():
    """The window-less Windows launch (pythonw) has no other way to stop."""
    server = getattr(api.state, "server", None)
    if server is None:
        raise HTTPException(409, "Not started by main.py; stop it where it was started.")
    threading.Timer(0.3, lambda: setattr(server, "should_exit", True)).start()
    return {"ok": True}


# ----------------------------------------------------------------- dialogs

class DialogReq(BaseModel):
    kind: str                     # files | folder | save | exe
    initial: str | None = None
    name: str | None = None


@api.post("/api/dialog")
def dialog(req: DialogReq):
    if req.kind not in ("files", "folder", "save", "exe"):
        raise HTTPException(422, "unknown dialog kind")
    try:
        return {"paths": dialogs.ask(req.kind, initial=req.initial, name=req.name)}
    except dialogs.DialogError as exc:
        raise HTTPException(500, str(exc)) from exc


# ---------------------------------------------------------------- settings

class Logic2PathReq(BaseModel):
    path: str | None


@api.post("/api/settings/logic2")
def set_logic2(req: Logic2PathReq):
    logic2.save_path(req.path)
    S.logic2.close()                       # reconnect with the new binary next time
    return S.logic2.status()


@api.post("/api/cache/clear")
def clear_cache():
    busy = any(e.status == "loading" for e in S.entries.values())
    if busy:
        raise HTTPException(409, "A capture is loading; clear the cache when it is done.")
    import gc
    with S.lock:
        reload = [e for e in S.entries.values() if e.kind == "sal"]
        for e in reload:                  # drop the memory maps first: Windows cannot
            e.capture, e.status, e.step = None, "queued", ""   # delete a mapped file
    gc.collect()
    cache.clear()
    for e in reload:
        S.pool.submit(_load, e)
    return {"cache_bytes": cache.size_bytes()}


# ------------------------------------------------------------------ static

if UI_DIST.is_dir():
    api.mount("/assets", StaticFiles(directory=UI_DIST / "assets"), name="assets")

    @api.get("/{rest:path}")
    def spa(rest: str):
        f = (UI_DIST / rest).resolve()
        if rest and f.is_file() and UI_DIST in f.parents:
            return FileResponse(f)
        return FileResponse(UI_DIST / "index.html")
