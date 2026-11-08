@echo off
REM Edge Bench on Windows -- double-click to start.
REM First run creates .venv; updater.py --prepare then keeps its packages in
REM step with requirements.txt (after every update too).  Every run checks the
REM app in this console first, so a problem is shown here instead of the app
REM silently not opening.
setlocal
cd /d "%~dp0"
REM Python on Windows reads files as cp1252 unless told otherwise.
set "PYTHONUTF8=1"
title Edge Bench

if exist ".venv\Scripts\python.exe" goto prepare

echo Edge Bench: first start, setting up Python...
set "PY="
py -3.13 -c "import sys" >nul 2>nul && set "PY=py -3.13"
if not defined PY py -3.12 -c "import sys" >nul 2>nul && set "PY=py -3.12"
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>nul && set "PY=python"
if not defined PY goto nopython
%PY% -m venv .venv || goto failed

:prepare
REM updater.py --prepare: undo an update that was cut off, install the Python
REM packages whenever requirements.txt changed (from .\wheels when present: no
REM internet needed), and build the UI in a source folder.
".venv\Scripts\python.exe" updater.py --prepare || goto preparefailed

:check
".venv\Scripts\python.exe" main.py --check
if errorlevel 1 goto checkfailed
start "" ".venv\Scripts\pythonw.exe" main.py
REM Wait here until it answers and show its address: if the browser does not
REM open, the address is on screen; if it never answers, this window stays.
".venv\Scripts\python.exe" main.py --wait || goto waitfailed
timeout /t 8 >nul
exit /b 0

:nopython
echo.
echo Python 3.12 or 3.13 was not found.
echo Install it from https://www.python.org/downloads/windows/  (the default options are fine),
echo then double-click this file again.
pause
exit /b 1

:preparefailed
echo.
echo Setting up failed -- the message above says why. Starting again retries.
pause
exit /b 1

:checkfailed
echo.
echo Edge Bench could not start -- the message above says why.
echo Details are also in %USERPROFILE%\.local\state\edge-bench\logs\crash.log
pause
exit /b 1

:waitfailed
echo.
echo Edge Bench was started but did not answer. The files named above say why.
pause
exit /b 1

:failed
echo.
echo Setting up failed (see the messages above). Removing the half-made .venv so the next try starts clean.
rmdir /s /q .venv
pause
exit /b 1
