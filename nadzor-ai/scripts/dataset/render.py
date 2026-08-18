"""Рендер демонстрационных документов в PDF с текстовым слоем.

Текстовый слой нужен, чтобы координаты (bbox) извлекались точно и воспроизводимо.
OCR в демо не требуется, но остаётся включаемым для сканированных комплектов.
"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table, TableStyle

FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
FONT = "DejaVu"
FONT_BOLD = "DejaVu-Bold"

_FONTS_READY = False


def _ensure_fonts() -> None:
    global _FONTS_READY
    if _FONTS_READY:
        return
    pdfmetrics.registerFont(TTFont(FONT, str(FONT_DIR / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, str(FONT_DIR / "DejaVuSans-Bold.ttf")))
    pdfmetrics.registerFontFamily(FONT, normal=FONT, bold=FONT_BOLD)
    _FONTS_READY = True


def styles() -> dict:
    _ensure_fonts()
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], fontName=FONT_BOLD, fontSize=12,
                                leading=15, alignment=TA_CENTER, spaceAfter=6),
        "h": ParagraphStyle("h", parent=base["Heading2"], fontName=FONT_BOLD, fontSize=10,
                            leading=13, spaceBefore=6, spaceAfter=4),
        "p": ParagraphStyle("p", parent=base["Normal"], fontName=FONT, fontSize=8.5,
                            leading=12, alignment=TA_JUSTIFY, spaceAfter=3),
        "small": ParagraphStyle("s", parent=base["Normal"], fontName=FONT, fontSize=7,
                                leading=9),
        "cell": ParagraphStyle("c", parent=base["Normal"], fontName=FONT, fontSize=7.5,
                               leading=10),
        "cellb": ParagraphStyle("cb", parent=base["Normal"], fontName=FONT_BOLD, fontSize=7.5,
                                leading=10),
    }


def _stamp_drawer(stamp: dict | None):
    """Основная надпись по ГОСТ Р 21.1101 — правый нижний угол каждого листа."""
    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont(FONT, 6.5)
        canvas.drawRightString(doc.pagesize[0] - 15 * mm, 8 * mm, f"Лист {canvas.getPageNumber()}")
        if stamp:
            w, h = 150 * mm, 32 * mm
            x, y = doc.pagesize[0] - 15 * mm - w, 12 * mm
            canvas.setStrokeColor(colors.black)
            canvas.setLineWidth(0.8)
            canvas.rect(x, y, w, h)
            canvas.line(x, y + h - 8 * mm, x + w, y + h - 8 * mm)
            canvas.line(x + 100 * mm, y, x + 100 * mm, y + h - 8 * mm)
            canvas.setFont(FONT_BOLD, 7)
            canvas.drawString(x + 2 * mm, y + h - 5.5 * mm, stamp.get("code", ""))
            canvas.setFont(FONT, 6.5)
            canvas.drawRightString(x + w - 2 * mm, y + h - 5.5 * mm,
                                   f"Изм. {stamp.get('revision', '')}   Лист {stamp.get('sheet', '')}")
            lines = [
                stamp.get("object", ""),
                stamp.get("section", ""),
                f"Стадия: {stamp.get('stage', '')}      Дата: {stamp.get('date', '')}",
            ]
            for i, line in enumerate(lines):
                canvas.drawString(x + 2 * mm, y + h - 13 * mm - i * 5 * mm, line[:95])
            org = [stamp.get("organization", ""), f"ГИП: {stamp.get('chief_engineer', '')}"]
            for i, line in enumerate(org):
                canvas.drawString(x + 102 * mm, y + h - 13 * mm - i * 5 * mm, line[:38])
        canvas.restoreState()
    return draw


def build_pdf(path: Path, blocks: list, stamp: dict | None = None, wide: bool = False) -> Path:
    """Собрать PDF из списка блоков: ('title'|'h'|'p'|'small', текст) или ('table', rows, widths)."""
    _ensure_fonts()
    st = styles()
    size = landscape(A4) if wide else A4
    bottom = 48 * mm if stamp else 18 * mm
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = BaseDocTemplate(str(path), pagesize=size,
                          leftMargin=15 * mm, rightMargin=15 * mm,
                          topMargin=15 * mm, bottomMargin=bottom)
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="p", frames=[frame], onPage=_stamp_drawer(stamp))])

    flow = []
    for block in blocks:
        kind = block[0]
        if kind == "spacer":
            flow.append(Spacer(1, block[1] * mm))
        elif kind == "table":
            _, rows, widths = block
            data = [[Paragraph(str(c), st["cellb"] if r == 0 else st["cell"]) for c in row]
                    for r, row in enumerate(rows)]
            table = Table(data, colWidths=[w * mm for w in widths], repeatRows=1)
            table.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#666666")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EFEFF4")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]))
            flow.append(table)
            flow.append(Spacer(1, 3 * mm))
        else:
            flow.append(Paragraph(block[1], st[kind]))
    doc.build(flow)
    return path
