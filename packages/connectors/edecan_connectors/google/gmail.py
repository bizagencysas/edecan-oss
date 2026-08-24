"""Llamadas autenticadas a Gmail API (`https://gmail.googleapis.com/gmail/v1`).

Todas las funciones reciben `(http, bundle, ...)` y usan
`Authorization: Bearer {bundle.access_token}`. Ninguna persiste credenciales.
"""

from __future__ import annotations

import base64
from email.mime.text import MIMEText
from typing import Any

import httpx
from edecan_schemas import TokenBundle

from ..base import ConnectorError

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"

_MAX_RESULTS = 10


def _auth_headers(bundle: TokenBundle) -> dict[str, str]:
    return {"Authorization": f"Bearer {bundle.access_token}"}


def _raise_for_gmail_error(response: httpx.Response) -> None:
    if response.status_code >= 400:
        raise ConnectorError(f"Error de Gmail API ({response.status_code}): {response.text}")


async def search_messages(
    http: httpx.AsyncClient,
    bundle: TokenBundle,
    q: str,
    max_results: int = _MAX_RESULTS,
) -> list[dict[str, Any]]:
    """Busca mensajes con la sintaxis de búsqueda de Gmail (`q`, p. ej.
    `from:jefe@empresa.com is:unread`) y devuelve, para cada uno (máx. 10),
    `id`, `thread_id`, `from`, `subject` y `snippet`.
    """
    limit = max(1, min(max_results, _MAX_RESULTS))
    list_response = await http.get(
        f"{GMAIL_API_BASE}/messages",
        params={"q": q, "maxResults": limit},
        headers=_auth_headers(bundle),
    )
    _raise_for_gmail_error(list_response)
    message_ids = [item["id"] for item in list_response.json().get("messages", [])]

    results: list[dict[str, Any]] = []
    for message_id in message_ids[:limit]:
        detail_response = await http.get(
            f"{GMAIL_API_BASE}/messages/{message_id}",
            params={"format": "metadata", "metadataHeaders": ["From", "Subject"]},
            headers=_auth_headers(bundle),
        )
        _raise_for_gmail_error(detail_response)
        detail = detail_response.json()
        headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
        results.append(
            {
                "id": detail.get("id", message_id),
                "thread_id": detail.get("threadId"),
                "from": headers.get("From", ""),
                "subject": headers.get("Subject", ""),
                "snippet": detail.get("snippet", ""),
            }
        )
    return results


async def send_message(
    http: httpx.AsyncClient,
    bundle: TokenBundle,
    to: str,
    subject: str,
    body_text: str,
) -> dict[str, Any]:
    """Envía un correo de texto plano vía Gmail API (`POST .../messages/send`)."""
    response = await http.post(
        f"{GMAIL_API_BASE}/messages/send",
        json={"raw": _build_raw_message(to=to, subject=subject, body_text=body_text)},
        headers=_auth_headers(bundle),
    )
    _raise_for_gmail_error(response)
    return response.json()


async def create_draft(
    http: httpx.AsyncClient,
    bundle: TokenBundle,
    to: str,
    subject: str,
    body_text: str,
) -> dict[str, Any]:
    """Crea (sin enviar) un borrador vía Gmail API (`POST .../drafts`)."""
    response = await http.post(
        f"{GMAIL_API_BASE}/drafts",
        json={"message": {"raw": _build_raw_message(to=to, subject=subject, body_text=body_text)}},
        headers=_auth_headers(bundle),
    )
    _raise_for_gmail_error(response)
    return response.json()


def _build_raw_message(*, to: str, subject: str, body_text: str) -> str:
    """Arma un mensaje MIME simple y lo codifica en base64url, tal como lo
    exige el campo `raw` de la Gmail API.
    """
    message = MIMEText(body_text, "plain", "utf-8")
    message["To"] = to
    message["Subject"] = subject
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
