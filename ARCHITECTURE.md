# Architecture — Edge Bench

*Written before the code, kept true after every change.*

## What this app is for

Saleae Logic 2 captures (`.sal`) from the bench, opened offline. Pick a
**reference channel and edge** (or let the app take the first channel that
changes), and Edge Bench reports, for every other channel, the **first edge
after that reference** and the time between them. The results go into a table,
a waveform view that shows the edges it picked, and an **Excel** workbook.

It replaces the CSV export, which is too large and too slow for long captures.
Edge Bench has Logic 2 export **binary** data in the background, then
memory-maps the files and binary-searches them, so there is no text to parse
and no full scan.

Works with every Saleae model that Logic 2 supports (Logic 8, Pro 8, Pro 16 and
newer). It reads binary export formats v0 and v1, and a CSV fallback, because the
format is chosen by the device, not by us.

## Layers

```
ui/  ──HTTP /api──▶  server/  ──imports──▶  app/                shared/constants.json
(React + TS,         (FastAPI: routes,     (pure Python:          shared/themes.json
 canvas waveform)     jobs, no maths)       readers, edges,        (read by both sides)
                                            measure, excel,
                                            logic2 session)
```

Dependencies point one way. `app/` runs and is tested with the server absent.
`app/` never imports FastAPI; `server/` never does arithmetic on edges.

Inside `app/`:

| Module | Job |
|---|---|
| `readers/` | Registry of capture formats → one `Capture` object. `binary.py` (v0 + v1), `csv_digital.py`, `sal.py` (`meta.json` straight from the zip — names, rate, device, without Logic 2) |
| `capture.py` | `Capture` / `Channel` / `Segment`: edge times (float64, memory-mapped) + initial level per segment. Level alternates at each edge, so direction is parity — no per-edge storage |
| `edges.py` | `find_edge()` — binary search to t, parity for direction, optional glitch filter (state machine over the few edges near t) |
| `measure.py` | Reference resolution (manual / auto-first-change) and per-channel Δt. Returns plain dataclasses |
| `waveform.py` | Decimation for the viewport: exact edges when few, per-pixel "busy" buckets when many |
| `compare.py` | Baseline (A) against new (B): same settings, each aligned on its own reference; per-channel metrics over a common span; verdicts same / changed / critical with reasons |
| `units.py` | Time as text for sentences the core writes (reasons, Excel notes), same digits rule as the UI |
| `excel.py` | Workbook writers: results (Results, Batch matrix, Settings) and comparison (Comparison, Baseline, New, Settings) |
| `logic2.py` | Find / connect to / launch Logic 2, export binary, verify the files are complete |
| `cache.py` | Exported binaries keyed by (path, size, mtime); LRU size cap |
| `dialogs.py` | Native file dialog in a child process (tkinter), because a browser cannot hand over a path |

## Data flow — "measure a .sal"

1. **Add files** → `POST /api/dialog/open` → `dialogs.open_files()` returns paths
   → `POST /api/files` registers them.
2. The server reads `meta.json` from each `.sal` straight away (channel names,
   rate, device), and a background job asks `logic2.export_binary()` for the
   edges. That job is cached, so each file is exported only once.
3. The UI polls `GET /api/files` until the file is `ready`, then calls
   `POST /api/measure {file_id, settings}` → `measure.run()` → a JSON result.
4. The waveform asks `GET /api/waveform?file_id&t0&t1&px` → `waveform.decimate()`.
5. **Export Excel** → `POST /api/export` → `excel.write()` → saved next to the
   capture (or to a chosen path) and revealed.

## Where state lives

| State | Home | Survives restart? |
|---|---|---|
| exported binaries | `~/.cache/edge-bench/<key>/` | yes (LRU-capped) |
| file list, results | server memory | no |
| measurement settings, theme, last folder | browser `localStorage` | yes |
| Logic 2 path override | `~/.config/edge-bench/settings.json` | yes |

## How to add a new X

**A new capture format** (say the Logic 1.x `.logicdata`):
1. Write `app/readers/<name>.py` with `sniff(path) -> bool` and `load(path) -> Capture`.
2. Add it to `app/readers/__init__.py::REGISTRY`.
3. Add its extension to `constants.json → files.extensions`. The UI dialog filter follows.

**A new measurement column** (say pulse width after the edge):
1. Add the field to `measure.ChannelResult` and compute it in `measure.run()`.
2. Add the column to `constants.json → excel.results_columns` and `ui.result_columns`.
3. Test it in `tests/test_measure.py` with two independent examples.

**A new comparison check** (e.g. "frequency of a clock"):
1. Add the field to `compare.Metrics` and compute it in `compare.metrics_for()` (vectorised over the span, like `span_activity`).
2. Add the rule to `compare._compare_row()`: a `Reason` with severity and a sentence; its tolerance goes in `Tolerance` and `constants.json → compare`.
3. Add the A/B columns in `excel.write_comparison` and a line in `CompareResults.tsx → Detail`. Test it in `tests/test_compare.py` against a naive computation.

**Repeated cycles** (the planned next mode):
1. `measure.run_cycles()` loops `find_edge()` over every reference edge.
   `Capture` already supports it, so nothing below that changes.
2. Add a `cycles` sheet writer to `excel.SHEETS`.
3. Add a mode to the settings registry in `constants.json → measure.modes`.

## Decisions

| Decision | Why | Rejected alternative |
|---|---|---|
| Logic 2 binary export via Automation API | Only Logic 2 can read `.sal` (the in-zip data is an undocumented compressed format). Binary is 8 bytes per edge per channel and needs no parsing | CSV: one text row per edge on *any* channel, slow to write and to read. Decoding `.sal` ourselves: undocumented, would break on the next Logic release |
| Memory-map + `searchsorted` | The first edge after t is O(log n). Nothing is loaded that is not looked at | Loading the whole channel into a list |
| Direction from parity, not stored | The level flips at each edge (Saleae's own definition), so `initial ^ (i & 1)` is free | A per-edge direction array |
| Launch our own Logic 2 on a free port, or reuse one already on 10430 | The user can keep their own Logic 2 window. Our instance is closed when the app exits | `Manager.launch()` — it hard-codes the port and needs pywin32 job objects on Windows |
| Native dialog in a child process | Browsers never reveal file paths, and Logic 2 needs a path. tkinter on a non-main thread crashes on macOS, so it runs in its own process | Uploading a 1 GB `.sal` into the server |
| Glitch filter as a state machine around t | Exact (same as a hardware deglitch), and it only visits the handful of edges near the event | Pre-filtering the whole channel |
| FastAPI + browser UI | His standard stack; the core stays testable | Electron (150 MB per app), Qt |
| Digits from the sample period | A Δt at 10 ns resolution shows 2.1884 ms, not 2.19 ms; Excel values are rounded the same way, so no 2188.3999999 | A fixed 3-significant-digit formatter (hides real precision) |
| Quit button + `/api/quit` | On Windows the app runs under pythonw.exe with no window, so closing the tab cannot stop it | Killing it from Task Manager |
| Windows zip with prebuilt UI + wheels | Work PCs have no Node.js and may sit behind a proxy | Building on the PC |

## Known sharp edges

- Compare: "time high" is only compared on channels with >= `duty_min_edges` edges. On a single transition it restates Δt, and before this rule a shifted edge was flagged twice, so raising the delay tolerance could never clear it.
- UI requests use "newest request wins" (an id counter), not AbortController. An abort landing while a finished response was still being read once lost a comparison result.

- numpy 2.5 has no Python 3.11 wheels: the Windows launcher accepts Python 3.12+ only.
- `logic2-automation` depends on pywin32 on Windows; `make_windows_package.sh` adds that wheel by hand, because `pip download --platform` does not evaluate the marker for the target platform.

- `export_raw_data_binary` returns **before** the files are fully written
  (reported on the Saleae forum). `logic2.wait_complete()` checks every file
  against the size its own header promises. Do not remove that check.
- Logic 2 timestamps are relative to its time base (usually capture start).
  Every Δt is a difference, so this never matters, but absolute times are
  shown only as "t in capture".
- Format v1 files can have **gaps between chunks**. An edge is never searched across
  a gap, and the level at the start of each chunk comes from that chunk's own
  `initial_state`.
- Excel has 1,048,576 rows. Results are one row per channel per file, so this
  is never reached — but never export raw edges to Excel.
