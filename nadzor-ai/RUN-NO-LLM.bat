@echo off
setlocal enabledelayedexpansion

rem Прогон анализа БЕЗ ЛЛМ (registry_diff.py + rooms/equipment/routing —
rem чистый код, без API-ключа: см. scripts/registry_diff.py и
rem scripts/scan_cli.py --no-llm). Кладите файлы ПД в папку BEFORE рядом с
rem этим файлом, файлы РД/ИД — в папку AFTER, затем запускайте.

where wsl >nul 2>&1
if errorlevel 1 (
  echo Windows Subsystem for Linux ^(WSL^) не установлен.
  echo Открой PowerShell от администратора и выполни:  wsl --install
  echo Затем перезагрузи компьютер и запусти этот файл снова.
  echo.
  pause
  exit /b 1
)

set "HEREW=%~dp0"
if "%HEREW:~-1%"=="\" set "HEREW=%HEREW:~0,-1%"

if not exist "%HEREW%\BEFORE" mkdir "%HEREW%\BEFORE"
if not exist "%HEREW%\AFTER" mkdir "%HEREW%\AFTER"

dir /b "%HEREW%\BEFORE\*.pdf" >nul 2>&1
if errorlevel 1 (
  echo В папке BEFORE нет ни одного PDF ^(это должна быть проектная документация — ПД^).
  echo Положи туда файлы и запусти этот файл снова.
  echo.
  echo Папка BEFORE: %HEREW%\BEFORE
  echo Папка AFTER:  %HEREW%\AFTER
  pause
  exit /b 1
)
dir /b "%HEREW%\AFTER\*.pdf" >nul 2>&1
if errorlevel 1 (
  echo В папке AFTER нет ни одного PDF ^(это должна быть рабочая/исполнительная документация — РД/ИД^).
  echo Положи туда файлы и запусти этот файл снова.
  echo.
  echo Папка BEFORE: %HEREW%\BEFORE
  echo Папка AFTER:  %HEREW%\AFTER
  pause
  exit /b 1
)

echo Обновляю копию репозитория внутри WSL ...
wsl -e bash -c "test -d ~/nadzor-project/.git"
if errorlevel 1 (
  wsl -e bash -c "rm -rf ~/nadzor-project; git clone -b claude/new-session-d44es2 https://github.com/DmitryKDI/DmitryKDI.git ~/nadzor-project"
) else (
  wsl -e bash -c "cd ~/nadzor-project && git fetch origin claude/new-session-d44es2 && git reset --hard FETCH_HEAD"
)

echo Проверяю Python-окружение ^(только библиотеки для этого прогона, без фронтенда^) ...
wsl -e bash -c "cd ~/nadzor-project/nadzor-ai && [ -d .venv ] || python3 -m venv .venv"
wsl -e bash -c "cd ~/nadzor-project/nadzor-ai && ./.venv/bin/pip install --quiet --upgrade pip && ./.venv/bin/pip install --quiet -r requirements.txt"

for /f "delims=" %%W in ('wsl wslpath -a "%HEREW%\BEFORE"') do set "BEFOREL=%%W"
for /f "delims=" %%W in ('wsl wslpath -a "%HEREW%\AFTER"') do set "AFTERL=%%W"

echo.
echo ============================================================
echo Запускаю анализ без ЛЛМ ^(реестры помещений/оборудования, Приложение Г^) ...
echo ============================================================
echo.

wsl -e bash -c "cd ~/nadzor-project/nadzor-ai && bash scripts/run_no_llm.sh '!BEFOREL!' '!AFTERL!'"

echo.
echo ============================================================
echo Готово. Если хочешь прогнать со зрением ^(ЛЛМ^) на этом же комплекте —
echo используй scripts/scan_cli.py --provider gigachat --api-key ВАШ_КЛЮЧ
echo вручную внутри WSL ^(ключ никуда не сохраняется этим файлом^).
echo ============================================================
pause
