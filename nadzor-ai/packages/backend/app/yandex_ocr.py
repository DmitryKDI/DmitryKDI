"""Распознавание страниц без текстового слоя через Yandex Vision OCR.

Сервис вызывается только для страниц, которые PyMuPDF не смог прочитать
текстовым путём. Отсутствие настройки и техническая ошибка возвращаются как
явные состояния: вызывающий код не вправе превращать их в «текста нет».
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path

import httpx
import pymupdf


YANDEX_OCR_URL = os.environ.get(
    "YANDEX_VISION_OCR_URL", "https://ocr.api.cloud.yandex.net/ocr/v1/recognizeText")
YANDEX_OCR_MODEL = os.environ.get("YANDEX_VISION_OCR_MODEL", "page")
YANDEX_OCR_TIMEOUT_SEC = 90
YANDEX_OCR_RENDER_SCALE = 2
YANDEX_OCR_MAX_PIXELS = 20_000_000
YANDEX_OCR_MAX_BYTES = 10 * 1024 * 1024

SECRETS_DIR = Path(os.environ.get(
    "NADZOR_SECRETS_DIR", Path(__file__).resolve().parents[3] / "secrets"))


@dataclass(frozen=True)
class YandexOcrConfig:
    api_key: str
    folder_id: str
    model: str = YANDEX_OCR_MODEL


@dataclass(frozen=True)
class YandexOcrResult:
    text: str = ""
    error: str = ""


def _secret_values() -> dict[str, str]:
    """Читает только явно названный секрет Yandex, не перебирая чужие ключи."""
    values: dict[str, str] = {}
    if not SECRETS_DIR.is_dir():
        return values
    candidates = sorted(
        path for path in SECRETS_DIR.iterdir()
        if path.is_file() and "yandex" in path.name.lower()
    )
    for path in candidates:
        try:
            raw = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            for key, value in parsed.items():
                values[str(key).upper()] = str(value).strip()
            continue
        for line in raw.splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            key, value = line.split("=", 1)
            values[key.strip().upper()] = value.strip().strip("'\"")
    return values


def load_config() -> YandexOcrConfig | None:
    """Настройка из окружения или ``secrets/yandex*.{json,env,txt}``."""
    secrets = _secret_values()
    api_key = (
        os.environ.get("YANDEX_VISION_API_KEY")
        or os.environ.get("YANDEX_OCR_API_KEY")
        or secrets.get("YANDEX_VISION_API_KEY")
        or secrets.get("YANDEX_OCR_API_KEY")
        or secrets.get("API_KEY")
        or ""
    ).strip()
    folder_id = (
        os.environ.get("YANDEX_FOLDER_ID")
        or os.environ.get("YANDEX_VISION_FOLDER_ID")
        or os.environ.get("YANDEX_GPT_FOLDER_ID")
        or secrets.get("YANDEX_FOLDER_ID")
        or secrets.get("YANDEX_VISION_FOLDER_ID")
        or secrets.get("YANDEX_GPT_FOLDER_ID")
        or secrets.get("FOLDER_ID")
        or ""
    ).strip()
    if not api_key or not folder_id:
        return None
    return YandexOcrConfig(api_key=api_key, folder_id=folder_id)


def is_configured() -> bool:
    return load_config() is not None


def _render_page(page) -> bytes:
    """Рендерит страницу в пределах документированных ограничений API."""
    scale = float(YANDEX_OCR_RENDER_SCALE)
    page_pixels = max(float(page.rect.width * page.rect.height), 1.0)
    if page_pixels * scale * scale > YANDEX_OCR_MAX_PIXELS:
        scale = (YANDEX_OCR_MAX_PIXELS / page_pixels) ** 0.5
    pixmap = page.get_pixmap(
        matrix=pymupdf.Matrix(scale, scale), alpha=False, colorspace=pymupdf.csRGB)
    content = pixmap.tobytes("jpeg", jpg_quality=90)
    if len(content) > YANDEX_OCR_MAX_BYTES:
        content = pixmap.tobytes("jpeg", jpg_quality=70)
    if len(content) > YANDEX_OCR_MAX_BYTES:
        raise ValueError("изображение страницы превышает лимит Yandex Vision OCR")
    return content


def recognize_page(page, config: YandexOcrConfig | None = None) -> YandexOcrResult:
    """Возвращает текст страницы либо безопасное описание причины сбоя."""
    cfg = config or load_config()
    if cfg is None:
        return YandexOcrResult(error="Yandex Vision OCR не настроен")
    try:
        content = _render_page(page)
        response = httpx.post(
            YANDEX_OCR_URL,
            headers={
                "Authorization": f"Api-Key {cfg.api_key}",
                "x-folder-id": cfg.folder_id,
                "x-data-logging-enabled": "false",
                "Content-Type": "application/json",
            },
            json={
                "mimeType": "JPEG",
                "languageCodes": ["ru", "en"],
                "model": cfg.model,
                "content": base64.b64encode(content).decode("ascii"),
            },
            timeout=YANDEX_OCR_TIMEOUT_SEC,
        )
        response.raise_for_status()
        payload = response.json()
        annotation = (payload.get("result") or {}).get("textAnnotation") or {}
        return YandexOcrResult(text=str(annotation.get("fullText") or "").strip())
    except httpx.HTTPStatusError as exc:
        return YandexOcrResult(error=f"Yandex Vision OCR вернул HTTP {exc.response.status_code}")
    except (httpx.HTTPError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return YandexOcrResult(error=f"Yandex Vision OCR не выполнил распознавание: {type(exc).__name__}")
