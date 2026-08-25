@echo off
setlocal

where wsl >nul 2>&1
if errorlevel 1 (
  echo Windows Subsystem for Linux ^(WSL^) is not installed.
  echo Install it: open PowerShell as Administrator and run:  wsl --install
  echo Then restart the computer and run this file again.
  echo.
  pause
  exit /b 1
)

echo Starting NADZOR.AI ...
echo Keep this window open - closing it stops the system.
echo.

(
echo #!/usr/bin/env bash
echo set -e
echo BRANCH=claude/new-session-d44es2
echo REPO=https://github.com/DmitryKDI/DmitryKDI.git
echo if [ -d ~/nadzor-project/.git ]; then
echo   cd ~/nadzor-project
echo   git fetch origin $BRANCH
echo   git reset --hard FETCH_HEAD
echo else
echo   git clone -b $BRANCH $REPO ~/nadzor-project
echo fi
echo chmod +x ~/nadzor-project/nadzor-ai/scripts/*.sh
echo exec ~/nadzor-project/nadzor-ai/scripts/start-all.sh
) | wsl -e bash

echo.
echo Finished.
pause
