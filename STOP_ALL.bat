@echo off
title Agentic OS Browser — Stop All Services
color 0C

echo.
echo  =========================================================
echo    AGENTIC OS BROWSER — Stopping All Services
echo  =========================================================
echo.

:: Kill Orchestrator (Python process on port 7771)
echo  [1/4] Stopping Orchestrator...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":7771" 2^>nul') do (
    taskkill /PID %%p /F >nul 2>&1
    echo  [OK]  Orchestrator stopped (PID %%p)
)

:: Kill Ollama serve
echo  [2/4] Stopping Ollama server...
taskkill /IM "ollama.exe" /F >nul 2>&1
if not errorlevel 1 (echo  [OK]  Ollama stopped.) else (echo  [INFO] Ollama was not running.)

:: Stop all agentic Docker containers
echo  [3/4] Cleaning up Docker containers...
for /f "tokens=*" %%c in ('docker ps -q --filter "name=agentic-task" 2^>nul') do (
    docker kill %%c >nul 2>&1
    docker rm %%c >nul 2>&1
    echo  [OK]  Container %%c removed.
)
docker container prune -f --filter "label=agentic-os" >nul 2>&1
echo  [OK]  Docker cleanup done.

:: Close all titled windows
echo  [4/4] Closing launcher windows...
taskkill /FI "WINDOWTITLE eq Agentic OS*" /F >nul 2>&1
echo  [OK]  Windows closed.

echo.
echo  =========================================================
echo    All services stopped.
echo  =========================================================
echo.
timeout /t 3 /nobreak >nul
