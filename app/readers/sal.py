"""Saleae Logic 2 `.sal` capture.

A `.sal` is a zip.  Its `meta.json` is plain JSON (device, sample rate, enabled
channels, user channel names) and is read here directly -- instant, no Logic 2.
The edge data beside it is an undocumented compressed format, so the edges come
from Logic 2 itself: `app.logic2` exports them as binary into the cache, once.
"""
from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from app.capture import Capture
from app.readers import binary


@dataclass
class SalMeta:
    device: str | None = None
    device_type: str | None = None
    simulation: bool = False
    sample_rate: float | None = None
    digital: list[int] = field(default_factory=list)       # enabled digital channel indices
    names: dict[int, str] = field(default_factory=dict)
    capture_start_unix_ms: float | None = None
    meta_version: int | None = None
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "device": self.device, "device_type": self.device_type,
            "simulation": self.simulation, "sample_rate": self.sample_rate,
            "digital": self.digital,
            "names": {str(k): v for k, v in self.names.items()},
            "capture_start_unix_ms": self.capture_start_unix_ms,
            "warnings": self.warnings,
        }


def sniff(path: Path) -> bool:
    return path.suffix.lower() == ".sal" and path.is_file()


def _digital_rows(data: dict) -> dict[int, str]:
    names: dict[int, str] = {}
    for row in data.get("rowsSettings") or []:
        ch = row.get("channel") or {}
        if row.get("type") == "channel" and ch.get("type") == "Digital":
            idx = ch.get("deviceChannel")
            if isinstance(idx, int):
                names.setdefault(idx, str(row.get("name") or f"Channel {idx}"))
    return names


def read_meta(path: Path) -> SalMeta:
    """Everything the .sal says about itself, tolerant of missing keys.

    Field names were read from Logic 2.4.48 files (meta version 22).  Newer
    devices may store them elsewhere; anything not found is left None and the
    binary export fills the gap.
    """
    meta = SalMeta()
    try:
        with zipfile.ZipFile(path) as z:
            raw = json.loads(z.read("meta.json"))
            members = z.namelist()
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path.name}: not a readable Logic 2 capture ({exc})") from exc

    meta.meta_version = raw.get("version")
    data = raw.get("data") or {}
    dev = data.get("legacyDevice") or data.get("device") or {}
    meta.device = dev.get("name") or dev.get("deviceType")
    meta.device_type = dev.get("deviceType")
    meta.simulation = bool(dev.get("isSimulation"))

    settings = data.get("legacySettings") or data.get("settings") or {}
    rate = (settings.get("sampleRate") or {}).get("digital")
    meta.sample_rate = float(rate) if rate else None
    enabled = [c.get("index") for c in settings.get("enabledChannels") or []
               if c.get("type") == "Digital" and isinstance(c.get("index"), int)]

    names = _digital_rows(data)
    # The zip itself lists digital-<n>.bin for every channel that holds data.
    in_zip = sorted(i for m in members if (i := binary.channel_index(Path(m))) is not None)
    meta.digital = sorted(set(enabled) or set(in_zip) or set(names))
    meta.names = {i: names.get(i, f"Channel {i}") for i in meta.digital}

    start = data.get("captureStartTime") or {}
    if "unixTimeMilliseconds" in start:
        meta.capture_start_unix_ms = start["unixTimeMilliseconds"] + start.get("fractionalMilliseconds", 0)
    if not meta.digital:
        meta.warnings.append("No digital channels found in meta.json -- the export will ask Logic 2 for all.")
    if meta.sample_rate is None:
        meta.warnings.append("Sample rate not found in meta.json.")
    return meta


def load(path: Path, *, logic2=None, progress=None, **_) -> Capture:
    """Export through Logic 2 (cached), then read the binary like any other."""
    from app import cache

    meta = read_meta(path)
    folder = cache.lookup(path)
    if folder is None:
        if logic2 is None:
            raise RuntimeError("This .sal has not been exported yet and Logic 2 is not available.")
        with cache.slot(path) as tmp:
            logic2.export_binary(path, tmp, meta.digital or None, progress=progress)
        folder = cache.lookup(path)
    cap = binary.load_folder(folder, names=meta.names, sample_rate=meta.sample_rate,
                             device=meta.device, source_path=path, source="sal")
    cap.notes.extend(meta.warnings)
    if meta.simulation:
        cap.notes.append("Captured on a Logic 2 simulated device (demo data).")
    return cap
