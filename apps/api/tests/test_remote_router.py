"""`edecan_api.routers.remote` — control remoto de pantalla (WP-V2-09) + input
de teclado/mouse (WP-V4-10, "fase 2"; ver el docstring del propio router para
el diseño completo y qué es real vs. temporal).

`edecan_api.main.create_app()` YA monta `remote.router` (está en
`main.V2_ROUTER_NAMES`) — aun así, igual que el resto de `test_*.py` de esta
carpeta que necesitan un doble de companion, el fixture `_mounted_app`/
`client` de aquí abajo monta el router de nuevo sobre la `app` de
`conftest.py` (inofensivo — `include_router` dos veces solo duplica rutas
idénticas) para poder reemplazar `companion_manager` por el doble de este
archivo antes de construir el cliente HTTP.

Modelo de precio de pago único (ver el docstring de `edecan_schemas.plans`):
`companion.remote_view`/`companion.remote_input` ya están en `True` en las 4
entradas de `PLANES` por igual — no hay más "plan sin flag X" que probar con
planes reales. Los tests de abajo usan `auth_headers(plan_key=...)` con
cualquier `plan_key` válido de `PLANES` (ver `PLAN_WITH_REMOTE_VIEW`/
`PLAN_WITH_REMOTE_CONTROL`). El único gate que sigue siendo real es
"remote_view=True, remote_input=False" para `kind="control"` — esa
combinación no existe en ningún plan real de `PLANES` hoy, así que ESE test
sobreescribe `get_current_user` a mano con una `TenantCtx` sintética (ver
`test_create_session_kind_control_requires_remote_input_flag`).

`_FakeCompanionManager` es un doble mínimo de
`edecan_api.companion_manager.ConnectionManager` (mismo espíritu que
`FakeRepo`/`FakeRedis` en `api_fakes.py`, pero específico de este router:
no vive ahí porque ningún otro test lo necesita) — permite guionar por
adelantado la respuesta de `send_command` sin abrir WebSockets reales.

`_FakeRepoWithKind` (WP-V4-10) extiende `FakeRepo` (`api_fakes.py`, SIN
tocar — prohibido para este WP) con `mark_remote_session_kind`, la única
pieza que `Repo` necesita para sesiones `kind="control"` (ver el comentario
en `edecan_api.repo.Repo` junto al método real). La fixture `fake_repo` de
aquí abajo SOMBREA a propósito la de `conftest.py` (mismo nombre, prioridad
de pytest al fixture más específico) para que TODOS los tests de este
archivo — nuevos y viejos — reciban esta subclase; como no sobreescribe
ningún método heredado, los tests view-only preexistentes siguen pasando
exactamente igual que antes de este WP.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from api_fakes import FakeRepo
from conftest import auth_headers
from httpx import ASGITransport, AsyncClient

from edecan_api import deps as edecan_deps
from edecan_api.companion_manager import CompanionError
from edecan_api.routers import remote

Row = dict[str, Any]


class _FakeDbSession:
    """Doble mínimo de `AsyncSession`: solo cuenta llamadas a `commit()`. `get_frame` la usa
    ÚNICAMENTE para el commit explícito antes de lanzar el 403 de denegación
    (`HOTFIXES_PENDIENTES.md` punto 8) — ningún otro endpoint de este router toca la sesión
    directamente (todo lo demás pasa por `repo`, ver el docstring del router). `conftest.py`
    deja `get_tenant_session` en `lambda: None` por defecto, que no sirve para ese caso
    puntual — de ahí este doble local (mismo criterio que pide el paquete de trabajo: extender
    mínimamente DENTRO de este archivo, sin tocar `conftest.py`/`api_fakes.py` compartidos)."""

    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class _FakeCompanionManager:
    """Doble de `ConnectionManager`: `is_connected` se controla con
    `.connected`, y `send_command` devuelve (o lanza) lo siguiente que se le
    haya cargado en `.responses` (lista usada como cola FIFO)."""

    def __init__(self) -> None:
        self.connected: set[uuid.UUID] = set()
        self.responses: list[dict | Exception] = []
        self.calls: list[tuple[uuid.UUID, str, dict]] = []

    def is_connected(self, tenant_id: uuid.UUID) -> bool:
        return tenant_id in self.connected

    async def send_command(
        self, tenant_id: uuid.UUID, action: str, params: dict, timeout: float = 30
    ) -> dict:
        self.calls.append((tenant_id, action, params))
        if not self.responses:
            raise AssertionError("_FakeCompanionManager: no hay respuesta guionada")
        outcome = self.responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FakeRepoWithKind(FakeRepo):
    """`FakeRepo` (api_fakes.py, SIN tocar) + `mark_remote_session_kind`
    (WP-V4-10) — ver el docstring del módulo para el porqué de esta subclase
    en vez de tocar el fake compartido."""

    async def mark_remote_session_kind(
        self, *, tenant_id: uuid.UUID, session_id: uuid.UUID, kind: str
    ) -> Row:
        row = self.remote_sessions.get(session_id)
        assert row is not None and row["tenant_id"] == tenant_id
        row["kind"] = kind
        row["updated_at"] = datetime.now(UTC)
        return dict(row)


CANNED_FRAME_OK = {"ok": True, "result": {"image_b64": "aGVsbG8=", "width": 1920, "height": 1080}}
UNSUPPORTED_ACTION_ERROR = {"ok": False, "error": "acción no soportada: 'screenshot'"}
# Mensaje real de `edecan_companion.actions.execute` cuando `screenshot` existe
# (WP-V2-08 ya aterrizó `_screenshot`) pero ese companion tiene `ide_enabled:
# false` en su `companion.yaml` (`screenshot` vive en `_IDE_ACTIONS`, así que
# hereda ese gate) — un motivo distinto de "no soportada" para el mismo 501.
IDE_DISABLED_ERROR = {
    "ok": False,
    "error": "el IDE está deshabilitado en este companion (ide_enabled=false en companion.yaml)",
}
# Mensaje real de `edecan_companion.actions._screenshot` cuando corre
# en un `sys.platform` distinto de `darwin` (`screenshot` SÍ existe en
# `ACTIONS` y ese companion SÍ tiene `ide_enabled: true` — la plataforma
# misma es la que nunca va a poder servir capturas) — un tercer motivo
# distinto de "no soportada"/"IDE deshabilitado" para el mismo 501.
PLATFORM_UNSUPPORTED_ERROR = {"ok": False, "error": "captura no soportada en esta plataforma"}
DENIED_ERROR = {"ok": False, "error": "acción rechazada (sin aprobación del usuario)"}

# WP-V4-10: mensajes reales de `edecan_companion.actions.execute`/
# `_get_input_backend` para `input_pointer`/`input_key` (ver
# `_translate_input_companion_error` en `remote.py`).
INPUT_UNSUPPORTED_ACTION_ERROR = {"ok": False, "error": "acción no soportada: 'input_pointer'"}
INPUT_DISABLED_ERROR = {
    "ok": False,
    "error": (
        "el control remoto de teclado/mouse está deshabilitado en este companion "
        "(remote_input_enabled=false en companion.yaml)"
    ),
}
INPUT_PLATFORM_UNSUPPORTED_ERROR = {
    "ok": False,
    "error": "el control remoto de teclado/mouse no está soportado en esta plataforma",
}
# Mensaje real de `_QuartzInputBackend.__init__` sin permiso de Accesibilidad
# -- NINGUNO de los tres prefijos de arriba lo reconoce a propósito: cae al
# 502 genérico (ver el docstring de `_translate_input_companion_error`).
INPUT_ACCESSIBILITY_DENIED_ERROR = {
    "ok": False,
    "error": (
        "este proceso no tiene el permiso de Accesibilidad concedido en macOS. Ve a "
        "Ajustes del Sistema → Privacidad y Seguridad → Accesibilidad ..."
    ),
}

# Todos los planes de PLANES traen companion.remote_view=True hoy -- hosted_pro
# se usa aquí como cualquier plan_key válido (ROADMAP_V2.md §7.2).
PLAN_WITH_REMOTE_VIEW = "hosted_pro"
# hosted_pro también trae companion.remote_input=True (WP-V4-10) -- alias
# para que los tests de control remoto no dependan silenciosamente de que
# sea "el mismo plan" que PLAN_WITH_REMOTE_VIEW.
PLAN_WITH_REMOTE_CONTROL = PLAN_WITH_REMOTE_VIEW


@pytest.fixture
def fake_manager() -> _FakeCompanionManager:
    return _FakeCompanionManager()


@pytest.fixture
def fake_db_session() -> _FakeDbSession:
    return _FakeDbSession()


@pytest.fixture
def fake_repo() -> _FakeRepoWithKind:
    """SOMBREA la fixture `fake_repo` de `conftest.py` (mismo nombre) para
    que `app` (que la pide como dependencia) reciba esta subclase — ver el
    docstring del módulo."""
    return _FakeRepoWithKind()


@pytest.fixture
def _mounted_app(app, fake_manager: _FakeCompanionManager, fake_db_session: _FakeDbSession):
    """`app` (de `conftest.py`) + `remote.router` montado + companion_manager
    reemplazado por el doble de este archivo + `get_tenant_session` reemplazado por
    `fake_db_session` (`get_frame` la necesita para el commit explícito de
    `HOTFIXES_PENDIENTES.md` punto 8; el resto de los endpoints de este router nunca tocan
    la sesión directamente, así que este override no cambia su comportamiento)."""
    app.include_router(remote.router)
    app.state.companion_manager = fake_manager
    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: fake_db_session
    return app


@pytest.fixture
async def client(_mounted_app) -> AsyncIterator[AsyncClient]:
    # Sombrea a propósito el fixture `client` de `conftest.py`: ese vive sobre
    # `app` sin el router de `remote` montado ni el companion_manager fake.
    transport = ASGITransport(app=_mounted_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _headers_with_remote_view() -> tuple[dict[str, str], uuid.UUID, uuid.UUID]:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key=PLAN_WITH_REMOTE_VIEW)
    return headers, tenant_id, user_id


# ---------------------------------------------------------------------------
# Autenticación / flag gate
# ---------------------------------------------------------------------------


async def test_create_session_requires_authentication(client) -> None:
    response = await client.post("/v1/remote/sessions", json={"consent": True})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Consentimiento explícito
# ---------------------------------------------------------------------------


async def test_create_session_requires_consent_true(client) -> None:
    headers, _, _ = _headers_with_remote_view()
    response = await client.post("/v1/remote/sessions", json={"consent": False}, headers=headers)
    assert response.status_code == 422


async def test_create_session_requires_consent_field(client) -> None:
    headers, _, _ = _headers_with_remote_view()
    response = await client.post("/v1/remote/sessions", json={}, headers=headers)
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Companion no conectado
# ---------------------------------------------------------------------------


async def test_create_session_503_without_companion(client) -> None:
    headers, _, _ = _headers_with_remote_view()
    # fake_manager.connected está vacío: ningún tenant tiene companion.
    response = await client.post("/v1/remote/sessions", json={"consent": True}, headers=headers)
    assert response.status_code == 503


async def test_frame_503_when_companion_disconnects_after_session_created(
    client, fake_manager: _FakeCompanionManager
) -> None:
    headers, tenant_id, _ = _headers_with_remote_view()
    fake_manager.connected.add(tenant_id)
    created = await client.post("/v1/remote/sessions", json={"consent": True}, headers=headers)
    assert created.status_code == 201
    session_id = created.json()["id"]

    fake_manager.connected.discard(tenant_id)  # se desconecta antes del primer frame
    response = await client.get(f"/v1/remote/sessions/{session_id}/frame", headers=headers)
    assert response.status_code == 503


# ---------------------------------------------------------------------------
# Creación exitosa + auditoría
# ---------------------------------------------------------------------------


async def test_create_session_success_is_pending_and_audits(
    client, fake_manager: _FakeCompanionManager, fake_repo
) -> None:
    headers, tenant_id, user_id = _headers_with_remote_view()
    fake_manager.connected.add(tenant_id)

    response = await client.post("/v1/remote/sessions", json={"consent": True}, headers=headers)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending"
    assert body["kind"] == "view"
    assert body["frames_count"] == 0
    assert body["started_at"] is None
    assert body["tenant_id"] == str(tenant_id)
    assert body["user_id"] == str(user_id)

    assert len(fake_repo.audit_log) == 1
    entry = fake_repo.audit_log[0]
    assert entry["action"] == "remote.session.requested"
    assert entry["target"] == body["id"]
    assert entry["tenant_id"] == tenant_id
    assert entry["actor_user_id"] == user_id


async def test_list_and_get_session_are_tenant_isolated(
    client, fake_manager: _FakeCompanionManager
) -> None:
    headers_a, tenant_a, _ = _headers_with_remote_view()
    fake_manager.connected.add(tenant_a)
    created = await client.post("/v1/remote/sessions", json={"consent": True}, headers=headers_a)
    session_id = created.json()["id"]

    listed = await client.get("/v1/remote/sessions", headers=headers_a)
    assert listed.status_code == 200
    assert [s["id"] for s in listed.json()] == [session_id]

    fetched = await client.get(f"/v1/remote/sessions/{session_id}", headers=headers_a)
    assert fetched.status_code == 200
    assert fetched.json()["id"] == session_id

    # Otro tenant (mismo plan, tenant_id distinto) no la ve.
    headers_b, tenant_b, _ = _headers_with_remote_view()
    assert tenant_b != tenant_a
    listed_b = await client.get("/v1/remote/sessions", headers=headers_b)
    assert listed_b.json() == []
    fetched_b = await client.get(f"/v1/remote/sessions/{session_id}", headers=headers_b)
    assert fetched_b.status_code == 404


async def test_get_session_404_for_unknown_id(client) -> None:
    headers, _, _ = _headers_with_remote_view()
    response = await client.get(f"/v1/remote/sessions/{uuid.uuid4()}", headers=headers)
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Frame feliz + transición pending -> active + rate limit
# ---------------------------------------------------------------------------


async def _create_connected_session(
    client, fake_manager: _FakeCompanionManager
) -> tuple[dict, dict]:
    headers, tenant_id, _ = _headers_with_remote_view()
    fake_manager.connected.add(tenant_id)
    created = await client.post("/v1/remote/sessions", json={"consent": True}, headers=headers)
    assert created.status_code == 201
    return headers, created.json()


async def test_frame_success_transitions_pending_to_active_and_returns_canned_b64(
    client, fake_manager: _FakeCompanionManager, fake_repo
) -> None:
    headers, session = await _create_connected_session(client, fake_manager)
    fake_manager.responses.append(dict(CANNED_FRAME_OK))

    response = await client.get(f"/v1/remote/sessions/{session['id']}/frame", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "image_b64": "aGVsbG8=",
        "width": 1920,
        "height": 1080,
        "mime": "image/png",
        "origin_x": 0,
        "origin_y": 0,
        "seq": 1,
    }

    # El companion recibe un frame JPEG acotado para la vista interactiva.
    assert fake_manager.calls == [
        (
            uuid.UUID(session["tenant_id"]),
            "screenshot",
            {"format": "jpeg", "quality": 68, "max_width": 1600},
        ),
    ]

    fetched = await client.get(f"/v1/remote/sessions/{session['id']}", headers=headers)
    fetched_body = fetched.json()
    assert fetched_body["status"] == "active"
    assert fetched_body["started_at"] is not None
    assert fetched_body["frames_count"] == 1

    actions = [entry["action"] for entry in fake_repo.audit_log]
    assert actions == ["remote.session.requested", "remote.session.started"]


async def test_frame_second_call_after_interval_increments_seq_without_restarting(
    client, fake_manager: _FakeCompanionManager, monkeypatch
) -> None:
    headers, session = await _create_connected_session(client, fake_manager)
    fake_manager.responses.append(dict(CANNED_FRAME_OK))
    fake_manager.responses.append(dict(CANNED_FRAME_OK))

    import edecan_api.routers.remote as remote_module

    monkeypatch.setattr(remote_module.time, "time", lambda: 1_000_000.0)
    first = await client.get(f"/v1/remote/sessions/{session['id']}/frame", headers=headers)
    assert first.status_code == 200
    assert first.json()["seq"] == 1

    # Avanza el reloj más allá del intervalo mínimo por defecto (1.0s).
    monkeypatch.setattr(remote_module.time, "time", lambda: 1_000_010.0)
    second = await client.get(f"/v1/remote/sessions/{session['id']}/frame", headers=headers)
    assert second.status_code == 200
    assert second.json()["seq"] == 2

    fetched = await client.get(f"/v1/remote/sessions/{session['id']}", headers=headers)
    # Sigue "active" (no se reinicia started_at en llamadas posteriores).
    assert fetched.json()["status"] == "active"
    assert fetched.json()["frames_count"] == 2


async def test_frame_rate_limited_returns_429_on_immediate_second_call(
    client, fake_manager: _FakeCompanionManager
) -> None:
    headers, session = await _create_connected_session(client, fake_manager)
    fake_manager.responses.append(dict(CANNED_FRAME_OK))

    first = await client.get(f"/v1/remote/sessions/{session['id']}/frame", headers=headers)
    assert first.status_code == 200

    # Sin avanzar el reloj: el segundo pedido cae dentro de la ventana mínima (1.0s default).
    second = await client.get(f"/v1/remote/sessions/{session['id']}/frame", headers=headers)
    assert second.status_code == 429
    # No se le pidió un segundo frame al companion: el límite cortó antes.
    assert len(fake_manager.calls) == 1


async def test_frame_404_for_unknown_session(client) -> None:
    headers, _, _ = _headers_with_remote_view()
    response = await client.get(f"/v1/remote/sessions/{uuid.uuid4()}/frame", headers=headers)
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Degradación: acción no soportada por el companion (WP-V2-08 no aterrizado)
# ---------------------------------------------------------------------------


async def test_frame_returns_501_when_companion_action_unsupported(
    client, fake_manager: _FakeCompanionManager
) -> None:
    headers, session = await _create_connected_session(client, fake_manager)
    fake_manager.responses.append(dict(UNSUPPORTED_ACTION_ERROR))

    response = await client.get(f"/v1/remote/sessions/{session['id']}/frame", headers=headers)

    assert response.status_code == 501
    assert "no soporta captura de pantalla" in response.json()["detail"]


async def test_frame_returns_501_when_companion_has_ide_disabled(
    client, fake_manager: _FakeCompanionManager
) -> None:
    """`screenshot` SÍ existe en `ACTIONS` (companion actualizado tras
    WP-V2-08) pero ese companion en particular tiene `ide_enabled: false` —
    motivo distinto de "acción no soportada", mismo resultado práctico: 501,
    no 502 genérico (ver `_translate_companion_error`)."""
    headers, session = await _create_connected_session(client, fake_manager)
    fake_manager.responses.append(dict(IDE_DISABLED_ERROR))

    response = await client.get(f"/v1/remote/sessions/{session['id']}/frame", headers=headers)

    assert response.status_code == 501
    assert "deshabilitada" in response.json()["detail"]


async def test_frame_returns_501_when_companion_platform_unsupported(
    client, fake_manager: _FakeCompanionManager
) -> None:
    """`screenshot` SÍ existe y `ide_enabled: true`, pero el companion corre
    en un sistema operativo distinto de macOS — `_screenshot` la rechaza
    siempre ahí, sin importar la config. Motivo distinto de los otros dos,
    mismo resultado práctico: 501, no 502 genérico (ver
    `_translate_companion_error`)."""
    headers, session = await _create_connected_session(client, fake_manager)
    fake_manager.responses.append(dict(PLATFORM_UNSUPPORTED_ERROR))

    response = await client.get(f"/v1/remote/sessions/{session['id']}/frame", headers=headers)

    assert response.status_code == 501
    assert "sistema operativo" in response.json()["detail"]


# ---------------------------------------------------------------------------
# El usuario deniega en el companion
# ---------------------------------------------------------------------------


async def test_frame_denied_by_user_marks_session_denied_and_returns_403(
    client, fake_manager: _FakeCompanionManager, fake_repo
) -> None:
    headers, session = await _create_connected_session(client, fake_manager)
    fake_manager.responses.append(dict(DENIED_ERROR))

    response = await client.get(f"/v1/remote/sessions/{session['id']}/frame", headers=headers)
    assert response.status_code == 403

    fetched = await client.get(f"/v1/remote/sessions/{session['id']}", headers=headers)
    assert fetched.json()["status"] == "denied"

    actions = [entry["action"] for entry in fake_repo.audit_log]
    assert actions == ["remote.session.requested", "remote.session.denied"]

    # Una sesión denegada no vuelve a intentar pedir frames al companion.
    fake_manager.responses.append(dict(CANNED_FRAME_OK))
    retried = await client.get(f"/v1/remote/sessions/{session['id']}/frame", headers=headers)
    assert retried.status_code == 403
    assert len(fake_manager.calls) == 1  # el segundo intento nunca llegó a llamar al companion


async def test_frame_denied_commits_audit_evidence_before_raising_403(
    client,
    fake_manager: _FakeCompanionManager,
    fake_repo,
    fake_db_session: _FakeDbSession,
) -> None:
    """`HOTFIXES_PENDIENTES.md` punto 8: `get_tenant_session` envuelve TODA la request en una
    única transacción con ROLLBACK automático ante cualquier excepción — el `HTTPException(403)`
    de la denegación lo es. Sin un `db_session.commit()` explícito ANTES de lanzarla, ese
    rollback se llevaría por delante la marca de denegación y su audit log.

    Este test verifica el commit EN SÍ (`fake_db_session.commits`), a diferencia de
    `test_frame_denied_by_user_marks_session_denied_and_returns_403` (que solo verifica el
    resultado en `fake_repo`, un fake en memoria SIN semántica transaccional real — "persiste"
    la denegación aunque el código nunca llamara a `commit()`, así que por sí solo ese test no
    habría detectado ni el bug original ni la variante con `session`/parámetro sombreados que
    lo reemplazó — ver el comentario en `remote.py::get_frame`)."""
    headers, session = await _create_connected_session(client, fake_manager)
    fake_manager.responses.append(dict(DENIED_ERROR))

    response = await client.get(f"/v1/remote/sessions/{session['id']}/frame", headers=headers)

    assert response.status_code == 403
    assert fake_db_session.commits == 1

    actions = [entry["action"] for entry in fake_repo.audit_log]
    assert actions == ["remote.session.requested", "remote.session.denied"]


# ---------------------------------------------------------------------------
# Terminar sesión
# ---------------------------------------------------------------------------


async def test_end_session_marks_ended_and_audits_with_duration(
    client, fake_manager: _FakeCompanionManager, fake_repo
) -> None:
    headers, session = await _create_connected_session(client, fake_manager)
    fake_manager.responses.append(dict(CANNED_FRAME_OK))
    await client.get(f"/v1/remote/sessions/{session['id']}/frame", headers=headers)

    response = await client.post(f"/v1/remote/sessions/{session['id']}/end", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ended"
    assert body["ended_at"] is not None

    actions = [entry["action"] for entry in fake_repo.audit_log]
    assert actions == ["remote.session.requested", "remote.session.started", "remote.session.ended"]
    end_entry = fake_repo.audit_log[-1]
    assert end_entry["meta"]["frames_count"] == 1
    assert end_entry["meta"]["duration_seconds"] is not None


async def test_end_session_without_any_frame_still_audits_once(
    client, fake_manager: _FakeCompanionManager, fake_repo
) -> None:
    headers, session = await _create_connected_session(client, fake_manager)

    response = await client.post(f"/v1/remote/sessions/{session['id']}/end", headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "ended"
    assert response.json()["started_at"] is None

    actions = [entry["action"] for entry in fake_repo.audit_log]
    assert actions == ["remote.session.requested", "remote.session.ended"]


async def test_end_session_is_idempotent_and_does_not_double_audit(
    client, fake_manager: _FakeCompanionManager, fake_repo
) -> None:
    headers, session = await _create_connected_session(client, fake_manager)

    first = await client.post(f"/v1/remote/sessions/{session['id']}/end", headers=headers)
    second = await client.post(f"/v1/remote/sessions/{session['id']}/end", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    actions = [entry["action"] for entry in fake_repo.audit_log]
    assert actions.count("remote.session.ended") == 1


async def test_frame_after_end_returns_409(client, fake_manager: _FakeCompanionManager) -> None:
    headers, session = await _create_connected_session(client, fake_manager)
    await client.post(f"/v1/remote/sessions/{session['id']}/end", headers=headers)

    response = await client.get(f"/v1/remote/sessions/{session['id']}/frame", headers=headers)
    assert response.status_code == 409


async def test_end_session_404_for_unknown_session(client) -> None:
    headers, _, _ = _headers_with_remote_view()
    response = await client.post(f"/v1/remote/sessions/{uuid.uuid4()}/end", headers=headers)
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# WP-V4-10 — kind="control": creación
# ---------------------------------------------------------------------------


def _headers_with_remote_control() -> tuple[dict[str, str], uuid.UUID, uuid.UUID]:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key=PLAN_WITH_REMOTE_CONTROL)
    return headers, tenant_id, user_id


async def _create_control_session(
    client: AsyncClient, fake_manager: _FakeCompanionManager
) -> tuple[dict[str, str], dict]:
    headers, tenant_id, _ = _headers_with_remote_control()
    fake_manager.connected.add(tenant_id)
    created = await client.post(
        "/v1/remote/sessions", json={"consent": True, "kind": "control"}, headers=headers
    )
    assert created.status_code == 201
    return headers, created.json()


async def _create_active_control_session(
    client: AsyncClient, fake_manager: _FakeCompanionManager
) -> tuple[dict[str, str], dict]:
    """`_create_control_session` + un `GET .../frame` exitoso para activarla
    (`send_input` exige `status == "active"`, igual que una sesión de vista
    necesita al menos un frame antes de considerarse "activa")."""
    headers, session = await _create_control_session(client, fake_manager)
    fake_manager.responses.append(dict(CANNED_FRAME_OK))
    frame_response = await client.get(f"/v1/remote/sessions/{session['id']}/frame", headers=headers)
    assert frame_response.status_code == 200
    fetched = await client.get(f"/v1/remote/sessions/{session['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "active"
    return headers, fetched.json()


async def test_create_session_default_kind_is_view(
    client, fake_manager: _FakeCompanionManager
) -> None:
    headers, tenant_id, _ = _headers_with_remote_view()
    fake_manager.connected.add(tenant_id)

    response = await client.post("/v1/remote/sessions", json={"consent": True}, headers=headers)

    assert response.status_code == 201
    assert response.json()["kind"] == "view"


async def test_create_session_rejects_invalid_kind_value(client) -> None:
    headers, _, _ = _headers_with_remote_view()
    response = await client.post(
        "/v1/remote/sessions", json={"consent": True, "kind": "god-mode"}, headers=headers
    )
    assert response.status_code == 422


async def test_create_session_kind_control_success_marks_kind_and_audits(
    client, fake_manager: _FakeCompanionManager, fake_repo: _FakeRepoWithKind
) -> None:
    headers, session = await _create_control_session(client, fake_manager)

    assert session["kind"] == "control"
    assert session["status"] == "pending"

    assert len(fake_repo.audit_log) == 1
    entry = fake_repo.audit_log[0]
    assert entry["action"] == "remote.session.requested"
    assert entry["meta"] == {"kind": "control"}


async def test_create_session_kind_control_requires_remote_input_flag(
    client, app, fake_manager: _FakeCompanionManager
) -> None:
    """Ningún plan real de `PLANES` combina remote_view=True con
    remote_input=False (ver docstring del módulo) -- se sobreescribe
    `get_current_user` a mano para aislar ESTE gate nuevo del ya existente."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    fake_manager.connected.add(tenant_id)

    synthetic_user = edecan_deps.CurrentUser(
        user_id=user_id,
        tenant=edecan_deps.TenantCtx(
            tenant_id=tenant_id,
            plan_key="plan-sintetico-de-test",
            flags={remote.FLAG_REMOTE_VIEW: True, remote.FLAG_REMOTE_INPUT: False},
        ),
    )
    app.dependency_overrides[edecan_deps.get_current_user] = lambda: synthetic_user

    response = await client.post(
        "/v1/remote/sessions", json={"consent": True, "kind": "control"}, headers={}
    )

    assert response.status_code == 403
    assert "control remoto" in response.json()["detail"].lower()


async def test_create_session_kind_view_does_not_require_remote_input_flag(
    client, app, fake_manager: _FakeCompanionManager
) -> None:
    """El mismo usuario sintético de arriba (remote_input=False) SÍ puede
    crear una sesión kind="view" (default): el gate nuevo nunca se evalúa."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    fake_manager.connected.add(tenant_id)

    synthetic_user = edecan_deps.CurrentUser(
        user_id=user_id,
        tenant=edecan_deps.TenantCtx(
            tenant_id=tenant_id,
            plan_key="plan-sintetico-de-test",
            flags={remote.FLAG_REMOTE_VIEW: True, remote.FLAG_REMOTE_INPUT: False},
        ),
    )
    app.dependency_overrides[edecan_deps.get_current_user] = lambda: synthetic_user

    response = await client.post("/v1/remote/sessions", json={"consent": True}, headers={})

    assert response.status_code == 201
    assert response.json()["kind"] == "view"


# ---------------------------------------------------------------------------
# WP-V4-10 — POST .../input: gates de autenticación/flag/kind/status
# ---------------------------------------------------------------------------


async def test_input_requires_authentication(client) -> None:
    response = await client.post(
        f"/v1/remote/sessions/{uuid.uuid4()}/input", json={"tipo": "key", "tecla": "enter"}
    )
    assert response.status_code == 401


async def test_input_404_for_unknown_session(client) -> None:
    headers, _, _ = _headers_with_remote_control()
    response = await client.post(
        f"/v1/remote/sessions/{uuid.uuid4()}/input",
        json={"tipo": "key", "tecla": "enter"},
        headers=headers,
    )
    assert response.status_code == 404


async def test_input_404_for_session_belonging_to_another_tenant(
    client, fake_manager: _FakeCompanionManager
) -> None:
    _, session = await _create_active_control_session(client, fake_manager)

    headers_b, tenant_b, _ = _headers_with_remote_control()
    assert tenant_b != uuid.UUID(session["tenant_id"])

    response = await client.post(
        f"/v1/remote/sessions/{session['id']}/input",
        json={"tipo": "key", "tecla": "enter"},
        headers=headers_b,
    )
    assert response.status_code == 404


async def test_input_403_when_session_kind_is_view(
    client, fake_manager: _FakeCompanionManager
) -> None:
    # PLAN_WITH_REMOTE_VIEW también trae remote_input=True -- aísla el gate
    # de "kind" del gate de flags de plan.
    headers, session = await _create_connected_session(client, fake_manager)
    assert session["kind"] == "view"

    response = await client.post(
        f"/v1/remote/sessions/{session['id']}/input",
        json={"tipo": "key", "tecla": "enter"},
        headers=headers,
    )

    assert response.status_code == 403
    assert "no es de control remoto" in response.json()["detail"].lower()


async def test_input_409_when_session_still_pending(
    client, fake_manager: _FakeCompanionManager
) -> None:
    headers, session = await _create_control_session(client, fake_manager)

    response = await client.post(
        f"/v1/remote/sessions/{session['id']}/input",
        json={"tipo": "key", "tecla": "enter"},
        headers=headers,
    )

    assert response.status_code == 409
    assert "todavía no está activa" in response.json()["detail"]


async def test_input_403_when_session_already_denied(
    client, fake_manager: _FakeCompanionManager
) -> None:
    headers, session = await _create_control_session(client, fake_manager)
    fake_manager.responses.append(dict(DENIED_ERROR))
    denied = await client.get(f"/v1/remote/sessions/{session['id']}/frame", headers=headers)
    assert denied.status_code == 403

    response = await client.post(
        f"/v1/remote/sessions/{session['id']}/input",
        json={"tipo": "key", "tecla": "enter"},
        headers=headers,
    )

    assert response.status_code == 403
    assert "denegó" in response.json()["detail"]


async def test_input_409_when_session_already_ended(
    client, fake_manager: _FakeCompanionManager
) -> None:
    headers, session = await _create_active_control_session(client, fake_manager)
    ended = await client.post(f"/v1/remote/sessions/{session['id']}/end", headers=headers)
    assert ended.status_code == 200

    response = await client.post(
        f"/v1/remote/sessions/{session['id']}/input",
        json={"tipo": "key", "tecla": "enter"},
        headers=headers,
    )

    assert response.status_code == 409
    assert "ya terminó" in response.json()["detail"]


async def test_input_503_when_companion_disconnects_before_input(
    client, fake_manager: _FakeCompanionManager
) -> None:
    headers, session = await _create_active_control_session(client, fake_manager)
    fake_manager.connected.discard(uuid.UUID(session["tenant_id"]))

    response = await client.post(
        f"/v1/remote/sessions/{session['id']}/input",
        json={"tipo": "key", "tecla": "enter"},
        headers=headers,
    )

    assert response.status_code == 503


# ---------------------------------------------------------------------------
# WP-V4-10 — POST .../input: validación de payload (pointer/key)
# ---------------------------------------------------------------------------


async def test_input_rejects_unknown_tipo(client, fake_manager: _FakeCompanionManager) -> None:
    headers, session = await _create_active_control_session(client, fake_manager)

    response = await client.post(
        f"/v1/remote/sessions/{session['id']}/input", json={"tipo": "voz"}, headers=headers
    )
    assert response.status_code == 422


async def test_input_pointer_rejects_invalid_accion(
    client, fake_manager: _FakeCompanionManager
) -> None:
    headers, session = await _create_active_control_session(client, fake_manager)

    response = await client.post(
        f"/v1/remote/sessions/{session['id']}/input",
        json={"tipo": "pointer", "x": 1, "y": 2, "accion": "teleport"},
        headers=headers,
    )
    assert response.status_code == 422


async def test_input_key_rejects_both_texto_and_tecla(
    client, fake_manager: _FakeCompanionManager
) -> None:
    headers, session = await _create_active_control_session(client, fake_manager)

    response = await client.post(
        f"/v1/remote/sessions/{session['id']}/input",
        json={"tipo": "key", "texto": "hola", "tecla": "enter"},
        headers=headers,
    )
    assert response.status_code == 422


async def test_input_key_rejects_neither_texto_nor_tecla(
    client, fake_manager: _FakeCompanionManager
) -> None:
    headers, session = await _create_active_control_session(client, fake_manager)

    response = await client.post(
        f"/v1/remote/sessions/{session['id']}/input", json={"tipo": "key"}, headers=headers
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# WP-V4-10 — POST .../input: feliz + auditoría + redacción de texto
# ---------------------------------------------------------------------------


async def test_input_pointer_success_forwards_action_with_session_id_and_audits(
    client, fake_manager: _FakeCompanionManager, fake_repo: _FakeRepoWithKind
) -> None:
    headers, session = await _create_active_control_session(client, fake_manager)
    fake_manager.responses.append(
        {"ok": True, "result": {"x": 10, "y": 20, "accion": "click", "button": "left"}}
    )

    response = await client.post(
        f"/v1/remote/sessions/{session['id']}/input",
        json={"tipo": "pointer", "x": 10, "y": 20, "accion": "click"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True

    _, action, params = fake_manager.calls[-1]
    assert action == "input_pointer"
    assert params == {"session_id": session["id"], "x": 10, "y": 20, "accion": "click"}

    logged_actions = [entry["action"] for entry in fake_repo.audit_log]
    assert logged_actions[-1] == "remote.session.input"
    assert fake_repo.audit_log[-1]["meta"] == {"tipo": "pointer", "accion": "click"}


async def test_input_pointer_forwards_button_when_provided(
    client, fake_manager: _FakeCompanionManager
) -> None:
    headers, session = await _create_active_control_session(client, fake_manager)
    fake_manager.responses.append({"ok": True, "result": {}})

    await client.post(
        f"/v1/remote/sessions/{session['id']}/input",
        json={"tipo": "pointer", "x": 1, "y": 2, "accion": "click", "button": "right"},
        headers=headers,
    )

    _, _, params = fake_manager.calls[-1]
    assert params["button"] == "right"


async def test_input_key_texto_success_and_audit_never_contains_raw_text(
    client, fake_manager: _FakeCompanionManager, fake_repo: _FakeRepoWithKind
) -> None:
    headers, session = await _create_active_control_session(client, fake_manager)
    fake_manager.responses.append({"ok": True, "result": {"tipo": "texto", "length": 4}})

    response = await client.post(
        f"/v1/remote/sessions/{session['id']}/input",
        json={"tipo": "key", "texto": "hola"},
        headers=headers,
    )

    assert response.status_code == 200

    _, action, params = fake_manager.calls[-1]
    assert action == "input_key"
    assert params == {"session_id": session["id"], "texto": "hola"}  # el companion SÍ lo necesita

    entry = fake_repo.audit_log[-1]
    assert entry["action"] == "remote.session.input"
    assert entry["meta"] == {"tipo": "key", "clave": "texto", "length": 4}
    assert "hola" not in str(entry["meta"])


async def test_input_key_tecla_success(client, fake_manager: _FakeCompanionManager) -> None:
    headers, session = await _create_active_control_session(client, fake_manager)
    fake_manager.responses.append({"ok": True, "result": {"tipo": "tecla", "tecla": "enter"}})

    response = await client.post(
        f"/v1/remote/sessions/{session['id']}/input",
        json={"tipo": "key", "tecla": "enter"},
        headers=headers,
    )

    assert response.status_code == 200
    _, action, params = fake_manager.calls[-1]
    assert action == "input_key"
    assert params == {"session_id": session["id"], "tecla": "enter"}


# ---------------------------------------------------------------------------
# WP-V4-10 — POST .../input: rate limit propio
# ---------------------------------------------------------------------------


async def test_input_rate_limited_returns_429_on_immediate_second_call(
    client, fake_manager: _FakeCompanionManager, monkeypatch
) -> None:
    headers, session = await _create_active_control_session(client, fake_manager)
    fake_manager.responses.append({"ok": True, "result": {}})

    monkeypatch.setattr(remote.time, "time", lambda: 3_000_000.0)
    first = await client.post(
        f"/v1/remote/sessions/{session['id']}/input",
        json={"tipo": "key", "tecla": "enter"},
        headers=headers,
    )
    assert first.status_code == 200

    # Mismo instante congelado -- cae dentro de la ventana mínima (0.05s
    # default) con certeza, sin depender de qué tan rápido corre la máquina.
    second = await client.post(
        f"/v1/remote/sessions/{session['id']}/input",
        json={"tipo": "key", "tecla": "enter"},
        headers=headers,
    )
    assert second.status_code == 429
    # 2 = el "screenshot" de activación (`_create_active_control_session`) +
    # el primer "input_key" exitoso; el segundo intento nunca llegó al companion.
    assert len(fake_manager.calls) == 2


async def test_input_rate_limit_is_independent_from_frame_rate_limit(
    client, fake_manager: _FakeCompanionManager, monkeypatch
) -> None:
    """Pedir un frame y mandar un input en el mismo instante no debe
    interferir entre sí -- claves de Redis separadas (`remote:frame:...` vs
    `remote:input:...`)."""
    headers, session = await _create_active_control_session(client, fake_manager)

    # Un valor bien lejos en el futuro (no uno pequeño tipo "4_000_000.0"):
    # `_create_active_control_session` ya guardó un timestamp de frame con el
    # reloj REAL (sin parchear) hace un instante -- un valor congelado menor
    # que ese "ahora" real produciría un `elapsed` negativo y dispararía un
    # 429 espurio por la razón EQUIVOCADA (no por compartir cupo con input).
    monkeypatch.setattr(remote.time, "time", lambda: 9_999_999_999.0)
    fake_manager.responses.append(dict(CANNED_FRAME_OK))
    frame_response = await client.get(f"/v1/remote/sessions/{session['id']}/frame", headers=headers)
    assert frame_response.status_code == 200

    fake_manager.responses.append({"ok": True, "result": {}})
    input_response = await client.post(
        f"/v1/remote/sessions/{session['id']}/input",
        json={"tipo": "key", "tecla": "enter"},
        headers=headers,
    )
    assert input_response.status_code == 200


# ---------------------------------------------------------------------------
# WP-V4-10 — POST .../input: degradación (companion viejo/deshabilitado/plataforma)
# ---------------------------------------------------------------------------


async def test_input_returns_501_when_companion_action_unsupported(
    client, fake_manager: _FakeCompanionManager
) -> None:
    headers, session = await _create_active_control_session(client, fake_manager)
    fake_manager.responses.append(dict(INPUT_UNSUPPORTED_ACTION_ERROR))

    response = await client.post(
        f"/v1/remote/sessions/{session['id']}/input",
        json={"tipo": "pointer", "x": 1, "y": 1, "accion": "move"},
        headers=headers,
    )

    assert response.status_code == 501
    assert "no soporta control remoto" in response.json()["detail"]


async def test_input_returns_501_when_remote_input_disabled_on_companion(
    client, fake_manager: _FakeCompanionManager
) -> None:
    headers, session = await _create_active_control_session(client, fake_manager)
    fake_manager.responses.append(dict(INPUT_DISABLED_ERROR))

    response = await client.post(
        f"/v1/remote/sessions/{session['id']}/input",
        json={"tipo": "key", "tecla": "enter"},
        headers=headers,
    )

    assert response.status_code == 501
    assert "deshabilitado" in response.json()["detail"]


async def test_input_returns_501_when_platform_unsupported(
    client, fake_manager: _FakeCompanionManager
) -> None:
    headers, session = await _create_active_control_session(client, fake_manager)
    fake_manager.responses.append(dict(INPUT_PLATFORM_UNSUPPORTED_ERROR))

    response = await client.post(
        f"/v1/remote/sessions/{session['id']}/input",
        json={"tipo": "key", "tecla": "enter"},
        headers=headers,
    )

    assert response.status_code == 501
    assert "sistema operativo" in response.json()["detail"]


async def test_input_returns_502_for_unrecognized_companion_error(
    client, fake_manager: _FakeCompanionManager
) -> None:
    """P. ej. falta pyobjc-framework-Quartz o falta el permiso de
    Accesibilidad -- problemas puntuales del equipo, no "no existe/está
    apagado", así que caen al 502 genérico (igual que `screenshot` con el
    permiso de Grabación de pantalla, ver `_translate_input_companion_error`)."""
    headers, session = await _create_active_control_session(client, fake_manager)
    fake_manager.responses.append(dict(INPUT_ACCESSIBILITY_DENIED_ERROR))

    response = await client.post(
        f"/v1/remote/sessions/{session['id']}/input",
        json={"tipo": "key", "tecla": "enter"},
        headers=headers,
    )

    assert response.status_code == 502
    assert "Accesibilidad" in response.json()["detail"]


async def test_input_503_when_companion_does_not_respond_in_time(
    client, fake_manager: _FakeCompanionManager
) -> None:
    headers, session = await _create_active_control_session(client, fake_manager)
    fake_manager.responses.append(CompanionError("tiempo agotado (simulado)"))

    response = await client.post(
        f"/v1/remote/sessions/{session['id']}/input",
        json={"tipo": "key", "tecla": "enter"},
        headers=headers,
    )

    assert response.status_code == 503


# ---------------------------------------------------------------------------
# WP-V4-10 — POST .../input: el usuario deniega en el companion
# ---------------------------------------------------------------------------


async def test_input_denied_by_user_marks_session_denied_and_commits_before_403(
    client,
    fake_manager: _FakeCompanionManager,
    fake_repo: _FakeRepoWithKind,
    fake_db_session: _FakeDbSession,
) -> None:
    headers, session = await _create_active_control_session(client, fake_manager)
    assert fake_db_session.commits == 0  # el frame previo fue exitoso, no denegado

    fake_manager.responses.append(dict(DENIED_ERROR))
    response = await client.post(
        f"/v1/remote/sessions/{session['id']}/input",
        json={"tipo": "key", "tecla": "enter"},
        headers=headers,
    )

    assert response.status_code == 403
    assert fake_db_session.commits == 1  # HOTFIXES_PENDIENTES.md punto 8, ver docstring del módulo

    fetched = await client.get(f"/v1/remote/sessions/{session['id']}", headers=headers)
    assert fetched.json()["status"] == "denied"

    logged_actions = [entry["action"] for entry in fake_repo.audit_log]
    assert logged_actions[-1] == "remote.session.input_denied"

    # Una sesión denegada no reintenta contra el companion.
    fake_manager.responses.append({"ok": True, "result": {}})
    retried = await client.post(
        f"/v1/remote/sessions/{session['id']}/input",
        json={"tipo": "key", "tecla": "enter"},
        headers=headers,
    )
    assert retried.status_code == 403
    # 2 = el "screenshot" de activación + el "input_key" denegado; el
    # reintento nunca volvió a llamar al companion.
    assert len(fake_manager.calls) == 2
