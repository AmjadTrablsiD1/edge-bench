#!/usr/bin/env python3
"""Writes the two power-up fixtures as Logic 2 digital CSVs (1 ns grid).

  power_up_sequence.csv   baseline firmware
  power_up_new_fw.csv     "new firmware" with known, deliberate differences:
      RESET_N   released at +2.5 ms instead of +2 ms             -> changed
      PLL_LOCK  never locks                                       -> critical
      SPI_SCK   clock twice as fast (50 ns half period)           -> changed
      IRQ_N     idles low instead of high                         -> critical
      LED       harder bounce (10 ns spikes)                      -> changed
      PWR_GOOD, CLK_EN, SPI_CS_N unchanged                        -> same
  power_up_new_fw_rewired.csv   the same new firmware, probes moved to other
      inputs (LED on D0, PWR_GOOD on D1, ...) and RESET_N spelt "Reset_N" --
      for matching channels by name
"""
from pathlib import Path

NS = 1e-9
NAMES = ["PWR_GOOD", "RESET_N", "CLK_EN", "PLL_LOCK", "SPI_CS_N", "SPI_SCK", "IRQ_N", "LED"]


def events(new_fw: bool):
    t0 = 1.2e-3
    init = [0, 0, 0, 0, 1, 0, 0 if new_fw else 1, 0]
    ev = {i: [] for i in range(8)}
    ev[0] = [t0]
    ev[1] = [t0 + (2.5e-3 if new_fw else 2.000e-3)]
    ev[2] = [t0 + 2.150e-3]
    ev[3] = [] if new_fw else [t0 + 2.150e-3 + 38.4e-6]
    ev[4] = [t0 + 2.6e-3, t0 + 2.6e-3 + 3.2e-6]
    half = 50 * NS if new_fw else 100 * NS
    sck, t = [], t0 + 2.6e-3 + 200 * NS
    for _ in range(16):
        sck += [t, t + half]
        t += 2 * half
    ev[5] = sck
    ev[6] = [t0 + 2.9e-3, t0 + 2.9e-3 + 1.5e-6]
    b = t0 + 3.1e-3
    ev[7] = ([b, b + 10 * NS, b + 90 * NS, b + 100 * NS, b + 400 * NS] if new_fw
             else [b, b + 40 * NS, b + 90 * NS, b + 150 * NS, b + 400 * NS])
    return init, ev


REWIRED = ["LED", "PWR_GOOD", "IRQ_N", "Reset_N", "SPI_SCK", "CLK_EN", "SPI_CS_N", "PLL_LOCK"]


def write(path: Path, new_fw: bool, order: list[str] | None = None) -> None:
    init, ev = events(new_fw)
    names = order or NAMES
    # column k of the file carries the signal whose canonical name matches names[k]
    src = [[n.upper() for n in NAMES].index(n.upper()) for n in names]
    init = [init[i] for i in src]
    ev = {k: ev[i] for k, i in enumerate(src)}
    grid = {i: [round(x / NS) for x in v] for i, v in ev.items()}
    ticks = sorted({k for v in grid.values() for k in v})
    state = init[:]
    rows = ["Time [s]," + ",".join(names), "0.000000000," + ",".join(map(str, state))]
    for k in ticks:
        for i in range(8):
            state[i] ^= grid[i].count(k) & 1
        rows.append(f"{k * NS:.9f}," + ",".join(map(str, state)))
    rows.append(f"{0.008:.9f}," + ",".join(map(str, state)))
    path.write_text("\n".join(rows) + "\n")


if __name__ == "__main__":
    here = Path(__file__).parent
    write(here / "power_up_sequence.csv", new_fw=False)
    write(here / "power_up_new_fw.csv", new_fw=True)
    write(here / "power_up_new_fw_rewired.csv", new_fw=True, order=REWIRED)
