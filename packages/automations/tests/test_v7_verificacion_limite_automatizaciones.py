"""Verificación del hallazgo candidato de WP-V7-08 (barrido v7, routers restantes):
¿`GestionarAutomatizacionTool` (`edecan_automations.tools`) aplica
`LIMIT_AUTOMATIONS_ACTIVE` igual que lo aplica `apps/api/edecan_api/routers/
automations.py::_check_limit`, análogo al fix de `delegar_mision`/`_cupo_disponible`
(`HOTFIXES_PENDIENTES.md`, "RESUELTO (2026-07-09): `delegar_mision` no aplicaba
`limits.missions_per_day`")?

**Conclusión de la investigación (documentada aquí, ver también
`docs/cumplimiento/barrido-v7-routers-restantes.md`): NO es el mismo bug —
`GestionarAutomatizacionTool._bajo_limite` (`tools.py`) YA aplica
`LIMIT_AUTOMATIONS_ACTIVE` desde WP-V6-02**, con la MISMA semántica fail-closed que
el router (`-1` ilimitado se salta el `COUNT`; cualquier otro valor, incluido `0`
ausente, compara contra `SELECT COUNT(*) ... WHERE enabled = true`) — ya cubierto
extensamente por `test_tools.py` (`test_crear_en_el_limite_del_plan_no_inserta`,
`test_activar_en_el_limite_no_actualiza`, etc.) y por `test_v6_paridad_flag_router.py`
(compara el VALOR de la constante local contra `edecan_schemas.plans`, para que un
cambio futuro del catálogo no la desincronice en silencio). Este archivo NO repite
esa cobertura — agrega dos ángulos que faltaban:

1. El caso límite exacto que si tuviera el bug de `delegar_mision` explotaría: un
   `ctx.extras["flags"]` que NO trae la clave `LIMIT_AUTOMATIONS_ACTIVE` en absoluto
   (no `0` explícito — la clave AUSENTE del todo, lo que pasa con un `plan_key`
   huérfano vía `edecan_api.deps.flags_for_plan`, que devuelve `{}`, o con
   cualquier extensión futura de flags que se olvide de este límite). Ninguno de los
   tests existentes de `test_tools.py` ejercita `crear`/`activar` con un diccionario
   de flags COMPLETAMENTE vacío (solo `test_desactivar_exitoso_no_chequea_limite` usa
   flags vacíos, pero para la acción que a propósito NO chequea límite).
2. Un centinela estructural (`inspect.getsource`) que ancla el DEFAULT exacto de
   `_bajo_limite` en `0` (fail-closed) — mismo espíritu que
   `test_v7_sweep_v4residual.py::test_cargar_credenciales_push_no_acepta_ni_puede_leer_settings`
   (verificar la FORMA del código, no solo su comportamiento hoy): si alguien
   "simplificara" ese default a `_UNLIMITED`/`-1` en el futuro (exactamente el bug de
   fail-open que este mismo WP encontró y corrigió en `apps/api/edecan_api/routers/
   files.py::_check_storage_quota`/`voice.py::_check_voice_quota` — ver el informe),
   este centinela lo detecta incluso si algún test de comportamiento no cubriera ese
   escenario exacto.
"""

from __future__ import annotations

import inspect

from edecan_automations.tools import (
    FLAG_AUTOMATIONS_RULES,
    LIMIT_AUTOMATIONS_ACTIVE,
    GestionarAutomatizacionTool,
)

UNLIMITED = -1


async def test_crear_sin_flags_en_absoluto_deniega_sin_insertar(
    make_ctx, make_session, make_result
) -> None:
    """`ctx.extras["flags"] == {}` (ninguna clave, ni siquiera
    `LIMIT_AUTOMATIONS_ACTIVE`) -- el mismo estado que deja un `plan_key` huérfano en
    el router HTTP (`edecan_api.deps.flags_for_plan` devuelve `{}`). `_bajo_limite`
    SÍ ejecuta el `COUNT` (no hay atajo para el caso "clave ausente" distinto del
    caso "0 explícito" -- a diferencia de `edecan_agents.tools.DelegarMisionTool.
    _cupo_disponible`, que sí corta antes con `if limite == 0: return False`), pero
    el resultado es el mismo: `activas < 0` es SIEMPRE falso, así que nunca inserta."""
    session = make_session([make_result(scalar=0)])
    ctx = make_ctx(session=session)  # sin pasar `flags` -> `make_ctx` deja `{}`

    resultado = await GestionarAutomatizacionTool().run(
        ctx,
        {
            "accion": "crear",
            "nombre": "Reporte diario",
            "rrule": "FREQ=DAILY",
            "instruccion": "Manda el reporte de ventas.",
        },
    )

    assert "límite" in resultado.content
    assert len(session.llamadas) == 1  # solo el COUNT -- nunca llegó al INSERT
    sql_ejecutado = session.llamadas[0][0].upper()
    assert "COUNT(*)" in sql_ejecutado
    assert "INSERT INTO AUTOMATIONS" not in sql_ejecutado


async def test_activar_sin_flags_en_absoluto_deniega_sin_actualizar(
    make_ctx, make_session, make_result
) -> None:
    """Misma verificación que la anterior, para la acción `activar` (la otra que sí
    chequea el límite -- `desactivar`/`listar` no lo necesitan, ver docstring de
    `tools.py`)."""
    session = make_session([make_result(scalar=0)])
    ctx = make_ctx(session=session)

    resultado = await GestionarAutomatizacionTool().run(
        ctx, {"accion": "activar", "automation_id": "33333333-3333-3333-3333-333333333333"}
    )

    assert "límite" in resultado.content
    assert len(session.llamadas) == 1  # solo el COUNT -- nunca llegó al UPDATE


def test_bajo_limite_default_es_fail_closed_no_ilimitado() -> None:
    """Centinela estructural (ver docstring del módulo): el default de
    `_tenant_flags(ctx).get(LIMIT_AUTOMATIONS_ACTIVE, ...)` dentro de `_bajo_limite`
    debe seguir siendo `0`, nunca `_UNLIMITED`/`-1` -- mismo patrón de regresión que
    este WP encontró y corrigió en `files.py`/`voice.py` (defaulteaban a `UNLIMITED`
    cuando la clave faltaba, en vez de `0`)."""
    fuente = inspect.getsource(GestionarAutomatizacionTool._bajo_limite)
    assert f"{LIMIT_AUTOMATIONS_ACTIVE!r}" not in fuente  # usa el símbolo, no un literal repetido
    assert "LIMIT_AUTOMATIONS_ACTIVE, 0)" in fuente, (
        "_bajo_limite debe defaultear a 0 (fail-closed) cuando la clave de límite no "
        "está en ctx.extras['flags'] -- ver el hallazgo de files.py/voice.py en este "
        "mismo WP para por qué UNLIMITED sería fail-open."
    )


def test_tool_requiere_flag_y_aplica_limite_con_las_constantes_canonicas() -> None:
    """Resumen de la verificación completa del hallazgo candidato: `requires_flags`
    usa `FLAG_AUTOMATIONS_RULES` (gate binario del plan) Y, además, `_bajo_limite`
    aplica `LIMIT_AUTOMATIONS_ACTIVE` (cuota) -- las DOS capas que
    `apps/api/edecan_api/routers/automations.py` también exige
    (`_require_automations_flag` + `_check_limit`), replicadas del lado de la tool
    de chat. Complementa (no repite) `test_v6_paridad_flag_router.py`, que solo
    compara los VALORES de las constantes."""
    assert GestionarAutomatizacionTool.requires_flags == frozenset({FLAG_AUTOMATIONS_RULES})
    fuente_run = inspect.getsource(GestionarAutomatizacionTool._crear)
    assert "_bajo_limite" in fuente_run
    fuente_activar = inspect.getsource(GestionarAutomatizacionTool._set_enabled)
    assert "_bajo_limite" in fuente_activar
