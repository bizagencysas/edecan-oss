"""Conector oficial de Slack — OAuth v2 de apps de Slack (`ARCHITECTURE.md` §10.8).

Cada tenant autoriza SU PROPIA app de Slack (instalada en su propio
workspace); este módulo nunca persiste tokens: recibe siempre un
`TokenBundle` ya vigente y lo usa para llamar a la Web API oficial
(`slack.com/api/...`) — quien la usa así es `edecan_messaging.clients.SlackClient`
(`packages/messaging/`), no este paquete. Ver `docs/mensajeria.md` para cómo
crear la app de Slack del tenant.

Nota: al igual que `social/meta.py`, este conector NO reutiliza los helpers
genéricos `edecan_connectors.base.build_authorize_url`/`_post_token`, por dos
motivos:

1. La Web API de Slack separa el `scope` por COMAS (no por espacios, como el
   resto de proveedores "OAuth2 estándar" de este paquete) tanto al pedir
   autorización como al devolver el `scope` concedido en la respuesta de
   token — igual que Meta.
2. Slack señala error con `{"ok": false, "error": "<código>"}` en el CUERPO
   de una respuesta que casi siempre trae HTTP 200 (no se puede confiar solo
   en el código de estado, a diferencia de Google/Microsoft/X).

Sí se reutiliza `ConnectorError` para mantener el mismo tipo de error que el
resto del paquete. `client_id`/`client_secret` son la app OAuth PROPIA de
cada tenant (ver docstring de `..base`), nunca variables de entorno.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
from edecan_schemas import TokenBundle

from ..base import Connector, ConnectorError, OAuthSpec

AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
TOKEN_URL = "https://slack.com/api/oauth.v2.access"

# Scopes mínimos de bot para enviar y leer mensajes en canales públicos
# (ROADMAP_V2.md §7.7, WP-V2-05): `chat:write` para enviar, `channels:read`
# para listar/resolver canales, `channels:history` para leer mensajes
# recientes. Cada tenant puede ampliar scopes reinstalando su propia app de
# Slack; esta plataforma nunca pide de entrada más de lo mínimo necesario.
SCOPES = ["chat:write", "channels:read", "channels:history"]

SLACK_OAUTH = OAuthSpec(
    auth_url=AUTHORIZE_URL,
    token_url=TOKEN_URL,
    scopes=SCOPES,
    pkce=False,
)


def _expires_at(expires_in: object) -> datetime | None:
    """`expires_in` (segundos desde ahora) -> `datetime` absoluto en UTC.

    La mayoría de los tokens de bot de Slack NO expiran (no hay rotación
    habilitada por defecto), así que casi siempre será `None`.
    """
    if expires_in is None:
        return None
    try:
        seconds = int(expires_in)
    except (TypeError, ValueError):
        return None
    return datetime.now(UTC) + timedelta(seconds=seconds)


def _parse_scopes(payload: dict, fallback: list[str]) -> list[str]:
    """El `scope` de la respuesta de Slack va separado por COMAS (no por
    espacios) — ver el punto 1 del docstring del módulo."""
    scope_value = payload.get("scope")
    if isinstance(scope_value, str) and scope_value.strip():
        return [s for s in scope_value.split(",") if s]
    return list(fallback)


def _raise_for_slack_error(payload: dict, response: httpx.Response) -> None:
    """Slack casi siempre responde HTTP 200 incluso en error: la Web API
    señala fallas con `{"ok": false, "error": "<código>"}` en el cuerpo, así
    que hay que revisar `ok` además del código de estado (punto 2 del
    docstring del módulo)."""
    if response.status_code >= 400 or payload.get("ok") is False:
        detail = payload.get("error") or response.text
        raise ConnectorError(f"Error de la Web API de Slack ({TOKEN_URL}): {detail}")


async def _post_oauth(http: httpx.AsyncClient, data: dict[str, str]) -> dict:
    try:
        response = await http.post(TOKEN_URL, data=data, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise ConnectorError(
            f"No se pudo contactar el endpoint de token de Slack ({TOKEN_URL}): {exc}"
        ) from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise ConnectorError(f"Respuesta no-JSON de Slack (HTTP {response.status_code}).") from exc
    if not isinstance(payload, dict):
        raise ConnectorError(
            f"Respuesta con forma inesperada de Slack (HTTP {response.status_code})."
        )

    _raise_for_slack_error(payload, response)

    if not payload.get("access_token"):
        raise ConnectorError(f"Respuesta de Slack sin access_token ({TOKEN_URL}).")
    return payload


class SlackConnector(Connector):
    """Conector de Slack: OAuth v2 de apps de Slack (token de bot)."""

    key = "slack"
    display_name = "Slack"
    oauth = SLACK_OAUTH

    def auth_url(self, redirect_uri: str, state: str, client_id: str) -> str:
        """`scope` va separado por comas (ver docstring del módulo, punto 1)."""
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": ",".join(self.oauth.scopes),
        }
        return f"{self.oauth.auth_url}?{urlencode(params)}"

    async def exchange_code(
        self,
        code: str,
        redirect_uri: str,
        http: httpx.AsyncClient,
        client_id: str,
        client_secret: str | None,
        code_verifier: str | None = None,
    ) -> TokenBundle:
        if not client_secret:
            raise ConnectorError(
                "Falta el client_secret de tu app OAuth de Slack (Conectores → "
                "Slack → 'Configurar app OAuth')."
            )
        data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        }
        payload = await _post_oauth(http, data)
        return TokenBundle(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token"),
            expires_at=_expires_at(payload.get("expires_in")),
            scopes=_parse_scopes(payload, self.oauth.scopes),
            token_type=payload.get("token_type", "bearer"),
        )

    async def refresh(
        self,
        bundle: TokenBundle,
        http: httpx.AsyncClient,
        client_id: str,
        client_secret: str | None,
    ) -> TokenBundle:
        """Renueva el token vía rotación de tokens de Slack (opt-in por app:
        https://api.slack.com/authentication/rotation).

        La mayoría de los tokens de bot de Slack NO expiran y no traen
        `refresh_token`; si el tenant no activó rotación en su app, este
        método lanza `ConnectorError` (igual que `XConnector.refresh`/
        `GoogleConnector.refresh` cuando falta `refresh_token`) — el tenant
        debe reautorizar en vez de refrescar.
        """
        if not bundle.refresh_token:
            raise ConnectorError(
                "El TokenBundle de Slack no tiene refresh_token (la rotación de tokens es "
                "opcional en Slack); el tenant debe reautorizar su app de Slack."
            )
        if not client_secret:
            raise ConnectorError(
                "Falta el client_secret de tu app OAuth de Slack (Conectores → "
                "Slack → 'Configurar app OAuth')."
            )
        data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": bundle.refresh_token,
        }
        payload = await _post_oauth(http, data)
        return TokenBundle(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token", bundle.refresh_token),
            expires_at=_expires_at(payload.get("expires_in")),
            scopes=_parse_scopes(payload, bundle.scopes),
            token_type=payload.get("token_type", "bearer"),
        )
