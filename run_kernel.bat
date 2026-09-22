@echo off
REM ===========================================================================
REM  Multi-Agent Kernal (ADK) - launcher for the web UI.
REM
REM  * double-click this file, or
REM  * run it from a terminal, or
REM  * make a desktop shortcut to it (see README.md, "Desktop shortcut")
REM
REM  What it does: activates the project virtual environment, starts the ADK
REM  web UI on http://localhost:8000, and opens that address in your browser.
REM
REM  Offline demo without an API key:
REM      set KERNEL_MOCK=1        (in the terminal, before running this file)
REM ===========================================================================
setlocal
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
set "ADK=.venv\Scripts\adk.exe"

if not exist "%PY%" (
    echo [!] .venv was not found. Create it first:
    echo       python -m venv .venv
    echo       .venv\Scripts\python -m pip install -r requirements.txt
    pause
    exit /b 1
)

if not exist "kernel_agent\agent.py" (
    echo [!] kernel_agent\agent.py was not found. Run this file from the project folder.
    pause
    exit /b 1
)

if not exist "%ADK%" (
    echo [!] google-adk is not installed in .venv. Install it with:
    echo       .venv\Scripts\python -m pip install -r requirements.txt
    pause
    exit /b 1
)

REM --- mock mode needs no key at all, so skip the key check ------------------
if /i "%KERNEL_MOCK%"=="1" goto run
if /i "%KERNEL_MOCK%"=="true" goto run

REM --- make sure a real API key is present (a placeholder does not count) ----
"%PY%" -c "import os,sys;from dotenv import dotenv_values;v=[(dotenv_values('.env').get('GOOGLE_API_KEY') or '').strip(),(dotenv_values('kernel_agent/.env').get('GOOGLE_API_KEY') or '').strip()];ok=any(x and x!='your_key_here' for x in v) or (os.environ.get('GOOGLE_API_KEY','').strip() not in ('','your_key_here'));sys.exit(0 if ok else 3)"
if errorlevel 3 (
    echo [!] No Google API key found yet.
    echo.
    echo     1. Open the file  .env  in this folder with Notepad.
    echo     2. Replace the line  GOOGLE_API_KEY=your_key_here
    echo        with your own free key from https://aistudio.google.com/apikey
    echo     3. Save the file and run this launcher again.
    echo.
    echo     Or demo everything offline with fake agents:  set KERNEL_MOCK=1
    pause
    exit /b 1
)

:run
REM --- keep the agent folder's .env in sync with this folder's .env ----------
REM  The ADK CLI loads kernel_agent\.env when it starts the agent, and a later
REM  .env load wins - so a stale placeholder in there would shadow your real key.
if exist ".env" copy /y ".env" "kernel_agent\.env" >nul

REM --- open the browser as soon as the server answers -------------------------
REM  (it waits up to 30 seconds for http://localhost:8000, then opens it)
start "" /min powershell -NoProfile -WindowStyle Hidden -Command "for ($i=0; $i -lt 60; $i++) { try { Invoke-WebRequest -Uri 'http://localhost:8000' -UseBasicParsing -TimeoutSec 2 | Out-Null; break } catch { Start-Sleep -Milliseconds 500 } }; Start-Process 'http://localhost:8000'"

echo.
echo [*] Starting the ADK web UI for the kernel_agent agent...
echo     URL:  http://localhost:8000
echo     Pick "kernel_agent" in the dropdown, type a task, and press Enter.
echo     Press Ctrl+C in this window to stop the server.
echo.

".venv\Scripts\adk.exe" web kernel_agent
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
    echo.
    echo [!] adk web exited with code %EXITCODE%. See the messages above.
    pause
)

endlocal & exit /b %EXITCODE%
