.PHONY: help install data api web up down test eval lint security clean

help:            ## Список команд
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/' | column -t -s "$$(printf '\t')"

install:         ## Установить зависимости для локальной разработки
	python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
	cd frontend && npm install

data:            ## Сформировать демонстрационный комплект документации
	PYTHONPATH=. .venv/bin/python scripts/gen_dataset.py

api:             ## Запустить сервер приложения
	PYTHONPATH=packages APP_ROOT=$(PWD) .venv/bin/python -m uvicorn api.main:app --reload --port 8000

web:             ## Запустить интерфейс
	cd frontend && npm run dev

up:              ## Поднять всё одной командой
	docker compose up --build

down:            ## Остановить и удалить контейнеры
	docker compose down -v

test:            ## Прогнать тесты с покрытием
	.venv/bin/python -m pytest tests -q --cov=packages --cov-report=term

eval:            ## Метрики детектора по эталону
	PYTHONPATH=. .venv/bin/python scripts/evaluate.py

lint:            ## Стиль и границы модулей
	.venv/bin/ruff check packages scripts tests
	.venv/bin/lint-imports --config .importlinter

security:        ## Локальный прогон проверок безопасности
	.venv/bin/bandit -q -r packages -c pyproject.toml
	.venv/bin/pip-audit -r requirements.txt || true

clean:           ## Убрать сгенерированное
	rm -rf data/demo/generated data/nadzor.db .pytest_cache htmlcov
