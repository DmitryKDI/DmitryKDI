# Базовый образ закреплён выпуском ОС: пересборка не должна молча менять
# версию Tesseract и путь к его словарям.
# Целевая операционная система — Astra Linux SE; здесь используется
# совместимый образ для демонстрационного контура.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/packages/backend:/app \
    TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata

WORKDIR /app

# Tesseract — локальное распознавание страниц без текстового слоя: внешние
# OCR-сервисы в закрытом контуре недопустимы. Русский словарь обязателен:
# без него кириллица «распознаётся» латиницей, что хуже честного отказа.
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-dejavu-core libgl1 libglib2.0-0 \
        tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/* \
    && test -f "$TESSDATA_PREFIX/rus.traineddata" \
    && test -f "$TESSDATA_PREFIX/eng.traineddata"

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY packages/backend ./packages/backend
COPY scripts ./scripts
COPY data/known_violations.json ./data/known_violations.json
COPY data/parameter_catalog_v1_1.json ./data/parameter_catalog_v1_1.json

# Сборка падает, если распознавание в образе не настроено: иначе стенд
# поднялся бы и отвечал «движок распознавания не установлен» на каждом скане.
RUN python -c "from app.local_ocr import load_config; assert load_config() is not None, 'Tesseract rus+eng не настроен'"

# Изменяемые данные — базы прогонов, оригиналы документов, разборы, журналы —
# в отдельном каталоге, принадлежащем пользователю приложения. Справочные
# файлы (матрица параметров) остаются в образе: при обновлении образа они
# меняются вместе с кодом, а не застревают в томе с данными.
ENV NADZOR_DB_PATH=/app/state/nadzor.db \
    FACTS_STORE_DB=/app/state/facts_store.db \
    FILE_STORE_DB=/app/state/file_store.db \
    FILE_STORE_CACHE=/app/state/uploads \
    NADZOR_PD_STORE=/app/state/pd_store.db \
    SECTION_PROFILE_DB=/app/state/section_profiles.db \
    NADZOR_INSPECTOR_MEMORY=/app/state/inspector_memory.sqlite3 \
    NADZOR_LLM_CACHE_DB=/app/state/llm_result_cache.sqlite3 \
    NADZOR_RUN_LOGS_DIR=/app/state/run_logs

# Приложение работает от непривилегированного пользователя.
RUN useradd --create-home --uid 10001 nadzor && mkdir -p /app/state \
    && chown -R nadzor:nadzor /app
USER nadzor

EXPOSE 8010
HEALTHCHECK --interval=15s --timeout=5s --retries=10 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8010/health')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8010"]
