"""Нормативная база с чанкингом по пунктам.

Единица хранения — пункт нормативного документа, а не фрагмент фиксированной
длины: ссылка в находке должна указывать на конкретный пункт. Поиск гибридный:
совпадение по ключевым словам и по словам запроса. В продуктиве векторная часть
переносится в pgvector, контракт функции search при этом не меняется.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

_WORD_RE = re.compile(r"[а-яёa-z0-9\.\-]{3,}", re.IGNORECASE)


def _tokens(text: str) -> set[str]:
    return {w.lower() for w in _WORD_RE.findall(text)}


@dataclass
class Clause:
    """Пункт нормативного документа."""
    doc_code: str
    title_doc: str
    edition: str
    clause: str
    title: str
    text: str
    is_actual: bool = True
    is_paraphrase: bool = False
    superseded_by: str = ""
    keywords: list[str] = field(default_factory=list)

    @property
    def ref(self) -> str:
        return f"{self.doc_code}:{self.clause}"

    def to_norm_ref(self) -> dict:
        """Ссылка в формате контракта находки."""
        return {"doc": self.doc_code, "clause": self.clause, "edition": self.edition,
                "actual": self.is_actual, "title": self.title}

    def score(self, query: str) -> float:
        """Оценка релевантности: ключевые слова весомее совпадений по тексту."""
        q = _tokens(query)
        if not q:
            return 0.0
        kw = _tokens(" ".join(self.keywords))
        body = _tokens(f"{self.title} {self.text}")
        kw_hit = len(q & kw) / max(len(q), 1)
        body_hit = len(q & body) / max(len(q), 1)
        return round(kw_hit * 0.7 + body_hit * 0.3, 4)


class NormsBase:
    """Нормативная база региона."""

    def __init__(self, clauses: list[Clause], legal_acts: list[dict], classes: list[dict]) -> None:
        self.clauses = clauses
        self.legal_acts = legal_acts
        self.classes = classes
        self._by_ref = {c.ref: c for c in clauses}
        self._by_code = {c["code"]: c for c in classes}

    def get(self, ref: str) -> Clause | None:
        """Пункт по ссылке вида «СП 63.13330.2018:6.1.6»."""
        return self._by_ref.get(ref)

    def search(self, query: str, top_k: int = 5, actual_only: bool = True) -> list[Clause]:
        scored = [(c.score(query), c) for c in self.clauses
                  if c.is_actual or not actual_only]
        scored = [(s, c) for s, c in scored if s > 0]
        scored.sort(key=lambda p: (-p[0], p[1].ref))
        return [c for _, c in scored[:top_k]]

    def check_edition(self, ref: str) -> dict:
        """Контроль актуальности редакции пункта."""
        clause = self.get(ref)
        if clause is None:
            return {"found": False, "actual": False,
                    "message": "Пункт отсутствует в нормативной базе региона."}
        if not clause.is_actual:
            replacement = self.get(clause.superseded_by) if clause.superseded_by else None
            return {"found": True, "actual": False,
                    "superseded_by": clause.superseded_by,
                    "message": f"Редакция {clause.edition} не действует."
                               + (f" Действующая редакция: {replacement.edition}."
                                  if replacement else "")}
        return {"found": True, "actual": True, "message": "Редакция действующая."}

    def norm_refs(self, refs: list[str]) -> list[dict]:
        """Развернуть ссылки в блок norm_refs контракта находки."""
        out = []
        for ref in refs:
            clause = self.get(ref)
            if clause:
                out.append(clause.to_norm_ref())
            else:
                doc, _, num = ref.partition(":")
                out.append({"doc": doc, "clause": num, "edition": "не установлена",
                            "actual": False, "title": ""})
        return out

    def legal_refs(self, keys: list[str]) -> list[dict]:
        """Развернуть ссылки на статьи КоАП вида «9.4/2»."""
        out = []
        for key in keys:
            article, _, part = key.partition("/")
            for act in self.legal_acts:
                if act["article"] == article and (not part or act["part"] == part):
                    out.append({"act": act["act"], "article": act["article"],
                                "part": act["part"], "title": act["title"]})
                    break
        return out

    def klass(self, code: str) -> dict:
        """Класс расхождения из классификатора."""
        return self._by_code.get(code, {"code": code, "title": "Неклассифицированное расхождение",
                                        "default_severity": "minor", "norm_refs": [],
                                        "legal_refs": [], "group": "Прочее"})

    def classifier(self) -> list[dict]:
        return self.classes


def load_norms(norms_path: str | Path = "data/norms/norms.json",
               classifier_path: str | Path = "data/norms/classifier.json") -> NormsBase:
    """Загрузить нормативный пакет региона."""
    data = json.loads(Path(norms_path).read_text(encoding="utf-8"))
    classes = json.loads(Path(classifier_path).read_text(encoding="utf-8"))["classes"]
    clauses = [Clause(**{k: v for k, v in c.items() if k in Clause.__annotations__})
               for c in data["clauses"]]
    return NormsBase(clauses, data.get("legal_acts", []), classes)


@lru_cache(maxsize=4)
def cached_norms(norms_path: str, classifier_path: str) -> NormsBase:
    return load_norms(norms_path, classifier_path)
