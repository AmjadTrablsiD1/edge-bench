EDGE BENCH FOR WINDOWS
======================

Saleae edge timing: pick a reference edge, get the delay to the first edge
on every other channel, export to Excel.

You need
  1. Python 3.12 or 3.13      https://www.python.org/downloads/windows/
     (Python 3.11 and older will not work: numpy 2.5 needs 3.12+)
  2. Saleae Logic 2           https://www.saleae.com/downloads/
     (only for .sal files; CSV and binary exports work without it)

Install
  1. Unzip this folder anywhere you can write to (e.g. Documents\EdgeBench).
  2. Double-click  "Create desktop shortcut.bat"  (optional, once).
  3. Double-click  "run-edge-bench.bat"  (or the new desktop shortcut).
     The first start installs the Python packages into a private .venv
     folder. The "-offline" zip carries them in .\wheels, so no internet
     or proxy is needed. Later starts take a second or two.

Use
  Edge Bench opens in your web browser. It runs only on this PC
  (127.0.0.1); nothing is sent anywhere.
  Add captures -> pick the reference -> read the table -> Export Excel.
  App tab -> Quit stops it (and the Logic 2 it started in the background).

If Logic 2 is not found
  App tab -> "Choose Logic 2..." and pick Logic.exe
  (normally C:\Program Files\Logic\Logic.exe).

You do NOT need Node.js
  This zip contains the built user interface. Node.js is only needed when
  running from the source folder (git clone); run-edge-bench.bat then builds
  the interface itself on the first start.

If it does not start
  run-edge-bench.bat checks the app in its own window before starting it.
  If something is wrong, that window stays open and says what - send a
  photo of it. Logs are in
  %USERPROFILE%\.local\state\edge-bench\logs   (crash.log, edge-bench.log)
  To start completely fresh: delete the .venv folder next to this file.
  If the PC gets slow: Task Manager -> end "pythonw.exe" and "Logic".

Status
  0.1.2: Edge Bench can no longer take the PC down. Only one copy runs at a
  time, it stops itself before using half of the RAM, and the Logic 2 it
  starts in the background is always closed with it. The launcher window
  shows the app's address for a few seconds.
  If a capture does not open, the red text under it in the list says why.
  Please send that text, plus edge-bench.log from the logs folder above.
