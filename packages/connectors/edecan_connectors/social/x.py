"""Conector oficial de X (API v2) — OAuth 2.0 con PKCE (RFC 7636).

Cada tenant autoriza SU PROPIA app de X. Este módulo nunca persiste tokens:
recibe siempre un `TokenBundle` ya vigente. Solo usa endpoints documentados de
`twitter.com` / `api.twitter.com`. Ver `ARCHITECTURE.md` §5 y §10.8.

PKCE: igual que `GoogleConnector`/`MicrosoftConnector`, delegamos en
`edecan_connectors.base.build_authorize_url`, que deriva el `code_challenge`
(método S256) directamente de `state`. Eso exige que `state` YA sea, por
construcción de quien orquesta el flujo (`apps/api`), un valor aleatorio con
la entropía y el alfabeto de un `code_verifier` válido (p. ej.
`secrets.token_urlsafe(32)`) — y que ese mismo `state` se reenvíe, sin
modificar, como `code_verifier` a `exchange_code(...)` tras el callback.

`client_id`/`client_secret` son la app OAuth PROPIA de cada tenant (ver
docstring de `..base`), nunca variables de entorno. `client_secret` es
OPCIONAL: X admite apps "públicas" protegidas solo por PKCE, sin secreto
compartido.
"""

from __future__ import annotations

import httpx
from edecan_schemas import TokenBundle

from ..base import Connector, ConnectorError, OAuthSpec, build_authorize_url
from ._util import bearer_header, expires_at_from_seconds

TOKEN_URL = "https://api.twitter.com/2/oauth2/token"
TWEETS_URL = "https://api.twitter.com/2/tweets"
USERS_ME_URL = "https://api.twitter.com/2/users/me"

SCOPES = ["tweet.read", "tweet.write", "users.read", "offline.access"]

X_OAUTH = OAuthSpec(
    auth_url="https://twitter.com/i/oauth2/authorize",
    token_url=TOKEN_URL,
    scopes=SCOPES,
    pkce=True,
)


def _basic_auth(client_id: str, client_secret: str | None) -> httpx.BasicAuth | None:
    """X exige Basic Auth (client_id:client_secret) para apps confidenciales.

    Si el tenant no pegó un `client_secret` (app pública), se omite: la
    seguridad la da PKCE, no un secreto compartido.
    """
    if not client_secret:
        return None
    return httpx.BasicAuth(client_id, client_secret)


def _raise_for_x_error(response: httpx.Response) -> None:
    if response.status_code >= 400:
        raise ConnectorError(f"Error de la API de X ({response.status_code}): {response.text}")


class XConnector(Connector):
    """Conector de X (antes Twitter): API v2, OAuth 2.0 + PKCE."""

    key = "x"
    display_name = "X"
    oauth = X_OAUTH

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
        if not code_verifier:
            raise ConnectorError(
                "X exige PKCE: falta code_verifier (debe ser el mismo `state` "
                "usado al construir auth_url)."
            )
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
            "client_id": client_id,
        }
        resp = await http.post(
            self.oauth.token_url, data=data, auth=_basic_auth(client_id, client_secret)
        )
        _raise_for_x_error(resp)
        payload = resp.json()
        scope_str = payload.get("scope")
        return TokenBundle(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token"),
            expires_at=expires_at_from_seconds(payload.get("expires_in")),
            scopes=scope_str.split() if scope_str else self.oauth.scopes,
            token_type=payload.get("token_type", "bearer"),
        )

    async def refresh(
        self,
        bundle: TokenBundle,
        http: httpx.AsyncClient,
        client_id: str,
        client_secret: str | None,
    ) -> TokenBundle:
        if not bundle.refresh_token:
            raise ConnectorError(
                "El TokenBundle de X no tiene refresh_token; "
                "el tenant debe reautorizar (¿faltó el scope offline.access?)."
            )
        data = {
            "grant_type": "refresh_token",
            "refresh_token": bundle.refresh_token,
            "client_id": client_id,
        }
        resp = await http.post(
            self.oauth.token_url, data=data, auth=_basic_auth(client_id, client_secret)
        )
        _raise_for_x_error(resp)
        payload = resp.json()
        scope_str = payload.get("scope")
        return TokenBundle(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token", bundle.refresh_token),
            expires_at=expires_at_from_seconds(payload.get("expires_in")),
            scopes=scope_str.split() if scope_str else bundle.scopes,
            token_type=payload.get("token_type", "bearer"),
        )


async def post_tweet(http: httpx.AsyncClient, bundle: TokenBundle, text: str) -> dict:
    """Publica un tweet de texto simple. `POST /2/tweets`."""
    resp = await http.post(
        TWEETS_URL, json={"text": text}, headers=bearer_header(bundle.access_token)
    )
    _raise_for_x_error(resp)
    return resp.json()


async def get_me(http: httpx.AsyncClient, bundle: TokenBundle) -> dict:
    """Perfil básico del usuario autenticado. `GET /2/users/me`."""
    resp = await http.get(USERS_ME_URL, headers=bearer_header(bundle.access_token))
    _raise_for_x_error(resp)
    return resp.json()


async def get_mentions(
    http: httpx.AsyncClient, bundle: TokenBundle, user_id: str, max_results: int = 20
) -> dict:
    """Menciones recientes del usuario. `GET /2/users/{id}/mentions`."""
    resp = await http.get(
        f"https://api.twitter.com/2/users/{user_id}/mentions",
        headers=bearer_header(bundle.access_token),
        params={"max_results": max_results},
    )
    _raise_for_x_error(resp)
    return resp.json()
