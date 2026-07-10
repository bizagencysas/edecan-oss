"""Tests de `edecan_toolkit.agenda`: `agenda_eventos` y `crear_evento`.

Usa `respx` (offline, determinista — `ARCHITECTURE.md` §10.15) para las
llamadas HTTP reales de los conectores; el bundle es un `SimpleNamespace` local
(no se importa `edecan_schemas.TokenBundle`, ver `conftest.py`).
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import respx
from edecan_toolkit.agenda import AgendaEventosTool, CrearEventoTool

GOOGLE_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
MS_EVENTS_URL = "https://graph.microsoft.com/v1.0/me/calendarView"


async def test_agenda_eventos_sin_cuenta_conectada_pide_conectar(make_ctx, make_session):
    ctx = make_ctx(session=make_session([[]]))
    resultado = await AgendaEventosTool().run(ctx, {})
    assert "/app/conectores" in resultado.content


async def test_agenda_eventos_cuenta_conectada_pero_sin_bundle_en_el_vault(
    make_ctx, make_session, make_vault
):
    fila_cuenta = {"id": "acc-1", "connector_key": "google"}
    ctx = make_ctx(session=make_session([[fila_cuenta]]), vault=make_vault(bundle=None))
    resultado = await AgendaEventosTool().run(ctx, {})
    assert "/app/conectores" in resultado.content


@respx.mock
async def test_agenda_eventos_lista_via_google(make_ctx, make_session, make_vault):
    fila_cuenta = {"id": "acc-1", "connector_key": "google"}
    bundle = SimpleNamespace(access_token="tok-123")
    ctx = make_ctx(session=make_session([[fila_cuenta]]), vault=make_vault(bundle=bundle))

    respx.get(GOOGLE_EVENTS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "ev1",
                        "summary": "Reunión con cliente",
                        "start": {"dateTime": "2026-08-01T10:00:00-05:00"},
                        "end": {"dateTime": "2026-08-01T11:00:00-05:00"},
                    }
                ]
            },
        )
    )

    resultado = await AgendaEventosTool().run(ctx, {})

    assert "Reunión con cliente" in resultado.content
    assert resultado.data["eventos"][0]["titulo"] == "Reunión con cliente"


@respx.mock
async def test_agenda_eventos_lista_via_microsoft(make_ctx, make_session, make_vault):
    fila_cuenta = {"id": "acc-2", "connector_key": "microsoft"}
    bundle = SimpleNamespace(access_token="tok-456")
    ctx = make_ctx(session=make_session([[fila_cuenta]]), vault=make_vault(bundle=bundle))

    respx.get(MS_EVENTS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "ev2",
                        "subject": "Standup",
                        "start": {"dateTime": "2026-08-01T09:00:00"},
                        "end": {"dateTime": "2026-08-01T09:15:00"},
                    }
                ]
            },
        )
    )

    resultado = await AgendaEventosTool().run(ctx, {})

    assert "Standup" in resultado.content
    assert resultado.data["eventos"][0]["titulo"] == "Standup"


@respx.mock
async def test_agenda_eventos_sin_eventos_en_el_rango(make_ctx, make_session, make_vault):
    fila_cuenta = {"id": "acc-1", "connector_key": "google"}
    bundle = SimpleNamespace(access_token="tok-123")
    ctx = make_ctx(session=make_session([[fila_cuenta]]), vault=make_vault(bundle=bundle))

    respx.get(GOOGLE_EVENTS_URL).mock(return_value=httpx.Response(200, json={"items": []}))
    resultado = await AgendaEventosTool().run(ctx, {})

    assert resultado.data["eventos"] == []
    assert "no tienes eventos" in resultado.content.lower()


@respx.mock
async def test_crear_evento_via_google_incluye_link(make_ctx, make_session, make_vault):
    fila_cuenta = {"id": "acc-1", "connector_key": "google"}
    bundle = SimpleNamespace(access_token="tok-123")
    ctx = make_ctx(session=make_session([[fila_cuenta]]), vault=make_vault(bundle=bundle))

    respx.post(GOOGLE_EVENTS_URL).mock(
        return_value=httpx.Response(
            200, json={"id": "created-1", "htmlLink": "https://calendar.google.com/event?eid=1"}
        )
    )

    resultado = await CrearEventoTool().run(
        ctx,
        {
            "titulo": "Demo con inversionista",
            "inicio": "2026-08-01T10:00:00-05:00",
            "fin": "2026-08-01T11:00:00-05:00",
        },
    )

    assert "Demo con inversionista" in resultado.content
    assert "calendar.google.com" in resultado.content
    assert resultado.data["evento"]["id"] == "created-1"


@respx.mock
async def test_crear_evento_sin_titulo_no_llama_a_ningun_conector(
    make_ctx, make_session, make_vault
):
    fila_cuenta = {"id": "acc-1", "connector_key": "google"}
    bundle = SimpleNamespace(access_token="tok-123")
    ctx = make_ctx(session=make_session([[fila_cuenta]]), vault=make_vault(bundle=bundle))

    ruta = respx.post(GOOGLE_EVENTS_URL).mock(
        return_value=httpx.Response(200, json={"id": "no-debe-usarse"})
    )
    resultado = await CrearEventoTool().run(
        ctx, {"titulo": "  ", "inicio": "2026-08-01T10:00:00Z", "fin": "2026-08-01T11:00:00Z"}
    )

    assert not ruta.called
    assert "título" in resultado.content.lower()
