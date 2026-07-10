"""`/v1/hooks/{automation_id}` (`edecan_api.routers.hooks`, ROADMAP_V2.md
§7.6, dueño WP-V2-07) — endpoint PÚBLICO (sin `Authorization: Bearer`).

Mismo motivo que `test_automations_router.py` para la fixture `app` local
(este router tampoco lo monta todavía `create_app()`) y para `_FakeSession`
(duplicada a propósito — `ARCHITECTURE.md` §10.1: los tests no importan
paquetes hermanos, y aquí ni siquiera se importa el `_FakeSession` de
`test_automations_router.py`, un archivo hermano dentro del mismo paquete,
para que este módulo se pueda leer/ejecutar de forma aislada)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest

import edecan_api.deps as edecan_deps
import edecan_api.routers.hooks as hooks_module
from edecan_api.main import create_app


class _FakeResult:
    def __init__(self, rows=None) -> None:
        self._rows = rows if rows is not None else []

    def mappings(self) -> _FakeResult:
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """Doble mínimo de `AsyncSession`. `commits` cuenta llamadas a `commit()`
    (mismo patrón que `test_remote_router.py::_FakeDbSession`/
    `test_voz_avanzada.py::FakeSession`) — necesario para verificar el commit
    explícito de evidencia de `trigger_hook` antes del `enqueue` (ver
    docstring de `hooks.py`, sección "commit de evidencia antes del
    `enqueue`")."""

    def __init__(self, respuestas=None) -> None:
        self.respuestas = list(respuestas or [])
        self.executed: list[tuple[str, dict]] = []
        self.commits = 0

    async def execute(self, clause, params=None):
        self.executed.append((str(clause), dict(params or {})))
        if not self.respuestas:
            return _FakeResult()
        siguiente = self.respuestas.pop(0)
        return siguiente if isinstance(siguiente, _FakeResult) else _FakeResult(rows=siguiente)

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        self.commits += 1


@pytest.fixture
def app(fake_repo, fake_redis, test_settings):
    application = create_app()
    application.include_router(hooks_module.router)

    application.dependency_overrides[edecan_deps.get_settings] = lambda: test_settings
    application.dependency_overrides[edecan_deps.get_platform_repo] = lambda: fake_repo
    application.dependency_overrides[edecan_deps.get_repo] = lambda: fake_repo
    application.dependency_overrides[edecan_deps.get_redis] = lambda: fake_redis
    application.dependency_overrides[edecan_deps.get_tenant_session] = lambda: None
    application.dependency_overrides[edecan_deps.get_vault] = lambda: None
    application.dependency_overrides[edecan_deps.get_llm_router] = lambda: None

    yield application
    application.dependency_overrides.clear()


@pytest.fixture
def fake_session() -> _FakeSession:
    return _FakeSession()


@pytest.fixture(autouse=True)
def _wire_fake_platform_session(app, fake_session: _FakeSession):
    """`hooks.py` usa `get_platform_session` (rol dueño, sin `tenant_id` — es
    un endpoint público, no hay JWT del que sacar uno), a diferencia de
    `get_tenant_session` que usan la mayoría de los demás routers."""
    app.dependency_overrides[edecan_deps.get_platform_session] = lambda: fake_session


def _row(**overrides) -> dict:
    base = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "nombre": "Reporte diario",
        "trigger": json.dumps({"kind": "webhook", "hook_secret": "el-secreto-correcto"}),
        "accion": json.dumps({"kind": "agent_instruction", "instruccion": "Manda el reporte."}),
        "enabled": True,
        "next_run_at": None,
        "last_run_at": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    base.update(overrides)
    return base


def _mock_enqueue(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    llamadas: list[tuple] = []

    async def fake_enqueue(settings, job_type, payload, tenant_id):
        llamadas.append((job_type, payload, tenant_id))
        return uuid.uuid4()

    monkeypatch.setattr(hooks_module, "enqueue", fake_enqueue)
    return llamadas


# ---------------------------------------------------------------------------
# Rechazo (siempre 404, ver docstring de hooks.py)
# ---------------------------------------------------------------------------


async def test_hook_automation_not_found(client, fake_session: _FakeSession) -> None:
    fake_session.respuestas = [[]]

    response = await client.post(
        f"/v1/hooks/{uuid.uuid4()}", headers={"X-Hook-Secret": "cualquiera"}
    )

    assert response.status_code == 404


async def test_hook_wrong_secret(client, fake_session: _FakeSession) -> None:
    automation_id = uuid.uuid4()
    fake_session.respuestas = [[_row(id=automation_id)]]

    response = await client.post(
        f"/v1/hooks/{automation_id}", headers={"X-Hook-Secret": "secreto-incorrecto"}
    )

    assert response.status_code == 404
    assert len(fake_session.executed) == 1  # nunca llegó al audit_log


async def test_hook_missing_secret_header(client, fake_session: _FakeSession) -> None:
    automation_id = uuid.uuid4()
    fake_session.respuestas = [[_row(id=automation_id)]]

    response = await client.post(f"/v1/hooks/{automation_id}")

    assert response.status_code == 404


async def test_hook_trigger_not_webhook_kind(client, fake_session: _FakeSession) -> None:
    automation_id = uuid.uuid4()
    fake_session.respuestas = [
        [_row(id=automation_id, trigger=json.dumps({"kind": "schedule", "rrule": "FREQ=DAILY"}))]
    ]

    response = await client.post(
        f"/v1/hooks/{automation_id}", headers={"X-Hook-Secret": "lo-que-sea"}
    )

    assert response.status_code == 404


async def test_hook_disabled_automation(client, fake_session: _FakeSession) -> None:
    automation_id = uuid.uuid4()
    fake_session.respuestas = [[_row(id=automation_id, enabled=False)]]

    response = await client.post(
        f"/v1/hooks/{automation_id}", headers={"X-Hook-Secret": "el-secreto-correcto"}
    )

    assert response.status_code == 404


async def test_rejection_responses_are_indistinguishable(
    client, fake_session: _FakeSession
) -> None:
    """Not-found, secreto malo, kind incorrecto y desactivada devuelven
    EXACTAMENTE el mismo status/cuerpo — ninguno debe filtrar más que otro."""
    automation_id = uuid.uuid4()

    fake_session.respuestas = [[]]
    not_found = await client.post(f"/v1/hooks/{automation_id}", headers={"X-Hook-Secret": "x"})

    fake_session.respuestas = [[_row(id=automation_id)]]
    wrong_secret = await client.post(
        f"/v1/hooks/{automation_id}", headers={"X-Hook-Secret": "incorrecto"}
    )

    assert not_found.status_code == wrong_secret.status_code == 404
    assert not_found.text == wrong_secret.text


# ---------------------------------------------------------------------------
# Éxito
# ---------------------------------------------------------------------------


async def test_hook_success_enqueues_and_audits(
    client, fake_session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = uuid.uuid4()
    automation_id = uuid.uuid4()
    fake_session.respuestas = [[_row(id=automation_id, tenant_id=tenant_id)]]
    llamadas = _mock_enqueue(monkeypatch)

    response = await client.post(
        f"/v1/hooks/{automation_id}", headers={"X-Hook-Secret": "el-secreto-correcto"}
    )

    assert response.status_code == 204
    assert llamadas == [("run_automation", {"automation_id": str(automation_id)}, tenant_id)]

    statements = [sql for sql, _ in fake_session.executed]
    assert any("INSERT INTO audit_log" in sql for sql in statements)
    audit_params = next(p for sql, p in fake_session.executed if "INSERT INTO audit_log" in sql)
    assert audit_params["tenant_id"] == tenant_id
    assert fake_session.commits == 1


async def test_hook_enqueue_falla_no_pierde_la_evidencia_de_audit_log(
    client, fake_session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regresión directa del hallazgo: si `enqueue` lanza DESPUÉS de que
    `_audit_hook_triggered` ya insertó la fila de `audit_log`, esa fila debe
    haberse comiteado ANTES del fallo — de lo contrario el rollback
    automático de `get_platform_session` (`edecan_db.session.get_session`,
    ROLLBACK ante cualquier excepción que se propague fuera del handler) se
    la lleva puesta, perdiendo la única evidencia de que un tercero
    autenticado disparó este webhook. Ver docstring de `hooks.py`, sección
    "commit de evidencia antes del `enqueue`"."""
    tenant_id = uuid.uuid4()
    automation_id = uuid.uuid4()
    fake_session.respuestas = [[_row(id=automation_id, tenant_id=tenant_id)]]

    async def fake_enqueue_falla(settings, job_type, payload, tenant_id):
        raise RuntimeError("SQS_QUEUE_URL no está configurado")

    monkeypatch.setattr(hooks_module, "enqueue", fake_enqueue_falla)

    with pytest.raises(RuntimeError):
        await client.post(
            f"/v1/hooks/{automation_id}", headers={"X-Hook-Secret": "el-secreto-correcto"}
        )

    statements = [sql for sql, _ in fake_session.executed]
    assert any("INSERT INTO audit_log" in sql for sql in statements)
    # La aserción que de verdad importa: el commit ocurrió ANTES de que
    # `enqueue` lanzara, no depende de si la sesión real hace rollback
    # después (este doble no lo modela) — es la garantía que el fix agrega.
    assert fake_session.commits == 1


# ---------------------------------------------------------------------------
# Rate limit (30/min por automatización)
# ---------------------------------------------------------------------------


async def test_hook_rate_limited_after_30_per_minute(
    client, fake_session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    automation_id = uuid.uuid4()
    row = _row(id=automation_id)
    # Cada llamada exitosa consume 2 respuestas (SELECT + INSERT audit_log);
    # la que termine bloqueada por rate limit solo consume la primera (nunca
    # llega al audit_log) — se sobra a propósito en vez de contar exacto.
    fake_session.respuestas = [[row], []] * 35
    _mock_enqueue(monkeypatch)
    headers = {"X-Hook-Secret": "el-secreto-correcto"}

    statuses = []
    for _ in range(31):
        response = await client.post(f"/v1/hooks/{automation_id}", headers=headers)
        statuses.append(response.status_code)

    assert statuses[:30] == [204] * 30
    assert statuses[30] == 429
