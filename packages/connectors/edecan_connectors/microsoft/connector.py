"""`Connector` de Microsoft (Outlook Mail + Calendar). Ver ARCHITECTURE.md §10.8."""

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

MICROSOFT_OAUTH = OAuthSpec(
    auth_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
    token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
    scopes=[
        "offline_access",
        "User.Read",
        "Mail.ReadWrite",
        "Mail.Send",
        "Calendars.ReadWrite",
    ],
    pkce=True,
)


class MicrosoftConnector(Connector):
    """OAuth 2.0 con Microsoft identity platform (endpoint `common`, admite
    cuentas laborales/escolares y personales). `client_id`/`client_secret`
    son los de la app OAuth PROPIA de cada tenant (ver `apps/api/edecan_api/
    oauth_app_credentials.py` y el docstring de `..base`) — cada tenant la
    registra en Azure AD / Microsoft Entra y la pega en Conectores; nunca
    vienen de variables de entorno del servidor.
    """

    key = "microsoft"
    display_name = "Microsoft (Outlook + Calendar)"
    oauth = MICROSOFT_OAUTH

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
                "Falta el client_secret de tu app OAuth de Microsoft (Conectores → "
                "Microsoft → 'Configurar app OAuth')."
            )
        data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "scope": " ".join(self.oauth.scopes),
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
                "el tenant debe reautorizar su cuenta de Microsoft."
            )
        if not client_secret:
            raise ConnectorError(
                "Falta el client_secret de tu app OAuth de Microsoft (Conectores → "
                "Microsoft → 'Configurar app OAuth')."
            )
        data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": bundle.refresh_token,
            "grant_type": "refresh_token",
            "scope": " ".join(self.oauth.scopes),
        }
        refreshed = await _post_token(http, self.oauth.token_url, data)
        # Microsoft reemite un refresh_token nuevo en cada refresh (rotación),
        # pero por robustez igual conservamos el anterior si llegara a faltar.
        return TokenBundle(
            access_token=refreshed.access_token,
            refresh_token=refreshed.refresh_token or bundle.refresh_token,
            expires_at=refreshed.expires_at,
            scopes=refreshed.scopes or bundle.scopes,
            token_type=refreshed.token_type,
        )
