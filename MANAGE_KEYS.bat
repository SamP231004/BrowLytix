@echo off
setlocal EnableDelayedExpansion
title Agentic OS Browser — Manage API Keys
color 0B

set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"

:: Activate venv if it exists
if exist "%PROJECT_DIR%venv\Scripts\activate.bat" (
    call "%PROJECT_DIR%venv\Scripts\activate.bat"
)
set "PYTHONPATH=%PROJECT_DIR%"

:MENU
cls
echo.
echo  =========================================================
echo    AGENTIC OS BROWSER - API Key Manager
echo  =========================================================
echo.
echo  [1] Add / Update OpenAI key    (starts with sk-)
echo  [2] Add / Update Anthropic key (starts with sk-ant-)
echo  [3] Add a different key
echo  [4] List all stored keys
echo  [5] Delete a key
echo  [6] Exit
echo.
set "CHOICE="
set /p CHOICE=  Choose option (1-6): 

if "!CHOICE!"=="1" goto :ADD_OPENAI
if "!CHOICE!"=="2" goto :ADD_ANTHROPIC
if "!CHOICE!"=="3" goto :ADD_CUSTOM
if "!CHOICE!"=="4" goto :LIST
if "!CHOICE!"=="5" goto :DELETE
if "!CHOICE!"=="6" goto :END
goto :MENU

:ADD_OPENAI
echo.
echo  Paste your OpenAI API key below (starts with sk-)
echo  Then press Enter:
echo.
set "KEYVAL="
set /p KEYVAL=  OpenAI API Key: 
echo.
if not "!KEYVAL!"=="" (
    python "%PROJECT_DIR%store_key.py" "OPENAI_API_KEY" "!KEYVAL!"
) else (
    echo  [SKIP] Nothing entered.
)
echo.
pause
goto :MENU

:ADD_ANTHROPIC
echo.
echo  Paste your Anthropic API key below (starts with sk-ant-)
echo  Then press Enter:
echo.
set "KEYVAL="
set /p KEYVAL=  Anthropic API Key: 
echo.
if not "!KEYVAL!"=="" (
    python "%PROJECT_DIR%store_key.py" "ANTHROPIC_API_KEY" "!KEYVAL!"
) else (
    echo  [SKIP] Nothing entered.
)
echo.
pause
goto :MENU

:ADD_CUSTOM
echo.
set "KNAME="
set /p KNAME=  Key name (e.g. AWS_ACCESS_KEY): 
set "KVAL="
set /p KVAL=  Key value: 
echo.
if not "!KNAME!"=="" if not "!KVAL!"=="" (
    python "%PROJECT_DIR%store_key.py" "!KNAME!" "!KVAL!"
) else (
    echo  [SKIP] Name or value was empty.
)
echo.
pause
goto :MENU

:LIST
echo.
python "%PROJECT_DIR%list_keys.py"
echo.
pause
goto :MENU

:DELETE
echo.
set "KNAME="
set /p KNAME=  Key name to delete: 
echo.
if not "!KNAME!"=="" (
    python "%PROJECT_DIR%delete_key.py" "!KNAME!"
) else (
    echo  [SKIP] Nothing entered.
)
echo.
pause
goto :MENU

:END
endlocal
