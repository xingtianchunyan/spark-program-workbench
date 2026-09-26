@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONUNBUFFERED=1"
set "setup_log=%~dp0setup-notion.log"

rem Prefer the Windows Python launcher.  A Microsoft Store alias named
rem python.exe may exist even when it cannot actually start Python.
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if not errorlevel 1 (
    set "python_cmd=py -3"
    goto run_setup
)
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if not errorlevel 1 (
    set "python_cmd=python"
    goto run_setup
)
python3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if not errorlevel 1 (
    set "python_cmd=python3"
    goto run_setup
)

echo Python 3 not found. Install Python 3.11 or later first.
echo If Python is installed, open Command Prompt and run: py -3 --version
pause
exit /b 1

:run_setup
echo Using Python: %python_cmd%
%python_cmd% -m spark_web.setup
set "setup_code=%errorlevel%"

:setup_done
if "%setup_code%"=="0" goto setup_success
echo.
echo Setup failed. Python was found and started successfully.
if exist "%setup_log%" (
    echo The complete error has been saved to:
    echo %setup_log%
    echo.
    echo ---------------- setup-notion.log ----------------
    type "%setup_log%"
    echo ----------------------------------------------------
) else (
    echo No log was created. Run this command from the workbench folder:
    echo %python_cmd% -m spark_web.setup
)
pause
exit /b %setup_code%

:setup_success
if exist "%setup_log%" del /q "%setup_log%" >nul 2>nul
echo.
echo Setup completed successfully.
pause
exit /b 0
