@echo off
setlocal enabledelayedexpansion

rem Проверка требований без кода системы (Г.32/Г.33) с ЭСКАЛАЦИЕЙ В ЗРЕНИЕ
rem по листу РД — нужен ключ ИИ-провайдера (по умолчанию GigaChat).
rem Использует те же папки BEFORE (ПД) и AFTER (РД/ИД), что и
rem RUN-NO-LLM.bat — если уже прогоняли тот файл, файлы раскладывать
rem заново не нужно.
rem
rem Ключ хранится ОТДЕЛЬНО от общего .env приложения, в файле
rem vision-keys.env рядом с этим .bat — при первом запуске файл создаётся
rem автоматически из шаблона, откройте его в Блокноте и вставьте ключ.

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

echo Обновляю копию репозитория внутри WSL ...
wsl -e bash -c "test -d ~/nadzor-project/.git"
if errorlevel 1 (
  wsl -e bash -c "rm -rf ~/nadzor-project; git clone -b claude/new-session-d44es2 https://github.com/DmitryKDI/DmitryKDI.git ~/nadzor-project"
) else (
  wsl -e bash -c "cd ~/nadzor-project && git fetch origin claude/new-session-d44es2 && git reset --hard FETCH_HEAD"
)

rem Файл ключей — на стороне Windows, рядом с этим .bat, чтобы открывался
rem обычным Блокнотом без захода в WSL. git reset --hard внутри WSL его не
rem касается (он не в git-репозитории вообще, а на Windows-диске).
if not exist "%HEREW%\vision-keys.env" (
  copy /Y "%HEREW%\vision-keys.env.example" "%HEREW%\vision-keys.env" >nul
)

findstr /R /C:"^GIGACHAT_CREDENTIALS=.+" "%HEREW%\vision-keys.env" >nul 2>&1
if errorlevel 1 (
  echo Ключ не заполнен.
  echo.
  echo Открой файл  %HEREW%\vision-keys.env  в Блокноте и вставь ключ
  echo в строку GIGACHAT_CREDENTIALS= ^(без пробелов и кавычек^), сохрани
  echo файл и запусти этот .bat снова.
  echo.
  pause
  exit /b 1
)

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

echo Проверяю Python-окружение ^(только библиотеки для этого прогона, без фронтенда^) ...
wsl -e bash -c "cd ~/nadzor-project/nadzor-ai && [ -d .venv ] || python3 -m venv .venv"
wsl -e bash -c "cd ~/nadzor-project/nadzor-ai && ./.venv/bin/pip install --quiet --upgrade pip && ./.venv/bin/pip install --quiet -r requirements.txt"

for /f "delims=" %%W in ('wsl wslpath -a "%HEREW%\BEFORE"') do set "BEFOREL=%%W"
for /f "delims=" %%W in ('wsl wslpath -a "%HEREW%\AFTER"') do set "AFTERL=%%W"
for /f "delims=" %%W in ('wsl wslpath -a "%HEREW%\vision-keys.env"') do set "KEYSL=%%W"

echo.
echo ============================================================
echo Запускаю сверку требований с эскалацией в зрение по РД ...
echo Это обращается к внешнему ИИ-провайдеру ^(GigaChat^) — не бесплатно
echo и не мгновенно, только для найденных кандидатов без кода системы.
echo ============================================================
echo.

wsl -e bash -c "cd ~/nadzor-project/nadzor-ai && bash scripts/run_vision_requirements.sh '!BEFOREL!' '!AFTERL!' '!KEYSL!'"

echo.
echo ============================================================
echo Готово.
echo ============================================================
pause
