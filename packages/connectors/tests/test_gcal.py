"""Tests de `edecan_connectors.google.gcal`."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from edecan_connectors.base import ConnectorError
from edecan_connectors.google import gcal

EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


@respx.mock
async def test_list_events_sends_time_range_and_returns_items(token_bundle) -> None:
    route = respx.get(EVENTS_URL).mock(
        return_value=httpx.Response(200, json={"items": [{"id": "ev1"}, {"id": "ev2"}]})
    )

    async with httpx.AsyncClient() as client:
        events = await gcal.list_events(
            client, token_bundle, "2025-01-01T00:00:00Z", "2025-01-02T00:00:00Z"
        )

    assert events == [{"id": "ev1"}, {"id": "ev2"}]
    params = route.calls.last.request.url.params
    assert params["timeMin"] == "2025-01-01T00:00:00Z"
    assert params["timeMax"] == "2025-01-02T00:00:00Z"
    assert params["singleEvents"] == "true"


@respx.mock
async def test_create_event_sends_correct_body(token_bundle) -> None:
    route = respx.post(EVENTS_URL).mock(
        return_value=httpx.Response(200, json={"id": "created-1"})
    )

    async with httpx.AsyncClient() as client:
        created = await gcal.create_event(
            client,
            token_bundle,
            summary="Reunión con cliente",
            start_iso="2025-01-01T10:00:00-05:00",
            end_iso="2025-01-01T11:00:00-05:00",
            description="Notas de la reunión",
        )

    assert created == {"id": "created-1"}
    body = json.loads(route.calls.last.request.content)
    assert body["summary"] == "Reunión con cliente"
    assert body["start"] == {"dateTime": "2025-01-01T10:00:00-05:00"}
    assert body["end"] == {"dateTime": "2025-01-01T11:00:00-05:00"}
    assert body["description"] == "Notas de la reunión"


@respx.mock
async def test_create_event_without_description_omits_field(token_bundle) -> None:
    respx.post(EVENTS_URL).mock(return_value=httpx.Response(200, json={"id": "created-2"}))

    async with httpx.AsyncClient() as client:
        await gcal.create_event(
            client, token_bundle, "Solo", "2025-01-01T10:00:00Z", "2025-01-01T11:00:00Z"
        )

    body = json.loads(respx.calls.last.request.content)
    assert "description" not in body


@respx.mock
async def test_list_events_raises_connector_error_on_http_error(token_bundle) -> None:
    respx.get(EVENTS_URL).mock(return_value=httpx.Response(403, text="Forbidden"))

    async with httpx.AsyncClient() as client:
        with pytest.raises(ConnectorError):
            await gcal.list_events(
                client, token_bundle, "2025-01-01T00:00:00Z", "2025-01-02T00:00:00Z"
            )
