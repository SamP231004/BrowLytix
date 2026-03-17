@echo off
setlocal EnableDelayedExpansion
title Agentic OS Browser — First Time Setup
color 0B

set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"
set "LOG=%PROJECT_DIR%setup.log"
echo Setup started %date% %time% > "%LOG%"

echo.
echo  =========================================================
echo    AGENTIC OS BROWSER - First Time Setup
echo  =========================================================
echo.
echo  This will install everything and store your API keys.
echo  Press any key to begin, or Ctrl+C to cancel.
pause >nul

:: =============================================================
::  STEP 1 - CHECK PYTHON
:: =============================================================
echo.
echo  [STEP 1] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    color 0C
    echo.
    echo  ERROR: Python not found!
    echo  Download from: https://www.python.org/downloads/windows/
    echo  Make sure to CHECK "Add Python to PATH" during install.
    echo.
    pause & exit /b 1
)
for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo  [OK]  %%v

:: =============================================================
::  STEP 2 - VIRTUAL ENVIRONMENT
:: =============================================================
echo.
echo  [STEP 2] Setting up virtual environment...
if exist "%PROJECT_DIR%venv\" (
    echo  [INFO] Removing old venv...
    rmdir /s /q "%PROJECT_DIR%venv"
)
python -m venv venv
if errorlevel 1 ( echo  [ERROR] venv failed. & pause & exit /b 1 )
call "%PROJECT_DIR%venv\Scripts\activate.bat"
python -m pip install --upgrade pip --quiet
echo  [OK]  Virtual environment ready.

:: =============================================================
::  STEP 3 - INSTALL DEPENDENCIES
:: =============================================================
echo.
echo  [STEP 3] Installing dependencies (2-5 minutes)...
pip install -r requirements.txt --quiet --no-warn-script-location >> "%LOG%" 2>&1
if errorlevel 1 (
    echo  [WARN] Some packages had issues. Installing core packages...
    pip install websockets anthropic openai cryptography keyring psutil aiohttp pydantic python-dotenv pyyaml networkx aiofiles structlog --quiet >> "%LOG%" 2>&1
) else (
    echo  [OK]  All dependencies installed.
)

:: =============================================================
::  STEP 4 - SET PYTHONPATH
:: =============================================================
echo.
echo  [STEP 4] Setting PYTHONPATH...
setx PYTHONPATH "%PROJECT_DIR%" >nul 2>&1
set "PYTHONPATH=%PROJECT_DIR%"
echo  [OK]  PYTHONPATH set.

:: =============================================================
::  STEP 5 - API KEYS (uses store_key.py helper - no inline Python)
:: =============================================================
echo.
echo  =========================================================
echo    API KEY SETUP
echo    Keys are AES-256-GCM encrypted and stored locally.
echo    They will never be sent anywhere or stored in plain text.
echo  =========================================================
echo.

echo  Enter your OpenAI API key (starts with sk-)
echo  Press Enter to skip if you do not have one yet:
echo.
set "OAIKEY="
set /p OAIKEY=  OpenAI API Key: 
echo.
if not "!OAIKEY!"=="" (
    python "%PROJECT_DIR%store_key.py" "OPENAI_API_KEY" "!OAIKEY!"
) else (
    echo  [SKIP] OpenAI key not provided.
)

echo.
echo  Enter your Anthropic API key (starts with sk-ant-)
echo  Press Enter to skip if you do not have one yet:
echo.
set "ANTKEY="
set /p ANTKEY=  Anthropic API Key: 
echo.
if not "!ANTKEY!"=="" (
    python "%PROJECT_DIR%store_key.py" "ANTHROPIC_API_KEY" "!ANTKEY!"
) else (
    echo  [SKIP] Anthropic key not provided.
)

:: =============================================================
::  STEP 6 - OLLAMA (optional)
:: =============================================================
echo.
echo  =========================================================
echo    OLLAMA LOCAL MODELS (optional - 100 percent free)
echo  =========================================================
echo.
ollama --version >nul 2>&1
if errorlevel 1 (
    echo  [SKIP] Ollama not installed.
    echo         Download from: https://ollama.com/download/windows
) else (
    echo  Which local models do you want to download?
    echo.
    echo  [1] Mistral       4GB  - fast, good for most tasks
    echo  [2] Llama 3.3 70B 40GB - best quality, needs 32GB RAM
    echo  [3] Qwen Coder    18GB - best for code tasks
    echo  [4] All three models
    echo  [5] Skip for now
    echo.
    set "OCHOICE="
    set /p OCHOICE=  Your choice (1-5): 
    if "!OCHOICE!"=="1" ollama pull mistral:latest
    if "!OCHOICE!"=="2" ollama pull llama3.3:70b
    if "!OCHOICE!"=="3" ollama pull qwen2.5-coder:32b
    if "!OCHOICE!"=="4" (
        ollama pull mistral:latest
        ollama pull llama3.3:70b
        ollama pull qwen2.5-coder:32b
    )
)

:: =============================================================
::  STEP 7 - RUN TESTS
:: =============================================================
echo.
echo  =========================================================
echo    RUNNING 35 UNIT TESTS
echo  =========================================================
echo.
python tests\test_all_modules.py
if errorlevel 1 (
    echo.
    echo  [WARN] Some tests failed. Check setup.log for details.
) else (
    echo.
    echo  [OK]  All 35 tests passed!
)

:: =============================================================
::  DESKTOP SHORTCUT
:: =============================================================
echo.
echo  Creating desktop shortcut...
set "SHORTCUT=%USERPROFILE%\Desktop\Agentic OS Browser.lnk"
powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $sc = $ws.CreateShortcut('%SHORTCUT%'); $sc.TargetPath = '%PROJECT_DIR%LAUNCH.bat'; $sc.WorkingDirectory = '%PROJECT_DIR%'; $sc.Description = 'Agentic OS Browser'; $sc.Save()" >nul 2>&1
if exist "%SHORTCUT%" (
    echo  [OK]  Desktop shortcut created.
) else (
    echo  [INFO] Use LAUNCH.bat to start.
)

echo.
echo  =========================================================
echo    SETUP COMPLETE!
echo.
echo    To start the browser:
echo    Double-click "Agentic OS Browser" on your Desktop
echo    OR double-click LAUNCH.bat in this folder.
echo  =========================================================
echo.
pause
endlocal
