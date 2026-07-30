"""`edecan_agents.orchestrator.Orchestrator.run` — ejecución por olas
(paralelismo/dependencias/replan/timeout, WP-V5-05; timing por paso,
WP-V6-10) de pasos con `Agent`/`registry` falsos (`ARCHITECTURE.md` §10.1:
sin importar `edecan_core`), contexto sintético entre pasos, presupuesto,
pausa por confirmación, reanudación y manejo de errores."""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import edecan_agents.orchestrator as orchestrator_module
import pytest
from edecan_agents.orchestrator import Mission, Orchestrator, _timing_usage
from edecan_agents.profiles import PROFILES
from edecan_agents.registry_view import RestrictedRegistry


@pytest.fixture
def wrapped_registry(make_tool, make_tool_registry):
    # nombres que cubren research/data_analyst/content (ver profiles.py).
    nombres = [
        "buscar_web",
        "navegar_web",
        "extraer_datos_web",
        "consultar_documentos",
        "hora_actual",
        "analizar_tabla",
        "extraer_tablas_pdf",
        "generar_grafico",
        "exportar_analisis",
        "calculadora",
        "generar_contenido",
        "crear_documento",
        "crear_presentacion",
        "crear_pdf",
    ]
    return make_tool_registry([make_tool(n) for n in nombres])


def _mission(**overrides) -> Mission:
    defaults = dict(
        id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        objetivo="Objetivo de prueba",
        plan=[
            {"seq": 1, "agente": "research", "instruccion": "Paso uno"},
            {"seq": 2, "agente": "data_analyst", "instruccion": "Paso dos"},
        ],
        presupuesto={"max_steps": 8},
    )
    defaults.update(overrides)
    return Mission(**defaults)


@pytest.fixture(autouse=True)
def _patch_agent(monkeypatch: pytest.MonkeyPatch):
    """Cada test arma su propio `FakeAgentFactory` y lo instala; este fixture
    solo deja el hook de monkeypatch listo bajo `orchestrator_module.Agent`."""

    def _install(factory):
        monkeypatch.setattr(orchestrator_module, "Agent", factory)
        return factory

    return _install


async def test_dos_pasos_exitosos_sintetiza_y_marca_done(
    make_llm_router, make_deps, _patch_agent, make_agent_factory, make_event, wrapped_registry
):
    factory = make_agent_factory(
        [
            [make_event(type="text_delta", text="Resultado del paso 1")],
            [make_event(type="text_delta", text="Resultado del paso 2")],
        ]
    )
    _patch_agent(factory)

    router = make_llm_router(responses=["Síntesis final para el usuario"])
    deps = make_deps(flags={"agents.missions": True})
    orchestrator = Orchestrator(router, wrapped_registry)
    mission = _mission()

    await orchestrator.run(mission, deps)

    # 2 pasos * (running + done) = 4 llamadas a save_step.
    assert [c["status"] for c in deps.step_calls] == ["running", "done", "running", "done"]
    assert deps.step_calls[1]["seq"] == 1
    assert deps.step_calls[1]["resultado"] == "Resultado del paso 1"
    assert deps.step_calls[3]["seq"] == 2
    assert deps.step_calls[3]["resultado"] == "Resultado del paso 2"

    assert len(deps.mission_calls) == 1
    assert deps.mission_calls[0]["status"] == "done"
    assert deps.mission_calls[0]["resultado"] == "Síntesis final para el usuario"
    assert deps.mission_calls[0]["error"] is None


async def test_el_resultado_del_paso_1_se_antepone_como_historial_del_paso_2(
    make_llm_router, make_deps, _patch_agent, make_agent_factory, make_event, wrapped_registry
):
    factory = make_agent_factory(
        [
            [make_event(type="text_delta", text="Resultado del paso 1")],
            [make_event(type="text_delta", text="Resultado del paso 2")],
        ]
    )
    _patch_agent(factory)
    router = make_llm_router(responses=["síntesis"])
    deps = make_deps()
    orchestrator = Orchestrator(router, wrapped_registry)

    await orchestrator.run(_mission(), deps)

    assert factory.calls[0].history == []  # paso 1: sin historial previo
    historial_paso_2 = factory.calls[1].history
    assert len(historial_paso_2) == 2
    assert historial_paso_2[0].role == "user"
    assert historial_paso_2[0].content == "Paso uno"
    assert historial_paso_2[1].role == "assistant"
    assert historial_paso_2[1].content == "Resultado del paso 1"
    assert factory.calls[1].user_text == "Paso dos"


async def test_cada_paso_usa_el_registry_recortado_de_su_propio_perfil(
    make_llm_router, make_deps, _patch_agent, make_agent_factory, make_event, wrapped_registry
):
    factory = make_agent_factory(
        [[make_event(type="text_delta", text="r1")], [make_event(type="text_delta", text="r2")]]
    )
    _patch_agent(factory)
    router = make_llm_router(responses=["síntesis"])
    deps = make_deps()
    orchestrator = Orchestrator(router, wrapped_registry)

    await orchestrator.run(_mission(), deps)

    registry_research = factory.registries[0]
    assert isinstance(registry_research, RestrictedRegistry)
    assert registry_research.get("buscar_web") is not None
    assert registry_research.get("crear_pdf") is None  # es de "content", no de "research"

    registry_data_analyst = factory.registries[1]
    assert registry_data_analyst.get("analizar_tabla") is not None
    assert registry_data_analyst.get("buscar_web") is None  # es de "research", no de "data_analyst"


async def test_cada_paso_pasa_el_model_alias_del_perfil_al_agent(
    make_llm_router, make_deps, _patch_agent, make_agent_factory, make_event, wrapped_registry
):
    """`Orchestrator._run_step` debe pasar `perfil.model_alias` al construir
    el `Agent` de cada paso (`profiles.py`) — si se le olvida, el campo queda
    declarado pero muerto y cada perfil termina resolviendo el alias interno
    por defecto de `Agent`, sin importar lo que diga su `model_alias`."""
    factory = make_agent_factory(
        [[make_event(type="text_delta", text="r1")], [make_event(type="text_delta", text="r2")]]
    )
    _patch_agent(factory)
    router = make_llm_router(responses=["síntesis"])
    deps = make_deps()
    orchestrator = Orchestrator(router, wrapped_registry)

    await orchestrator.run(_mission(), deps)  # perfiles: research, data_analyst

    assert factory.model_aliases == ["profundo", "profundo"]


async def test_agente_inexistente_cae_al_perfil_research(
    make_llm_router, make_deps, _patch_agent, make_agent_factory, make_event, wrapped_registry
):
    """`PROFILES.get(agente)` devuelve `None` para una clave que ni siquiera
    existe (a diferencia de `voice`, que existe pero `disponible=False`, ver
    el test siguiente) — `_run_step` cae igual al perfil `research`."""
    factory = make_agent_factory([[make_event(type="text_delta", text="r1")]])
    _patch_agent(factory)
    router = make_llm_router(responses=["síntesis"])
    deps = make_deps()
    orchestrator = Orchestrator(router, wrapped_registry)
    mission = _mission(
        plan=[{"seq": 1, "agente": "coordinador_ventas_inventado", "instruccion": "Analiza"}]
    )

    await orchestrator.run(mission, deps)

    assert factory.calls[0].persona.nombre_asistente == "Investigación"
    assert factory.registries[0].get("buscar_web") is not None


async def test_agente_declarado_no_disponible_cae_al_perfil_research(
    make_llm_router,
    make_deps,
    _patch_agent,
    make_agent_factory,
    make_event,
    wrapped_registry,
    monkeypatch: pytest.MonkeyPatch,
):
    """Tras `WP-V5-05` las 16 claves pinned quedan `disponible=True` (`voice`
    era la última en seguir `disponible=False`, ver `profiles.py`) — ya no
    queda ningún ejemplo REAL de un perfil "declarado pero no disponible"
    para ejercitar la rama `not perfil.disponible` de `_resolver_perfil`
    (distinta de la rama `perfil is None` del test anterior). Se inyecta un
    perfil sintético en `PROFILES` para seguir cubriendo ese camino de código
    (sigue vivo: nada impide declarar un 17º perfil `disponible=False` en el
    futuro, mismo patrón que usó `voice` hasta este WP)."""
    from edecan_agents.profiles import AgentProfile

    perfil_futuro = AgentProfile(
        key="futuro_no_disponible",
        nombre="Futuro",
        descripcion="perfil de prueba, todavía sin herramientas",
        system_prompt_extra="prueba",
        allowed_tools=frozenset(),
        disponible=False,
    )
    monkeypatch.setitem(orchestrator_module.PROFILES, "futuro_no_disponible", perfil_futuro)

    factory = make_agent_factory([[make_event(type="text_delta", text="r1")]])
    _patch_agent(factory)
    router = make_llm_router(responses=["síntesis"])
    deps = make_deps()
    orchestrator = Orchestrator(router, wrapped_registry)
    mission = _mission(
        plan=[{"seq": 1, "agente": "futuro_no_disponible", "instruccion": "Contesta la llamada"}]
    )

    await orchestrator.run(mission, deps)

    assert factory.calls[0].persona.nombre_asistente == "Investigación"
    assert factory.registries[0].get("buscar_web") is not None


async def test_confirmation_required_pausa_la_mision_y_no_ejecuta_mas_pasos(
    make_llm_router, make_deps, _patch_agent, make_agent_factory, make_event, wrapped_registry
):
    factory = make_agent_factory(
        [
            [
                make_event(
                    type="confirmation_required",
                    tool_call_id="call-1",
                    name="usar_computadora",
                    args={"cmd": "ls"},
                )
            ],
            [make_event(type="text_delta", text="nunca debería correr")],
        ]
    )
    _patch_agent(factory)
    router = make_llm_router(responses=["no debería llamarse"])
    deps = make_deps()
    orchestrator = Orchestrator(router, wrapped_registry)

    await orchestrator.run(_mission(), deps)

    assert len(factory.registries) == 1  # el paso 2 nunca se ejecutó
    assert [c["status"] for c in deps.step_calls] == ["running", "waiting_confirmation"]
    pendiente = deps.step_calls[1]["usage"]["pending_tool_call"]
    assert pendiente == {"id": "call-1", "name": "usar_computadora", "args": {"cmd": "ls"}}
    assert len(deps.mission_calls) == 1
    assert deps.mission_calls[0]["status"] == "waiting_confirmation"
    assert router.provider.requests == []  # nunca se llegó a sintetizar


async def test_error_event_marca_paso_y_mision_en_error_y_detiene(
    make_llm_router, make_deps, _patch_agent, make_agent_factory, make_event, wrapped_registry
):
    factory = make_agent_factory(
        [
            [make_event(type="text_delta", text="Resultado del paso 1")],
            [make_event(type="error", message="el proveedor LLM falló")],
        ]
    )
    _patch_agent(factory)
    router = make_llm_router(responses=["no debería llamarse"])
    deps = make_deps()
    orchestrator = Orchestrator(router, wrapped_registry)

    await orchestrator.run(_mission(), deps)

    assert [c["status"] for c in deps.step_calls] == ["running", "done", "running", "error"]
    assert deps.step_calls[3]["resultado"] == "el proveedor LLM falló"
    assert deps.mission_calls[-1] == {
        "status": "error",
        "resultado": None,
        "error": "el proveedor LLM falló",
    }


async def test_presupuesto_trunca_los_pasos_ejecutados(
    make_llm_router, make_deps, _patch_agent, make_agent_factory, make_event, wrapped_registry
):
    factory = make_agent_factory(
        [
            [make_event(type="text_delta", text="r1")],
            [make_event(type="text_delta", text="r2")],
            [make_event(type="text_delta", text="r3 -- no debería correr")],
        ]
    )
    _patch_agent(factory)
    router = make_llm_router(responses=["síntesis"])
    deps = make_deps()
    orchestrator = Orchestrator(router, wrapped_registry)
    mission = _mission(
        plan=[
            {"seq": 1, "agente": "research", "instruccion": "uno"},
            {"seq": 2, "agente": "research", "instruccion": "dos"},
            {"seq": 3, "agente": "research", "instruccion": "tres"},
        ],
        presupuesto={"max_steps": 2},
    )

    await orchestrator.run(mission, deps)

    assert len(factory.registries) == 2  # el paso 3 se truncó por presupuesto
    assert deps.mission_calls[0]["status"] == "done"


async def test_excepcion_inesperada_marca_la_mision_en_error_sin_lanzar(
    make_llm_router, make_deps, _patch_agent, make_agent_factory, make_event, wrapped_registry
):
    factory = make_agent_factory(
        [
            [make_event(type="text_delta", text="r1")],
            [make_event(type="text_delta", text="r2")],
        ]
    )
    _patch_agent(factory)
    # La síntesis final (después de los 2 pasos) lanza -> `run` no debe propagar.
    router = make_llm_router(responses=[RuntimeError("boom de síntesis")])
    deps = make_deps()
    orchestrator = Orchestrator(router, wrapped_registry)

    await orchestrator.run(_mission(), deps)

    # los 2 pasos sí llegaron a completarse antes del fallo de síntesis.
    assert [c["status"] for c in deps.step_calls] == ["running", "done", "running", "done"]
    assert len(deps.mission_calls) == 1
    assert deps.mission_calls[0]["status"] == "error"
    assert "boom de síntesis" in deps.mission_calls[0]["error"]


async def test_reanudacion_ejecuta_la_tool_aprobada_directo_sin_reinvocar_al_llm(
    make_llm_router,
    make_deps,
    _patch_agent,
    make_agent_factory,
    make_event,
    make_tool_registry,
):
    """Reanudar (`mission.resume_step_seq`/`approved_tool_*`) NUNCA debe
    reconstruir un `Agent`/reinvocar al LLM para el paso pendiente: el
    `tool_call_id` de una tool `dangerous` lo acuñó el proveedor LLM en la
    respuesta puntual que ya no existe, así que una llamada nueva jamás
    reproduciría ese id y la misión quedaría en un loop de "aprobar" que
    nunca progresa. En vez de eso, el paso resuelto debe ejecutar DIRECTO la
    tool/args aprobados, y el paso 3 (normal, posterior) debe seguir viendo
    el resultado de esa ejecución como historial sintético."""
    calls: list[dict[str, Any]] = []

    class _FakeDangerousTool:
        name = "enviar_correo"
        dangerous = True

        async def run(self, ctx: Any, args: dict[str, Any]) -> Any:
            calls.append({"ctx": ctx, "args": dict(args)})
            return SimpleNamespace(content="correo enviado a x@y.com")

    registry = make_tool_registry([_FakeDangerousTool()])
    # UN solo guion: el único Agent que debe construirse es el del paso 3.
    factory = make_agent_factory([[make_event(type="text_delta", text="Resultado del paso 3")]])
    _patch_agent(factory)
    router = make_llm_router(responses=["síntesis final"])
    deps = make_deps()
    orchestrator = Orchestrator(router, registry)

    mission = _mission(
        plan=[
            {
                "seq": 1,
                "agente": "research",
                "instruccion": "Paso uno",
                "status": "done",
                "resultado": "Resultado ya guardado del paso 1",
            },
            {"seq": 2, "agente": "sales", "instruccion": "Paso dos", "status": "pending"},
            {"seq": 3, "agente": "research", "instruccion": "Paso tres", "status": "pending"},
        ],
        resume_step_seq=2,
        approved_tool_call_id="call-guardado",
        approved_tool_name="enviar_correo",
        approved_tool_args={"to": "x@y.com"},
    )

    await orchestrator.run(mission, deps)

    # el paso 1 no se re-ejecuta; el paso 2 (reanudado) NO construye ningún
    # Agent -> el único registrado es el del paso 3.
    assert len(factory.registries) == 1

    # la tool aprobada sí se ejecutó, con los args EXACTOS que se aprobaron
    # (no lo que un LLM reinvocado pudiera decidir de nuevo).
    assert len(calls) == 1
    assert calls[0]["args"] == {"to": "x@y.com"}

    # el paso 3 ve, como historial sintético, el resultado real de la tool
    # ejecutada en el paso 2 (no un texto inventado por un LLM re-llamado).
    llamada_paso_3 = factory.calls[0]
    assert [(m.role, m.content) for m in llamada_paso_3.history] == [
        ("user", "Paso uno"),
        ("assistant", "Resultado ya guardado del paso 1"),
        ("user", "Paso dos"),
        ("assistant", "Listo, ejecuté «enviar_correo». correo enviado a x@y.com"),
    ]

    # save_step: paso 2 (running + done) y paso 3 (running + done); el
    # paso 1 no se vuelve a tocar porque ya estaba "done".
    assert [c["seq"] for c in deps.step_calls] == [2, 2, 3, 3]
    assert deps.step_calls[1]["status"] == "done"
    assert (
        deps.step_calls[1]["resultado"]
        == "Listo, ejecuté «enviar_correo». correo enviado a x@y.com"
    )
    assert deps.mission_calls[0]["status"] == "done"
    assert deps.mission_calls[0]["resultado"] == "síntesis final"


async def test_reanudacion_marca_error_si_la_tool_aprobada_ya_no_existe(
    make_llm_router, make_deps, _patch_agent, make_agent_factory, make_tool_registry
):
    """Si la tool aprobada desapareció del registro entre la pausa y la
    aprobación (p. ej. se desinstaló ese paquete de herramientas), reanudar
    debe fallar explícito (`error`) en vez de fingir éxito o reintentar con
    el LLM."""
    registry = make_tool_registry([])  # "enviar_correo" ya no está.
    factory = make_agent_factory([])
    _patch_agent(factory)
    router = make_llm_router(responses=["no debería llamarse"])
    deps = make_deps()
    orchestrator = Orchestrator(router, registry)

    mission = _mission(
        plan=[{"seq": 1, "agente": "sales", "instruccion": "Paso uno", "status": "pending"}],
        resume_step_seq=1,
        approved_tool_call_id="call-guardado",
        approved_tool_name="enviar_correo",
        approved_tool_args={"to": "x@y.com"},
    )

    await orchestrator.run(mission, deps)

    assert factory.registries == []  # nunca se construyó un Agent
    assert [c["status"] for c in deps.step_calls] == ["running", "error"]
    assert "enviar_correo" in deps.step_calls[1]["resultado"]
    assert deps.mission_calls[0]["status"] == "error"
    assert "enviar_correo" in deps.mission_calls[0]["error"]


async def test_reanudacion_no_ejecuta_la_tool_si_el_flag_del_plan_actual_no_esta_satisfecho(
    make_llm_router, make_deps, _patch_agent, make_agent_factory, make_tool_registry
):
    """plan-flag-bypass: `self._registry.get()` (a diferencia de `.specs()`,
    que sí filtra por flags al ANUNCIAR una tool) nunca revisaba
    `requires_flags` al EJECUTAR la tool aprobada — así que una tool
    `dangerous` propuesta cuando el tenant SÍ tenía el flag (p. ej.
    `publicar_social`/`connectors.social`, perfil `marketing`) podía
    ejecutarse igual tras la aprobación humana aunque el plan hubiera bajado
    (o el flag fino se hubiera revocado) ENTRE la pausa y la aprobación:
    `deps.flags` en la reanudación ya es el plan VIGENTE del tenant
    (`run_mission.py::handle` lo relee en cada job), nunca el de cuando se
    propuso — pero nada comparaba `tool.requires_flags` contra él antes de
    `tool.run()`. `_run_resumed_step` debe bloquear sin ejecutar la tool."""
    ejecuciones: list[dict[str, Any]] = []

    class _FakePublicarSocial:
        name = "publicar_social"
        dangerous = True
        requires_flags = frozenset({"connectors.social"})

        async def run(self, ctx: Any, args: dict[str, Any]) -> Any:
            ejecuciones.append(dict(args))
            return SimpleNamespace(content="publicado")

    registry = make_tool_registry([_FakePublicarSocial()])
    factory = make_agent_factory([])  # ningún Agent nuevo debería construirse.
    _patch_agent(factory)
    router = make_llm_router(responses=["no debería llamarse"])
    # El plan VIGENTE del tenant en este job ya no trae `connectors.social`
    # (downgrade/flag fino revocado entre la pausa y esta aprobación).
    deps = make_deps(flags={"agents.missions": True})
    orchestrator = Orchestrator(router, registry)
    mission = _mission(
        plan=[
            {
                "seq": 1,
                "agente": "marketing",
                "instruccion": "Publica el post",
                "status": "pending",
            }
        ],
        resume_step_seq=1,
        approved_tool_call_id="call-mkt-1",
        approved_tool_name="publicar_social",
        approved_tool_args={"red": "x", "texto": "hola"},
    )

    await orchestrator.run(mission, deps)

    assert ejecuciones == []  # la tool JAMÁS se ejecutó.
    assert factory.registries == []  # tampoco se reinvocó al LLM/Agent.
    assert [c["status"] for c in deps.step_calls] == ["running", "error"]
    assert "publicar_social" in deps.step_calls[1]["resultado"]
    assert deps.mission_calls[0]["status"] == "error"
    assert "publicar_social" in deps.mission_calls[0]["error"]


async def test_reanudacion_ejecuta_la_tool_si_el_flag_del_plan_actual_si_esta_satisfecho(
    make_llm_router, make_deps, _patch_agent, make_agent_factory, make_tool_registry
):
    """Contraparte del test anterior: si el flag SÍ está presente en el plan
    vigente (`deps.flags`) al momento de la reanudación, la revalidación
    nueva no debe bloquear el camino feliz — la tool se sigue ejecutando
    exactamente igual que antes de este fix."""
    ejecuciones: list[dict[str, Any]] = []

    class _FakePublicarSocial:
        name = "publicar_social"
        dangerous = True
        requires_flags = frozenset({"connectors.social"})

        async def run(self, ctx: Any, args: dict[str, Any]) -> Any:
            ejecuciones.append(dict(args))
            return SimpleNamespace(content="publicado")

    registry = make_tool_registry([_FakePublicarSocial()])
    factory = make_agent_factory([])  # ningún Agent nuevo debería construirse.
    _patch_agent(factory)
    router = make_llm_router(responses=["síntesis final"])
    deps = make_deps(flags={"connectors.social": True})
    orchestrator = Orchestrator(router, registry)
    mission = _mission(
        plan=[
            {
                "seq": 1,
                "agente": "marketing",
                "instruccion": "Publica el post",
                "status": "pending",
            }
        ],
        resume_step_seq=1,
        approved_tool_call_id="call-mkt-1",
        approved_tool_name="publicar_social",
        approved_tool_args={"red": "x", "texto": "hola"},
    )

    await orchestrator.run(mission, deps)

    assert ejecuciones == [{"red": "x", "texto": "hola"}]
    assert deps.step_calls[-1]["status"] == "done"
    assert deps.mission_calls[-1]["status"] == "done"
    assert deps.mission_calls[-1]["resultado"] == "síntesis final"


async def test_perfil_con_dangerous_pausa_la_mision_y_la_reanudacion_ejecuta_la_tool_una_sola_vez(
    make_llm_router, make_deps, _patch_agent, make_agent_factory, make_event, make_tool_registry
):
    """WP-V4-05: un paso asignado a un perfil con
    `permite_dangerous_con_confirmacion=True` (`developer`, que declara
    `usar_computadora` — `dangerous=True` — en su `allowed_tools`) debe
    poder pedir esa tool: el `RestrictedRegistry` que arma `_run_step` para
    ese paso ya NO la oculta (a diferencia de un perfil sin el flag, ver
    `test_cada_paso_usa_el_registry_recortado_de_su_propio_perfil`). Cuando
    el (fake) `Agent` emite `confirmation_required` para ella, la misión
    queda `waiting_confirmation` con el `pending_tool_call` persistido y la
    tool JAMÁS se ejecuta en este primer intento; solo tras la reanudación
    (`resume_step_seq`/`approved_tool_*`, simulando el
    `POST /v1/missions/{id}/confirm` real) se ejecuta, y exactamente una
    vez."""
    assert PROFILES["developer"].permite_dangerous_con_confirmacion is True
    assert "usar_computadora" in PROFILES["developer"].allowed_tools

    ejecuciones: list[dict[str, Any]] = []

    class _FakeUsarComputadora:
        name = "usar_computadora"
        dangerous = True

        async def run(self, ctx: Any, args: dict[str, Any]) -> Any:
            ejecuciones.append(dict(args))
            return SimpleNamespace(content="comando ejecutado")

    registry = make_tool_registry([_FakeUsarComputadora()])

    # --- Primer intento: el sub-agente pide la tool, queda pendiente. -----
    factory = make_agent_factory(
        [
            [
                make_event(
                    type="confirmation_required",
                    tool_call_id="call-dev-1",
                    name="usar_computadora",
                    args={"cmd": "ls"},
                )
            ]
        ]
    )
    _patch_agent(factory)
    router = make_llm_router(responses=["no debería llamarse"])
    deps = make_deps()
    orchestrator = Orchestrator(router, registry)
    mission = _mission(plan=[{"seq": 1, "agente": "developer", "instruccion": "Corre el comando"}])

    await orchestrator.run(mission, deps)

    # plomería real: el registry recortado de ESTE paso SÍ deja pasar la
    # tool dangerous, porque `developer.permite_dangerous_con_confirmacion`
    # es `True` (a diferencia de research/data_analyst/content).
    assert isinstance(factory.registries[0], RestrictedRegistry)
    assert factory.registries[0].get("usar_computadora") is not None

    assert [c["status"] for c in deps.step_calls] == ["running", "waiting_confirmation"]
    pendiente = deps.step_calls[1]["usage"]["pending_tool_call"]
    assert pendiente == {"id": "call-dev-1", "name": "usar_computadora", "args": {"cmd": "ls"}}
    assert deps.mission_calls == [
        {"status": "waiting_confirmation", "resultado": None, "error": None}
    ]
    assert ejecuciones == []  # la tool JAMÁS se ejecutó en este primer intento.
    assert router.provider.requests == []  # nunca se llegó a sintetizar.

    # --- Reanudación: nueva Mission, como la armaría run_mission.py tras el
    # --- POST /v1/missions/{id}/confirm con approved=true. ----------------
    deps_resumida = make_deps()
    factory_resumida = make_agent_factory([])  # ningún Agent nuevo debería construirse.
    _patch_agent(factory_resumida)
    router_resumido = make_llm_router(responses=["síntesis final"])
    orchestrator_resumido = Orchestrator(router_resumido, registry)
    mission_resumida = _mission(
        plan=[
            {
                "seq": 1,
                "agente": "developer",
                "instruccion": "Corre el comando",
                "status": "pending",
            }
        ],
        resume_step_seq=1,
        approved_tool_call_id="call-dev-1",
        approved_tool_name="usar_computadora",
        approved_tool_args={"cmd": "ls"},
    )

    await orchestrator_resumido.run(mission_resumida, deps_resumida)

    assert factory_resumida.registries == []  # no se reinvoca al LLM/Agent.
    assert ejecuciones == [{"cmd": "ls"}]  # se ejecutó exactamente una vez.
    assert deps_resumida.step_calls[-1]["status"] == "done"
    assert deps_resumida.mission_calls[-1]["status"] == "done"
    assert deps_resumida.mission_calls[-1]["resultado"] == "síntesis final"


# ---------------------------------------------------------------------------
# WP-V5-05: dependencias + paralelismo por olas
# ---------------------------------------------------------------------------


async def test_plan_viejo_sin_depende_de_sigue_ejecutandose_100_por_ciento_secuencial(
    make_llm_router, make_deps, _patch_agent, make_agent_factory, make_event, wrapped_registry
):
    """Retrocompatibilidad explícita (misión guardada ANTES de WP-V5-05,
    plan sin la clave `depende_de` en ningún paso): 3 pasos deben ejecutarse
    uno a la vez (una ola por paso, cadena secuencial completa vía
    `_resolver_depende_de` -> "depende de TODOS los anteriores") y el
    último debe ver el historial COMPLETO de los dos previos — byte a byte
    el mismo comportamiento que antes de este WP."""
    factory = make_agent_factory(
        [
            [make_event(type="text_delta", text="Resultado 1")],
            [make_event(type="text_delta", text="Resultado 2")],
            [make_event(type="text_delta", text="Resultado 3")],
        ]
    )
    _patch_agent(factory)
    router = make_llm_router(responses=["síntesis"])
    deps = make_deps()
    orchestrator = Orchestrator(router, wrapped_registry)
    mission = _mission(
        plan=[
            {"seq": 1, "agente": "research", "instruccion": "uno"},
            {"seq": 2, "agente": "research", "instruccion": "dos"},
            {"seq": 3, "agente": "research", "instruccion": "tres"},
        ]
    )

    await orchestrator.run(mission, deps)

    # 3 Agents, cada uno en su propia ola (ejecución estrictamente 1 a 1).
    assert len(factory.registries) == 3
    assert factory.calls[0].history == []
    assert [(m.role, m.content) for m in factory.calls[1].history] == [
        ("user", "uno"),
        ("assistant", "Resultado 1"),
    ]
    # el paso 3 ve AMBOS resultados previos -- no solo el inmediato anterior.
    assert [(m.role, m.content) for m in factory.calls[2].history] == [
        ("user", "uno"),
        ("assistant", "Resultado 1"),
        ("user", "dos"),
        ("assistant", "Resultado 2"),
    ]
    assert [c["status"] for c in deps.step_calls] == [
        "running",
        "done",
        "running",
        "done",
        "running",
        "done",
    ]
    assert deps.mission_calls[-1]["status"] == "done"


async def test_dos_pasos_independientes_corren_realmente_en_paralelo(
    make_llm_router, make_deps, _patch_agent, make_event, wrapped_registry
):
    """Prueba de concurrencia REAL, no solo estructural: cada `Agent` se
    bloquea esperando una señal que SOLO se activa cuando el OTRO paso
    también llegó a arrancar. Si `run()` los ejecutara uno a la vez
    (secuencial), el primero jamás se liberaría (el segundo nunca llegaría a
    arrancar) y `asyncio.wait_for` reventaría por timeout -> el test
    fallaría. Con paralelismo real, ambos se liberan mutuamente y el test
    termina rápido."""
    activos = 0
    maximo_visto = 0
    barrera = asyncio.Event()

    class _AgentConcurrente:
        def __init__(self, llm_router: Any, registry: Any, *, model_alias: Any = None) -> None:
            del llm_router, registry, model_alias

        async def run_turn(self, *, ctx, persona, history, user_text, flags):
            nonlocal activos, maximo_visto
            del ctx, persona, history, flags
            activos += 1
            maximo_visto = max(maximo_visto, activos)
            if activos < 2:
                await asyncio.wait_for(barrera.wait(), timeout=2.0)
            else:
                barrera.set()
            yield make_event(type="text_delta", text=f"resultado de {user_text}")

    _patch_agent(_AgentConcurrente)
    router = make_llm_router(responses=["síntesis"])
    deps = make_deps()
    orchestrator = Orchestrator(router, wrapped_registry)
    mission = _mission(
        plan=[
            {"seq": 1, "agente": "research", "instruccion": "A", "depende_de": []},
            {"seq": 2, "agente": "data_analyst", "instruccion": "B", "depende_de": []},
        ]
    )

    await orchestrator.run(mission, deps)

    assert maximo_visto == 2  # ambos estuvieron "en vuelo" al mismo tiempo.
    assert deps.mission_calls[-1]["status"] == "done"


async def test_missions_parallel_max_limita_pasos_en_vuelo_a_la_vez(
    make_llm_router, make_deps, _patch_agent, make_event, wrapped_registry
):
    """4 pasos independientes, `MISSIONS_PARALLEL_MAX=2` -> nunca deben
    estar más de 2 `Agent` corriendo al mismo tiempo."""
    activos = 0
    maximo_visto = 0
    lock = asyncio.Lock()

    class _AgentContador:
        def __init__(self, llm_router: Any, registry: Any, *, model_alias: Any = None) -> None:
            del llm_router, registry, model_alias

        async def run_turn(self, *, ctx, persona, history, user_text, flags):
            nonlocal activos, maximo_visto
            del ctx, persona, history, flags
            async with lock:
                activos += 1
                maximo_visto = max(maximo_visto, activos)
            await asyncio.sleep(0.02)
            async with lock:
                activos -= 1
            yield make_event(type="text_delta", text=f"resultado de {user_text}")

    _patch_agent(_AgentContador)
    router = make_llm_router(responses=["síntesis"])
    deps = make_deps(settings=SimpleNamespace(MISSIONS_PARALLEL_MAX=2))
    orchestrator = Orchestrator(router, wrapped_registry)
    mission = _mission(
        plan=[
            {"seq": 1, "agente": "research", "instruccion": "A", "depende_de": []},
            {"seq": 2, "agente": "research", "instruccion": "B", "depende_de": []},
            {"seq": 3, "agente": "research", "instruccion": "C", "depende_de": []},
            {"seq": 4, "agente": "research", "instruccion": "D", "depende_de": []},
        ]
    )

    await orchestrator.run(mission, deps)

    assert maximo_visto == 2
    assert deps.mission_calls[-1]["status"] == "done"


async def test_missions_parallel_max_ausente_en_settings_usa_default_3_sin_reventar(
    make_llm_router, make_deps, _patch_agent, make_agent_factory, make_event, wrapped_registry
):
    """`getattr(deps.settings, "MISSIONS_PARALLEL_MAX", ...)` defensivo: un
    `settings` sin ese campo (p. ej. antes de que WP-V5-01 lo agregue) no
    debe reventar `run()`."""
    factory = make_agent_factory(
        [[make_event(type="text_delta", text="r1")], [make_event(type="text_delta", text="r2")]]
    )
    _patch_agent(factory)
    router = make_llm_router(responses=["síntesis"])
    deps = make_deps(settings=SimpleNamespace())  # sin MISSIONS_PARALLEL_MAX
    orchestrator = Orchestrator(router, wrapped_registry)
    mission = _mission(
        plan=[
            {"seq": 1, "agente": "research", "instruccion": "A", "depende_de": []},
            {"seq": 2, "agente": "research", "instruccion": "B", "depende_de": []},
        ]
    )

    await orchestrator.run(mission, deps)

    assert deps.mission_calls[-1]["status"] == "done"


async def test_lock_serializa_los_saves_durante_una_ola_paralela(
    make_llm_router, make_agent_factory, _patch_agent, make_event, wrapped_registry
):
    """Varios pasos de una misma ola llaman a `save_step` casi al mismo
    tiempo -- el `asyncio.Lock` de `run()` debe garantizar que nunca haya
    más de UNA escritura "en vuelo" a la vez (simula lo que pasaría si dos
    corutinas usaran la MISMA `AsyncSession` concurrentemente)."""
    en_vuelo = 0
    maximo_en_vuelo = 0

    class _DepsConcurrencia:
        def __init__(self) -> None:
            self.session = None
            self.settings = SimpleNamespace()
            self.vault = None
            self.flags: dict[str, Any] = {}
            self.step_calls: list[dict[str, Any]] = []
            self.mission_calls: list[dict[str, Any]] = []
            self.insert_steps_calls: list[list[dict[str, Any]]] = []

        async def save_step(self, **kwargs: Any) -> None:
            nonlocal en_vuelo, maximo_en_vuelo
            en_vuelo += 1
            maximo_en_vuelo = max(maximo_en_vuelo, en_vuelo)
            await asyncio.sleep(0.01)
            en_vuelo -= 1
            self.step_calls.append(kwargs)

        async def save_mission(self, **kwargs: Any) -> None:
            self.mission_calls.append(kwargs)

        async def insert_steps(self, pasos: list[dict[str, Any]]) -> None:
            self.insert_steps_calls.append(list(pasos))

    factory = make_agent_factory(
        [[make_event(type="text_delta", text="rA")], [make_event(type="text_delta", text="rB")]]
    )
    _patch_agent(factory)
    router = make_llm_router(responses=["síntesis"])
    deps = _DepsConcurrencia()
    orchestrator = Orchestrator(router, wrapped_registry)
    mission = _mission(
        plan=[
            {"seq": 1, "agente": "research", "instruccion": "A", "depende_de": []},
            {"seq": 2, "agente": "data_analyst", "instruccion": "B", "depende_de": []},
        ]
    )

    await orchestrator.run(mission, deps)

    assert maximo_en_vuelo == 1  # el lock impidió que 2 save_step se solaparan.
    assert len(deps.step_calls) == 4  # 2 pasos * (running + done)


async def test_waiting_confirmation_en_ola_con_mas_de_un_paso_espera_y_persiste_los_demas(
    make_llm_router, make_deps, _patch_agent, make_event, wrapped_registry
):
    """`research`/`data_analyst` NO son `permite_dangerous_con_confirmacion`,
    así que 2 pasos independientes de esos perfiles SÍ comparten ola. Este
    test simula (mismo espíritu que
    `test_confirmation_required_pausa_la_mision_y_no_ejecuta_mas_pasos`, que
    ya hacía lo mismo con perfiles "seguros" para aislar el MECANISMO de
    `Agent.run_turn`/`RestrictedRegistry` real) que uno de los dos igual
    emite `confirmation_required` — prueba que `run()` espera a que el OTRO
    paso de la misma ola termine (`gather` normal), persiste su resultado, y
    la misión queda `waiting_confirmation` con el `pending_tool_call` del que
    pausó."""

    class _AgentPorInstruccion:
        def __init__(self, llm_router: Any, registry: Any, *, model_alias: Any = None) -> None:
            del llm_router, registry, model_alias

        async def run_turn(self, *, ctx, persona, history, user_text, flags):
            del ctx, persona, history, flags
            if user_text == "B":
                yield make_event(
                    type="confirmation_required",
                    tool_call_id="call-x",
                    name="usar_computadora",
                    args={"cmd": "ls"},
                )
            else:
                yield make_event(type="text_delta", text=f"resultado de {user_text}")

    _patch_agent(_AgentPorInstruccion)
    router = make_llm_router(responses=["no debería llamarse"])
    deps = make_deps()
    orchestrator = Orchestrator(router, wrapped_registry)
    mission = _mission(
        plan=[
            {"seq": 1, "agente": "research", "instruccion": "A", "depende_de": []},
            {"seq": 2, "agente": "data_analyst", "instruccion": "B", "depende_de": []},
        ]
    )

    await orchestrator.run(mission, deps)

    estados_paso_1 = [c["status"] for c in deps.step_calls if c.get("seq") == 1]
    estados_paso_2 = [c["status"] for c in deps.step_calls if c.get("seq") == 2]
    assert "done" in estados_paso_1  # el paso A sí terminó y se persistió.
    assert estados_paso_2[-1] == "waiting_confirmation"
    assert deps.mission_calls[-1]["status"] == "waiting_confirmation"
    assert router.provider.requests == []  # nunca se llegó a sintetizar/replanear.


async def test_dependencia_entre_dos_pasos_independientes_de_una_ola_de_3(
    make_llm_router, make_deps, _patch_agent, make_agent_factory, make_event, wrapped_registry
):
    """4 pasos: el 3ro depende del 1ro y 2do; el 4to es independiente ->
    olas [{1,2,4}, {3}]. Verifica que el paso 3 solo ve el historial de SUS
    dependencias (1 y 2), no el del 4 (que no declaró como dependencia pese
    a compartir ola con los otros dos)."""
    factory = make_agent_factory(
        [
            [make_event(type="text_delta", text="r1")],
            [make_event(type="text_delta", text="r2")],
            [make_event(type="text_delta", text="r4")],
            [make_event(type="text_delta", text="r3")],
        ]
    )
    _patch_agent(factory)
    router = make_llm_router(responses=["síntesis"])
    deps = make_deps()
    orchestrator = Orchestrator(router, wrapped_registry)
    mission = _mission(
        plan=[
            {"seq": 1, "agente": "research", "instruccion": "uno", "depende_de": []},
            {"seq": 2, "agente": "research", "instruccion": "dos", "depende_de": []},
            {"seq": 3, "agente": "research", "instruccion": "tres", "depende_de": [0, 1]},
            {"seq": 4, "agente": "research", "instruccion": "cuatro", "depende_de": []},
        ]
    )

    await orchestrator.run(mission, deps)

    assert len(factory.registries) == 4
    historial_paso_3 = factory.calls[-1].history
    assert [(m.role, m.content) for m in historial_paso_3] == [
        ("user", "uno"),
        ("assistant", "r1"),
        ("user", "dos"),
        ("assistant", "r2"),
    ]
    assert deps.mission_calls[-1]["status"] == "done"


# ---------------------------------------------------------------------------
# WP-V5-05: replan acotado
# ---------------------------------------------------------------------------


async def test_replan_tras_error_genera_plan_nuevo_y_lo_ejecuta_hasta_done(
    make_llm_router, make_deps, _patch_agent, make_agent_factory, make_event, wrapped_registry
):
    factory = make_agent_factory(
        [
            [make_event(type="error", message="el proveedor LLM falló")],
            [make_event(type="text_delta", text="resultado del paso de reemplazo")],
        ]
    )
    _patch_agent(factory)
    router = make_llm_router(
        responses=[
            '{"pasos": [{"agente": "research", "instruccion": "Reintenta de otra forma"}]}',
            "síntesis final",
        ]
    )
    deps = make_deps()
    orchestrator = Orchestrator(router, wrapped_registry)
    mission = _mission(plan=[{"seq": 1, "agente": "research", "instruccion": "uno"}])

    await orchestrator.run(mission, deps)

    # el paso 1 quedó en error, se intentó UN replan (2da llamada al LLM), y
    # el paso nuevo (seq=2) se ejecutó hasta "done".
    assert [c["status"] for c in deps.step_calls if c.get("seq") == 1] == ["running", "error"]
    assert [c["status"] for c in deps.step_calls if c.get("seq") == 2] == ["running", "done"]
    assert len(deps.insert_steps_calls) == 1
    assert deps.insert_steps_calls[0][0]["seq"] == 2
    assert deps.insert_steps_calls[0][0]["agente"] == "research"
    assert deps.insert_steps_calls[0][0]["instruccion"] == "Reintenta de otra forma"

    # el contador de replans se persistió ANTES de seguir ejecutando, sin
    # perder el resto del presupuesto original (`max_steps`).
    presupuestos = [c["presupuesto"] for c in deps.mission_calls if "presupuesto" in c]
    assert presupuestos == [{"max_steps": 8, "replans_usados": 1}]

    assert deps.mission_calls[-1]["status"] == "done"
    assert deps.mission_calls[-1]["resultado"] == "síntesis final"
    assert len(router.provider.requests) == 2  # 1 replan + 1 síntesis (nunca un 2do replan)


async def test_replan_respeta_el_presupuesto_original_de_pasos(
    make_llm_router, make_deps, _patch_agent, make_agent_factory, make_event, wrapped_registry
):
    """`presupuesto={"max_steps": 2}`, 0 pasos completados cuando falla el
    único paso -> el replan pide como máximo 2 pasos nuevos, aunque el LLM
    ofrezca más."""
    factory = make_agent_factory(
        [
            [make_event(type="error", message="falló")],
            [make_event(type="text_delta", text="r-nuevo-1")],
            [make_event(type="text_delta", text="r-nuevo-2")],
        ]
    )
    _patch_agent(factory)
    respuesta_replan = (
        '{"pasos": ['
        '{"agente": "research", "instruccion": "nuevo uno"}, '
        '{"agente": "research", "instruccion": "nuevo dos"}, '
        '{"agente": "research", "instruccion": "nuevo tres -- no debería usarse"}]}'
    )
    router = make_llm_router(responses=[respuesta_replan, "síntesis"])
    deps = make_deps()
    orchestrator = Orchestrator(router, wrapped_registry)
    mission = _mission(
        plan=[{"seq": 1, "agente": "research", "instruccion": "uno"}],
        presupuesto={"max_steps": 2},
    )

    await orchestrator.run(mission, deps)

    assert len(deps.insert_steps_calls) == 1
    nuevos = deps.insert_steps_calls[0]
    assert len(nuevos) == 2  # truncado a 2, no 3.
    assert [p["instruccion"] for p in nuevos] == ["nuevo uno", "nuevo dos"]
    assert deps.mission_calls[-1]["status"] == "done"


async def test_replan_ya_usado_va_directo_a_error_sin_llamar_al_llm_de_nuevo(
    make_llm_router, make_deps, _patch_agent, make_agent_factory, make_event, wrapped_registry
):
    """`presupuesto["replans_usados"]` ya en 1 (misión que ya replaneó en una
    corrida anterior) -> un nuevo error NO dispara otro replan, va directo a
    `error`."""
    factory = make_agent_factory([[make_event(type="error", message="falló de nuevo")]])
    _patch_agent(factory)
    router = make_llm_router(responses=["no debería llamarse"])
    deps = make_deps()
    orchestrator = Orchestrator(router, wrapped_registry)
    mission = _mission(
        plan=[{"seq": 1, "agente": "research", "instruccion": "uno"}],
        presupuesto={"max_steps": 8, "replans_usados": 1},
    )

    await orchestrator.run(mission, deps)

    assert router.provider.requests == []  # ni replan ni síntesis.
    assert deps.insert_steps_calls == []
    assert deps.mission_calls[-1] == {
        "status": "error",
        "resultado": None,
        "error": "falló de nuevo",
    }


async def test_replan_sin_json_usable_mision_termina_en_error_con_el_error_original(
    make_llm_router, make_deps, _patch_agent, make_agent_factory, make_event, wrapped_registry
):
    factory = make_agent_factory([[make_event(type="error", message="el error original")]])
    _patch_agent(factory)
    router = make_llm_router(responses=["esto no es JSON en absoluto"])
    deps = make_deps()
    orchestrator = Orchestrator(router, wrapped_registry)
    mission = _mission(plan=[{"seq": 1, "agente": "research", "instruccion": "uno"}])

    await orchestrator.run(mission, deps)

    assert deps.insert_steps_calls == []
    assert deps.mission_calls[-1] == {
        "status": "error",
        "resultado": None,
        "error": "el error original",
    }


async def test_replan_nunca_se_dispara_para_un_paso_waiting_confirmation(
    make_llm_router, make_deps, _patch_agent, make_agent_factory, make_event, wrapped_registry
):
    """Una pausa por confirmación NUNCA cuenta como "error" -> no dispara
    replan, sin importar el presupuesto de replans disponible."""
    factory = make_agent_factory(
        [
            [
                make_event(
                    type="confirmation_required",
                    tool_call_id="call-1",
                    name="usar_computadora",
                    args={"cmd": "ls"},
                )
            ]
        ]
    )
    _patch_agent(factory)
    router = make_llm_router(responses=["no debería llamarse"])
    deps = make_deps()
    orchestrator = Orchestrator(router, wrapped_registry)
    mission = _mission(plan=[{"seq": 1, "agente": "developer", "instruccion": "Corre algo"}])

    await orchestrator.run(mission, deps)

    assert router.provider.requests == []
    assert deps.insert_steps_calls == []
    assert deps.mission_calls[-1]["status"] == "waiting_confirmation"


async def test_replan_pasos_pendientes_no_lanzados_quedan_skipped(
    make_llm_router, make_deps, _patch_agent, make_agent_factory, make_event, wrapped_registry
):
    """3 pasos secuencialmente dependientes: el 2do falla -> el 3ro (que
    nunca llegó a lanzarse, seguía "pending") debe quedar `skipped`, no
    reintentarse ni desaparecer."""
    factory = make_agent_factory(
        [
            [make_event(type="text_delta", text="r1")],
            [make_event(type="error", message="falla el 2do")],
            [make_event(type="text_delta", text="r-reemplazo")],
        ]
    )
    _patch_agent(factory)
    router = make_llm_router(
        responses=['{"pasos": [{"agente": "research", "instruccion": "reemplazo"}]}', "síntesis"]
    )
    deps = make_deps()
    orchestrator = Orchestrator(router, wrapped_registry)
    mission = _mission(
        plan=[
            {"seq": 1, "agente": "research", "instruccion": "uno", "depende_de": []},
            {"seq": 2, "agente": "research", "instruccion": "dos", "depende_de": [0]},
            {"seq": 3, "agente": "research", "instruccion": "tres", "depende_de": [1]},
        ]
    )

    await orchestrator.run(mission, deps)

    assert [c["status"] for c in deps.step_calls if c.get("seq") == 1] == ["running", "done"]
    assert [c["status"] for c in deps.step_calls if c.get("seq") == 2] == ["running", "error"]
    # el paso 3 JAMÁS llegó a "running" -- se marcó "skipped" directo.
    assert [c["status"] for c in deps.step_calls if c.get("seq") == 3] == ["skipped"]
    # el paso de reemplazo entra como seq=4 (sigue después del último usado).
    assert deps.insert_steps_calls[0][0]["seq"] == 4
    assert deps.mission_calls[-1]["status"] == "done"


# ---------------------------------------------------------------------------
# WP-V5-05: timeout por paso
# ---------------------------------------------------------------------------


async def test_timeout_marca_el_paso_en_error_con_mensaje_claro_y_dispara_replan(
    make_llm_router, make_deps, _patch_agent, make_event, wrapped_registry
):
    """`_AgentLento` se queda dormido mucho más que el timeout configurado
    en TODOS sus pasos (incluido el que propone el replan) — así que este
    test ejercita el camino completo: paso 1 agota el timeout -> dispara UN
    replan (única llamada al LLM, ya que `_synthesize` nunca se alcanza) ->
    el paso de reemplazo TAMBIÉN agota el timeout -> como el presupuesto de
    replans ya se usó, la misión termina en `error` (nunca un 2do replan)."""

    class _AgentLento:
        def __init__(self, llm_router: Any, registry: Any, *, model_alias: Any = None) -> None:
            del llm_router, registry, model_alias

        async def run_turn(self, *, ctx, persona, history, user_text, flags):
            del ctx, persona, history, user_text, flags
            await asyncio.sleep(10)  # muchísimo más que el timeout configurado.
            yield make_event(type="text_delta", text="nunca debería llegar aquí")

    _patch_agent(_AgentLento)
    router = make_llm_router(
        responses=['{"pasos": [{"agente": "research", "instruccion": "más rápido esta vez"}]}']
    )
    deps = make_deps(settings=SimpleNamespace(MISSIONS_STEP_TIMEOUT_SECONDS=0.05))
    orchestrator = Orchestrator(router, wrapped_registry)
    mission = _mission(plan=[{"seq": 1, "agente": "research", "instruccion": "uno"}])

    await orchestrator.run(mission, deps)

    step1_calls = [c for c in deps.step_calls if c.get("seq") == 1]
    assert step1_calls[0]["status"] == "running"
    assert step1_calls[1]["status"] == "error"
    assert "tiempo máximo" in step1_calls[1]["resultado"]
    assert "0" in step1_calls[1]["resultado"]  # menciona el timeout configurado.

    # el replan SÍ se intentó (única llamada al LLM: nunca se llega a
    # sintetizar, y el presupuesto de replans se agota con este único
    # intento) y produjo un paso de reemplazo (seq=2) que también agotó el
    # timeout.
    assert len(router.provider.requests) == 1
    assert len(deps.insert_steps_calls) == 1
    step2_calls = [c for c in deps.step_calls if c.get("seq") == 2]
    assert step2_calls[-1]["status"] == "error"

    # sin más presupuesto de replans, la misión termina en error (nunca
    # "done") con el mensaje del timeout del paso de reemplazo.
    assert deps.mission_calls[-1]["status"] == "error"
    assert "tiempo máximo" in deps.mission_calls[-1]["error"]


async def test_timeout_no_aplica_al_paso_reanudado_sin_agent(
    make_llm_router, make_deps, _patch_agent, make_tool_registry
):
    """`_run_resumed_step` (reanudación de una tool aprobada) no construye
    ningún `Agent` -> el timeout de `asyncio.timeout` envuelve SOLO
    `_run_step`, nunca la reanudación. Un `MISSIONS_STEP_TIMEOUT_SECONDS`
    minúsculo no debe afectar a un paso reanudado, aunque la tool tarde más
    que ese timeout."""

    class _ToolLenta:
        name = "usar_computadora"
        dangerous = True

        async def run(self, ctx: Any, args: dict[str, Any]) -> Any:
            del ctx, args
            await asyncio.sleep(0.2)  # más que el timeout configurado (0.01s).
            return SimpleNamespace(content="listo, tardó pero terminó")

    registry = make_tool_registry([_ToolLenta()])
    router = make_llm_router(responses=["síntesis"])
    deps = make_deps(settings=SimpleNamespace(MISSIONS_STEP_TIMEOUT_SECONDS=0.01))
    orchestrator = Orchestrator(router, registry)
    mission = _mission(
        plan=[{"seq": 1, "agente": "developer", "instruccion": "Corre algo", "status": "pending"}],
        resume_step_seq=1,
        approved_tool_call_id="call-1",
        approved_tool_name="usar_computadora",
        approved_tool_args={"cmd": "ls"},
    )

    await orchestrator.run(mission, deps)

    assert deps.step_calls[-1]["status"] == "done"
    assert "tardó pero terminó" in deps.step_calls[-1]["resultado"]
    assert deps.mission_calls[-1]["status"] == "done"


# ---------------------------------------------------------------------------
# WP-V6-10: `started_at`/`finished_at` por paso (`agent_steps.usage`)
# ---------------------------------------------------------------------------


def _parse_iso(value: Any) -> datetime:
    assert isinstance(value, str)
    return datetime.fromisoformat(value)


def test_timing_usage_mezcla_started_finished_sin_perder_lo_que_ya_traia():
    resultado = _timing_usage("2026-01-01T00:00:00+00:00", {"input_tokens": 5})
    assert resultado["input_tokens"] == 5
    assert resultado["started_at"] == "2026-01-01T00:00:00+00:00"
    assert isinstance(resultado["finished_at"], str)
    datetime.fromisoformat(resultado["finished_at"])  # no lanza: es ISO válido.


def test_timing_usage_sin_extra_solo_trae_las_dos_marcas():
    resultado = _timing_usage("2026-01-01T00:00:00+00:00")
    assert set(resultado.keys()) == {"started_at", "finished_at"}


async def test_paso_exitoso_persiste_started_at_y_finished_at_en_usage(
    make_llm_router, make_deps, _patch_agent, make_agent_factory, make_event, wrapped_registry
):
    """La transición a `running` sigue sin tocar `usage` (`None` = "no lo
    toques", ver docstring del módulo) — solo el guardado TERMINAL (`done`
    aquí) gana `started_at`/`finished_at`."""
    factory = make_agent_factory(
        [
            [
                make_event(type="text_delta", text="Resultado del paso 1"),
                make_event(type="done", usage={"input_tokens": 10, "output_tokens": 20}),
            ]
        ]
    )
    _patch_agent(factory)
    router = make_llm_router(responses=["síntesis"])
    deps = make_deps()
    orchestrator = Orchestrator(router, wrapped_registry)
    mission = _mission(plan=[{"seq": 1, "agente": "research", "instruccion": "uno"}])

    await orchestrator.run(mission, deps)

    llamada_running = deps.step_calls[0]
    assert llamada_running["status"] == "running"
    assert llamada_running["usage"] is None

    llamada_done = deps.step_calls[1]
    assert llamada_done["status"] == "done"
    usage = llamada_done["usage"]
    assert usage["input_tokens"] == 10  # los tokens de `Usage` sobreviven junto al timing.
    assert usage["output_tokens"] == 20
    started = _parse_iso(usage["started_at"])
    finished = _parse_iso(usage["finished_at"])
    assert finished >= started


async def test_paso_waiting_confirmation_incluye_timing_junto_al_pending_tool_call(
    make_llm_router, make_deps, _patch_agent, make_agent_factory, make_event, wrapped_registry
):
    factory = make_agent_factory(
        [
            [
                make_event(
                    type="confirmation_required",
                    tool_call_id="call-1",
                    name="usar_computadora",
                    args={"cmd": "ls"},
                )
            ]
        ]
    )
    _patch_agent(factory)
    router = make_llm_router(responses=["no debería llamarse"])
    deps = make_deps()
    orchestrator = Orchestrator(router, wrapped_registry)
    mission = _mission(plan=[{"seq": 1, "agente": "research", "instruccion": "uno"}])

    await orchestrator.run(mission, deps)

    usage = deps.step_calls[-1]["usage"]
    assert usage["pending_tool_call"] == {
        "id": "call-1",
        "name": "usar_computadora",
        "args": {"cmd": "ls"},
    }
    started = _parse_iso(usage["started_at"])
    finished = _parse_iso(usage["finished_at"])
    assert finished >= started


async def test_paso_error_event_incluye_timing(
    make_llm_router, make_deps, _patch_agent, make_agent_factory, make_event, wrapped_registry
):
    factory = make_agent_factory([[make_event(type="error", message="boom")]])
    _patch_agent(factory)
    router = make_llm_router(responses=["no debería llamarse"])
    deps = make_deps()
    orchestrator = Orchestrator(router, wrapped_registry)
    # `replans_usados` ya en el tope: la misión falla directo, sin replan de
    # por medio -- este test solo le interesa el `usage` del paso que falló.
    mission = _mission(
        plan=[{"seq": 1, "agente": "research", "instruccion": "uno"}],
        presupuesto={"max_steps": 8, "replans_usados": 1},
    )

    await orchestrator.run(mission, deps)

    llamada = deps.step_calls[-1]
    assert llamada["status"] == "error"
    usage = llamada["usage"]
    started = _parse_iso(usage["started_at"])
    finished = _parse_iso(usage["finished_at"])
    assert finished >= started


async def test_timeout_incluye_timing_en_el_usage_del_paso(
    make_llm_router, make_deps, _patch_agent, make_event, wrapped_registry
):
    class _AgentLento:
        def __init__(self, llm_router: Any, registry: Any, *, model_alias: Any = None) -> None:
            del llm_router, registry, model_alias

        async def run_turn(self, *, ctx, persona, history, user_text, flags):
            del ctx, persona, history, user_text, flags
            await asyncio.sleep(10)
            yield make_event(type="text_delta", text="nunca debería llegar aquí")

    _patch_agent(_AgentLento)
    router = make_llm_router(responses=["no debería llamarse"])
    deps = make_deps(settings=SimpleNamespace(MISSIONS_STEP_TIMEOUT_SECONDS=0.05))
    orchestrator = Orchestrator(router, wrapped_registry)
    mission = _mission(
        plan=[{"seq": 1, "agente": "research", "instruccion": "uno"}],
        presupuesto={"max_steps": 8, "replans_usados": 1},
    )

    await orchestrator.run(mission, deps)

    llamada = deps.step_calls[-1]
    assert llamada["status"] == "error"
    usage = llamada["usage"]
    started = _parse_iso(usage["started_at"])
    finished = _parse_iso(usage["finished_at"])
    assert finished >= started
    assert (finished - started).total_seconds() >= 0.05


async def test_paso_reanudado_incluye_timing_en_usage(
    make_llm_router, make_deps, _patch_agent, make_agent_factory, make_tool_registry
):
    class _FakeTool:
        name = "enviar_correo"
        dangerous = True

        async def run(self, ctx: Any, args: dict[str, Any]) -> Any:
            del ctx, args
            return SimpleNamespace(content="listo")

    registry = make_tool_registry([_FakeTool()])
    factory = make_agent_factory([])  # ningún Agent nuevo debería construirse.
    _patch_agent(factory)
    router = make_llm_router(responses=["síntesis"])
    deps = make_deps()
    orchestrator = Orchestrator(router, registry)
    mission = _mission(
        plan=[{"seq": 1, "agente": "sales", "instruccion": "Paso uno", "status": "pending"}],
        resume_step_seq=1,
        approved_tool_call_id="call-x",
        approved_tool_name="enviar_correo",
        approved_tool_args={"to": "x@y.com"},
    )

    await orchestrator.run(mission, deps)

    llamada = deps.step_calls[-1]
    assert llamada["status"] == "done"
    usage = llamada["usage"]
    started = _parse_iso(usage["started_at"])
    finished = _parse_iso(usage["finished_at"])
    assert finished >= started


async def test_paso_reanudado_sin_tool_disponible_incluye_timing_en_usage(
    make_llm_router, make_deps, _patch_agent, make_agent_factory, make_tool_registry
):
    registry = make_tool_registry([])  # "enviar_correo" ya no está.
    factory = make_agent_factory([])
    _patch_agent(factory)
    router = make_llm_router(responses=["no debería llamarse"])
    deps = make_deps()
    orchestrator = Orchestrator(router, registry)
    mission = _mission(
        plan=[{"seq": 1, "agente": "sales", "instruccion": "Paso uno", "status": "pending"}],
        resume_step_seq=1,
        approved_tool_call_id="call-x",
        approved_tool_name="enviar_correo",
        approved_tool_args={"to": "x@y.com"},
    )

    await orchestrator.run(mission, deps)

    llamada = deps.step_calls[-1]
    assert llamada["status"] == "error"
    usage = llamada["usage"]
    started = _parse_iso(usage["started_at"])
    finished = _parse_iso(usage["finished_at"])
    assert finished >= started
