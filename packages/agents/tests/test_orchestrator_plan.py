"""`edecan_agents.orchestrator.Orchestrator.plan` — plan estructurado vía LLM,
parseo tolerante, validación de agente, resolución de `depende_de`
(WP-V5-05), y truncado a presupuesto."""

from __future__ import annotations

import json
from types import SimpleNamespace

import edecan_agents.orchestrator as orchestrator_module
import pytest
from edecan_agents.orchestrator import Orchestrator


@pytest.fixture
def plan(make_tool_registry):
    async def _plan(router, objetivo="Investiga el mercado de CRMs", flags=None, settings=None):
        orchestrator = Orchestrator(router, make_tool_registry())
        return await orchestrator.plan(objetivo, flags or {}, settings)

    return _plan


async def test_plan_json_valido_directo(make_llm_router, plan):
    router = make_llm_router(
        responses=['{"pasos": [{"agente": "research", "instruccion": "Busca datos de mercado"}]}']
    )
    pasos = await plan(router)
    assert pasos == [
        {"seq": 1, "agente": "research", "instruccion": "Busca datos de mercado", "depende_de": []}
    ]
    # La planificación es trabajo pesado y usa el alias profundo.
    assert router.resolved[0][0] == "profundo"


async def test_plan_json_envuelto_en_prosa_y_markdown(make_llm_router, plan):
    respuesta = (
        "Claro, aquí está el plan:\n```json\n"
        '{"pasos": [{"agente": "research", "instruccion": "Paso uno"}, '
        '{"agente": "data_analyst", "instruccion": "Paso dos"}]}\n```\n'
        "Espero que ayude."
    )
    router = make_llm_router(responses=[respuesta])
    pasos = await plan(router)
    # sin `depende_de` explícito en el JSON del LLM -> cadena secuencial
    # retrocompatible: cada paso depende de TODOS los anteriores (aquí solo
    # hay uno antes del segundo), ver `_resolver_depende_de`.
    assert pasos == [
        {"seq": 1, "agente": "research", "instruccion": "Paso uno", "depende_de": []},
        {"seq": 2, "agente": "data_analyst", "instruccion": "Paso dos", "depende_de": [0]},
    ]


async def test_plan_json_como_lista_bare_sin_envoltorio_pasos(make_llm_router, plan):
    router = make_llm_router(
        responses=['[{"agente": "content", "instruccion": "Redacta el resumen"}]']
    )
    pasos = await plan(router)
    assert pasos == [
        {"seq": 1, "agente": "content", "instruccion": "Redacta el resumen", "depende_de": []}
    ]


async def test_plan_json_invalido_cae_a_un_paso_research(make_llm_router, plan):
    router = make_llm_router(responses=["esto no es JSON en absoluto"])
    pasos = await plan(router, objetivo="Organiza mi semana")
    assert pasos == [
        {"seq": 1, "agente": "research", "instruccion": "Organiza mi semana", "depende_de": []}
    ]


async def test_plan_agente_inexistente_se_reasigna_a_research(make_llm_router, plan):
    """Una clave que el planificador inventó y que ni siquiera existe en
    `PROFILES` también se reasigna a `research`: `_pasos_desde_json` solo
    verifica pertenencia a `IMPLEMENTED_AGENT_KEYS`, no distingue el motivo
    (ver `test_plan_agente_declarado_no_disponible_sigue_reasignandose_a_research`
    para el caso — hoy solo simulable, ver ese test — de un perfil que
    EXISTE en `PROFILES` pero sigue `disponible=False`)."""
    router = make_llm_router(
        responses=[
            '{"pasos": [{"agente": "coordinador_ventas_inventado", '
            '"instruccion": "Evalúa el negocio"}]}'
        ]
    )
    pasos = await plan(router)
    assert pasos == [
        {"seq": 1, "agente": "research", "instruccion": "Evalúa el negocio", "depende_de": []}
    ]


async def test_plan_pasos_sin_instruccion_se_descartan(make_llm_router, plan):
    router = make_llm_router(
        responses=[
            '{"pasos": [{"agente": "research", "instruccion": ""}, '
            '{"agente": "content", "instruccion": "  "}, '
            '{"agente": "data_analyst", "instruccion": "Analiza la tabla"}]}'
        ]
    )
    pasos = await plan(router)
    # los dos pasos con instrucción vacía se descartan ANTES de resolver
    # `depende_de` -> el único sobreviviente queda en el índice 0 (sin deps),
    # no en el índice 2 que tenía en la lista cruda del LLM.
    assert pasos == [
        {"seq": 1, "agente": "data_analyst", "instruccion": "Analiza la tabla", "depende_de": []}
    ]


async def test_plan_todos_los_pasos_vacios_cae_a_fallback(make_llm_router, plan):
    router = make_llm_router(responses=['{"pasos": [{"agente": "research", "instruccion": ""}]}'])
    pasos = await plan(router, objetivo="Objetivo original")
    assert pasos == [
        {"seq": 1, "agente": "research", "instruccion": "Objetivo original", "depende_de": []}
    ]


async def test_plan_trunca_al_presupuesto_de_settings(make_llm_router, plan):
    respuesta = (
        '{"pasos": ['
        '{"agente": "research", "instruccion": "uno"}, '
        '{"agente": "research", "instruccion": "dos"}, '
        '{"agente": "research", "instruccion": "tres"}]}'
    )
    router = make_llm_router(responses=[respuesta])
    settings = SimpleNamespace(MISSIONS_MAX_STEPS=2)
    pasos = await plan(router, settings=settings)
    assert [p["seq"] for p in pasos] == [1, 2]
    assert [p["instruccion"] for p in pasos] == ["uno", "dos"]
    # el 3er paso ("tres") se truncó ANTES de resolver `depende_de` -> el
    # 2do paso depende solo del 1ro (índice 0), no de un 3ro que no existe.
    assert [p["depende_de"] for p in pasos] == [[], [0]]


async def test_plan_sin_settings_usa_default_8_sin_reventar(make_llm_router, plan):
    respuesta = '{"pasos": [{"agente": "research", "instruccion": "x"}]}'
    router = make_llm_router(responses=[respuesta])
    pasos = await plan(router, settings=None)
    assert len(pasos) == 1


async def test_plan_settings_sin_missions_max_steps_usa_default_8(make_llm_router, plan):
    nueve_pasos = [{"agente": "research", "instruccion": f"paso {i}"} for i in range(9)]
    respuesta = json.dumps({"pasos": nueve_pasos})
    router = make_llm_router(responses=[respuesta])
    pasos = await plan(router, settings=SimpleNamespace())  # sin MISSIONS_MAX_STEPS
    assert len(pasos) == 8  # default DEFAULT_MAX_STEPS, no revienta con getattr


async def test_plan_llm_lanza_excepcion_cae_a_fallback(make_llm_router, plan):
    router = make_llm_router(responses=[RuntimeError("proveedor caído")])
    pasos = await plan(router, objetivo="Objetivo pese al error")
    assert pasos == [
        {
            "seq": 1,
            "agente": "research",
            "instruccion": "Objetivo pese al error",
            "depende_de": [],
        }
    ]


async def test_plan_pasa_los_flags_al_resolve(make_llm_router, plan):
    router = make_llm_router(responses=['{"pasos": [{"agente": "research", "instruccion": "x"}]}'])
    flags = {"agents.missions": True, "models.premium": False}
    await plan(router, flags=flags)
    assert router.resolved[0] == ("profundo", flags)


# ---------------------------------------------------------------------------
# WP-V5-05: `depende_de` -- integración completa vía LLM+JSON (la validación
# en sí, unitaria y exhaustiva, vive en `test_dependencias_y_paralelismo.py`;
# aquí solo se confirma que el pipeline completo de `plan()` la conecta).
# ---------------------------------------------------------------------------


async def test_plan_depende_de_explicito_y_valido_se_conserva(make_llm_router, plan):
    respuesta = (
        '{"pasos": ['
        '{"agente": "research", "instruccion": "uno", "depende_de": []}, '
        '{"agente": "research", "instruccion": "dos", "depende_de": []}, '
        '{"agente": "data_analyst", "instruccion": "tres", "depende_de": [0, 1]}]}'
    )
    router = make_llm_router(responses=[respuesta])
    pasos = await plan(router)
    assert [p["depende_de"] for p in pasos] == [[], [], [0, 1]]


async def test_plan_depende_de_con_ciclo_de_dos_pasos_se_degrada_a_secuencial(
    make_llm_router, plan
):
    """El caso "ciclo" del WP, de punta a punta a través de `plan()`: el LLM
    propone que el paso 0 dependa del 1 (referencia hacia adelante, inválida
    por sí sola) y que el paso 1 dependa del 0 (válida). Solo la referencia
    inválida se descarta -- el resultado ya no tiene ciclo."""
    respuesta = (
        '{"pasos": ['
        '{"agente": "research", "instruccion": "A", "depende_de": [1]}, '
        '{"agente": "research", "instruccion": "B", "depende_de": [0]}]}'
    )
    router = make_llm_router(responses=[respuesta])
    pasos = await plan(router)
    assert pasos[0]["depende_de"] == []  # se degradó (idx=0 no tiene "anterior").
    assert pasos[1]["depende_de"] == [0]  # se conserva, era válida por sí sola.


async def test_plan_depende_de_con_indice_fuera_de_rango_se_degrada(make_llm_router, plan):
    respuesta = (
        '{"pasos": ['
        '{"agente": "research", "instruccion": "uno", "depende_de": []}, '
        '{"agente": "research", "instruccion": "dos", "depende_de": []}, '
        '{"agente": "data_analyst", "instruccion": "tres", "depende_de": [99]}]}'
    )
    router = make_llm_router(responses=[respuesta])
    pasos = await plan(router)
    # se descarta la lista completa (no solo el 99) y cae a [idx - 1] = [1].
    assert pasos[2]["depende_de"] == [1]


async def test_plan_sin_depende_de_en_ningun_paso_es_retrocompatible(make_llm_router, plan):
    """Confirmación explícita a nivel `plan()` de la retrocompatibilidad
    (ver también `test_orchestrator_run.py::
    test_plan_viejo_sin_depende_de_sigue_ejecutandose_100_por_ciento_secuencial`
    para la ejecución): sin la clave en NINGÚN paso, cada uno depende de
    TODOS los anteriores."""
    respuesta = (
        '{"pasos": ['
        '{"agente": "research", "instruccion": "uno"}, '
        '{"agente": "research", "instruccion": "dos"}, '
        '{"agente": "data_analyst", "instruccion": "tres"}]}'
    )
    router = make_llm_router(responses=[respuesta])
    pasos = await plan(router)
    assert [p["depende_de"] for p in pasos] == [[], [0], [0, 1]]


# ---------------------------------------------------------------------------
# WP-V4-05: los 12 perfiles recién activados también son elegibles por
# `plan()` (`IMPLEMENTED_AGENT_KEYS` creció de 3 a 15) y el planificador los
# describe en su system prompt. `voice` (WP-V5-05) se suma al mismo grupo:
# `IMPLEMENTED_AGENT_KEYS` llega a las 16 claves pinned completas.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "agente",
    [
        "ceo",
        "design",
        "legal",
        "video",
        "finance",
        "marketing",
        "sales",
        "social_media",
        "developer",
        "qa",
        "security",
        "devops",
        "voice",
    ],
)
async def test_plan_no_reasigna_un_paso_a_un_perfil_activado(make_llm_router, plan, agente):
    """Antes de WP-V4-05/WP-V5-05, ninguno de estos 13 estaba en
    `IMPLEMENTED_AGENT_KEYS`, así que `_pasos_desde_json` los reasignaba a
    `research` (ver `test_plan_agente_declarado_no_disponible_sigue_reasignandose_a_research`
    para el caso — hoy solo simulable con un perfil sintético, ver ese test —
    de un perfil que SIGUE sin estar disponible). Ahora deben conservarse tal
    cual."""
    respuesta = f'{{"pasos": [{{"agente": "{agente}", "instruccion": "Instrucción de prueba"}}]}}'
    router = make_llm_router(responses=[respuesta])
    pasos = await plan(router)
    assert pasos == [
        {"seq": 1, "agente": agente, "instruccion": "Instrucción de prueba", "depende_de": []}
    ]


async def test_plan_agente_declarado_no_disponible_sigue_reasignandose_a_research(
    make_llm_router, plan, monkeypatch: pytest.MonkeyPatch
):
    """`_pasos_desde_json` solo verifica pertenencia a `IMPLEMENTED_AGENT_KEYS`
    — no distingue "la clave no existe en absoluto" (ver
    `test_plan_agente_inexistente_se_reasigna_a_research`) de "existe en
    `PROFILES` pero no está implementada", así que ambos casos comparten el
    mismo código. Tras `WP-V5-05` las 16 claves pinned quedan
    `disponible=True` (`voice` era la última, ver `profiles.py`), así que ya
    no queda ningún ejemplo REAL del segundo caso — se simula excluyendo
    `"voice"` de `IMPLEMENTED_AGENT_KEYS` solo para este test, para seguir
    cubriendo la rama tal como la vería el planificador si un perfil
    existente se "despublicara"."""
    monkeypatch.setattr(
        orchestrator_module, "IMPLEMENTED_AGENT_KEYS", frozenset({"research", "data_analyst"})
    )

    router = make_llm_router(
        responses=['{"pasos": [{"agente": "voice", "instruccion": "Contesta la llamada"}]}']
    )
    pasos = await plan(router)
    assert pasos == [
        {"seq": 1, "agente": "research", "instruccion": "Contesta la llamada", "depende_de": []}
    ]


async def test_plan_system_prompt_describe_los_trece_perfiles_activados_por_wp_v4_05_y_v5_05(
    make_llm_router, plan
):
    router = make_llm_router(responses=['{"pasos": [{"agente": "research", "instruccion": "x"}]}'])
    await plan(router)

    assert len(router.provider.requests) == 1
    system_prompt = router.provider.requests[0].system

    for agente in (
        "ceo",
        "design",
        "legal",
        "video",
        "finance",
        "marketing",
        "sales",
        "social_media",
        "developer",
        "qa",
        "security",
        "devops",
        "voice",
    ):
        assert f"- {agente}:" in system_prompt, agente


async def test_plan_system_prompt_menciona_depende_de(make_llm_router, plan):
    """`plan()` documenta el campo `depende_de` en el prompt de
    planificación (WP-V5-05) para que el LLM sepa que puede declararlo."""
    router = make_llm_router(responses=['{"pasos": [{"agente": "research", "instruccion": "x"}]}'])
    await plan(router)
    system_prompt = router.provider.requests[0].system
    assert "depende_de" in system_prompt


async def test_plan_system_prompt_no_describe_un_perfil_no_disponible(
    make_llm_router, plan, monkeypatch: pytest.MonkeyPatch
):
    """Ningún perfil con `disponible=False` aparece en el system prompt del
    planificador (`_planner_system_prompt` filtra por `p.disponible`) — tras
    `WP-V5-05` no queda ningún ejemplo real (las 16 claves están
    `disponible=True`), así que se simula con un perfil sintético."""
    from edecan_agents.profiles import PROFILES, AgentProfile

    perfil_futuro = AgentProfile(
        key="futuro_no_disponible",
        nombre="Futuro",
        descripcion="perfil de prueba",
        system_prompt_extra="prueba",
        allowed_tools=frozenset(),
        disponible=False,
    )
    monkeypatch.setitem(PROFILES, "futuro_no_disponible", perfil_futuro)

    router = make_llm_router(responses=['{"pasos": [{"agente": "research", "instruccion": "x"}]}'])
    await plan(router)
    system_prompt = router.provider.requests[0].system
    assert "- futuro_no_disponible:" not in system_prompt
