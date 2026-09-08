@echo off
setlocal

rem Запуск НАДЗОР.ИИ на Windows через WSL.
rem
rem Два правила, каждое из реальной ситуации:
rem  * git НИКОГДА не спрашивает логин в этом окне (репозиторий приватный,
rem    а вводить учётные данные тут некуда) — GIT_TERMINAL_PROMPT=0;
rem  * версия печатается ВСЕГДА, до запуска. «Открылось старое» перестаёт
rem    быть предметом догадок: видно, какой код лежит на диске.

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

set GIT_TERMINAL_PROMPT=0

wsl -e bash -c "test -d ~/nadzor-project/.git"
if errorlevel 1 goto :first_download

echo Checking for updates ...
wsl -e bash -c "cd ~/nadzor-project; GIT_TERMINAL_PROMPT=0 git fetch origin claude/new-session-d44es2 2>/dev/null"
if errorlevel 1 goto :update_failed
wsl -e bash -c "cd ~/nadzor-project; git reset --hard FETCH_HEAD >/dev/null && echo Updated."
goto :show_version

:update_failed
echo.
echo ==================================================================
echo   UPDATE FAILED - starting the version already on this computer.
echo   The repository is private, so git needs a saved GitHub login.
echo   Run this once in a normal terminal and sign in in the browser:
echo       git clone https://github.com/DmitryKDI/DmitryKDI.git %TEMP%\gh-login
echo   Then start this file again.
echo ==================================================================
echo.
goto :show_version

:first_download
echo Downloading the project for the first time ...
wsl -e bash -c "GIT_TERMINAL_PROMPT=0 git clone -b claude/new-session-d44es2 https://github.com/DmitryKDI/DmitryKDI.git ~/nadzor-project"
if errorlevel 1 (
  echo.
  echo Could not download the project - the repository is private.
  echo Run this once in a normal terminal and sign in in the browser:
  echo     git clone https://github.com/DmitryKDI/DmitryKDI.git %TEMP%\gh-login
  echo Then start this file again.
  echo.
  pause
  exit /b 1
)

:show_version
wsl -e bash -c "cd ~/nadzor-project; echo; echo 'Version on disk:'; git log -1 --format='  %%h  %%cs  %%s'; echo"

rem Ключ и сертификаты лежат вне репозитория (secrets/, certs/ в .gitignore)
rem и обновление их не трогает.
wsl -e bash -c "test -f ~/nadzor-project/nadzor-ai/.env"
if errorlevel 1 (
  wsl -e bash -c "cp ~/nadzor-project/nadzor-ai/.env.example ~/nadzor-project/nadzor-ai/.env"
)

wsl -e bash -c "chmod +x ~/nadzor-project/nadzor-ai/scripts/*.sh; exec ~/nadzor-project/nadzor-ai/scripts/start-all.sh"

echo.
echo Finished.
pause
