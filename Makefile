SHELL := /bin/bash
.PHONY: deps down sync api worker worker-scheduler web db-migrate test test-external lint fmt docs-check audit check web-check desktop-test selfhost-smoke check-all doctor eval eval-live phase3-coverage phase3-harness-map phase3-p0 phase3-final-reviews phase3-release-gate phase3-test phase3-mobile-test phase3-mobile-device phase3-search-longitudinal phase3-benchmark production-validation

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
	EDECAN_RUN_REAL_EXTERNAL_TESTS=0 uv run --all-packages pytest

test-external:
	@test "$${EDECAN_RUN_REAL_EXTERNAL_TESTS:-}" = "1" || { echo "Para pruebas externas usa EDECAN_RUN_REAL_EXTERNAL_TESTS=1 make test-external" >&2; exit 2; }
	uv run --all-packages pytest

lint:
	uv run --all-packages ruff check .

fmt:
	uv run --all-packages ruff format .

docs-check:
	uv run --all-packages python scripts/check_markdown_links.py

# Consulta la base de advisories actual; a diferencia de `check`, requiere red.
# NOTA portabilidad: "/dev/stdin" no es un path real en Windows -- uvx invoca un
# binario nativo (uv.exe) que no pasa por la traduccion de rutas de Git Bash, y
# el open() de pip-audit fallaria. Usamos un archivo temporal via mktemp (viene
# con el coreutils de Git for Windows, asi que corre igual en macOS/Linux/Windows
# bajo Git Bash) y lo limpiamos siempre con trap, incluso si pip-audit falla.
audit:
	tmp_req="$$(mktemp)"; \
	trap 'rm -f "$$tmp_req"' EXIT; \
	uv export --locked --all-packages --format requirements-txt --no-emit-workspace --no-hashes > "$$tmp_req" && \
		uvx --from pip-audit==2.10.1 pip-audit -r "$$tmp_req" --progress-spinner off

# Baseline rápido y determinista del núcleo Python. Es el comando que deben
# ejecutar contribuidores antes de abrir un PR.
check: lint docs-check test

web-check:
	cd apps/web && npm ci
	cd apps/web && npm audit --audit-level=high
	cd apps/web && npm run lint
	cd apps/web && npm run typecheck
	cd apps/web && npm test
	cd apps/web && npm run test:config
	cd apps/web && npm run build

# El sidecar Python real se construye durante el empaquetado. Para compilar y
# probar el crate aislado, Tauri recibe un override que no exige ese binario.
desktop-test:
	cd apps/desktop/src-tauri && \
		TAURI_CONFIG='{"bundle":{"externalBin":[]}}' cargo test --locked

selfhost-smoke:
	./scripts/smoke_selfhost.sh

check-all: check web-check desktop-test

# --- PHASE 3 ---------------------------------------------------------------

# Comprobación local, no destructiva: imports críticos, archivos de contrato y
# head de Alembic. No demuestra que producción esté desplegada.
doctor:
	uv run --all-packages python scripts/phase3_doctor.py

# Evals deterministas del core y suites golden del agente. El segundo comando
# usa fakes offline: no llama proveedores ni ejecuta herramientas reales.
eval:
	uv run --all-packages pytest packages/core/tests/test_evals_suite.py packages/core/tests/test_session.py packages/voice/tests/test_realtime.py packages/agents/tests/test_tools.py -q
	uv run --all-packages python -m edecan_evals.run --suite todas

# Evidencia contra el proveedor LLM real, solo por decisión explícita del
# operador. Consume tokens; las tools siguen siendo dobles sin efectos.
eval-live:
	@test "$${EDECAN_RUN_REAL_EXTERNAL_TESTS:-}" = "1" || { echo "Usa EDECAN_RUN_REAL_EXTERNAL_TESTS=1 make eval-live" >&2; exit 2; }
	uv run --all-packages python -m edecan_evals.run --suite todas --live

# Gate live explícito, sin credenciales ni escrituras: varias rondas acotadas
# contra DuckDuckGo para comprobar que la evidencia de frescura no es una sola
# respuesta aislada. No forma parte de check-all por depender de internet.
phase3-search-longitudinal:
	uv run --all-packages python scripts/phase3_search_longitudinal.py \
		--query "$${EDECAN_SEARCH_LONGITUDINAL_QUERY:-version latest de la librería FastAPI}" \
		--rounds "$${EDECAN_SEARCH_LONGITUDINAL_ROUNDS:-3}" \
		--official-package "$${EDECAN_SEARCH_OFFICIAL_PACKAGE:-fastapi}"

phase3-benchmark:
	uv run --all-packages python scripts/phase3_benchmark.py \
		--iterations "$${EDECAN_PHASE3_BENCHMARK_ITERATIONS:-2}" \
		--concurrency "$${EDECAN_PHASE3_BENCHMARK_CONCURRENCY:-8}"

# Gate final deliberadamente estricto: repite suite PHASE3, benchmark, evals y
# builds web/desktop/iOS Simulator; solo pasa después con todas las filas
# completas, los 13 escenarios P0 y las siete revisiones sin hallazgos.
# Prueba enfocada de las olas implementadas en PHASE3.
phase3-mobile-test:
	uv run --all-packages pytest apps/mobile/ios/tests/test_share_extension_contract.py -q
	swift test --package-path apps/mobile/ios/EdecanKit --filter RealtimeVoiceClientTests
	swift test --package-path apps/mobile/ios/EdecanKit --filter APISessionRaceTests
	swift test --package-path apps/mobile/ios/EdecanKit --filter SharePayloadStoreTests
	swift test --package-path apps/mobile/ios/EdecanKit --filter WidgetSnapshotStoreTests
	DEVELOPER_DIR=/Applications/Xcode-beta.app/Contents/Developer xcodebuild -project apps/mobile/ios/Edecan.xcodeproj -scheme EdecanApp -sdk iphonesimulator -configuration Debug build CODE_SIGNING_ALLOWED=NO

phase3-mobile-device:
	cd apps/mobile/ios && bash scripts/install_device.sh

# Solo valida que el entorno tenga variables mínimas; no despliega ni toca
# infraestructura. Las pruebas de conectividad/backup siguen siendo un gate
# externo y deben ejecutarse con el runbook del operador.
production-validation:
	uv run --all-packages python scripts/phase3_doctor.py --production
