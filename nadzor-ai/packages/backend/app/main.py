"""FastAPI-приложение: загрузка документов, запуск анализа, находки, настройки.

Однопользовательский локальный инструмент — без RBAC/аудит-цепочки (это
намеренно простой слой поверх движка; см.
обсуждение архитектуры в сессии — этот бэкенд не заменяет packages/api, а
существует отдельно как более лёгкий вариант под конкретную механику
сравнения документов).
"""
from __future__ import annotations

import os
import shutil
import sys
import uuid
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from . import models, schemas
from .classification import DISCIPLINE_CODES, classify_document
from .compliance import check_compliance, render_compliance_report
from .db import get_session, init_db
from .document_composition import (describe_volume, name_unread_sheets,
                                   render_composition)
from .documents import extract_document_facts
from .level_pages import augment_room_index_with_level_fallback
from .llm import LlmConfig, check_llm_reachable
from .matching import DocumentInput, match_page_pairs
from .pd_stage import load_text_facts, render_summary
from .pd_store import load_run, save_run
from .requirement_llm_extract import extract_requirements_llm
from .requirement_text_verify import verify_general_requirements_llm
from .stamp_vision import read_stamp_ocr
from .triangulated_pipeline import run_triangulated_analysis
from .vision import (
    compare_page_pair,
    compare_text_pair,
    make_llm_stamp_classifier,
    render_page_to_png_bytes,
)
from .vision_page_compare import check_requirement_on_page

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
            # Г.94 — по умолчанию GigaChat: инструмент делается под него, и
            # инспектор ничего не выбирает. Ключ подхватывается из
            # GIGACHAT_CREDENTIALS, если не задан в настройках (см. _llm_config).
            db.add(models.Settings(id=1, provider="gigachat", base_url="", model="", api_key=""))
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


_PROVIDER_ENV_KEY = {"gigachat": "GIGACHAT_CREDENTIALS", "anthropic": "ANTHROPIC_API_KEY"}


# Г.99 — сколько вызовов модели разрешено потратить на чтение наименований
# листов по изображению штампа ЗА ОДИН ПРОГОН (не на каждый том: инспектор
# нажимает кнопку на комплект, и потолок должен относиться к тому, что он
# нажал). Ноль (умолчание) — шаг не выполняется вовсе.
# Задаётся администратором при развёртывании, как и ключ (Г.94): у рабочей
# документации это порядка сотни вызовов на том, и такое решение принимает не
# инспектор нажатием кнопки, а тот, кто отвечает за бюджет обращений.
SHEET_NAME_VISION_BUDGET_ENV = "SHEET_NAME_VISION_BUDGET"


def _sheet_vision_budget() -> int:
    try:
        return max(0, int(os.environ.get(SHEET_NAME_VISION_BUDGET_ENV, "0")))
    except ValueError:
        # Опечатка в переменной окружения не должна ронять прогон и не должна
        # молча включать платный шаг: считаем, что он выключен.
        return 0


def _llm_config(db: Session) -> LlmConfig:
    """Конфигурация провайдера для прогона, запущенного из интерфейса.

    Г.94 — ключ берётся из настроек, а если там пусто, из переменной
    окружения по провайдеру. Так администратор может задать ключ один раз
    при развёртывании, и инспектор не вводит вообще ничего: загрузил
    документы и нажал кнопку. Тот же приём, что уже сделан в CLI (Г.82),
    где отсутствие env-фолбэка приводило к молчаливому прогону без ключа.
    """
    s = db.query(models.Settings).first()
    provider = s.provider if s is not None else "gigachat"
    api_key = (s.api_key if s is not None else "") or os.environ.get(
        _PROVIDER_ENV_KEY.get(provider, ""), "")
    return LlmConfig(
        provider=provider, api_key=api_key,
        base_url=s.base_url if s is not None else "",
        model=s.model if s is not None else "",
    )


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


@app.patch("/documents/{document_id}", response_model=schemas.DocumentOut)
def update_document(document_id: int, body: schemas.DocumentUpdate, db: Session = Depends(get_session)):
    """Ручной выбор или исправление раздела/тома (Г.97).

    Автоматическое определение (имя файла, титульный лист, штамп, номер
    раздела по ПП №87 — Г.13/79/80) остаётся и работает как раньше. Но у
    инспектора должно быть последнее слово: не опознанный файл иначе уходит
    в разбор с «раздел не определён», и требования остаются без привязки к
    разделу — а привязка это прямое требование пользователя (Г.86).

    `discipline_code=null` снимает ручную правку и возвращает то, что
    определила программа: ошибочный ввод не должен быть необратимым.
    """
    doc = db.get(models.Document, document_id)
    if doc is None:
        raise HTTPException(404, "not found")

    if body.discipline_code is None:
        # Возврат к автоматике: пересчитываем, а не оставляем прежнее
        # значение — иначе «отменил правку» тихо сохранило бы её результат.
        try:
            result = classify_document(doc.file_path, doc.name)
            doc.discipline_code = result.discipline_code
            doc.classification_source = result.source
        except Exception as exc:  # noqa: BLE001 — не определили: честное «нет», а не падение
            doc.discipline_code = None
            doc.classification_source = f"не определён: {exc}"
    else:
        code = body.discipline_code.strip().upper()
        if code not in DISCIPLINE_CODES:
            raise HTTPException(400, "неизвестный код раздела; допустимые: "
                                     + ", ".join(DISCIPLINE_CODES))
        doc.discipline_code = code
        # Источник виден в списке файлов: инспектор должен различать, что
        # определила программа, а что он поправил сам (Г.10).
        doc.classification_source = "manual"
    db.commit()
    db.refresh(doc)
    return doc


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
            DocumentInput(d.name, f.pages, f.text_facts, f.room_facts, d.discipline_code,
                          f.page_kinds, f.equipment_facts, f.balance_facts)
            for d, f in zip(before_docs, before_facts)
        ]
        after_inputs = [
            DocumentInput(d.name, f.pages, f.text_facts, f.room_facts, d.discipline_code,
                          f.page_kinds, f.equipment_facts, f.balance_facts)
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
        run.provider = config.provider
        run.model = config.resolved_model()
        db.commit()
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
                        config, context=context, discipline=before_doc.discipline_code,
                    )
                else:
                    result = compare_page_pair(
                        before_doc.file_path, row.before_page,
                        after_doc.file_path, row.after_page,
                        config, context=context, discipline=before_doc.discipline_code,
                    )
            except Exception as exc:  # noqa: BLE001 — одна упавшая пара не должна ронять весь прогон
                result = None
                row.llm_status, row.llm_error = "error", str(exc)
            else:
                if result is None:
                    # Вызов дошёл до ответа, но не удалось разобрать JSON
                    # (см. llm.extract_json_object) — не то же самое, что
                    # сетевой сбой, но для инспектора одинаково "ИИ не сказал
                    # ничего по этой паре", и это должно быть видно, а не
                    # выглядеть как "различий нет".
                    row.llm_status, row.llm_error = "error", "ИИ ответил, но ответ не разобран как JSON"
                else:
                    row.llm_status = "ok"
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
            if row.llm_status == "ok":
                run.pairs_llm_ok += 1
            else:
                run.pairs_llm_error += 1
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


def _run_triangulated(run_id: int) -> None:
    """Фоновая задача: реальный движок Приложения Г (реестры помещений/
    оборудования, комплектность, требования из прозы, триангуляция,
    очередь эскалации, см. `triangulated_pipeline.py`) на уже загруженных
    документах — та же плоскость, что и `/documents` (см. `upload_document`
    выше), другой анализ, не `_run_analysis` (Г.51, прямое сравнение
    листов зрением). Фоновая задача, а не синхронный ответ, — комплект
    может быть сотни страниц (см. `UploadZone` во фронтенде, тот же довод),
    прогон может занять больше времени, чем разумно держать HTTP-запрос
    открытым."""
    db = next(get_session())
    try:
        run = db.get(models.TriangulatedRun, run_id)
        if run is None:
            return
        before_docs = [db.get(models.Document, i) for i in run.before_document_ids]
        after_docs = [db.get(models.Document, i) for i in run.after_document_ids]
        if any(d is None for d in before_docs) or any(d is None for d in after_docs):
            run.status = "error"
            run.error = "один или несколько документов не найдены (удалены после создания прогона?)"
            db.commit()
            return

        config = _llm_config(db)
        run.provider = config.provider
        db.commit()

        result = run_triangulated_analysis(
            [d.file_path for d in before_docs],
            [d.file_path for d in after_docs],
            room_keys=run.room_keys,
            llm_config=config,
            before_names=[d.name for d in before_docs],
            after_names=[d.name for d in after_docs],
        )
        run.result = result
        run.status = "done"
        db.commit()
    except Exception as exc:  # noqa: BLE001 — сбой прогона должен быть виден инспектору, не ронять сервер
        run = db.get(models.TriangulatedRun, run_id)
        if run is not None:
            run.status = "error"
            run.error = str(exc)
            db.commit()
    finally:
        db.close()


@app.post("/triangulated-runs", response_model=schemas.TriangulatedRunOut)
def create_triangulated_run(
    body: schemas.TriangulatedRunCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_session)
):
    run = models.TriangulatedRun(
        before_document_ids=body.before_document_ids,
        after_document_ids=body.after_document_ids,
        room_keys=body.room_keys,
        status="running",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    background_tasks.add_task(_run_triangulated, run.id)
    return run


@app.get("/triangulated-runs/{run_id}", response_model=schemas.TriangulatedRunOut)
def get_triangulated_run(run_id: int, db: Session = Depends(get_session)):
    run = db.get(models.TriangulatedRun, run_id)
    if run is None:
        raise HTTPException(404, "not found")
    return run


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


@app.get("/analysis-runs/{run_id}/pairs", response_model=list[schemas.PagePairOut])
def list_page_pairs(run_id: int, db: Session = Depends(get_session)):
    # "Данные о работе ИИ" по прогону: какие пары листов реально дошли до
    # ответа модели и какие упали — без этого списка "расхождений не найдено"
    # неотличимо на глаз от "ИИ не ответил ни разу" (см. AnalysisRun.pairs_llm_error).
    return (
        db.query(models.PagePair)
        .filter(models.PagePair.run_id == run_id)
        .order_by(models.PagePair.id)
        .all()
    )


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


def _run_pd(run_id: int) -> None:
    """Фоновая задача: стадия 1 (разбор ПД, Г.86) на загруженных документах.

    Г.94 — сценарий инспектора: загрузил документацию, нажал кнопку.
    Промпт, модель, разбивка на пачки и правила выжимки зашиты в код и
    сюда не передаются: их не надо знать, вводить и присылать.

    Связь с провайдером проверяется ДО разбора (Г.91): разбор тома — это
    десятки вызовов и минуты, а при оборванной связи каждый молча вернул
    бы пустой результат, и прогон закончился бы правдоподобной пустой
    сводкой. Это ловушка Г.77, уже стоившая проекту трёх раундов правок
    промпта против бага, которого в промпте не было.
    """
    db = next(get_session())
    try:
        run = db.get(models.PdRun, run_id)
        if run is None:
            return
        docs = [(i, db.get(models.Document, i)) for i in run.document_ids]
        missing = [str(i) for i, d in docs if d is None]
        if missing:
            run.status = "error"
            run.error = ("документы не найдены: " + ", ".join(missing)
                         + " (удалены после создания прогона?)")
            db.commit()
            return

        config = _llm_config(db)
        run.provider = config.provider
        db.commit()

        # Г.97 — третий элемент: раздел, заданный вручную; он побеждает.
        sources = [(d.file_path, d.name, _manual_section(d)) for _, d in docs]

        # Г.95/Г.98 — состав считается ПЕРВЫМ и БЕЗ УСЛОВИЙ, до проверки
        # связи: он детерминированный, модели не требует и полезен сам по
        # себе. Раньше он стоял после проверки, и прогон без ключа возвращал
        # инспектору ноль — хотя перечень листов, таблиц и графики можно
        # было отдать. Найдено прогоном «как инспектор» на реальном
        # комплекте: обе кнопки вернули только ошибку.
        volumes = [describe_volume(path, name, manual) for path, name, manual in sources]
        run.composition = render_composition(volumes)
        db.commit()

        reachable, why = check_llm_reachable(config if config.api_key else None)
        if not reachable:
            # Состав уже посчитан и сохранён выше — инспектор увидит его
            # вместе с причиной, по которой требования не извлекались.
            run.status = "error"
            run.error = (f"состав комплекта разобран, но требования НЕ извлекались: "
                         f"связь с провайдером {config.provider} не прошла — {why}. "
                         "Это не «в документах нет требований».")
            db.commit()
            return

        # Г.99 — наименования листов, не давшиеся текстом, дочитываются по
        # изображению штампа. Только здесь: шаг требует связи, а состав выше
        # обязан считаться и без неё. Бюджет нулевой по умолчанию, поэтому
        # без явного решения администратора не тратится ни одного вызова.
        remaining = _sheet_vision_budget()
        if remaining:
            for volume, (path, _, _) in zip(volumes, sources, strict=True):
                if remaining <= 0:
                    break  # бюджет один на весь прогон, а не на каждый том
                name_unread_sheets(volume, path,
                                   lambda page: read_stamp_ocr(page, config).sheet_name,
                                   remaining)
                remaining -= volume.vision_calls
            run.composition = render_composition(volumes)
            db.commit()

        requirements = extract_requirements_llm(load_text_facts(sources), config=config)
        run.summary = render_summary(requirements)
        run.requirements_total = len(requirements)
        run.extractor = "llm"
        if run.side == "before":
            # Г.87 — тот же склад, что у CLI: отсюда разбор ПД берёт стадия
            # сверки с РД, здесь же копится датасет. Разбор РД сюда НЕ идёт:
            # сверке нужен текст рабочей документации, а не извлечённые из
            # неё требования, и смешивать их в одном хранилище значило бы
            # потом гадать, чей это разбор.
            run.store_run_id = save_run(
                requirements, documents=[d.name for _, d in docs], extractor="llm",
                provider=config.provider, model=config.model,
            )
        run.status = "done"
        db.commit()
    except Exception as exc:  # noqa: BLE001 — сбой прогона виден инспектору, сервер жив
        run = db.get(models.PdRun, run_id)
        if run is not None:
            run.status = "error"
            run.error = f"{type(exc).__name__}: {exc}"
            db.commit()
    finally:
        db.close()


@app.post("/pd-runs", response_model=schemas.PdRunOut)
def create_pd_run(
    body: schemas.PdRunCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
):
    """Кнопка «Разобрать документацию». Единственный вход стадии 1 из
    интерфейса: на входе — какие документы, больше ничего."""
    if not body.document_ids:
        raise HTTPException(400, "не выбран ни один документ")
    if body.side not in ("before", "after"):
        raise HTTPException(400, "side должен быть 'before' (ПД) или 'after' (РД)")
    run = models.PdRun(document_ids=body.document_ids, side=body.side, status="running")
    db.add(run)
    db.commit()
    db.refresh(run)
    background_tasks.add_task(_run_pd, run.id)
    return run


@app.get("/pd-runs/{run_id}", response_model=schemas.PdRunOut)
def get_pd_run(run_id: int, db: Session = Depends(get_session)):
    run = db.get(models.PdRun, run_id)
    if run is None:
        raise HTTPException(404, "not found")
    return run


@app.get("/pd-runs", response_model=list[schemas.PdRunOut])
def list_pd_runs(db: Session = Depends(get_session)):
    return db.query(models.PdRun).order_by(models.PdRun.id.desc()).limit(50).all()


def _manual_section(doc: models.Document) -> str | None:
    """Раздел, заданный инспектором вручную, или None (Г.97)."""
    return doc.discipline_code if doc.classification_source == "manual" else None


def _rd_room_index(sources: list[tuple[str, str]]) -> dict[str, list[dict]]:
    """{номер помещения: [{path, page}]} по рабочей документации.

    Единственный способ узнать, КАКОЙ лист смотреть под требование: у
    требования из прозы ПД нет строки реестра, которая указала бы лист
    (Г.35). Битый файл пропускается с причиной, а не роняет прогон."""
    index: dict[str, list[dict]] = {}
    for source in sources:
        path, name = source[0], source[1]
        try:
            facts = extract_document_facts(path, name)
        except Exception as exc:  # noqa: BLE001 — один файл не роняет сверку
            print(f"реестр помещений РД не построен ({exc}): {name}", file=sys.stderr)
            continue
        for fact in facts.room_facts:
            key = str(fact.get("key") or "")
            if key:
                index.setdefault(key, []).append({"path": path, "page": fact["page"], "name": name})

    # Г.98 — резерв по отметке этажа (Г.40): план и его экспликация физически
    # лежат на РАЗНЫХ листах, и номер помещения на самом плане в текстовом
    # слое часто отсутствует. Отметка высоты (`+X.XXX`) остаётся текстом даже
    # там, где остальная графика в кривых, и даёт листы ТОГО ЖЕ ЭТАЖА — не
    # гарантированно то же помещение, поэтому кандидаты добавляются ПОСЛЕ
    # найденных по номеру и берутся только если бюджет листов не исчерпан
    # настоящими совпадениями.
    try:
        index = augment_room_index_with_level_fallback(index, [p for p, _ in
                                                               [(s[0], s[1]) for s in sources]])
    except Exception as exc:  # noqa: BLE001 — резерв не обязан работать всегда
        print(f"резерв по отметке этажа не построен: {exc}", file=sys.stderr)
    return index


def _run_compliance(run_id: int) -> None:
    """Фоновая задача: сверка требований ПД с рабочей документацией (Г.96).

    Требования берутся из СОХРАНЁННОГО разбора ПД, а не извлекаются заново.
    Связь проверяется до работы (Г.91), как и в стадии 1.
    """
    db = next(get_session())
    try:
        run = db.get(models.ComplianceRun, run_id)
        if run is None:
            return
        pd_run = db.get(models.PdRun, run.pd_run_id)
        if pd_run is None or pd_run.store_run_id is None:
            run.status = "error"
            run.error = ("разбор ПД не найден или не сохранён — сначала выполните разбор "
                         "проектной документации")
            db.commit()
            return

        requirements = load_run(pd_run.store_run_id)
        if not requirements:
            run.status = "error"
            run.error = ("в сохранённом разборе ПД нет ни одного требования — сверять не с чем "
                         "(это не «в РД всё выполнено»)")
            db.commit()
            return
        run.requirements_total = len(requirements)

        rd_docs = [(i, db.get(models.Document, i)) for i in run.rd_document_ids]
        missing = [str(i) for i, d in rd_docs if d is None]
        if missing:
            run.status = "error"
            run.error = "документы РД не найдены: " + ", ".join(missing)
            db.commit()
            return

        config = _llm_config(db)
        run.provider = config.provider
        db.commit()
        has_key = bool(config.api_key)
        if has_key:
            reachable, why = check_llm_reachable(config)
            if not reachable:
                run.status = "error"
                run.error = (f"связь с провайдером {config.provider} не прошла — {why}. "
                             "Сверка НЕ выполнена: это не «в РД ничего не подтвердилось».")
                db.commit()
                return

        sources = [(d.file_path, d.name, _manual_section(d)) for _, d in rd_docs]
        room_index = _rd_room_index(sources) if has_key else {}

        def _candidates(rooms: list[str], _sources) -> list[tuple[str, int]]:
            pages: list[tuple[str, int]] = []
            for room in rooms:
                for entry in room_index.get(str(room), []):
                    pair = (entry["path"], entry["page"])
                    if pair not in pages:
                        pages.append(pair)
            return pages

        result = check_compliance(
            requirements,
            rd_text_facts=load_text_facts(sources),
            rd_sources=sources,
            config=config if has_key else None,
            llm_verify=(lambda reqs, facts, cfg: verify_general_requirements_llm(reqs, facts, cfg))
            if has_key else None,
            vision_check=check_requirement_on_page if has_key else None,
            candidate_pages=_candidates if has_key else None,
        )
        run.report = render_compliance_report(result)
        run.counts = result.counts
        run.status = "done"
        db.commit()
    except Exception as exc:  # noqa: BLE001 — сбой виден инспектору, сервер жив
        run = db.get(models.ComplianceRun, run_id)
        if run is not None:
            run.status = "error"
            run.error = f"{type(exc).__name__}: {exc}"
            db.commit()
    finally:
        db.close()


@app.post("/compliance-runs", response_model=schemas.ComplianceRunOut)
def create_compliance_run(
    body: schemas.ComplianceRunCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
):
    """Кнопка «Сверить РД с требованиями ПД» — третья и последняя."""
    if not body.rd_document_ids:
        raise HTTPException(400, "не выбран ни один документ рабочей документации")
    run = models.ComplianceRun(
        pd_run_id=body.pd_run_id, rd_document_ids=body.rd_document_ids, status="running")
    db.add(run)
    db.commit()
    db.refresh(run)
    background_tasks.add_task(_run_compliance, run.id)
    return run


@app.get("/compliance-runs/{run_id}", response_model=schemas.ComplianceRunOut)
def get_compliance_run(run_id: int, db: Session = Depends(get_session)):
    run = db.get(models.ComplianceRun, run_id)
    if run is None:
        raise HTTPException(404, "not found")
    return run


@app.get("/llm-check", response_model=schemas.LlmCheckOut)
def llm_check(db: Session = Depends(get_session)):
    """Проверка связи одним коротким вызовом (Г.91) — чтобы инспектор узнал
    о проблеме до запуска разбора на сотни страниц, а не по его пустому
    результату."""
    config = _llm_config(db)
    if not config.api_key:
        return schemas.LlmCheckOut(
            reachable=False, provider=config.provider,
            message="ключ провайдера не задан — ни в настройках, ни в переменной окружения "
                    f"{_PROVIDER_ENV_KEY.get(config.provider, '')}",
        )
    ok, message = check_llm_reachable(config)
    return schemas.LlmCheckOut(reachable=ok, provider=config.provider, message=message)


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
