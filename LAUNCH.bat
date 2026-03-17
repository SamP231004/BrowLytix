@echo off
setlocal EnableDelayedExpansion
title Agentic OS Browser
color 0A

set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"
set "LOG=%PROJECT_DIR%launcher.log"
echo Launcher started %date% %time% > "%LOG%"

echo.
echo  =========================================================
echo    AGENTIC OS BROWSER  v1.0.0
echo  =========================================================
echo.

echo  [1/6] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    color 0C
    echo  [ERROR] Python not found.
    echo  Download: https://www.python.org/downloads/windows/
    pause & exit /b 1
)
echo  [OK]  Python found.

echo  [2/6] Activating virtual environment...
if not exist "%PROJECT_DIR%venv\Scripts\activate.bat" (
    echo  [INFO] Creating virtual environment...
    python -m venv venv
    call "%PROJECT_DIR%venv\Scripts\activate.bat"
    pip install -r requirements.txt --quiet >> "%LOG%" 2>&1
    echo  [OK]  Done.
) else (
    call "%PROJECT_DIR%venv\Scripts\activate.bat"
    echo  [OK]  Activated.
)
set "PYTHONPATH=%PROJECT_DIR%"

echo  [3/6] Checking dependencies...
python check_deps.py >nul 2>&1
if errorlevel 1 (
    echo  [INFO] Installing missing packages...
    pip install websockets anthropic openai cryptography keyring psutil aiohttp pydantic python-dotenv pyyaml networkx aiofiles structlog --quiet >> "%LOG%" 2>&1
    echo  [OK]  Done.
) else (
    echo  [OK]  Dependencies ready.
)

echo  [4/6] Checking Docker...
docker info >nul 2>&1
if errorlevel 1 (
    echo  [WARN] Docker not running. Attempting to start...
    start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    timeout /t 15 /nobreak >nul
) else (
    echo  [OK]  Docker running.
)

echo  [5/6] Checking Ollama...
ollama list >nul 2>&1
if errorlevel 1 (
    echo  [INFO] Ollama not running - cloud-only mode.
) else (
    echo  [OK]  Ollama running.
)

echo  [6/6] Clearing port 7771...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":7771" 2^>nul') do (
    taskkill /PID %%p /F >nul 2>&1
)
echo  [OK]  Port ready.

echo.
echo  =========================================================
echo    Launching...
echo  =========================================================
echo.

start "Agentic OS - Orchestrator" cmd /k "cd /d "%PROJECT_DIR%" && call venv\Scripts\activate.bat && set PYTHONPATH=%PROJECT_DIR% && python main.py"

timeout /t 5 /nobreak >nul

if exist "%PROJECT_DIR%ui\index.html" (
    start "" "%PROJECT_DIR%ui\index.html"
    echo  [OK]  Browser UI opened.
) else (
    echo  [INFO] UI not found at ui\index.html
)

echo.
echo  =========================================================
echo    RUNNING - Orchestrator on ws://localhost:7771
echo    Close the Orchestrator window to stop.
echo  =========================================================
echo.
pause
endlocal
