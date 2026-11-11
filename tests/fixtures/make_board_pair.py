#!/usr/bin/env python3
"""Write board_a.sal / board_b.sal: two Logic 2 captures of one board, old and new firmware.

Same eight named signals at 100 MS/s for 0.5 s.  B differs from A in three
planted ways, which the Compare tests look for:

  PLL_LOCK   locks 9 ms after PWR_GOOD in A, never in B          (critical)
  FAULT      silent in A, one 30 ns glitch in B                  (critical)
  PWM_FAN    stops toggling 100 ms early in B                    (changed)

and RESET_N moves by 20 µs on 2.5 ms, inside the default tolerance   (same).

  .venv/bin/python tests/fixtures/make_board_pair.py
"""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
from tests import salgen  # noqa: E402

RATE, END = 100e6, 50_000_000
NAMES = ["SPI_CLK", "SPI_MOSI", "UART_TX", "PWM_FAN", "PWR_GOOD", "RESET_N", "PLL_LOCK", "FAULT"]
REF = 1_000_000                                            # PWR_GOOD rises at 10 ms


def edges(which: str) -> dict[int, np.ndarray]:
    rng = np.random.default_rng(1)
    clk = 2_000_000 + np.arange(40_000) * 50              # 1 MHz clock burst
    mosi = clk[rng.random(clk.size) < 0.3] + 7
    uart = np.cumsum(rng.integers(868, 868 * 6, 3000)) + 3_000_000
    pwm = REF + np.arange(1, 9601) * 5000                 # 10 kHz
    ch = {0: clk, 1: mosi, 2: uart[uart < END - 10], 3: pwm if which == "a" else pwm[pwm < END - 10_000_000],
          4: np.array([REF]), 5: np.array([REF + (250_000 if which == "a" else 252_000)]),
          6: np.array([REF + 900_000] if which == "a" else [], dtype=np.int64),
          7: np.array([], dtype=np.int64) if which == "a" else REF + 5_000_000 + np.array([0, 3])}
    return {c: np.asarray(e, dtype=np.int64) for c, e in ch.items()}


def main() -> None:
    for which in "ab":
        files = {c: salgen.channel_file(RATE, salgen.chunks_from_edges(e, 0, 0, END, int(RATE / 30)))
                 for c, e in edges(which).items()}
        path = salgen.write_sal(HERE / f"board_{which}.sal", files, RATE)
        with zipfile.ZipFile(path) as z:
            meta = json.loads(z.read("meta.json"))
            blobs = {n: z.read(n) for n in z.namelist() if n != "meta.json"}
        row = meta["data"]["rowsSettings"][0]
        meta["data"]["rowsSettings"] = [dict(row, id=f"row-{c}", name=NAMES[c], channel=dict(row["channel"], deviceChannel=c))
                                        for c in sorted(files)]
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("meta.json", json.dumps(meta))
            for n, b in blobs.items():
                z.writestr(n, b)
        print(path.name, path.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
