@echo off
setlocal enabledelayedexpansion

REM Start the desktop teleprompter on Windows.
REM Usage:
REM   start_teleprompter.bat [path\to\prompt.txt]
REM Optional env flags:
REM   set TP_WINDOWED=1   -> adds --windowed
REM   set TP_NO_CAMERA=1  -> adds --no-camera

cd /d "%~dp0"

REM Build flags from environment
set "WINDOWED="
set "NO_CAMERA="
if /I "%TP_WINDOWED%"=="1" set "WINDOWED=--windowed"
if /I "%TP_NO_CAMERA%"=="1" set "NO_CAMERA=--no-camera"

REM Resolve prompt argument or pick the first .txt in prompts
set "PROMPT_ARG="
if not "%~1"=="" (
	set "PROMPT_ARG=%~1"
) else (
	for %%F in ("prompts\*.txt") do (
		set "PROMPT_ARG=%%~fF"
		goto :have_prompt
	)
	echo No .txt files found in "%~dp0prompts". Provide a file or add one to the prompts folder.
	exit /b 1
)
:have_prompt

REM Choose Python: prefer venv, else py -3, else python
set "PY_CMD=%~dp0teleprompter-venv\Scripts\python.exe"
set "PY_ARGS="
if not exist "%PY_CMD%" (
	where py >nul 2>&1
	if %ERRORLEVEL%==0 (
		set "PY_CMD=py"
		set "PY_ARGS=-3"
	) else (
		set "PY_CMD=python"
	)
)

echo Launching teleprompter...
"%PY_CMD%" %PY_ARGS% "%~dp0teleprompter.py" "%PROMPT_ARG%" %WINDOWED% %NO_CAMERA%
set "RC=%ERRORLEVEL%"
echo Teleprompter exited with code %RC%
exit /b %RC%
