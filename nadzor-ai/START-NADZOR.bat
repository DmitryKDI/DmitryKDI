@echo off
setlocal

rem Запуск НАДЗОР.ИИ на Windows через WSL.
rem
rem Обновление из репозитория здесь ДОБРОВОЛЬНОЕ и молчаливое: если git не
rem может достучаться до репозитория (он приватный, а на машине нет входа),
rem запуск не должен останавливаться и уж тем более спрашивать логин и
rem пароль в окне, где их некуда ввести. Работаем тем, что уже лежит на
rem диске, и говорим об этом одной строкой.

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

rem GIT_TERMINAL_PROMPT=0 — ключевая строка: без неё git на приватном
rem репозитории останавливается и ждёт ввода логина, а окно запуска для
rem этого не предназначено (наблюдалось живьём).
set GIT_TERMINAL_PROMPT=0

wsl -e bash -c "test -d ~/nadzor-project/.git"
if errorlevel 1 (
  echo Downloading the project for the first time ...
  wsl -e bash -c "GIT_TERMINAL_PROMPT=0 git clone -b claude/new-session-d44es2 https://github.com/DmitryKDI/DmitryKDI.git ~/nadzor-project"
  if errorlevel 1 (
    echo.
    echo Could not download the project.
    echo The repository is private - Windows needs a saved GitHub login.
    echo Open a terminal and run once:
    echo     git clone https://github.com/DmitryKDI/DmitryKDI.git
    echo Sign in when the browser window appears, then run this file again.
    echo.
    pause
    exit /b 1
  )
) else (
  echo Checking for updates ...
  wsl -e bash -c "cd ~/nadzor-project; GIT_TERMINAL_PROMPT=0 git fetch origin claude/new-session-d44es2 2>/dev/null"
  if errorlevel 1 (
    echo Update skipped - no access to the repository right now.
    echo Starting with the version already on this computer.
  ) else (
    wsl -e bash -c "cd ~/nadzor-project; git reset --hard FETCH_HEAD"
  )
)

rem Ключ и сертификаты лежат вне репозитория и переживают обновление:
rem secrets/ и certs/ в .gitignore, reset --hard их не трогает.
wsl -e bash -c "test -f ~/nadzor-project/nadzor-ai/.env"
if errorlevel 1 (
  wsl -e bash -c "cp ~/nadzor-project/nadzor-ai/.env.example ~/nadzor-project/nadzor-ai/.env"
)

wsl -e bash -c "chmod +x ~/nadzor-project/nadzor-ai/scripts/*.sh; exec ~/nadzor-project/nadzor-ai/scripts/start-all.sh"

echo.
echo Finished.
pause
