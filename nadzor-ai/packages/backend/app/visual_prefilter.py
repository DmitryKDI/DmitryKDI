"""Дешёвый пиксельный предфильтр пары чертёжных листов (Приложение Г.54).

Пользовательская находка: `router.py` даёт паре level=0 («пропустить»), если
текстовые реестры (помещения/оборудование) совпали — но конфигурация на
самом чертеже (например, обвязка воздуховодов внутри помещения с тем же
названием) при этом может отличаться и никогда не станет фактом реестра
(равно как ни room_facts, ни equipment_facts её не видят). Реальный
слепой прогон на nadzor_sample: пара с совпавшими реестрами помещений
124/142 пропускалась level=0, хотя воздуховодная обвязка на листе отличалась.

Полноценное зрение (ИИ) на КАЖДОЙ такой паре — не выход: на комплекте в
тысячи листов это взрывает время и стоимость прогона (та самая «экономия
времени», которая измеримый результат задачи, раздел 0.0). Здесь —
промежуточный шаг: сравнить сами рендеры без ИИ (доли секунды, без
внешнего вызова) и промоутить в LLM-очередь только те level=0-пары, где
картинка ДЕЙСТВИТЕЛЬНО другая, а не полагаться на молчание реестров."""
from __future__ import annotations

from typing import Optional

import pymupdf

# Сетка семплов для грубого сравнения — не по-пиксельно (изображения разных
# рендеров почти никогда не совпадают побитово даже для идентичного листа:
# антиалиасинг, версия рендерера), а по регулярной сетке точек яркости.
_GRID = 16
# Порог яркости, начиная с которого точка считается «другой» (0-255).
_POINT_THRESHOLD = 40
# Доля отличающихся точек сетки, выше которой пара считается визуально
# другой, несмотря на совпавшие текстовые реестры.
DIFF_RATIO_THRESHOLD = 0.12


def _render_gray(pdf_path: str, page_no: int, max_dim: int = 300):
    doc = pymupdf.open(pdf_path)
    try:
        page = doc[page_no - 1]
        rect = page.rect
        scale = max_dim / max(rect.width, rect.height)
        scale = min(scale, 4.0)
        return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csGRAY)
    finally:
        doc.close()


def _diff_cells(before_path: str, before_page: int, after_path: str, after_page: int) -> set[tuple[int, int]]:
    """Множество координат (i, j) клеток сетки _GRID×_GRID, различающихся по
    яркости сильнее _POINT_THRESHOLD — общий проход, на котором построены и
    `visual_diff_ratio` (Г.54, да/нет для всего листа), и `diff_hot_zone`
    (Г.55, ГДЕ именно на листе отличие, не только сам факт)."""
    pb = _render_gray(before_path, before_page)
    pa = _render_gray(after_path, after_page)
    cells: set[tuple[int, int]] = set()
    for i in range(_GRID):
        for j in range(_GRID):
            xb = min(int(i / _GRID * pb.width), pb.width - 1)
            yb = min(int(j / _GRID * pb.height), pb.height - 1)
            xa = min(int(i / _GRID * pa.width), pa.width - 1)
            ya = min(int(j / _GRID * pa.height), pa.height - 1)
            vb = pb.pixel(xb, yb)[0]
            va = pa.pixel(xa, ya)[0]
            if abs(vb - va) > _POINT_THRESHOLD:
                cells.add((i, j))
    return cells


def visual_diff_ratio(before_path: str, before_page: int, after_path: str, after_page: int) -> float:
    """Доля точек регулярной сетки _GRID×_GRID, различающихся по яркости
    сильнее _POINT_THRESHOLD, между серым рендером двух листов. 0.0 — листы
    визуально неотличимы на этом разрешении, 1.0 — полностью разные.
    Работает через `Pixmap.pixel` (не сырые байты `samples`) — устойчиво к
    страйду/выравниванию растра, не требует внешних библиотек (PIL и т.п.)
    сверх уже используемого pymupdf."""
    return len(_diff_cells(before_path, before_page, after_path, after_page)) / (_GRID * _GRID)


# Доля площади листа (по каждой стороне), выше которой зона отличий
# считается «весь лист», а не локальным пятном — кроп такой ширины не
# сужает картинку модели, смысла в нём нет.
_HOT_ZONE_MAX_FRACTION = 0.75
# Отступ вокруг найденной зоны отличий, в долях стороны листа — без запаса
# кроп рискует обрезать сам изменившийся элемент по краю (Г.22: граница
# зоны должна быть привязана не впритык к первому найденному признаку).
_HOT_ZONE_PADDING = 0.06


def diff_hot_zone(
    before_path: str, before_page: int, after_path: str, after_page: int,
) -> Optional[tuple[float, float, float, float]]:
    """Приложение Г.55 — не только «отличается ли лист» (Г.54), но и ГДЕ:
    прямоугольник (x0, y0, x1, y1) в ДОЛЯХ ширины/высоты листа (0.0-1.0,
    одинаково применим к обеим сторонам пары даже при разных физических
    размерах листов), охватывающий все отличающиеся клетки сетки с
    отступом `_HOT_ZONE_PADDING`.

    Возвращает `None` в двух разных по смыслу случаях — вызывающий код не
    обязан их различать (оба означают «сравнивай лист целиком, как
    раньше»), но причины разные:
      - отличий нет вовсе (лист визуально идентичен);
      - отличия разбросаны почти по всему листу (>_HOT_ZONE_MAX_FRACTION
        по любой стороне) — локализовывать нечего, кроп не сузил бы
        картинку модели.

    Мотивация — реальный пропуск слепого прогона (Г.54/55): насыщенный
    поэтажный план с сотней помещений, где одно локальное изменение
    воздуховодной обвязки тонет в общей картинке при сравнении листа
    целиком (`compare_page_pair` в vision.py). Сетка отличий уже строится
    для Г.54 — здесь тот же проход возвращает не долю, а координаты."""
    cells = _diff_cells(before_path, before_page, after_path, after_page)
    if not cells:
        return None
    xs = [c[0] for c in cells]
    ys = [c[1] for c in cells]
    x0 = min(xs) / _GRID
    x1 = (max(xs) + 1) / _GRID
    y0 = min(ys) / _GRID
    y1 = (max(ys) + 1) / _GRID
    if (x1 - x0) > _HOT_ZONE_MAX_FRACTION or (y1 - y0) > _HOT_ZONE_MAX_FRACTION:
        return None
    x0 = max(0.0, x0 - _HOT_ZONE_PADDING)
    y0 = max(0.0, y0 - _HOT_ZONE_PADDING)
    x1 = min(1.0, x1 + _HOT_ZONE_PADDING)
    y1 = min(1.0, y1 + _HOT_ZONE_PADDING)
    return (x0, y0, x1, y1)


def is_visually_different(before_path: str, before_page: int, after_path: str, after_page: int) -> bool:
    """True, если визуальный предфильтр находит лист достаточно другим,
    чтобы промоутить level=0-пару (реестры совпали) в очередь на LLM,
    несмотря на совпавшие текстовые реестры (Г.54). Сбой рендера (битый
    файл, неверная страница) — не считается расхождением, level=0 остаётся
    в силе: у предфильтра нет права ложно поднимать бюджет там, где сам
    рендер не удался (это отдельная, уже видимая проблема — DocumentInput
    выше по цепочке, не этот шаг)."""
    try:
        return visual_diff_ratio(before_path, before_page, after_path, after_page) > DIFF_RATIO_THRESHOLD
    except Exception:  # noqa: BLE001 — рендер одного листа не должен ронять весь прогон
        return False
