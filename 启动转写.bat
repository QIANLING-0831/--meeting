@echo off
set "COPILOT_PY=%~dp0.venv\Scripts\python.exe"
set "COPILOT_APP=%~dp0app.py"

if not exist "%COPILOT_PY%" (
    echo Python environment not found: .venv\Scripts\python.exe
    echo See README.md for setup instructions.
    pause
    exit /b 1
)

title Interview Copilot
echo Starting Interview Copilot in your browser...
echo.
"%COPILOT_PY%" "%COPILOT_APP%" %*

echo.
echo Interview Copilot stopped.
pause
