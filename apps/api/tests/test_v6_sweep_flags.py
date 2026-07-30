"""WP-V6-02 — Barrido patrón A: matriz pinned `Tool.requires_flags` <-> gate
del router dedicado (`ARCHITECTURE.md` §0, §10.7, §10.13).

## El patrón (referencia canónica)

`HOTFIXES_PENDIENTES.md` sección "RESUELTO (2026-07-09): `usar_computadora`
se saltaba `companion.remote_input`/`companion.ide`" es el caso real de este
bug: `edecan_companion.actions.ACTIONS` era un dispatch table COMPARTIDO por
tres superficies (`usar_computadora`, `routers/ide.py`, `routers/remote.py`)
— dos de ellas exigían un flag de plan fino antes de reenviar la acción, la
tercera solo exigía el flag base `companion`, así que un tenant con el flag
fino apagado podía alcanzar la acción igual, por chat. Generalizado: **una
tool/endpoint/handler que alcanza un dispatch table, registry o capa de
servicio COMPARTIDA con otra superficie, donde la otra superficie exige un
flag de plan de grano fino y esta no.**

## Qué hace este archivo (y por qué rompe a propósito ARCHITECTURE.md §10.1)

A diferencia de `test_ads_router.py`/`test_erp_router.py`/etc. (que prueban
CADA router en aislamiento vía HTTP, con fakes), este archivo importa las
clases `Tool` REALES de cada paquete de herramientas Y los routers REALES, y
compara programáticamente que el flag que exige la tool (`requires_flags`,
el gate de "¿se le ofrece esta tool al modelo?", `ToolRegistry.specs()`) es
EXACTAMENTE el mismo flag que exige el router dedicado que gestiona esa
misma capacidad por HTTP (el gate de "¿puede este tenant usar esta
capacidad desde la UI?"). Es una excepción deliberada a "los tests no
importan paquetes hermanos" (§10.1): el propósito explícito de este archivo
es cruzar paquetes hermanos — un fake nunca reproduce un desync real entre
dos paquetes reales, así que usar fakes aquí lo haría inútil. Mismo criterio
que ya aplican `test_ads_router.py`/`test_vehiculos_router.py` (que sí
importan `edecan_api.routers.<x>` real) llevado un paso más allá: además del
router, se importa también la tool hermana.

Todos los paquetes de tools referenciados aquí (`edecan_ads`, `edecan_business`,
`edecan_commerce`, `edecan_messaging`, `edecan_travel`, `edecan_voice`,
`edecan_browser`, `edecan_creative`, `edecan_toolkit`, `edecan_automations`,
`edecan_agents`, `edecan_smarthome`, `edecan_skills`) ya son dependencias
declaradas de `apps/api` (`apps/api/pyproject.toml`) — se importan directo,
sin `sys.path` manual. `edecan_vehicles` forma parte del núcleo OSS. La capa
`edecan_premium` es opcional y no se distribuye en este repositorio: los
casos que cruzan sus tools se agregan solo cuando el paquete está instalado.

## Rutas fuera del alcance de escritura de este WP (solo lectura)

`packages/agents/`, `packages/vehicles/`, `premium/`, `apps/api/edecan_api/
routers/{ads,commerce,erp,rrhh,mensajes,viajes,voice,voz_avanzada,missions,
vehiculos,ide,remote,consents,connectors}.py` — este archivo los IMPORTA
para verificar, nunca los edita (ver el encabezado del paquete de trabajo).

## Los DOS hallazgos que este barrido encontró FUERA del alcance de escritura

Ninguno de los dos vivía en un archivo que este WP pudiera tocar
(`packages/core/edecan_core/agent.py` y `packages/agents/edecan_agents/
tools.py`, ninguno de los dos en la lista de rutas permitidas) — se
documentan en detalle en `docs/seguridad-modelo-amenazas.md` y se PINNEAN
aquí con un test ejecutable cada uno, para que sean imposibles de pasar por
alto y para que, el día que alguien los corrija, la prueba correspondiente
falle y le avise que actualice/borre el pin como parte de ESE cambio:

1. **RESUELTO** — `Agent._run_turn` (`packages/core/edecan_core/agent.py`)
   antes nunca volvía a verificar `requires_flags` al EJECUTAR una tool —
   solo lo hacía `ToolRegistry.specs(flags)`, usado exclusivamente para
   decidir qué se OFRECE al modelo (`CompletionRequest.tools`).
   `resolved_calls = [(call, self._registry.get(call.name)) for call in
   tool_calls]` resolvía por NOMBRE contra el registro COMPLETO (sin filtrar
   por `flags`) y ejecutaba cualquier tool no-`dangerous` (o `dangerous` ya
   aprobada) que el modelo pidiera, exista o no en la lista que se le
   ofreció. Fix: `_con_flags_satisfechos` (`agent.py`) revalida
   `tool.requires_flags` contra `flags` sobre CADA tool ya resuelta, antes de
   la 1ª pasada (gate de `dangerous`) — una tool sin sus flags se trata como
   "herramienta desconocida", igual que ya hacía `extra_tools`. Como
   `RestrictedRegistry.get()` (`packages/agents/edecan_agents/
   registry_view.py`, usado por `Orchestrator` para pasos de misión) es el
   ÚNICO camino por el que algo llama `.get()` sobre ella (siempre a través
   de `Agent._run_turn`, nunca directo), el mismo fix cierra también ese
   hueco sin tocar `registry_view.py`. Pin principal:
   `packages/core/tests/test_agent.py` (`test_tool_con_flag_no_satisfecho_no_se_ejecuta`
   y sus vecinas); este archivo conserva
   `test_agent_run_turn_no_ejecuta_una_tool_cuyo_flag_no_esta_satisfecho` más
   abajo como regresión cruzada. Detalle completo (impacto por tool
   `dangerous`/no-`dangerous`, vector de explotación vía inyección de prompt
   indirecta) en `docs/seguridad-modelo-amenazas.md`, Hallazgo 1.

2. **RESUELTO** — `DelegarMisionTool` (`packages/agents/edecan_agents/
   tools.py`) revisaba el flag base `agents.missions` pero nunca
   `LIMIT_MISSIONS_PER_DAY`, a diferencia de `POST /v1/missions`
   (`missions.py::_check_missions_quota`, WP-V6-10) — un tenant con
   `agents.missions=True` podía crear misiones sin límite por chat aunque ya
   había agotado su cupo diario del plan. No estaba relacionado con el
   hallazgo 1 (ese fix ya garantiza que sin `agents.missions` la tool ni
   siquiera se ejecuta): el límite diario en sí seguía sin aplicarse aun con
   el flag base activo. Fix: `DelegarMisionTool._cupo_disponible` replica el
   mismo criterio que `_check_missions_quota` (mismo flag, mismo `-1` =
   ilimitado, mismo `SELECT COUNT(*) FROM agent_missions` desde medianoche
   UTC) antes de insertar/encolar. Pin (aserción invertida, antes
   `test_HALLAZGO_...`):
   `test_delegar_mision_revisa_limits_missions_per_day` más abajo;
   cobertura de comportamiento completa en
   `packages/agents/tests/test_tools.py`.
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

import edecan_skills
import pytest
from edecan_ads.tools import AdsPrepararCampanaTool, AdsResumenTool
from edecan_agents.tools import DelegarMisionTool
from edecan_automations.tools import GestionarAutomatizacionTool
from edecan_browser.tools import CompararPreciosTool, ExtraerDatosWebTool, NavegarWebTool
from edecan_business.tools import (
    EstadoInventarioTool,
    GestionarEmpleadoTool,
    GestionarInventarioTool,
    PrepararNominaTool,
    RegistrarAusenciaTool,
)
from edecan_commerce.tools import (
    CotizarActivoTool,
    GestionarPresupuestoTool,
    PrepararOrdenTool,
    PrepararPagoTool,
)
from edecan_creative.tools import CrearPodcastTool, GenerarEfectoSonidoTool, GenerarImagenTool
from edecan_messaging.tools import EnviarMensajeTool, LeerMensajesTool

# La capa comercial es un plugin opcional. El export Apache-2.0 debe poder
# ejecutar toda su suite sin tener ese paquete privado en disco.
try:
    from edecan_premium.tools import EnviarSmsTool, LanzarCampanaTool, LlamarContactoTool
except ImportError:  # esperado en el checkout público
    _PREMIUM_TOOL_CLASSES: tuple[type, ...] = ()
else:
    _PREMIUM_TOOL_CLASSES = (LlamarContactoTool, EnviarSmsTool, LanzarCampanaTool)
from edecan_schemas.plans import (
    FLAG_AGENTS_MISSIONS,
    FLAG_AUTOMATIONS_RULES,
    FLAG_COMMERCE_ORDERS,
    FLAG_COMPANION_IDE,
    FLAG_COMPANION_REMOTE_INPUT,
    FLAG_COMPANION_REMOTE_VIEW,
    FLAG_CONNECTORS_MESSAGING,
    FLAG_CONNECTORS_SOCIAL,
    FLAG_ERP_HR,
    FLAG_ERP_INVENTORY,
    FLAG_TOOLS_ADS,
    FLAG_TOOLS_BROWSER,
    FLAG_TOOLS_IMAGES,
    FLAG_TOOLS_PODCAST,
    FLAG_TOOLS_TRAVEL,
    FLAG_TOOLS_VEHICLES,
    FLAG_VOICE_TELEPHONY,
    FLAG_VOICE_WEB,
)
from edecan_schemas.queue import JOB_TYPES
from edecan_smarthome import get_all_tools as smarthome_get_all_tools
from edecan_toolkit import computadora as computadora_module
from edecan_toolkit.contenido import PublicarSocialTool
from edecan_travel.tools import (
    BuscarHotelesTool,
    BuscarVuelosTool,
    EstadoVueloTool,
    PrepararReservaTool,
    RastrearPaqueteTool,
)
from edecan_vehicles.tools import VehiculoControlarTool, VehiculoEstadoTool
from edecan_voice.tools import ListarVocesTool, SintetizarVozTool
from fastapi import HTTPException

from edecan_api.deps import CurrentUser, TenantCtx

# Routers propios de este WP (ya auditados; ninguno necesitó un fix): ads,
# automations, companion, hooks, skills, smarthome. El resto son routers
# hermanos de SOLO LECTURA -- dueños reales: WP-V6-03 (consents, connectors,
# premium vía las tools de arriba), WP-V6-04 (voz_avanzada), WP-V6-10
# (missions) -- este archivo los IMPORTA para verificar, nunca los edita
# (ver "Rutas fuera del alcance de escritura" en el docstring del módulo).
# `hooks` no se referencia más abajo (ese router ya tiene su propia
# cobertura extensa en `test_hooks_router.py`) -- se deja fuera del import
# para no arrastrar un `noqa: F401` sin necesidad real.
from edecan_api.routers import ads as ads_router
from edecan_api.routers import automations as automations_router
from edecan_api.routers import commerce as commerce_router
from edecan_api.routers import companion as companion_router
from edecan_api.routers import connectors as connectors_router
from edecan_api.routers import content_studio as content_studio_router
from edecan_api.routers import erp as erp_router
from edecan_api.routers import ide as ide_router
from edecan_api.routers import mensajes as mensajes_router
from edecan_api.routers import missions as missions_router
from edecan_api.routers import remote as remote_router
from edecan_api.routers import rrhh as rrhh_router
from edecan_api.routers import skills as skills_router
from edecan_api.routers import smarthome as smarthome_router
from edecan_api.routers import vehiculos as vehiculos_router
from edecan_api.routers import viajes as viajes_router
from edecan_api.routers import voice as voice_router
from edecan_api.routers import voz_avanzada as voz_avanzada_router

if _PREMIUM_TOOL_CLASSES:
    from edecan_api.routers import consents as consents_router

# Nota: NO se declara `pytestmark = pytest.mark.asyncio` -- este workspace
# corre `asyncio_mode = "auto"` (`pyproject.toml`/`apps/api/pyproject.toml`,
# `[tool.pytest.ini_options]`), así que cualquier `async def test_*` ya se
# ejecuta como test asíncrono sin marcador explícito; este archivo mezcla
# tests síncronos y asíncronos, y un `pytestmark` a nivel de módulo los
# marcaría a TODOS (pytest-asyncio avisa/rompe con los síncronos).

_ROUTERS_DIR = Path(__file__).resolve().parents[1] / "edecan_api" / "routers"


# ---------------------------------------------------------------------------
# Helpers: invocar un gate `_require_*`/`require_*` REAL sin FastAPI/HTTP.
# ---------------------------------------------------------------------------


def _fake_user(flags: dict[str, Any]) -> CurrentUser:
    return CurrentUser(
        user_id=uuid4(),
        tenant=TenantCtx(tenant_id=uuid4(), plan_key="plan-de-prueba-v6-sweep", flags=dict(flags)),
    )


async def _invoke_gate(gate: Any, user: CurrentUser) -> None:
    """Llama un gate REAL de un router pasándole un `CurrentUser` fabricado a
    mano — sin FastAPI ni HTTP, así se ejercita la lógica real
    (`tenant.flags.get(FLAG, False)` + `raise HTTPException`) en vez de solo
    comparar strings de constantes.

    La mayoría de los gates de este repo son `(async) def gate(current_user:
    CurrentUser = Depends(...))`; algunos (`voice.py::_require_voice_web`,
    `ide.py::_require_companion_ide`, `consents.py`/`connectors.py::
    _require_voice_telephony`) toman `tenant: TenantCtx` directo — se detecta
    por el nombre del primer parámetro, sin necesitar una tabla aparte por
    gate. Síncronos y asíncronos se llaman igual (se espera solo si hace
    falta)."""
    primer_parametro = next(iter(inspect.signature(gate).parameters))
    argumento: Any = user.tenant if primer_parametro == "tenant" else user
    resultado = gate(argumento)
    if inspect.isawaitable(resultado):
        await resultado


async def _assert_gate_bloquea_sin_flag(gate: Any, flag: str) -> None:
    with pytest.raises(HTTPException) as excinfo:
        await _invoke_gate(gate, _fake_user({flag: False}))
    assert excinfo.value.status_code == 403


async def _assert_gate_permite_con_flag(gate: Any, flag: str) -> None:
    await _invoke_gate(gate, _fake_user({flag: True}))  # no debe lanzar


def _routers_que_importan_constante(nombre_constante: str) -> set[str]:
    """Nombres (sin `.py`) de `apps/api/edecan_api/routers/*.py` cuyo texto
    fuente menciona el identificador Python `nombre_constante` (p. ej.
    `"FLAG_TOOLS_BROWSER"`) — deliberadamente se busca el NOMBRE de la
    constante, no el valor string del flag: todo gate real de este repo
    referencia su flag vía un nombre importado (`tenant.flags.get(FLAG_X,
    False)`, o un alias local tipo `_FLAG_ADS = "tools.ads"` seguido de
    `flags.get(_FLAG_ADS, ...)`) — nunca un literal suelto dentro de
    `.get(...)`. Buscar el valor string a secas da falsos positivos reales
    (p. ej. `"campaigns"` aparece en `ads.py` por `list_campaigns()` de la
    API de Meta, sin relación con el flag de plan `campaigns`; `"tools.
    images"` aparece en un comentario de `voz_avanzada.py` que solo lo cita
    como ejemplo) — verificado a mano antes de escribir este helper."""
    encontrados: set[str] = set()
    for archivo in _ROUTERS_DIR.glob("*.py"):
        if nombre_constante in archivo.read_text(encoding="utf-8"):
            encontrados.add(archivo.stem)
    return encontrados


# ---------------------------------------------------------------------------
# PARTE 1 — matriz pinned tool.requires_flags <-> gate del router dedicado.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Par:
    """Un par (tool, gate de router) que DEBE exigir el MISMO flag de plan."""

    id: str
    tool_cls: type
    flag: str
    gate: Any


_GATE_COMMERCE = commerce_router._require_commerce_orders
_GATE_ADS = ads_router._require_tools_ads
_GATE_ERP = erp_router._require_erp_inventory
_GATE_RRHH = rrhh_router._require_erp_hr
_GATE_MESSAGING = mensajes_router._require_messaging
_GATE_TRAVEL = viajes_router._require_tools_travel
_GATE_VOICE_WEB_V1 = voice_router._require_voice_web
_GATE_VOICE_WEB_V5 = voz_avanzada_router._require_voice_web
_GATE_PODCAST = voz_avanzada_router._require_tools_podcast
_GATE_AUTOMATIONS = automations_router._require_automations_flag
_GATE_MISSIONS = missions_router._require_agents_missions
_GATE_VEHICLES = vehiculos_router.require_vehicles_flag
_GATE_TELEPHONY_CONNECTORS = connectors_router._require_voice_telephony
_GATE_SOCIAL_PUBLISH = content_studio_router._require_connectors_social

MATRIZ_TOOL_ROUTER: list[_Par] = [
    # -- commerce (ROADMAP_V2.md §7.2, flag commerce.orders) -----------------
    _Par("commerce:CotizarActivoTool", CotizarActivoTool, FLAG_COMMERCE_ORDERS, _GATE_COMMERCE),
    _Par(
        "commerce:GestionarPresupuestoTool",
        GestionarPresupuestoTool,
        FLAG_COMMERCE_ORDERS,
        _GATE_COMMERCE,
    ),
    _Par("commerce:PrepararPagoTool", PrepararPagoTool, FLAG_COMMERCE_ORDERS, _GATE_COMMERCE),
    _Par("commerce:PrepararOrdenTool", PrepararOrdenTool, FLAG_COMMERCE_ORDERS, _GATE_COMMERCE),
    # -- ads (ARCHITECTURE.md §13, flag tools.ads) — solo lectura ------------
    _Par("ads:AdsResumenTool", AdsResumenTool, FLAG_TOOLS_ADS, _GATE_ADS),
    _Par("ads:AdsPrepararCampanaTool", AdsPrepararCampanaTool, FLAG_TOOLS_ADS, _GATE_ADS),
    # -- business/ERP (ARCHITECTURE.md §13, flag erp.inventory) --------------
    _Par("erp:GestionarInventarioTool", GestionarInventarioTool, FLAG_ERP_INVENTORY, _GATE_ERP),
    _Par("erp:EstadoInventarioTool", EstadoInventarioTool, FLAG_ERP_INVENTORY, _GATE_ERP),
    # -- business/RRHH (ARCHITECTURE.md §14, flag erp.hr) --------------------
    _Par("rrhh:GestionarEmpleadoTool", GestionarEmpleadoTool, FLAG_ERP_HR, _GATE_RRHH),
    _Par("rrhh:RegistrarAusenciaTool", RegistrarAusenciaTool, FLAG_ERP_HR, _GATE_RRHH),
    _Par("rrhh:PrepararNominaTool", PrepararNominaTool, FLAG_ERP_HR, _GATE_RRHH),
    # -- messaging (ROADMAP_V2.md §7.2, flag connectors.messaging) -----------
    _Par(
        "messaging:EnviarMensajeTool",
        EnviarMensajeTool,
        FLAG_CONNECTORS_MESSAGING,
        _GATE_MESSAGING,
    ),
    _Par(
        "messaging:LeerMensajesTool", LeerMensajesTool, FLAG_CONNECTORS_MESSAGING, _GATE_MESSAGING
    ),
    # -- travel (ARCHITECTURE.md §14, flag tools.travel) ---------------------
    _Par("travel:BuscarVuelosTool", BuscarVuelosTool, FLAG_TOOLS_TRAVEL, _GATE_TRAVEL),
    _Par("travel:BuscarHotelesTool", BuscarHotelesTool, FLAG_TOOLS_TRAVEL, _GATE_TRAVEL),
    _Par("travel:EstadoVueloTool", EstadoVueloTool, FLAG_TOOLS_TRAVEL, _GATE_TRAVEL),
    _Par("travel:RastrearPaqueteTool", RastrearPaqueteTool, FLAG_TOOLS_TRAVEL, _GATE_TRAVEL),
    _Par("travel:PrepararReservaTool", PrepararReservaTool, FLAG_TOOLS_TRAVEL, _GATE_TRAVEL),
    # -- voice.web: DOS routers dedicados gatean el mismo flag (voice.py de
    #    v1 y voz_avanzada.py de v5, que reutiliza el criterio) ------------
    _Par("voice:ListarVocesTool~voice.py", ListarVocesTool, FLAG_VOICE_WEB, _GATE_VOICE_WEB_V1),
    _Par("voice:SintetizarVozTool~voice.py", SintetizarVozTool, FLAG_VOICE_WEB, _GATE_VOICE_WEB_V1),
    _Par(
        "voice:ListarVocesTool~voz_avanzada.py",
        ListarVocesTool,
        FLAG_VOICE_WEB,
        _GATE_VOICE_WEB_V5,
    ),
    _Par(
        "voice:SintetizarVozTool~voz_avanzada.py",
        SintetizarVozTool,
        FLAG_VOICE_WEB,
        _GATE_VOICE_WEB_V5,
    ),
    # -- tools.podcast: CrearPodcastTool y `POST /v1/voz/podcasts` (WP-V6-04,
    #    landed durante esta misma sesión) producen el MISMO job
    #    (generate_podcast) con el MISMO flag — ver PARTE 4 (encolado) ------
    _Par(
        "podcast:CrearPodcastTool~voz_avanzada.py",
        CrearPodcastTool,
        FLAG_TOOLS_PODCAST,
        _GATE_PODCAST,
    ),
    # -- automations (mío) ----------------------------------------------------
    _Par(
        "automations:GestionarAutomatizacionTool",
        GestionarAutomatizacionTool,
        FLAG_AUTOMATIONS_RULES,
        _GATE_AUTOMATIONS,
    ),
    # -- agents/missions — SOLO LECTURA (dueño real WP-V6-10, missions.py) ---
    _Par("agents:DelegarMisionTool", DelegarMisionTool, FLAG_AGENTS_MISSIONS, _GATE_MISSIONS),
    # -- vehicles — SOLO LECTURA/ASSERT, JAMÁS profundizar (DIRECCION_ACTUAL.md
    #    "Vehículos (Smartcar) eliminado del alcance") -----------------------
    _Par("vehicles:VehiculoEstadoTool", VehiculoEstadoTool, FLAG_TOOLS_VEHICLES, _GATE_VEHICLES),
    _Par(
        "vehicles:VehiculoControlarTool",
        VehiculoControlarTool,
        FLAG_TOOLS_VEHICLES,
        _GATE_VEHICLES,
    ),
    # -- social publishing: chat tool and Content Studio publish through the
    #    same official connector and must share the exact plan gate ----------
    _Par(
        "social:PublicarSocialTool~content_studio.py",
        PublicarSocialTool,
        FLAG_CONNECTORS_SOCIAL,
        _GATE_SOCIAL_PUBLISH,
    ),
]

if _PREMIUM_TOOL_CLASSES:
    _GATE_TELEPHONY_CONSENTS = consents_router._require_voice_telephony
    MATRIZ_TOOL_ROUTER.extend(
        [
            _Par(
                "premium:LlamarContactoTool~consents.py",
                LlamarContactoTool,
                FLAG_VOICE_TELEPHONY,
                _GATE_TELEPHONY_CONSENTS,
            ),
            _Par(
                "premium:EnviarSmsTool~consents.py",
                EnviarSmsTool,
                FLAG_VOICE_TELEPHONY,
                _GATE_TELEPHONY_CONSENTS,
            ),
            _Par(
                "premium:LlamarContactoTool~connectors.py",
                LlamarContactoTool,
                FLAG_VOICE_TELEPHONY,
                _GATE_TELEPHONY_CONNECTORS,
            ),
            _Par(
                "premium:EnviarSmsTool~connectors.py",
                EnviarSmsTool,
                FLAG_VOICE_TELEPHONY,
                _GATE_TELEPHONY_CONNECTORS,
            ),
        ]
    )


@pytest.mark.parametrize("par", MATRIZ_TOOL_ROUTER, ids=[p.id for p in MATRIZ_TOOL_ROUTER])
async def test_flag_de_tool_coincide_con_gate_del_router_dedicado(par: _Par) -> None:
    """Para cada `_Par`: (1) la tool declara EXACTAMENTE ese flag (ni de más
    ni de menos — detecta tanto "le agregaron un flag fino nuevo a la tool
    sin replicarlo en el router" como al revés), y (2) el gate del router
    REAL bloquea con 403 cuando el flag está apagado y permite cuando está
    prendido — invocado directo (`_invoke_gate`), sin HTTP."""
    assert par.tool_cls.requires_flags == frozenset({par.flag}), (
        f"{par.tool_cls.__name__}.requires_flags cambió — actualiza esta matriz "
        "(test_v6_sweep_flags.py) si el cambio fue intencional, o es el patrón "
        "de bug de HOTFIXES_PENDIENTES.md si no lo fue."
    )
    await _assert_gate_bloquea_sin_flag(par.gate, par.flag)
    await _assert_gate_permite_con_flag(par.gate, par.flag)


# ---------------------------------------------------------------------------
# PARTE 2 — flags con un ÚNICO punto de exigencia (sin router dedicado).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_cls,flag,nombre_constante",
    [
        (NavegarWebTool, FLAG_TOOLS_BROWSER, "FLAG_TOOLS_BROWSER"),
        (ExtraerDatosWebTool, FLAG_TOOLS_BROWSER, "FLAG_TOOLS_BROWSER"),
        (CompararPreciosTool, FLAG_TOOLS_BROWSER, "FLAG_TOOLS_BROWSER"),
        (GenerarImagenTool, FLAG_TOOLS_IMAGES, "FLAG_TOOLS_IMAGES"),
    ]
    + (
        [(LanzarCampanaTool, "campaigns", "FLAG_CAMPAIGNS")]
        if _PREMIUM_TOOL_CLASSES
        else []
    ),
    ids=[
        "browser:NavegarWebTool",
        "browser:ExtraerDatosWebTool",
        "browser:CompararPreciosTool",
        "creative:GenerarImagenTool",
    ]
    + (["premium:LanzarCampanaTool"] if _PREMIUM_TOOL_CLASSES else []),
)
def test_flags_de_unico_punto_de_exigencia_sin_router_dedicado(
    tool_cls: type, flag: str, nombre_constante: str
) -> None:
    """`tools.browser`/`tools.images`/`campaigns` no
    tienen (todavía) un router dedicado que gestione la MISMA capacidad por
    HTTP — `ToolRegistry.specs(flags)` (`ARCHITECTURE.md` §10.7) es el ÚNICO
    punto de exigencia hoy, así que no hay nada con qué desincronizarse
    (verificado: `navegar_web`/`extraer_datos_web`/`comparar_precios` son de
    solo lectura sin router propio; `generar_imagen` solo genera un archivo
    privado, `credentials.py` solo administra CREDENCIALES de proveedor de
    imágenes, nunca genera; `publicar_social` ya se compara con el endpoint
    confirmado de Content Studio en `MATRIZ_TOOL_ROUTER`;
    `run_campaign_step`/`INSERT INTO campaigns` solo los produce
    `LanzarCampanaTool`, confirmado con grep). Esto NO es un hallazgo — es
    el estado esperado, documentado en `docs/seguridad-modelo-amenazas.md`.

    La segunda aserción es la red de alerta temprana: si algún día un router
    nuevo empieza a IMPORTAR el nombre de esta constante, esta prueba falla
    con un id obvio y alguien debe decidir si ese router debe coincidir con
    la tool (agregarlo a `MATRIZ_TOOL_ROUTER` arriba) o no."""
    assert tool_cls.requires_flags == frozenset({flag})
    assert _routers_que_importan_constante(nombre_constante) == set()


def test_generar_efecto_sonido_comparte_tools_podcast_sin_endpoint_propio() -> None:
    """`GenerarEfectoSonidoTool` (`edecan_creative.tools`) exige el MISMO
    flag `tools.podcast` que `CrearPodcastTool` (ver `MATRIZ_TOOL_ROUTER`) —
    pero `POST /v1/voz/podcasts` (WP-V6-04, `voz_avanzada.py`) solo crea
    PODCASTS completos, no hay (ni hace falta que haya) un endpoint HTTP
    separado solo para un efecto de sonido suelto: es la misma capacidad de
    plan que ya audita el par de arriba, por eso no se repite como "único
    punto de exigencia" (SÍ hay router para el flag, vía `CrearPodcastTool`)
    ni se agrega como un tercer `_Par` a la matriz (no hay un
    `_require_generar_efecto_sonido` que auditar aparte)."""
    assert GenerarEfectoSonidoTool.requires_flags == frozenset({FLAG_TOOLS_PODCAST})


def test_ninguna_tool_declara_voice_cloning() -> None:
    """`voice.cloning` (`_require_voice_cloning`, `voz_avanzada.py`) gatea
    EXCLUSIVAMENTE `POST/GET/DELETE /v1/voz/clones/*` — un endpoint de UI con
    un humano presente. `docs/voz-telefonia.md`/el docstring de
    `edecan_voice.tools` son explícitos: "el agente JAMÁS clona una voz". Se
    verifica programáticamente que NINGUNA tool del repo declare este flag
    (si alguna vez alguna lo hiciera, sería el patrón de bug inverso: una
    tool alcanzando una capacidad que hoy es exclusiva de un endpoint con
    humano de por medio)."""
    todas_las_tools = [
        *smarthome_get_all_tools(),
        *edecan_skills.get_all_tools(),
        DelegarMisionTool(),
        AdsResumenTool(),
        AdsPrepararCampanaTool(),
        GestionarAutomatizacionTool(),
        NavegarWebTool(),
        ExtraerDatosWebTool(),
        CompararPreciosTool(),
        GestionarInventarioTool(),
        EstadoInventarioTool(),
        GestionarEmpleadoTool(),
        RegistrarAusenciaTool(),
        PrepararNominaTool(),
        CotizarActivoTool(),
        GestionarPresupuestoTool(),
        PrepararPagoTool(),
        PrepararOrdenTool(),
        GenerarImagenTool(),
        CrearPodcastTool(),
        GenerarEfectoSonidoTool(),
        EnviarMensajeTool(),
        LeerMensajesTool(),
        PublicarSocialTool(),
        BuscarVuelosTool(),
        BuscarHotelesTool(),
        EstadoVueloTool(),
        RastrearPaqueteTool(),
        PrepararReservaTool(),
        ListarVocesTool(),
        SintetizarVozTool(),
        VehiculoEstadoTool(),
        VehiculoControlarTool(),
    ]
    todas_las_tools.extend(tool_cls() for tool_cls in _PREMIUM_TOOL_CLASSES)
    for tool in todas_las_tools:
        assert "voice.cloning" not in tool.requires_flags, (
            f"{type(tool).__name__} declara voice.cloning -- eso rompe la "
            "garantía documentada de que ninguna tool clona voces."
        )


# ---------------------------------------------------------------------------
# PARTE 3 — superficies SIN flag de plan por diseño (smarthome, skills).
# ---------------------------------------------------------------------------


def test_smarthome_sin_flag_de_plan_router_y_tools_coinciden() -> None:
    """`smarthome.py` (mío) no gatea con ningún flag de plan — decisión de
    producto explícita de WP-V3-12 ("SIN flag de plan nuevo — no toques
    edecan_schemas", ver el docstring de
    `packages/smarthome/edecan_smarthome/tools.py`: lo que gatea en la
    práctica es si el tenant conectó Home Assistant, y `dangerous=True` en
    `casa_controlar`). Confirmado en AMBOS lados: el router (0 referencias a
    `tenant.flags` en su código fuente) y las 3 tools
    (`requires_flags` vacío)."""
    fuente_router = inspect.getsource(smarthome_router)
    assert "tenant.flags" not in fuente_router
    assert ".flags.get(" not in fuente_router

    tools = smarthome_get_all_tools()
    assert len(tools) == 3
    for tool in tools:
        assert tool.requires_flags == frozenset()


def test_skills_sin_flag_de_plan_router_y_tools_coinciden() -> None:
    """Ídem para el marketplace de Agent Skills (`skills.py`, mío) — decisión
    documentada en su propio docstring ("Sin flag de plan, sin segundo gate
    de confirmación"): el marketplace es parte del toolkit base, disponible
    en todos los planes, no una capacidad premium."""
    fuente_router = inspect.getsource(skills_router)
    assert "tenant.flags" not in fuente_router
    assert ".flags.get(" not in fuente_router

    tools = edecan_skills.get_all_tools()
    assert len(tools) == 6
    for tool in tools:
        assert tool.requires_flags == frozenset()


# ---------------------------------------------------------------------------
# PARTE 4 — superficies de encolado: quién puede meter cada JOB_TYPE en la
# cola y qué gate tiene ese camino (ver `edecan_schemas.queue.JOB_TYPES`).
# ---------------------------------------------------------------------------

# Documentado + verificado con grep sobre TODO el repo (`enqueue(...,
# "<job_type>", ...)`), NO solo sobre `apps/api/edecan_api/routers/` — ver el
# detalle completo por tipo en `docs/seguridad-modelo-amenazas.md`.
_SUPERFICIES_DE_ENCOLADO: dict[str, str] = {
    "ingest_file": "POST /v1/files (routers/files.py) -- sin flag, capacidad base.",
    "sync_connector": (
        "Solo el scheduler interno del worker (JOBS_PERIODICOS) -- nunca un tenant directo."
    ),
    "send_reminder": (
        "Solo send_reminder_scan.py (interno) por cada reminder vencido -- nunca un tenant directo."
    ),
    "send_reminder_scan": (
        "Solo el scheduler interno del worker/apps/local -- nunca un tenant directo."
    ),
    "run_campaign_step": (
        "LanzarCampanaTool (premium/edecan_premium/tools.py, requires_flags={'campaigns'}) "
        "al crear una campana; premium/edecan_premium/campaigns.py se re-encola a si mismo "
        "para el siguiente lote de hasta 10 targets (interno, misma campana ya gateada)."
    ),
    "generate_content": (
        "NINGUN productor todavia (dead code documentado en su propio handler) -- no corre "
        "en produccion."
    ),
    "memory_consolidate": (
        "routers/perfil.py + routers/conversations.py -- sin flag, capacidad base v1."
    ),
    "run_mission": (
        "POST /v1/missions (missions.py, flag agents.missions + limits.missions_per_day, "
        "WP-V6-10) Y DelegarMisionTool (edecan_agents/tools.py, requires_flags="
        "{'agents.missions'} + _cupo_disponible replica limits.missions_per_day -- "
        "hallazgo 2 del docstring del modulo, RESUELTO: ambos caminos aplican el mismo "
        "limite diario antes de encolar)."
    ),
    "run_automation": (
        "POST /v1/automations/{id}/probar + creacion habilitada (automations.py, mio, flag "
        "automations.rules) Y POST /v1/hooks/{id} (hooks.py, mio, secreto por automatizacion, "
        "sin JWT) Y automation_scan.py (interno, agenda vencida). Los TRES terminan en "
        "run_automation.py, que re-valida FLAG_AUTOMATIONS_RULES el mismo antes de ejecutar "
        "(defensa en profundidad real, ver test de PARTE 4 mas abajo) -- asi que aunque el "
        "flag se apague DESPUES de crear una automatizacion webhook, el job encolado por el "
        "hook publico no ejecuta nada."
    ),
    "automation_scan": (
        "Solo el scheduler interno del worker/apps/local -- nunca un tenant directo."
    ),
    "generate_podcast": (
        "CrearPodcastTool (edecan_creative/tools.py, requires_flags={'tools.podcast'}) Y "
        "POST /v1/voz/podcasts (voz_avanzada.py, WP-V6-04, flag tools.podcast via "
        "_require_tools_podcast) -- mismo flag en los dos caminos, ver MATRIZ_TOOL_ROUTER."
    ),
    "process_meeting": (
        "Ningun productor todavia (v6, WP-V6-05 en paralelo, handler+router aun no aterrizan)."
    ),
    "notify_phone_call_summary": (
        "Solo phone.py despues de persistir un cierre terminal firmado o un fallo del "
        "dispatcher; el worker exige tenant_id+call_id, reclama un resumen existente y "
        "solo envia un push generico sin datos de la llamada."
    ),
    "notify_incoming_phone_call": (
        "Solo phone.py tras verificar la firma Twilio y persistir la llamada entrante con "
        "su evento; el worker relee ambos y usa notificaciones universales idempotentes."
    ),
    "notify_important_event": (
        "Solo productores internos de herramientas sincrónicas; el payload se limita a "
        "enums e identificadores UUID y el worker persiste actividad antes del push."
    ),
}


def test_job_types_documentados_coinciden_con_edecan_schemas_queue() -> None:
    """Pinnea `JOB_TYPES` completo: si alguien agrega un job type nuevo (o
    quita uno), esta prueba avisa para que se documente aquí quién puede
    encolarlo y qué gate tiene ese camino (ver `_SUPERFICIES_DE_ENCOLADO` y
    `docs/seguridad-modelo-amenazas.md`)."""
    assert set(JOB_TYPES) == set(_SUPERFICIES_DE_ENCOLADO)


def test_run_automation_worker_revalida_el_flag_sin_importar_por_donde_entro() -> None:
    """Verificación programática (source scan, sin ejecutar el handler
    completo -- `run_automation.py` está fuera de las rutas que este WP
    puede tocar, WP-V6-07) de la defensa en profundidad citada en
    `_SUPERFICIES_DE_ENCOLADO['run_automation']`: el handler del worker
    re-lee el flag `automations.rules` desde el plan REAL del tenant antes
    de ejecutar, sin importar si el job llegó por el hook público (sin JWT,
    solo protegido por el secreto), por `probar_automation` o por el
    scan agendado."""
    # `edecan_worker` no es dependencia declarada de `apps/api` (deployables
    # independientes, ver `ARCHITECTURE.md` §10.1) pero SÍ es miembro del
    # workspace uv -- importable bajo `uv run --all-packages`, mismo criterio
    # que `edecan_vehicles`/`edecan_premium` (ver docstring del módulo).
    import edecan_worker.handlers.run_automation as run_automation_handler

    fuente = inspect.getsource(run_automation_handler)
    assert "FLAG_AUTOMATIONS_RULES" in fuente
    assert "flags.get(FLAG_AUTOMATIONS_RULES" in fuente.replace(" ", "")


# ---------------------------------------------------------------------------
# PARTE 5 — `usar_computadora`: cross-check comportamental (no solo de
# strings) contra los routers dedicados de IDE/control remoto. El fix en sí
# (`_bloqueo_por_plan`) ya está hecho y tiene 24 tests en
# packages/toolkit/tests/test_computadora.py — esto agrega la pieza que
# faltaba: comparar la decisión de `_bloqueo_por_plan` con la decisión REAL
# de `ide._require_companion_ide`/`remote._require_remote_view`/
# `remote._require_remote_control` para los MISMOS `flags`, no solo con lo
# que el propio `computadora.py` cree que exigen.
# ---------------------------------------------------------------------------

# Las SEIS acciones que `routers/ide.py` sirve bajo `/v1/ide/*`, TODAS detrás
# de `_require_companion_ide` (`GET /tree`, `GET /file`, `PUT /file`,
# `POST /edit`, `POST /run`, `POST /search`) -- no solo las tres que además
# coinciden con `edecan_companion.actions._IDE_ACTIONS` (gate LOCAL y
# distinto del companion, ver el comentario de `_ACCIONES_IDE` en
# `computadora.py`). Antes esta tupla también tenía solo tres, así que este
# cross-check nunca ejercitó `read_file`/`write_file`/`run_command` y no
# habría detectado el hallazgo plan-flag-bypass de esas tres acciones.
_ACCIONES_IDE = (
    "list_tree",
    "search_files",
    "apply_edit",
    "read_file",
    "write_file",
    "run_command",
)
_ACCIONES_INPUT_REMOTO = ("input_pointer", "input_key")


def test_bloqueo_por_plan_ide_coincide_con_ide_require_companion_ide() -> None:
    for accion in _ACCIONES_IDE:
        assert computadora_module._bloqueo_por_plan(accion, {}) is not None
        with pytest.raises(HTTPException) as excinfo:
            ide_router._require_companion_ide(TenantCtx(tenant_id=uuid4(), plan_key="x", flags={}))
        assert excinfo.value.status_code == 403

        assert computadora_module._bloqueo_por_plan(accion, {FLAG_COMPANION_IDE: True}) is None
        ide_router._require_companion_ide(
            TenantCtx(tenant_id=uuid4(), plan_key="x", flags={FLAG_COMPANION_IDE: True})
        )  # no lanza


def test_bloqueo_por_plan_screenshot_coincide_con_remote_require_remote_view() -> None:
    assert computadora_module._bloqueo_por_plan("screenshot", {}) is not None
    with pytest.raises(HTTPException) as excinfo:
        remote_router._require_remote_view(_fake_user({}))
    assert excinfo.value.status_code == 403

    assert (
        computadora_module._bloqueo_por_plan("screenshot", {FLAG_COMPANION_REMOTE_VIEW: True})
        is None
    )
    remote_router._require_remote_view(_fake_user({FLAG_COMPANION_REMOTE_VIEW: True}))  # no lanza


@pytest.mark.parametrize("accion", _ACCIONES_INPUT_REMOTO)
def test_bloqueo_por_plan_input_remoto_reproduce_el_hallazgo_original(accion: str) -> None:
    """Reproduce el escenario EXACTO de `HOTFIXES_PENDIENTES.md`
    (`hosted_basic`: `companion.remote_view=True`, `companion.remote_input=
    False`) contra el gate REAL de `remote.py`, no solo contra
    `_bloqueo_por_plan` en aislamiento: `remote_view=True` solo NUNCA debe
    bastar para `input_pointer`/`input_key` — ni en `computadora.py` ni en
    `remote._require_remote_control` (que, llamado directo así como
    `Agent.run_turn` recibiría el resultado de `Depends(_require_remote_view)`
    ya resuelto, valida el flag de INPUT; el de VIEW ya se prueba aparte
    arriba)."""
    flags_insuficientes = {FLAG_COMPANION_REMOTE_VIEW: True, FLAG_COMPANION_REMOTE_INPUT: False}
    assert computadora_module._bloqueo_por_plan(accion, flags_insuficientes) is not None
    with pytest.raises(HTTPException) as excinfo:
        remote_router._require_remote_control(_fake_user(flags_insuficientes))
    assert excinfo.value.status_code == 403

    flags_completos = {FLAG_COMPANION_REMOTE_VIEW: True, FLAG_COMPANION_REMOTE_INPUT: True}
    assert computadora_module._bloqueo_por_plan(accion, flags_completos) is None
    remote_router._require_remote_control(_fake_user(flags_completos))  # no lanza


# ---------------------------------------------------------------------------
# PARTE 6 — `ConnectionManager.send_command`: ¿hay otro camino API -> companion
# sin gate, además de los ya conocidos?
# ---------------------------------------------------------------------------


def test_solo_tres_modulos_de_routers_invocan_send_command() -> None:
    """`ConnectionManager.send_command` (`edecan_api.companion_manager`) es
    el ÚNICO canal por el que la API le pide una acción al companion de
    escritorio. Antes del fix de `usar_computadora`, dos llamadores
    (`ide.py`/`remote.py`) SÍ gateaban por flag fino y un tercero (la tool de
    chat, que llega vía `conversations.py::_companion_caller`) no. Este test
    fija la lista completa de módulos de `apps/api/edecan_api/routers/` que
    mencionan `send_command` para que un CUARTO camino nuevo (que se salte
    el gate) se note de inmediato — no reemplaza los tests dedicados de cada
    uno (`test_ide_router.py`/`test_remote_router.py`/
    `test_computadora.py`), solo confirma que la SUPERFICIE de invocación no
    creció sin que este archivo se entere.

    `companion.py` (mío, el router de pairing/WS) NO aparece en esta lista:
    `companion_ws`/`handle_incoming` solo procesan RESPUESTAS `{request_id,
    ...}` que ya vienen del companion, nunca reenvían un comando arbitrario
    -- confirmado leyendo `ConnectionManager.handle_incoming`
    (`companion_manager.py`): valida `request_id` contra `_pending` (y que
    pertenezca al `tenant_id` correcto) antes de resolver el `Future`, nunca
    llama a `send_command` él mismo."""
    llamadores = {
        archivo.stem
        for archivo in _ROUTERS_DIR.glob("*.py")
        if "send_command" in archivo.read_text(encoding="utf-8")
    }
    assert llamadores == {"ide", "remote", "conversations"}

    fuente_companion = inspect.getsource(companion_router)
    assert "send_command" not in fuente_companion


# ---------------------------------------------------------------------------
# PARTE 7 — skills: ¿puede el CONTENIDO de una skill de terceros escalar
# privilegios (alcanzar una tool/acción gateada por otra superficie)?
# Respuesta corta: NO. El detalle + el test negativo ejecutable viven en
# packages/skills/tests/test_v6_seguridad_privilegios.py (dueño real de ese
# paquete es este mismo WP, packages/skills/ está en sus rutas permitidas).
# Aquí solo se deja el ancla programática de que las 5 tools del marketplace
# nunca declaran requires_flags (ya cubierto por PARTE 3) y que ninguna
# importa ToolRegistry/ConnectionManager -- ver ese archivo para el resto.
# ---------------------------------------------------------------------------


def _nombres_importados(ruta: Path) -> set[str]:
    """Nombres que `ruta` trae con `import x`/`from x import y` -- a
    diferencia de un barrido de texto plano, un `ast.parse` no confunde una
    MENCIÓN en un docstring/comentario (p. ej. "usa `ToolRegistry.
    load_entry_points(...)`" en prosa) con un import real: los nodos
    `ast.Import`/`ast.ImportFrom` nunca aparecen dentro de un `ast.Constant`
    de docstring."""
    arbol = ast.parse(ruta.read_text(encoding="utf-8"), filename=str(ruta))
    nombres: set[str] = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            nombres.update(alias.name.split(".")[-1] for alias in nodo.names)
        elif isinstance(nodo, ast.ImportFrom):
            nombres.update(alias.name for alias in nodo.names)
    return nombres


def test_edecan_skills_nunca_importa_toolregistry_ni_connectionmanager() -> None:
    """`edecan_skills` (5 tools + `installer`/`security`/`sources`/`store`/
    `client`) nunca importa `ToolRegistry` (`edecan_core.tools.registry`) ni
    `ConnectionManager` (`edecan_api.companion_manager`) -- las dos
    superficies con privilegios que una tool PODRÍA, en teoría, alcanzar
    (contrastar con `edecan_toolkit.computadora`, que SÍ usa
    `ctx.extras["companion"]` a propósito y por eso necesita
    `_bloqueo_por_plan`). Ver también
    `packages/skills/tests/test_v6_seguridad_privilegios.py` para la prueba
    en RUNTIME (no solo de imports) de que `usar_skill` nunca invoca nada
    más allá de devolver texto."""
    paquete = Path(edecan_skills.__file__).resolve().parent
    for archivo in paquete.glob("*.py"):
        nombres = _nombres_importados(archivo)
        assert "ToolRegistry" not in nombres, f"{archivo.name} importa ToolRegistry"
        assert "ConnectionManager" not in nombres, f"{archivo.name} importa ConnectionManager"


# ---------------------------------------------------------------------------
# PARTE 8 — Hallazgo 1, RESUELTO (ver docstring del módulo y
# docs/seguridad-modelo-amenazas.md): `Agent.run_turn` ahora revalida
# `requires_flags` al EJECUTAR una tool resuelta, no solo al anunciarla
# (`packages/core/edecan_core/agent.py::_con_flags_satisfechos`, dueño real
# del fix: `packages/core/tests/test_agent.py` tiene el pin principal). Este
# archivo (`apps/api/tests`, cruza paquetes hermanos a propósito, ver el
# docstring del módulo) conserva la prueba equivalente como regresión: si
# `edecan_core` alguna vez reintroduce el hueco, se nota también desde acá,
# no solo desde `packages/core` en aislado. Prueba ejecutable — no un mock de
# `Agent`, el `Agent` REAL de `edecan_core`.
# ---------------------------------------------------------------------------


@dataclass
class _FakeToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class _FakeChunk:
    type: str
    text: str | None = None
    tool_call: _FakeToolCall | None = None
    usage: Any | None = None


class _FakeProviderPideToolNoAnunciada:
    """Provider falso que SIEMPRE responde con un `tool_use` para
    `tool_name` en su primera llamada, sin que importe qué `tools=` trajo el
    `CompletionRequest` — `Agent` no valida en general que un `tool_use`
    corresponda a algo listado en `tools=` esa vuelta (sigue resolviendo por
    NOMBRE contra el registro), pero desde el fix de PARTE 8 sí revalida
    `requires_flags` de lo que resuelve, que es lo que este test ejercita.
    Representa tanto un modelo mal comportado como el escenario real de
    interés (inyección de prompt indirecta vía contenido de una skill de
    terceros, una página web navegada con `navegar_web`, o un documento de
    `consultar_documentos`)."""

    def __init__(self, tool_name: str) -> None:
        self._tool_name = tool_name
        self._ya_pidio = False

    async def stream(self, req: Any):
        if not self._ya_pidio:
            self._ya_pidio = True
            yield _FakeChunk(
                type="tool_call",
                tool_call=_FakeToolCall(id="call_1", name=self._tool_name, arguments={}),
            )
        else:
            yield _FakeChunk(type="text", text="listo")


class _FakeLLMRouterFijo:
    def __init__(self, provider: Any) -> None:
        self._provider = provider

    def resolve(self, alias: str, tenant_flags: dict) -> tuple[Any, str]:
        return self._provider, "fake-model"


class _ToolConFlagNoOfrecida:
    """Tool falsa con `requires_flags` no vacío -- duck-typed como
    `edecan_core.tools.base.Tool` sin heredar de la ABC (evita tener que
    importar `edecan_core` solo para esto; `ToolRegistry`/`Agent` solo usan
    atributos, no `isinstance`)."""

    def __init__(self, flag: str) -> None:
        self.name = "tool_con_flag_de_plan_no_satisfecho"
        self.description = "Tool falsa con flag de plan, para el test de regresión de este archivo."
        self.input_schema: dict[str, Any] = {"type": "object", "properties": {}}
        self.requires_flags = frozenset({flag})
        self.dangerous = False
        self.calls: list[dict[str, Any]] = []

    async def run(self, ctx: Any, args: dict[str, Any]) -> Any:
        from edecan_core.tools.base import ToolResult

        self.calls.append(args)
        return ToolResult(content="NO debería poder ejecutarse sin el flag -- ver el test")


async def test_agent_run_turn_no_ejecuta_una_tool_cuyo_flag_no_esta_satisfecho() -> None:
    """Regresión del Hallazgo 1, ya RESUELTO (ver docstring de PARTE 8 y
    `docs/seguridad-modelo-amenazas.md`): `Agent._run_turn`
    (`packages/core/edecan_core/agent.py`) usa `flags` tanto para calcular
    `tool_specs = self._registry.specs(flags)` (qué se OFRECE al modelo) como
    — vía `_con_flags_satisfechos` — para decidir qué se EJECUTA. Este test
    arma un `ToolRegistry` real con una tool cuyo flag NO está en `flags={}`
    (por lo que `specs({})` jamás la habría anunciado — se verifica
    explícitamente abajo) y un `Agent` real; el fake LLM "decide" pedirla de
    todos modos — pero `Agent` la trata como herramienta desconocida y NUNCA
    la ejecuta.

    Si `packages/core/edecan_core/agent.py` alguna vez reintroduce el hueco
    (p. ej. un refactor que toque `resolved_calls` sin querer), este assert
    empieza a FALLAR — tratarlo con la misma severidad que el hallazgo
    original, no como un test flaky cualquiera."""
    from edecan_core.agent import Agent
    from edecan_core.tools.registry import ToolRegistry

    tool = _ToolConFlagNoOfrecida(FLAG_TOOLS_ADS)
    registry = ToolRegistry()
    registry.register(tool)  # type: ignore[arg-type]

    # Confirma la premisa: con flags={}, esta tool NUNCA se habría anunciado.
    assert registry.specs({}) == []

    provider = _FakeProviderPideToolNoAnunciada(tool.name)
    agent = Agent(_FakeLLMRouterFijo(provider), registry)

    from edecan_core.tools.base import ToolContext
    from edecan_schemas import PersonaConfig

    ctx = ToolContext(
        tenant_id=uuid4(),
        user_id=uuid4(),
        session=None,
        settings=None,
        llm=None,
        vault=None,
        extras={},
    )

    [
        _
        async for _ in agent.run_turn(
            ctx=ctx, persona=PersonaConfig(), history=[], user_text="hola", flags={}
        )
    ]

    # FIX verificado: la tool NUNCA se ejecuta -- `flags={}` no satisface
    # `requires_flags={FLAG_TOOLS_ADS}`, así que `Agent` la trata igual que
    # una herramienta desconocida en vez de correr `tool.run()`.
    assert tool.calls == []


def test_delegar_mision_revisa_limits_missions_per_day() -> None:
    """Hallazgo 2, ya RESUELTO (ver docstring del módulo y
    `docs/seguridad-modelo-amenazas.md`): antes `DelegarMisionTool.run()`
    (`packages/agents/edecan_agents/tools.py`, fuera de las rutas que este WP
    puede escribir) revisaba el flag base `agents.missions` (vía
    `requires_flags`, sujeto además al hallazgo 1) pero nunca
    `LIMIT_MISSIONS_PER_DAY`, a diferencia de `POST /v1/missions`
    (`missions.py::_check_missions_quota`, WP-V6-10, que sí lo revisaba antes
    de encolar el mismo job `run_mission`). Fix: `DelegarMisionTool.
    _cupo_disponible` replica el mismo criterio (mismo flag, mismo `-1` =
    ilimitado, mismo `0`/ausente = sin cupo, mismo `SELECT COUNT(*) FROM
    agent_missions` desde medianoche UTC) antes de insertar/encolar.

    Verificado por inspección de fuente, igual que el hallazgo antes de
    resolverse (antes `test_HALLAZGO_delegar_mision_no_referencia_
    limits_missions_per_day`, con la aserción del límite invertida) — busca
    el símbolo `LIMIT_MISSIONS_PER_DAY` en vez de la subcadena de la prosa,
    para no depender de cómo esté redactado el docstring del módulo. La
    cobertura de comportamiento completa (cupo agotado bloquea sin
    insertar/encolar, `-1` se salta el `SELECT COUNT`, ausencia de flags
    hace fail-closed, etc.) vive en `packages/agents/tests/test_tools.py`,
    fuera del alcance de escritura de este WP."""
    import edecan_agents.tools as delegar_mision_module

    fuente_tool = inspect.getsource(delegar_mision_module)
    assert "agents.missions" in fuente_tool  # el flag base se sigue exigiendo
    assert "LIMIT_MISSIONS_PER_DAY" in fuente_tool  # el límite YA se exige (fix)

    fuente_router = inspect.getsource(missions_router)
    assert "missions_per_day" in fuente_router  # el router lo sigue exigiendo, para contraste
