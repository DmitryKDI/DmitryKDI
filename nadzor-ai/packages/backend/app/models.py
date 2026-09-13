"""Схема хранения — SQLite через SQLAlchemy ORM.

relationship() объявлен явно на каждой связи (а не только Column(ForeignKey))
— в этой же сессии проекта уже был такой баг,
где SQLAlchemy без relationship() не мог определить порядок вставки строк и
падал по внешнему ключу на реальном Postgres (SQLite это спускает, реальная
СУБД — нет). Здесь база тоже SQLite, но повторять ту же ошибку незачем.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Integer, String,
                        Text)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    side: Mapped[str] = mapped_column(String)  # 'before' | 'after'
    file_path: Mapped[str] = mapped_column(String)
    pages: Mapped[int] = mapped_column(Integer, default=0)
    discipline_code: Mapped[str | None] = mapped_column(String, nullable=True)
    classification_source: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="parsing")  # parsing|ok|error
    uploaded_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    # Отпечаток содержимого — ключ оригинала в отдельном хранилище
    # (`file_store`). Через него документ и файл связаны: два документа с
    # одинаковым содержимым ссылаются на один оригинал.
    digest: Mapped[str] = mapped_column(String, default="", index=True)
    size: Mapped[int] = mapped_column(Integer, default=0)
    # Части тяжёлого тома: [{digest, first_page, pages}] в порядке листов.
    # Пусто — том целый. Нумерация листов наружу всегда исходная, части —
    # внутреннее устройство хранения (`document_split`).
    parts: Mapped[list] = mapped_column(JSON, default=list)


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    status: Mapped[str] = mapped_column(String, default="running")  # running|done|error
    before_document_ids: Mapped[list] = mapped_column(JSON, default=list)
    after_document_ids: Mapped[list] = mapped_column(JSON, default=list)
    pairs_total: Mapped[int] = mapped_column(Integer, default=0)
    pairs_done: Mapped[int] = mapped_column(Integer, default=0)
    # Какой провайдер/модель реально считали этот прогон и сколько пар
    # реально дошли до ответа ИИ — без этого "критических несоответствий не
    # найдено" неотличимо от "ИИ не ответил ни разу" (см. pairs_llm_error).
    provider: Mapped[str] = mapped_column(String, default="")
    model: Mapped[str] = mapped_column(String, default="")
    pairs_llm_ok: Mapped[int] = mapped_column(Integer, default=0)
    pairs_llm_error: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String, nullable=True)

    pairs: Mapped[list["PagePair"]] = relationship(back_populates="run", cascade="all, delete-orphan")
    findings: Mapped[list["Finding"]] = relationship(back_populates="run", cascade="all, delete-orphan")
    # Момент, когда инспектор попросил остановить прогон (Г.114). Отдельно
    # от статуса: пока исполнитель не дошёл до ближайшей безопасной точки,
    # прогон ещё идёт, и врать «остановлен» раньше времени нельзя. После
    # перезапуска сервера эта отметка не даёт продолжить то, что просили
    # прекратить.
    cancelled_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)



class PagePair(Base):
    __tablename__ = "page_pairs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id"))
    before_document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    before_page: Mapped[int] = mapped_column(Integer)
    after_document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    after_page: Mapped[int] = mapped_column(Integer)
    matched_by: Mapped[str] = mapped_column(String)  # 'text' | 'position'
    page_kind: Mapped[str] = mapped_column(String, default="drawing")  # 'drawing' | 'text'
    score: Mapped[float] = mapped_column(Float, default=0.0)
    discipline_mismatch: Mapped[bool] = mapped_column(default=False)
    # Дошёл ли вызов ИИ до ответа по этой паре, а не упал (сеть, лимит
    # провайдера, невалидный JSON и т.п.) — раньше падение молча превращалось
    # в "значимых расхождений нет", неотличимое от настоящего "ИИ проверил и
    # совпадений не нашёл".
    llm_status: Mapped[str] = mapped_column(String, default="ok")  # 'ok' | 'error'
    llm_error: Mapped[str | None] = mapped_column(String, nullable=True)

    run: Mapped[AnalysisRun] = relationship(back_populates="pairs")
    before_document: Mapped[Document] = relationship(foreign_keys=[before_document_id])
    after_document: Mapped[Document] = relationship(foreign_keys=[after_document_id])
    findings: Mapped[list["Finding"]] = relationship(back_populates="pair")

    # При нескольких файлах с каждой стороны один номер страницы неоднозначен
    # без имени файла — а именно это и нужно, чтобы проверить самому, какому
    # реальному листу РД сопоставлен лист ПД (см. "Подробности по листам").
    @property
    def before_document_name(self) -> str:
        return self.before_document.name

    @property
    def after_document_name(self) -> str:
        return self.after_document.name


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id"))
    pair_id: Mapped[int | None] = mapped_column(ForeignKey("page_pairs.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String)  # 'text' | 'vision'
    label: Mapped[str] = mapped_column(String)
    change_text: Mapped[str] = mapped_column(String)
    # Порядок обхода объекта и действие на месте. Пустая строка, а не NULL:
    # модель может не вернуть поле, и на экране это должно читаться как
    # «не указано», без ветвления на None в каждом месте.
    severity: Mapped[str] = mapped_column(String, default="")
    field_check: Mapped[str] = mapped_column(String, default="")
    raw_llm_response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reviewed_status: Mapped[str] = mapped_column(String, default="new")  # new|confirmed|rejected
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    run: Mapped[AnalysisRun] = relationship(back_populates="findings")
    pair: Mapped[PagePair | None] = relationship(back_populates="findings")

    # Денормализованный доступ к листу-источнику для фронтенда (картинка
    # листа к находке) — без пары (текстовая находка старого вида, больше не
    # создаётся, но старые записи в БД могут остаться) просто None.
    @property
    def after_document_id(self) -> int | None:
        return self.pair.after_document_id if self.pair else None

    @property
    def after_page(self) -> int | None:
        return self.pair.after_page if self.pair else None

    @property
    def before_document_id(self) -> int | None:
        return self.pair.before_document_id if self.pair else None

    @property
    def before_page(self) -> int | None:
        return self.pair.before_page if self.pair else None


class TriangulatedRun(Base):
    """Прогон реального движка Приложения Г (`triangulated_pipeline.py`,
    Г.46/Г.61/Г.64/Г.65) — реестры помещений/оборудования, комплектность,
    требования из прозы, триангуляция и очередь эскалации. Отдельная
    таблица от `AnalysisRun` (тот прогоняет `_run_analysis` — прямое
    сравнение листов зрением, Г.51 — другой движок, другой контракт): эти
    два движка не смешивают свои прогоны, чтобы не приходилось гадать по
    полям одной записи, каким путём она была посчитана.

    `result` хранит весь ответ `run_triangulated_analysis()` одним JSON-
    блоком, а не по таблице на каждый тип находки: структура ответа —
    read-only витрина для инспектора, не то, что потребуется выбирать
    SQL-запросом по отдельным находкам, поэтому отдельная реляционная
    схема была бы сложностью без выгоды (см. Б.1 «бюджет сложности»)."""
    __tablename__ = "triangulated_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    status: Mapped[str] = mapped_column(String, default="running")  # running|done|error
    before_document_ids: Mapped[list] = mapped_column(JSON, default=list)
    after_document_ids: Mapped[list] = mapped_column(JSON, default=list)
    room_keys: Mapped[list] = mapped_column(JSON, default=list)
    provider: Mapped[str] = mapped_column(String, default="")
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Момент, когда инспектор попросил остановить прогон (Г.114). Отдельно
    # от статуса: пока исполнитель не дошёл до ближайшей безопасной точки,
    # прогон ещё идёт, и врать «остановлен» раньше времени нельзя. После
    # перезапуска сервера эта отметка не даёт продолжить то, что просили
    # прекратить.
    cancelled_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)



class PdRun(Base):
    """Прогон СТАДИИ 1 — самостоятельного разбора проектной документации
    (Г.86), запущенный из интерфейса, а не из командной строки.

    Г.94: до этого стадия 1 жила только в CLI, и инспектор через интерфейс
    получить сводку не мог вообще — сервер умел лишь сравнение ПД↔РД, то
    есть форму, которая по Г.86 больше не является главной и требует РД,
    которой на половине объектов нет.

    Отдельная таблица от `TriangulatedRun` по той же причине, по которой та
    отделена от `AnalysisRun`: это другой движок с другим контрактом, и
    смешивать их прогоны в одной записи значит потом гадать, каким путём
    посчитана строка.

    `store_run_id` — ссылка на запись в ХРАНИЛИЩЕ РАЗБОРОВ (`pd_store.py`,
    отдельная база, Г.87). Здесь хранится состояние прогона для интерфейса,
    там — сами требования: для передачи во вторую стадию и как датасет.
    Дублировать требования в этой базе значило бы завести второй источник
    истины о том же."""
    __tablename__ = "pd_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    # Ход работы для полосы прогресса (Г.112). Единица — пачка страниц,
    # потому что именно она стоит один вызов модели и из пачек складывается
    # время. Отдельно — момент начала: без него нельзя посчитать скорость,
    # а без скорости любая оценка остатка была бы выдумкой.
    stage: Mapped[str] = mapped_column(String, default="")
    units_total: Mapped[int] = mapped_column(Integer, default=0)
    units_done: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    status: Mapped[str] = mapped_column(String, default="running")  # running|done|error
    document_ids: Mapped[list] = mapped_column(JSON, default=list)
    # Г.95 — сторона комплекта: 'before' (ПД) | 'after' (РД/ИД). Механизм
    # разбора один и тот же, поэтому не заводится вторая таблица: разница
    # только в том, что разбор ПД сохраняется в хранилище для стадии сверки,
    # а разбор РД — нет (сверке нужен ТЕКСТ рабочей документации, а не
    # извлечённые из неё требования).
    side: Mapped[str] = mapped_column(String, default="before")
    provider: Mapped[str] = mapped_column(String, default="")
    extractor: Mapped[str] = mapped_column(String, default="")  # llm|regex
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    requirements_total: Mapped[int] = mapped_column(Integer, default=0)
    store_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Г.95 — состав комплекта: сколько листов чертежей, сколько таблиц и
    # каких. Считается ВСЕГДА, а не только когда сводка пуста: у рабочей
    # документации текстового слоя почти нет по природе, и без состава
    # пустая сводка неотличима от сбоя (Г.8/Г.10).
    composition: Mapped[str] = mapped_column(Text, default="")
    # Момент, когда инспектор попросил остановить прогон (Г.114). Отдельно
    # от статуса: пока исполнитель не дошёл до ближайшей безопасной точки,
    # прогон ещё идёт, и врать «остановлен» раньше времени нельзя. После
    # перезапуска сервера эта отметка не даёт продолжить то, что просили
    # прекратить.
    cancelled_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)



class ComplianceRun(Base):
    """Прогон СТАДИИ 3 — сверки «выполнено ли в РД то, что требует ПД» (Г.96).

    Ссылается на СОХРАНЁННЫЙ разбор ПД (`pd_run_id`), а не извлекает
    требования заново: разбор тома — десятки вызовов модели, повторять их
    ради сверки бессмысленно (Г.87, ровно тот довод, ради которого
    хранилище разборов и заводилось).

    `report` — готовый текст для инспектора, `counts` — сводка по статусам
    для интерфейса. Ни один статус не является вердиктом о нарушении:
    отсутствие подтверждения в рабочей документации ожидаемо (Б.6, Г.96).
    """
    __tablename__ = "compliance_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    # Ход работы для полосы прогресса (Г.112). Единица — пачка страниц,
    # потому что именно она стоит один вызов модели и из пачек складывается
    # время. Отдельно — момент начала: без него нельзя посчитать скорость,
    # а без скорости любая оценка остатка была бы выдумкой.
    stage: Mapped[str] = mapped_column(String, default="")
    units_total: Mapped[int] = mapped_column(Integer, default=0)
    units_done: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    status: Mapped[str] = mapped_column(String, default="running")  # running|done|error
    pd_run_id: Mapped[int] = mapped_column(Integer, default=0)
    rd_document_ids: Mapped[list] = mapped_column(JSON, default=list)
    provider: Mapped[str] = mapped_column(String, default="")
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    report: Mapped[str] = mapped_column(Text, default="")
    counts: Mapped[dict] = mapped_column(JSON, default=dict)
    requirements_total: Mapped[int] = mapped_column(Integer, default=0)
    # Момент, когда инспектор попросил остановить прогон (Г.114). Отдельно
    # от статуса: пока исполнитель не дошёл до ближайшей безопасной точки,
    # прогон ещё идёт, и врать «остановлен» раньше времени нельзя. После
    # перезапуска сервера эта отметка не даёт продолжить то, что просили
    # прекратить.
    cancelled_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)



class ReviewMessage(Base):
    """Реплика инспектора в разборе результата сверки и ответ модели (Г.100).

    Замечание и обычный вопрос лежат в одной таблице, но различаются полем
    `kind`: пусто у вопроса, род ошибки у замечания. Слить их в одно было бы
    удобнее в коде и неверно по существу — в датасет идут только замечания,
    а отделить их от переписки задним числом нельзя (см. докстринг
    `review_dialog`).

    `approved` — разрешение человека использовать замечание примером в
    промпте. По умолчанию False и автоматически не выставляется никогда:
    Г.11 требует, чтобы правило заводилось по осознанному решению, а не по
    факту накопления.

    `documents` — имена документов, на которых замечание получено. Нужны не
    для справки, а для гейта Г.12: на этих же документах пример в промпт не
    подставляется, иначе получается подсказка ответа самому себе.
    """
    __tablename__ = "review_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    compliance_run_id: Mapped[int] = mapped_column(Integer, default=0)
    role: Mapped[str] = mapped_column(String, default="inspector")  # inspector|assistant
    text: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String, default="")  # род ошибки, пусто у вопроса
    target: Mapped[str] = mapped_column(Text, default="")  # к какому требованию
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    documents: Mapped[list] = mapped_column(JSON, default=list)
    # Почему ответа модели нет: «связи не было» и «ответила пусто» требуют от
    # инспектора разного, а сведённые в «ответа нет» неотличимы (Г.10).
    no_answer_reason: Mapped[str] = mapped_column(String, default="")


class Settings(Base):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    # Г.94 — инструмент делается под GigaChat: провайдер по умолчанию он,
    # а не тот, что оставался от разработки. Инспектор ничего не выбирает.
    provider: Mapped[str] = mapped_column(String, default="gigachat")
    base_url: Mapped[str] = mapped_column(String, default="")
    model: Mapped[str] = mapped_column(String, default="")
    api_key: Mapped[str] = mapped_column(String, default="")
    # Сроки и лимиты хранения (Б.4/Б.5). Значения по умолчанию — бюджеты, а
    # не пороги истины: их меняет администратор под свой стенд, и от них не
    # зависит правильность разбора, только сколько места и времени он берёт.
    # Единица — килобайт, а не мегабайт: интерфейс показывает мегабайты,
    # но в килобайтах предел можно задать и для маленького стенда, и
    # проверить тестом, не собирая гигабайтный файл ради проверки лимита.
    retention_days: Mapped[int] = mapped_column(Integer, default=90)
    max_upload_kb: Mapped[int] = mapped_column(Integer, default=512 * 1024)
    max_pages: Mapped[int] = mapped_column(Integer, default=5000)
    part_kb: Mapped[int] = mapped_column(Integer, default=32 * 1024)
