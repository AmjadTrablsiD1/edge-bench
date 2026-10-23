#!/usr/bin/env python3
"""CSV vs binary on a large capture -- the measurement behind the README's speed claim.

Captures DURATION seconds on Logic 2's simulated Logic Pro 16 (16 channels), saves
the .sal, then times:
  1. Logic 2 exporting digital CSV           (what people do today)
  2. reading that CSV with pandas            (the best case for CSV)
  3. Edge Bench: .sal -> binary -> memory map (cold, through Logic 2)
  4. Edge Bench: the same .sal again         (warm, from the cache)
  5. one measurement on all 16 channels

  .venv/bin/python scripts/benchmark.py <out-dir> [seconds]
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from saleae import automation  # noqa: E402

from app import cache, logic2, measure, readers  # noqa: E402
from app.readers import csv_digital  # noqa: E402


def mb(p: Path) -> float:
    if p.is_dir():
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1e6
    return p.stat().st_size / 1e6


def main() -> None:
    out = Path(sys.argv[1]).resolve()
    duration = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0
    out.mkdir(parents=True, exist_ok=True)
    sal = out / f"bench_{int(duration)}s.sal"
    s = logic2.Logic2()
    rows = []
    try:
        mgr = s._ensure()
        if not sal.exists():
            dev = next(d for d in mgr.get_devices(include_simulation_devices=True)
                       if d.device_type.name == "LOGIC_PRO_16")
            cfg = automation.LogicDeviceConfiguration(enabled_digital_channels=list(range(16)),
                                                      digital_sample_rate=100_000_000)
            t = time.perf_counter()
            cap = mgr.start_capture(device_id=dev.device_id, device_configuration=cfg,
                                    capture_configuration=automation.CaptureConfiguration(
                                        capture_mode=automation.TimedCaptureMode(duration_seconds=duration)))
            cap.wait()
            cap.save_capture(str(sal))
            cap.close()
            rows.append(("capture + save .sal", time.perf_counter() - t, mb(sal)))

        csv_dir = out / "csv"
        csv_dir.mkdir(exist_ok=True)
        cap = mgr.load_capture(str(sal))
        t = time.perf_counter()
        cap.export_raw_data_csv(str(csv_dir), digital_channels=list(range(16)))
        csv = csv_dir / "digital.csv"
        last = -1
        while True:                      # export returns early; wait until the file stops growing
            time.sleep(0.5)
            size = csv.stat().st_size if csv.exists() else 0
            if size == last and size > 0:
                break
            last = size
        rows.append(("Logic 2 CSV export", time.perf_counter() - t - 0.5, mb(csv)))
        cap.close()

        t = time.perf_counter()
        c = csv_digital.load(csv)
        rows.append(("read CSV (pandas)", time.perf_counter() - t, None))
        edges = sum(ch.edge_count for ch in c.channels)
        del c

        k = cache.root() / cache.key(sal)
        if k.exists():
            import shutil
            shutil.rmtree(k)
        t = time.perf_counter()
        b = readers.load(sal, logic2=s)
        rows.append(("Edge Bench cold: .sal -> binary -> mmap", time.perf_counter() - t, mb(k)))
        t = time.perf_counter()
        b = readers.load(sal, logic2=None)
        rows.append(("Edge Bench warm: from cache", time.perf_counter() - t, None))
        t = time.perf_counter()
        r = measure.run(b, measure.Settings(ref_channel=0, occurrence=1000))
        rows.append(("measure 16 channels (ref = 1000th edge)", time.perf_counter() - t, None))
        assert r.reference is not None
    finally:
        s.close()

    print(f"\nCapture: {duration:g} s, 16 channels @ 100 MS/s, {edges:,} edges, .sal {mb(sal):.1f} MB\n")
    print(f"{'step':45} {'time':>10} {'size':>10}")
    for name, sec, size in rows:
        print(f"{name:45} {sec:9.3f}s {'' if size is None else f'{size:8.1f}MB':>10}")


if __name__ == "__main__":
    main()
