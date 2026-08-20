"""Извлечение сущностей в строгую схему с координатами на листе.

Способ извлечения выбирается по заголовку таблицы, а не по имени файла:
одна и та же логика работает на любом документе с такой же формой.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

from documents.pdf import PageData, TableData
from documents.schemas import Fact, SourceRef

# Номер помещения — общий ключ сшивки требования (ПД) и факта (РД/ИД), не зависящий
# от формы документа: на чертеже это подпись у контура плана, в «Экспликации
# помещений» — строка таблицы с площадью. Обе формы читаются одним и тем же образом,
# без разбора по имени файла.
RE_ROOM_ROW = re.compile(
    r"^(\d{3,4}[а-яё]?)\s+([А-Яа-яЁё][^\d]{1,90}?)(?:\s+(\d+[.,]\d+))?(?:\s+(В\d))?$")

# Общая подпись на несколько однотипных помещений сразу («267, 270 Раздевальная
# и санузул для МГН») — распространённый способ подписать группу МГН-помещений
# на инженерных схемах. Каждому номеру из списка достаётся отдельный факт.
RE_ROOM_LIST_ROW = re.compile(
    r"^((?:\d{3,4}[а-яё]?\s*,\s*)+\d{3,4}[а-яё]?)\s+([А-Яа-яЁё][^\d]{1,90}?)"
    r"(?:\s+(\d+[.,]\d+))?(?:\s+(В\d))?$")

# Канонические имена реквизитов. Слева — как написано в форме документа.
KV_ALIASES = {
    "наименование освидетельствуемых работ": "work",
    "марка конструкции": "mark",
    "отметка": "level",
    "оси": "axes",
    "участок (секция)": "section",
    "применённые материалы": "material",
    "документ о качестве материала": "material_doc",
    "объём выполненных работ": "volume",
    "дата освидетельствования": "act_date",
    "геометрические параметры участка бетонирования": "geometry",
    "лицо, осуществляющее строительство": "contractor",
    "разрешается производство последующих работ": "next_work",
    "номер паспорта": "passport_no",
    "дата выдачи": "issue_date",
    "изготовитель": "supplier",
    "наименование продукции": "product",
    "класс по прочности на сжатие": "concrete_class",
    "марка по морозостойкости": "frost",
    "марка по водонепроницаемости": "water",
    "объём партии": "batch_volume",
    "номер протокола": "report_no",
    "дата испытания": "test_date",
    "акт освидетельствования": "aosr_ref",
    "заявленный класс бетона": "declared_class",
    "фактический класс по прочности": "actual_class",
    "конструкция": "element_name",
    "заключение": "conclusion",
    "член сро": "sro_member",
    "сведения действительны до": "sro_valid_until",
    "статус членства": "sro_status",
    "саморегулируемая организация": "sro_name",
    "заключение экспертизы": "expertise_ref",
    "застройщик": "developer",
}

# Колонки ведомости конструкций.
COLUMN_ALIASES = {
    "марка": "mark",
    "наименование конструкции": "name",
    "отметка": "level",
    "оси": "axes",
    "класс бетона": "concrete_class",
    "марка f/w": "frost_water",
    "арматура": "rebar",
    "сечение / толщина": "size",
}


def join_wrapped(text: str) -> str:
    """Склеить строки внутри ячейки.

    Перенос внутри числа или обозначения («+3.000…+75.00» + «0») склеивается
    без пробела, перенос между словами — с пробелом.
    """
    if not text:
        return ""
    parts = text.split("\n")
    out = parts[0].rstrip()
    for part in parts[1:]:
        nxt = part.lstrip()
        if not nxt:
            continue
        glue = "" if out[-1:].isdigit() or nxt[:1].isdigit() else " "
        out = f"{out}{glue}{nxt}"
    return " ".join(out.split())


def _norm(text: str) -> str:
    return join_wrapped(text).strip().lower()


def _ref(file_id: str, page: int, bbox) -> SourceRef:
    return SourceRef(file_id=file_id, page=page, bbox=tuple(float(v) for v in bbox))


def _facts_kv(table: TableData, doc_id: str, file_id: str, start: int) -> list[Fact]:
    """Таблица «реквизит — значение»."""
    facts = []
    for i, row in enumerate(table.rows):
        if len(row) < 2 or not row[0].text:
            continue
        raw = _norm(row[0].text)
        facts.append(Fact(
            id=f"{doc_id}:{start + i}", doc_id=doc_id, fact_type="kv",
            key=KV_ALIASES.get(raw, raw), value=join_wrapped(row[1].text),
            section="реквизиты", source=_ref(file_id, table.page, row[1].bbox)))
    return facts


def _facts_elements(table: TableData, doc_id: str, file_id: str, start: int) -> list[Fact]:
    """Ведомость конструкций: одна строка — один конструктивный элемент."""
    mapping = {}
    for idx, head in enumerate(table.header):
        key = COLUMN_ALIASES.get(_norm(head or ""))
        if key:
            mapping[key] = idx
    facts = []
    for i, row in enumerate(table.rows):
        if not row or not row[0].text:
            continue
        value = {}
        bbox = row[0].bbox
        for key, idx in mapping.items():
            if idx < len(row):
                value[key] = join_wrapped(row[idx].text)
                if key == "concrete_class":
                    bbox = row[idx].bbox
        facts.append(Fact(
            id=f"{doc_id}:{start + i}", doc_id=doc_id, fact_type="element",
            key=value.get("mark", ""), value=value, section="ведомость конструкций",
            source=_ref(file_id, table.page, bbox)))
    return facts


def _facts_rows(table: TableData, doc_id: str, file_id: str, start: int,
                fact_type: str, section: str, columns: list[str]) -> list[Fact]:
    """Общий случай: строка таблицы превращается в факт с именованными колонками."""
    facts = []
    for i, row in enumerate(table.rows):
        if not row or not any(c.text for c in row):
            continue
        value = {columns[j]: join_wrapped(row[j].text)
                 for j in range(min(len(columns), len(row)))}
        facts.append(Fact(
            id=f"{doc_id}:{start + i}", doc_id=doc_id, fact_type=fact_type,
            key=str(value.get(columns[0], "")), value=value, section=section,
            source=_ref(file_id, table.page, row[0].bbox)))
    return facts


def _table_facts(table: TableData, doc_id: str, file_id: str, start: int) -> list[Fact]:
    sig = table.header_signature()
    if sig.startswith(("реквизит|значение", "показатель|значение")):
        return _facts_kv(table, doc_id, file_id, start)
    if "класс бетона" in sig:
        return _facts_elements(table, doc_id, file_id, start)
    if "наименование приложения" in sig:
        return _facts_rows(table, doc_id, file_id, start, "attachment", "приложения",
                           ["n", "title"])
    if "содержание изменения" in sig:
        return _facts_rows(table, doc_id, file_id, start, "change", "регистрация изменений",
                           ["revision", "sheets", "content", "basis", "date"])
    if "наименование работ" in sig:
        return _facts_rows(table, doc_id, file_id, start, "journal_entry", "журнал работ",
                           ["date", "work", "volume", "responsible", "act"])
    if "замечание" in sig:
        return _facts_rows(table, doc_id, file_id, start, "expertise_remark",
                           "замечания экспертизы", ["n", "text", "resolution"])
    if "фамилия, инициалы" in sig:
        return _facts_rows(table, doc_id, file_id, start, "signatory", "подписи",
                           ["role", "organization", "person", "signature"])
    return []


def _room_fact(doc_id: str, file_id: str, n: int, page: int, bbox, room_no: str, name: str,
              area: str | None, category: str | None) -> Fact:
    return Fact(
        id=f"{doc_id}:{n}", doc_id=doc_id, fact_type="room", key=room_no,
        value={"room_no": room_no, "name": name.strip(" -"),
              "area": area.replace(",", ".") if area else "", "category": category or ""},
        section="помещение", source=_ref(file_id, page, bbox))


def _room_facts(page: PageData, doc_id: str, file_id: str, start: int) -> list[Fact]:
    """Номер, наименование, площадь и категория помещения по подписи на листе."""
    facts, n = [], start
    for block in page.blocks:
        text = block.text.strip()
        if match := RE_ROOM_ROW.match(text):
            room_no, name, area, category = match.groups()
            facts.append(_room_fact(doc_id, file_id, n, page.page, block.bbox,
                                    room_no, name, area, category))
            n += 1
            continue
        if match := RE_ROOM_LIST_ROW.match(text):
            room_list, name, area, category = match.groups()
            for room_no in re.split(r"\s*,\s*", room_list):
                facts.append(_room_fact(doc_id, file_id, n, page.page, block.bbox,
                                        room_no, name, area, category))
                n += 1
    return facts


def _text_facts(pages: list[PageData], doc_id: str, file_id: str, start: int,
                recognized_zones: list | None = None) -> list[Fact]:
    """Абзацы вне таблиц: указания, особые отметки, выводы.

    Из текста исключаются только зоны, где нашлась распознанная таблица
    (её строки уже стали фактами): на насыщенных чертежах общего вида поиск
    таблиц нередко ошибочно принимает за таблицу весь лист целиком — такую
    зону как «занятую» не считаем, иначе пропадает пояснительный текст листа.
    """
    facts, n = [], start
    for page in pages:
        table_zones = recognized_zones if recognized_zones is not None else \
            [t.bbox for t in page.tables]
        section = ""
        for block in page.blocks:
            inside = any(b[0] - 2 <= block.bbox[0] and block.bbox[2] <= b[2] + 2
                         and b[1] - 2 <= block.bbox[1] and block.bbox[3] <= b[3] + 2
                         for b in table_zones)
            if inside:
                continue
            if block.size >= 9.5 and len(block.text) < 90:
                section = block.text.strip()
                continue
            if len(block.text) < 40:
                continue
            facts.append(Fact(id=f"{doc_id}:{n}", doc_id=doc_id, fact_type="text",
                              key=section.lower(), value=block.text, section=section,
                              source=_ref(file_id, page.page, block.bbox)))
            n += 1
    return facts


def extract_page_facts(page: PageData, doc_id: str, file_id: str, start: int) -> list[Fact]:
    """Факты одного листа: таблицы и абзацы вне таблиц."""
    facts: list[Fact] = []
    recognized_zones = []
    for table in page.tables:
        table_facts = _table_facts(table, doc_id, file_id, start + len(facts))
        if table_facts:
            recognized_zones.append(table.bbox)
        facts.extend(table_facts)
    facts.extend(_room_facts(page, doc_id, file_id, start + len(facts)))
    facts.extend(_text_facts([page], doc_id, file_id, start + len(facts), recognized_zones))
    return facts


def extract_facts(pages: Iterable[PageData], doc_id: str, file_id: str) -> list[Fact]:
    """Извлечь факты документа. Принимает и список листов, и поток листов.

    Каждый факт несёт лист и координаты области.
    """
    facts: list[Fact] = []
    for page in pages:
        facts.extend(extract_page_facts(page, doc_id, file_id, len(facts) + 1))
    return facts
