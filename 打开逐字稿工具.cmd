@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Please install the Python environment as described in README.md.
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "scripts\window.py"
