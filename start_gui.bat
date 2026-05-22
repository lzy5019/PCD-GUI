@echo off
setlocal
set SCRIPT_DIR=%~dp0

if not exist "%SCRIPT_DIR%\.venv\Scripts\python.exe" (
    echo Python virtual environment not found.
    echo Please run setup_env.bat first.
    pause
    exit /b 1
)

"%SCRIPT_DIR%\.venv\Scripts\python.exe" "%SCRIPT_DIR%\main.py"
endlocal
