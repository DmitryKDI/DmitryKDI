"""Офлайн-провайдер для тестов и демонстрации без внешней сети.

Честное предупреждение: это детерминированная заглушка, а не языковая модель.
Она реализует тот же контракт, что и продуктивные адаптеры, и выполняет
сопоставление текстовых указаний по фиксированным шаблонам. Нужна для
воспроизводимых тестов и для демонстрации в изолированном контуре.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

from llm_core.ports import CompletionRequest, CompletionResponse, ProviderHealth, Vector

RE_REBAR_STEP = re.compile(r"шаг\s+поперечной\s+арматуры.{0,160}?(\d{2,4})\s*мм", re.IGNORECASE)
RE_PROTECT_LAYER = re.compile(r"защитный\s+слой.{0,160}?(\d{2,3})\s*мм", re.IGNORECASE)

SEMANTIC_RULES = [
    ("шаг поперечной арматуры", RE_REBAR_STEP, "D-12", "СП 63.13330.2018:10.3.9",
     "Шаг поперечной арматуры в рабочей документации ({after} мм) превышает "
     "принятый в проектной документации ({before} мм)"),
    ("защитный слой бетона", RE_PROTECT_LAYER, "D-01", "СП 63.13330.2018:6.1.6",
     "Толщина защитного слоя бетона в рабочей документации ({after} мм) отличается "
     "от проектной ({before} мм)"),
]


class MockProvider:
    """Заглушка провайдера. Никуда не ходит, работает офлайн."""

    name = "mock"
    model = "mock-detector-1"
    supports_function_calling = False
    max_context_tokens = 32000
    is_sovereign = True

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        started = time.monotonic()
        payload = self._verify(req) if req.task == "verify" else self._semantic_diff(req)
        return CompletionResponse(
            raw_text=json.dumps(payload, ensure_ascii=False),
            provider=self.name, model=self.model, prompt_version=req.prompt_version,
            tokens_in=len(json.dumps(req.facts, ensure_ascii=False)) // 4,
            tokens_out=len(json.dumps(payload, ensure_ascii=False)) // 4,
            latency_ms=int((time.monotonic() - started) * 1000))

    def _semantic_diff(self, req: CompletionRequest) -> dict:
        """Сопоставление текстовых указаний двух состояний.

        Разбираются блоки недоверенного контейнера вида
        «[факт D1:12 | сторона: after | документ: …]» — так же, как их увидит
        продуктивная модель.
        """
        before, after = {}, {}
        for block in req.untrusted_blocks:
            fact_id, side, text = self._split_block(block)
            if not fact_id:
                continue
            (before if side == "before" else after)[fact_id] = text
        findings = []
        for label, regex, code, clause, template in SEMANTIC_RULES:
            b_val, b_fact = self._first_match(before, regex)
            a_val, a_fact = self._first_match(after, regex)
            if b_val and a_val and b_val != a_val:
                findings.append({
                    "code": code,
                    "title": template.format(before=b_val, after=a_val),
                    "severity": "critical" if code == "D-12" else "major",
                    "confidence": 0.88,
                    "element": req.context.get("element") or {
                        "name": "Плита перекрытия", "mark": "ПП-8", "level": "+33.600",
                        "axes": "А–Г / 1–5", "section": "секция 2"},
                    "fact_ids": [b_fact, a_fact],
                    "norm_clause": clause,
                    "rationale": (f"Параметр «{label}» в состоянии «после» равен {a_val} мм, "
                                  f"в состоянии «до» — {b_val} мм. Отступление рабочей "
                                  "документации от проектных решений."),
                })
        return {"findings": findings}

    @staticmethod
    def _split_block(block: str) -> tuple[str, str, str]:
        """Разобрать заголовок недоверенного блока: идентификатор факта и сторона."""
        m = re.match(r"\[факт\s+([^\s|\]]+)\s*\|\s*сторона:\s*(\w+)[^\]]*\]\s*(.*)",
                     block, re.DOTALL)
        return (m.group(1), m.group(2), m.group(3)) if m else ("", "", block)

    @staticmethod
    def _first_match(blocks: dict, regex: re.Pattern) -> tuple[str, str]:
        for fact_id, text in blocks.items():
            if m := regex.search(text):
                return m.group(1), fact_id
        return "", ""

    def _verify(self, req: CompletionRequest) -> dict:
        """Перепроверка: вывод без фактов и без нормы согласия не получает."""
        finding = req.context.get("finding", {})
        ok = bool(finding.get("fact_ids")) and bool(finding.get("norm_clause"))
        return {"agreement": ok, "confidence": 0.86 if ok else 0.2,
                "comment": "Доказательная база и нормативное основание присутствуют." if ok
                           else "Отсутствует ссылка на факт или пункт норматива."}

    async def embed(self, texts: list[str]) -> list[Vector]:
        """Детерминированные векторы: одинаковый текст — одинаковый вектор."""
        out: list[Vector] = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            out.append([(b - 128) / 128 for b in digest[:32]])
        return out

    async def health(self) -> ProviderHealth:
        return ProviderHealth(available=True, latency_ms=0, detail="офлайн-заглушка")
