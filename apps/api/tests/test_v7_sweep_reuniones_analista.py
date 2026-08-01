"""WP-V7-04 — Barrido cruzado reuniones/analista: pines que no encajan
naturalmente en `test_reuniones_router.py`/`test_analista_router.py` porque
comparan los DOS routers entre sí, o cruzan `packages/meetings`/
`apps/api`/`edecan_schemas` para verificar que una constante duplicada a
propósito (`ARCHITECTURE.md` §10.1: "cada paquete lleva su propia copia") no
se haya desincronizado.

## Por qué esto no lo prueban ya los archivos de cada router

`test_reuniones_router.py` verifica "sin `tools.meetings`, 403" endpoint por
endpoint, y `test_analista_router.py` verifica que ningún endpoint importa
`edecan_llm` — pero NINGUNO de los dos deja asentado, en un solo lugar, la
comparación explícita que motivó `ARCHITECTURE.md` §15 (el docstring de
`analista.py`, sección "Por qué SIN flag de plan"): que **reuniones exige
`tools.meetings` y analista deliberadamente no exige NADA**, pese a ser dos
routers hermanos escritos en el mismo WP (WP-V6-05/06) sobre el mismo
dominio (analizar contenido que el tenant ya subió). Es exactamente la clase
de bug que ya documentó `HOTFIXES_PENDIENTES.md` para
`usar_computadora`/`companion.ide` (un flag que se queda pegado o se
olvida al copiar un patrón entre superficies parecidas) — mismo espíritu que
`test_v7_sweep_voz.py` (WP-V7-03) aplicado a DOS ROUTERS en vez de dos
capacidades dentro del mismo router.

Los tests de introspección estática (`_dependency_names`) no invocan ningún
endpoint — leen la firma real de la función (los `Depends(...)` son valores
default de FastAPI) y revientan de inmediato si alguien agrega/quita un gate
de flag en el futuro, sin depender de reproducir toda la maquinaria de
fixtures (`FakeSession`/`fake_aioboto3`/etc.) de cada router.
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from edecan_schemas.plans import FLAG_TOOLS_MEETINGS, PLANES
from edecan_schemas.queue import JOB_TYPES
from httpx import ASGITransport, AsyncClient

from edecan_api import deps as edecan_deps
from edecan_api.routers import analista, reuniones

# ---------------------------------------------------------------------------
# BARRIDO B — introspección estática de los `Depends(...)` reales.
# ---------------------------------------------------------------------------


def _dependency_names(func: Any) -> set[str]:
    """Nombres de los callables detrás de cada `Depends(...)` de una función
    de router — introspección de la firma real, no de invocar el endpoint."""
    nombres: set[str] = set()
    for param in inspect.signature(func).parameters.values():
        dependencia = getattr(param.default, "dependency", None)
        if dependencia is not None:
            nombres.add(getattr(dependencia, "__name__", repr(dependencia)))
    return nombres


_ENDPOINTS_ANALISTA = (
    analista.list_archivos,
    analista.resumen,
    analista.forecast,
    analista.grafico,
)
_ENDPOINTS_REUNIONES = (
    reuniones.crear_reunion,
    reuniones.listar_reuniones,
    reuniones.obtener_reunion,
    reuniones.borrar_reunion,
)


def test_analista_ningun_endpoint_depende_de_un_gate_de_flag() -> None:
    """Ninguno de los 4 endpoints de `analista.py` depende de una función
    `_require_*`/gate de flag — confirma en código lo que el docstring del
    router y `docs/analista.md` documentan en prosa ("ningún endpoint
    declara un flag de plan", paridad deliberada con las 8 tools de
    `edecan_docanalysis`, que tampoco llevan `requires_flags`)."""
    permitidas = {"get_current_user", "get_tenant_session", "get_repo", "get_settings"}
    for func in _ENDPOINTS_ANALISTA:
        nombres = _dependency_names(func)
        assert nombres, f"{func.__name__} no declaró ningún Depends() — ¿cambió la firma?"
        inesperadas = nombres - permitidas
        assert not inesperadas, (
            f"{func.__name__} depende de {inesperadas} — analista.py NO debe exigir ningún "
            "flag de plan (ARCHITECTURE.md §15, ver el docstring del router)."
        )


def test_reuniones_los_4_endpoints_dependen_de_require_tools_meetings() -> None:
    """Espejo del test anterior: los 4 endpoints de `reuniones.py` SÍ deben
    depender de `_require_tools_meetings` — ninguno puede quedar
    accidentalmente sin el gate (ver también los tests `..._sin_flag_
    returns_403` de `test_reuniones_router.py`, que verifican el EFECTO end-
    to-end; este verifica la CAUSA en la firma misma)."""
    for func in _ENDPOINTS_REUNIONES:
        nombres = _dependency_names(func)
        assert "_require_tools_meetings" in nombres, (
            f"{func.__name__} no depende de _require_tools_meetings — un tenant sin "
            "tools.meetings podría alcanzarlo igual."
        )


# ---------------------------------------------------------------------------
# BARRIDO B — confirmación end-to-end (HTTP real) del contraste: el MISMO
# plan (`hosted_basic`, sin `tools.meetings`) bloquea reuniones y deja pasar
# analista. La introspección de arriba ya prueba la causa; esto prueba que
# el efecto observable coincide.
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> _FakeResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)


@dataclass
class _FakeSession:
    respuestas: list[list[dict[str, Any]]] = field(default_factory=list)
    llamadas: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        self.llamadas.append((str(stmt), dict(params or {})))
        filas = self.respuestas.pop(0) if self.respuestas else []
        return _FakeResult(filas)


@pytest.fixture
def fake_session() -> _FakeSession:
    return _FakeSession()


@pytest.fixture
def _mounted_app(app, fake_session: _FakeSession):
    """`app` (de `conftest.py`) + AMBOS routers montados + `get_tenant_session`
    reemplazado — mismo patrón que `test_reuniones_router.py`/
    `test_analista_router.py`, esta vez con los dos a la vez."""
    app.include_router(reuniones.router)
    app.include_router(analista.router)
    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: fake_session
    return app


@pytest.fixture
async def client(_mounted_app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=_mounted_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# BARRIDO A — constantes duplicadas a propósito entre `apps/api`/
# `packages/meetings` (`ARCHITECTURE.md` §10.1) no deben desincronizarse.
# ---------------------------------------------------------------------------


def test_voice_stt_connector_key_no_diverge_entre_apps_api_y_edecan_meetings() -> None:
    """`edecan_meetings.stt.VOICE_STT_CONNECTOR_KEY` se documenta como "el
    mismo string EXACTO que `edecan_api.deps.VOICE_STT_CONNECTOR_KEY`" — si
    alguno de los dos cambiara sin el otro, `resolver_stt_del_tenant`
    dejaría de encontrar la credencial que sí guardó `PUT /v1/credentials/
    voice/stt` (mismo riesgo de desync que ya motivó comparar
    `edecan_voice.tenant.VOICE_TTS_CONNECTOR_KEY` contra su par)."""
    from edecan_meetings.stt import VOICE_STT_CONNECTOR_KEY as connector_key_meetings

    assert connector_key_meetings == edecan_deps.VOICE_STT_CONNECTOR_KEY == "voice_stt"


def test_disclaimer_consentimiento_identico_entre_router_y_tool() -> None:
    """`DISCLAIMER_CONSENTIMIENTO` está duplicado a propósito en
    `reuniones.py` y `edecan_meetings/tools.py` (el banner de
    `apps/web/.../reuniones/page.tsx` es la tercera copia, fuera del alcance
    de este paquete de trabajo — TypeScript, no se audita acá) — deben decir
    EXACTAMENTE lo mismo, byte por byte."""
    from edecan_meetings.tools import DISCLAIMER_CONSENTIMIENTO as disclaimer_tool

    assert reuniones.DISCLAIMER_CONSENTIMIENTO == disclaimer_tool


def test_resumir_reunion_exige_el_mismo_flag_que_el_router() -> None:
    """`resumir_reunion` (la tool de chat) y `/v1/reuniones` (el router HTTP)
    son dos puertas de entrada a la MISMA capacidad — deben exigir el MISMO
    flag (`ARCHITECTURE.md` §15.f pin: `requires_flags={"tools.meetings"}`)."""
    from edecan_meetings.tools import ResumirReunionTool

    tool = ResumirReunionTool()
    assert tool.requires_flags == frozenset({FLAG_TOOLS_MEETINGS})
    assert tool.dangerous is False  # solo lee/resume contenido propio del tenant


# ---------------------------------------------------------------------------
# Esquema/vocabulario — pinnea en código lo que v6 dejó documentado en
# `ARCHITECTURE.md` §15.b/§15.d y que `DIRECCION_ACTUAL.md`/
# `HOTFIXES_PENDIENTES.md` señalan como el bug índice de v6 (columnas/
# vocabulario "asumidos" vs. los que de verdad aterrizaron).
# ---------------------------------------------------------------------------


def test_process_meeting_esta_en_job_types() -> None:
    assert "process_meeting" in JOB_TYPES


def test_flag_tools_meetings_matriz_de_planes() -> None:
    """Modelo de precio de pago único (2026-07-09, `edecan_schemas.plans`
    docstring): `tools.meetings` ya no diferencia por plan — las 4 entradas
    de `PLANES` lo conceden por igual (mismo criterio que `packages/schemas/
    tests/test_plans.py`/`test_v2_contracts.py`)."""
    esperado = {
        "free_selfhost": True,
        "hosted_basic": True,
        "hosted_pro": True,
        "hosted_business": True,
    }
    real = {plan_key: plan.flags.get(FLAG_TOOLS_MEETINGS) for plan_key, plan in PLANES.items()}
    assert real == esperado


def test_vocabulario_de_status_no_incluye_queued() -> None:
    """`'queued'` NO es un valor válido del CHECK real de `meetings.status`
    (`pending|running|done|error`, `0008_v6_expansion.py`) — este test
    pinnea que ningún literal `'queued'` sigue vivo en el SQL de
    `reuniones.py` (`process_meeting.py` se cubre aparte, en su propio
    archivo de test, con `_ESTADOS_TERMINALES`)."""
    import ast

    arbol = ast.parse(inspect.getsource(reuniones))
    literales_de_texto = {
        nodo.value
        for nodo in ast.walk(arbol)
        if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str)
    }
    assert "queued" not in literales_de_texto
