@echo off
rem Start Kliprix on Windows: double-click this file or run scripts\start.bat (stop with Ctrl+C).
rem Any Python 3 can run the launcher; it picks Python 3.11-3.12 for the backend itself.
cd /d "%~dp0.."
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 scripts\run.py %*
  goto :done
)
where python >nul 2>nul
if %errorlevel%==0 (
  python scripts\run.py %*
  goto :done
)
echo [kliprix] ERROR: Python 3 was not found. Install Python 3.12 from https://www.python.org/downloads/
exit /b 1
:done
if errorlevel 1 pause
