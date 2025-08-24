@echo off
setlocal enabledelayedexpansion
cd /d %~dp0

REM Prefer venv python if available
set PYEXE=%~dp0teleprompter-venv\Scripts\python.exe
if not exist "%PYEXE%" set PYEXE=python

echo Launching web interface...
"%PYEXE%" web_interface.py

endlocal
