@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run start_windows.bat once to install Job Coach and create your account first.
  pause
  exit /b 1
)
echo Background worker only. Keep this window and computer running.
echo Configure schedules through the normal Job Coach app. Press Ctrl+C to stop.
".venv\Scripts\python.exe" main.py --scheduler-only
pause
