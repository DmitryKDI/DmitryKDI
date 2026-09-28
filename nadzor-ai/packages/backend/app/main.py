"""FastAPI-приложение «Инспектор ИИ»: приём документов комплекта, изображение
листа, проверка связи с локальной моделью, настройки хранения.

Сама проверка ПД → РД → ИД по матрице параметров ТЗ — `official_api`
(`/official/...`, интерфейс инспектора) и `api_v1` (`/api/v1/...`, внешний
контракт с process_id). Здесь — только общий для них приём файлов.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pymupdf
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from . import (
    audit,
    auth,
    facts_store,
    file_store,
    jobs,
    models,
    openapi30,
    parameter_catalog,
    schemas,
)
from .admin_api import router as admin_router
from .api_v1 import router as api_v1_router
from .auth_api import router as auth_router
from .classification import classify_document
from .db import get_session, init_db
from .document_split import split_pdf
from .llm import LlmConfig, available_models, check_llm_reachable, local_config, model_configured
from .ml_api import router as ml_router
from .official_api import router as official_router
from .vision import make_llm_stamp_classifier, render_page_to_png_bytes

# Каталог кэша оригиналов — производное от хранилища (`file_store`): его
# можно удалить целиком, файлы восстановятся из базы по требованию.
UPLOAD_DIR = file_store.CACHE_DIR

app = FastAPI(title="Инспектор ИИ — API")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)
# Вход открыт; всё остальное — только после входа и по роли (ТЗ 12, п.1–2).
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(ml_router)
app.include_router(official_router, dependencies=[Depends(auth.require(*auth.READERS))])
app.include_router(api_v1_router, dependencies=[Depends(auth.require(*auth.READERS))])


@app.middleware("http")
async def audit_trail(request: Request, call_next):
    """Каждое действие, меняющее данные, — запись журнала аудита (ТЗ 12, п.4)."""
    response = await call_next(request)
    if audit.should_record(request):
        audit.record(request, response.status_code)
    return response

# ТЗ 1.3: схема API — OpenAPI 3.0 (FastAPI по умолчанию строит 3.1).
openapi30.install(app)


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
            # Модель только локальная, инспектор ничего не выбирает: адрес и
            # имя модели задаются окружением развёртывания (local_config).
            db.add(models.Settings(id=1, provider="local", base_url="", model=""))
            db.commit()
    finally:
        db.close()
    auth.ensure_initial_admin()
    parameter_catalog.ensure_seeded()


_ensure_schema_and_defaults()


@app.on_event("startup")
def _start_background_jobs() -> None:
    # Фоновые задачи (повторы передачи, еженедельный отчёт) — только в
    # работающем сервисе; тестам фоновый поток не нужен.
    jobs.start()


# Поле `tls` ответа проверки связи сохранено ради совместимости контракта.
# Модель работает внутри контура по внутренней сети контейнеров: внешнего
# канала, который нужно было бы защищать сертификатами, у решения нет.
_LOCAL_TRANSPORT = "внутренняя сеть контура, внешних соединений нет"


def _llm_config(db: Session) -> LlmConfig:
    """Конфигурация модели для прогона, запущенного из интерфейса.

    Модель локальная и задаётся окружением развёртывания (см.
    llm.local_config), поэтому настройки из базы здесь не читаются.
    """
    return local_config()


# ---------- Документы ----------


def _document_out(doc: models.Document) -> schemas.DocumentOut:
    """Ответ о документе с вычисляемыми полями: число частей в БД не
    хранится, а инспектору важно видеть, что тяжёлый том обрабатывается
    по кускам (нумерация листов при этом исходная)."""
    out = schemas.DocumentOut.model_validate(doc)
    out.parts_count = len(doc.parts or [])
    return out


def _limits(db: Session) -> models.Settings:
    row = db.query(models.Settings).first()
    if row is None:
        row = models.Settings()
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def _referenced_digests(db: Session) -> set[str]:
    """Отпечатки, на которые ещё ссылается хоть один документ.

    Удалить оригинал, на который есть ссылка, нельзя ни по какому сроку:
    иначе разбор перестанет открываться, а инспектор увидит ошибку вместо
    документа, который он сам загрузил.
    """
    keep: set[str] = set()
    for doc in db.query(models.Document).all():
        if doc.digest:
            keep.add(doc.digest)
        for part in (doc.parts or []):
            if isinstance(part, dict) and part.get("digest"):
                keep.add(str(part["digest"]))
    return keep


def _parse_document(document_id: int) -> None:
    """Разбор загруженного тома: разбор страниц и определение раздела.

    Вынесено из ответа на загрузку (Г.114). Разбор тома в сотни листов —
    это минуты; пока он шёл внутри запроса, браузер держал соединение и
    показывал «обрабатывается… 167 с», а инспектор не мог ни загрузить
    следующий файл, ни понять, работает ли программа вообще. Теперь ответ
    приходит сразу со статусом `parsing`, а состояние видно в списке.
    """
    db = next(get_session())
    try:
        doc = db.get(models.Document, document_id)
        if doc is None:
            return
        try:
            facts = facts_store.facts_for(doc.file_path, doc.name, digest=doc.digest)
            config = _llm_config(db)
            vision_fn = make_llm_stamp_classifier(config) if config.provider else None
            classification = classify_document(doc.file_path, doc.name, vision_stamp_fn=vision_fn)
            doc.pages = facts.pages
            # Ручной выбор раздела не затирается: инспектор мог поправить
            # раздел, пока шёл разбор, и его слово последнее (Г.97).
            if doc.classification_source != "manual":
                doc.discipline_code = classification.discipline_code
                doc.classification_source = classification.source
            doc.status = "ok"
        except Exception as exc:  # noqa: BLE001 — на распознавании не валим загрузку
            doc.status = "error"
            doc.classification_source = str(exc)
        db.commit()
    finally:
        db.close()


@app.post("/documents", response_model=schemas.DocumentOut,
          dependencies=[Depends(auth.require(*auth.VERIFIERS))])
def upload_document(side: str, file: UploadFile, background_tasks: BackgroundTasks,
                    db: Session = Depends(get_session)):
    if side not in ("before", "after"):
        raise HTTPException(400, "side must be 'before' or 'after'")
    # Читаем в память один раз: отпечаток, лимит и укладка в хранилище нужны
    # до того, как файл где-то окажется. Лимит проверяется по факту
    # прочитанного, а не по заголовку запроса — заголовок присылает клиент.
    from .document_convert import UnsupportedFormatError, to_pdf
    try:
        converted = to_pdf(file.file.read(), file.filename or "document")
    except UnsupportedFormatError as exc:
        raise HTTPException(415, str(exc)) from exc
    doc = ingest_pdf(db, converted.pdf, file.filename or "document.pdf", side,
                     background_tasks)
    # Через `_document_out`, а не напрямую: число частей — вычисляемое поле,
    # и возврат ORM-объекта отдавал бы ноль частей у разрезанного тома.
    return _document_out(doc)


def ingest_pdf(db: Session, data: bytes, filename: str, side: str,
               background_tasks: BackgroundTasks) -> models.Document:
    """Проверка, укладка в хранилище и постановка разбора — общая для всех
    способов загрузки. Отказ — HTTPException с причиной для пользователя."""
    limits = _limits(db)
    if not data:
        raise HTTPException(400, "пустой файл")
    max_bytes = max(1, limits.max_upload_kb) * 1024
    if len(data) > max_bytes:
        raise HTTPException(413, f"файл больше допустимого: {len(data) / 1048576:.1f} МБ "
                                 f"при пределе {limits.max_upload_kb / 1024:.0f} МБ")
    # Тип определяется по сигнатуре, а не по расширению (Б.4): расширение
    # приходит от загружающей стороны и ничего не доказывает.
    if not data.startswith(b"%PDF-"):
        raise HTTPException(415, "это не PDF (проверка по сигнатуре файла, не по расширению)")

    # Оригинальное имя файла — только отображаемые метаданные (используется в
    # классификации по имени и в подписях находок), на диск не идёт вообще:
    # приходит от клиента и не должно участвовать в построении пути.
    original_name = Path(filename).name

    digest = file_store.digest_of(data)
    pages = 0
    try:
        with pymupdf.open(stream=data, filetype="pdf") as probe:
            pages = probe.page_count
    except Exception as exc:  # noqa: BLE001 — битый PDF не должен ронять сервер
        raise HTTPException(415, f"PDF не открывается: {exc}") from exc
    if pages > max(1, limits.max_pages):
        raise HTTPException(413, f"в документе {pages} страниц при пределе {limits.max_pages}")

    # Дедупликация по содержимому: тот же том, загруженный второй раз,
    # места больше не занимает — ключ хранилища и есть отпечаток.
    file_store.put(data, pages=pages)
    dest = file_store.materialize(digest)
    if dest is None:
        raise HTTPException(500, "оригинал не сохранён в хранилище")

    # Тяжёлый том режется на части ПО ВЕСУ (`document_split`): нумерация
    # листов при этом остаётся исходной, части — внутреннее устройство.
    part_rows: list[dict] = []
    part_bytes = max(1, limits.part_kb) * 1024
    if len(data) > part_bytes:
        try:
            for part in split_pdf(dest, part_bytes):
                part_digest = file_store.put(part.data, pages=part.pages)
                part_rows.append({"digest": part_digest, "first_page": part.first_page,
                                  "pages": part.pages})
        except Exception as exc:  # noqa: BLE001 — не смогли разрезать, работаем целиком
            print(f"том не разрезан на части ({exc}): {original_name}", file=sys.stderr)
            part_rows = []

    doc = models.Document(name=original_name, side=side, file_path=str(dest),
                          status="parsing", digest=digest, size=len(data), parts=part_rows)
    db.add(doc)
    db.commit()
    db.refresh(doc)

    # Число страниц известно из проверки лимита — показываем сразу, не
    # дожидаясь разбора: пустая строка «0 листов» рядом с именем файла
    # выглядит как сбой загрузки.
    doc.pages = pages
    db.commit()
    db.refresh(doc)
    background_tasks.add_task(_parse_document, doc.id)
    return doc


@app.get("/documents", response_model=list[schemas.DocumentOut],
         dependencies=[Depends(auth.require(*auth.READERS))])
def list_documents(db: Session = Depends(get_session)):
    rows = db.query(models.Document).order_by(models.Document.uploaded_at.desc()).all()
    return [_document_out(d) for d in rows]


@app.delete("/documents/{document_id}", dependencies=[Depends(auth.require(*auth.VERIFIERS))])
def delete_document(document_id: int, db: Session = Depends(get_session)):
    doc = db.get(models.Document, document_id)
    if doc is None:
        raise HTTPException(404, "not found")
    if any(
        (row.source_metadata or {}).get("predecessor_id") == document_id
        for row in db.query(models.Document).all()
    ):
        raise HTTPException(409, "документ является предыдущей редакцией")
    if any(
        any(item.get("id") == document_id for item in (run.input_snapshot or []))
        for run in db.query(models.OfficialRun).all()
    ):
        raise HTTPException(409, "документ входит в сохранённый официальный протокол")
    mine = {doc.digest} | {str(p.get("digest")) for p in (doc.parts or []) if isinstance(p, dict)}
    # История карточки принадлежит документу. Без явного удаления внешний ключ
    # блокирует кнопку удаления после первого же сохранения метаданных.
    db.query(models.DocumentMetadataEvent).filter_by(document_id=doc.id).delete(
        synchronize_session=False,
    )
    db.delete(doc)
    db.commit()
    # Оригинал удаляется, только если на него не осталось ссылок: одно и то
    # же содержимое бывает загружено в двух комплектах (дедупликация), и
    # удаление одного документа не должно уносить чужой файл.
    still_used = _referenced_digests(db)
    for digest in mine - still_used - {""}:
        file_store.forget(digest)
    return {"ok": True}


@app.get("/page-image/{document_id}/{page}",
         dependencies=[Depends(auth.require(*auth.READERS))])
def get_page_image(document_id: int, page: int, db: Session = Depends(get_session)):
    doc = db.get(models.Document, document_id)
    if doc is None:
        raise HTTPException(404, "not found")
    if page < 1 or page > doc.pages:
        raise HTTPException(404, "page out of range")
    png_bytes = render_page_to_png_bytes(doc.file_path, page)
    return Response(content=png_bytes, media_type="image/png")


@app.get("/llm-check", response_model=schemas.LlmCheckOut,
         dependencies=[Depends(auth.require(*auth.READERS))])
def llm_check(db: Session = Depends(get_session)):
    """Проверка связи одним коротким вызовом (Г.91) — чтобы инспектор узнал
    о проблеме до запуска разбора на сотни страниц, а не по его пустому
    результату."""
    config = _llm_config(db)
    if not model_configured(config):
        # «Модель не задана» и «модель не отвечает» требуют от администратора
        # разного: подменять одно другим значит повторять Г.10.
        ok, message = False, ("локальная модель не задана: укажите "
                              "NADZOR_LOCAL_LLM_MODEL и NADZOR_LOCAL_LLM_URL")
    else:
        ok, message = check_llm_reachable(config)
    # Перечень моделей запрашивается только когда связь уже подтверждена:
    # при мёртвой связи это второй вызов с заранее известным исходом.
    models = available_models(config) if ok else None
    return schemas.LlmCheckOut(
        reachable=ok, provider=config.provider, message=message,
        tls=_LOCAL_TRANSPORT,
        model=config.resolved_model(),
        model_available=models.configured_available if models else None,
        models_available=models.models if models else [],
        models_message=(models.error if models else
                        "перечень моделей не запрашивался: связи нет"),
    )


@app.get("/settings", response_model=schemas.SettingsOut,
         dependencies=[Depends(auth.require(*auth.READERS))])
def get_settings(db: Session = Depends(get_session)):
    return _settings_out(_limits(db), db)


def _settings_out(row: models.Settings, db: Session) -> schemas.SettingsOut:
    """Настройки для интерфейса: действующая модель и лимиты хранения."""
    out = schemas.SettingsOut.model_validate(row)
    # Показывается модель, которой прогон реально пойдёт, а не строка базы:
    # модель выбирает окружение развёртывания.
    effective = local_config()
    out.provider = effective.provider
    out.model = effective.resolved_model()
    out.base_url = effective.resolved_base_url()
    return out


@app.put("/settings", response_model=schemas.SettingsOut,
         dependencies=[Depends(auth.require("admin"))])
def update_settings(body: schemas.SettingsUpdate, db: Session = Depends(get_session)):
    s = db.query(models.Settings).first()
    s.provider = body.provider
    s.base_url = body.base_url
    s.model = body.model
    # Сроки и лимиты необязательны: интерфейс может прислать только настройки
    # провайдера, и тогда прежние значения сохраняются, а не обнуляются.
    for field in ("retention_days", "max_upload_kb", "max_pages", "part_kb"):
        value = getattr(body, field, None)
        if value is not None:
            setattr(s, field, max(0, int(value)))
    db.commit()
    db.refresh(s)
    return _settings_out(s, db)


@app.get("/version")
def version():
    """Какая версия кода сейчас работает.

    Появилось после того, как в браузере несколько раз открывалась старая
    версия и отличить «код не обновился» от «страница из кэша» было нечем
    (Г.113). Показывается в интерфейсе, поэтому вопрос закрывается взглядом,
    а не разбирательством.
    """
    import subprocess
    root = Path(__file__).resolve().parents[3]

    def git(*args: str) -> str:
        try:
            return subprocess.run(["git", *args], cwd=root, capture_output=True,
                                  text=True, timeout=5).stdout.strip()
        except Exception:  # noqa: BLE001 — версия не критична для работы
            return ""

    return {
        "commit": git("rev-parse", "--short", "HEAD"),
        "date": git("log", "-1", "--format=%cs"),
        "subject": git("log", "-1", "--format=%s")[:120],
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
    }
