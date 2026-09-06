"""Эскалация в зрение для помещений со слабой текстовой доказательной базой.

Часть расхождений видна только на самом чертеже (конфигурация оборудования,
трассировка сетей), а не в извлечённом тексте: номер помещения рядом стоит,
а описание изменения — нет, оно выражено самой схемой. Эскалация не заменяет
текстовый слой, а достраивает его для кандидатов, где текста почти нет —
и работает только с провайдером, заявившим поддержку зрения (Б.3: недоверенные
данные документа, не инструкция).
"""
from __future__ import annotations

import base64
from pathlib import Path

from documents.pdf import render_png
from documents.schemas import Fact
from llm_core.ports import ImageBlock

from analysis.models import ParsedDoc

SidePair = tuple[Fact, ParsedDoc]


def needs_escalation(before_facts: list[SidePair], after_facts: list[SidePair],
                     min_text_facts: int) -> bool:
    """Мало текстовых фактов у помещения с обеих сторон — сравнение по тексту ненадёжно."""
    text_count = sum(1 for fact, _ in (*before_facts, *after_facts)
                     if fact.fact_type == "text")
    return text_count < min_text_facts


def render_room_images(room: str, side: str, side_facts: list[SidePair],
                       dpi: int, max_images: int) -> list[ImageBlock]:
    """Отрисовать листы, где встречается номер помещения, для одной стороны сравнения.

    Один лист на документ: разные факты одного помещения часто лежат на одной
    странице (план целиком), повторный рендер того же листа не нужен.
    """
    seen: set[tuple[str, int]] = set()
    images: list[ImageBlock] = []
    for fact, doc in side_facts:
        if len(images) >= max_images:
            break
        key = (doc.id, fact.source.page)
        if key in seen:
            continue
        seen.add(key)
        path = Path(doc.path)
        if not path.exists():
            continue
        png = render_png(path.read_bytes(), fact.source.page, dpi=dpi)
        images.append(ImageBlock(
            media_type="image/png", data_b64=base64.b64encode(png).decode("ascii"),
            label=f"помещение {room}, сторона «{side}», документ «{doc.title}», "
                 f"лист {fact.source.page}"))
    return images
