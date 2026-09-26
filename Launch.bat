@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM Check which Python command is available and start only once
where pythonw >nul 2>nul && start "" pythonw "spark_task_tracker.py" && exit /b 0
where python >nul 2>nul && start "" python "spark_task_tracker.py" && exit /b 0
where py >nul 2>nul && start "" py "spark_task_tracker.py" && exit /b 0
where python3 >nul 2>nul && start "" python3 "spark_task_tracker.py" && exit /b 0

cls
echo ============================================
echo   Spark Program Workbench cannot start
echo ============================================
echo.
echo Reason: Python not found on this computer.
echo.
echo Solution options:
echo.
echo [Option 1] Microsoft Store (Fastest)
echo   1. Open Microsoft Store
echo   2. Search for "Python 3.11" and install
echo   3. After installation, double-click this file again
echo.
echo [Option 2] python.org (Recommended)
echo   1. Visit https://www.python.org/downloads/
echo   2. Download Windows installer (64-bit)
echo   3. IMPORTANT: Check "Add Python to PATH" during install
echo   4. After installation, double-click this file again
echo.
echo ============================================
pause
exit /b 1
