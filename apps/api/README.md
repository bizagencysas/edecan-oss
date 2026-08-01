# apps/api — `edecan_api`

API HTTP en **FastAPI**: autenticación (JWT + TOTP opcional), persona, conversaciones con streaming **SSE**, memoria, conectores (OAuth), archivos, recordatorios/contactos/finanzas, voz web, pairing del companion, uso y facturación (Stripe).

Rutas versionadas bajo `/v1/*` — lista completa y pinned en `ARCHITECTURE.md` §10.12.

Si el paquete `edecan_premium` está instalado (`importlib.util.find_spec("edecan_premium")`), esta app monta también `edecan_premium.twilio_router.router` (webhooks de telefonía). El núcleo funciona completo sin ese paquete.

Cada request abre una transacción con `SET LOCAL ROLE app_user` + `SET LOCAL app.tenant_id` para aislamiento multi-tenant vía Row-Level Security (`ARCHITECTURE.md` §2 y §10.3). Además: pairing/WS del companion de escritorio (`edecan_api.companion_manager.ConnectionManager`), `GET /v1/usage` (uso del mes vs. límites del plan) y `GET /v1/admin/*` (solo superadmin).

Rate-limit simple por tenant (60 solicitudes/min, Redis `INCR`+`EXPIRE`) como dependency (`edecan_api.deps.rate_limit`) en todas las rutas autenticadas.

Correr localmente: `make api` (uvicorn en `:8000`, ver `ARCHITECTURE.md` §8).

## Tests

```
cd apps/api
uv sync   # o: python -m venv .venv && .venv/bin/pip install -e . y las libs de dev
uv run pytest
```

Los tests (`apps/api/tests/`) usan `httpx.AsyncClient` contra la app en memoria
(sin Postgres/Redis reales) con `app.dependency_overrides` sobre `FakeRepo`/
`FakeRedis` (`tests/api_fakes.py`) y, para el turno del agente, `Agent` reemplazado
vía `monkeypatch` en `edecan_api.routers.conversations` — nunca se llama a un
LLM real. `tests/_stub_siblings.py` agrega el código fuente de los paquetes
hermanos (`packages/schemas`, `db`, `llm`, `connectors`, `voice`, `core`,
`toolkit`) a `sys.path` para que esta suite sea corrible de forma aislada,
sin depender de un `uv sync` completo del workspace.
