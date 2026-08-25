# Общие функции для setup.sh и start-all.sh. Отдельным файлом, чтобы правки
# в одном месте действовали на оба сценария.
#
# Подключается через `source`, поэтому здесь нет ни shebang, ни `set -e`:
# режим ошибок задаёт вызывающий скрипт.

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; BOLD=$'\033[1m'; OFF=$'\033[0m'

say()  { printf '%s\n' "${BOLD}$*${OFF}"; }
ok()   { printf '%s\n' "${GREEN}  ✓ $*${OFF}"; }
warn() { printf '%s\n' "${YELLOW}  ! $*${OFF}"; }
die()  { printf '\n%s\n\n' "${RED}${BOLD}Ошибка: $*${OFF}" >&2; exit 1; }

# Windows-версии node/npm попадают в PATH внутри WSL и падают на путях WSL
# («UNC-пути не поддерживаются»). Берём именно линуксовый бинарь.
pick_npm() {
  if [ -x /usr/bin/npm ]; then echo /usr/bin/npm; return; fi
  local found; found="$(command -v npm 2>/dev/null || true)"
  case "$found" in
    /mnt/c/*|"") die "не найден npm для Linux. Установите: sudo apt install -y nodejs npm" ;;
    *) echo "$found" ;;
  esac
}

pick_node() {
  if [ -x /usr/bin/node ]; then echo /usr/bin/node; return; fi
  local found; found="$(command -v node 2>/dev/null || true)"
  case "$found" in
    /mnt/c/*|"") die "не найден node для Linux. Установите: sudo apt install -y nodejs npm" ;;
    *) echo "$found" ;;
  esac
}

# Ждём, пока сервер начнёт отвечать. Без этого браузер открывается на пустом
# порту и пользователь видит ошибку соединения там, где всё в порядке.
wait_for_port() {
  local port="$1" name="$2" tries="${3:-60}" i=0
  while [ "$i" -lt "$tries" ]; do
    if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then exec 3<&- 3>&-; return 0; fi
    i=$((i + 1)); sleep 1
  done
  return 1
}

port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null && { exec 3<&- 3>&-; return 0; } || return 1; }
