"""`Connector` de Google (Gmail + Calendar). Ver ARCHITECTURE.md §10.8."""

from __future__ import annotations

import httpx
from edecan_schemas import TokenBundle

from ..base import (
    Connector,
    ConnectorError,
    OAuthSpec,
    _post_token,
    build_authorize_url,
)

GOOGLE_OAUTH = OAuthSpec(
    auth_url="https://accounts.google.com/o/oauth2/v2/auth",
    token_url="https://oauth2.googleapis.com/token",
    scopes=[
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.compose",
        "https://www.googleapis.com/auth/calendar.events",
        # Import de contactos (`routers/contacts.py::import_contacts_google`,
        # People API) — un tenant que ya había conectado Google ANTES de
        # sumar este scope necesita reautorizar una vez (`prompt=consent`
        # de arriba ya fuerza que Google se lo vuelva a pedir).
        "https://www.googleapis.com/auth/contacts.readonly",
    ],
    pkce=True,
    extra_params={"access_type": "offline", "prompt": "consent"},
)


class GoogleConnector(Connector):
    """OAuth 2.0 con Google. `client_id`/`client_secret` son los de la app
    OAuth PROPIA de cada tenant (ver `apps/api/edecan_api/
    oauth_app_credentials.py` y el docstring de `..base`) — cada tenant la
    registra en Google Cloud Console y la pega en Conectores; nunca vienen de
    variables de entorno del servidor.
    """

    key = "google"
    display_name = "Google (Gmail + Calendar)"
    oauth = GOOGLE_OAUTH

    def auth_url(self, redirect_uri: str, state: str, client_id: str) -> str:
        return build_authorize_url(self.oauth, client_id, redirect_uri, state)

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
                "Falta el client_secret de tu app OAuth de Google (Conectores → "
                "Google → 'Configurar app OAuth')."
            )
        data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
        if code_verifier:
            data["code_verifier"] = code_verifier
        return await _post_token(http, self.oauth.token_url, data)

    async def refresh(
        self,
        bundle: TokenBundle,
        http: httpx.AsyncClient,
        client_id: str,
        client_secret: str | None,
    ) -> TokenBundle:
        if not bundle.refresh_token:
            raise ConnectorError(
                "El TokenBundle no trae refresh_token; "
                "el tenant debe reautorizar su cuenta de Google."
            )
        if not client_secret:
            raise ConnectorError(
                "Falta el client_secret de tu app OAuth de Google (Conectores → "
                "Google → 'Configurar app OAuth')."
            )
        data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": bundle.refresh_token,
            "grant_type": "refresh_token",
        }
        refreshed = await _post_token(http, self.oauth.token_url, data)
        # Google normalmente NO reemite refresh_token/scope al refrescar: se
        # conservan los del bundle anterior si el proveedor no manda nuevos.
        return TokenBundle(
            access_token=refreshed.access_token,
            refresh_token=refreshed.refresh_token or bundle.refresh_token,
            expires_at=refreshed.expires_at,
            scopes=refreshed.scopes or bundle.scopes,
            token_type=refreshed.token_type,
        )
