"""WP-V7-03 — Barrido B (plan-flag): `edecan_api.routers.voz_avanzada` sirve
DOS capacidades con flags DISTINTOS en el MISMO router — clonación de voz
(`_FLAG_VOICE_CLONING = "voice.cloning"`, endpoints `/clones*`) y podcasts
(`_FLAG_TOOLS_PODCAST = "tools.podcast"`, endpoints `/podcasts*`). Este
archivo pinnea, endpoint por endpoint, que cada uno exige el flag de SU
PROPIA capacidad — nunca el de la otra, ni solo un flag genérico compartido
— exactamente la clase de bug que ya documentó `HOTFIXES_PENDIENTES.md`
para `usar_computadora`/`companion.remote_input`/`companion.ide` (un único
dispatch/router compartido por dos superficies, donde solo una de las dos
exigía su flag fino).

## Por qué esto NO lo prueba ya `test_voz_avanzada.py`/`test_podcasts_router.py`

Ambos archivos SÍ verifican "sin el flag propio, 403" (`test_crear_clon_sin
_flag_voice_cloning_returns_403`, `test_crear_podcast_sin_flag_tools_podcast
_returns_403`, etc.) — pero cada uno lo hace con un `plan_key` real donde
`voice.cloning` y `tools.podcast` SIEMPRE valen LO MISMO (`hosted_basic`:
ambos `False`; `hosted_pro`/`hosted_business`/`free_selfhost`: ambos
`True` — ver `edecan_schemas.plans.PLANES`, `ARCHITECTURE.md` §14.c). Con
esa matriz, un 403 en `/clones` bajo `hosted_basic` es AMBIGUO: no se puede
distinguir "el gate de verdad exige `voice.cloning`" de "el gate por error
quedó copiado de `tools.podcast`" (o de un flag genérico compartido) — las
DOS hipótesis predicen exactamente el mismo resultado observable con los
4 planes reales tal como están hoy. Hace falta aislar los dos flags a mano
(uno `True` y el otro `False`, en las dos direcciones) para que las dos
hipótesis dejen de coincidir — eso es lo que hace este archivo, partiendo
de un plan REAL (`edecan_api.deps.flags_for_plan`, que lee
`edecan_schemas.plans.PLANES` de verdad) para el resto de flags, y
sobreescribiendo SOLO `voice.cloning`/`tools.podcast` de forma aislada
(mismo mecanismo `dependency_overrides[get_current_user]` que ya usa
`test_voz_avanzada.py::_headers_con_cloning`, generalizado a controlar
ambos flags a la vez en vez de solo uno).

## Diseño de cada caso: sin tocar la sesión más de lo necesario

Para la dirección "debería pasar el gate", cada endpoint recibe un payload
mínimo que dispara la PRIMERA validación propia del endpoint (después del
gate de flag, antes de cualquier escritura) — así el resultado es
determinista sin necesitar montar ElevenLabs/S3/ffmpeg de por medio:

- `POST /clones` con `nombre=""` -> `400` ("nombre es obligatorio"),
  ANTES de tocar `session`/S3 (ver `voz_avanzada.py::crear_clon_voz`).
- `POST /podcasts` con `titulo="   "` -> `400` ("titulo es obligatorio"),
  ANTES de tocar `session` (ver `voz_avanzada.py::crear_podcast_endpoint`).
- `GET /clones`, `GET /podcasts` -> `200 []` con `fake_session.respuestas =
  [[]]` (un único `SELECT` vacío).
- `DELETE /clones/{id}`, `GET /podcasts/{id}` -> `404` con
  `fake_session.respuestas = [[]]` (un único `SELECT` que no encuentra
  nada).

Para la dirección "debería bloquear", CUALQUIER payload basta (incluso uno
vacío): `_require_voice_cloning`/`_require_tools_podcast` son la PRIMERA
dependencia que FastAPI resuelve (antes de siquiera intentar leer/validar
el cuerpo del endpoint), así que el `403` sale sin tocar `fake_session` —
se verifica explícitamente con `fake_session.llamadas == []`, mismo
criterio que el resto de tests de gates de este router.

`FakeSession`/`FakeResult` duplicadas a propósito de `test_voz_avanzada.py`/
`test_podcasts_router.py` (`ARCHITECTURE.md` §10.1: los tests no importan
símbolos de otros archivos de test, ver la lección de
`packages/core/tests/test_agent_extra_tools.py` en
`docs/cumplimiento/barrido-evidencia-v6.md`).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from conftest import auth_headers
from edecan_schemas.plans import FLAG_TOOLS_PODCAST, FLAG_VOICE_CLONING, PLANES
from httpx import ASGITransport, AsyncClient

from edecan_api import deps as edecan_deps
from edecan_api.routers import voz_avanzada

# Plan base "real" del que se parte para el resto de flags (ambos objetivo
# valen `True` en este plan concreto — se sobreescriben de forma aislada
# abajo, ver `_flags_aislados`). Cualquier plan real serviría como base;
# `hosted_pro` es el mismo que ya usa `test_voz_avanzada.py::
# _headers_con_cloning` para el camino feliz de clonación.
_PLAN_BASE = "hosted_pro"


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
        self.commits += 1


@pytest.fixture
def fake_session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def _mounted_app(app, fake_session: FakeSession):
    """`app` (de `conftest.py`) + `voz_avanzada.router` montado +
    `get_tenant_session` reemplazado por `fake_session` — mismo patrón que
    `test_voz_avanzada.py`/`test_podcasts_router.py`."""
    app.include_router(voz_avanzada.router)
    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: fake_session
    return app


@pytest.fixture
async def client(_mounted_app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=_mounted_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _flags_aislados(*, voice_cloning: bool, tools_podcast: bool) -> dict[str, Any]:
    """Flags de un plan REAL (`edecan_schemas.plans.PLANES[_PLAN_BASE]`, vía
    `edecan_api.deps.flags_for_plan`) con SOLO `voice.cloning`/`tools.podcast`
    sobreescritos de forma aislada — ver el docstring del módulo para por qué
    hace falta esto en vez de un `plan_key` real tal cual."""
    flags = dict(edecan_deps.flags_for_plan(_PLAN_BASE))
    assert PLANES[_PLAN_BASE].flags.get(FLAG_VOICE_CLONING) is True  # sanity: el plan base
    assert PLANES[_PLAN_BASE].flags.get(FLAG_TOOLS_PODCAST) is True  # trae ambos en True.
    flags[FLAG_VOICE_CLONING] = voice_cloning
    flags[FLAG_TOOLS_PODCAST] = tools_podcast
    return flags


def _headers_con_flags_aislados(
    app,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    voice_cloning: bool,
    tools_podcast: bool,
) -> dict[str, str]:
    user_id = user_id or uuid.uuid4()
    tenant = edecan_deps.TenantCtx(
        tenant_id=tenant_id,
        plan_key=_PLAN_BASE,
        flags=_flags_aislados(voice_cloning=voice_cloning, tools_podcast=tools_podcast),
    )
    current_user = edecan_deps.CurrentUser(user_id=user_id, tenant=tenant)
    app.dependency_overrides[edecan_deps.get_current_user] = lambda: current_user
    # El token en sí no importa (el override de arriba gana), pero hace
    # falta un Bearer con forma válida para pasar `_extract_bearer_token`.
    return auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key=_PLAN_BASE)


# Las dos direcciones que la matriz de PLANES reales NUNCA deja ver hoy (ver
# docstring del módulo): cloning encendido/podcast apagado, y viceversa.
_DIRECCIONES = (
    pytest.param(True, False, id="cloning_on_podcast_off"),
    pytest.param(False, True, id="cloning_off_podcast_on"),
)


# ---------------------------------------------------------------------------
# Endpoints de clonación (`/clones*`) — deben exigir SOLO `voice.cloning`.
# ---------------------------------------------------------------------------


async def _post_clones_nombre_vacio(client: AsyncClient, *, headers: dict[str, str]) -> Any:
    # `crear_clon_voz` declara `File(...)` (`consentimiento`/`muestras`), así
    # que Starlette exige `multipart/form-data` -- httpx solo lo codifica así
    # si `files=` viene presente (con `data=` solo, manda
    # `application/x-www-form-urlencoded` y el propio framework devuelve un
    # `422` estructural ANTES de llegar a nuestro código, contaminando el
    # resultado). `nombre` va con espacios (no `""` literal): un valor
    # multipart REALMENTE vacío hace que python-multipart/Starlette lo
    # reporte como campo `missing` (también `422`, verificado empíricamente)
    # en vez de entregárselo a nuestro código — `" "` sigue siendo
    # "vacío" para el router (hace `.strip()` antes de comparar), pero SÍ
    # tiene bytes de contenido, así que el parser multipart lo entrega bien.
    # El contenido de `consentimiento` no importa: el chequeo de `nombre`
    # vacío es LITERALMENTE la primera línea del cuerpo de la función, antes
    # de mirar attestation/consentimiento/muestras.
    return await client.post(
        "/v1/voz/clones",
        data={"nombre": "   "},
        files=[("consentimiento", ("c.mp3", b"x", "audio/mpeg"))],
        headers=headers,
    )


@pytest.mark.parametrize("voice_cloning,tools_podcast", _DIRECCIONES)
async def test_crear_clon_exige_su_propio_flag_no_el_de_podcast(
    client, fake_session: FakeSession, app, voice_cloning: bool, tools_podcast: bool
) -> None:
    headers = _headers_con_flags_aislados(
        app, tenant_id=uuid.uuid4(), voice_cloning=voice_cloning, tools_podcast=tools_podcast
    )
    response = await _post_clones_nombre_vacio(client, headers=headers)

    if voice_cloning:
        # El gate de voice.cloning lo dejó pasar -- llega a la validación
        # propia del endpoint (nombre vacío), sin importar tools.podcast.
        assert response.status_code == 400
        assert "nombre" in response.json()["detail"].lower()
    else:
        assert response.status_code == 403
        assert fake_session.llamadas == []


@pytest.mark.parametrize("voice_cloning,tools_podcast", _DIRECCIONES)
async def test_listar_clones_exige_su_propio_flag_no_el_de_podcast(
    client, fake_session: FakeSession, app, voice_cloning: bool, tools_podcast: bool
) -> None:
    fake_session.respuestas = [[]]
    headers = _headers_con_flags_aislados(
        app, tenant_id=uuid.uuid4(), voice_cloning=voice_cloning, tools_podcast=tools_podcast
    )
    response = await client.get("/v1/voz/clones", headers=headers)

    if voice_cloning:
        assert response.status_code == 200
        assert response.json() == []
    else:
        assert response.status_code == 403
        assert fake_session.llamadas == []


@pytest.mark.parametrize("voice_cloning,tools_podcast", _DIRECCIONES)
async def test_revocar_clon_exige_su_propio_flag_no_el_de_podcast(
    client, fake_session: FakeSession, app, voice_cloning: bool, tools_podcast: bool
) -> None:
    fake_session.respuestas = [[]]  # SELECT vacío -> 404 si el gate deja pasar
    headers = _headers_con_flags_aislados(
        app, tenant_id=uuid.uuid4(), voice_cloning=voice_cloning, tools_podcast=tools_podcast
    )
    response = await client.delete(f"/v1/voz/clones/{uuid.uuid4()}", headers=headers)

    if voice_cloning:
        assert response.status_code == 404
    else:
        assert response.status_code == 403
        assert fake_session.llamadas == []


# ---------------------------------------------------------------------------
# Endpoints de podcasts (`/podcasts*`) — deben exigir SOLO `tools.podcast`.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("voice_cloning,tools_podcast", _DIRECCIONES)
async def test_crear_podcast_exige_su_propio_flag_no_el_de_cloning(
    client, fake_session: FakeSession, app, voice_cloning: bool, tools_podcast: bool
) -> None:
    headers = _headers_con_flags_aislados(
        app, tenant_id=uuid.uuid4(), voice_cloning=voice_cloning, tools_podcast=tools_podcast
    )
    response = await client.post(
        "/v1/voz/podcasts",
        json={"titulo": "   ", "guion": [{"texto": "hola"}]},
        headers=headers,
    )

    if tools_podcast:
        # El gate de tools.podcast lo dejó pasar -- llega a la validación
        # propia del endpoint (titulo vacío), sin importar voice.cloning.
        assert response.status_code == 400
        assert "titulo" in response.json()["detail"].lower()
    else:
        assert response.status_code == 403
        assert fake_session.llamadas == []


@pytest.mark.parametrize("voice_cloning,tools_podcast", _DIRECCIONES)
async def test_listar_podcasts_exige_su_propio_flag_no_el_de_cloning(
    client, fake_session: FakeSession, app, voice_cloning: bool, tools_podcast: bool
) -> None:
    fake_session.respuestas = [[]]
    headers = _headers_con_flags_aislados(
        app, tenant_id=uuid.uuid4(), voice_cloning=voice_cloning, tools_podcast=tools_podcast
    )
    response = await client.get("/v1/voz/podcasts", headers=headers)

    if tools_podcast:
        assert response.status_code == 200
        assert response.json() == []
    else:
        assert response.status_code == 403
        assert fake_session.llamadas == []


@pytest.mark.parametrize("voice_cloning,tools_podcast", _DIRECCIONES)
async def test_obtener_podcast_exige_su_propio_flag_no_el_de_cloning(
    client, fake_session: FakeSession, app, voice_cloning: bool, tools_podcast: bool
) -> None:
    fake_session.respuestas = [[]]  # SELECT vacío -> 404 si el gate deja pasar
    headers = _headers_con_flags_aislados(
        app, tenant_id=uuid.uuid4(), voice_cloning=voice_cloning, tools_podcast=tools_podcast
    )
    response = await client.get(f"/v1/voz/podcasts/{uuid.uuid4()}", headers=headers)

    if tools_podcast:
        assert response.status_code == 404
    else:
        assert response.status_code == 403
        assert fake_session.llamadas == []


# ---------------------------------------------------------------------------
# Regresión pinned: las tools de agente de `edecan_voice.tools` (listar_voces/
# sintetizar_voz) gatean `voice.web` -- NUNCA `voice.cloning` ni
# `tools.podcast` (ninguna de las dos clona ni genera podcasts, ver
# `ARCHITECTURE.md` §14.e y el docstring de `edecan_voice.tools`). Ya
# cubierto por `packages/voice/tests/test_voice_tools.py`
# (`test_listar_voces_no_es_dangerous_y_gatea_voice_web`/
# `test_sintetizar_voz_no_es_dangerous_y_gatea_voice_web`); se repite aquí en
# `apps/api` importando las clases reales, mismo criterio de "excepción
# deliberada a ARCHITECTURE.md §10.1" que `test_v6_sweep_flags.py` (cruzar
# paquetes hermanos a propósito: un fake nunca reproduce un desync real).
# ---------------------------------------------------------------------------


def test_tools_de_voz_no_gatean_cloning_ni_podcast() -> None:
    from edecan_voice.tools import ListarVocesTool, SintetizarVozTool

    for tool_cls in (ListarVocesTool, SintetizarVozTool):
        flags = tool_cls().requires_flags
        assert flags == frozenset({"voice.web"})
        assert FLAG_VOICE_CLONING not in flags
        assert FLAG_TOOLS_PODCAST not in flags
