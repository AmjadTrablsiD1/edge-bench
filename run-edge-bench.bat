@echo off
REM Edge Bench on Windows -- double-click to start.
REM WRITTEN ON macOS, NOT YET RUN ON WINDOWS.  First run creates .venv and
REM installs the packages (from .\wheels when present: no internet needed).
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" goto run

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

:run
if not exist "ui\dist\index.html" (
  echo The user interface is missing ^(ui\dist^). Use the release zip, or build it: cd ui ^&^& npm install ^&^& npm run build
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" main.py
exit /b 0

:nopython
echo.
echo Python 3.12 or 3.13 was not found.
echo Install it from https://www.python.org/downloads/windows/  (the default options are fine),
echo then double-click this file again.
pause
exit /b 1

:failed
echo.
echo Setting up failed (see the messages above). Removing the half-made .venv so the next try starts clean.
rmdir /s /q .venv
pause
exit /b 1
