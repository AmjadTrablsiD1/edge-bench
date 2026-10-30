@echo off
REM Edge Bench on Windows -- double-click to start.
REM First run creates .venv and installs the packages (from .\wheels when
REM present: no internet needed).  Every run checks the app in this console
REM first, so a problem is shown here instead of the app silently not opening.
setlocal
cd /d "%~dp0"
REM Python on Windows reads files as cp1252 unless told otherwise.
set "PYTHONUTF8=1"
title Edge Bench

if exist ".venv\Scripts\pythonw.exe" goto ui

echo Edge Bench: first start, setting up Python packages...
set "PY="
py -3.13 -c "import sys" >nul 2>nul && set "PY=py -3.13"
if not defined PY py -3.12 -c "import sys" >nul 2>nul && set "PY=py -3.12"
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>nul && set "PY=python"
if not defined PY goto nopython

%PY% -m venv .venv || goto failed
REM usebackq + an unquoted relative exe: cmd's quote stripping cannot mangle it
for /f "usebackq delims=" %%T in (`.venv\Scripts\python.exe -c "import sys;print(f'cp{sys.version_info[0]}{sys.version_info[1]}')"`) do set "TAG=%%T"
if exist "wheels\%TAG%" (
  echo Installing from the bundled wheels\%TAG% folder ^(offline^)...
  ".venv\Scripts\python.exe" -m pip install --no-index --find-links "wheels\%TAG%" -r requirements.txt || goto failed
) else (
  echo Installing from the internet ^(pypi.org^)...
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto failed
)

:ui
if exist "ui\dist\index.html" goto check
REM Running from the source folder (git clone): build the UI once if Node is here.
where npm >nul 2>nul || goto noui
echo Building the user interface (first start from the source folder)...
pushd ui
call npm install --no-audit --no-fund || (popd & goto noui)
call npm run build || (popd & goto noui)
popd
if not exist "ui\dist\index.html" goto noui

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

:noui
echo.
echo The user interface (ui\dist) is missing and could not be built.
echo Use the release zip EdgeBench-...-windows.zip (it contains the built UI),
echo or install Node.js from https://nodejs.org and run this file again.
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
