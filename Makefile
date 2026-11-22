PY ?= .venv/bin/python
UV ?= uv

.PHONY: install dev worker test lint fmt migrate seed hash-password import-sales check-stock import-assortment

install:
	$(UV) venv --python 3.12 .venv
	$(UV) pip install --python .venv/bin/python -e ".[dev]"

dev: migrate
	$(PY) -m uvicorn ducan.main:create_app --factory --reload --host 127.0.0.1 --port 8000

worker:
	$(PY) -m ducan.jobs.scheduler

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check src tests migrations
	$(PY) -m ruff format --check src tests migrations

fmt:
	$(PY) -m ruff format src tests migrations
	$(PY) -m ruff check --fix src tests migrations

migrate:
	$(PY) -m alembic upgrade head

seed: migrate
	$(PY) -m ducan.jobs.cli seed

hash-password:
	$(PY) -m ducan.auth.hashpw

import-sales:
	$(PY) -m ducan.jobs.cli import-sales

check-stock:
	$(PY) -m ducan.jobs.cli check-stock

# Usage: make import-assortment XLSX=path/to/P4_ducan_model.xlsx
import-assortment:
	$(PY) -m ducan.jobs.cli import-assortment "$(XLSX)"
