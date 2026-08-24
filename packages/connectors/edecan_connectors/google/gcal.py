"""Llamadas autenticadas a Google Calendar API — calendario `primary` del tenant."""

from __future__ import annotations

from typing import Any

import httpx
from edecan_schemas import TokenBundle

from ..base import ConnectorError

GCAL_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


def _auth_headers(bundle: TokenBundle) -> dict[str, str]:
    return {"Authorization": f"Bearer {bundle.access_token}"}


def _raise_for_gcal_error(response: httpx.Response) -> None:
    if response.status_code >= 400:
        raise ConnectorError(
            f"Error de Google Calendar API ({response.status_code}): {response.text}"
        )


async def list_events(
    http: httpx.AsyncClient,
    bundle: TokenBundle,
    time_min: str,
    time_max: str,
) -> list[dict[str, Any]]:
    """Lista eventos del calendario `primary` entre `time_min` y `time_max`
    (RFC3339, p. ej. `2025-01-01T00:00:00Z`), ordenados por inicio.
    """
    response = await http.get(
        GCAL_EVENTS_URL,
        params={
            "timeMin": time_min,
            "timeMax": time_max,
            "singleEvents": "true",
            "orderBy": "startTime",
        },
        headers=_auth_headers(bundle),
    )
    _raise_for_gcal_error(response)
    return response.json().get("items", [])


async def create_event(
    http: httpx.AsyncClient,
    bundle: TokenBundle,
    summary: str,
    start_iso: str,
    end_iso: str,
    description: str | None = None,
) -> dict[str, Any]:
    """Crea un evento en el calendario `primary`. `start_iso`/`end_iso` son
    fecha-hora RFC3339 (p. ej. `2025-01-01T10:00:00-05:00`).
    """
    body: dict[str, Any] = {
        "summary": summary,
        "start": {"dateTime": start_iso},
        "end": {"dateTime": end_iso},
    }
    if description:
        body["description"] = description
    response = await http.post(GCAL_EVENTS_URL, json=body, headers=_auth_headers(bundle))
    _raise_for_gcal_error(response)
    return response.json()
