"""FastAPI-приложение: загрузка документов, запуск анализа, находки, настройки.

Однопользовательский локальный инструмент — без RBAC/аудит-цепочки (это
намеренное упрощение по сравнению с packages/api в этом же репозитории; см.
обсуждение архитектуры в сессии — этот бэкенд не заменяет packages/api, а
существует отдельно как более лёгкий вариант под конкретную механику
сравнения документов).
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from . import models, schemas
from .classification import classify_document
from .db import get_session, init_db
from .documents import extract_document_facts
from .llm import LlmConfig
from .matching import DocumentInput, match_page_pairs
from .vision import compare_page_pair, compare_text_pair, make_llm_stamp_classifier, render_page_to_png_bytes

UPLOAD_DIR = Path(__file__).resolve().parents[1] / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

app = FastAPI(title="НАДЗОР.ИИ — backend")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


def _ensure_schema_and_defaults() -> None:
    # На module-level, а не только в @app.on_event("startup") — тестовые
    # клиенты (и не только) не всегда гарантированно проигрывают lifespan-
    # события перед первым запросом, а без таблиц первый же INSERT падает.
    init_db()
    db = next(get_session())
    try:
        if db.query(models.Settings).count() == 0:
            db.add(models.Settings(id=1, provider="local", base_url="", model="", api_key=""))
            db.commit()
    finally:
        db.close()


_ensure_schema_and_defaults()


# Ключи те же, что в контракте находки (CLAUDE.md, раздел 5.2) и в
# theme.severity фронтенда: русские подписи живут в интерфейсе, данные —
# на латинице, иначе получилось бы два несогласованных словаря степеней.
SEVERITY_CRITICAL = "critical"
SEVERITY_MAJOR = "major"
SEVERITY_MINOR = "minor"

# Порядок обхода объекта: критичное первым. Неразобранная величина уходит в
# конец, но находку не теряет — пропустить возможное нарушение хуже, чем
# показать его без степени.
SEVERITY_ORDER = {SEVERITY_CRITICAL: 0, SEVERITY_MAJOR: 1, SEVERITY_MINOR: 2, "": 3}


def _normalize_severity(value: object) -> str:
    """Привести ответ модели к одной из трёх степеней.

    Модель просят вернуть одно из трёх слов, но 7B-модель регулярно отвечает
    синонимом или английским термином. Сопоставляем по корню, а незнакомое
    значение отбрасываем в пустую строку, а не выдаём за настоящую оценку.
    """
    text = str(value or "").strip().lower()
    if not text:
        return ""

    # Отрицание разбирается первым и по строке без пробелов и дефисов: модель
    # пишет и «незначительно», и «не значимо», и «не критично» — во всех
    # случаях это низшая степень. Без этого «не значимо» цеплялось за корень
    # «значим» и самая безобидная находка вставала в начало списка обхода.
    compact = text.replace(" ", "").replace("-", "")
    if compact.startswith("не") and any(
        root in compact for root in ("знач", "крит", "сущест", "важн")
    ):
        return SEVERITY_MINOR

    for word, severity in (
        ("крит", SEVERITY_CRITICAL), ("critical", SEVERITY_CRITICAL), ("high", SEVERITY_CRITICAL),
        ("сущест", SEVERITY_MAJOR), ("major", SEVERITY_MAJOR), ("medium", SEVERITY_MAJOR),
        ("значим", SEVERITY_MAJOR), ("средн", SEVERITY_MAJOR),
        ("minor", SEVERITY_MINOR), ("low", SEVERITY_MINOR),
    ):
        if word in text:
            return severity
    return ""


def _llm_config(db: Session) -> LlmConfig:
    s = db.query(models.Settings).first()
    if s is None:
        return LlmConfig(provider="local")
    return LlmConfig(provider=s.provider, api_key=s.api_key, base_url=s.base_url, model=s.model)


# ---------- Документы ----------


@app.post("/documents", response_model=schemas.DocumentOut)
def upload_document(side: str, file: UploadFile, db: Session = Depends(get_session)):
    if side not in ("before", "after"):
        raise HTTPException(400, "side must be 'before' or 'after'")

    # Оригинальное имя файла — только отображаемые метаданные (используется в
    # классификации по имени и в подписях находок), на диск не идёт вообще:
    # приходит от клиента и не должно участвовать в построении пути.
    original_name = Path(file.filename or "document.pdf").name
    dest = UPLOAD_DIR / f"{uuid.uuid4().hex}.pdf"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    doc = models.Document(name=original_name, side=side, file_path=str(dest), status="parsing")
    db.add(doc)
    db.commit()
    db.refresh(doc)

    try:
        facts = extract_document_facts(str(dest), original_name)
        config = _llm_config(db)
        vision_fn = make_llm_stamp_classifier(config) if config.provider else None
        classification = classify_document(str(dest), original_name, vision_stamp_fn=vision_fn)
        doc.pages = facts.pages
        doc.discipline_code = classification.discipline_code
        doc.classification_source = classification.source
        doc.status = "ok"
    except Exception as exc:  # noqa: BLE001 — на распознавании не валим загрузку
        doc.status = "error"
        doc.classification_source = str(exc)
    db.commit()
    db.refresh(doc)
    return doc


@app.get("/documents", response_model=list[schemas.DocumentOut])
def list_documents(db: Session = Depends(get_session)):
    return db.query(models.Document).order_by(models.Document.uploaded_at.desc()).all()


@app.delete("/documents/{document_id}")
def delete_document(document_id: int, db: Session = Depends(get_session)):
    doc = db.get(models.Document, document_id)
    if doc is None:
        raise HTTPException(404, "not found")
    Path(doc.file_path).unlink(missing_ok=True)
    db.delete(doc)
    db.commit()
    return {"ok": True}


@app.get("/page-image/{document_id}/{page}")
def get_page_image(document_id: int, page: int, db: Session = Depends(get_session)):
    doc = db.get(models.Document, document_id)
    if doc is None:
        raise HTTPException(404, "not found")
    if page < 1 or page > doc.pages:
        raise HTTPException(404, "page out of range")
    png_bytes = render_page_to_png_bytes(doc.file_path, page)
    return Response(content=png_bytes, media_type="image/png")


# ---------- Анализ ----------


def _run_analysis(run_id: int) -> None:
    db = next(get_session())
    try:
        run = db.get(models.AnalysisRun, run_id)
        if run is None:
            return
        before_docs = [db.get(models.Document, i) for i in run.before_document_ids]
        after_docs = [db.get(models.Document, i) for i in run.after_document_ids]

        before_facts = [extract_document_facts(d.file_path, d.name) for d in before_docs]
        after_facts = [extract_document_facts(d.file_path, d.name) for d in after_docs]

        before_inputs = [
            DocumentInput(d.name, f.pages, f.text_facts, f.room_facts, d.discipline_code, f.page_kinds)
            for d, f in zip(before_docs, before_facts)
        ]
        after_inputs = [
            DocumentInput(d.name, f.pages, f.text_facts, f.room_facts, d.discipline_code, f.page_kinds)
            for d, f in zip(after_docs, after_facts)
        ]
        pairs = match_page_pairs(before_inputs, after_inputs)
        run.pairs_total = len(pairs)
        db.commit()

        pair_rows = []
        for p in pairs:
            row = models.PagePair(
                run_id=run.id,
                before_document_id=before_docs[p.before_file_idx].id,
                before_page=p.before_page,
                after_document_id=after_docs[p.after_file_idx].id,
                after_page=p.after_page,
                matched_by=p.matched_by,
                page_kind=p.page_kind,
                score=p.score,
                discipline_mismatch=p.discipline_mismatch,
            )
            db.add(row)
            pair_rows.append(row)
        db.commit()

        # Текст листа по номеру страницы — нужен для текстового сравнения
        # (не всего документа, только конкретной сопоставленной пары листов).
        def _page_text(facts_list, docs, document_id: int, page: int) -> str:
            idx = next(i for i, d in enumerate(docs) if d.id == document_id)
            return "\n".join(f["text"] for f in facts_list[idx].text_facts if f["page"] == page)

        config = _llm_config(db)
        for i, row in enumerate(pair_rows):
            before_doc = next(d for d in before_docs if d.id == row.before_document_id)
            after_doc = next(d for d in after_docs if d.id == row.after_document_id)
            context = f"раздел {before_doc.discipline_code or '?'}"
            if row.matched_by == "position" and row.discipline_mismatch:
                context += " (сопоставлено по позиции, разделы штампа не совпадают — проверьте применимость)"
            try:
                if row.page_kind == "text":
                    result = compare_text_pair(
                        _page_text(before_facts, before_docs, row.before_document_id, row.before_page),
                        _page_text(after_facts, after_docs, row.after_document_id, row.after_page),
                        config, context=context,
                    )
                else:
                    result = compare_page_pair(
                        before_doc.file_path, row.before_page,
                        after_doc.file_path, row.after_page,
                        config, context=context,
                    )
            except Exception as exc:  # noqa: BLE001 — одна упавшая пара не должна ронять весь прогон
                result = None
            kind = "vision" if row.page_kind == "drawing" else "text"
            if result and isinstance(result.get("significant"), list):
                for item in result["significant"]:
                    if not item.get("change"):
                        continue
                    db.add(models.Finding(
                        run_id=run.id, pair_id=row.id, kind=kind,
                        label=item.get("label", ""), change_text=item["change"],
                        severity=_normalize_severity(item.get("severity")),
                        field_check=str(item.get("field_check") or "").strip(),
                        raw_llm_response=result,
                    ))
            # Попытка внушить модели что-либо через содержимое документа —
            # сама по себе находка и повод проверить добросовестность
            # заявителя, а не техническая ошибка разбора (модель угроз, Б.3.5).
            if result and result.get("injection_suspected") is True:
                db.add(models.Finding(
                    run_id=run.id, pair_id=row.id, kind=kind,
                    label="Подозрение на инъекцию инструкций",
                    change_text="В содержимом листа обнаружена попытка повлиять на "
                                "работу анализатора. Указания из документа не выполнялись.",
                    severity=SEVERITY_CRITICAL,
                    field_check="Проверить добросовестность заявителя, сверить лист вручную",
                    raw_llm_response=result,
                ))
            run.pairs_done = i + 1
            db.commit()

        run.status = "done"
        db.commit()
    except Exception as exc:  # noqa: BLE001
        run = db.get(models.AnalysisRun, run_id)
        if run is not None:
            run.status = "error"
            run.error = str(exc)
            db.commit()
    finally:
        db.close()


@app.post("/analysis-runs", response_model=schemas.AnalysisRunOut)
def create_analysis_run(
    body: schemas.AnalysisRunCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_session)
):
    run = models.AnalysisRun(
        before_document_ids=body.before_document_ids,
        after_document_ids=body.after_document_ids,
        status="running",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    background_tasks.add_task(_run_analysis, run.id)
    return run


@app.get("/analysis-runs/{run_id}", response_model=schemas.AnalysisRunOut)
def get_analysis_run(run_id: int, db: Session = Depends(get_session)):
    run = db.get(models.AnalysisRun, run_id)
    if run is None:
        raise HTTPException(404, "not found")
    return run


# ---------- Находки ----------


@app.get("/findings", response_model=list[schemas.FindingOut])
def list_findings(run_id: int, status: str | None = None, db: Session = Depends(get_session)):
    q = db.query(models.Finding).filter(models.Finding.run_id == run_id)
    if status:
        q = q.filter(models.Finding.reviewed_status == status)
    # Инспектору нужен порядок обхода, а не хронология разбора: критичное
    # первым, внутри одной степени — как нашли. Сортируем в Python, потому что
    # порядок задан словарём, а не алфавитом колонки.
    findings = q.order_by(models.Finding.created_at).all()
    return sorted(findings, key=lambda f: SEVERITY_ORDER.get(f.severity, 3))


@app.patch("/findings/{finding_id}", response_model=schemas.FindingOut)
def update_finding(finding_id: int, body: schemas.FindingUpdate, db: Session = Depends(get_session)):
    finding = db.get(models.Finding, finding_id)
    if finding is None:
        raise HTTPException(404, "not found")
    finding.reviewed_status = body.reviewed_status
    db.commit()
    db.refresh(finding)
    return finding


# ---------- Настройки ----------


@app.get("/settings", response_model=schemas.SettingsOut)
def get_settings(db: Session = Depends(get_session)):
    return db.query(models.Settings).first()


@app.put("/settings", response_model=schemas.SettingsOut)
def update_settings(body: schemas.SettingsUpdate, db: Session = Depends(get_session)):
    s = db.query(models.Settings).first()
    s.provider = body.provider
    s.base_url = body.base_url
    s.model = body.model
    s.api_key = body.api_key
    db.commit()
    db.refresh(s)
    return s
