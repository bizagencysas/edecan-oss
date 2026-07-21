"""Telefonía Twilio abierta para Edecán.

Este módulo contiene únicamente contratos de proveedor y primitivas puras:
validación E.164, firma de webhooks, cliente REST inyectable y generación de
TwiML. No conoce FastAPI ni la base de datos, por lo que se puede probar sin
credenciales ni llamadas reales.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from xml.etree.ElementTree import Element, SubElement, tostring

import httpx

TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"
E164_RE = re.compile(r"^\+[1-9]\d{1,14}$")
CALL_STATUSES = frozenset(
    {
        "draft",
        "confirmed",
        "queued",
        "ringing",
        "in_progress",
        "completed",
        "failed",
        "busy",
        "no_answer",
        "cancelled",
    }
)


class TelephonyError(RuntimeError):
    """Error seguro de dominio o del proveedor de telefonía."""


@dataclass(frozen=True)
class TwilioCredentials:
    account_sid: str
    auth_token: str
    phone_number: str

    def __post_init__(self) -> None:
        if not self.account_sid.startswith("AC") or len(self.account_sid) != 34:
            raise ValueError("Account SID de Twilio inválido.")
        if not self.auth_token:
            raise ValueError("Auth Token de Twilio vacío.")
        normalize_e164(self.phone_number)


@dataclass(frozen=True)
class TwilioCall:
    sid: str
    status: str


def normalize_e164(value: Any) -> str:
    """Normaliza espacios exteriores y exige el formato internacional E.164."""
    phone = str(value or "").strip()
    if not E164_RE.fullmatch(phone):
        raise ValueError("Usa un número internacional E.164, por ejemplo +573001234567.")
    return phone


def normalize_goal(value: Any, *, max_chars: int = 500) -> str:
    goal = " ".join(str(value or "").split()).strip()
    if not goal:
        raise ValueError("Explica qué debe conseguir Edecan durante la llamada.")
    if len(goal) > max_chars:
        raise ValueError(f"El objetivo de la llamada no puede superar {max_chars} caracteres.")
    return goal


def normalize_twilio_status(value: Any) -> str:
    status = str(value or "").strip().lower().replace("-", "_")
    aliases = {"initiated": "queued", "in-progress": "in_progress", "no-answer": "no_answer"}
    status = aliases.get(status, status)
    return status if status in CALL_STATUSES else "failed"


def twilio_signature(url: str, params: Mapping[str, Any], auth_token: str) -> str:
    """Calcula `X-Twilio-Signature` según el algoritmo de Twilio para POST."""
    material = url + "".join(
        f"{key}{value}" for key, value in sorted((str(k), str(v)) for k, v in params.items())
    )
    digest = hmac.new(auth_token.encode(), material.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def verify_twilio_signature(
    *, url: str, params: Mapping[str, Any], auth_token: str, supplied_signature: str | None
) -> bool:
    if not supplied_signature:
        return False
    expected = twilio_signature(url, params, auth_token)
    return hmac.compare_digest(expected, supplied_signature.strip())


class TwilioVoiceClient:
    """Cliente mínimo de llamadas salientes; `http_client` es inyectable en tests."""

    def __init__(
        self,
        credentials: TwilioCredentials,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 12.0,
    ) -> None:
        self.credentials = credentials
        self._provided_client = http_client
        self._timeout_seconds = timeout_seconds

    async def create_call(
        self,
        *,
        to_e164: str,
        voice_url: str,
        status_callback_url: str,
    ) -> TwilioCall:
        to_phone = normalize_e164(to_e164)
        endpoint = (
            f"{TWILIO_API_BASE}/Accounts/{self.credentials.account_sid}/Calls.json"
        )
        payload = {
            "To": to_phone,
            "From": self.credentials.phone_number,
            "Url": voice_url,
            "Method": "POST",
            "StatusCallback": status_callback_url,
            "StatusCallbackMethod": "POST",
            "StatusCallbackEvent": "initiated ringing answered completed",
        }
        own_client = self._provided_client is None
        client = self._provided_client or httpx.AsyncClient(timeout=self._timeout_seconds)
        try:
            response = await client.post(
                endpoint,
                data=payload,
                auth=(self.credentials.account_sid, self.credentials.auth_token),
            )
        except httpx.HTTPError as exc:
            raise TelephonyError("Twilio no respondió al intentar iniciar la llamada.") from exc
        finally:
            if own_client:
                await client.aclose()

        if response.status_code not in {200, 201}:
            raise TelephonyError(
                f"Twilio rechazó la llamada (HTTP {response.status_code}). "
                "Revisa el número y la cuenta."
            )
        try:
            body = response.json()
            sid = str(body["sid"])
            status = normalize_twilio_status(body.get("status", "queued"))
        except (KeyError, TypeError, ValueError) as exc:
            raise TelephonyError("Twilio devolvió una respuesta de llamada inválida.") from exc
        if not sid.startswith("CA"):
            raise TelephonyError("Twilio no devolvió un Call SID válido.")
        return TwilioCall(sid=sid, status=status)


def _xml(root: Element) -> str:
    return '<?xml version="1.0" encoding="UTF-8"?>' + tostring(
        root, encoding="unicode", short_empty_elements=True
    )


def conversation_twiml(
    *,
    message: str,
    gather_url: str,
    language: str = "es-MX",
    end_after_message: bool = False,
) -> str:
    """TwiML de un turno: habla y escucha la siguiente frase, o termina."""
    root = Element("Response")
    SubElement(root, "Say", {"language": language}).text = message
    if end_after_message:
        SubElement(root, "Hangup")
    else:
        gather = SubElement(
            root,
            "Gather",
            {
                "input": "speech",
                "action": gather_url,
                "method": "POST",
                "language": language,
                "speechTimeout": "auto",
                "timeout": "5",
            },
        )
        SubElement(gather, "Say", {"language": language}).text = "Te escucho."
        SubElement(root, "Redirect", {"method": "POST"}).text = gather_url
    return _xml(root)


def reject_twiml(message: str) -> str:
    root = Element("Response")
    SubElement(root, "Say", {"language": "es-MX"}).text = message
    SubElement(root, "Hangup")
    return _xml(root)
