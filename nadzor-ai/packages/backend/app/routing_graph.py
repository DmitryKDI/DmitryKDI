"""Граф маршрутизации «помещение → распределительная ветка → точка сбора»
(Г.30, п.3) — рёбра, а не множества меток.

Зачем. Г.29 показала эмпирически: реестр присутствия (`registry_diff.py`)
структурно слеп к нарушениям класса 2. Помещения есть с обеих сторон,
номера веток в ПД и РД разные (сама по себе перенумерация — не нарушение),
а находка в том, что маршрут одного помещения в РД физически ведёт к другой
точке сбора или к другому числу присоединений. Это видно только по РЁБРАМ,
поэтому здесь строится граф, а не ещё один реестр ключей.

Как. Чистая геометрия, без модели и без зрения (Г.7/Г.30: зрение — последний
шаг эскалации, не первый источник). Источники — только `page.get_drawings()`
(полилинии) и `page.get_text("words")` (bbox подписей). Вызовов LLM/vision в
этом модуле нет ни одного и быть не должно.

## Что реально измерено на живых листах (а не предположено)

Замеры сделаны на `АНО-150321-1-РД-ОВ1 изм. 4_в1 (1)-1-100.pdf`, стр. 17
(план 1 этажа, вентиляция; 2384×3370 pt, 152 410 путей / 338 113 примитивов)
и `АНО-150321-1-РД-ОВ2.1_изм. 3_в1.pdf`, стр. 16 (план 2 этажа, 164 576
путей).

1. **Подписей веток в текстовом слое НЕТ.** Прогон обеих книг целиком
   (136 страниц) регуляркой `^[А-ЯЁ]{1,2}\\d{1,2}[.,]\\d{1,2}$` по
   `get_text("words")` дал ровно одно попадание на все 136 страниц
   (`К6.2`, стр. 5) и ни одного вхождения «поз.»/«М.О.» на планах. Глазами
   на рендере той же стр. 17 подписи `В2.7`, `В2.8`, `В2.9`, `В2.10`,
   `М.О. поз.159/160/169` читаются без труда — они нарисованы кривыми
   (Г.13, CAD-текст в кривых). Это та же поправка, что уже сделана в
   `balance_box.py`: «извлекается чистым текстом» было утверждением по
   прочтению рендера глазами, а не по факту текстового извлечения.
   Следствие для архитектуры: геометрический движок здесь отделён от
   источника подписей. `collect_labels()` — быстрый текстовый путь;
   `build_routing_graph(page, labels=...)` принимает подписи откуда угодно
   (в том числе из очереди на зрение, Г.30 п.5) и строит по ним граф той же
   геометрией. Пустой результат текстового пути — видимое состояние с
   причиной в `RoutingGraph.notes` (Г.10), а не «маршрутов нет».
2. **Номера помещений на плане текстом ЕСТЬ.** На стр. 17 `140`, `141`,
   `142`, `147`, `198` находятся и в экспликации (x≈456, y>2600), и на
   самом плане (`140` → (2197.1, 655.1), `141` → (2171.2, 885.4),
   `142` → (2160.7, 469.8)). То есть привязка ребра к помещению текстом
   работает, а привязка к ветке — нет.
3. **Устройство выноски.** Замер выноски `В2.7`: подпись несёт СОБСТВЕННЫЙ
   нарисованный прямоугольник (2093.1, 822.3)–(2113.4, 833.8) с диагональю,
   от угла которого идёт линия длиной 35.4 pt к острию на стояке
   (2060.2, 820.6). У `М.О. поз.169` то же самое, но выноска из двух
   звеньев: полка (2316.4, 823.4)–(2347.6, 823.4) плюс наклонная до острия
   (2266.7, 860.1). Звенья одной выноски делят узел и попадают в одну
   компоненту связности — «декорацию подписи».
4. **Цепь воздуховода прослеживается, но НЕ приварена к выноске.** Острие
   выноски `В2.7` не совпадает узлом ни с чем: ближайший узел цепи
   воздуховода — в 2.26 pt. Сама цепь при этом настоящая и длинная: от
   стояка (x≈2021) до символа местного отсоса (x≈2256), 138 узлов, и она
   же несёт магистраль Ø200 к `М.О. поз.169`. То есть трассировка
   «ветка → точка сбора» физически проходит, но соединение подписи с
   цепью — это ЗАЗОР, а не общий узел.
5. **Символ точки сбора наложен на воздуховод, а не сварен с ним.** Замер
   `М.О. поз.169` при 16-кратном увеличении: оранжевый прямоугольник
   (2236.0, 860.2)–(2266.3, 879.9) с чёрным крестом внутри, синий
   воздуховод входит слева и заканчивается пурпурным кружком в центре.
   Остриё выноски `М.О.` попадает в ВЕРХНИЙ ПРАВЫЙ УГОЛ оранжевой рамки,
   а рамка ПЕРЕСЕКАЕТ линии воздуховода, нигде с ними не совпадая узлами.
   Расстояние от острия выноски до ближайшего УЗЛА цепи — 13.2 pt, а до
   ближайшего ОТРЕЗКА цепи — 0 (пересечение).

Прямое следствие пп. 4–5 для алгоритма: связность «по общим узлам»
замыкает выноску на цепь только на одной стороне и не замыкает на другой.
Поэтому стык подписи с цепью считается по расстоянию ОТРЕЗОК–ОТРЕЗОК
(включая пересечение), а не по совпадению узлов. Проверено, что оба замера
при этом разрешаются однозначно: `В2.7` → цепь на 0.68 pt, `М.О. поз.169`
→ та же цепь через пересечение оранжевой рамки.

6. **Глобальное увеличение допуска связности вместо этого — тупик.**
   Замер: при snap 0.6 pt цепь воздуховода = 138 узлов из 10 973 в зоне
   (чисто), при snap 1.5 pt выноска и цепь оказываются в одной компоненте
   — но эта компонента уже 4628 узлов, то есть почти вся зона слиплась и
   «связность» перестаёт что-либо значить. Поэтому snap оставлен малым, а
   стык делается точечно, у самой подписи.

## Наблюдённые источники ложных срабатываний (правила поставлены по ним, Г.11)

* **Контуры глифов CAD-текста — тоже пути `get_drawings()`.** Вокруг bbox
  подписи `В2.7` нашлось 39 узлов в 5+ компонентах: штрихи букв «В», «2»,
  «.», «7» (отрезки длиной 4–7.5 pt). Все они касаются bbox подписи и
  отсеиваются как её декорация — вместе с рамкой подписи, её диагональю и
  самой выноской.
* **Осевые и строительные линии проходят вплотную к символу.** У точки
  сбора `М.О. поз.169` ближайший к выноске примитив — одиночный
  вертикальный отрезок x≈2265.6, тянущийся через весь лист (y от 157 до
  934): стена. Он бы стал вторым кандидатом. Отсюда правило: цепь
  маршрута — это ЛОМАНАЯ из многих звеньев; компонента из 2–3 узлов
  (осевая линия, стрелка выноски, засечка стояка) маршрутом не считается.
  Замер разделяет классы с запасом: засечки 2 узла, цепь 138.
* **Стена как крупная компонента рядом.** У острия `В2.7` штриховка стен —
  компонента в 2896 узлов на расстоянии 4.26 pt, тогда как настоящая цепь
  — на 0.68 pt. Отсюда `join_radius` по умолчанию 1.5 pt, а не «с
  запасом»: 4-кратный зазор между настоящим стыком и ближайшей стеной.
* **Полка чужой выноски проходит вплотную к соседней подписи.** Выноска
  `В2.8` — (2060.0, 808.7)–(2116.4, 816.0) — идёт на 6.3 pt выше рамки
  `В2.7`. При допуске касания больше ~6 pt она попала бы в декорацию
  `В2.7`. Поэтому `touch_tolerance` мал (1.5 pt).
* **Номер помещения встречается на листе дважды** — в экспликации и на
  плане (замер, п.2). Отдельного детектора таблицы здесь нет и по замеру
  не потребовалось: при прогоне по ВСЕЙ странице (обе позиции каждого
  номера поданы как метки) привязка по близости к цепи выбрала позицию на
  плане, а не в таблице — таблица отстоит от цепи на тысячи pt. Если на
  другом листе так не выйдет, средство — параметр `clip` (ограничить зону
  плана, Г.22); равноудалённые позиции честно уходят в `room_candidates`.
* **`В2`, `В3`, `В4` в экспликации — это графа «Кат. пом.»** (категория
  пожароопасности помещения), а не код системы: на стр. 17 таких слов 19
  штук, все с y>2600, то есть в таблице. Регулярка ветки требует точку и
  цифру после неё (`В2.7`), поэтому категория под неё не подходит — но
  расширять регулярку до `^[А-ЯЁ]\\d+$` нельзя, тест это фиксирует.

## Что осталось незакрытым (открытые наблюдения, а не решённые вопросы)

* Если выноска приварена к воздуховоду общим узлом (одна компонента с
  подписью), цепь будет отброшена вместе с декорацией подписи. На двух
  проверенных листах такого не встретилось — правило по Г.11 не вводится
  до наблюдения, ограничение зафиксировано здесь.
* Привязка ребра к помещению — по близости номера к цепи. Это работает,
  пока кружок номера стоит внутри обслуживаемого помещения; на планах с
  выносными подписями номеров правило потребует пересмотра.

## Почему модуль НЕ подключён в `documents.py` автоматически

По образцу `balance_vision.py`, который тоже не зовётся из общего конвейера.
Причина здесь — измеренная цена, а не принцип: разбор стр. 17 целиком даёт
349 573 отрезка за 3.6 с, сеть связности с индексом — ещё 2.6 с, итого ~6 с
на страницу (для сравнения: `extract_room_facts` по той же странице —
доли секунды). На книге в 100 листов это минуты против секунд у остальных
экстракторов, и при этом (п.1) текстовый путь на CAD-листах всё равно не
даст подписей веток — сплошной проход платил бы полную цену за заведомо
пустой результат. Поэтому граф строится точечно: по уже сопоставленной паре
листов (`matching.py`) и по уже известной зоне (`clip`). Точка входа для
конвейера — `routing_facts_for_page()`, отдающая тот же формат
`[{page, room_key, branch_code, target_code, resolved, ambiguous, ...}]`,
что `room_facts`/`equipment_facts`/`balance_facts`, чтобы подключение одной
строкой оставалось возможным, когда цена перестанет быть проблемой.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Sequence

Point = tuple[float, float]
Bbox = tuple[float, float, float, float]
Segment = tuple[Point, Point]

KIND_ROOM = "room"
KIND_BRANCH = "branch"
KIND_TARGET = "target"
# Ось/отметка (axes.py) как якорь, когда на листе нет экспликации помещений
# (Г.5: «план ↔ план (КР, КЖ, КМ) — оси + отметка»). Значение строки
# намеренно совпадает с `axes.KIND_AXIS`/`axes.KIND_ELEVATION`, чтобы
# `AxisAnchor`, обёрнутый в `Label`, не требовал перевода констант.
KIND_AXIS = "axis"
KIND_ELEVATION = "elevation"
# Типы якоря, которые по умолчанию участвуют в поиске «ближайшего» для
# привязки цепи маршрута к месту. Отметка высоты (KIND_ELEVATION) сюда
# намеренно НЕ входит: это указатель уровня/этажа для листа в целом, а не
# точечный ориентир в плане XY — как ближайший сосед для конкретной цепи
# она не осмыслена, в отличие от номера помещения или оси. Передать её
# явно через `anchor_kinds` вызывающий код всё равно может.
DEFAULT_ANCHOR_KINDS: tuple[str, ...] = (KIND_ROOM, KIND_AXIS)

# Код распределительной ветки на схеме: «В2.7», «П3.1», «К6.2». Точка с
# цифрой обязательна — без неё под шаблон попадает графа «Кат. пом.»
# экспликации («В2», «В3», «В4»), см. наблюдение в докстринге.
#
# Оба шаблона подобраны под формат обозначений раздела ОВ (В — вентиляция,
# П — приток, К — кондиционирование). Формат кодов веток/точек сбора у
# других разделов (кабельные линии ЭОМ, стояки ВК) не измерен — сеть связности
# (`SegmentNetwork`/`trace_label`/`diff_routing_graphs`) сама по себе
# дисциплино-независима, только распознавание подписей завязано на эти
# регулярки. `collect_labels()` принимает подписи откуда угодно, так что для
# другой дисциплины нужен только другой экстрактор подписей, не переписывание
# геометрии — но такого экстрактора пока нет (Г.11: не изобретать формат
# заранее второго реального документа).
BRANCH_CODE_RE = re.compile(r"^[А-ЯЁ]{1,2}\d{1,2}[.,]\d{1,2}$")
# Точка сбора: местный отсос «М.О. поз.169» либо просто «поз.169»/«поз. 169».
TARGET_CODE_RE = re.compile(r"^(?:М\.?\s?О\.?\s*)?поз\.?\s*\d{1,4}$", re.I)

# Шаг снапа концов отрезков в сети связности, pt. Мал намеренно: при 1.5 pt
# зона слипается в одну компоненту на 4628 узлов (замер, п.6 докстринга).
DEFAULT_SNAP = 0.6
# Что считать декорацией подписи: отрезок, у которого конец не дальше этого
# от bbox подписи. 1.5 pt — полка чужой выноски проходит в 6.3 pt.
DEFAULT_TOUCH_TOLERANCE = 1.5
# Зазор между декорацией подписи и цепью маршрута, pt. Замер: настоящий
# стык 0.68 pt, ближайшая посторонняя стена 4.26 pt.
DEFAULT_JOIN_RADIUS = 1.5
# Меньше узлов — это осевая линия, стрелка выноски или засечка стояка, а не
# маршрут. Замер разделяет классы с запасом: 2 узла против 138.
DEFAULT_MIN_CHAIN_NODES = 4
# Два номера помещений, равноудалённых от цепи с точностью до размера
# самого кружка номера (замер: кружок ~15 pt), по близости не различимы.
DEFAULT_ROOM_MARGIN = 20.0
# Шаг сетки пространственного индекса отрезков, pt.
_GRID_CELL = 8.0


@dataclass(frozen=True)
class Label:
    """Подпись на листе с её геометрией. `kind` — room/branch/target.

    Источник подписи модулю безразличен: текстовый слой (`collect_labels`)
    или внешняя очередь (зрение, ручной замер) — движок один и тот же."""
    text: str
    bbox: Bbox
    kind: str

    @property
    def center(self) -> Point:
        return ((self.bbox[0] + self.bbox[2]) / 2, (self.bbox[1] + self.bbox[3]) / 2)


@dataclass(frozen=True)
class RoutingEdge:
    """Ребро графа маршрутизации. Неразрешённость и неоднозначность —
    поля структуры, а не отсутствие записи (Г.10)."""
    branch_code: str
    room_key: str | None = None
    target_code: str | None = None
    resolved: bool = False
    ambiguous: bool = False
    reason: str = ""
    target_candidates: tuple[str, ...] = ()
    room_candidates: tuple[str, ...] = ()
    chain_size: int = 0

    def as_fact(self) -> dict:
        fact: dict = {
            "branch_code": self.branch_code,
            "room_key": self.room_key,
            "target_code": self.target_code,
            "resolved": self.resolved,
            "ambiguous": self.ambiguous,
        }
        if self.reason:
            fact["reason"] = self.reason
        if self.target_candidates:
            fact["target_candidates"] = list(self.target_candidates)
        if self.room_candidates:
            fact["room_candidates"] = list(self.room_candidates)
        return fact


@dataclass(frozen=True)
class RoutingGraph:
    """Граф листа плюс объяснение того, чего в нём нет.

    `notes` — видимое состояние (Г.10): пустой `edges` при богатой векторной
    графике значит «подписи не извлеклись», а не «маршрутов на листе нет»."""
    edges: tuple[RoutingEdge, ...] = ()
    labels: tuple[Label, ...] = ()
    notes: tuple[str, ...] = ()
    segment_count: int = 0
    component_count: int = 0

    def resolved(self) -> list[RoutingEdge]:
        return [e for e in self.edges if e.resolved]

    def unresolved(self) -> list[RoutingEdge]:
        return [e for e in self.edges if not e.resolved and not e.ambiguous]

    def ambiguous(self) -> list[RoutingEdge]:
        return [e for e in self.edges if e.ambiguous]

    def as_facts(self) -> list[dict]:
        return [e.as_fact() for e in self.edges]


# --------------------------------------------------------------------------
# Элементарная геометрия
# --------------------------------------------------------------------------

def _expand(bbox: Bbox, margin: float) -> Bbox:
    return (bbox[0] - margin, bbox[1] - margin, bbox[2] + margin, bbox[3] + margin)


def _inside(pt: Point, bbox: Bbox) -> bool:
    return bbox[0] <= pt[0] <= bbox[2] and bbox[1] <= pt[1] <= bbox[3]


def _bbox_distance(pt: Point, bbox: Bbox) -> float:
    dx = max(bbox[0] - pt[0], 0.0, pt[0] - bbox[2])
    dy = max(bbox[1] - pt[1], 0.0, pt[1] - bbox[3])
    return math.hypot(dx, dy)


def _point_segment_distance(p: Point, a: Point, b: Point) -> float:
    vx, vy = b[0] - a[0], b[1] - a[1]
    length2 = vx * vx + vy * vy
    if length2 == 0.0:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = ((p[0] - a[0]) * vx + (p[1] - a[1]) * vy) / length2
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return math.hypot(p[0] - a[0] - t * vx, p[1] - a[1] - t * vy)


def _cross(o: Point, a: Point, b: Point) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _segments_cross(a1: Point, a2: Point, b1: Point, b2: Point) -> bool:
    d1, d2 = _cross(b1, b2, a1), _cross(b1, b2, a2)
    d3, d4 = _cross(a1, a2, b1), _cross(a1, a2, b2)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def segment_distance(a1: Point, a2: Point, b1: Point, b2: Point) -> float:
    """Расстояние между двумя отрезками. Пересекающиеся дают 0 — именно так
    оранжевая рамка символа местного отсоса «сходится» с воздуховодом, не
    имея с ним ни одного общего узла (замер, п.5 докстринга)."""
    if _segments_cross(a1, a2, b1, b2):
        return 0.0
    return min(_point_segment_distance(a1, b1, b2), _point_segment_distance(a2, b1, b2),
               _point_segment_distance(b1, a1, a2), _point_segment_distance(b2, a1, a2))


def _rect_edges(x0: float, y0: float, x1: float, y1: float) -> list[Segment]:
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return list(zip(pts, pts[1:] + pts[:1]))


def page_segments(page, clip: Bbox | None = None) -> list[Segment]:
    """Отрезки листа из `page.get_drawings()`.

    Кривые Безье ('c') сводятся к хорде: для трассировки важна связность
    концов, а не форма дуги — отвод воздуховода рисуется дугой, и хорда
    сохраняет ту же пару узлов. Прямоугольники и квадры разворачиваются в
    рёбра, иначе символ (клапан, местный отсос) не пересечётся с подходящим
    к нему воздуховодом."""
    segments: list[Segment] = []
    for drawing in page.get_drawings():
        if clip is not None:
            rect = drawing["rect"]
            if (rect.x1 < clip[0] or rect.x0 > clip[2]
                    or rect.y1 < clip[1] or rect.y0 > clip[3]):
                continue
        for item in drawing["items"]:
            op = item[0]
            if op == "l":
                segments.append(((item[1].x, item[1].y), (item[2].x, item[2].y)))
            elif op == "c":
                segments.append(((item[1].x, item[1].y), (item[4].x, item[4].y)))
            elif op == "re":
                r = item[1]
                segments.extend(_rect_edges(r.x0, r.y0, r.x1, r.y1))
            elif op == "qu":
                q = item[1]
                pts = [(q.ul.x, q.ul.y), (q.ur.x, q.ur.y),
                       (q.lr.x, q.lr.y), (q.ll.x, q.ll.y)]
                segments.extend(zip(pts, pts[1:] + pts[:1]))
    return segments


class SegmentNetwork:
    """Отрезки листа: компоненты связности по общим узлам плюс сеточный
    индекс для локальных запросов «что проходит рядом с этой точкой».

    Связность считается по СОВПАДЕНИЮ узлов с малым снапом — этого хватает,
    чтобы собрать воздуховод в одну цепь, и не хватает, чтобы слепить лист
    целиком (замер, п.6 докстринга модуля). Стык подписи с цепью через
    зазор делается отдельно, запросом `chains_near`."""

    def __init__(self, segments: Sequence[Segment], snap: float = DEFAULT_SNAP):
        self.snap = snap
        self.segments: list[Segment] = list(segments)
        self.segment_count = len(self.segments)
        self._parent: dict[tuple[int, int], tuple[int, int]] = {}
        self._point: dict[tuple[int, int], Point] = {}
        for a, b in self.segments:
            self._add(a)
            self._add(b)
        for a, b in self.segments:
            self._union(self._key(a), self._key(b))
        cells = list(self._parent)
        known = set(cells)
        for cell in cells:
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if dx or dy:
                        other = (cell[0] + dx, cell[1] + dy)
                        if other in known:
                            self._union(cell, other)
        self._members: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
        for cell in self._parent:
            self._members[self._find(cell)].append(cell)
        self.roots: list[tuple[int, int]] = [self._find(self._key(a))
                                             for a, _ in self.segments]
        self._grid: dict[tuple[int, int], list[int]] = defaultdict(list)
        self._by_root: dict[tuple[int, int], list[int]] = defaultdict(list)
        for idx, (a, b) in enumerate(self.segments):
            for cell in self._cells_along(a, b):
                self._grid[cell].append(idx)
            self._by_root[self.roots[idx]].append(idx)

    # -- union-find -------------------------------------------------------
    def _key(self, pt: Point) -> tuple[int, int]:
        return (round(pt[0] / self.snap), round(pt[1] / self.snap))

    def _add(self, pt: Point) -> None:
        cell = self._key(pt)
        if cell not in self._parent:
            self._parent[cell] = cell
            self._point[cell] = pt

    def _find(self, cell: tuple[int, int]) -> tuple[int, int]:
        parent = self._parent
        while parent[cell] != cell:
            parent[cell] = parent[parent[cell]]
            cell = parent[cell]
        return cell

    def _union(self, a: tuple[int, int], b: tuple[int, int]) -> None:
        ra, rb = self._find(a), self._find(b)
        if ra != rb:
            self._parent[ra] = rb

    # -- сеточный индекс --------------------------------------------------
    @staticmethod
    def _cells_along(a: Point, b: Point) -> set[tuple[int, int]]:
        """Ячейки сетки, через которые проходит отрезок. Длинный отрезок
        (стена, осевая) занимает много ячеек — это плата за то, чтобы
        запрос «что рядом» не пропускал линию, у которой рядом нет концов."""
        steps = int(max(abs(b[0] - a[0]), abs(b[1] - a[1])) / _GRID_CELL) + 1
        cells = set()
        for i in range(steps + 1):
            t = i / steps
            x = a[0] + (b[0] - a[0]) * t
            y = a[1] + (b[1] - a[1]) * t
            cells.add((int(math.floor(x / _GRID_CELL)), int(math.floor(y / _GRID_CELL))))
        return cells

    def _candidate_indices(self, bbox: Bbox, margin: float) -> set[int]:
        x0 = int(math.floor((bbox[0] - margin) / _GRID_CELL))
        x1 = int(math.floor((bbox[2] + margin) / _GRID_CELL))
        y0 = int(math.floor((bbox[1] - margin) / _GRID_CELL))
        y1 = int(math.floor((bbox[3] + margin) / _GRID_CELL))
        found: set[int] = set()
        for cx in range(x0, x1 + 1):
            for cy in range(y0, y1 + 1):
                found.update(self._grid.get((cx, cy), ()))
        return found

    # -- запросы ----------------------------------------------------------
    @property
    def component_count(self) -> int:
        return len(self._members)

    def component_size(self, root) -> int:
        return len(self._members.get(root, ())) if root is not None else 0

    def component_points(self, root) -> list[Point]:
        if root is None:
            return []
        return [self._point[c] for c in self._members[root]]

    def component_indices(self, root) -> list[int]:
        return list(self._by_root.get(root, ()))

    def decoration_indices(self, bbox: Bbox,
                           touch_tolerance: float = DEFAULT_TOUCH_TOLERANCE) -> list[int]:
        """Отрезки декорации подписи целиком.

        Сначала берутся примитивы, у которых хотя бы один конец лежит у
        bbox подписи (рамка, диагональ, штрихи глифов, первое звено
        выноски), затем — ВСЕ примитивы их компонент связности. Второй шаг
        обязателен: у `М.О. поз.169` bbox касается только полки выноски, а
        до воздуховода дотягивается её дальнее звено вместе с оранжевой
        рамкой символа — они в той же компоненте, но самого bbox не
        касаются (замер, п.5 докстринга модуля)."""
        touch = _expand(bbox, touch_tolerance)
        touching = [idx for idx in self._candidate_indices(bbox, touch_tolerance)
                    if _inside(self.segments[idx][0], touch)
                    or _inside(self.segments[idx][1], touch)]
        indices: list[int] = []
        for root in {self.roots[idx] for idx in touching}:
            indices.extend(self.component_indices(root))
        return indices

    def chains_near(
        self, indices: Sequence[int], join_radius: float = DEFAULT_JOIN_RADIUS,
        min_chain_nodes: int = DEFAULT_MIN_CHAIN_NODES,
    ) -> list[tuple[tuple[int, int], float]]:
        """Цепи, подходящие к указанным отрезкам ближе `join_radius`.

        Исключены: сами эти отрезки со своими компонентами (декорация
        подписи) и компоненты короче `min_chain_nodes` узлов — осевые
        линии, стрелки выносок, засечки стояков (замер в докстринге).
        Возвращает [(корень цепи, расстояние)] по возрастанию расстояния."""
        own_roots = {self.roots[i] for i in indices}
        best: dict[tuple[int, int], float] = {}
        for i in indices:
            a1, a2 = self.segments[i]
            bbox = (min(a1[0], a2[0]), min(a1[1], a2[1]),
                    max(a1[0], a2[0]), max(a1[1], a2[1]))
            for j in self._candidate_indices(bbox, join_radius):
                root = self.roots[j]
                if root in own_roots:
                    continue
                if self.component_size(root) < min_chain_nodes:
                    continue
                if best.get(root, math.inf) <= 0.0:
                    continue
                b1, b2 = self.segments[j]
                d = segment_distance(a1, a2, b1, b2)
                if d <= join_radius and d < best.get(root, math.inf):
                    best[root] = d
        return sorted(best.items(), key=lambda kv: kv[1])


def trace_label(
    network: SegmentNetwork, label: Label,
    touch_tolerance: float = DEFAULT_TOUCH_TOLERANCE,
    join_radius: float = DEFAULT_JOIN_RADIUS,
    min_chain_nodes: int = DEFAULT_MIN_CHAIN_NODES,
) -> list[tuple[tuple[int, int], float]]:
    """Цепи, к которым подведена подпись: сначала её декорация (рамка +
    выноска + глифы), потом всё, что подходит к этой декорации ближе
    `join_radius`. Несколько результатов — это развилка, и разрешать её
    здесь нечем; вызывающий обязан пометить ребро `ambiguous`."""
    decoration = network.decoration_indices(label.bbox, touch_tolerance)
    if not decoration:
        return []
    return network.chains_near(decoration, join_radius, min_chain_nodes)


# --------------------------------------------------------------------------
# Подписи из текстового слоя
# --------------------------------------------------------------------------

_MAX_PHRASE_WORDS = 3


def _text_lines(page, clip: Bbox | None = None) -> list[list[tuple]]:
    """Слова листа, сгруппированные по строкам `get_text("words")`."""
    lines: dict[tuple[int, int], list[tuple]] = defaultdict(list)
    for w in page.get_text("words"):
        if clip is not None and not _inside((w[0], w[1]), clip):
            continue
        lines[(w[5], w[6])].append(w)
    return [sorted(words, key=lambda w: w[0]) for words in lines.values()]


def collect_labels(
    page,
    room_keys: Iterable[str] | None = None,
    clip: Bbox | None = None,
    branch_re: "re.Pattern[str]" = BRANCH_CODE_RE,
    target_re: "re.Pattern[str]" = TARGET_CODE_RE,
) -> list[Label]:
    """Быстрый текстовый путь: подписи веток, точек сбора и номеров
    помещений из текстового слоя листа.

    Разбор строки — жадный, от самой длинной склейки к самой короткой:
    «М.О.» и «поз.169» приходят из `get_text("words")` отдельными словами,
    а код точки сбора — это их пара, и без жадности одна подпись дала бы
    сразу две метки («поз.169» и «М.О. поз.169»), а ветка увидела бы на
    своей цепи две точки сбора вместо одной.

    На CAD-листах проверенного комплекта веток и точек сбора здесь не
    будет — они в кривых (замер в докстринге модуля). Это ожидаемый пустой
    результат, а не поломка; `build_routing_graph` фиксирует его в `notes`."""
    keys = set(room_keys or ())
    labels: list[Label] = []
    for words in _text_lines(page, clip):
        i = 0
        while i < len(words):
            for span in range(min(_MAX_PHRASE_WORDS, len(words) - i), 0, -1):
                chunk = words[i:i + span]
                text = " ".join(c[4] for c in chunk)
                if text in keys:
                    kind = KIND_ROOM
                elif branch_re.match(text):
                    kind = KIND_BRANCH
                elif target_re.match(text):
                    kind = KIND_TARGET
                else:
                    continue
                labels.append(Label(
                    text=text, kind=kind,
                    bbox=(min(c[0] for c in chunk), min(c[1] for c in chunk),
                          max(c[2] for c in chunk), max(c[3] for c in chunk))))
                i += span
                break
            else:
                i += 1
    return labels


# --------------------------------------------------------------------------
# Построение графа
# --------------------------------------------------------------------------

def _nearest_anchor(
    anchors: Sequence[Label], points: Sequence[Point], margin: float,
) -> tuple[str | None, tuple[str, ...]]:
    """Ближайший к цепи якорь — номер помещения ПО УМОЛЧАНИЮ, но геометрия
    одинакова для любого источника меток (ось из `axes.py`, обёрнутая в
    `Label(kind=KIND_AXIS)`, работает тем же кодом без изменений — общий
    механизм для листов без экспликации помещений, Г.5). Если второй по
    близости отстоит меньше чем на `margin` — различить их по близости
    нельзя, оба уходят в кандидаты, а ключ остаётся пустым (Г.10:
    неоднозначность видна).

    Статус n=1 для случая якоря-оси: код идентичен уже проверенному пути
    для помещений, но сам путь «нет room_keys → в ход идёт ось» ни разу не
    прогонялся на реальном листе без экспликации — только теоретически
    обобщён из общего механизма `axes.py`."""
    if not anchors or not points:
        return None, ()
    scored: list[tuple[float, str]] = []
    for anchor in anchors:
        scored.append((min(_bbox_distance(pt, anchor.bbox) for pt in points), anchor.text))
    scored.sort()
    if len(scored) > 1 and scored[1][1] != scored[0][1] and scored[1][0] - scored[0][0] < margin:
        return None, tuple(sorted({scored[0][1], scored[1][1]}))
    return scored[0][1], ()


def build_routing_graph(
    page,
    labels: Sequence[Label] | None = None,
    room_keys: Iterable[str] | None = None,
    clip: Bbox | None = None,
    snap: float = DEFAULT_SNAP,
    touch_tolerance: float = DEFAULT_TOUCH_TOLERANCE,
    join_radius: float = DEFAULT_JOIN_RADIUS,
    min_chain_nodes: int = DEFAULT_MIN_CHAIN_NODES,
    room_margin: float = DEFAULT_ROOM_MARGIN,
    anchor_kinds: Sequence[str] = DEFAULT_ANCHOR_KINDS,
) -> RoutingGraph:
    """Граф `anchor_key → branch_code → target_code` для одного листа (поле
    результата по-прежнему называется `room_key` — не переименовано, чтобы
    не ломать уже существующих потребителей (`diff_routing_graphs`,
    `triangulation.py`), но по смыслу это «ключ ближайшего якоря», не
    обязательно номер помещения).

    `labels=None` — подписи берутся из текстового слоя (`collect_labels`,
    только помещения/ветки/точки сбора — ось `collect_labels` не читает,
    её даёт `axes.py` отдельно). Явно переданный список позволяет подать
    подписи из любого источника (очередь на зрение по Г.30 п.5, ручной
    замер, `axes.py` для листа без экспликации — обернуть каждый
    `AxisAnchor` в `Label(a.text, a.bbox, routing_graph.KIND_AXIS)` и
    передать вместе с остальными метками) — геометрия та же.

    `anchor_kinds` — какие типы меток считаются кандидатом на «ближайший
    якорь» (по умолчанию помещение и ось, см. `DEFAULT_ANCHOR_KINDS`);
    несколько типов участвуют в поиске ближайшего ОДНОВРЕМЕННО, не по
    очереди — если рядом с веткой есть и номер помещения, и осевая метка,
    выигрывает тот, что физически ближе. На листе без единого помещения
    (room_keys пуст, в `labels` нет меток KIND_ROOM) это естественным
    образом отдаёт роль якоря оси без отдельной ветки кода "если пусто —
    попробовать другое"."""
    segments = page_segments(page, clip)
    network = SegmentNetwork(segments, snap=snap)
    if labels is None:
        labels = collect_labels(page, room_keys=room_keys, clip=clip)
    labels = tuple(labels)

    anchor_kind_set = set(anchor_kinds)
    rooms = [lb for lb in labels if lb.kind in anchor_kind_set]
    branches = [lb for lb in labels if lb.kind == KIND_BRANCH]
    targets = [lb for lb in labels if lb.kind == KIND_TARGET]

    notes: list[str] = []
    if segments and not branches:
        notes.append(
            f"подписей веток в источнике нет ({len(segments)} векторных отрезков "
            f"на листе): текстовый слой их не несёт — CAD-текст в кривых (Г.13). "
            f"Пустой граф здесь значит «подписи не извлеклись», а не «маршрутов нет»")
    if branches and not targets:
        notes.append("подписи точек сбора в источнике отсутствуют: концы веток "
                     "определить не с чем, все рёбра останутся неразрешёнными")
    if not rooms:
        notes.append(f"якорей типа {sorted(anchor_kind_set)} в источнике нет: "
                     "рёбра будут без room_key")

    def chain_of(label: Label):
        return trace_label(network, label, touch_tolerance, join_radius, min_chain_nodes)

    # Точка сбора СПРАШИВАЕТСЯ, а не выбирается: для неё собирается МНОЖЕСТВО
    # цепей, которых касается её символ, и ребро возникает, когда цепь ветки
    # оказалась среди них. Так и должно быть по смыслу: у одной точки сбора
    # законно несколько присоединённых веток (на замеренном листе рядом с
    # «М.О. поз.169» стоит «В2.7,8,9 −950 м³/ч»), а лишние цепи-кандидаты —
    # стена и строительные линии, по которым лежит символ, — безвредны,
    # пока к ним не подведена ни одна ветка.
    target_chains: dict[tuple[int, int], list[str]] = defaultdict(list)
    untraced_targets: list[str] = []
    multi_chain_targets: list[str] = []
    for target in targets:
        found = chain_of(target)
        if not found:
            untraced_targets.append(target.text)
            continue
        if len(found) > 1:
            multi_chain_targets.append(f"{target.text} ({len(found)})")
        for root, _distance in found:
            if target.text not in target_chains[root]:
                target_chains[root].append(target.text)
    if untraced_targets:
        notes.append("точки сбора без прослеживаемой выноски: "
                     + ", ".join(sorted(set(untraced_targets))))
    if multi_chain_targets:
        notes.append("символ точки сбора лежит сразу на нескольких цепях (в скобках "
                     "— сколько): " + ", ".join(sorted(set(multi_chain_targets)))
                     + "; ребро появится только для той цепи, к которой реально "
                       "подведена ветка")

    edges: list[RoutingEdge] = []
    for branch in branches:
        found = chain_of(branch)
        if not found:
            edges.append(RoutingEdge(
                branch_code=branch.text,
                reason="цепь маршрута у подписи ветки не найдена: подпись ни к "
                       "чему не подведена линией в пределах допуска"))
            continue
        if len(found) > 1:
            edges.append(RoutingEdge(
                branch_code=branch.text, ambiguous=True,
                reason=f"к подписи ветки подходит {len(found)} разных цепей — "
                       "развилка не разрешается в коде, нужна эскалация"))
            continue
        root = found[0][0]
        size = network.component_size(root)
        room_key, room_candidates = _nearest_anchor(
            rooms, network.component_points(root), room_margin)
        reached = target_chains.get(root, [])
        if len(reached) == 1:
            edges.append(RoutingEdge(
                branch_code=branch.text, room_key=room_key, target_code=reached[0],
                resolved=True, room_candidates=room_candidates, chain_size=size))
        elif len(reached) > 1:
            edges.append(RoutingEdge(
                branch_code=branch.text, room_key=room_key, ambiguous=True,
                target_candidates=tuple(sorted(reached)), room_candidates=room_candidates,
                chain_size=size,
                reason=f"в одной цепи с веткой оказалось {len(reached)} точек сбора — "
                       "пересечение или общий коллектор, эвристикой не разрешается"))
        else:
            edges.append(RoutingEdge(
                branch_code=branch.text, room_key=room_key,
                room_candidates=room_candidates, chain_size=size,
                reason="цепь ветки прослежена, но ни одна подпись точки сбора "
                       "в ней не найдена"))
    return RoutingGraph(
        edges=tuple(edges), labels=labels, notes=tuple(notes),
        segment_count=network.segment_count, component_count=network.component_count)


def routing_facts_for_page(page, page_no: int, **kwargs) -> list[dict]:
    """Формат для конвейера — тот же, что у `room_facts`/`balance_facts`:
    `[{page, branch_code, room_key, target_code, resolved, ambiguous, ...}]`.
    Не зовётся из `documents.py` автоматически — см. докстринг модуля."""
    graph = build_routing_graph(page, **kwargs)
    return [{"page": page_no, **fact} for fact in graph.as_facts()]


# --------------------------------------------------------------------------
# Сравнение двух сторон по рёбрам (Г.30 п.3, правило Г.30 п.5)
# --------------------------------------------------------------------------

def diff_routing_graphs(
    before: Sequence[RoutingEdge], after: Sequence[RoutingEdge],
) -> dict[str, list[dict]]:
    """Сравнение ПД и РД по РЁБРАМ, а не по множеству меток веток (Г.29).

    Категории:
      * `renumbered` — то же число присоединений и та же точка сбора при
        других кодах веток: перенумерация, не нарушение (Г.30 п.5);
      * `retargeted` — маршрут помещения ведёт к другой точке сбора;
      * `connection_count_changed` — другое число присоединений;
      * `unchanged` — совпало полностью;
      * `unusable` — у помещения есть неразрешённое/неоднозначное ребро
        хотя бы с одной стороны: вывод по нему делать нельзя, и это
        видимое состояние, а не «расхождений нет» (Г.10);
      * `room_only_before` / `room_only_after` — помещение есть только с
        одной стороны (вопрос комплектности, Г.9, не маршрутизации).
    """
    def group(edges: Sequence[RoutingEdge]) -> dict[str, list[RoutingEdge]]:
        out: dict[str, list[RoutingEdge]] = defaultdict(list)
        for e in edges:
            if e.room_key is not None:
                out[e.room_key].append(e)
        return out

    g_before, g_after = group(before), group(after)
    result: dict[str, list[dict]] = {
        "renumbered": [], "retargeted": [], "connection_count_changed": [],
        "unchanged": [], "unusable": [], "room_only_before": [], "room_only_after": [],
    }
    for room in sorted(set(g_before) - set(g_after)):
        result["room_only_before"].append({"room_key": room})
    for room in sorted(set(g_after) - set(g_before)):
        result["room_only_after"].append({"room_key": room})
    for room in sorted(set(g_before) & set(g_after)):
        eb, ea = g_before[room], g_after[room]
        if any(not e.resolved for e in eb + ea):
            result["unusable"].append({
                "room_key": room,
                "reason": "есть неразрешённое или неоднозначное ребро — "
                          "маршрут сравнивать не на чем"})
            continue
        tb = sorted(e.target_code for e in eb if e.target_code)
        ta = sorted(e.target_code for e in ea if e.target_code)
        bb = sorted(e.branch_code for e in eb)
        ba = sorted(e.branch_code for e in ea)
        entry = {"room_key": room, "before_targets": tb, "after_targets": ta,
                 "before_branches": bb, "after_branches": ba}
        if sorted(set(tb)) != sorted(set(ta)):
            result["retargeted"].append(entry)
        elif len(eb) != len(ea):
            result["connection_count_changed"].append(entry)
        elif bb != ba:
            result["renumbered"].append(entry)
        else:
            result["unchanged"].append(entry)
    return result
