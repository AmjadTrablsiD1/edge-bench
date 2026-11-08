# Changelog

What changed in each version, newest first. The app shows these sections as
"What's new" before an update. Each heading is `## <version> — <date>`, and the
bullets underneath are written for the people who use Edge Bench.

## 0.3.1 — 2026-11-08
- Large binary exports measure and draw quickly without copying the whole channel into memory (a 2 GB channel: 2.2 GB → 0.3 GB, seconds → instant).
- If Edge Bench stops (for example its memory guard ends it), the page now says so instead of going quiet.

## 0.3.0 — 2026-11-08
- Updates in place: set an update folder (a network share works), and Edge Bench offers each new version itself; or drag a zip onto "Update Edge Bench.bat".
- Each update checks itself before it stays; if anything fails, the old version is put back automatically.
- "Roll back" returns to the previous version; settings, logs and the cache are never touched.
- Python packages are reinstalled only when a version needs different ones.

## 0.2.0 — 2026-11-08
- .sal captures are read directly: Logic 2 is no longer needed, and there is no huge binary export.
- A capture is unpacked once (about 1–3 bytes per edge) and then opens instantly; only what is measured or shown is decoded.
- A .sal in a layout Edge Bench does not know is still exported through Logic 2; the Coverage tab says so.

## 0.1.3 — 2026-11-07
- Fixed: a large export could be deleted right after it was made ("exported but would not open").
- Compare no longer loads whole channels into memory.
- An export shows how many GB are written and stops before the disk is full.

## 0.1.2 — 2026-10-30
- Edge Bench can no longer take the PC down: only one copy runs, and it stops itself before using half the RAM.
- Large CSV files load about 15 times faster with a fraction of the memory.
- The launcher window shows the app's address.

## 0.1.1 — 2026-10-29
- Fixed: on Windows the app closed silently at start-up.

## 0.1.0 — 2026-10-24
- First version: reference edge to every channel, Excel export, Compare A ↔ B.
