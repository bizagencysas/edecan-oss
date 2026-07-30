"""WP-V7-05 — MCP bring-your-own: barrido dedicado de `edecan_api.routers.mcp`
+ `packages/mcp` (`ARCHITECTURE.md` §15.g; `docs/mcp.md`; `docs/cumplimiento/
barrido-v7-mcp.md` para el informe completo). `mcp.py` no estaba cubierto por
ningún barrido v6 — este archivo pinnea, con tests ejecutables, lo que la
auditoría confirmó correcto y cierra el ÚNICO hueco de cobertura real que
encontró (BARRIDO B: el camino `confirm_tool_call` para una tool `mcp_*` con
el flag `tools.mcp` apagado, que `test_conversations_mcp.py` todavía no
cubría — ver su docstring, "ya existe; extiende en TU archivo test_v7_sweep_
mcp.py si falta el caso de flag apagado").

## BARRIDO A (aislamiento) y BARRIDO C (evidencia) — dónde viven sus anclas

El aislamiento del subproceso `stdio` (allowlist de entorno) y el SSRF de
`http`/redirects viven en `packages/mcp/` (paquete, no HTTP) — sus anclas
están en `packages/mcp/tests/test_transport.py` (`test_stdio_transport_el_
subproceso_solo_hereda_path_y_home`, `test_http_transport_no_sigue_redirects
_automaticamente`) y `packages/mcp/tests/test_seguridad.py`. La evidencia de
auditoría (`PUT`/`DELETE` -> `audit_log`, handshake ANTES de persistir) tiene
su ancla nueva en `apps/api/tests/test_mcp_router.py::
test_validate_falla_nunca_persiste_nada`. Este archivo se enfoca en lo que
esos dos no cubren: BARRIDO B de punta a punta (flag de plan) y las dos
verificaciones "cross-package" que, por convención de este repo
(`test_v6_sweep_flags.py`), viven en un archivo de barrido dedicado en vez de
en el router/paquete de origen.

## BARRIDO B.3 (límite de servidores por tenant) — sin código nuevo, a propósito

Ni `ARCHITECTURE.md` §15.g ni `edecan_schemas.plans` documentan ningún límite
al número de servidores MCP que un tenant puede registrar (a diferencia de,
p. ej., `limits.phone_numbers`/`limits.seats`, que sí son límites de plan
pinned). Instrucción explícita del paquete de trabajo: "si hay límite
documentado, que se aplique en TODAS las superficies" — como NO existe
ninguno, no se inventa uno acá (`docs/cumplimiento/barrido-v7-mcp.md` deja la
observación operativa: sin límite, un tenant que conecte muchos servidores
paga un costo de latencia real en cada recálculo de `extra_tools`, ver
`apps/api/edecan_api/deps.py::_build_mcp_tools_for_tenant`, porque
`construir_tools_mcp` hace un handshake+`tools/list` POR SERVIDOR, en serie —
observación para una ola futura, no un hallazgo de seguridad).
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any

from conftest import auth_headers
from edecan_core.tools.base import Tool, ToolContext, ToolResult

_MCP_SRC = str(Path(__file__).resolve().parents[3] / "packages" / "mcp")
if _MCP_SRC not in sys.path:
    sys.path.insert(0, _MCP_SRC)


async def _create_conversation(client: Any, headers: dict[str, str]) -> str:
    response = await client.post("/v1/conversations", json={}, headers=headers)
    assert response.status_code == 201
    return response.json()["id"]


# ---------------------------------------------------------------------------
# Consistencia entre paquetes: el flag hardcodeado en `edecan_mcp.tool_adapter`
# debe seguir siendo LITERALMENTE el mismo string que el flag pinned de
# `edecan_schemas.plans` — dos literales independientes en dos paquetes
# distintos que hoy coinciden por convención, no por una única fuente de
# verdad compartida (`edecan_mcp` no depende de `edecan_schemas.plans`, a
# propósito, ver `packages/mcp/pyproject.toml`). Mismo espíritu que las
# comparaciones cross-package de `test_v6_sweep_flags.py`.
# ---------------------------------------------------------------------------


def test_flag_hardcodeado_en_tool_adapter_coincide_con_el_flag_pinned_del_plan() -> None:
    from edecan_mcp.tool_adapter import REQUIRES_FLAG_MCP
    from edecan_schemas.plans import FLAG_TOOLS_MCP

    assert REQUIRES_FLAG_MCP == FLAG_TOOLS_MCP == "tools.mcp"


def test_flag_hardcodeado_en_router_mcp_coincide_con_el_flag_pinned_del_plan() -> None:
    from edecan_schemas.plans import FLAG_TOOLS_MCP

    from edecan_api.routers import mcp as mcp_router

    assert mcp_router.FLAG_TOOLS_MCP == FLAG_TOOLS_MCP


# ---------------------------------------------------------------------------
# BARRIDO B.1 — los 4 endpoints de `/v1/mcp/*` exigen `_require_tools_mcp`,
# verificado por introspección REAL del grafo de dependencias de FastAPI (no
# un doble/simulación) — si algún endpoint nuevo se agrega a `mcp.py` sin
# pasar por ese gate, o sin agregarse a la lista pinned de abajo, este test
# falla explícitamente en vez de dejarlo pasar en silencio.
# ---------------------------------------------------------------------------


def test_los_4_endpoints_de_mcp_exigen_require_tools_mcp() -> None:
    from edecan_api.routers import mcp as mcp_router

    rutas_cubiertas: set[tuple[str, frozenset[str]]] = set()
    for route in mcp_router.router.routes:
        dependants_call = {d.call for d in route.dependant.dependencies}
        assert mcp_router._require_tools_mcp in dependants_call, (
            f"{route.path} ({sorted(route.methods or [])}) no exige _require_tools_mcp"
        )
        rutas_cubiertas.add((route.path, frozenset(route.methods or [])))

    # Lista pinned de endpoints conocidos — un endpoint nuevo que no aparezca
    # acá rompe esta aserción a propósito, para que alguien la actualice
    # explícitamente como parte de ESE cambio (mismo criterio de "romper en
    # vez de fallar en silencio" que el resto de tests pinned de este repo).
    assert rutas_cubiertas == {
        ("/v1/mcp/servers", frozenset({"GET"})),
        ("/v1/mcp/servers", frozenset({"PUT"})),
        ("/v1/mcp/servers/{nombre}", frozenset({"DELETE"})),
        ("/v1/mcp/servers/{nombre}/tools", frozenset({"GET"})),
    }


# ---------------------------------------------------------------------------
# BARRIDO B.2 — `confirm_tool_call` con una tool `mcp_*` y el flag `tools.mcp`
# apagado. `test_conversations_mcp.py` ya cubre el camino feliz (flag on,
# `POST .../confirm` ejecuta la tool MCP recalculada vía `extra_tools`) y el
# 409 de "la tool ya no existe" — pero NINGÚN test existente ejercita una
# tool `mcp_*` pendiente de confirmar cuyo flag de plan está apagado. Modelo
# de precio de pago único (`edecan_schemas.plans` docstring): `tools.mcp` ya
# está en `True` en las 4 entradas de `PLANES` por igual, así que el
# escenario "defensa en profundidad" (flag apagado incluso cuando
# `get_mcp_tools_for_tenant` lo ignora) ya no es alcanzable con ningún
# `plan_key` real y se retiró. El escenario que sí sigue vigente, con la
# garantía real de que la tool JAMÁS se ejecuta:
# ---------------------------------------------------------------------------


class _FakeMCPToolQueRegistra(Tool):
    name = "mcp_acme_buscar"
    description = "[MCP:Acme] Tool remota de prueba."
    input_schema = {"type": "object", "properties": {}}
    dangerous = True
    requires_flags = frozenset({"tools.mcp"})

    def __init__(self, ejecuciones: list[dict[str, Any]]) -> None:
        self._ejecuciones = ejecuciones

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        self._ejecuciones.append(args)
        return ToolResult(content="NO debería ejecutarse en ninguno de estos tests")


async def test_confirm_mcp_con_flag_apagado_hoy_da_409_via_extra_tools_vacias(
    client: Any, monkeypatch: Any, fake_redis: Any
) -> None:
    """Comportamiento REAL de hoy: `get_mcp_tools_for_tenant` (`edecan_api.
    deps`) corta en seco ANTES de construir ninguna tool cuando `tools.mcp`
    está apagado (`if not current_user.tenant.flags.get(_MCP_TOOLS_FLAG,
    False): return []`) — así que, tras un downgrade de plan ocurrido
    DESPUÉS de que el turno original propuso la tool y ANTES de que el
    humano confirmara (ventana `PENDING_CONFIRMATION_TTL_SECONDS`, 15 min,
    mismo escenario que documenta `HOTFIXES_PENDIENTES.md` para el hallazgo
    CRITICAL de `confirm_tool_call`), `extra_tools` recalculadas en el
    `POST .../confirm` es `[]`: la tool nunca se encuentra -> `409` ("ya no
    disponible"), no `403`. Este test fija cuál de los dos códigos ocurre
    HOY para que un cambio futuro en el orden de resolución no lo mueva sin
    que alguien se dé cuenta — la garantía que de verdad importa (la tool
    JAMÁS se ejecuta) se verifica en los dos tests de este archivo."""
    import edecan_api.routers.conversations as conversations_module

    async def _fake_get_mcp_tools_flag_apagado(request: Any, current_user: Any) -> list[Any]:
        return []  # mismo resultado que produce el `get_mcp_tools_for_tenant` real

    monkeypatch.setattr(
        conversations_module, "get_mcp_tools_for_tenant", _fake_get_mcp_tools_flag_apagado
    )

    tenant_id = uuid.uuid4()
    # hosted_basic: tools.mcp=False (edecan_schemas.plans.PLANES).
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    conversation_id = await _create_conversation(client, headers)

    await conversations_module._store_pending_confirmation(
        fake_redis,
        tenant_id=tenant_id,
        conversation_id=uuid.UUID(conversation_id),
        tool_call_id="call-mcp-downgrade",
        name="mcp_acme_buscar",
        args={"q": "hola"},
    )

    response = await client.post(
        f"/v1/conversations/{conversation_id}/confirm",
        json={"tool_call_id": "call-mcp-downgrade", "approved": True},
        headers=headers,
    )

    assert response.status_code == 409


async def test_confirm_mcp_con_flag_prendido_si_ejecuta(
    client: Any, monkeypatch: Any, fake_redis: Any
) -> None:
    """Contraparte de los dos tests de arriba: con `tools.mcp=True` de
    verdad (`hosted_pro`), la MISMA tool SÍ se ejecuta — confirma que los
    dos tests anteriores bloquean por el flag específicamente, no por algún
    otro efecto secundario de este archivo (mismo patrón "negativo + positivo"
    que `HOTFIXES_PENDIENTES.md` documenta para el fix original)."""
    import edecan_api.routers.conversations as conversations_module

    ejecuciones: list[dict[str, Any]] = []
    fake_tool = _FakeMCPToolQueRegistra(ejecuciones)

    async def _fake_get_mcp_tools(request: Any, current_user: Any) -> list[Any]:
        return [fake_tool]

    monkeypatch.setattr(conversations_module, "get_mcp_tools_for_tenant", _fake_get_mcp_tools)

    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_pro")
    conversation_id = await _create_conversation(client, headers)

    await conversations_module._store_pending_confirmation(
        fake_redis,
        tenant_id=tenant_id,
        conversation_id=uuid.UUID(conversation_id),
        tool_call_id="call-mcp-ok",
        name="mcp_acme_buscar",
        args={"q": "hola"},
    )

    response = await client.post(
        f"/v1/conversations/{conversation_id}/confirm",
        json={"tool_call_id": "call-mcp-ok", "approved": True},
        headers=headers,
    )

    assert response.status_code == 200
    assert ejecuciones == [{"q": "hola"}]
