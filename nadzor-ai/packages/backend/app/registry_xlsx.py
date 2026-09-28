"""Чтение реестра файлов из XLSX (перечень ИД, ред. 1.1: «CSV/XLSX/JSON»).

Реестр — одна таблица: первая строка — заголовки полей, дальше по строке на
файл. XLSX — это ZIP с XML-частями; читаются только общие строки и первый
лист, без формул и стилей. Новой зависимости не нужно.

Реестр приходит от загружающей стороны, поэтому XML с объявлением DTD или
сущностей отклоняется целиком (защита от XXE и раздувания сущностей), а
размер распакованных частей ограничен.
"""
from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET  # noqa: N817
import zipfile

# Сколько байт XML-части реестра допускается распаковать: реестр — это
# таблица на тысячи строк, а не документ; больше — признак ZIP-бомбы.
MAX_PART_BYTES = 20 * 1024 * 1024

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


class RegistryFormatError(ValueError):
    pass


def _xml(archive: zipfile.ZipFile, name: str) -> ET.Element | None:
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_PART_BYTES:
        raise RegistryFormatError("реестр XLSX слишком большой")
    data = archive.read(name)
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise RegistryFormatError("XML с объявлением DTD/сущностей не принимается")
    return ET.fromstring(data)  # noqa: S314 — DTD отклонён выше


def _column(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference.upper())
    index = 0
    for char in letters.group() if letters else "A":
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def _first_sheet(archive: zipfile.ZipFile) -> str:
    workbook = _xml(archive, "xl/workbook.xml")
    rels = _xml(archive, "xl/_rels/workbook.xml.rels")
    if workbook is not None and rels is not None:
        sheet = workbook.find("m:sheets/m:sheet", _NS)
        if sheet is not None:
            rel_id = sheet.get(f"{{{_REL_NS}}}id")
            for rel in rels:
                if rel.get("Id") == rel_id:
                    target = rel.get("Target", "").lstrip("/")
                    return target if target.startswith("xl/") else f"xl/{target}"
    return "xl/worksheets/sheet1.xml"


def _cell_text(cell: ET.Element, shared: list[str]) -> str:
    kind = cell.get("t")
    if kind == "inlineStr":
        return "".join(node.text or "" for node in cell.iterfind(".//m:t", _NS))
    value = cell.find("m:v", _NS)
    text = value.text if value is not None and value.text is not None else ""
    if kind == "s" and text.isdigit() and int(text) < len(shared):
        return shared[int(text)]
    if text.endswith(".0") and text[:-2].lstrip("-").isdigit():
        return text[:-2]  # целое число, записанное Excel как дробное
    return text


def read_rows(data: bytes) -> list[dict]:
    """Строки реестра как словари «поле → значение»; пустые строки пропускаются."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise RegistryFormatError("файл не является XLSX") from exc
    with archive:
        strings = _xml(archive, "xl/sharedStrings.xml")
        shared = ["".join(node.text or "" for node in item.iterfind(".//m:t", _NS))
                  for item in (strings.findall("m:si", _NS) if strings is not None else [])]
        sheet = _xml(archive, _first_sheet(archive))
        if sheet is None:
            raise RegistryFormatError("в XLSX нет листа с реестром")
        table: list[list[str]] = []
        for row in sheet.iterfind(".//m:sheetData/m:row", _NS):
            values: dict[int, str] = {}
            for position, cell in enumerate(row.findall("m:c", _NS)):
                ref = cell.get("r")
                values[_column(ref) if ref else position] = _cell_text(cell, shared).strip()
            if values:
                width = max(values) + 1
                table.append([values.get(index, "") for index in range(width)])
    if not table:
        return []
    header = [name.strip() for name in table[0]]
    rows = []
    for line in table[1:]:
        if not any(line):
            continue
        rows.append({name: (line[index] if index < len(line) else "")
                     for index, name in enumerate(header) if name})
    return rows
