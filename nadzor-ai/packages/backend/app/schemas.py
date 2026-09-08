from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    side: str
    pages: int
    discipline_code: str | None
    classification_source: str | None
    status: str
    uploaded_at: dt.datetime
    # Размер оригинала и число частей, на которые он разрезан для обработки.
    # Инспектору это говорит, почему тяжёлый том обрабатывается по кускам;
    # нумерация листов при этом остаётся исходной.
    size: int = 0
    parts_count: int = 0


class StorageStats(BaseModel):
    """Что занимает место и что можно освободить."""
    files: int
    bytes: int
    cache_files: int
    cache_bytes: int
    documents: int
    retention_days: int


class StorageFile(BaseModel):
    """Один ТОМ в хранилище — то, что инспектор видит списком.

    Именно том, а не кусок тома (Г.114). Тяжёлый файл режется на части для
    обработки, и раньше каждая часть стояла в списке отдельной безымянной
    строкой: один загруженный документ выглядел как десяток неизвестных
    файлов. Части теперь сложены под именем своего тома, а их число стоит
    рядом — устройство хранения, а не отдельные документы.

    `documents` — названия документов, которые на него ссылаются. Пусто
    означает «файл больше никому не нужен и уйдёт по сроку хранения», и это
    ровно то, ради чего список открывают.
    """
    digest: str
    size: int
    pages: int
    created_at: dt.datetime
    used_at: dt.datetime
    documents: list[str] = []
    cached: bool = False
    # Из скольких частей состоит том. 0 — не дробился.
    parts: int = 0
    # Суммарный размер с частями: место, которое том занимает на самом деле.
    total_size: int = 0
    # Есть ли на него ссылка из загруженных документов. Пусто в `documents`
    # и `in_use=false` — это одно и то же состояние, названное словом.
    in_use: bool = False


class StorageCleanupResult(BaseModel):
    removed_files: int
    freed_bytes: int
    cache_removed: int
    cache_freed_bytes: int
    kept_referenced: int


class AnalysisRunCreate(BaseModel):
    before_document_ids: list[int]
    after_document_ids: list[int]


class AnalysisRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: dt.datetime
    status: str
    pairs_total: int
    pairs_done: int
    provider: str
    model: str
    pairs_llm_ok: int
    pairs_llm_error: int
    error: str | None


class PagePairOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    before_document_id: int
    before_document_name: str
    before_page: int
    after_document_id: int
    after_document_name: str
    after_page: int
    matched_by: str
    page_kind: str
    discipline_mismatch: bool
    llm_status: str
    llm_error: str | None


class FindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int
    pair_id: int | None
    kind: str
    label: str
    change_text: str
    severity: str
    field_check: str
    reviewed_status: str
    created_at: dt.datetime
    before_document_id: int | None
    before_page: int | None
    after_document_id: int | None
    after_page: int | None


class FindingUpdate(BaseModel):
    reviewed_status: str


class TriangulatedRunCreate(BaseModel):
    before_document_ids: list[int]
    after_document_ids: list[int]
    # Явный список номеров помещений для графа маршрутизации (Г.30 пп.3-5,
    # routing_diff.py) — без ключа ИИ иначе не проверяется вовсе (Г.50).
    room_keys: list[str] = []


class TriangulatedRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: dt.datetime
    status: str
    provider: str
    error: str | None
    result: dict | None


class SettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    provider: str
    base_url: str
    model: str
    # Сам ключ наружу НЕ отдаётся (Г.112/Б.5): интерфейсу нужен один факт —
    # задан он или нет. Отдавать секрет в браузер только ради того, чтобы
    # показать точки в поле ввода, значит класть его в каждый ответ сервера
    # и в историю запросов.
    api_key_set: bool = False
    retention_days: int = 90
    max_upload_kb: int = 512 * 1024
    max_pages: int = 5000
    part_kb: int = 32 * 1024


class SettingsUpdate(BaseModel):
    provider: str
    base_url: str = ""
    model: str = ""
    # None означает «не трогать»: интерфейс ключ не присылает вовсе, и
    # пустая строка не должна стирать заданный при развёртывании ключ.
    api_key: str | None = None
    retention_days: int | None = None
    max_upload_kb: int | None = None
    max_pages: int | None = None
    part_kb: int | None = None


class PdRunCreate(BaseModel):
    """Запрос инспектора: «разобрать вот эти документы». Больше ничего —
    ни промпта, ни модели, ни ключа: всё это Г.94 держит на сервере."""
    document_ids: list[int]
    # 'before' — проектная документация, 'after' — рабочая (Г.95).
    side: str = "before"


class PdRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: dt.datetime
    status: str
    provider: str
    extractor: str
    error: str | None
    summary: str
    requirements_total: int
    store_run_id: int | None
    side: str
    composition: str
    # Ход работы: этап словами и пройдено/всего в пачках. Оценку остатка
    # считает интерфейс по фактической скорости этого прогона — придумывать
    # её на сервере не из чего, а показывать выдуманное время хуже, чем не
    # показывать никакого (Г.112).
    stage: str = ""
    units_total: int = 0
    units_done: int = 0
    started_at: dt.datetime | None = None


class RunCancelOut(BaseModel):
    """Ответ на просьбу остановить прогон.

    `detail` обязателен: остановка не мгновенная, и без объяснения кнопка
    выглядела бы сломанной — нажал, а прогон ещё идёт (Г.114).
    """
    id: int
    status: str
    detail: str


class DocumentDigest(BaseModel):
    """ВЫЖИМКА разбора документа — то, что показывают вместо всего подряд."""
    name: str
    pages: int
    drawings: int
    text_pages: int
    pages_without_text: int
    pages_without_text_list: list[int] = []
    excluded: int = 0
    excluded_reasons: list[str] = []
    rooms_total: int = 0
    rooms: list[str] = []
    equipment_total: int = 0
    equipment: list[str] = []
    sheets_total: int = 0
    sheets: list[str] = []
    systems: list[str] = []
    text_chars: int = 0


class DocumentPageRow(BaseModel):
    """Один лист разобранного документа. Без текста листа: подробности
    достаются по требованию, а не грузятся все сразу."""
    page: int
    kind: str = ""
    sheet_no: str = ""
    sheet_name: str = ""
    shifr: str = ""
    rooms: list[str] = []
    equipment: int = 0
    chars: int = 0
    excluded: str = ""


class RequirementOut(BaseModel):
    """Требование, извлечённое из проектной документации."""
    page: int
    sentence: str
    summary: str = ""
    code: str | None = None
    document: str = ""
    section: str | None = None
    rooms: list[str] = []
    norm: str = ""


class LlmCheckOut(BaseModel):
    """Ответ предполётной проверки связи (Г.91) для кнопки в интерфейсе."""
    reachable: bool
    provider: str
    message: str
    # Чем проверяется TLS. Отдельным полем, а не внутри message: состояние
    # «проверка отключена» обязано быть видно всегда, а не только когда
    # что-то сломалось (Г.110).
    tls: str = ""


class ComplianceRunCreate(BaseModel):
    """Кнопка «Сверить РД с требованиями ПД»: на входе сохранённый разбор ПД
    и документы РД. Требования не передаются — они уже разобраны (Г.96)."""
    pd_run_id: int
    rd_document_ids: list[int]


class ComplianceRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: dt.datetime
    status: str
    pd_run_id: int
    provider: str
    error: str | None
    report: str
    counts: dict
    requirements_total: int
    # Ход работы: этап словами и пройдено/всего в пачках. Оценку остатка
    # считает интерфейс по фактической скорости этого прогона — придумывать
    # её на сервере не из чего, а показывать выдуманное время хуже, чем не
    # показывать никакого (Г.112).
    stage: str = ""
    units_total: int = 0
    units_done: int = 0
    started_at: dt.datetime | None = None


class DocumentUpdate(BaseModel):
    """Ручная правка раздела/тома (Г.97).

    `None` — снять ручную правку и вернуться к автоматическому определению:
    ошибочный ввод не должен становиться необратимым.
    """
    discipline_code: str | None = None


class ReviewMessageCreate(BaseModel):
    """Реплика инспектора в разборе результата сверки (Г.100).

    `kind` пуст у обычного вопроса и содержит род ошибки у замечания.
    Разделение задаётся ЗДЕСЬ, на входе, а не разбором переписки задним
    числом: в датасет идут только замечания, и отличить их от реплики после
    того, как они смешались в один поток, нельзя.
    """
    text: str
    kind: str = ""
    target: str = ""


class ReviewMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: dt.datetime
    role: str
    text: str
    kind: str
    target: str
    approved: bool
    no_answer_reason: str


class ReviewMessageUpdate(BaseModel):
    """Разрешить или отозвать использование замечания примером в промпте.

    Отдельным действием, а не флагом при создании: Г.11 требует, чтобы
    правило заводилось осознанным решением человека, а не по факту того, что
    замечание вообще было написано.
    """
    approved: bool
