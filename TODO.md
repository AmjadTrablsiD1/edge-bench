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
- [x] 2026-10-24 Fixed: install.sh's `--exclude dist` also dropped `ui/dist` (installed app showed {"detail":"Not Found"}); a missing UI now says so
- [x] 2026-10-24 **Compare A ↔ B**: same settings on two captures, per-channel verdict (same / changed / critical) with reasons, A/B overlay view, comparison workbook. Tested on a planted "new firmware" fixture (5 differences, all found), naive cross-checks for the span metrics, 14 new pytest + 6 new Playwright tests
- [x] 2026-10-24 Compare: **match channels by name** (case/space-insensitive; reference and exclusions follow the name; duplicates paired in channel order; `D1 ↔ D3` labels). Tested on a rewired fixture: by name reproduces the original verdicts exactly
- [x] 2026-10-29 **Windows 11 first run failed** (0.1.0): cp1252 decode of constants.json at import, hidden by pythonw. 0.1.1: explicit UTF-8 everywhere, PYTHONUTF8=1 in the launcher, `main.py --check` run visibly before every start, crash → message box + crash.log, proxies bypassed for local HTTP/gRPC, CRLF .bat + .gitattributes, UI auto-build from a source checkout. Guarded by tests/test_windows_readiness.py (mutation-checked)

- [x] 2026-10-30 **Windows 11, 0.1.1**: started, but opening a `.sal` and a binary export failed, and a retry took a 64 GB PC to full RAM / 99 % CPU. 0.1.2: chunked CSV reader (252 MB file: 50 s / 1.44 GB → 3.2 s / 233 MB, fuzzed against a row-by-row reference), single-instance OS lock, memory guard, Logic 2 in a Windows job object, no Retry while loading, cancel on remove, `main.py --wait` in the launcher. 11 tests in tests/test_safety.py (mutation-checked against the old reader)

## Doing
- [ ] Windows 11 run with 0.1.2 (you): error text under the capture + edge-bench.log + Task Manager (which process held the memory: pythonw or Logic)
- [ ] **In-place updates** (paused until 0.1.2 runs): `updater.py` stage → swap → deps → check → rollback, an Updates well in the App tab, an update folder on a share, scripts/release.sh, CHANGELOG.md

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
