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
| `compare.py` | Pairs channels (`pair_channels`: by number, or by name with the reference and exclusions translated to B's numbers), then baseline (A) against new (B): same settings, each aligned on its own reference; per-channel metrics over a common span; verdicts same / changed / critical with reasons |
| `units.py` | Time as text for sentences the core writes (reasons, Excel notes), same digits rule as the UI |
| `excel.py` | Workbook writers: results (Results, Batch matrix, Settings) and comparison (Comparison, Baseline, New, Settings) |
| `logic2.py` | Find / connect to / launch Logic 2, export binary, verify the files are complete |
| `cache.py` | Exported binaries keyed by (path, size, mtime); LRU size cap |
| `dialogs.py` | Native file dialog in a child process (tkinter), because a browser cannot hand over a path |

## Data flow — "measure a .sal"

1. **Add files** → `POST /api/dialog/open` → `dialogs.open_files()` returns paths
   → `POST /api/files` registers them.
2. The server reads `meta.json` from each `.sal` straight away (channel names,
   rate, device). A background job then reads the edges **directly**
   (`readers/sal_native.py`): each channel file is inflated once into the
   cache and indexed, and edge times are decoded on demand. Only a `.sal` in a
   layout the direct reader does not know goes through `logic2.export_binary()`
   (the capture's notes say why).
3. The UI polls `GET /api/files` until the file is `ready`, then calls
   `POST /api/measure {file_id, settings}` → `measure.run()` → a JSON result.
4. The waveform asks `GET /api/waveform?file_id&t0&t1&px` → `waveform.decimate()`.
5. **Export Excel** → `POST /api/export` → `excel.write()` → saved next to the
   capture (or to a chosen path) and revealed.

## Where state lives

| State | Home | Survives restart? |
|---|---|---|
| unpacked .sal channels + index, or exported binaries | `~/.cache/edge-bench/<key>/` (key includes the kind) | yes (LRU-capped) |
| file list, results | server memory | no |
| measurement settings, theme, last folder | browser `localStorage` | yes |
| Logic 2 path override, update folder | `~/.config/edge-bench/settings.json` | yes |
| program files | the install folder (an update replaces them) | until the next update |
| previous version, staged update, journal, last result | `<install>/.update/` | yes |
| Python packages (+ the requirements hash they match) | `<install>/.venv/` | yes, reinstalled when requirements change |

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

## The direct .sal reader (`app/readers/sal_native.py`)

Logic 2's channel files (`digital-N.bin` inside the `.sal` zip) are
undocumented; the layout is in the module docstring. In short: a header
(rate, an optional trim window), then chunks `[a, b)` that each hold run
lengths, which add up to exactly `b - a`. Each run is 1 to 9 bytes, big-endian,
7 bits per byte.

- **How it was decoded, and how it is checked.** `scripts/make_sal_corpus.py`
  records captures with Logic 2's simulated devices, together with Logic 2's
  own binary export of each; the result is `tests/fixtures/sal_corpus`
  (Logic 2.4.48). The checks:
  - every decoded edge sits on Logic 2's sample;
  - re-encoding every chunk gives Logic 2's bytes exactly;
  - measurement and waveform results equal the export path's;
  - `tests/salgen.py`, an independent encoder, adds long runs, trim windows,
    split chunks and millions of edges.

  After a Logic 2 update, re-record the corpus and run `tests/test_sal_native.py`.
- **Never guess.** Any mismatch raises `NativeFormatError`, and `sal.load` uses
  the Logic 2 export instead:
  - an unknown version or header flag;
  - a gap between chunks;
  - runs that don't add up to the chunk;
  - trailing bytes;
  - glitch filter on;
  - an unknown time origin.
- **Memory follows what is looked at, not the edge count.**
  - The raw file is memory-mapped.
  - Indexing works in 256 kB blocks, because decoding needs about 150 times its
    input in temporary arrays.
  - Every 4096th edge gets a *mark* (the byte offset and sample of its token),
    so any edge or `searchsorted` decodes at most 4096 tokens.
  - Decoded stretches share a 256 MB LRU.
  - The waveform calls a pixel busy without decoding when a mark lies strictly
    inside it.
  - Measured with 100 M edges: indexing ran at 9 M edges/s with 375 MB peak RSS,
    149 MB of which was the mapped file. A measurement took 12 ms and a full
    view 1 ms.
- **Times** are `(sample - trigger_sample) / rate`, correctly rounded. Logic 2's
  export differs by a few ULPs on some edges (it accumulates in floating
  point): about 1e-19 s, on the same sample.
- **Not seen in any Logic 2 file yet:** runs of 4 bytes or more (over about
  1 M samples, so a channel idle for longer than that at high sample rates).
  The decoder reads them as the same scheme extended, and the synthetic tests
  cover that. If Logic 2 writes them differently, the chunk sums fail and the
  export takes over.
- **Callers** read times only with `len`, `[i]`, `[a:b]` and `times.searchsorted`.
  `np.searchsorted(times, …)` or `np.asarray(times)` would decode a whole
  channel.

## Updates (`updater.py`)

`updater.py` is standard-library only, because it runs before the packages
exist. An update runs in this order:

1. **Stage:** unzip into `.update/staged` and check that it is Edge Bench, is
   newer, and carries its own `updater.py`.
2. **Hand over:** run the *staged* `updater.py`, so each release installs
   itself the way it wants. The running app quits; the installer waits for its
   process to end.
3. **Swap:** move this version's files into `.update/previous` and the new
   ones in. These are renames on one disk, so they are instant. Each phase is
   written to `.update/journal.json` before it starts.
4. **Dependencies:** `pip` runs only if the hash of `requirements.txt` differs
   from `.venv/edge-bench-requirements.sha256`. It installs from `wheels/`
   when the package carries them.
5. **Check:** `main.py --check` of the new version. If it fails, or anything
   else does (Ctrl+C included), `restore()` puts the old files back and
   `.update/last.json` records why.
6. **Start the new version.**

Crash safety: `restore()` can finish or undo any partly done swap, and running
it twice does no harm. The launcher runs `updater.py --prepare` before every
start, and that calls it whenever a journal is not marked `done`.
`tests/test_updater.py` cuts the power after every possible number of moves
and checks that the install comes back intact.

Rules for future versions:

- **Never write user data into the install folder.** It is replaced, and only
  `.venv`, `.update` and `.git` survive. Settings, logs and caches belong in
  the `paths` folders of `constants.json`.
- **A release that changes stored data** (settings keys, cache layout) adds a
  step to `updater.migrate()`. That runs once, in the new version, after its
  check. A rollback does not undo it, so keep data readable by the previous
  version, or bump `cache.key_version` and let the cache rebuild.
- **New top-level files** need nothing extra:
  - `make_windows_package.sh` lists every entry in `MANIFEST.json`.
  - The next update replaces exactly those entries.
  - A file the user put in the folder is never touched.
- **Changing how updates work** is safe: the *new* version's `updater.py` runs
  the update, so a fix ships with the release that needs it. An old updater
  only ever stages and hands over.
- **Every release** needs a `CHANGELOG.md` section. `scripts/release.sh`
  enforces it, and the app shows it to users before they install.

## Known sharp edges

- **Always pass `encoding="utf-8"`** to every text read/write/subprocess. Windows reads text as cp1252 by default and constants.json holds µ, Δ, ⏱ — 0.1.0 died on its first line on Windows 11, invisibly under pythonw. `tests/test_windows_readiness.py` fails the build if any app code relies on the default.
- Local HTTP/gRPC must bypass proxies (`ProxyHandler({})`, `grpc.enable_http_proxy=0`): work PCs set proxies that cannot reach 127.0.0.1.
- `.bat` files stay CRLF (`.gitattributes`); cmd.exe can miss GOTO labels in LF files.
- **Never read a capture into Python objects.** The first CSV reader used `pd.read_csv(dtype=str)`: about 5.5x the file size in RAM and 50 s per 250 MB. On Windows 11 a large work file took a 64 GB PC to 100 % RAM / 99 % CPU. The reader now goes chunk by chunk and keeps only edge times (3.2 s, 233 MB peak for the same file). `tests/test_safety.py` measures the peak against the file size.
- **One instance per user**: an OS file lock (`state_dir/instance.lock`, released by the OS however the process ends). A port file alone cannot tell "busy" from "dead", and a busy first instance once got a second one started beside it. A second start opens the first, or says it is busy; it never serves.
- **Memory guard** (`app/guard.py`): above `guard.max_private_frac` of physical RAM in *private* memory (memory-mapped captures do not count), the app writes crash.log, shows why and exits. Windows and Linux only.
- **Logic 2 is a process tree** (Electron). On Windows it runs in a job object that is closed on drop/exit, so no headless Logic 2 is left holding memory. `terminate()` alone stops only the main process.
- **The cache keeps the export it just made**, even when that alone is over `cache.max_gb` (0.1.2 evicted it and then could not open it). Older entries make room instead.
- **No per-edge arrays over a whole span**: `compare.span_activity` walks in `span_chunk_edges` chunks. A span can hold 10^9 edges; one float64 per edge for each of `e`, `bounds`, `widths` and `levels` is about 32 GB.
- **Exports are watched** (`logic2.ExportWatch`): GB written goes to the progress line, the export stops below `min_free_disk_gb`, and removing the capture stops it. Only a Logic 2 we started can be stopped. On Windows that Logic 2's job may commit at most `job_memory_frac` of RAM.
- Retry is refused while a capture is loading; removing a loading capture cancels it at the reader's next progress step.

- Compare rows are not unique by channel number once channels are matched by name (B's D0 can sit beside A's D0). The UI keys rows with `rowId()` (A's number, or -(B's number + 1)); never key them by `index` alone.

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
