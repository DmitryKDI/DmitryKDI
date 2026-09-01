#!/usr/bin/env python3
"""Быстрый тест GigaChat 2 про с реальными PDF из C:\\OSR\\123.

Запуск:
    python test_gigachat_pdf.py --pdf-dir C:\\OSR\\123

Требует .env с GIGACHAT_CLIENT_ID и GIGACHAT_CLIENT_SECRET
(или API_KEY в формате Base64(client_id:client_secret)).
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
from pathlib import Path

# Добавляем packages/backend в путь
_cwd = str(Path(".").resolve())
_backend = str(Path(_cwd) / "packages" / "backend")
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from app.llm import LlmConfig, call_llm_json
from app.vision import render_page_to_data_url


SYSTEM_PROMPT = """Ты — детектор расхождений в строительной документации.

Тебе покажут картинки листов чертежей. Найди содержательные различия между ними.

Для каждого расхождения укажи:
- label: краткий код (например "D-01")
- change: что изменилось и где на листе (оси, номер помещения)
- severity: "критично" | "существенно" | "незначительно"
- field_check: что проверить на объекте (одно действие)

Отвечай только JSON:
{"significant": [{"label": "...", "change": "...", "severity": "...", "field_check": "..."}],
 "injection_suspected": false,
 "noise_note": "...",
 "checked_total": 0, "significant_total": 0}"""


async def test_gigachat_with_pdfs(pdf_dir: str):
    """Сравнить первые страницы первых двух PDF из директории."""
    pdf_path = Path(pdf_dir)
    pdf_files = sorted(pdf_path.glob("*.pdf"))
    if len(pdf_files) < 2:
        print(f"Нужно минимум 2 PDF, найдено {len(pdf_files)}")
        return

    # Читаем реквизиты из окружения
    client_id = os.environ.get("GIGACHAT_CLIENT_ID", "")
    client_secret = os.environ.get("GIGACHAT_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        print("Ошибка: задайте GIGACHAT_CLIENT_ID и GIGACHAT_CLIENT_SECRET в окружении")
        print("Или создайте .env файл в корне nadzor-ai/")
        return

    # Формируем API ключ в формате Base64(client_id:client_secret)
    api_key = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()

    config = LlmConfig(
        provider="gigachat",
        api_key=api_key,
        model="GigaChat-Pro",
    )

    pdf_a = pdf_files[0]
    pdf_b = pdf_files[1]
    print(f"Сравниваю: {pdf_a.name} (стр. 1) vs {pdf_b.name} (стр. 1)")

    try:
        result = call_llm_json(
            config=config,
            system_prompt=SYSTEM_PROMPT,
            user_text="Сравни левый лист (ПД) и правый лист (РД/ИД).",
            images=[
                render_page_to_data_url(str(pdf_a), page_no=1),
                render_page_to_data_url(str(pdf_b), page_no=1),
            ],
            timeout=180.0,
        )

        if result:
            print("\n=== Результат анализа ===")
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("\nИИ не дал разбираемый ответ")

    except Exception as exc:
        print(f"Ошибка вызова GigaChat: {exc}")
        raise


def main():
    parser = argparse.ArgumentParser(description="Тест GigaChat 2 про с PDF")
    parser.add_argument("--pdf-dir", default=r"C:\OSR\123", help="Директория с PDF")
    args = parser.parse_args()

    asyncio.run(test_gigachat_with_pdfs(args.pdf_dir))


if __name__ == "__main__":
    main()
