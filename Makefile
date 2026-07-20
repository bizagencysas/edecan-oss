SHELL := /bin/bash
.PHONY: deps down sync api worker worker-scheduler web db-migrate test lint fmt docs-check audit check web-check desktop-test selfhost-smoke check-all

# NOTA (bug conocido de uv, ver README.md / CONTRIBUTING.md): el pyproject.toml
# raíz declara el workspace pero NO tiene "dependencies" propias (es un
# contenedor puro, ARCHITECTURE.md §10.1/§12.h) — por eso "uv sync"/"uv run"
# SIN --all-packages (ni --package <x>) sólo instalan ese cierre vacío y
# PODAN (desinstalan) los ~93 paquetes editables del workspace en silencio.
# TODOS los targets de abajo que invocan "uv run"/"uv sync" pasan
# --all-packages explícitamente por eso — no lo quites aunque parezca
# redundante, y si agregas un target nuevo que use uv, replica el flag.
deps:
	docker compose up -d

down:
	docker compose down

# --- Apps (ver ARCHITECTURE.md §8) -----------------------------------------

api:
	uv run --all-packages uvicorn edecan_api.main:app --reload --port 8000

worker:
	uv run --all-packages python -m edecan_worker.main

# Solo dev/self-host: encola send_reminder_scan cada 30s (en prod lo hace
# EventBridge Scheduler, ver ARCHITECTURE.md §7). Correr junto con `make worker`.
worker-scheduler:
	uv run --all-packages python -m edecan_worker.scheduler

web:
	cd apps/web && npm run dev

db-migrate:
	uv run --all-packages alembic -c packages/db/alembic.ini upgrade head

# --- Calidad -----------------------------------------------------------------

sync:
	uv sync --all-packages --frozen

test:
	uv run --all-packages pytest

lint:
	uv run --all-packages ruff check .

fmt:
	uv run --all-packages ruff format .

docs-check:
	uv run --all-packages python scripts/check_markdown_links.py

# Consulta la base de advisories actual; a diferencia de `check`, requiere red.
audit:
	uv export --locked --all-packages --format requirements-txt --no-emit-workspace --no-hashes | \
		uvx --from pip-audit==2.10.1 pip-audit -r /dev/stdin --progress-spinner off

# Baseline rápido y determinista del núcleo Python. Es el comando que deben
# ejecutar contribuidores antes de abrir un PR.
check: lint docs-check test

web-check:
	cd apps/web && npm ci
	cd apps/web && npm audit --audit-level=high
	cd apps/web && npm run lint
	cd apps/web && npm run typecheck
	cd apps/web && npm run build

# El sidecar Python real se construye durante el empaquetado. Para compilar y
# probar el crate aislado, Tauri recibe un override que no exige ese binario.
desktop-test:
	cd apps/desktop/src-tauri && \
		TAURI_CONFIG='{"bundle":{"externalBin":[]}}' cargo test --locked

selfhost-smoke:
	./scripts/smoke_selfhost.sh

check-all: check web-check desktop-test
