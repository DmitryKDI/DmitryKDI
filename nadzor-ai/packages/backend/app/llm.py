"""Провайдер-абстракция для LLM — по прямому решению пользователя (Г.71)
только два провайдера: Anthropic (Claude — «сам инструмент») и GigaChat.
Раньше здесь были ещё local/Ollama, OpenAI, Google, YandexGPT — убраны
по запросу «оставь только себя и гигачат», код и тесты под них
неиспользуемы, лишняя площадь для поддержки без реального применения в
этом продукте.

Порт AI_PROVIDERS/buildLlmRequest/callLlm/extractJsonObject из
nadzor-browser/main.js: тот же контракт structured-JSON вывода, который в
браузерном инструменте решил проблему рассуждающей модели, уходящей в
посторонний текст вместо ответа по задаче — здесь применяется и к тексту,
и к vision-запросам.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import ssl
import sys
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

PROVIDER_DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5",
    # Базовая GigaChat-2 отвечает 422 "Model does not support image" на
    # vision-запрос (реальный случай) — раз сравнение листов всегда идёт
    # картинками, дефолт обязан быть Pro/Max-тиром, иначе каждое сравнение
    # молча падает.
    "gigachat": "GigaChat-2-Pro",
}

# GigaChat: OAuth-эндпоинт и сама API — разные хосты, оба фиксированы
# (Сбер), настраивать через LlmConfig.base_url незачем — переопределить
# можно точку API целиком через переменную окружения, если понадобится.
# Адрес авторизации вынесен в переменную окружения: он живёт на
# НЕСТАНДАРТНОМ порту, и именно этот порт чаще всего закрыт межсетевым
# экраном, антивирусом или корпоративной сетью. Симптом — ConnectTimeout
# при полностью исправном ключе и сертификатах (проверяется
# scripts/check_gigachat.py: имя разрешается, порт 443 открыт, 9443 нет).
# Значение по умолчанию — документированное провайдером; заменить можно, не
# трогая код, если провайдер даст другой адрес.
GIGACHAT_OAUTH_URL = os.environ.get(
    "GIGACHAT_OAUTH_URL", "https://ngw.devices.sberbank.ru:9443/api/v2/oauth")
# Целевой URL с 16 июля 2026 — единый для всех пользователей
GIGACHAT_API_BASE = os.environ.get("GIGACHAT_API_BASE", "https://api.giga.chat")
# Личный/бизнес — тариф аккаунта, не модели; задаётся авторизационным ключом.
GIGACHAT_SCOPE = os.environ.get("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
# TLS-сертификат хостов провайдера может быть подписан удостоверяющим
# центром, которого нет в системном доверенном наборе (типичный симптом —
# CERTIFICATE_VERIFY_FAILED на первом же вызове). Отключать проверку нельзя:
# это снимает защиту от подмены на всём канале, а не «чинит сертификат».
#
# Правильный путь и он же самый простой для пользователя: положить файл
# корневого сертификата в каталог `certs/` в корне проекта. Ничего больше
# делать не нужно — каталог просматривается сам, все найденные сертификаты
# СКЛЕИВАЮТСЯ с системным набором (certifi), поэтому и хосты провайдера, и
# все остальные продолжают проверяться. Собранный набор кэшируется под
# именем-отпечатком, поэтому добавление или замена файла подхватывается, а
# лишней работы при каждом вызове нет.
#
# Переменная окружения GIGACHAT_CA_BUNDLE остаётся и побеждает каталог:
#   путь   — использовать этот файл;
#   false  — не проверять (последнее средство, видно в логе).
CERTS_DIR = Path(os.environ.get(
    "GIGACHAT_CA_DIR", Path(__file__).resolve().parents[3] / "certs"))
# Расширений у сертификата много, и это ровно тот случай, где перечень
# должен быть широким: формат определяется по содержимому (см. `_as_pem`),
# поэтому лишнее расширение ничем не грозит, а пропущенное означает молча
# ненайденный сертификат. Найдено на живом случае: файлы пришли с «.cert».
_CERT_SUFFIXES = (".pem", ".crt", ".cer", ".cert", ".der", ".ca-bundle", ".txt")


def _as_pem(raw: bytes) -> bytes:
    """Сертификат в виде PEM, откуда бы он ни пришёл.

    Официальная выгрузка удостоверяющего центра для Windows — файлы `.cer`
    в двоичном виде (DER). Склеенные как есть, они дают набор, который
    OpenSSL не читает, и проверка молча остаётся сломанной. Здесь формат
    определяется по содержимому, а не по расширению, и двоичный
    преобразуется в текстовый.
    """
    if b"-----BEGIN" in raw[:200]:
        pem = raw if raw.endswith(b"\n") else raw + b"\n"
    else:
        try:
            pem = ssl.DER_cert_to_PEM_cert(raw).encode("ascii")
        except Exception as exc:  # noqa: BLE001 — негодный файл не роняет запуск
            print(f"файл не похож на сертификат и пропущен ({exc})", file=sys.stderr)
            return b""
    # Проверка тем же разбором, каким набор будут читать потом. Без неё
    # мусорный файл превращался в правдоподобный блок PEM (перекодировка
    # содержимого проверок не делает), и ВЕСЬ набор переставал читаться —
    # один посторонний файл в каталоге ломал проверку сертификатов
    # целиком. Найдено тестом.
    try:
        ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).load_verify_locations(
            cadata=pem.decode("ascii"))
    except Exception as exc:  # noqa: BLE001 — пропускаем один файл, не весь каталог
        print(f"файл не является сертификатом и пропущен ({exc})", file=sys.stderr)
        return b""
    return pem


def _certificates_from_archive(path: Path) -> list[bytes]:
    """Сертификаты прямо из архива: с сайта их отдают именно так, и
    распаковывать вручную ради этого незачем."""
    out: list[bytes] = []
    try:
        with zipfile.ZipFile(path) as archive:
            for name in sorted(archive.namelist()):
                if Path(name).suffix.lower() not in _CERT_SUFFIXES:
                    continue
                out.append(_as_pem(archive.read(name)))
    except Exception as exc:  # noqa: BLE001 — битый архив не роняет запуск
        print(f"архив с сертификатами не прочитан ({exc}): {path}", file=sys.stderr)
    return out


def _bundle_from_certs_dir() -> str | None:
    """Склеенный набор «системные корни + всё из certs/», или None."""
    if not CERTS_DIR.is_dir():
        return None
    # Скрытые файлы пропускаются намеренно: собранный набор лежит в
    # подкаталоге, но точечные файлы в каталоге сертификатов оставляют и
    # редакторы, и системы синхронизации. Найдено тестом: первая версия
    # клала собранный набор рядом, он попадал в собственный список, и
    # отпечаток менялся на каждом вызове — то есть набор пересобирался
    # бесконечно.
    # Поиск ВГЛУБЬ, а не только в самом каталоге: распаковка архива в
    # проводнике Windows создаёт подпапку с именем архива, и сертификаты
    # оказываются на уровень ниже. Первая версия их не видела и молча
    # оставалась на системном наборе — пользователь сделал всё правильно и
    # не получил ни результата, ни сообщения (Г.10). Служебный подкаталог
    # со сборкой и скрытые файлы пропускаются.
    found = sorted(p for p in CERTS_DIR.rglob("*")
                   if p.suffix.lower() in _CERT_SUFFIXES + (".zip",)
                   and p.is_file() and not p.name.startswith(".")
                   and not any(part.startswith(".") for part in p.relative_to(CERTS_DIR).parts))
    if not found:
        return None
    blobs: list[bytes] = []
    for path in found:
        if path.suffix.lower() == ".zip":
            blobs.extend(_certificates_from_archive(path))
        else:
            blobs.append(_as_pem(path.read_bytes()))
    blobs = [b for b in blobs if b]
    if not blobs:
        return None
    try:
        import certifi
        blobs.insert(0, Path(certifi.where()).read_bytes())
    except Exception as exc:  # noqa: BLE001 — без системных корней набор всё равно рабочий
        print(f"системный набор корневых сертификатов не добавлен ({exc}): "
              f"проверяться будут только сертификаты из {CERTS_DIR}", file=sys.stderr)
    digest = hashlib.sha256(b"".join(blobs)).hexdigest()[:16]
    cache = CERTS_DIR / ".cache"
    combined = cache / f"bundle-{digest}.pem"
    if not combined.exists():
        cache.mkdir(exist_ok=True)
        for stale in cache.glob("bundle-*.pem"):
            stale.unlink(missing_ok=True)
        combined.write_bytes(b"\n".join(blobs))
    return str(combined)


# Ключ можно не вводить и не прописывать в переменных: достаточно положить
# файл, выданный в личном кабинете провайдера, в каталог `secrets/` рядом с
# проектом — тот же приём «просто положи файл», что и с сертификатами.
# В репозиторий каталог не идёт (см. .gitignore): ключ относится к машине и
# к учётной записи, а не к коду, и попав в общий репозиторий он становится
# общедоступным (Б.5).
SECRETS_DIR = Path(os.environ.get(
    "NADZOR_SECRETS_DIR", Path(__file__).resolve().parents[3] / "secrets"))

# Строка ключа среди прочего текста выгрузки из личного кабинета: там рядом
# лежат идентификатор приложения и название тарифа, и заставлять человека
# вырезать нужную строку руками — лишний шаг, на котором ошибаются.
_CREDENTIALS_RE = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")


def credentials_from_file(provider: str) -> str:
    """Ключ провайдера из файла в `secrets/`, или пустая строка.

    Ищется файл, в имени которого есть название провайдера; если такого нет
    — любой файл каталога. Формат не навязывается: принимается и голый
    ключ, и выгрузка из личного кабинета целиком.
    """
    if not SECRETS_DIR.is_dir():
        return ""
    files = sorted(p for p in SECRETS_DIR.iterdir()
                   if p.is_file() and not p.name.startswith(".")
                   and p.suffix.lower() in ("", ".txt", ".key", ".env"))
    named = [p for p in files if provider.lower() in p.name.lower()]
    for path in (named or files):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 — нечитаемый файл не роняет запуск
            print(f"файл с ключом не прочитан ({exc}): {path}", file=sys.stderr)
            continue
        stripped = text.strip()
        if _CREDENTIALS_RE.fullmatch(stripped):
            return stripped
        found = _CREDENTIALS_RE.search(text)
        if found:
            return found.group(0)
    return ""


def ca_bundle():
    """Чем проверять TLS: путь к набору, True (системный) или False (не
    проверять). Считается при каждом обращении, а не один раз при импорте:
    положенный в `certs/` файл должен подхватываться без правки кода."""
    raw = os.environ.get("GIGACHAT_CA_BUNDLE", "").strip()
    if raw.lower() in ("false", "0", "no"):
        return False
    if raw:
        return raw
    return _bundle_from_certs_dir() or True


def ca_bundle_description() -> str:
    """Человеческое описание того, чем сейчас проверяется TLS — чтобы
    «проверка отключена» никогда не выглядело так же, как «всё в порядке»."""
    value = ca_bundle()
    if value is False:
        return "проверка сертификата ОТКЛЮЧЕНА (GIGACHAT_CA_BUNDLE=false)"
    if value is True:
        return "системный набор корневых сертификатов"
    return f"набор из certs/ вместе с системным: {value}"

_gigachat_token_cache: dict[str, tuple[str, float]] = {}  # api_key -> (token, истекает_в_monotonic)


def _gigachat_upload_image(access_token: str, png_bytes: bytes) -> str:
    """Загрузить картинку в хранилище GigaChat и вернуть file_id для attachments."""
    resp = _post_json(
        f"{GIGACHAT_API_BASE}/v1/files",
        files={"file": ("page.png", png_bytes, "image/png")},
        data={"purpose": "general"},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=60.0, verify=ca_bundle(),
    )
    return resp.json()["id"]


def _gigachat_token(client_id: str, client_secret: str) -> str:
    """Токен живёт 30 минут — кэшируем по паре client_id:client_secret с запасом
    в 10 минут, чтобы не получать новый на каждый вызов внутри одного прогона."""
    cache_key = f"{client_id}:{client_secret}"
    cached = _gigachat_token_cache.get(cache_key)
    if cached and cached[1] > time.monotonic():
        return cached[0]
    import base64
    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    resp = _post_json(
        GIGACHAT_OAUTH_URL,
        data={"scope": GIGACHAT_SCOPE},
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "RqUID": str(uuid.uuid4()),
            "Authorization": f"Basic {creds}",
        },
        timeout=30.0, verify=ca_bundle(),
    )
    data = resp.json()
    token = data["access_token"]
    expires_at_ms = data.get("expires_at", int(time.time() * 1000) + 1800000)
    expires_in = max(60.0, expires_at_ms / 1000 - time.time())
    _gigachat_token_cache[cache_key] = (token, time.monotonic() + expires_in - 600)
    return token

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


@dataclass
class LlmConfig:
    provider: str  # 'anthropic' | 'gigachat'
    api_key: str = ""
    base_url: str = ""
    model: str = ""

    def resolved_model(self) -> str:
        return self.model or PROVIDER_DEFAULT_MODELS.get(self.provider, "")

    def resolved_base_url(self) -> str:
        return self.base_url


def extract_json_object(text: str) -> dict | None:
    if not text:
        return None
    cleaned = _THINK_BLOCK_RE.sub("", text).strip()
    m = _FENCED_JSON_RE.search(cleaned)
    candidate = m.group(1) if m else cleaned
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(candidate[start : end + 1])
    except json.JSONDecodeError:
        return None


def _anthropic_image_block(data_url: str) -> dict:
    header, b64data = data_url.split(",", 1)
    mime = header.split(";")[0].split(":")[1]
    return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64data}}


def png_bytes_to_data_url(png_bytes: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")


# Г.78 — реальный найденный сбой: пачка последовательных вызовов
# (requirement_text_verify.py делает по вызову на КАЖДУЮ оставшуюся
# страницу РД для каждого требования — на комплекте с 53+ требованиями и
# десятком страниц это сотни вызовов подряд) упирается в лимит частоты
# GigaChat раньше, чем в реальный сбой сети — 429 у КР/НВ (1540 и 14
# сбоев) на том же прогоне, где ОВ/АР с меньшим числом вызовов отработали
# чисто. Без ретрая это неотличимо от честного "провайдер недоступен".
# Г.82 (независимый аудит Opus) — реальная находка: `Retry-After` не
# проверялся на верхнюю границу — провайдер, приславший, например,
# `Retry-After: 3600`, заставил бы один вызов молча спать час, а с тремя
# попытками подряд — до нескольких часов, без единой строки лога о том,
# что происходит. Потолок ниже не устраняет 429 как таковой (для этого и
# есть ретраи), а не даёт одному сбойному ответу провайдера превратить
# прогон в многочасовое зависание, неотличимое от простого "не отвечает".
_RATE_LIMIT_MAX_RETRIES = 3
_RATE_LIMIT_BASE_DELAY = 2.0
_RATE_LIMIT_MAX_DELAY = 30.0


def _post_json(url: str, **kwargs) -> httpx.Response:
    """httpx.post + raise_for_status, но с телом ответа в тексте ошибки —
    провайдер обычно объясняет причину 4xx (неверная модель, формат запроса),
    а голый код без текста превращает диагностику в гадание вслепую (реальный
    случай: 400 от Ollama на vision-запросе, причина ясна только из тела).

    429 (Too Many Requests) — отдельная ветка: несколько попыток с
    задержкой (`Retry-After` от провайдера, если есть, иначе экспоненциально
    растущая пауза) вместо немедленного отказа — см. Г.78."""
    attempt = 0
    while True:
        resp = httpx.post(url, **kwargs)
        if resp.status_code == 429 and attempt < _RATE_LIMIT_MAX_RETRIES:
            retry_after = resp.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else _RATE_LIMIT_BASE_DELAY * (2 ** attempt)
            except ValueError:
                delay = _RATE_LIMIT_BASE_DELAY * (2 ** attempt)
            delay = min(delay, _RATE_LIMIT_MAX_DELAY)
            time.sleep(delay)
            attempt += 1
            continue
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise httpx.HTTPStatusError(f"{e}\nОтвет провайдера: {resp.text[:2000]}", request=e.request, response=e.response) from e
        return resp


def call_llm_json(
    config: LlmConfig,
    system_prompt: str,
    user_text: str,
    images: list[str] | None = None,
    timeout: float = 120.0,
) -> dict | None:
    """Синхронный structured-JSON вызов. images — список data-URL (png/jpeg)."""
    provider = config.provider
    model = config.resolved_model()
    images = images or []

    if provider == "anthropic":
        content = [{"type": "text", "text": user_text}]
        for img in images:
            content.append(_anthropic_image_block(img))
        body = {
            "model": model,
            "max_tokens": 4096,
            "system": system_prompt,
            "messages": [{"role": "user", "content": content}],
        }
        headers = {
            "x-api-key": config.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        resp = _post_json("https://api.anthropic.com/v1/messages", json=body, headers=headers, timeout=timeout)
        data = resp.json()
        text = data["content"][0]["text"]
        return extract_json_object(text)

    if provider == "gigachat":
        # GigaChat 2 про: клиентские реквизиты в формате Client ID:Client Secret
        if not config.api_key:
            raise ValueError("не задан GigaChat api_key (Base64-строка Client ID:Client Secret)")
        import base64
        try:
            decoded = base64.b64decode(config.api_key).decode()
            parts = decoded.split(":", 1)
            if len(parts) != 2:
                raise ValueError("api_key должен быть Base64 от 'client_id:client_secret'")
            client_id, client_secret = parts
        except Exception as exc:
            raise ValueError(f"Не удалось разобрать api_key: {exc}") from None
        access_token = _gigachat_token(client_id, client_secret)
        # GigaChat не поддерживает response_format и inline image_url —
        # изображения загружаются в хранилище через /files и передаются как attachments.
        attachments = []
        if images:
            for img in images:
                _, b64data = img.split(",", 1)
                png_bytes = base64.b64decode(b64data)
                file_id = _gigachat_upload_image(access_token, png_bytes)
                attachments.append(file_id)
        message: dict = {"role": "user", "content": user_text}
        if attachments:
            message["attachments"] = attachments
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system_prompt}, message],
        }
        resp = _post_json(
            f"{GIGACHAT_API_BASE}/v1/chat/completions",
            json=body, headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            timeout=timeout, verify=ca_bundle(),
        )
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        return extract_json_object(text)

    raise ValueError(f"unknown provider: {provider}")


# Бюджеты предполётной проверки: сколько ждать ответа и сколько раз пробовать.
# Не пороги истины — ошибка стоит времени, а не правильности разбора.
_REACH_TIMEOUT = 45.0
_REACH_ATTEMPTS = 3
_REACH_RETRY_DELAY = 2.0


def check_llm_reachable(llm_config: LlmConfig | None) -> tuple[bool, str]:
    """Предполётная проверка связи с провайдером — один короткий вызов.

    Г.91. Разбор тома — это десятки вызовов и минуты работы. Если связи
    нет, каждый вызов молча возвращает пустой результат, и прогон
    заканчивается правдоподобным отчётом, в котором ничего не найдено, —
    ровно ловушка Г.77: 100%-ный отказ связи был неотличим от «модель со
    всем согласна» и стоил трёх раундов правок промпта против бага,
    которого в промпте не было. Один вызов до начала работы отделяет
    «связи нет» от «модель так ответила».

    Три состояния различаются явно, а не сводятся к «не получилось»:
    ключа нет (проверять нечего), связь есть, связь не прошла — с точной
    причиной, потому что «сеть не пускает» и «ключ протух» требуют от
    пользователя разных действий (Г.10).
    """
    if llm_config is None:
        return False, "ключ ЛЛМ не задан — проверять нечего"
    # Пробуем несколько раз: наблюдался разовый ConnectTimeout там, где через
    # минуту связь была. Отказ по одной неудачной попытке блокировал бы весь
    # разбор из-за секундного провала сети, а разбор — это минуты работы,
    # которые не должны зависеть от одного пакета.
    last: Exception | None = None
    for attempt in range(_REACH_ATTEMPTS):
        try:
            call_llm_json(
                llm_config,
                "Ты отвечаешь строго JSON. Проверка связи.",
                'Ответь ровно: {"ok": true}',
                timeout=_REACH_TIMEOUT,
            )
            last = None
            break
        except Exception as exc:  # noqa: BLE001 — причина нужна целиком, любая
            last = exc
            if attempt + 1 < _REACH_ATTEMPTS:
                time.sleep(_REACH_RETRY_DELAY)
    if last is not None:
        exc = last
        return False, (f"{type(exc).__name__}: {exc} "
                       f"(попыток: {_REACH_ATTEMPTS}){_tls_advice(exc)}")
    return True, f"связь с провайдером есть ({ca_bundle_description()})"


def _tls_advice(exc: Exception) -> str:
    """Подсказка ровно на тот случай, который пользователь не решит сам.

    Сообщение «CERTIFICATE_VERIFY_FAILED» ничего не говорит о том, что
    делать, и толкает к первому попавшемуся совету из интернета —
    отключить проверку. Поэтому здесь называется рабочее действие, а
    отключение упомянуто последним и как последнее средство.
    """
    text = f"{type(exc).__name__}: {exc}"
    if "CERTIFICATE_VERIFY" not in text.upper() and "SSL" not in text.upper():
        return ""
    return (f". Это не ключ и не сеть: сертификат хоста подписан центром, "
            f"которого нет в доверенном наборе машины. Положите файл корневого "
            f"сертификата (.pem/.crt/.cer) в каталог {CERTS_DIR} и перезапустите "
            f"сервер — он подхватится сам и будет склеен с системным набором. "
            f"Сейчас используется: {ca_bundle_description()}")
