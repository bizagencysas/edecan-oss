"""Tests de `edecan_connectors.google.gmail`."""

from __future__ import annotations

import base64
import email
import json

import httpx
import respx
from edecan_connectors.google import gmail

BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


@respx.mock
async def test_search_messages_fetches_details_and_extracts_fields(token_bundle) -> None:
    respx.get(f"{BASE}/messages").mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "m1"}, {"id": "m2"}]})
    )
    respx.get(f"{BASE}/messages/m1").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "m1",
                "threadId": "t1",
                "snippet": "Hola desde m1",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "a@example.com"},
                        {"name": "Subject", "value": "Asunto 1"},
                    ]
                },
            },
        )
    )
    respx.get(f"{BASE}/messages/m2").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "m2",
                "threadId": "t2",
                "snippet": "Hola desde m2",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "b@example.com"},
                        {"name": "Subject", "value": "Asunto 2"},
                    ]
                },
            },
        )
    )

    async with httpx.AsyncClient() as client:
        results = await gmail.search_messages(client, token_bundle, "is:unread")

    assert len(results) == 2
    assert results[0] == {
        "id": "m1",
        "thread_id": "t1",
        "from": "a@example.com",
        "subject": "Asunto 1",
        "snippet": "Hola desde m1",
    }
    assert results[1]["from"] == "b@example.com"


@respx.mock
async def test_search_messages_respects_max_results_and_avoids_extra_fetches(
    token_bundle,
) -> None:
    respx.get(f"{BASE}/messages").mock(
        return_value=httpx.Response(
            200, json={"messages": [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}]}
        )
    )
    respx.get(f"{BASE}/messages/m1").mock(return_value=httpx.Response(200, json={"id": "m1"}))
    m2_route = respx.get(f"{BASE}/messages/m2").mock(
        return_value=httpx.Response(200, json={"id": "m2"})
    )
    m3_route = respx.get(f"{BASE}/messages/m3").mock(
        return_value=httpx.Response(200, json={"id": "m3"})
    )

    async with httpx.AsyncClient() as client:
        results = await gmail.search_messages(client, token_bundle, "q", max_results=1)

    assert len(results) == 1
    assert not m2_route.called
    assert not m3_route.called


@respx.mock
async def test_send_message_builds_correct_raw_mime(token_bundle) -> None:
    route = respx.post(f"{BASE}/messages/send").mock(
        return_value=httpx.Response(200, json={"id": "sent-1"})
    )

    async with httpx.AsyncClient() as client:
        result = await gmail.send_message(
            client,
            token_bundle,
            to="dest@example.com",
            subject="Asunto de prueba",
            body_text="Cuerpo del mensaje con ñ y á",
        )

    assert result == {"id": "sent-1"}
    sent_body = json.loads(route.calls.last.request.content)
    parsed = email.message_from_bytes(base64.urlsafe_b64decode(sent_body["raw"]))
    assert parsed["To"] == "dest@example.com"
    assert parsed["Subject"] == "Asunto de prueba"
    assert parsed.get_payload(decode=True).decode("utf-8") == "Cuerpo del mensaje con ñ y á"


@respx.mock
async def test_create_draft_wraps_raw_message_in_message_field(token_bundle) -> None:
    route = respx.post(f"{BASE}/drafts").mock(
        return_value=httpx.Response(200, json={"id": "draft-1"})
    )

    async with httpx.AsyncClient() as client:
        result = await gmail.create_draft(
            client, token_bundle, to="dest@example.com", subject="Borrador", body_text="Texto"
        )

    assert result == {"id": "draft-1"}
    sent_body = json.loads(route.calls.last.request.content)
    parsed = email.message_from_bytes(base64.urlsafe_b64decode(sent_body["message"]["raw"]))
    assert parsed["Subject"] == "Borrador"


@respx.mock
async def test_search_messages_sends_bearer_token(token_bundle) -> None:
    route = respx.get(f"{BASE}/messages").mock(
        return_value=httpx.Response(200, json={"messages": []})
    )
    async with httpx.AsyncClient() as client:
        await gmail.search_messages(client, token_bundle, "q")

    auth_header = route.calls.last.request.headers["Authorization"]
    assert auth_header == f"Bearer {token_bundle.access_token}"
