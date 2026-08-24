"""Llamadas autenticadas a Microsoft Graph (`https://graph.microsoft.com/v1.0`)."""

from __future__ import annotations

from typing import Any

import httpx
from edecan_schemas import TokenBundle

from ..base import ConnectorError

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

_MAX_RESULTS = 10


def _auth_headers(bundle: TokenBundle) -> dict[str, str]:
    return {"Authorization": f"Bearer {bundle.access_token}"}


def _raise_for_graph_error(response: httpx.Response) -> None:
    if response.status_code >= 400:
        raise ConnectorError(f"Error de Microsoft Graph ({response.status_code}): {response.text}")


async def search_mail(
    http: httpx.AsyncClient,
    bundle: TokenBundle,
    query: str,
    max_results: int = _MAX_RESULTS,
) -> list[dict[str, Any]]:
    """Busca mensajes de Outlook (`$search` de Microsoft Graph) y devuelve,
    para cada uno (máx. 10), `id`, `from`, `subject` y `snippet`.
    """
    limit = max(1, min(max_results, _MAX_RESULTS))
    response = await http.get(
        f"{GRAPH_BASE}/me/messages",
        params={
            "$search": f'"{query}"',
            "$top": limit,
            "$select": "id,subject,from,bodyPreview,receivedDateTime",
        },
        headers={**_auth_headers(bundle), "ConsistencyLevel": "eventual"},
    )
    _raise_for_graph_error(response)
    items = response.json().get("value", [])
    return [
        {
            "id": item.get("id"),
            "from": (item.get("from") or {}).get("emailAddress", {}).get("address", ""),
            "subject": item.get("subject", ""),
            "snippet": item.get("bodyPreview", ""),
            "received_at": item.get("receivedDateTime"),
        }
        for item in items[:limit]
    ]


async def send_mail(
    http: httpx.AsyncClient,
    bundle: TokenBundle,
    to: str,
    subject: str,
    body_text: str,
) -> None:
    """Envía un correo de texto plano vía Microsoft Graph (`POST /me/sendMail`)."""
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body_text},
            "toRecipients": [{"emailAddress": {"address": to}}],
        },
        "saveToSentItems": "true",
    }
    response = await http.post(
        f"{GRAPH_BASE}/me/sendMail", json=payload, headers=_auth_headers(bundle)
    )
    _raise_for_graph_error(response)


async def list_events(
    http: httpx.AsyncClient,
    bundle: TokenBundle,
    time_min: str,
    time_max: str,
) -> list[dict[str, Any]]:
    """Lista eventos del calendario principal entre `time_min` y `time_max`
    (ISO 8601) usando `/me/calendarView`.
    """
    response = await http.get(
        f"{GRAPH_BASE}/me/calendarView",
        params={
            "startDateTime": time_min,
            "endDateTime": time_max,
            "$orderby": "start/dateTime",
        },
        headers={**_auth_headers(bundle), "Prefer": 'outlook.timezone="UTC"'},
    )
    _raise_for_graph_error(response)
    return response.json().get("value", [])


async def create_event(
    http: httpx.AsyncClient,
    bundle: TokenBundle,
    summary: str,
    start_iso: str,
    end_iso: str,
    description: str | None = None,
) -> dict[str, Any]:
    """Crea un evento en el calendario principal de Outlook (`POST /me/events`)."""
    body: dict[str, Any] = {
        "subject": summary,
        "start": {"dateTime": start_iso, "timeZone": "UTC"},
        "end": {"dateTime": end_iso, "timeZone": "UTC"},
    }
    if description:
        body["body"] = {"contentType": "Text", "content": description}
    response = await http.post(f"{GRAPH_BASE}/me/events", json=body, headers=_auth_headers(bundle))
    _raise_for_graph_error(response)
    return response.json()
