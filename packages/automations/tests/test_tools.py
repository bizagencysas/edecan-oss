"""Tests de `edecan_automations.tools.GestionarAutomatizacionTool`."""

from __future__ import annotations

import json

from edecan_automations.tools import LIMIT_AUTOMATIONS_ACTIVE, GestionarAutomatizacionTool

UNLIMITED = -1


async def test_crear_sin_campos_obligatorios_no_toca_la_sesion(make_ctx, make_session) -> None:
    session = make_session()
    ctx = make_ctx(session=session, flags={LIMIT_AUTOMATIONS_ACTIVE: UNLIMITED})

    resultado = await GestionarAutomatizacionTool().run(ctx, {"accion": "crear"})

    assert "necesito" in resultado.content
    assert session.llamadas == []


async def test_crear_con_rrule_invalida_no_inserta(make_ctx, make_session) -> None:
    session = make_session()
    ctx = make_ctx(session=session, flags={LIMIT_AUTOMATIONS_ACTIVE: UNLIMITED})

    resultado = await GestionarAutomatizacionTool().run(
        ctx,
        {
            "accion": "crear",
            "nombre": "Reporte diario",
            "rrule": "ESTO NO ES UNA RRULE",
            "instruccion": "Manda el reporte de ventas.",
        },
    )

    assert "rrule inválida" in resultado.content
    assert session.llamadas == []


async def test_crear_en_el_limite_del_plan_no_inserta(make_ctx, make_session, make_result) -> None:
    session = make_session([make_result(scalar=3)])  # ya hay 3 activas
    ctx = make_ctx(session=session, flags={LIMIT_AUTOMATIONS_ACTIVE: 3})

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
    assert len(session.llamadas) == 1  # solo el COUNT, nunca llegó al INSERT


async def test_crear_exitoso_inserta_y_confirma(make_ctx, make_session, make_result) -> None:
    # limite=3, activas=0: bajo el límite -> sí llega a insertar. La primera
    # respuesta programada es el COUNT (_bajo_limite), la segunda el INSERT.
    session = make_session(
        [make_result(scalar=0), [{"id": "11111111-1111-1111-1111-111111111111"}]]
    )
    ctx = make_ctx(session=session, flags={LIMIT_AUTOMATIONS_ACTIVE: 3})

    resultado = await GestionarAutomatizacionTool().run(
        ctx,
        {
            "accion": "crear",
            "nombre": "Reporte diario",
            "rrule": "FREQ=DAILY;BYHOUR=9",
            "instruccion": "Manda el reporte de ventas del día.",
        },
    )

    assert "Reporte diario" in resultado.content
    assert resultado.data["id"] == "11111111-1111-1111-1111-111111111111"
    assert resultado.data["next_run_at"] is not None

    insert_sql, insert_params = session.llamadas[-1]
    assert "INSERT INTO automations" in insert_sql
    assert insert_params["nombre"] == "Reporte diario"
    trigger = json.loads(insert_params["trigger"])
    assert trigger == {"kind": "schedule", "rrule": "FREQ=DAILY;BYHOUR=9"}
    accion_guardada = json.loads(insert_params["accion"])
    assert accion_guardada["instruccion"] == "Manda el reporte de ventas del día."


async def test_listar_sin_automatizaciones(make_ctx, make_session) -> None:
    session = make_session([[]])
    ctx = make_ctx(session=session)

    resultado = await GestionarAutomatizacionTool().run(ctx, {"accion": "listar"})

    assert "No tienes automatizaciones" in resultado.content
    assert resultado.data["automatizaciones"] == []


async def test_listar_con_automatizaciones(make_ctx, make_session) -> None:
    session = make_session(
        [
            [
                {
                    "id": "22222222-2222-2222-2222-222222222222",
                    "nombre": "Reporte diario",
                    "enabled": True,
                    "trigger": json.dumps({"kind": "schedule", "rrule": "FREQ=DAILY"}),
                    "next_run_at": None,
                    "last_run_at": None,
                }
            ]
        ]
    )
    ctx = make_ctx(session=session)

    resultado = await GestionarAutomatizacionTool().run(ctx, {"accion": "listar"})

    assert "Reporte diario" in resultado.content
    assert resultado.data["automatizaciones"][0]["trigger"]["rrule"] == "FREQ=DAILY"


async def test_activar_sin_automation_id(make_ctx, make_session) -> None:
    session = make_session()
    ctx = make_ctx(session=session, flags={LIMIT_AUTOMATIONS_ACTIVE: UNLIMITED})

    resultado = await GestionarAutomatizacionTool().run(ctx, {"accion": "activar"})

    assert "Falta 'automation_id'" in resultado.content
    assert session.llamadas == []


async def test_activar_no_encontrada(make_ctx, make_session, make_result) -> None:
    session = make_session([make_result(scalar=0), []])
    ctx = make_ctx(session=session, flags={LIMIT_AUTOMATIONS_ACTIVE: 3})

    resultado = await GestionarAutomatizacionTool().run(
        ctx, {"accion": "activar", "automation_id": "33333333-3333-3333-3333-333333333333"}
    )

    assert "No encontré" in resultado.content


async def test_activar_en_el_limite_no_actualiza(make_ctx, make_session, make_result) -> None:
    session = make_session([make_result(scalar=5)])
    ctx = make_ctx(session=session, flags={LIMIT_AUTOMATIONS_ACTIVE: 5})

    resultado = await GestionarAutomatizacionTool().run(
        ctx, {"accion": "activar", "automation_id": "33333333-3333-3333-3333-333333333333"}
    )

    assert "límite" in resultado.content
    assert len(session.llamadas) == 1  # nunca llegó al UPDATE


async def test_activar_exitoso(make_ctx, make_session, make_result) -> None:
    session = make_session([make_result(scalar=0), [{"nombre": "Reporte diario"}]])
    ctx = make_ctx(session=session, flags={LIMIT_AUTOMATIONS_ACTIVE: 3})

    resultado = await GestionarAutomatizacionTool().run(
        ctx, {"accion": "activar", "automation_id": "33333333-3333-3333-3333-333333333333"}
    )

    assert "Activé" in resultado.content
    update_sql, update_params = session.llamadas[-1]
    assert "UPDATE automations" in update_sql
    assert update_params["enabled"] is True


async def test_desactivar_exitoso_no_chequea_limite(make_ctx, make_session) -> None:
    session = make_session([[{"nombre": "Reporte diario"}]])
    ctx = make_ctx(session=session)  # sin LIMIT_AUTOMATIONS_ACTIVE en flags: si chequeara, fallaría

    resultado = await GestionarAutomatizacionTool().run(
        ctx, {"accion": "desactivar", "automation_id": "33333333-3333-3333-3333-333333333333"}
    )

    assert "Desactivé" in resultado.content
    assert len(session.llamadas) == 1  # ninguna consulta de límite


async def test_accion_desconocida(make_ctx, make_session) -> None:
    ctx = make_ctx(session=make_session())

    resultado = await GestionarAutomatizacionTool().run(ctx, {"accion": "borrar"})

    assert "accion inválida" in resultado.content


def test_tool_metadata() -> None:
    tool = GestionarAutomatizacionTool()
    assert tool.name == "gestionar_automatizacion"
    assert tool.dangerous is True
    assert "automations.rules" in tool.requires_flags


def test_get_all_tools_devuelve_una_sola_tool() -> None:
    from edecan_automations import get_all_tools

    tools = get_all_tools()
    assert [t.name for t in tools] == ["gestionar_automatizacion"]
