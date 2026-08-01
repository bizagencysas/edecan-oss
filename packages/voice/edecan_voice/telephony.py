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
    aliases = {
        "initiated": "queued",
        "in-progress": "in_progress",
        "no-answer": "no_answer",
        "canceled": "cancelled",
    }
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
        from_e164: str | None = None,
    ) -> TwilioCall:
        """`from_e164` permite marcar desde un número distinto al de las credenciales.

        Hace falta porque el gateway se construye como dependencia, ANTES de saber a quién
        se llama: con varios números conectados, el llamador elige el del mismo país que el
        destino (ver `_elegir_numero_saliente`) y lo pasa acá. Sin esto, llamar a Colombia
        salía desde un gratuito de EE.UU., que entrega peor y le aparece al destinatario
        como una llamada extranjera.
        """
        to_phone = normalize_e164(to_e164)
        endpoint = f"{TWILIO_API_BASE}/Accounts/{self.credentials.account_sid}/Calls.json"
        payload = {
            "To": to_phone,
            "From": normalize_e164(from_e164) if from_e164 else self.credentials.phone_number,
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


_DIGITOS_VALIDOS = frozenset("0123456789*#w")
_MAX_DIGITOS = 32

# Marcador que el modelo escribe cuando necesita marcar teclas, p. ej. para pasar el menú
# de una central ("marque 1 para ventas"). Se saca del texto ANTES de hablar: nadie debe oír
# "corchete tonos uno". Ver `extraer_tonos`.
_PATRON_TONOS = re.compile(r"\[\[\s*tonos\s*:\s*([0-9*#w,\s]{1,64})\s*\]\]", re.IGNORECASE)


def normalizar_digitos(raw: str | None) -> str:
    """Deja solo lo que Twilio acepta en `<Play digits>`: dígitos, `*`, `#` y `w` (pausa ~0.5s).

    Devuelve cadena vacía si no queda nada marcable, para que el llamador simplemente omita
    el `<Play>` en vez de emitir TwiML inválido y tumbar la llamada entera.
    """
    if not raw:
        return ""
    limpio = "".join(c for c in str(raw).lower() if c in _DIGITOS_VALIDOS)
    return limpio[:_MAX_DIGITOS]


def extraer_tonos(texto: str) -> tuple[str, str]:
    """Separa `(texto_hablado, digitos)` a partir del marcador `[[tonos:123#]]`.

    Los menús de central telefónica ("marque 1 para ventas") no se pasan hablando: hay que
    mandar tonos DTMF de verdad. El modelo no puede emitir XML, así que pide los tonos con un
    marcador dentro de su respuesta y aquí se convierte en `<Play digits>`.

    Se admiten varios marcadores en una misma respuesta (se concatenan en orden). El texto
    devuelto ya viene sin ellos y con los espacios colapsados, listo para hablarse.
    """
    digitos: list[str] = []

    def _recoger(match: re.Match[str]) -> str:
        digitos.append(normalizar_digitos(match.group(1)))
        return " "

    hablado = _PATRON_TONOS.sub(_recoger, texto or "")
    return " ".join(hablado.split()), normalizar_digitos("".join(digitos))


def conversation_twiml(
    *,
    message: str,
    gather_url: str,
    language: str = "es-MX",
    end_after_message: bool = False,
    play_url: str | None = None,
    send_digits: str | None = None,
) -> str:
    """TwiML de un turno: habla, marca teclas si hace falta, y escucha la siguiente entrada.

    `send_digits` emite `<Play digits>` DESPUÉS de hablar: si una central pide "marque 1",
    primero se dice lo que haya que decir y recién entonces se manda el tono, nunca al revés.

    El `Gather` acepta `speech dtmf` (no solo voz) para que también se capten los tonos que
    marque el otro lado — un menú puede pedir confirmación por teclado.
    """
    root = Element("Response")
    if play_url:
        SubElement(root, "Play").text = play_url
    else:
        SubElement(root, "Say", {"language": language}).text = message
    tonos = normalizar_digitos(send_digits)
    if tonos:
        SubElement(root, "Play", {"digits": tonos})
    if end_after_message:
        SubElement(root, "Hangup")
    else:
        gather = SubElement(
            root,
            "Gather",
            {
                "input": "speech dtmf",
                "action": gather_url,
                "method": "POST",
                "language": language,
                "speechTimeout": "auto",
                "timeout": "5",
                # Un menú puede pedir varias teclas seguidas; `#` las cierra.
                "numDigits": "8",
                "finishOnKey": "#",
            },
        )
        if not play_url:
            SubElement(gather, "Say", {"language": language}).text = "Te escucho."
        SubElement(root, "Redirect", {"method": "POST"}).text = gather_url
    return _xml(root)


def reject_twiml(message: str) -> str:
    root = Element("Response")
    SubElement(root, "Say", {"language": "es-MX"}).text = message
    SubElement(root, "Hangup")
    return _xml(root)
