"""Cliente HTTP puro de WhatsApp Business Platform (Cloud API oficial de
Meta, `graph.facebook.com`) — WP-V3-13, `ARCHITECTURE.md` §12.b,
`docs/mensajeria.md`.

Mismo espíritu que `clients.py` (Telegram/Discord/Slack, WP-V2-05): recibe la
credencial del tenant YA RESUELTA (`access_token` permanente de la app de
Meta del tenant, más el `phone_number_id` de su número de WhatsApp Business
ya verificado — ver `edecan_api.routers.connectors.connect_whatsapp`) y habla
directo con la Graph API — nunca lee credenciales de variables de entorno ni
las persiste. Reutiliza `MessagingClientError`/`DEFAULT_TIMEOUT` de
`.clients` para que `tools.py` atrape el mismo tipo de error sin importar la
plataforma, y para compartir el mismo timeout por defecto.

Solo ENVÍO en v3 (ver `docs/mensajeria.md`): leer mensajes entrantes de
WhatsApp exige un webhook público verificado (`hub.challenge` +
`X-Hub-Signature-256`) que este work package NO monta — `LeerMensajesTool`
(`tools.py`) responde con un mensaje claro en vez de intentarlo.

Cumplimiento (WhatsApp Business Messaging Policy, ver `docs/mensajeria.md`):
el opt-in del destinatario es responsabilidad del propio tenant (este
paquete no lo verifica — no hay, a diferencia de SMS/voz, un
`connector_key`/tabla `consents` equivalente para WhatsApp en v3); fuera de
la ventana de 24h desde el último mensaje del destinatario, Meta exige una
plantilla pre-aprobada — `enviar_plantilla` existe exactamente para ese caso,
y `_mensaje_error_graph` traduce el código de error 131047 a una explicación
accionable en vez de dejar pasar el error crudo de Meta.
"""

from __future__ import annotations

from typing import Any

import httpx
from edecan_core.safety import redact

from .clients import DEFAULT_TIMEOUT, MessagingClientError

GRAPH_API_BASE = "https://graph.facebook.com/v21.0"
PLATAFORMA = "WhatsApp"

# Códigos de error de la Graph API de Meta que esta tool traduce a un mensaje
# accionable en español (ver docstring del módulo y `docs/mensajeria.md`).
# Referencia: https://developers.facebook.com/docs/whatsapp/cloud-api/support/error-codes
CODIGO_FUERA_DE_VENTANA_24H = 131047
CODIGO_DESTINATARIO_NO_DISPONIBLE = 131026


def normalizar_to(to_e164: str) -> str:
    """Quita un `'+'` inicial si lo trae: la Graph API de WhatsApp espera el
    número de destino SIN `'+'` (a diferencia del E.164 estricto que usa el
    resto del repo, p. ej. Twilio) — ver docstring del módulo. No se limpian
    espacios/guiones a propósito: es responsabilidad de quien llama pasar
    solo dígitos (más un `'+'` inicial opcional)."""
    numero = to_e164.strip()
    return numero[1:] if numero.startswith("+") else numero


def _mensaje_error_graph(payload: dict[str, Any], status_code: int) -> str:
    error = payload.get("error") or {}
    code = error.get("code")
    message = redact(str(error.get("message") or "error desconocido"))
    if code == CODIGO_FUERA_DE_VENTANA_24H:
        return (
            "Fuera de la ventana de 24 horas desde el último mensaje del destinatario: "
            "WhatsApp exige usar una plantilla pre-aprobada (parámetro 'plantilla') para "
            f"iniciar la conversación de nuevo. Detalle de Meta: {message}"
        )
    if code == CODIGO_DESTINATARIO_NO_DISPONIBLE:
        return (
            "El destinatario no está disponible en WhatsApp o no ha dado opt-in para "
            f"recibir mensajes de este número de negocio. Detalle de Meta: {message}"
        )
    return f"Error de la Graph API de WhatsApp (HTTP {status_code}, código {code}): {message}"


class WhatsAppClient:
    """Cliente de la Cloud API de WhatsApp Business Platform para EL número
    propio del tenant (`phone_number_id`), con SU access token permanente
    (system user, ver `docs/mensajeria.md`)."""

    PLATAFORMA = PLATAFORMA

    def __init__(
        self,
        access_token: str,
        phone_number_id: str,
        *,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        if not access_token or not access_token.strip():
            raise MessagingClientError("Falta el access token de WhatsApp.")
        if not phone_number_id or not phone_number_id.strip():
            raise MessagingClientError("Falta el phone_number_id de WhatsApp.")
        self._access_token = access_token.strip()
        self._phone_number_id = phone_number_id.strip()
        self._http = http

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token}"}

    async def _post_message(self, body: dict[str, Any]) -> dict[str, Any]:
        client = self._http
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)
        try:
            response = await client.post(
                f"{GRAPH_API_BASE}/{self._phone_number_id}/messages",
                json=body,
                headers=self._headers(),
            )
        except httpx.HTTPError as exc:
            raise MessagingClientError(
                f"No se pudo contactar la Graph API de WhatsApp: {redact(str(exc))}"
            ) from exc
        finally:
            if owns_client:
                await client.aclose()

        try:
            payload = response.json()
        except ValueError as exc:
            raise MessagingClientError(
                f"Respuesta no-JSON de la Graph API de WhatsApp (HTTP {response.status_code})."
            ) from exc

        if response.status_code >= 400:
            raise MessagingClientError(_mensaje_error_graph(payload, response.status_code))
        return payload

    async def enviar_texto(self, to_e164: str, texto: str) -> dict[str, Any]:
        """Mensaje de texto libre — solo válido DENTRO de la ventana de 24h
        desde el último mensaje del destinatario (ver docstring del módulo);
        fuera de esa ventana, Meta responde el código `131047`."""
        return await self._post_message(
            {
                "messaging_product": "whatsapp",
                "to": normalizar_to(to_e164),
                "type": "text",
                "text": {"body": texto},
            }
        )

    async def enviar_plantilla(
        self,
        to_e164: str,
        nombre_plantilla: str,
        codigo_idioma: str = "es",
        componentes: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Mensaje de plantilla pre-aprobada — la única forma de iniciar (o
        continuar fuera de la ventana de 24h) una conversación de negocio en
        WhatsApp. `nombre_plantilla`/`codigo_idioma`/`componentes` deben
        corresponder EXACTAMENTE a una plantilla ya aprobada por Meta en el
        Business Manager del tenant (`docs/mensajeria.md`) — este cliente no
        valida su existencia de antemano, se entera por el error de Meta si
        no coincide."""
        template: dict[str, Any] = {"name": nombre_plantilla, "language": {"code": codigo_idioma}}
        if componentes:
            template["components"] = componentes
        return await self._post_message(
            {
                "messaging_product": "whatsapp",
                "to": normalizar_to(to_e164),
                "type": "template",
                "template": template,
            }
        )
