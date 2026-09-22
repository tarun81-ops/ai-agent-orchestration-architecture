@echo off
REM ---------------------------------------------------------------------------
REM  Launch the Multi-Agent Kernal GUI (gui.py) on Windows.
REM  Double-click this file, or run it from PowerShell/cmd.
REM  Tip: replace "python.exe" with "pythonw.exe" below to start without a
REM       console window (errors then go unseen, so only do it once it works).
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

if not exist "gui.py" (
    echo [!] gui.py was not found in "%CD%".
    pause
    exit /b 1
)

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" -c "import customtkinter" >nul 2>&1
if errorlevel 1 (
    echo [*] customtkinter is missing. Installing the GUI dependencies...
    "%PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [!] Dependency install failed. Fix the messages above, then run this file again.
        pause
        exit /b 1
    )
)

echo [*] Starting the Multi-Agent Kernal GUI... (close the window to stop)
"%PY%" gui.py
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
    echo.
    echo [!] The GUI exited with code %EXITCODE%. See the messages above.
    pause
)

endlocal & exit /b %EXITCODE%
