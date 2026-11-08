#!/usr/bin/env python3
"""Record .sal captures with Logic 2's simulated devices, each with its binary export.

The direct .sal reader (app/readers/sal_native.py) decodes a format Saleae does
not document.  This makes its ground truth: for every capture, Logic 2's own
binary export of the same data.  Run it again after a Logic 2 update and
`pytest tests/test_sal_native.py --corpus DIR` says whether the format moved.

    .venv/bin/python scripts/make_sal_corpus.py OUT_DIR [--quick]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from saleae import automation  # noqa: E402

from app import logic2  # noqa: E402

# (DeviceType name, sample rates, durations s) -- small ones first
PLAN = [
    ("LOGIC_8", [1_000_000, 25_000_000], [0.001, 0.05, 2.0]),
    ("LOGIC_PRO_16", [125_000_000, 50_000_000], [0.001, 0.2]),
    ("LOGIC_PRO_8", [250_000_000], [0.5]),
]
# Captures whose time 0 is not their first sample: a trigger (pre-trigger data
# has negative times) and a trimmed timed capture (the start is cut off).
SPECIAL = [
    ("LOGIC_8", 25_000_000, "trigger", lambda: automation.DigitalTriggerCaptureMode(
        trigger_type=automation.DigitalTriggerType.RISING, trigger_channel_index=0,
        after_trigger_seconds=0.02)),
    ("LOGIC_8", 25_000_000, "trimmed", lambda: automation.TimedCaptureMode(
        duration_seconds=0.3, trim_data_seconds=0.1)),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--quick", action="store_true", help="only the smallest captures")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)

    l2 = logic2.Logic2()
    mgr = l2._ensure()
    devices = mgr.get_devices(include_simulation_devices=True)
    made = []

    def record(dev, nch, rate, mode, name):
        try:
            cap = mgr.start_capture(
                device_id=dev.device_id,
                device_configuration=automation.LogicDeviceConfiguration(
                    enabled_digital_channels=list(range(nch)), digital_sample_rate=rate),
                capture_configuration=automation.CaptureConfiguration(capture_mode=mode))
            cap.wait()
        except Exception as exc:                 # rate not offered by this device
            print(f"skip {name}: {exc}")
            return
        sal = a.out / f"{name}.sal"
        exp = a.out / f"{name}_bin"
        exp.mkdir(exist_ok=True)
        cap.save_capture(str(sal.resolve()))
        cap.export_raw_data_binary(str(exp.resolve()), digital_channels=list(range(nch)))
        cap.close()
        logic2.wait_complete(exp, nch)
        made.append({"name": name, "device": dev.device_type.name, "rate": rate})
        print("made", name, sal.stat().st_size, "bytes")

    def device(frag):
        return next((d for d in devices if d.is_simulation and d.device_type.name == frag), None)

    try:
        for frag, rate, kind, mode in SPECIAL:
            record(device(frag), 8, rate, mode(), f"{frag.lower()}_{rate // 1000}k_{kind}")
        for frag, rates, durations in PLAN:
            dev = device(frag)
            if dev is None:
                print(f"no simulated {frag}; have {[str(d.device_type) for d in devices]}")
                continue
            nch = 16 if frag.endswith("16") else 8
            for rate in rates[:1] if a.quick else rates:
                for dur in durations[:2] if a.quick else durations:
                    name = f"{frag.lower()}_{rate // 1000}k_{dur:g}s"
                    record(dev, nch, rate, automation.TimedCaptureMode(duration_seconds=dur), name)
    finally:
        l2.close()
    info = {"logic2_version": l2.version, "captures": made}
    (a.out / "corpus.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
