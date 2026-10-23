@echo off
REM Puts an "Edge Bench" shortcut with its icon on the Desktop.  NOT YET RUN ON WINDOWS.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop') + '\Edge Bench.lnk');" ^
  "$s.TargetPath = '%~dp0run-edge-bench.bat';" ^
  "$s.WorkingDirectory = '%~dp0';" ^
  "$s.IconLocation = '%~dp0branding\icon.ico';" ^
  "$s.WindowStyle = 7;" ^
  "$s.Description = 'Saleae edge timing to Excel';" ^
  "$s.Save()"
if errorlevel 1 (echo Could not create the shortcut. & pause & exit /b 1)
echo Shortcut "Edge Bench" created on your Desktop.
timeout /t 3 >nul
