@echo off
setlocal enabledelayedexpansion
cd /d %~dp0

REM Prefer venv python if available
set PYEXE=%~dp0teleprompter-venv\Scripts\python.exe
if not exist "%PYEXE%" set PYEXE=python

echo Launching presenter key inspector...
echo Press buttons on your presenter. Press 'q' to quit.
"%PYEXE%" teleprompter.py check_keys

endlocal
