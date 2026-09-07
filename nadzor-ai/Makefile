.PHONY: help install backend web up down test lint security clean

help:            ## Список команд
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/' | column -t -s "$$(printf '\t')"

install:         ## Установить зависимости для локальной разработки
	python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
	cd frontend && npm install

backend:         ## Запустить движок разбора документов
	cd packages/backend && PYTHONPATH=$(PWD)/packages/backend ../../.venv/bin/python -m uvicorn app.main:app --reload --port 8010

web:             ## Запустить интерфейс
	cd frontend && npm run dev

up:              ## Поднять всё одной командой
	docker compose up --build

down:            ## Остановить и удалить контейнеры
	docker compose down -v

test:            ## Прогнать тесты с покрытием
	PYTHONPATH=.:packages/backend .venv/bin/python -m pytest packages/backend/tests -q

lint:            ## Стиль и границы модулей
	.venv/bin/ruff check packages scripts

security:        ## Локальный прогон проверок безопасности
	.venv/bin/bandit -q -r packages -c pyproject.toml
	.venv/bin/pip-audit -r requirements.txt || true

clean:           ## Убрать сгенерированное
	rm -rf data/nadzor.db .pytest_cache htmlcov
