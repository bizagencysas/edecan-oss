"""Conector oficial de YouTube (Data API v3) — reutiliza el OAuth de Google.

YouTube no tiene su propio servidor de autorización: usa las mismas URLs de
Google OAuth 2.0, pero con scopes específicos de YouTube. Cada tenant
autoriza SU PROPIA cuenta de Google/YouTube con SU PROPIA app OAuth (ver
docstring de `..base`) — si un tenant ya registró una app de Google para el
conector `"google"`, puede pegar el MISMO `client_id`/`client_secret` acá
también (misma app de Google Cloud, distinto scope), o registrar una app
separada; este conector no asume ninguna de las dos. Este módulo nunca
persiste tokens. Solo usa endpoints documentados de `accounts.google.com` /
`googleapis.com`. Ver `ARCHITECTURE.md` §5 y §10.8.

El flujo OAuth (`auth_url`/`exchange_code`/`refresh`) reutiliza los mismos
helpers de `edecan_connectors.base` que `GoogleConnector` — es, en la
práctica, el mismo servidor de autorización con otro `OAuthSpec`.
"""

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
from ._util import bearer_header

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
YOUTUBE_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]

YOUTUBE_OAUTH = OAuthSpec(
    auth_url=GOOGLE_AUTH_URL,
    token_url=GOOGLE_TOKEN_URL,
    scopes=SCOPES,
    pkce=True,
    extra_params={"access_type": "offline", "prompt": "consent"},
)


def _raise_for_youtube_error(response: httpx.Response) -> None:
    if response.status_code >= 400:
        raise ConnectorError(
            f"Error de la API de YouTube ({response.status_code}): {response.text}"
        )


class YouTubeConnector(Connector):
    """Conector de YouTube: Data API v3 sobre el servidor OAuth de Google."""

    key = "youtube"
    display_name = "YouTube"
    oauth = YOUTUBE_OAUTH

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
                "Falta el client_secret de tu app OAuth de YouTube (Conectores → "
                "YouTube → 'Configurar app OAuth')."
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
                "El TokenBundle de YouTube no tiene refresh_token; "
                "el tenant debe reautorizar con access_type=offline + prompt=consent."
            )
        if not client_secret:
            raise ConnectorError(
                "Falta el client_secret de tu app OAuth de YouTube (Conectores → "
                "YouTube → 'Configurar app OAuth')."
            )
        data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": bundle.refresh_token,
            "grant_type": "refresh_token",
        }
        refreshed = await _post_token(http, self.oauth.token_url, data)
        # Igual que Google: al refrescar normalmente no reemite refresh_token/scope.
        return TokenBundle(
            access_token=refreshed.access_token,
            refresh_token=refreshed.refresh_token or bundle.refresh_token,
            expires_at=refreshed.expires_at,
            scopes=refreshed.scopes or bundle.scopes,
            token_type=refreshed.token_type,
        )


async def channel_stats(http: httpx.AsyncClient, bundle: TokenBundle) -> dict:
    """Estadísticas básicas (suscriptores, vistas, videos) del canal autenticado."""
    resp = await http.get(
        f"{YOUTUBE_API_BASE}/channels",
        headers=bearer_header(bundle.access_token),
        params={"part": "statistics,snippet", "mine": "true"},
    )
    _raise_for_youtube_error(resp)
    return resp.json()


async def upload_video(
    http: httpx.AsyncClient,
    bundle: TokenBundle,
    title: str,
    description: str,
    video_bytes: bytes,
    *,
    privacy_status: str = "private",
    mime_type: str = "video/*",
) -> dict:
    """Sube un video con el flujo *resumable* documentado de la Data API v3.

    Dos pasos:
    1. `POST .../upload/youtube/v3/videos?uploadType=resumable` con los
       metadatos (`snippet`, `status`) → el servidor responde sin cuerpo y con
       el header `Location`: la URL de subida de la sesión.
    2. `PUT` de los bytes del video a esa `Location`.
    """
    init_resp = await http.post(
        YOUTUBE_UPLOAD_URL,
        params={"uploadType": "resumable", "part": "snippet,status"},
        headers={
            **bearer_header(bundle.access_token),
            "X-Upload-Content-Type": mime_type,
            "X-Upload-Content-Length": str(len(video_bytes)),
        },
        json={
            "snippet": {"title": title, "description": description},
            "status": {"privacyStatus": privacy_status},
        },
    )
    _raise_for_youtube_error(init_resp)
    upload_url = init_resp.headers.get("Location")
    if not upload_url:
        raise ConnectorError("YouTube no devolvió la URL de subida resumable (header Location)")

    upload_resp = await http.put(
        upload_url,
        content=video_bytes,
        headers={
            **bearer_header(bundle.access_token),
            "Content-Type": mime_type,
        },
    )
    _raise_for_youtube_error(upload_resp)
    return upload_resp.json()
