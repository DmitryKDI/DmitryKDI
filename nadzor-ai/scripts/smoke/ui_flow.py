"""Сценарий инспектора в браузере (необязательная проверка, нужен Playwright).

Заполняет карточки ПД и РД, загружает файлы через интерфейс, запускает
проверку, отклоняет кандидата с кодированной причиной и финализирует
протокол. Проверяет, что на странице не было ошибок.

    pip install playwright   # браузер: `playwright install chromium`
    python scripts/smoke/ui_flow.py --url http://127.0.0.1:5173

С дублёром модели (`make smoke`, docker-compose.smoke.yml) кандидат
появляется, потому что площадь в ПД и РД различается.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

import pymupdf
from playwright.sync_api import expect, sync_playwright

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FILES = {"ui-pd.pdf": ["Площадь застройки: 1200 м2", "Этажность надземная: 9"],
         "ui-rd.pdf": ["Площадь застройки: 1300 м2", "Этажность надземная: 9"]}


def _write(folder: Path) -> None:
    for name, lines in FILES.items():
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_font(fontname="F0", fontfile=FONT)
        for index, line in enumerate(lines):
            page.insert_text((60, 80 + 22 * index), line, fontname="F0", fontsize=12)
        doc.save(folder / name)


def run(url: str, chromium: str | None) -> None:
    obj = f"UI-{int(time.time())}"
    errors: list[str] = []
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
        folder = Path(tmp)
        _write(folder)
        browser = p.chromium.launch(executable_path=chromium) if chromium else p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.goto(url, wait_until="networkidle")
        for title, code, name in (("Проектная документация", "UI-PD", "ui-pd.pdf"),
                                  ("Рабочая документация", "UI-RD", "ui-rd.pdf")):
            card = page.locator("section").filter(has=page.get_by_text(title, exact=True)).last
            card.get_by_placeholder("Идентификатор объекта").fill(obj)
            card.get_by_placeholder("Шифр").fill(code)
            card.get_by_placeholder("Например, 2").fill("1")
            card.locator("select").first.select_option(label="Утверждён")
            card.locator("input[type=date]").fill("2026-01-01")
            card.locator("input[type=file]").set_input_files(str(folder / name))
            expect(card.get_by_role("paragraph").filter(has_text=name).first).to_be_visible(
                timeout=30000)
            print("загружен", name)
        kit = page.locator("section").filter(has=page.get_by_text("Комплект для проверки")).last
        kit.locator("select").first.select_option(obj)
        page.wait_for_timeout(1000)
        page.get_by_role("button", name="Запустить проверку").click()
        expect(page.get_by_text("Кандидатов без решения инспектора: 1")).to_be_visible(
            timeout=300000)
        print("протокол готов, 1 кандидат")
        row = page.locator("tr").filter(has_text="M-001").filter(
            has=page.get_by_role("button", name="Подробнее")).first
        row.get_by_role("button", name="Подробнее").click()
        # Страница опрашивает сервер и перерисовывается: раскрытая строка
        # ищется по её содержимому, а не по соседству с исходной.
        detail = page.locator("tr").filter(
            has=page.get_by_role("button", name="Сохранить решение")).first
        expect(detail).to_be_visible(timeout=30000)
        detail.locator("select").first.select_option("NEGATIVE_VERIFIED")
        detail.get_by_placeholder("Инспектор *").fill("Инспектор")
        detail.get_by_placeholder("Основание *").fill("согласованное изменение")
        detail.locator("select").nth(1).select_option("APPROVED_CHANGE")
        detail.get_by_role("button", name="Сохранить решение").click()
        expect(page.get_by_text("Кандидатов без решения инспектора: 0")).to_be_visible(
            timeout=30000)
        print("решение сохранено")
        panel = page.locator("section").filter(has_text="Кандидатов без решения").last
        panel.get_by_placeholder("Инспектор *").fill("Инспектор")
        panel.get_by_role("button", name="Завершить").click()
        expect(page.get_by_text("Протокол финализирован").first).to_be_visible(timeout=30000)
        print("протокол финализирован")
        browser.close()
    if errors:
        sys.exit("ошибки страницы: " + "; ".join(errors))
    print("ГОТОВО: сценарий инспектора пройден, ошибок на странице нет")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://127.0.0.1:5173/")
    parser.add_argument("--chromium", default=None, help="путь к уже установленному Chromium")
    args = parser.parse_args()
    run(args.url, args.chromium)


if __name__ == "__main__":
    main()
