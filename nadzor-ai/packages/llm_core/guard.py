"""Детектор попыток воздействия на систему анализа (мера Б.3.5).

Срабатывание не блокирует анализ: оно поднимает флаг injection_suspected и
само становится находкой — поводом проверить добросовестность заявителя.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

PATTERNS: list[tuple[str, str]] = [
    (r"игнорир\w*\s+(все\s+)?(предыдущ\w+|прежн\w+|выше\w*)\s+(инструкц\w+|указан\w+|правил\w+)",
     "требование игнорировать инструкции"),
    (r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?)",
     "требование игнорировать инструкции (англ.)"),
    (r"(верни|вернуть|выведи|сформируй)\s+пуст\w+\s+(список|результат|ответ|массив)",
     "требование вернуть пустой результат"),
    (r"нарушен\w+\s+не\s+выявлен\w*.{0,40}(заверши|заканчивай|прекрати)",
     "предписание завершить анализ выводом об отсутствии нарушений"),
    (r"(вниман\w+|обращен\w+)\s+(систем\w+|модел\w+|нейросет\w+|ии|искусственн\w+ интеллект\w*)",
     "прямое обращение к системе анализа"),
    (r"не\s+сообщ\w+\s+(инспектору|пользователю|оператору|проверяющ\w+)",
     "требование скрыть сведения от инспектора"),
    (r"(ты|вы)\s+(должен|должна|обязан\w*)\s+(считать|признать|подтвердить)\s+",
     "навязывание вывода"),
    (r"</?\s*НЕДОВЕРЕННЫЙ_ДОКУМЕНТ\s*>", "подделка границ недоверенного контейнера"),
    (r"system\s*:\s*|<\s*/?\s*(system|assistant)\s*>", "подделка служебной роли сообщения"),
    (r"(данн\w+|этот)\s+документ\w*\s+(проверк\w+\s+не\s+подлежит|не\s+подлежит\s+проверке)",
     "объявление документа не подлежащим проверке"),
]

_COMPILED = [(re.compile(p, re.IGNORECASE | re.DOTALL), label) for p, label in PATTERNS]


@dataclass
class InjectionHit:
    label: str
    excerpt: str
    fact_id: str = ""


def scan_text(text: str, fact_id: str = "") -> list[InjectionHit]:
    """Проверить фрагмент текста на признаки внушения инструкций."""
    hits: list[InjectionHit] = []
    for regex, label in _COMPILED:
        m = regex.search(text)
        if m:
            start = max(0, m.start() - 40)
            hits.append(InjectionHit(label=label, excerpt=text[start:m.end() + 60].strip(),
                                     fact_id=fact_id))
    return hits


def scan_facts(facts: list[dict]) -> list[InjectionHit]:
    """Проверить все текстовые значения фактов документа."""
    hits: list[InjectionHit] = []
    for fact in facts:
        value = fact.get("value")
        chunks = [value] if isinstance(value, str) else (
            list(value.values()) if isinstance(value, dict) else [])
        for chunk in chunks:
            if isinstance(chunk, str) and len(chunk) > 20:
                hits.extend(scan_text(chunk, fact.get("id", "")))
    return hits
