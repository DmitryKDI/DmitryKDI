"""Детерминированный слой маршрутизации пар (Приложение Г.23/Г.25/Г.28).

Трёхуровневая схема принятия решения, отправлять ли пару в LLM:

  Уровень 0 (SKIP):   реестры помещений И оборудования полностью совпали —
                       пара пропускается, LLM не нужен.
  Уровень 2 (LLM):    найдены расхождения в реестрах (помещения/оборудование) —
                       пара ОБЯЗАТЕЛЬНО идёт в LLM.
  Уровень 3 (COND):   текстовая пара, реестры не применимы — текстовый diff
                       идёт в LLM только если значимых расхождений > порога.

Отсев прайса поставщика (Г.28): страницы, где equipment_facts выглядят как
прайс (названия без кириллицы, короткие, артикулы) — помечаются как
excluded и уходят в уровень 0 (пропуск). Дополняет `material.py`, который
отсеивает такие страницы ещё на этапе извлечения по колонтитулу/подытогу
сметы — здесь ловится случай, когда ни один из этих признаков на странице
не текстовый (сама страница уже смешана в один page_kind с проектным
материалом), но состав allegedly-оборудования всё равно похож на прайс.

Порядок обхода: сначала пары уровня 2 (самые подозрительные), потом 3,
уровень 0 показывается как пропущенный. Аналитик может вручную отменить
любой SKIP и отправить пару в LLM — или пропустить LLM-пару, если уверен.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .diffing import word_diff
from .matching import DocumentInput, PagePair

# Порог для текстовых пар: если значимых расхождений (del+add) меньше — пара
# считается «почти совпала» и не идёт в LLM.
TEXT_DIFF_THRESHOLD = 10

# Отсев прайса поставщика (Г.28): короткие названия оборудования без
# кириллицы — это артикулы, марки, координатные отметки, а не реальное
# оборудование. Если на странице большинство позиций такие — это прайс.
_SHORT_NAME_RE = re.compile(r"^[А-ЯЁA-Z]\d{2,}$")  # "В3", "Т12", "PP-R"
_ARTICUL_RE = re.compile(r"^[А-З]\d{3,}$")  # "Т123", "В456"


@dataclass
class PairVerdict:
    """Решение по одной паре — что делать и почему."""
    pair: PagePair
    level: int  # 0 = skip, 2 = LLM required, 3 = LLM conditional
    reason: str  # человекочитаемое объяснение
    # Детали для отчёта:
    room_before: list[dict] = field(default_factory=list)  # room_facts ПД
    room_after: list[dict] = field(default_factory=list)   # room_facts РД
    room_diff: list[str] = field(default_factory=list)     # что не совпало
    equip_before: list[dict] = field(default_factory=list)
    equip_after: list[dict] = field(default_factory=list)
    equip_diff: list[str] = field(default_factory=list)
    diff_count: int = 0  # количество значимых diff-операций (для level 3)


def _room_facts_for_page(entry: DocumentInput, page_no: int) -> list[dict]:
    return [f for f in entry.room_facts if f["page"] == page_no]


def _equip_facts_for_page(entry: DocumentInput, page_no: int) -> list[dict]:
    return [f for f in entry.equipment_facts if f["page"] == page_no]


def _is_price_page(equip_facts: list[dict], room_facts: list[dict]) -> bool:
    """Проверяет, выглядит ли страница как прайс поставщика.

    Критерии:
    1. На странице нет ни одного помещения из реестра — прайс поставщика,
       в отличие от спецификации проекта, обычно не привязан к конкретным
       помещениям листа (Г.14 — тот же принцип, что и для голых номеров:
       без реестра помещений короткие коды сами по себе не доказательство).
    2. Большинство позиций equipment_facts — короткие артикулы/марки без
       кириллицы, либо маркеры прайса («компл.», «-//-», «то же», «шт»)."""
    if not equip_facts or room_facts:
        return False
    price_markers = ("компл.", "-//-", "то же", "тоже", "шт", "арт.", "б/у",
                     "вкл.", "итого", "в т.ч.", "в том числе")
    normal_count = 0
    for f in equip_facts:
        name = f.get("name", "")
        low = name.lower()
        if any(m in low for m in price_markers):
            continue
        if len(name) > 5 and re.search(r"[а-яё]", name):
            normal_count += 1
        # короткое/без кириллицы — не считаем "нормальной" позицией
    # Если менее 15% позиций выглядят как настоящее оборудование — прайс.
    return normal_count / len(equip_facts) < 0.15


def _rooms_match(before: list[dict], after: list[dict]) -> tuple[bool, list[str]]:
    """Сравнить два реестра помещений по ключу.

    Возвращает (совпали ли, список расхождений). Ключ есть в одной стороне,
    но нет в другой — расхождение; ключ есть в обеих, но name/area
    отличаются — тоже расхождение."""
    diffs: list[str] = []
    b_map: dict[str, dict] = {f["key"]: f for f in before}
    a_map: dict[str, dict] = {f["key"]: f for f in after}

    for key in sorted(set(b_map) | set(a_map)):
        if key not in b_map:
            diffs.append(f"отсутствует в ПД: {key} «{a_map[key]['name']}»")
        elif key not in a_map:
            diffs.append(f"отсутствует в РД: {key} «{b_map[key]['name']}»")
        else:
            b, a = b_map[key], a_map[key]
            if b["name"] != a["name"]:
                diffs.append(f"{key}: ПД «{b['name']}» → РД «{a['name']}»")
            if b.get("area") and a.get("area") and b["area"] != a["area"]:
                diffs.append(f"{key} площадь: ПД {b['area']} → РД {a['area']}")

    return (len(diffs) == 0, diffs)


def _equip_match(before: list[dict], after: list[dict]) -> tuple[bool, list[str]]:
    """Сравнить два реестра оборудования по ключу (код позиции). Аналогично
    `_rooms_match`, но для equipment_facts."""
    diffs: list[str] = []
    b_map: dict[str, dict] = {f["key"]: f for f in before}
    a_map: dict[str, dict] = {f["key"]: f for f in after}

    for key in sorted(set(b_map) | set(a_map)):
        if key not in b_map:
            diffs.append(f"отсутствует в ПД: {key} «{a_map[key]['name']}»")
        elif key not in a_map:
            diffs.append(f"отсутствует в РД: {key} «{b_map[key]['name']}»")
        else:
            b, a = b_map[key], a_map[key]
            if b["name"] != a["name"]:
                diffs.append(f"{key}: ПД «{b['name']}» → РД «{a['name']}»")
            if b.get("qty") and a.get("qty") and b["qty"] != a["qty"]:
                diffs.append(f"{key} кол-во: ПД {b['qty']} → РД {a['qty']}")

    return (len(diffs) == 0, diffs)


def _text_diff_count(before_text: str, after_text: str) -> int:
    """Количество значимых diff-операций (del+add, без eq) для текстовой
    пары — грубая мера объёма расхождений. Меньше TEXT_DIFF_THRESHOLD —
    пара «почти совпала» (обычно перенумерация без смыслового отличия),
    не идёт в LLM. Считается тем же `word_diff`, что и `diffing.py`
    (ограничение MAX_DIFF_WORDS оттуда действует и здесь — было бы
    двойным подсчётом реализовывать тот же LCS второй раз)."""
    wa = [w for w in before_text.split() if w]
    wb = [w for w in after_text.split() if w]
    return sum(1 for op in word_diff(wa, wb) if op.type != "eq")


def classify_pair(
    pair: PagePair,
    before_inputs: list[DocumentInput],
    after_inputs: list[DocumentInput],
    before_docs: list[dict],
    after_docs: list[dict],
) -> PairVerdict:
    """Определить уровень обработки для одной пары.

    1. Извлечь room_facts/equipment_facts для страниц пары.
    2. Отсев прайса поставщика (Г.28) — уровень 0.
    3. Сравнить реестры: оба совпали → level=0, иначе → level=2."""
    b_input = before_inputs[pair.before_file_idx]
    a_input = after_inputs[pair.after_file_idx]

    room_b = _room_facts_for_page(b_input, pair.before_page)
    room_a = _room_facts_for_page(a_input, pair.after_page)
    equip_b = _equip_facts_for_page(b_input, pair.before_page)
    equip_a = _equip_facts_for_page(a_input, pair.after_page)

    if _is_price_page(equip_b, room_b) or _is_price_page(equip_a, room_a):
        return PairVerdict(
            pair=pair, level=0,
            reason="прайс поставщика (артикулы без помещений)",
            room_before=room_b, room_after=room_a,
            equip_before=equip_b, equip_after=equip_a,
        )

    room_ok, room_diffs = _rooms_match(room_b, room_a)
    equip_ok, equip_diffs = _equip_match(equip_b, equip_a)

    if not room_ok or not equip_ok:
        reasons = []
        if not room_ok:
            reasons.append(f"комнаты: {len(room_diffs)} расх.")
        if not equip_ok:
            reasons.append(f"оборудование: {len(equip_diffs)} расх.")
        return PairVerdict(
            pair=pair, level=2, reason=" | ".join(reasons),
            room_before=room_b, room_after=room_a, room_diff=room_diffs,
            equip_before=equip_b, equip_after=equip_a, equip_diff=equip_diffs,
        )

    reason_parts = []
    if room_b:
        reason_parts.append(f"комнаты совпали ({len(room_b)})")
    if equip_b:
        reason_parts.append(f"оборудование совпало ({len(equip_b)})")
    if not reason_parts:
        reason_parts.append("нет данных реестров")
    return PairVerdict(
        pair=pair, level=0, reason="; ".join(reason_parts),
        room_before=room_b, room_after=room_a, room_diff=room_diffs,
        equip_before=equip_b, equip_after=equip_a, equip_diff=equip_diffs,
    )


def classify_text_pair(pair: PagePair, before_text: str, after_text: str) -> PairVerdict:
    """Классификатор для текстовых пар (где room/equip реестры обычно
    пустые — акты, спецификации, содержание): текстовый diff > порога →
    level=3, иначе level=0."""
    diff_count = _text_diff_count(before_text, after_text)
    if diff_count > TEXT_DIFF_THRESHOLD:
        return PairVerdict(
            pair=pair, level=3,
            reason=f"текстовый diff: {diff_count} расх. (порог {TEXT_DIFF_THRESHOLD})",
            diff_count=diff_count,
        )
    return PairVerdict(
        pair=pair, level=0,
        reason=f"текстовый diff: {diff_count} расх. (порог {TEXT_DIFF_THRESHOLD}) — почти совпало",
        diff_count=diff_count,
    )


def classify_all_pairs(
    pairs: list[PagePair],
    before_inputs: list[DocumentInput],
    after_inputs: list[DocumentInput],
    before_docs: list[dict],
    after_docs: list[dict],
) -> list[PairVerdict]:
    return [classify_pair(p, before_inputs, after_inputs, before_docs, after_docs) for p in pairs]


def render_report(verdicts: list[PairVerdict]) -> str:
    """Печатный отчёт трёхуровневой схемы — для просмотра аналитиком перед
    ручным выбором пар для LLM."""
    lines = []
    level0 = [v for v in verdicts if v.level == 0]
    level2 = [v for v in verdicts if v.level == 2]
    level3 = [v for v in verdicts if v.level == 3]

    lines.append(f"ИТОГО пар: {len(verdicts)}")
    lines.append(f"  Уровень 0 (пропустить):  {len(level0)}")
    lines.append(f"  Уровень 2 (LLM обязат.): {len(level2)}")
    lines.append(f"  Уровень 3 (LLM условно): {len(level3)}")
    lines.append("")

    if level2:
        lines.append("=== УРОВЕНЬ 2 — ЛЛМ ОБЯЗАТЕЛЕН (расхождения в реестрах) ===")
        for i, v in enumerate(level2, 1):
            p = v.pair
            lines.append(
                f"  {i}. [{p.before_file_idx}:{p.before_page}] "
                f"↔ [{p.after_file_idx}:{p.after_page}] "
                f"score={p.score:.3f} {p.matched_by} ({v.reason})")
            for d in v.room_diff[:5]:
                lines.append(f"      комн: {d}")
            if len(v.room_diff) > 5:
                lines.append(f"      ... ещё {len(v.room_diff) - 5}")
            for d in v.equip_diff[:5]:
                lines.append(f"      обору: {d}")
            if len(v.equip_diff) > 5:
                lines.append(f"      ... ещё {len(v.equip_diff) - 5}")
        lines.append("")

    if level3:
        lines.append("=== УРОВЕНЬ 3 — ЛЛМ УСЛОВНО (текстовый diff) ===")
        for i, v in enumerate(level3, 1):
            p = v.pair
            lines.append(
                f"  {i}. [{p.before_file_idx}:{p.before_page}] "
                f"↔ [{p.after_file_idx}:{p.after_page}] "
                f"score={p.score:.3f} diff={v.diff_count}")
        lines.append("")

    if level0:
        lines.append("=== УРОВЕНЬ 0 — ПРОПУСК (реестры совпали) ===")
        for i, v in enumerate(level0, 1):
            p = v.pair
            lines.append(
                f"  {i}. [{p.before_file_idx}:{p.before_page}] "
                f"↔ [{p.after_file_idx}:{p.after_page}] "
                f"score={p.score:.3f} — {v.reason}")
        lines.append("")

    return "\n".join(lines)
