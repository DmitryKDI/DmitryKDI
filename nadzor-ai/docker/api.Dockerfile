# Базовый образ фиксируется по дайджесту в продуктивной сборке.
# Целевая операционная система — Astra Linux SE; здесь используется
# совместимый образ для демонстрационного контура.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/packages:/app

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-dejavu-core libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY packages ./packages
COPY scripts ./scripts
COPY config ./config
COPY data/norms ./data/norms
COPY db ./db

# Приложение работает от непривилегированного пользователя.
RUN useradd --create-home --uid 10001 nadzor && chown -R nadzor:nadzor /app
USER nadzor

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --retries=10 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')"

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
