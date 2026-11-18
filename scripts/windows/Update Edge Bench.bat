@echo off
REM Install a new Edge Bench version over this one, keeping this one for a rollback.
REM   Double-click it: the newest version from GitHub (or from the update
REM   folder set in the app, when that one is newer).  Or drag an
REM   EdgeBench-...-windows.zip onto this file.
REM   "Update Edge Bench.bat" --rollback   goes back to the version before.
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
title Edge Bench update
if not exist ".venv\Scripts\python.exe" (
  echo Start Edge Bench once first ^(run-edge-bench.bat^), then update.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" updater.py %*
if errorlevel 1 (
  echo.
  echo Nothing was changed, or the previous version was put back -- see above.
  pause
  exit /b 1
)
timeout /t 5 >nul
