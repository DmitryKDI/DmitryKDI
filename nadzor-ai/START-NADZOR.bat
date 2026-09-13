@echo off
setlocal EnableExtensions
chcp 65001 >nul
title NADZOR.AI

set "WSL_PROJECT_DIR=/mnt/c/OSR/DmitryKDI/nadzor-ai"
set "WSL_EXE=%SystemRoot%\System32\wsl.exe"

if not exist "%WSL_EXE%" goto no_wsl

echo Checking Windows project through WSL...
"%WSL_EXE%" -d Ubuntu --exec /bin/bash -lc "test -f %WSL_PROJECT_DIR%/scripts/start-all.sh"
if errorlevel 1 goto no_project

echo Stopping old services...
"%WSL_EXE%" -d Ubuntu --exec /bin/bash -lc "pkill -f 'uvicorn app.main:app' 2>/dev/null || true; pkill -f 'node_modules/.bin/vite' 2>/dev/null || true"
timeout /t 2 /nobreak >nul

echo Starting backend and frontend...
echo Frontend: http://localhost:5173
echo Backend:  http://127.0.0.1:8010
echo Keep this window open. Press Ctrl+C to stop.
echo.
"%WSL_EXE%" -d Ubuntu --exec /bin/bash -lc "cd %WSL_PROJECT_DIR% && exec /bin/bash ./scripts/start-all.sh"
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" goto start_error
goto end

:no_wsl
echo ERROR: WSL or Ubuntu is not available.
echo Run: wsl -l -v
pause
exit /b 1

:no_project
echo ERROR: project was not found at /mnt/c/OSR/DmitryKDI/nadzor-ai.
echo Copy the project first, then run this file again.
pause
exit /b 1

:start_error
echo ERROR: start-all.sh failed with code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%

:end
endlocal
exit /b 0
