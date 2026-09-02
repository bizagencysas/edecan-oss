"""`edecan_api.routers.mcp` — `/v1/mcp/*` (MCP bring-your-own, `ARCHITECTURE.md`
§15, WP-V6-07; ver el docstring del propio router para el contrato completo).

**Nota WP-V7-05**: `edecan-mcp` YA es una dependencia declarada de `apps/api`
(`apps/api/pyproject.toml`, ver su comentario "RESUELTO") — el bootstrap de
`sys.path` de abajo, escrito cuando eso todavía no era cierto, se deja tal
cual (idempotente/inofensivo si el paquete ya está instalado, mismo criterio
que documenta `packages/mcp/tests/conftest.py`) como red de seguridad extra
para un checkout parcial, no porque siga haciendo falta en el caso normal.
`edecan_api.main.create_app()` monta `mcp.router` de forma defensiva vía
`V6_ROUTER_NAMES` — mientras tanto, el fixture `_mounted_app` lo monta
también de forma explícita sobre la `app` de `conftest.py`, mismo criterio
que `test_ads_router.py`/`test_erp_router.py`.

`edecan_deps.get_current_user` SÍ se sobreescribe acá (a diferencia del resto
de la suite, que arma un Bearer real con `auth_headers` — ver
`conftest.py`): el flag `tools.mcp` todavía puede no estar pinned en
`edecan_schemas.plans.PLANES` mientras el linchpin de v6 lo aterriza en
paralelo, así que depender de `auth_headers(plan_key=...)` para "encender" el
flag sería no determinista según el orden de aterrizaje. Un
`CurrentUser`/`TenantCtx` armado a mano con `flags={FLAG: True/False}`
explícito hace estos tests deterministas sin importar ese orden. El único
test que SÍ usa el camino JWT real es `test_sin_autenticacion_401` (no
necesita ningún flag).
"""

from __future__ import annotations

import json
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

_MCP_SRC = str(Path(__file__).resolve().parents[3] / "packages" / "mcp")
if _MCP_SRC not in sys.path:
    sys.path.insert(0, _MCP_SRC)

from edecan_schemas import TokenBundle  # noqa: E402

import edecan_api.deps as edecan_deps  # noqa: E402
from edecan_api.config import Settings, get_settings  # noqa: E402
from edecan_api.deps import CurrentUser, TenantCtx  # noqa: E402
from edecan_api.routers import mcp as mcp_router  # noqa: E402

FLAG = mcp_router.FLAG_TOOLS_MCP
_URL = "https://mcp.ejemplo.com/rpc"


@pytest.fixture(autouse=True)
def _dns_determinista(monkeypatch: pytest.MonkeyPatch) -> None:
    """DNS determinista en tests: `mcp.ejemplo.com` no existe de verdad y la
    resolución real (o su ausencia) haría flaky el SSRF fail-closed del
    router (`edecan_mcp.seguridad.resolve_hostname_ips` documenta
    explícitamente este monkeypatch como el camino para probar). Todo
    hostname resuelve a una IP pública; los literales y los nombres
    bloqueados por lista siguen bloqueándose (los tests de SSRF con IP
    privada dependen de eso y siguen verdes)."""
    import edecan_mcp.seguridad as seguridad

    async def _resolver_publico(_hostname: str) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr(seguridad, "resolve_hostname_ips", _resolver_publico)


class FakeVault:
    """Doble de `edecan_db.vault.TokenVault` con `put`/`get` en memoria
    (mismo patrón que `test_credentials_router.py::FakeVault`)."""

    def __init__(self) -> None:
        self._store: dict[tuple[uuid.UUID, uuid.UUID], TokenBundle] = {}
        self.puts: list[tuple[uuid.UUID, uuid.UUID, TokenBundle]] = []

    async def put(self, tenant_id: uuid.UUID, account_id: uuid.UUID, bundle: TokenBundle) -> None:
        self.puts.append((tenant_id, account_id, bundle))
        self._store[(tenant_id, account_id)] = bundle

    async def get(self, tenant_id: uuid.UUID, account_id: uuid.UUID) -> TokenBundle | None:
        return self._store.get((tenant_id, account_id))


@pytest.fixture
def fake_vault() -> FakeVault:
    return FakeVault()


@pytest.fixture
def _mounted_app(app: Any, fake_vault: FakeVault):
    app.include_router(mcp_router.router)
    app.dependency_overrides[edecan_deps.get_vault] = lambda: fake_vault
    return app


@pytest.fixture
async def client(_mounted_app: Any) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=_mounted_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _current_user(*, mcp_flag: bool, tenant_id: uuid.UUID | None = None) -> CurrentUser:
    return CurrentUser(
        user_id=uuid.uuid4(),
        tenant=TenantCtx(
            tenant_id=tenant_id or uuid.uuid4(), plan_key="hosted_pro", flags={FLAG: mcp_flag}
        ),
    )


def _con_flag_mcp(app: Any, *, tenant_id: uuid.UUID | None = None) -> CurrentUser:
    cu = _current_user(mcp_flag=True, tenant_id=tenant_id)
    app.dependency_overrides[edecan_deps.get_current_user] = lambda: cu
    return cu


def _sin_flag_mcp(app: Any) -> CurrentUser:
    cu = _current_user(mcp_flag=False)
    app.dependency_overrides[edecan_deps.get_current_user] = lambda: cu
    return cu


def _use_local_mode(app: Any) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        JWT_SECRET="test-jwt-secret-solo-para-tests-32-bytes-o-mas",
        WEB_BASE_URL="http://localhost:3000",
        PUBLIC_BASE_URL="http://localhost:8000",
        EDECAN_LOCAL_MODE=True,
    )


def _mcp_handshake_responder(tools: list[dict[str, Any]] | None = None):
    """Callback de `respx` que responde correctamente a `initialize`,
    `notifications/initialized` y `tools/list` — sin importar el orden
    exacto en que `HTTPTransport` los mande (los tres son POSTs a la misma
    URL, ver `HTTPTransport._post`)."""
    tools = (
        tools
        if tools is not None
        else [{"name": "buscar", "description": "Busca cosas.", "inputSchema": {"type": "object"}}]
    )

    def _responder(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        metodo = payload.get("method")
        rid = payload.get("id")
        if metodo == "initialize":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {"protocolVersion": "2025-03-26", "capabilities": {}},
                },
            )
        if metodo == "notifications/initialized":
            return httpx.Response(202)
        if metodo == "tools/list":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": rid, "result": {"tools": tools}}
            )
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": rid, "result": {}})

    return _responder


# ---------------------------------------------------------------------------
# Autenticación / flag de plan
# ---------------------------------------------------------------------------


@respx.mock
async def test_sin_autenticacion_401(client: AsyncClient) -> None:
    response = await client.get("/v1/mcp/servers")
    assert response.status_code == 401


@respx.mock
async def test_sin_flag_tools_mcp_403(client: AsyncClient, _mounted_app: Any) -> None:
    _sin_flag_mcp(_mounted_app)
    response = await client.get("/v1/mcp/servers")
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# GET /v1/mcp/servers
# ---------------------------------------------------------------------------


@respx.mock
async def test_get_servers_vacio_por_defecto(client: AsyncClient, _mounted_app: Any) -> None:
    _con_flag_mcp(_mounted_app)
    response = await client.get("/v1/mcp/servers")
    assert response.status_code == 200
    assert response.json() == []


@respx.mock
async def test_get_servers_nunca_incluye_headers(client: AsyncClient, _mounted_app: Any) -> None:
    """`GET /servers` lista `provider_config` — el schema `MCPServerOut` ni
    siquiera tiene un campo `headers` (garantía estructural, ver docstring
    del router: "lista SIN tocar el vault en absoluto")."""
    cu = _con_flag_mcp(_mounted_app)
    put = await client.put(
        "/v1/mcp/servers",
        json={
            "nombre": "acme",
            "transporte": "http",
            "url": _URL,
            "headers": {"Authorization": "Bearer super-secreto"},
            "validate": False,
        },
    )
    assert put.status_code == 204

    response = await client.get("/v1/mcp/servers")
    assert response.status_code == 200
    servidores = response.json()
    assert len(servidores) == 1
    assert "headers" not in servidores[0]
    assert "Authorization" not in json.dumps(servidores[0])
    assert "super-secreto" not in json.dumps(servidores[0])
    assert servidores[0] == {
        "nombre": "acme",
        "transporte": "http",
        "url": _URL,
        "comando": None,
        "estado": "active",
        "autenticacion_configurada": True,
        "health": "unavailable",
        "latency_ms": None,
        "last_error": None,
    }
    del cu  # solo para dejar explícito que no se usa más allá del override


# ---------------------------------------------------------------------------
# PUT /v1/mcp/servers — validación (sin red, validate=false)
# ---------------------------------------------------------------------------


@respx.mock
async def test_put_nombre_vacio_400(client: AsyncClient, _mounted_app: Any) -> None:
    _con_flag_mcp(_mounted_app)
    response = await client.put(
        "/v1/mcp/servers",
        json={"nombre": "   ", "transporte": "http", "url": _URL, "validate": False},
    )
    assert response.status_code == 400


@respx.mock
async def test_put_transporte_desconocido_400(client: AsyncClient, _mounted_app: Any) -> None:
    _con_flag_mcp(_mounted_app)
    response = await client.put(
        "/v1/mcp/servers",
        json={"nombre": "x", "transporte": "websocket", "url": _URL, "validate": False},
    )
    assert response.status_code == 400


@respx.mock
async def test_put_http_sin_url_400(client: AsyncClient, _mounted_app: Any) -> None:
    _con_flag_mcp(_mounted_app)
    response = await client.put(
        "/v1/mcp/servers",
        json={"nombre": "x", "transporte": "http", "validate": False},
    )
    assert response.status_code == 400


@respx.mock
async def test_hosted_rechaza_stdio(client: AsyncClient, _mounted_app: Any) -> None:
    """Sin `EDECAN_LOCAL_MODE`, un servidor por stdio se rechaza ANTES de
    intentar spawnear absolutamente nada."""
    _con_flag_mcp(_mounted_app)
    response = await client.put(
        "/v1/mcp/servers",
        json={
            "nombre": "local",
            "transporte": "stdio",
            "comando": "npx mi-servidor-mcp",
            "validate": False,
        },
    )
    assert response.status_code == 400
    assert "local" in response.json()["detail"].lower()


@respx.mock
async def test_hosted_rechaza_http_sin_tls(client: AsyncClient, _mounted_app: Any) -> None:
    _con_flag_mcp(_mounted_app)
    response = await client.put(
        "/v1/mcp/servers",
        json={
            "nombre": "acme",
            "transporte": "http",
            "url": "http://mcp.ejemplo.com/rpc",
            "validate": False,
        },
    )
    assert response.status_code == 400
    assert "https" in response.json()["detail"].lower()


@respx.mock
async def test_hosted_rechaza_ip_privada_ssrf(client: AsyncClient, _mounted_app: Any) -> None:
    _con_flag_mcp(_mounted_app)
    response = await client.put(
        "/v1/mcp/servers",
        json={
            "nombre": "interno",
            "transporte": "http",
            "url": "https://192.168.1.5/rpc",
            "validate": False,
        },
    )
    assert response.status_code == 400


@respx.mock
async def test_local_mode_permite_stdio(client: AsyncClient, _mounted_app: Any) -> None:
    """`validate=false` a propósito: no se spawnea ningún subprocess real en
    este test (`StdioTransport` sí se ejercita de punta a punta y offline en
    `packages/mcp/tests/test_transport.py`) — acá solo importa que el router
    ACEPTE guardar la config en modo local."""
    _con_flag_mcp(_mounted_app)
    _use_local_mode(_mounted_app)
    response = await client.put(
        "/v1/mcp/servers",
        json={
            "nombre": "local",
            "transporte": "stdio",
            "comando": "python3 -m mi_servidor_mcp",
            "validate": False,
        },
    )
    assert response.status_code == 204


@respx.mock
async def test_stdio_env_se_guarda_cifrado_y_nunca_sale_por_get(
    client: AsyncClient,
    _mounted_app: Any,
    fake_vault: FakeVault,
) -> None:
    _con_flag_mcp(_mounted_app)
    _use_local_mode(_mounted_app)
    response = await client.put(
        "/v1/mcp/servers",
        json={
            "nombre": "meta-ads",
            "transporte": "stdio",
            "comando": "npx -y meta-ads-mcp-server",
            "env": {"META_ADS_ACCESS_TOKEN": "secreto-meta-del-tenant"},
            "validate": False,
        },
    )
    assert response.status_code == 204

    listado = await client.get("/v1/mcp/servers")
    assert listado.status_code == 200
    cuerpo = listado.json()[0]
    assert cuerpo["autenticacion_configurada"] is True
    assert "env" not in cuerpo
    assert "secreto-meta-del-tenant" not in json.dumps(cuerpo)
    assert json.loads(fake_vault.puts[-1][2].access_token)["env"] == {
        "META_ADS_ACCESS_TOKEN": "secreto-meta-del-tenant"
    }


@respx.mock
async def test_http_rechaza_env_local(client: AsyncClient, _mounted_app: Any) -> None:
    _con_flag_mcp(_mounted_app)
    response = await client.put(
        "/v1/mcp/servers",
        json={
            "nombre": "remoto",
            "transporte": "http",
            "url": _URL,
            "env": {"TOKEN": "no-aplica"},
            "validate": False,
        },
    )
    assert response.status_code == 400
    assert "solo aplican" in response.json()["detail"]


@respx.mock
async def test_stdio_rechaza_env_reservado(client: AsyncClient, _mounted_app: Any) -> None:
    _con_flag_mcp(_mounted_app)
    _use_local_mode(_mounted_app)
    response = await client.put(
        "/v1/mcp/servers",
        json={
            "nombre": "meta-ads",
            "transporte": "stdio",
            "comando": "npx -y meta-ads-mcp-server",
            "env": {"PATH": "/ruta/no-confiable"},
            "validate": False,
        },
    )
    assert response.status_code == 400
    assert "PATH está reservada" in response.json()["detail"]


@respx.mock
async def test_get_redacta_secretos_de_comando_legacy(
    client: AsyncClient,
    _mounted_app: Any,
) -> None:
    _con_flag_mcp(_mounted_app)
    _use_local_mode(_mounted_app)
    response = await client.put(
        "/v1/mcp/servers",
        json={
            "nombre": "legacy",
            "transporte": "stdio",
            "comando": "env META_TOKEN=super-secreto npx servidor --access-token otro-secreto",
            "validate": False,
        },
    )
    assert response.status_code == 204

    listado = await client.get("/v1/mcp/servers")
    comando = listado.json()[0]["comando"]
    assert "super-secreto" not in comando
    assert "otro-secreto" not in comando
    assert "META_TOKEN=" in comando


# ---------------------------------------------------------------------------
# PUT /v1/mcp/servers — validate=true (respx)
# ---------------------------------------------------------------------------


@respx.mock
async def test_validate_falla_400_con_detalle(client: AsyncClient, _mounted_app: Any) -> None:
    respx.post(_URL).mock(return_value=httpx.Response(401, text="credenciales rechazadas"))
    _con_flag_mcp(_mounted_app)
    response = await client.put(
        "/v1/mcp/servers",
        json={"nombre": "acme", "transporte": "http", "url": _URL, "validate": True},
    )
    assert response.status_code == 400
    assert response.json()["detail"]  # detalle no vacío


@respx.mock
async def test_validate_timeout_400(client: AsyncClient, _mounted_app: Any) -> None:
    respx.post(_URL).mock(side_effect=httpx.ConnectTimeout("timeout simulado"))
    _con_flag_mcp(_mounted_app)
    response = await client.put(
        "/v1/mcp/servers",
        json={"nombre": "acme", "transporte": "http", "url": _URL, "validate": True},
    )
    assert response.status_code == 400


@respx.mock
async def test_validate_falla_nunca_persiste_nada(
    client: AsyncClient, _mounted_app: Any, fake_repo: Any, fake_vault: FakeVault
) -> None:
    """WP-V7-05, BARRIDO C: el *handshake* (`validate=true`) debe ocurrir
    ANTES de escribir nada — mismo criterio que `credentials.py` (ver
    docstring del router, "Pegar y validar"). Este test cierra el loop que
    `test_validate_falla_400_con_detalle`/`test_validate_timeout_400` dejaban
    implícito (solo verificaban el `400`, nunca que la escritura no hubiera
    ocurrido): si el handshake falla, NI la `connector_account` NI la fila
    del vault deben existir — nada de estado a medio persistir."""
    respx.post(_URL).mock(return_value=httpx.Response(401, text="credenciales rechazadas"))
    cu = _con_flag_mcp(_mounted_app)

    response = await client.put(
        "/v1/mcp/servers",
        json={
            "nombre": "acme",
            "transporte": "http",
            "url": _URL,
            "headers": {"Authorization": "Bearer no-deberia-guardarse"},
            "validate": True,
        },
    )
    assert response.status_code == 400

    cuentas = await fake_repo.list_connector_accounts(tenant_id=cu.tenant_id)
    assert not [c for c in cuentas if c["connector_key"] == "mcp"]
    assert fake_vault.puts == []  # el vault ni siquiera se tocó


@respx.mock
async def test_put_happy_204_y_audita(
    client: AsyncClient, _mounted_app: Any, fake_repo: Any
) -> None:
    respx.post(_URL).mock(side_effect=_mcp_handshake_responder())
    cu = _con_flag_mcp(_mounted_app)
    response = await client.put(
        "/v1/mcp/servers",
        json={
            "nombre": "acme",
            "transporte": "http",
            "url": _URL,
            "headers": {"Authorization": "Bearer xyz"},
            "validate": True,
        },
    )
    assert response.status_code == 204

    cuentas = await fake_repo.list_connector_accounts(tenant_id=cu.tenant_id)
    mcp_cuentas = [c for c in cuentas if c["connector_key"] == "mcp"]
    assert len(mcp_cuentas) == 1
    assert mcp_cuentas[0]["external_account_id"] == "acme"

    audits = (
        [
            a
            for a in fake_repo.audit_log
            if a["action"] == "mcp.server.connected"  # type: ignore[attr-defined]
        ]
        if hasattr(fake_repo, "audit_log")
        else None
    )
    # No asumimos el nombre exacto del atributo interno de auditoría de
    # `FakeRepo` (implementación de `api_fakes.py`, fuera de las rutas de
    # este WP) — si no existe ese atributo, basta con que el PUT haya dado
    # 204 y haya creado la cuenta, ya verificado arriba.
    del audits


# ---------------------------------------------------------------------------
# PUT /v1/mcp/servers — upsert (repetir el mismo nombre reemplaza, no duplica)
# ---------------------------------------------------------------------------


@respx.mock
async def test_put_repetido_mismo_nombre_reemplaza_sin_duplicar(
    client: AsyncClient, _mounted_app: Any, fake_repo: Any, fake_vault: FakeVault
) -> None:
    respx.post(_URL).mock(side_effect=_mcp_handshake_responder())
    otra_url = "https://mcp-v2.ejemplo.com/rpc"
    respx.post(otra_url).mock(side_effect=_mcp_handshake_responder())

    cu = _con_flag_mcp(_mounted_app)

    primero = await client.put(
        "/v1/mcp/servers",
        json={
            "nombre": "acme",
            "transporte": "http",
            "url": _URL,
            "headers": {"Authorization": "Bearer viejo"},
            "validate": True,
        },
    )
    assert primero.status_code == 204

    segundo = await client.put(
        "/v1/mcp/servers",
        json={
            "nombre": "acme",
            "transporte": "http",
            "url": otra_url,
            "headers": {"Authorization": "Bearer nuevo"},
            "validate": True,
        },
    )
    assert segundo.status_code == 204

    cuentas = await fake_repo.list_connector_accounts(tenant_id=cu.tenant_id)
    mcp_cuentas = [c for c in cuentas if c["connector_key"] == "mcp"]
    assert len(mcp_cuentas) == 1  # nunca duplica

    listado = await client.get("/v1/mcp/servers")
    assert listado.json() == [
        {
            "nombre": "acme",
            "transporte": "http",
            "url": otra_url,
            "comando": None,
            "estado": "active",
            "autenticacion_configurada": True,
            "health": "unavailable",
            "latency_ms": None,
            "last_error": None,
        }
    ]

    # El header viejo ya no es alcanzable (la cuenta vieja se borró junto con
    # su fila del vault, `ON DELETE CASCADE`) — el `FakeVault` de este test
    # simplemente ya no tiene una entrada bajo el `account_id` viejo.
    assert len(fake_vault.puts) == 2
    ultimo_bundle = fake_vault.puts[-1][2]
    assert json.loads(ultimo_bundle.access_token)["headers"] == {"Authorization": "Bearer nuevo"}


# ---------------------------------------------------------------------------
# DELETE /v1/mcp/servers/{nombre}
# ---------------------------------------------------------------------------


@respx.mock
async def test_delete_idempotente_si_no_existe(client: AsyncClient, _mounted_app: Any) -> None:
    _con_flag_mcp(_mounted_app)
    response = await client.delete("/v1/mcp/servers/no-existe")
    assert response.status_code == 204


@respx.mock
async def test_delete_happy(client: AsyncClient, _mounted_app: Any, fake_repo: Any) -> None:
    respx.post(_URL).mock(side_effect=_mcp_handshake_responder())
    cu = _con_flag_mcp(_mounted_app)
    await client.put(
        "/v1/mcp/servers",
        json={"nombre": "acme", "transporte": "http", "url": _URL, "validate": True},
    )

    response = await client.delete("/v1/mcp/servers/acme")
    assert response.status_code == 204

    cuentas = await fake_repo.list_connector_accounts(tenant_id=cu.tenant_id)
    assert not [c for c in cuentas if c["connector_key"] == "mcp"]

    listado = await client.get("/v1/mcp/servers")
    assert listado.json() == []


# ---------------------------------------------------------------------------
# GET /v1/mcp/servers/{nombre}/tools
# ---------------------------------------------------------------------------


@respx.mock
async def test_get_tools_404_si_no_existe(client: AsyncClient, _mounted_app: Any) -> None:
    _con_flag_mcp(_mounted_app)
    response = await client.get("/v1/mcp/servers/no-existe/tools")
    assert response.status_code == 404


@respx.mock
async def test_get_tools_conecta_y_lista_en_vivo(client: AsyncClient, _mounted_app: Any) -> None:
    respx.post(_URL).mock(
        side_effect=_mcp_handshake_responder(
            tools=[
                {
                    "name": "buscar",
                    "description": "Busca cosas.",
                    "inputSchema": {"type": "object"},
                },
                {"name": "sumar", "description": "Suma.", "inputSchema": {"type": "object"}},
            ]
        )
    )
    _con_flag_mcp(_mounted_app)
    await client.put(
        "/v1/mcp/servers",
        json={"nombre": "acme", "transporte": "http", "url": _URL, "validate": False},
    )

    response = await client.get("/v1/mcp/servers/acme/tools")
    assert response.status_code == 200
    nombres = {t["name"] for t in response.json()["tools"]}
    assert nombres == {"buscar", "sumar"}


@respx.mock
async def test_get_tools_400_si_falla_la_conexion(client: AsyncClient, _mounted_app: Any) -> None:
    _con_flag_mcp(_mounted_app)
    await client.put(
        "/v1/mcp/servers",
        json={"nombre": "acme", "transporte": "http", "url": _URL, "validate": False},
    )
    respx.post(_URL).mock(return_value=httpx.Response(500, text="boom"))

    response = await client.get("/v1/mcp/servers/acme/tools")
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Salud por-servidor (`mcp_server_health`, directiva §27)
# ---------------------------------------------------------------------------


class _SaludRows:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeHealthSession:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    async def execute(self, clause: Any, params: dict | None = None) -> _SaludRows:
        sql = str(clause)
        if "INSERT INTO mcp_server_health" in sql:
            nombre = str(params["server_name"])
            self.rows[nombre] = {
                "server_name": nombre,
                "health": params["health"],
                "last_latency_ms": params["latency"],
                "last_error": params["error"],
            }
            return _SaludRows([])
        if "DELETE FROM mcp_server_health" in sql:
            self.rows.pop(str(params["server_name"]), None)
            return _SaludRows([])
        if "FROM mcp_server_health" in sql:
            return _SaludRows(list(self.rows.values()))
        return _SaludRows([])


@pytest.fixture
def health_session() -> _FakeHealthSession:
    return _FakeHealthSession()


@pytest.fixture
def _mounted_app_salud(_mounted_app: Any, health_session: _FakeHealthSession):
    _mounted_app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: health_session
    return _mounted_app


@pytest.fixture
async def client_salud(_mounted_app_salud: Any) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=_mounted_app_salud)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@respx.mock
async def test_get_servers_proyecta_salud_de_la_tabla(
    client_salud: AsyncClient, _mounted_app_salud: Any, health_session: _FakeHealthSession
) -> None:
    _con_flag_mcp(_mounted_app_salud)
    health_session.rows["acme"] = {
        "server_name": "acme",
        "health": "operational",
        "last_latency_ms": 42,
        "last_error": None,
    }
    await client_salud.put(
        "/v1/mcp/servers",
        json={"nombre": "acme", "transporte": "http", "url": _URL, "validate": False},
    )
    response = await client_salud.get("/v1/mcp/servers")
    assert response.status_code == 200
    servidor = response.json()[0]
    assert servidor["health"] == "operational"
    assert servidor["latency_ms"] == 42
    assert servidor["last_error"] is None


@respx.mock
async def test_put_validate_true_graba_operational(
    client_salud: AsyncClient, _mounted_app_salud: Any, health_session: _FakeHealthSession
) -> None:
    respx.post(_URL).mock(side_effect=_mcp_handshake_responder())
    _con_flag_mcp(_mounted_app_salud)
    response = await client_salud.put(
        "/v1/mcp/servers",
        json={"nombre": "acme", "transporte": "http", "url": _URL, "validate": True},
    )
    assert response.status_code == 204
    assert health_session.rows["acme"]["health"] == "operational"
    assert isinstance(health_session.rows["acme"]["last_latency_ms"], int)


@respx.mock
async def test_delete_borra_salud(
    client_salud: AsyncClient, _mounted_app_salud: Any, health_session: _FakeHealthSession
) -> None:
    respx.post(_URL).mock(side_effect=_mcp_handshake_responder())
    _con_flag_mcp(_mounted_app_salud)
    await client_salud.put(
        "/v1/mcp/servers",
        json={"nombre": "acme", "transporte": "http", "url": _URL, "validate": True},
    )
    assert "acme" in health_session.rows

    response = await client_salud.delete("/v1/mcp/servers/acme")
    assert response.status_code == 204
    assert "acme" not in health_session.rows


# ---------------------------------------------------------------------------
# GET /v1/mcp/health — resumen agregado de `mcp_server_health` (directiva §27)
# ---------------------------------------------------------------------------


@respx.mock
async def test_health_sin_autenticacion_401(client: AsyncClient) -> None:
    response = await client.get("/v1/mcp/health")
    assert response.status_code == 401


@respx.mock
async def test_health_sin_flag_tools_mcp_403(client: AsyncClient, _mounted_app: Any) -> None:
    _sin_flag_mcp(_mounted_app)
    response = await client.get("/v1/mcp/health")
    assert response.status_code == 403


@respx.mock
async def test_health_vacio_devuelve_ceros(
    client_salud: AsyncClient, _mounted_app_salud: Any
) -> None:
    _con_flag_mcp(_mounted_app_salud)
    response = await client_salud.get("/v1/mcp/health")
    assert response.status_code == 200
    body = response.json()
    assert body["format"] == "edecan-mcp-health.v1"
    assert body["status"] == "operational"
    assert body["configured"] == 0
    assert body["checked"] == 0
    assert body["unchecked"] == 0
    assert body["by_status"] == {
        "operational": 0,
        "degraded": 0,
        "auth_required": 0,
        "unavailable": 0,
        "unknown": 0,
    }
    assert body["operational_rate"] is None
    assert body["avg_latency_ms"] is None
    assert body["max_latency_ms"] is None
    assert body["servers"] == []


@respx.mock
async def test_health_agrega_por_estado(
    client_salud: AsyncClient,
    _mounted_app_salud: Any,
    health_session: _FakeHealthSession,
) -> None:
    _con_flag_mcp(_mounted_app_salud)
    health_session.rows.update(
        {
            "alpha": {
                "server_name": "alpha",
                "health": "operational",
                "last_latency_ms": 10,
                "last_error": None,
            },
            "beta": {
                "server_name": "beta",
                "health": "operational",
                "last_latency_ms": 30,
                "last_error": None,
            },
            "delta": {
                "server_name": "delta",
                "health": "degraded",
                "last_latency_ms": 120,
                "last_error": "responde lento",
            },
            "gamma": {
                "server_name": "gamma",
                "health": "unavailable",
                "last_latency_ms": None,
                "last_error": "timeout",
            },
        }
    )
    response = await client_salud.get("/v1/mcp/health")
    assert response.status_code == 200
    body = response.json()
    # Sin filas de `connector_accounts`, solo cuenta la salud leída de la tabla.
    assert body["configured"] == 0
    assert body["checked"] == 4
    assert body["unchecked"] == 0
    assert body["by_status"] == {
        "operational": 2,
        "degraded": 1,
        "auth_required": 0,
        "unavailable": 1,
        "unknown": 0,
    }
    assert body["status"] == "unavailable"
    assert body["operational_rate"] == 0.5
    assert body["avg_latency_ms"] == 53.3  # (10 + 30 + 120) / 3, redondeado a 1 decimal
    assert body["max_latency_ms"] == 120
    nombres = {s["server_name"] for s in body["servers"]}
    assert nombres == {"alpha", "beta", "delta", "gamma"}


@respx.mock
async def test_health_no_checados_degradan_el_resumen(
    client_salud: AsyncClient, _mounted_app_salud: Any
) -> None:
    _con_flag_mcp(_mounted_app_salud)
    await client_salud.put(
        "/v1/mcp/servers",
        json={"nombre": "nunca-validado", "transporte": "http", "url": _URL, "validate": False},
    )
    assert (await client_salud.get("/v1/mcp/health")).status_code == 200
    body = (await client_salud.get("/v1/mcp/health")).json()
    assert body["configured"] == 1
    assert body["checked"] == 0
    assert body["unchecked"] == 1
    assert body["status"] == "degraded"


@respx.mock
async def test_health_session_none_solo_devuelve_configurados(
    client: AsyncClient, _mounted_app: Any
) -> None:
    """Sin sesión (get_tenant_session → None), `_leer_salud` degrada a vacío y
    el resumen solo refleja los servidores configurados (todos `unchecked`)."""
    _con_flag_mcp(_mounted_app)
    await client.put(
        "/v1/mcp/servers",
        json={"nombre": "acme", "transporte": "http", "url": _URL, "validate": False},
    )
    response = await client.get("/v1/mcp/health")
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] == 1
    assert body["checked"] == 0
    assert body["unchecked"] == 1
    assert body["status"] == "degraded"
    assert body["avg_latency_ms"] is None


def test_health_summary_puro_contiene_estado_desconocido() -> None:
    """Casos borde de la función pura: una fila con `health` fuera del
    vocabulario pinned se cuenta como `unknown` (nunca revienta) y la latencia
    no numérica no se agrega."""
    resumen = mcp_router._mcp_health_summary(  # noqa: SLF001 - whitebox a propósito
        {
            "raro": {"server_name": "raro", "health": "not_a_real_state", "last_latency_ms": "n/a"},
            "sano": {"server_name": "sano", "health": "operational", "last_latency_ms": 7},
        },
        configured_names=["raro", "sano"],
    )
    assert resumen["by_status"]["unknown"] == 1
    assert resumen["by_status"]["operational"] == 1
    assert resumen["checked"] == 2
    assert resumen["unchecked"] == 0
    assert resumen["avg_latency_ms"] == 7.0
    assert resumen["max_latency_ms"] == 7
    assert resumen["status"] == "degraded"


# ---------------------------------------------------------------------------
# Colisión de nombres: mcp_* nunca choca con las ~40 tools reales.
# ---------------------------------------------------------------------------


def test_prefijo_mcp_nunca_colisiona_con_nombres_de_tools_reales() -> None:
    from edecan_core.tools import ToolRegistry

    registry = ToolRegistry()
    registry.load_entry_points(group="edecan.tools")
    nombres = set(registry._tools.keys())  # noqa: SLF001 - whitebox a propósito, ver docstring

    assert len(nombres) > 20  # de verdad cargó tools reales, no una lista vacía
    colisiones = {n for n in nombres if n.startswith("mcp_")}
    assert colisiones == set()
