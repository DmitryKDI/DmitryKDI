@echo off
chcp 65001 >nul
title НАДЗОР.ИИ
setlocal

set "BRANCH=claude/new-session-d44es2"
set "REPO=https://github.com/DmitryKDI/DmitryKDI.git"

echo.
echo   НАДЗОР.ИИ — запуск
echo   ------------------
echo.

where wsl >nul 2>&1
if errorlevel 1 (
  echo   Не найдена подсистема Linux ^(WSL^).
  echo   Установите её командой в PowerShell от администратора:  wsl --install
  echo.
  pause
  exit /b 1
)

echo   Обновляю проект...
echo.

rem Скачивание и обновление живут здесь, а не в самом проекте: файл-ярлык
rem должен уметь поднять систему на компьютере, где проекта ещё нет вообще.
rem Обновление всегда приводит папку к состоянию ветки, поэтому расхождения
rem между «скачанным раньше» и «текущим» не накапливаются.
wsl -e bash -lc "set -e; command -v git >/dev/null 2>&1 || { echo 'Нужен git. Установите: sudo apt install -y git'; exit 1; }; if [ -d ~/nadzor-project/.git ]; then cd ~/nadzor-project && git fetch origin '%BRANCH%' && git reset --hard FETCH_HEAD; else git clone -b '%BRANCH%' '%REPO%' ~/nadzor-project; fi; chmod +x ~/nadzor-project/nadzor-ai/scripts/*.sh; exec ~/nadzor-project/nadzor-ai/scripts/start-all.sh"

echo.
echo   Работа завершена.
pause
