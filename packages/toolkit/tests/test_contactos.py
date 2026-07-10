"""Tests de `edecan_toolkit.contactos`: `buscar_contactos` y `gestionar_contacto`."""

from __future__ import annotations

import json

from edecan_toolkit.contactos import BuscarContactosTool, GestionarContactoTool


async def test_buscar_contactos_sin_resultados(make_ctx, make_session):
    ctx = make_ctx(session=make_session([[]]))
    resultado = await BuscarContactosTool().run(ctx, {"consulta": "nadie"})
    assert resultado.data["contactos"] == []
    assert "nadie" in resultado.content


async def test_buscar_contactos_formatea_emails_y_phones_desde_jsonb_como_string(
    make_ctx, make_session
):
    fila = {
        "id": "c1",
        "nombre": "Ana Torres",
        "emails": json.dumps(["ana@ejemplo.com"]),
        "phones": json.dumps(["+52 555 000 0000"]),
        "empresa": "Acme",
        "notas": None,
        "tags": json.dumps(["cliente"]),
    }
    ctx = make_ctx(session=make_session([[fila]]))

    resultado = await BuscarContactosTool().run(ctx, {"consulta": "Ana"})

    assert "Ana Torres (Acme)" in resultado.content
    assert "ana@ejemplo.com" in resultado.content
    contacto = resultado.data["contactos"][0]
    assert contacto["emails"] == ["ana@ejemplo.com"]
    assert contacto["tags"] == ["cliente"]


async def test_buscar_contactos_sin_consulta_lista_los_mas_recientes(make_ctx, make_session):
    session = make_session([[]])
    ctx = make_ctx(session=session)
    await BuscarContactosTool().run(ctx, {})
    sql, params = session.llamadas[0]
    assert "ILIKE" not in sql
    assert "patron" not in params


async def test_gestionar_contacto_crea_uno_nuevo_si_no_existe(make_ctx, make_session):
    session = make_session([[], [{"id": "nuevo-1"}]])
    ctx = make_ctx(session=session)

    resultado = await GestionarContactoTool().run(
        ctx, {"nombre": "Beto Ruiz", "emails": ["beto@ejemplo.com"], "tags": ["proveedor"]}
    )

    assert "Creé" in resultado.content
    assert resultado.data["id"] == "nuevo-1"
    sql_insert, params_insert = session.llamadas[1]
    assert "INSERT INTO contacts" in sql_insert
    assert json.loads(params_insert["emails"]) == ["beto@ejemplo.com"]


async def test_gestionar_contacto_actualiza_si_ya_existe_por_nombre(make_ctx, make_session):
    session = make_session([[{"id": "existente-1"}]])
    ctx = make_ctx(session=session)

    resultado = await GestionarContactoTool().run(
        ctx, {"nombre": "Beto Ruiz", "empresa": "Nueva Empresa"}
    )

    assert "Actualicé" in resultado.content
    assert resultado.data["id"] == "existente-1"
    sql_update, params_update = session.llamadas[1]
    assert "UPDATE contacts" in sql_update
    assert params_update["empresa"] == "Nueva Empresa"
    assert params_update["id"] == "existente-1"


async def test_gestionar_contacto_sin_nombre_no_toca_la_sesion(make_ctx, make_session):
    session = make_session([])
    resultado = await GestionarContactoTool().run(make_ctx(session=session), {"nombre": "  "})
    assert "nombre" in resultado.content.lower()
    assert session.llamadas == []


async def test_gestionar_contacto_actualiza_solo_los_campos_provistos(make_ctx, make_session):
    """Si el LLM manda un subconjunto de campos (p. ej. solo `tags`), el UPDATE
    no debe tocar emails/phones/empresa/notas: deben preservarse tal cual
    estaban, no sobreescribirse con vacío."""
    session = make_session([[{"id": "existente-1"}]])
    ctx = make_ctx(session=session)

    resultado = await GestionarContactoTool().run(
        ctx, {"nombre": "Ana Torres", "tags": ["cliente"]}
    )

    assert "Actualicé" in resultado.content
    sql_update, params_update = session.llamadas[1]
    assert "UPDATE contacts" in sql_update
    assert json.loads(params_update["tags"]) == ["cliente"]
    assert "emails" not in params_update
    assert "phones" not in params_update
    assert "empresa" not in params_update
    assert "notas" not in params_update
    assert "emails" not in sql_update
    assert "phones" not in sql_update
    assert "empresa" not in sql_update
    assert "notas" not in sql_update


async def test_gestionar_contacto_sin_campos_opcionales_no_ejecuta_update(make_ctx, make_session):
    """Si el contacto ya existe y no llegó ningún campo opcional (solo
    `nombre`), no hace falta pegarle a la base con un UPDATE vacío."""
    session = make_session([[{"id": "existente-1"}]])
    ctx = make_ctx(session=session)

    resultado = await GestionarContactoTool().run(ctx, {"nombre": "Ana Torres"})

    assert "Actualicé" in resultado.content
    assert len(session.llamadas) == 1  # solo el SELECT de búsqueda por nombre
