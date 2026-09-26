@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONUNBUFFERED=1"
set "launch_log=%~dp0launch-web.log"
set "failure_marker=%~dp0launch-web.failed"
set "runtime_file=%~dp0launch-web.runtime"
set "server_args="
if /I "%~1"=="--silent" set "server_args=--no-browser"
set "SPARK_LAUNCH_NONCE=%~2"
if not defined SPARK_LAUNCH_NONCE set "SPARK_LAUNCH_NONCE=%RANDOM%-%RANDOM%-%RANDOM%"

if exist "%failure_marker%" del /q "%failure_marker%" >nul 2>nul
if exist "%runtime_file%" del /q "%runtime_file%" >nul 2>nul
>"%launch_log%" echo Spark Program Workbench launcher
>>"%launch_log%" echo Started: %date% %time%

rem Use the same verified launcher order as Setup-Notion.bat.  Merely finding
rem python.exe is not sufficient because Windows may expose a Store alias.
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if not errorlevel 1 (
    set "python_cmd=py -3"
    goto run_server
)
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if not errorlevel 1 (
    set "python_cmd=python"
    goto run_server
)
python3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if not errorlevel 1 (
    set "python_cmd=python3"
    goto run_server
)

>>"%launch_log%" echo ERROR: Python 3.11 or later was not found.
>"%failure_marker%" echo 1
if /I not "%~1"=="--silent" (
    echo Python 3.11 or later was not found.
    echo See: %launch_log%
    pause
)
exit /b 1

:run_server
>>"%launch_log%" echo Using Python: %python_cmd%
>>"%launch_log%" echo Working directory: %cd%
if /I not "%~1"=="--silent" (
    echo Starting Spark Program Workbench with %python_cmd% ...
    echo Keep this window open while using the workbench.
)
%python_cmd% -m spark_web.server %server_args% --auto-shutdown --runtime-file "%runtime_file%" >>"%launch_log%" 2>&1
set "server_code=%errorlevel%"
>>"%launch_log%" echo.
>>"%launch_log%" echo Server exited with code %server_code% at %date% %time%.
>"%failure_marker%" echo %server_code%
if "%server_code%"=="0" if /I not "%~1"=="--silent" (
    echo.
    echo The last browser page was closed. The local port has been released.
    timeout /t 2 /nobreak >nul
    exit /b 0
)
if /I not "%~1"=="--silent" (
    echo.
    echo The local service stopped. See the complete log at:
    echo %launch_log%
    echo.
    type "%launch_log%"
    pause
)
exit /b %server_code%
