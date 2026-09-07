"""Хранилище результатов разбора ПД (Г.87).

Решает ДВЕ задачи одним местом — так вышло не из экономии, а потому что
это буквально одни и те же данные:

1. **Передача между стадиями.** Стадия 2 (сверка с РД) читает готовый
   результат стадии 1, а не переизвлекает ПД заново. Прямое решение
   пользователя: сверка запускается отдельно, по сохранённому ПД.
2. **Датасет на будущее.** Каждое извлечённое требование копится со всем
   контекстом (файл, раздел, страница, версия промпта, провайдер, модель)
   — прямое решение пользователя на вопрос, что копить: «всё подряд,
   датасет на будущее».

Честная граница, которую важно не забыть (Г.75): дообучение весов GigaChat
этому проекту недоступно — нет ни договора, ни API дообучения. Датасет
копится на случай, когда оно появится, и как материал для few-shot
примеров. Сам по себе он модель не улучшает.

**Отдельный файл БД, не общий с `db.py`.** Причина конкретная: `make clean`
удаляет `data/nadzor.db`, и накопленный за месяцы датасет исчез бы вместе с
рабочей базой от одной команды очистки. Здесь же — `data/pd_store.db`,
который чистка не трогает. Оба файла под `*.db` в `.gitignore`: реальные
тексты требований объекта в git не попадают (Г.12).
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, create_engine, desc
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .requirement_registry import Requirement

# Версия промпта извлечения. Меняется руками при правке
# `_REQUIREMENT_EXTRACTION_TEMPLATE` — по ней в датасете можно отделить
# требования, извлечённые старым промптом, от новых. Без этого датасет
# смешивает результаты разных версий и годится только целиком.
PROMPT_VERSION = "Г.86"

STORE_PATH = os.environ.get(
    "NADZOR_PD_STORE",
    str(Path(__file__).resolve().parents[3] / "data" / "pd_store.db"),
)


class StoreBase(DeclarativeBase):
    pass


class PdRun(StoreBase):
    """Один прогон стадии 1 по одному или нескольким файлам ПД."""

    __tablename__ = "pd_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    documents: Mapped[list] = mapped_column(JSON, default=list)   # имена файлов
    sections: Mapped[list] = mapped_column(JSON, default=list)    # коды разделов
    # 'llm' или 'regex' — какой путь реально отработал. Без этого поля
    # датасет смешивает результат модели с результатом регулярки, а это
    # данные разного качества (Г.10: не выдавать одно за другое).
    extractor: Mapped[str] = mapped_column(String, default="")
    provider: Mapped[str] = mapped_column(String, default="")
    model: Mapped[str] = mapped_column(String, default="")
    prompt_version: Mapped[str] = mapped_column(String, default="")
    requirements_total: Mapped[int] = mapped_column(Integer, default=0)
    # Число упавших пачек вызова ЛЛМ. Ненулевое значение означает, что
    # прогон НЕПОЛНЫЙ — для датасета это важнее, чем для отчёта: обучать
    # на огрызке, считая его полным разбором тома, нельзя.
    failed_chunks: Mapped[int] = mapped_column(Integer, default=0)


class PdRequirement(StoreBase):
    """Одно извлечённое требование. Append-only: строки не переписываются."""

    __tablename__ = "pd_requirements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("pd_runs.id"), index=True)
    document: Mapped[str] = mapped_column(String, default="", index=True)
    section: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    page: Mapped[int] = mapped_column(Integer, default=0)
    sentence: Mapped[str] = mapped_column(Text, default="")
    # Г.93 — короткая суть рядом с цитатой: сводке нужна первая,
    # датасету и пакету доказательств — вторая.
    summary: Mapped[str] = mapped_column(Text, default="")
    code: Mapped[str | None] = mapped_column(String, nullable=True)
    rooms: Mapped[list] = mapped_column(JSON, default=list)


_engine = None
_SessionLocal = None


def _session() -> Session:
    """Ленивая инициализация: файл базы создаётся при первом обращении, а
    не при импорте модуля — иначе любой импорт `pd_store` (в том числе из
    теста, который хранилищем не пользуется) плодил бы файлы на диске."""
    global _engine, _SessionLocal
    if _SessionLocal is None:
        Path(STORE_PATH).parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(f"sqlite:///{STORE_PATH}",
                                connect_args={"check_same_thread": False})
        StoreBase.metadata.create_all(bind=_engine)
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    return _SessionLocal()


def save_run(
    requirements: list[Requirement],
    documents: list[str],
    extractor: str,
    provider: str = "",
    model: str = "",
    failed_chunks: int = 0,
) -> int:
    """Сохранить результат стадии 1, вернуть id прогона."""
    sections = sorted({r.section for r in requirements if r.section})
    with _session() as session:
        run = PdRun(
            documents=list(documents),
            sections=sections,
            extractor=extractor,
            provider=provider,
            model=model,
            prompt_version=PROMPT_VERSION,
            requirements_total=len(requirements),
            failed_chunks=failed_chunks,
        )
        session.add(run)
        session.flush()  # нужен run.id до вставки требований
        for req in requirements:
            session.add(PdRequirement(
                run_id=run.id,
                document=req.document,
                section=req.section,
                page=req.page,
                sentence=req.sentence,
                summary=req.summary,
                code=req.code,
                rooms=list(req.rooms),
            ))
        session.commit()
        return run.id


def load_run(run_id: int) -> list[Requirement]:
    """Требования конкретного прогона — вход стадии 2."""
    with _session() as session:
        rows = session.query(PdRequirement).filter(PdRequirement.run_id == run_id).all()
        return [_to_requirement(row) for row in rows]


def latest_run_id(document: str | None = None) -> int | None:
    """id последнего прогона; с `document` — последнего, где этот файл был.

    Возвращает None, если подходящего прогона нет — вызывающий обязан
    сказать об этом явно, а не показать пустую сверку как успешную (Г.10).

    Поиск идёт по СОСТАВУ прогона (`PdRun.documents`), а не по строкам
    требований. Разница не косметическая: прогон, честно не нашедший в томе
    ни одного требования, строк не оставляет — искали бы по ним, и такой
    том выглядел бы как «никогда не разбирался». Это ровно та подмена
    «пусто» на «не делали», которую запрещает Г.10.
    """
    with _session() as session:
        runs = session.query(PdRun).order_by(desc(PdRun.id)).all()
        for run in runs:
            if document is None or document in (run.documents or []):
                return run.id
        return None


def list_runs(limit: int = 20) -> list[dict]:
    """Краткий список прогонов — чтобы человек выбрал нужный по номеру."""
    with _session() as session:
        runs = session.query(PdRun).order_by(desc(PdRun.id)).limit(limit).all()
        return [{
            "id": r.id,
            "created_at": r.created_at.isoformat(sep=" ", timespec="seconds"),
            "documents": r.documents,
            "sections": r.sections,
            "extractor": r.extractor,
            "requirements_total": r.requirements_total,
            "failed_chunks": r.failed_chunks,
        } for r in runs]


def export_dataset(path: str, limit: int | None = None) -> int:
    """Выгрузить накопленное в JSONL — формат, который принимают почти все
    инструменты дообучения. Возвращает число записей.

    Каждая строка несёт и текст, и контекст (файл, раздел, страница), и
    условия получения (какой извлекатель, провайдер, модель, версия
    промпта): без последнего датасет нельзя отфильтровать по качеству, а
    regex-строки и строки модели в нём заведомо разного достоинства.
    """
    with _session() as session:
        query = (
            session.query(PdRequirement, PdRun)
            .join(PdRun, PdRequirement.run_id == PdRun.id)
            .order_by(PdRequirement.id)
        )
        if limit:
            query = query.limit(limit)
        rows = query.all()

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for req, run in rows:
            f.write(json.dumps({
                "sentence": req.sentence,
                "summary": req.summary,
                "document": req.document,
                "section": req.section,
                "page": req.page,
                "code": req.code,
                "rooms": req.rooms,
                "extractor": run.extractor,
                "provider": run.provider,
                "model": run.model,
                "prompt_version": run.prompt_version,
                "run_id": run.id,
                "run_created_at": run.created_at.isoformat(),
            }, ensure_ascii=False) + "\n")
    return len(rows)


def _to_requirement(row: PdRequirement) -> Requirement:
    return Requirement(
        rooms=list(row.rooms or []),
        page=row.page,
        sentence=row.sentence,
        summary=row.summary or "",
        code=row.code,
        document=row.document,
        section=row.section,
    )
