@echo off
setlocal EnableExtensions EnableDelayedExpansion
set SCRIPT_DIR=%~dp0
set VENV_PYTHON=%SCRIPT_DIR%\.venv\Scripts\python.exe
set FALLBACK_PYTHON=C:\Users\13987\anaconda3\envs\chatgpt\python.exe
set FALLBACK_SITE=%SCRIPT_DIR%\.venv\Lib\site-packages

if exist "%VENV_PYTHON%" (
    "%VENV_PYTHON%" -c "import sys; print(sys.version)" >nul 2>nul
    if !errorlevel!==0 (
        "%VENV_PYTHON%" "%SCRIPT_DIR%\main.py"
        set EXIT_CODE=!errorlevel!
        exit /b !EXIT_CODE!
    )
)

if exist "%FALLBACK_PYTHON%" (
    if exist "%FALLBACK_SITE%" (
        set PYTHONPATH=%FALLBACK_SITE%
    )
    "%FALLBACK_PYTHON%" "%SCRIPT_DIR%\main.py"
    set EXIT_CODE=!errorlevel!
    exit /b !EXIT_CODE!
)

echo Python virtual environment is missing or broken.
echo Please install Python 3.10 or newer, then run setup_env.bat.
pause
exit /b 1
endlocal
