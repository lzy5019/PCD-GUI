@echo off
setlocal
set SCRIPT_DIR=%~dp0

if not exist "%SCRIPT_DIR%\.venv\Scripts\python.exe" (
    where py >nul 2>nul
    if %errorlevel%==0 (
        py -3 -m venv "%SCRIPT_DIR%\.venv"
    ) else (
        python -m venv "%SCRIPT_DIR%\.venv"
    )
)

"%SCRIPT_DIR%\.venv\Scripts\python.exe" -m pip install --upgrade pip
"%SCRIPT_DIR%\.venv\Scripts\python.exe" -m pip install -r "%SCRIPT_DIR%\requirements.txt"

echo.
echo Environment is ready.
endlocal
