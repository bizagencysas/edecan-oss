"""`edecan_api.routers.voz_avanzada` — `/v1/voz/*` (WP-V5-10; ver el docstring
del propio router para el contrato completo).

`edecan_api.main.create_app()` todavía puede no montar `voz_avanzada.router`
(el nombre del módulo lo agrega WP-V5-01 a `V5_ROUTER_NAMES` cuando aterrice
esa pieza — ver el docstring del router, sección "Tabla voice_consents — nota
de coordinación"): igual que `test_erp_router.py`, este archivo monta el
router manualmente y de forma explícita sobre la `app` de `conftest.py`, así
estos tests no dependen del orden/estado de aterrizaje de otros paquetes de
trabajo en paralelo.

`fake_session` (`FakeSession`/`FakeResult` locales, duplicadas a propósito —
`ARCHITECTURE.md` §10.1, mismo patrón que `test_erp_router.py`) reemplaza
`edecan_api.deps.get_tenant_session`: consume una cola de respuestas
programadas en el ORDEN EXACTO en que el router las pide, y cuenta llamadas a
`commit()` (`fake_session.commits`, mismo patrón que
`test_commerce_router.py::FakeSession`/`test_remote_router.py::
_FakeDbSession`) para verificar que la evidencia de consentimiento sobrevive
al commit explícito de `crear_clon_voz` antes de sus `raise` — ver
`HOTFIXES_PENDIENTES.md` punto 8/9. `fake_repo` (`conftest.py`) cubre
`list_connector_accounts`/`create_file`/`add_audit_log` — nunca se toca
`edecan_api/repo.py`.

`respx` mockea ElevenLabs (cero red real); S3 se mockea con el mismo doble
que `api_fakes.py::_FakeAioboto3Session` (duplicado localmente, ver ese
módulo — no se importa: es privado de `test_files.py`/`api_fakes.py`).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx
from conftest import auth_headers
from edecan_schemas import TokenBundle
from httpx import ASGITransport, AsyncClient

from edecan_api import deps as edecan_deps
from edecan_api.routers import voz_avanzada

# hosted_basic (mismo criterio que test_erp_router.py) es el plan "de acceso"
# de estos tests. Modelo de precio de pago único (2026-07-09,
# `edecan_schemas.plans` docstring): `voice.cloning` (y cualquier otro flag) ya
# está en `True` en las 4 entradas de `PLANES` por igual -- no hay más "flag
# apagado" que probar. Los tests de "camino feliz" de `/clones` igual usan
# `_headers_con_cloning` (inyecta un `TenantCtx` directo) para no acoplarse a
# qué plan_key exponga el flag.
PLAN_CON_VOICE_WEB = "hosted_basic"


class FakeResult:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> FakeResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)


@dataclass
class FakeSession:
    respuestas: list[list[dict[str, Any]]] = field(default_factory=list)
    llamadas: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    commits: int = 0

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> FakeResult:
        self.llamadas.append((str(stmt), dict(params or {})))
        filas = self.respuestas.pop(0) if self.respuestas else []
        return FakeResult(filas)

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        # `crear_clon_voz` (HOTFIXES_PENDIENTES.md punto 8/9) comitea explícitamente antes de
        # los `raise` posteriores al INSERT de `voice_consents`, para que esa evidencia
        # sobreviva el ROLLBACK automático de `get_tenant_session` — ver el comentario en
        # `edecan_api/routers/voz_avanzada.py`. Este doble solo cuenta llamadas (mismo
        # criterio "extender mínimamente DENTRO del test file" que
        # `test_remote_router.py::_FakeDbSession`/`test_commerce_router.py::FakeSession`); no
        # modela transacciones reales.
        self.commits += 1


class FakeVault:
    def __init__(self) -> None:
        self._store: dict[tuple[uuid.UUID, uuid.UUID], TokenBundle] = {}

    async def put(self, tenant_id: uuid.UUID, account_id: uuid.UUID, bundle: TokenBundle) -> None:
        self._store[(tenant_id, account_id)] = bundle

    async def get(self, tenant_id: uuid.UUID, account_id: uuid.UUID) -> TokenBundle | None:
        return self._store.get((tenant_id, account_id))


class _FakeS3Client:
    def __init__(self, calls: list[dict[str, Any]]) -> None:
        self._calls = calls

    async def __aenter__(self) -> _FakeS3Client:
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        return None

    async def put_object(self, **kwargs: Any) -> None:
        self._calls.append(kwargs)


class _FakeAioboto3Session:
    def __init__(self, calls: list[dict[str, Any]]) -> None:
        self._calls = calls

    def client(self, service_name: str, **kwargs: Any) -> _FakeS3Client:
        assert service_name == "s3"
        return _FakeS3Client(self._calls)


@pytest.fixture
def fake_session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def s3_calls() -> list[dict[str, Any]]:
    return []


@pytest.fixture
def _mounted_app(app, fake_session: FakeSession, s3_calls: list[dict[str, Any]], monkeypatch):
    """`app` (de `conftest.py`) + `voz_avanzada.router` montado +
    `get_tenant_session` reemplazado por `fake_session` (mismo patrón que
    `test_erp_router.py`), más S3 mockeado (mismo patrón que `test_files.py`)."""
    app.include_router(voz_avanzada.router)
    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: fake_session
    monkeypatch.setattr(
        voz_avanzada.aioboto3, "Session", lambda: _FakeAioboto3Session(s3_calls)
    )
    return app


@pytest.fixture
async def client(_mounted_app) -> AsyncIterator[AsyncClient]:
    # Sombrea a propósito el fixture `client` de `conftest.py` (ver
    # `test_erp_router.py`): ese vive sobre `app` sin `voz_avanzada.router`.
    transport = ASGITransport(app=_mounted_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _headers_con_voice_web(**kw: Any) -> dict[str, str]:
    kw.setdefault("user_id", uuid.uuid4())
    kw.setdefault("tenant_id", uuid.uuid4())
    return auth_headers(plan_key=PLAN_CON_VOICE_WEB, **kw)


def _headers_con_cloning(
    app, *, tenant_id: uuid.UUID, user_id: uuid.UUID | None = None
) -> dict[str, str]:
    """`voice.cloning` no está pinned todavía en ningún plan real (ver
    docstring del módulo) — para ejercitar el "camino feliz" de `/clones` sin
    esperar a que otro paquete de trabajo agregue esa fila a `PLANES`, se
    sobreescribe `get_current_user` directo con un `CurrentUser` que SÍ trae
    el flag en `True`, en vez de depender de un `plan_key` real."""
    user_id = user_id or uuid.uuid4()
    tenant = edecan_deps.TenantCtx(
        tenant_id=tenant_id, plan_key="hosted_business", flags={"voice.cloning": True}
    )
    current_user = edecan_deps.CurrentUser(user_id=user_id, tenant=tenant)
    app.dependency_overrides[edecan_deps.get_current_user] = lambda: current_user
    return auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key="hosted_business")


def _bundle(config: dict[str, Any]) -> TokenBundle:
    return TokenBundle(access_token=json.dumps(config), token_type="config")


async def _conectar_elevenlabs_tenant(
    fake_repo: Any, fake_vault: FakeVault, *, tenant_id: uuid.UUID, api_key: str = "el-tenant-key"
) -> None:
    account = await fake_repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key="voice_tts",
        external_account_id="voice_tts",
        display_name="voice_tts",
        scopes=[],
    )
    await fake_vault.put(
        tenant_id, account["id"], _bundle({"provider": "elevenlabs", "api_key": api_key})
    )


def _voice_consent_row(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    base = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "voice_name": "Mi Voz",
        "attestation": True,
        "status": "attested",
        "consent_file_id": uuid.uuid4(),
        "provider_voice_id": None,
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return base


def _clone_files(
    *,
    incluir_consentimiento: bool = True,
    n_muestras: int = 1,
) -> list[tuple[str, tuple[str, bytes, str]]]:
    # httpx exige una LISTA de tuplas `(campo, (filename, contenido, mime))`
    # (no un dict) para poder mandar varios archivos bajo el MISMO nombre de
    # campo ("muestras") en una sola request multipart — un dict solo admite
    # un valor por clave.
    files: list[tuple[str, tuple[str, bytes, str]]] = []
    if incluir_consentimiento:
        files.append(
            ("consentimiento", ("consentimiento.mp3", b"audio-consentimiento", "audio/mpeg"))
        )
    for i in range(n_muestras):
        files.append(("muestras", (f"m{i}.mp3", f"audio-muestra-{i}".encode(), "audio/mpeg")))
    return files


# ---------------------------------------------------------------------------
# GET /v1/voz/voces
# ---------------------------------------------------------------------------


async def test_listar_voces_requires_authentication(client) -> None:
    response = await client.get("/v1/voz/voces")
    assert response.status_code == 401


async def test_listar_voces_sin_flag_voice_web_returns_403(client) -> None:
    headers = auth_headers(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="plan_no_existe"
    )
    response = await client.get("/v1/voz/voces", headers=headers)
    assert response.status_code == 403


async def test_listar_voces_sin_credencial_devuelve_stubs(client, fake_repo) -> None:
    response = await client.get("/v1/voz/voces", headers=_headers_con_voice_web())
    assert response.status_code == 200
    voces = response.json()
    assert len(voces) == 2
    assert all(v["voice_id"].startswith("stub-") for v in voces)


@respx.mock
async def test_listar_voces_con_elevenlabs_usa_catalogo_real(client, fake_repo, app) -> None:
    respx.get("https://api.elevenlabs.io/v1/voices").mock(
        return_value=httpx.Response(
            200,
            json={"voices": [{"voice_id": "voz-real", "name": "Voz Real", "category": "cloned"}]},
        )
    )
    fake_vault = FakeVault()
    app.dependency_overrides[edecan_deps.get_vault] = lambda: fake_vault
    tenant_id = uuid.uuid4()
    await _conectar_elevenlabs_tenant(fake_repo, fake_vault, tenant_id=tenant_id)

    response = await client.get(
        "/v1/voz/voces", headers=_headers_con_voice_web(tenant_id=tenant_id)
    )

    assert response.status_code == 200
    assert response.json() == [
        {"voice_id": "voz-real", "nombre": "Voz Real", "categoria": "cloned", "preview_url": None}
    ]


@respx.mock
async def test_listar_voces_error_elevenlabs_returns_502(client, fake_repo, app) -> None:
    respx.get("https://api.elevenlabs.io/v1/voices").mock(
        return_value=httpx.Response(401, text="invalid_api_key")
    )
    fake_vault = FakeVault()
    app.dependency_overrides[edecan_deps.get_vault] = lambda: fake_vault
    tenant_id = uuid.uuid4()
    await _conectar_elevenlabs_tenant(
        fake_repo, fake_vault, tenant_id=tenant_id, api_key="clave-secreta"
    )

    response = await client.get(
        "/v1/voz/voces", headers=_headers_con_voice_web(tenant_id=tenant_id)
    )

    assert response.status_code == 502
    assert "clave-secreta" not in response.text


# ---------------------------------------------------------------------------
# POST /v1/voz/clones
# ---------------------------------------------------------------------------


async def test_crear_clon_requires_authentication(client) -> None:
    response = await client.post("/v1/voz/clones", data={"nombre": "X"})
    assert response.status_code == 401


async def test_crear_clon_sin_attestation_returns_400(client, fake_repo, fake_session, app) -> None:
    tenant_id = uuid.uuid4()
    headers = _headers_con_cloning(app, tenant_id=tenant_id)
    response = await client.post(
        "/v1/voz/clones",
        data={"nombre": "X"},  # sin 'attestation'
        files=_clone_files(),
        headers=headers,
    )
    assert response.status_code == 400
    assert "consentimiento" in response.json()["detail"].lower()
    assert fake_session.llamadas == []


async def test_crear_clon_attestation_no_literal_true_returns_400(
    client, fake_repo, fake_session, app
) -> None:
    tenant_id = uuid.uuid4()
    headers = _headers_con_cloning(app, tenant_id=tenant_id)
    response = await client.post(
        "/v1/voz/clones",
        data={"nombre": "X", "attestation": "yes"},
        files=_clone_files(),
        headers=headers,
    )
    assert response.status_code == 400
    assert fake_session.llamadas == []


async def test_crear_clon_sin_archivo_de_consentimiento_returns_400(
    client, fake_repo, fake_session, app
) -> None:
    tenant_id = uuid.uuid4()
    headers = _headers_con_cloning(app, tenant_id=tenant_id)
    response = await client.post(
        "/v1/voz/clones",
        data={"nombre": "X", "attestation": "true"},
        files=_clone_files(incluir_consentimiento=False),
        headers=headers,
    )
    assert response.status_code == 400
    assert "consentimiento" in response.json()["detail"].lower()
    assert fake_session.llamadas == []


async def test_crear_clon_sin_muestras_returns_400(client, fake_repo, fake_session, app) -> None:
    tenant_id = uuid.uuid4()
    headers = _headers_con_cloning(app, tenant_id=tenant_id)
    response = await client.post(
        "/v1/voz/clones",
        data={"nombre": "X", "attestation": "true"},
        files=_clone_files(n_muestras=0),
        headers=headers,
    )
    assert response.status_code == 400
    assert fake_session.llamadas == []


async def test_crear_clon_demasiadas_muestras_returns_400(
    client, fake_repo, fake_session, app
) -> None:
    tenant_id = uuid.uuid4()
    headers = _headers_con_cloning(app, tenant_id=tenant_id)
    response = await client.post(
        "/v1/voz/clones",
        data={"nombre": "X", "attestation": "true"},
        files=_clone_files(n_muestras=6),
        headers=headers,
    )
    assert response.status_code == 400
    assert fake_session.llamadas == []


async def test_crear_clon_credencial_no_elevenlabs_returns_400_pero_guarda_evidencia(
    client, fake_repo, fake_session, app
) -> None:
    """Sin ElevenLabs conectado: el consentimiento YA se subió y se insertó
    ANTES del chequeo de credencial (ver docstring del router, paso 5/6) — la
    evidencia no se pierde aunque la clonación técnica falle.

    `HOTFIXES_PENDIENTES.md` punto 8/9: `get_tenant_session` envuelve TODA la
    request en una única transacción con ROLLBACK automático ante cualquier
    excepción, y el `HTTPException(400)` de abajo lo es — sin un
    `session.commit()` explícito ANTES de lanzarla, ese rollback se llevaría
    puesto el INSERT de `voice_consents` de arriba. Esta aserción verifica el
    commit EN SÍ (`fake_session.commits`), no solo que el INSERT se haya
    *pedido* — un `fake_session` sin semántica transaccional real "persiste"
    igual aunque el código nunca llame a `commit()`, así que por sí sola la
    aserción de `llamadas` de abajo no habría detectado este bug (mismo
    criterio que `test_remote_router.py::
    test_frame_denied_commits_audit_evidence_before_raising_403`)."""
    tenant_id = uuid.uuid4()
    headers = _headers_con_cloning(app, tenant_id=tenant_id)
    fila_insertada = _voice_consent_row(tenant_id=tenant_id, voice_name="X")
    fake_session.respuestas = [[fila_insertada]]  # solo el INSERT — nunca llega al UPDATE

    response = await client.post(
        "/v1/voz/clones",
        data={"nombre": "X", "attestation": "true"},
        files=_clone_files(),
        headers=headers,
    )

    assert response.status_code == 400
    assert "elevenlabs" in response.json()["detail"].lower()
    sql_insert, params = fake_session.llamadas[0]
    assert "INSERT INTO voice_consents" in sql_insert
    assert params["voice_name"] == "X"
    assert params["tenant_id"] == str(tenant_id)
    # Ningún UPDATE se disparó (la sesión solo vio la llamada del INSERT).
    assert len(fake_session.llamadas) == 1
    assert fake_session.commits == 1


@respx.mock
async def test_crear_clon_happy_path_returns_201_con_fila_attested(
    client, fake_repo, fake_session, app
) -> None:
    respx.post("https://api.elevenlabs.io/v1/voices/add").mock(
        return_value=httpx.Response(200, json={"voice_id": "nuevo-voice-id"})
    )
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    headers = _headers_con_cloning(app, tenant_id=tenant_id, user_id=user_id)
    fake_vault = FakeVault()
    app.dependency_overrides[edecan_deps.get_vault] = lambda: fake_vault
    await _conectar_elevenlabs_tenant(fake_repo, fake_vault, tenant_id=tenant_id)

    fila_insertada = _voice_consent_row(
        tenant_id=tenant_id, user_id=user_id, voice_name="Mi Voz Clonada"
    )
    fila_actualizada = dict(fila_insertada, provider_voice_id="nuevo-voice-id")
    fake_session.respuestas = [[fila_insertada], [fila_actualizada]]

    response = await client.post(
        "/v1/voz/clones",
        data={"nombre": "Mi Voz Clonada", "attestation": "true"},
        files=_clone_files(n_muestras=2),
        headers=headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "attested"
    assert body["attestation"] is True
    assert body["provider_voice_id"] == "nuevo-voice-id"

    sql_insert, params_insert = fake_session.llamadas[0]
    assert "INSERT INTO voice_consents" in sql_insert
    assert params_insert["voice_name"] == "Mi Voz Clonada"

    sql_update, params_update = fake_session.llamadas[1]
    assert "UPDATE voice_consents" in sql_update
    assert params_update["provider_voice_id"] == "nuevo-voice-id"

    acciones_auditadas = [a["action"] for a in fake_repo.audit_log]
    assert "voz.clon.creado" in acciones_auditadas
    # Camino feliz: sin commit explícito — nada lanza después de escribir, así que el
    # commit implícito de `get_session` al terminar la request ya persiste todo (mismo
    # criterio que `commerce.py::confirm_order`, HOTFIXES_PENDIENTES.md punto 8/9).
    assert fake_session.commits == 0


@respx.mock
async def test_crear_clon_elevenlabs_rechaza_returns_400_sin_filtrar_la_key(
    client, fake_repo, fake_session, app
) -> None:
    """Igual que el test anterior pero llegando más lejos: ElevenLabs SÍ está
    conectado, pero rechaza el clon (`VoiceCloningError`) — la evidencia de
    consentimiento tampoco se pierde acá (`HOTFIXES_PENDIENTES.md` punto
    8/9, mismo commit explícito antes del `raise`; ver el comentario en
    `edecan_api/routers/voz_avanzada.py`)."""
    respx.post("https://api.elevenlabs.io/v1/voices/add").mock(
        return_value=httpx.Response(422, text="voice_limit_reached")
    )
    tenant_id = uuid.uuid4()
    headers = _headers_con_cloning(app, tenant_id=tenant_id)
    fake_vault = FakeVault()
    app.dependency_overrides[edecan_deps.get_vault] = lambda: fake_vault
    await _conectar_elevenlabs_tenant(
        fake_repo, fake_vault, tenant_id=tenant_id, api_key="clave-secreta-tenant"
    )
    fila_insertada = _voice_consent_row(tenant_id=tenant_id, voice_name="X")
    fake_session.respuestas = [[fila_insertada]]

    response = await client.post(
        "/v1/voz/clones",
        data={"nombre": "X", "attestation": "true"},
        files=_clone_files(),
        headers=headers,
    )

    assert response.status_code == 400
    assert "voice_limit_reached" in response.text
    assert "clave-secreta-tenant" not in response.text
    # Evidencia de consentimiento (INSERT) comiteada ANTES del raise, no perdida en el
    # rollback automático de `get_tenant_session`.
    sql_insert, _ = fake_session.llamadas[0]
    assert "INSERT INTO voice_consents" in sql_insert
    assert len(fake_session.llamadas) == 1  # nunca llegó al UPDATE de provider_voice_id
    assert fake_session.commits == 1


# ---------------------------------------------------------------------------
# GET /v1/voz/clones
# ---------------------------------------------------------------------------


async def test_listar_clones_requires_authentication(client) -> None:
    response = await client.get("/v1/voz/clones")
    assert response.status_code == 401


async def test_listar_clones_returns_filas_del_tenant(client, fake_session, app) -> None:
    tenant_id = uuid.uuid4()
    headers = _headers_con_cloning(app, tenant_id=tenant_id)
    fila = _voice_consent_row(tenant_id=tenant_id, voice_name="Voz A")
    fake_session.respuestas = [[fila]]

    response = await client.get("/v1/voz/clones", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["voice_name"] == "Voz A"
    sql, params = fake_session.llamadas[0]
    assert "voice_consents" in sql
    assert params["tenant_id"] == str(tenant_id)


async def test_listar_clones_vacio(client, fake_session, app) -> None:
    tenant_id = uuid.uuid4()
    headers = _headers_con_cloning(app, tenant_id=tenant_id)
    fake_session.respuestas = [[]]

    response = await client.get("/v1/voz/clones", headers=headers)

    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# DELETE /v1/voz/clones/{id}
# ---------------------------------------------------------------------------


async def test_revocar_clon_requires_authentication(client) -> None:
    response = await client.delete(f"/v1/voz/clones/{uuid.uuid4()}")
    assert response.status_code == 401


async def test_revocar_clon_not_found_returns_404(client, fake_session, app) -> None:
    tenant_id = uuid.uuid4()
    headers = _headers_con_cloning(app, tenant_id=tenant_id)
    fake_session.respuestas = [[]]  # SELECT vacío

    response = await client.delete(f"/v1/voz/clones/{uuid.uuid4()}", headers=headers)

    assert response.status_code == 404


async def test_revocar_clon_marca_revoked_y_no_borra_la_fila(
    client, fake_repo, fake_session, app
) -> None:
    """Sin `provider_voice_id` (clon nunca se llegó a crear en ElevenLabs):
    revoca localmente sin llamar a ElevenLabs — nunca hace un DELETE de la
    fila, solo un UPDATE de status (ver docstring del router)."""
    tenant_id = uuid.uuid4()
    clon_id = uuid.uuid4()
    headers = _headers_con_cloning(app, tenant_id=tenant_id)
    fila = _voice_consent_row(id=clon_id, tenant_id=tenant_id, provider_voice_id=None)
    fila_revocada = dict(fila, status="revoked")
    fake_session.respuestas = [[fila], [fila_revocada]]

    response = await client.delete(f"/v1/voz/clones/{clon_id}", headers=headers)

    assert response.status_code == 200
    assert response.json()["status"] == "revoked"

    sql_select, _ = fake_session.llamadas[0]
    assert "SELECT" in sql_select
    sql_update, params_update = fake_session.llamadas[1]
    assert "UPDATE voice_consents" in sql_update
    assert "revoked" in sql_update
    assert "DELETE" not in sql_update
    assert params_update["id"] == str(clon_id)

    acciones_auditadas = [a["action"] for a in fake_repo.audit_log]
    assert "voz.clon.revocado" in acciones_auditadas


@respx.mock
async def test_revocar_clon_con_provider_voice_id_intenta_borrar_en_elevenlabs(
    client, fake_repo, fake_session, app
) -> None:
    route = respx.delete("https://api.elevenlabs.io/v1/voices/voice-real-id").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )
    tenant_id = uuid.uuid4()
    clon_id = uuid.uuid4()
    headers = _headers_con_cloning(app, tenant_id=tenant_id)
    fake_vault = FakeVault()
    app.dependency_overrides[edecan_deps.get_vault] = lambda: fake_vault
    await _conectar_elevenlabs_tenant(fake_repo, fake_vault, tenant_id=tenant_id)

    fila = _voice_consent_row(id=clon_id, tenant_id=tenant_id, provider_voice_id="voice-real-id")
    fila_revocada = dict(fila, status="revoked")
    fake_session.respuestas = [[fila], [fila_revocada]]

    response = await client.delete(f"/v1/voz/clones/{clon_id}", headers=headers)

    assert response.status_code == 200
    assert route.called


@respx.mock
async def test_revocar_clon_best_effort_si_elevenlabs_falla_igual_revoca(
    client, fake_repo, fake_session, app
) -> None:
    """Si ElevenLabs rechaza el borrado del clon técnico, la revocación local
    sigue adelante igual (best-effort, ver docstring del router)."""
    respx.delete("https://api.elevenlabs.io/v1/voices/voice-real-id").mock(
        return_value=httpx.Response(404, text="voice_not_found")
    )
    tenant_id = uuid.uuid4()
    clon_id = uuid.uuid4()
    headers = _headers_con_cloning(app, tenant_id=tenant_id)
    fake_vault = FakeVault()
    app.dependency_overrides[edecan_deps.get_vault] = lambda: fake_vault
    await _conectar_elevenlabs_tenant(fake_repo, fake_vault, tenant_id=tenant_id)

    fila = _voice_consent_row(id=clon_id, tenant_id=tenant_id, provider_voice_id="voice-real-id")
    fila_revocada = dict(fila, status="revoked")
    fake_session.respuestas = [[fila], [fila_revocada]]

    response = await client.delete(f"/v1/voz/clones/{clon_id}", headers=headers)

    assert response.status_code == 200
    assert response.json()["status"] == "revoked"
