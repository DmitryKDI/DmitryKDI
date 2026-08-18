"""Разбор PDF: текстовые блоки и таблицы с координатами ячеек.

Встроенный код документа не выполняется, внешние ссылки не разрешаются:
используется только извлечение текста и геометрии.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pymupdf

from documents.schemas import IntakeError

BBox = tuple[float, float, float, float]


@dataclass
class Cell:
    text: str
    bbox: BBox


@dataclass
class TableData:
    page: int
    bbox: BBox
    header: list[str]
    rows: list[list[Cell]] = field(default_factory=list)

    def header_signature(self) -> str:
        """Подпись таблицы по заголовку — по ней выбирается способ извлечения."""
        return "|".join((h or "").replace("\n", " ").strip().lower() for h in self.header)


@dataclass
class Block:
    page: int
    text: str
    bbox: BBox
    size: float


@dataclass
class PageData:
    page: int
    width: float
    height: float
    blocks: list[Block] = field(default_factory=list)
    tables: list[TableData] = field(default_factory=list)


def _cell_bbox(raw, fallback: BBox) -> BBox:
    if raw is None:
        return fallback
    return (float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]))


def _read_tables(page, page_no: int) -> list[TableData]:
    tables: list[TableData] = []
    try:
        finder = page.find_tables()
    except Exception:      # noqa: BLE001 — повреждённая страница не должна ронять разбор
        return tables
    for t in finder.tables:
        extracted = t.extract()
        if not extracted:
            continue
        header = [(c or "") for c in extracted[0]]
        rows: list[list[Cell]] = []
        for i, row in enumerate(extracted[1:], start=1):
            cells_geom = t.rows[i].cells if i < len(t.rows) else []
            cells = []
            for j, value in enumerate(row):
                geom = cells_geom[j] if j < len(cells_geom) else None
                cells.append(Cell(text=(value or "").strip(), bbox=_cell_bbox(geom, t.bbox)))
            rows.append(cells)
        tables.append(TableData(page=page_no, bbox=tuple(float(v) for v in t.bbox),
                                header=header, rows=rows))
    return tables


def _read_blocks(page, page_no: int) -> list[Block]:
    blocks: list[Block] = []
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        parts, size = [], 0.0
        for line in b["lines"]:
            parts.append("".join(s["text"] for s in line["spans"]))
            for s in line["spans"]:
                size = max(size, float(s["size"]))
        text = " ".join(p.strip() for p in parts if p.strip())
        if text:
            blocks.append(Block(page=page_no, text=text,
                                bbox=tuple(float(v) for v in b["bbox"]), size=size))
    return blocks


def parse(data: bytes, max_pages: int = 1500) -> list[PageData]:
    """Разобрать PDF в страницы с блоками и таблицами."""
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:      # noqa: BLE001
        raise IntakeError("Файл не читается как PDF. Возможно, он повреждён.") from exc
    if doc.needs_pass:
        raise IntakeError("PDF защищён паролем. Загрузите документ без защиты.")
    if doc.page_count > max_pages:
        raise IntakeError(f"В документе {doc.page_count} листов, допускается не более {max_pages}.")
    pages: list[PageData] = []
    for i, page in enumerate(doc, start=1):
        pages.append(PageData(page=i, width=float(page.rect.width), height=float(page.rect.height),
                              blocks=_read_blocks(page, i), tables=_read_tables(page, i)))
    doc.close()
    return pages


def has_text_layer(pages: list[PageData]) -> bool:
    """Есть ли текстовый слой. Если нет — документ уходит в OCR."""
    return sum(len(p.blocks) for p in pages) > 3


def render_png(data: bytes, page_no: int, dpi: int = 110) -> bytes:
    """Отрисовать лист для показа в интерфейсе с подсветкой области находки."""
    doc = pymupdf.open(stream=data, filetype="pdf")
    if page_no < 1 or page_no > doc.page_count:
        raise IntakeError("Запрошен несуществующий лист документа.")
    pix = doc[page_no - 1].get_pixmap(dpi=dpi)
    out = pix.tobytes("png")
    doc.close()
    return out
