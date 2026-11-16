@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 bootstrap.py
  goto finished
)
where python >nul 2>nul
if not errorlevel 1 (
  python bootstrap.py
  goto finished
)
echo Python was not found.
echo Install Python 3.12 or 3.13 from https://www.python.org/downloads/windows/
echo Include the Python launcher, then run this file again.
:finished
pause
endlocal
