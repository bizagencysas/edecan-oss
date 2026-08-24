"""Tests de `edecan_toolkit.recordatorios`."""

from __future__ import annotations

from datetime import UTC, datetime

from edecan_toolkit.recordatorios import CrearRecordatorioTool, ListarRecordatoriosTool


async def test_crear_recordatorio_inserta_y_confirma(make_ctx, make_session):
    session = make_session([[{"id": "11111111-1111-1111-1111-111111111111"}]])
    ctx = make_ctx(session=session)

    resultado = await CrearRecordatorioTool().run(
        ctx, {"mensaje": "Llamar al banco", "due_at": "2026-08-01T10:00:00-05:00"}
    )

    assert "Llamar al banco" in resultado.content
    assert resultado.data["id"] == "11111111-1111-1111-1111-111111111111"
    assert resultado.data["channel"] == "mobile"

    sql, params = session.llamadas[0]
    assert "INSERT INTO reminders" in sql
    assert "RETURNING id" in sql
    assert params["message"] == "Llamar al banco"
    assert params["channel"] == "mobile"
    assert params["rrule"] is None


async def test_crear_recordatorio_admite_rrule_y_channel(make_ctx, make_session):
    session = make_session([[{"id": "id-2"}]])
    ctx = make_ctx(session=session)

    await CrearRecordatorioTool().run(
        ctx,
        {
            "mensaje": "Tomar la pastilla",
            "due_at": "2026-08-01T08:00:00Z",
            "rrule": "FREQ=DAILY",
            "channel": "voice",
        },
    )

    _sql, params = session.llamadas[0]
    assert params["rrule"] == "FREQ=DAILY"
    assert params["channel"] == "voice"


async def test_crear_recordatorio_rechaza_fecha_invalida_sin_tocar_sesion(make_ctx, make_session):
    session = make_session([])
    resultado = await CrearRecordatorioTool().run(
        make_ctx(session=session), {"mensaje": "x", "due_at": "no-es-una-fecha"}
    )
    assert "fecha" in resultado.content.lower()
    assert session.llamadas == []


async def test_crear_recordatorio_rechaza_mensaje_vacio(make_ctx, make_session):
    session = make_session([])
    resultado = await CrearRecordatorioTool().run(
        make_ctx(session=session), {"mensaje": "   ", "due_at": "2026-01-01T00:00:00Z"}
    )
    assert "mensaje" in resultado.content.lower()
    assert session.llamadas == []


async def test_crear_recordatorio_channel_invalido_cae_a_web(make_ctx, make_session):
    session = make_session([[{"id": "id-3"}]])
    await CrearRecordatorioTool().run(
        make_ctx(session=session),
        {"mensaje": "x", "due_at": "2026-01-01T00:00:00Z", "channel": "fax"},
    )
    _sql, params = session.llamadas[0]
    assert params["channel"] == "web"


async def test_listar_recordatorios_formatea_pendientes(make_ctx, make_session):
    filas = [
        {
            "id": "aaaa",
            "due_at": datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
            "rrule": None,
            "message": "Pagar renta",
            "channel": "web",
            "status": "pending",
        }
    ]
    ctx = make_ctx(session=make_session([filas]))

    resultado = await ListarRecordatoriosTool().run(ctx, {})

    assert "Pagar renta" in resultado.content
    assert len(resultado.data["recordatorios"]) == 1
    assert resultado.data["recordatorios"][0]["status"] == "pending"


async def test_listar_recordatorios_sin_resultados(make_ctx, make_session):
    ctx = make_ctx(session=make_session([[]]))
    resultado = await ListarRecordatoriosTool().run(ctx, {})
    assert resultado.data["recordatorios"] == []
    assert "no tienes recordatorios" in resultado.content.lower()


async def test_listar_recordatorios_filtra_por_status_a_menos_que_se_pida_incluir_completados(
    make_ctx, make_session
):
    session = make_session([[]])
    ctx = make_ctx(session=session)

    await ListarRecordatoriosTool().run(ctx, {})
    sql_sin_completados, _ = session.llamadas[0]
    assert "status = 'pending'" in sql_sin_completados

    session2 = make_session([[]])
    ctx2 = make_ctx(session=session2)
    await ListarRecordatoriosTool().run(ctx2, {"incluir_completados": True})
    sql_con_completados, _ = session2.llamadas[0]
    assert "status = 'pending'" not in sql_con_completados


async def test_listar_recordatorios_limite_se_acota(make_ctx, make_session):
    session = make_session([[]])
    ctx = make_ctx(session=session)
    await ListarRecordatoriosTool().run(ctx, {"limite": 999})
    _sql, params = session.llamadas[0]
    assert params["limite"] == 100
