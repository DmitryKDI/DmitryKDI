"""Обнаружение общей координатной системы между листами разных стадий
(ПД/РД/ИД) — Г.29 (побочное наблюдение), Г.30 п.2.

Наблюдение с реального комплекта: у ПД, РД и исполнительной документации
одного узла нередко общая CAD-подложка — координаты одного и того же
номера помещения на плане совпадают между сторонами до долей единицы
(проверено прямым замером на нескольких листах одного комплекта). Это
дешёвая замена повторному текстовому сопоставлению (Г.4-Г.6): если якорь
уже найден на одной стороне, его координаты можно напрямую перенести на
другую, вместо того чтобы искать заново.

Не универсально — разные разделы/этажи бывают отрисованы заново, поэтому
это ПРОВЕРЯЕМОЕ допущение, не принимается по умолчанию (Г.11): сначала
измеряется совпадение по нескольким уже подтверждённым общим якорям
(`shares_coordinate_system`), и только при устойчивом совпадении общая
система координат считается фактом, а не предположением."""
from __future__ import annotations

DEFAULT_EPSILON = 1.0  # единицы PDF (points) — допуск на совпадение bbox
DEFAULT_MIN_KEYS = 3  # меньше — недостаточно данных, чтобы делать вывод
DEFAULT_MIN_RATIO = 0.8

Bbox = tuple[float, float, float, float]


def bboxes_for_key(page: "pymupdf.Page", key: str) -> list[Bbox]:  # noqa: F821
    """Все места на листе, где `key` встречается как отдельное слово —
    и в таблице экспликации/спецификации, и (если совпадает) на графике
    плана. Намеренно не пытается различить эти случаи — см. п. ниже."""
    return [tuple(w[:4]) for w in page.get_text("words") if w[4] == key]


def _min_bbox_distance(a_boxes: list[Bbox], b_boxes: list[Bbox]) -> float | None:
    """Минимальное расстояние (Чебышёва, по всем 4 координатам bbox) между
    любой парой найденных bbox — если якорь встречается на листе несколько
    раз (таблица + график), для вывода об общей системе координат
    достаточно, чтобы совпала ХОТЯ БЫ одна пара (обычно — графическая:
    таблица может быть переверстана независимо от плана)."""
    if not a_boxes or not b_boxes:
        return None
    best: float | None = None
    for a in a_boxes:
        for b in b_boxes:
            d = max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2]), abs(a[3] - b[3]))
            if best is None or d < best:
                best = d
    return best


def coordinate_match_ratio(
    page_a: "pymupdf.Page", page_b: "pymupdf.Page",  # noqa: F821
    keys: set[str], epsilon: float = DEFAULT_EPSILON,
) -> tuple[float, int]:
    """(доля совпавших координатами якорей, сколько ключей вообще нашлось
    на обеих страницах хоть в каком-то виде). Ключ, отсутствующий на одной
    из сторон, не участвует в знаменателе — это вопрос комплектности
    (Г.9), а не совпадения координат."""
    matched = 0
    total = 0
    for key in keys:
        a_boxes = bboxes_for_key(page_a, key)
        b_boxes = bboxes_for_key(page_b, key)
        if not a_boxes or not b_boxes:
            continue
        total += 1
        distance = _min_bbox_distance(a_boxes, b_boxes)
        if distance is not None and distance <= epsilon:
            matched += 1
    ratio = matched / total if total else 0.0
    return ratio, total


def shares_coordinate_system(
    page_a: "pymupdf.Page", page_b: "pymupdf.Page",  # noqa: F821
    keys: set[str], epsilon: float = DEFAULT_EPSILON,
    min_keys: int = DEFAULT_MIN_KEYS, min_ratio: float = DEFAULT_MIN_RATIO,
) -> bool:
    """True — координаты якорей на этих двух страницах можно переносить
    напрямую, без повторного текстового поиска. `keys` — уже подтверждённые
    общие номера помещений/позиций (например, room_key_set с обеих сторон
    для уже сопоставленной пары листов, Г.9) — функция не сама ищет общие
    ключи, ей их передают, чтобы не дублировать matching.py."""
    ratio, total = coordinate_match_ratio(page_a, page_b, keys, epsilon)
    return total >= min_keys and ratio >= min_ratio


def transfer_anchor_bbox(
    page_a: "pymupdf.Page", page_b: "pymupdf.Page",  # noqa: F821
    anchor_key: str, keys: set[str],
    epsilon: float = DEFAULT_EPSILON, min_keys: int = DEFAULT_MIN_KEYS,
    min_ratio: float = DEFAULT_MIN_RATIO,
) -> list[Bbox]:
    """Bbox якоря `anchor_key`, уже найденного на `page_a`, готовые к
    использованию на `page_b` — но ТОЛЬКО если `shares_coordinate_system`
    подтвердила общую систему координат по независимому набору `keys`
    (иначе перенос — неоправданное допущение, а не находка). При отказе —
    пустой список, а не координаты наугад: вызывающий код должен вернуться
    к обычному текстовому поиску на стороне B, не тихо промолчать (Г.10)."""
    if not shares_coordinate_system(page_a, page_b, keys, epsilon, min_keys, min_ratio):
        return []
    return bboxes_for_key(page_a, anchor_key)
