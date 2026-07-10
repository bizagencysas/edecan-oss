"""Tests de `edecan_connectors.microsoft.graph`."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from edecan_connectors.base import ConnectorError
from edecan_connectors.microsoft import graph

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


@respx.mock
async def test_search_mail_parses_results(token_bundle) -> None:
    respx.get(f"{GRAPH_BASE}/me/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "gm1",
                        "from": {"emailAddress": {"address": "jefe@empresa.com"}},
                        "subject": "Reporte semanal",
                        "bodyPreview": "Aquí va el resumen...",
                        "receivedDateTime": "2025-01-01T10:00:00Z",
                    }
                ]
            },
        )
    )

    async with httpx.AsyncClient() as client:
        results = await graph.search_mail(client, token_bundle, "reporte")

    assert results == [
        {
            "id": "gm1",
            "from": "jefe@empresa.com",
            "subject": "Reporte semanal",
            "snippet": "Aquí va el resumen...",
            "received_at": "2025-01-01T10:00:00Z",
        }
    ]


@respx.mock
async def test_send_mail_posts_correct_json_shape(token_bundle) -> None:
    route = respx.post(f"{GRAPH_BASE}/me/sendMail").mock(return_value=httpx.Response(202))

    async with httpx.AsyncClient() as client:
        await graph.send_mail(
            client, token_bundle, to="dest@example.com", subject="Asunto", body_text="Cuerpo"
        )

    assert route.called
    body = json.loads(route.calls.last.request.content)
    assert body == {
        "message": {
            "subject": "Asunto",
            "body": {"contentType": "Text", "content": "Cuerpo"},
            "toRecipients": [{"emailAddress": {"address": "dest@example.com"}}],
        },
        "saveToSentItems": "true",
    }


@respx.mock
async def test_list_events_uses_calendar_view(token_bundle) -> None:
    route = respx.get(f"{GRAPH_BASE}/me/calendarView").mock(
        return_value=httpx.Response(200, json={"value": [{"id": "ev1"}]})
    )

    async with httpx.AsyncClient() as client:
        events = await graph.list_events(
            client, token_bundle, "2025-01-01T00:00:00Z", "2025-01-02T00:00:00Z"
        )

    assert events == [{"id": "ev1"}]
    params = route.calls.last.request.url.params
    assert params["startDateTime"] == "2025-01-01T00:00:00Z"
    assert params["endDateTime"] == "2025-01-02T00:00:00Z"


@respx.mock
async def test_create_event_sends_correct_body(token_bundle) -> None:
    route = respx.post(f"{GRAPH_BASE}/me/events").mock(
        return_value=httpx.Response(200, json={"id": "created-1"})
    )

    async with httpx.AsyncClient() as client:
        created = await graph.create_event(
            client,
            token_bundle,
            summary="Reunión con cliente",
            start_iso="2025-01-01T10:00:00Z",
            end_iso="2025-01-01T11:00:00Z",
            description="Notas",
        )

    assert created == {"id": "created-1"}
    body = json.loads(route.calls.last.request.content)
    assert body["subject"] == "Reunión con cliente"
    assert body["start"] == {"dateTime": "2025-01-01T10:00:00Z", "timeZone": "UTC"}
    assert body["body"] == {"contentType": "Text", "content": "Notas"}


@respx.mock
async def test_send_mail_raises_connector_error_on_failure(token_bundle) -> None:
    respx.post(f"{GRAPH_BASE}/me/sendMail").mock(
        return_value=httpx.Response(400, text="Bad Request")
    )

    async with httpx.AsyncClient() as client:
        with pytest.raises(ConnectorError):
            await graph.send_mail(client, token_bundle, "a@b.com", "S", "B")
