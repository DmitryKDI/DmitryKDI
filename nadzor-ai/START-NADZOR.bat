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

wsl -e bash -c "test -d ~/nadzor-project/.git"
if errorlevel 1 (
  wsl -e bash -c "rm -rf ~/nadzor-project; git clone -b claude/new-session-d44es2 https://github.com/DmitryKDI/DmitryKDI.git ~/nadzor-project"
) else (
  wsl -e bash -c "cd ~/nadzor-project; git fetch origin claude/new-session-d44es2"
  wsl -e bash -c "cd ~/nadzor-project; git reset --hard FETCH_HEAD"
)

wsl -e bash -c "chmod +x ~/nadzor-project/nadzor-ai/scripts/*.sh; exec ~/nadzor-project/nadzor-ai/scripts/start-all.sh"

echo.
echo Finished.
pause
