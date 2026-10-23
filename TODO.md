# TODO

## Done
- [x] 2026-10-23 Probed real Logic 2.4.48 via the Automation API: simulated Logic 8 / Pro 8 / Pro 16 all save `.sal` and export binary v0 as `digital_N.bin`; `.sal` is a zip whose `meta.json` carries names, rate and device
- [x] 2026-10-23 Core: readers (binary v0 + v1, CSV, .sal meta), edge engine (searchsorted + parity, glitch state machine), measurement, waveform decimation, Excel writer
- [x] 2026-10-23 Logic 2 session (reuses a user instance on 10430, otherwise launches its own on a free port and closes it on exit) + export cache with LRU cap and size verification against each header
- [x] 2026-10-23 52 pytest tests: hand-worked cases, random captures checked against a naive sample-by-sample search (mutation-checked: two deliberate engine bugs were both caught), binary vs CSV of the same capture, real Logic 2 export reproduces hand exports bit for bit
- [x] 2026-10-23 UI: instrument shell, canvas waveform (zoom/pan/select, Δt markers, reference line), results table + evidence panel, coverage tab, both themes
- [x] 2026-10-23 16 Playwright tests ×2 themes: workflows, axe (no serious/critical), fold probe at 1366×768 and 1440×900, no page scroll, no clipping, reference line pixel-checked at its computed x
- [x] 2026-10-23 Benchmark: 60 s / 16 ch / 5.86 M edges — CSV path ≈ 80 s, Edge Bench 2.0 s cold, 8 ms warm
- [x] 2026-10-23 macOS install (.command, .app, App Launcher tile), Windows zips (online 1 MB, offline 76 MB with wheels for CPython 3.12/3.13), Quit button for the window-less Windows run

## Doing
- [ ] First run on a real Windows work PC (you)

## Next
1. **Repeated cycles with statistics.** Every reference edge is a cycle, with min / mean / max / σ per channel and a *Cycles* sheet. This is the most likely follow-up; `find_edge` already supports it.
2. **Per-channel edge kind** (e.g. D3 rising, D5 falling) instead of one global target edge.
3. **"Nearest edge" rule** (Δt may be negative) for channels that switch before the reference.
4. **Measurement presets** per test setup (named JSON in `~/.config/edge-bench/presets`), so a bench procedure is one click.
5. **Pass/fail limits** per channel (Δt within [min, max]) with red/green cells in Excel.

## Someday
- Logic 1.x `.logicdata` reader.
- Start a live capture from the app (Automation API `start_capture`).
- A single-file Windows build (PyInstaller) so the PC needs no Python.

## Won't do (and why)
- Decoding the `.sal` internal data ourselves: it is an undocumented compressed format that would break silently on a Logic 2 update.
- Raw edge dumps to Excel: Excel caps at about 1 M rows and nobody reads them; the binary is the raw data.

## Untested / limitations
- Windows: `.bat` files, Explorer reveal, `Logic.exe` detection and the offline wheel install were written and packaged on macOS, **never run on Windows**.
- Binary format v1 (newer devices): parser from Saleae's published spec, tested on synthetic files only.
- Real hardware captures: only Logic 2's simulated devices were used.
- Two Logic 2 instances side by side: works on macOS; Windows unknown.
