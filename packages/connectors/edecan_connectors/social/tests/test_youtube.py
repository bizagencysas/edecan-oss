"""Tests offline (respx) del conector de YouTube. Sin red real — ARCHITECTURE.md §10.15."""

from __future__ import annotations

import httpx
import pytest
import respx
from edecan_schemas import TokenBundle

from edecan_connectors.base import ConnectorError
from edecan_connectors.social.youtube import (
    GOOGLE_TOKEN_URL,
    YOUTUBE_API_BASE,
    YOUTUBE_UPLOAD_URL,
    YouTubeConnector,
    channel_stats,
    upload_video,
)

_CLIENT_ID = "google-client-test"
_CLIENT_SECRET = "google-secret-test"


def test_auth_url_reutiliza_endpoints_de_google_con_pkce():
    connector = YouTubeConnector()
    url = connector.auth_url(
        "https://app.example.com/callback", state="estado-yt-1-suficiente", client_id=_CLIENT_ID
    )
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "access_type=offline" in url
    assert "prompt=consent" in url
    assert "youtube.upload" in url
    assert "code_challenge_method=S256" in url


@pytest.mark.asyncio
@respx.mock
async def test_exchange_code():
    respx.post(GOOGLE_TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "tok-yt-1",
                "refresh_token": "refresh-yt-1",
                "expires_in": 3600,
                "token_type": "bearer",
            },
        )
    )
    connector = YouTubeConnector()
    async with httpx.AsyncClient() as http:
        bundle = await connector.exchange_code(
            "code-yt",
            "https://app.example.com/callback",
            http,
            client_id=_CLIENT_ID,
            client_secret=_CLIENT_SECRET,
            code_verifier="estado-yt-1-suficiente",
        )
    assert bundle.access_token == "tok-yt-1"
    assert bundle.refresh_token == "refresh-yt-1"
    assert bundle.expires_at is not None


@pytest.mark.asyncio
async def test_exchange_code_sin_client_secret_lanza_connector_error():
    connector = YouTubeConnector()
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorError):
            await connector.exchange_code(
                "code-yt",
                "https://app.example.com/callback",
                http,
                client_id=_CLIENT_ID,
                client_secret=None,
            )


@pytest.mark.asyncio
async def test_refresh_sin_refresh_token_falla_con_connector_error():
    connector = YouTubeConnector()
    bundle = TokenBundle(access_token="a", refresh_token=None, scopes=[])
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorError):
            await connector.refresh(
                bundle, http, client_id=_CLIENT_ID, client_secret=_CLIENT_SECRET
            )


@pytest.mark.asyncio
async def test_refresh_sin_client_secret_lanza_connector_error():
    connector = YouTubeConnector()
    bundle = TokenBundle(access_token="a", refresh_token="refresh-yt-1", scopes=[])
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorError):
            await connector.refresh(bundle, http, client_id=_CLIENT_ID, client_secret=None)


@pytest.mark.asyncio
@respx.mock
async def test_refresh_con_refresh_token_conserva_scopes_si_google_no_los_reenvia():
    respx.post(GOOGLE_TOKEN_URL).mock(
        return_value=httpx.Response(
            200, json={"access_token": "tok-yt-nuevo", "token_type": "bearer"}
        )
    )
    connector = YouTubeConnector()
    bundle = TokenBundle(
        access_token="a", refresh_token="refresh-yt-1", scopes=connector.oauth.scopes
    )
    async with httpx.AsyncClient() as http:
        nuevo = await connector.refresh(
            bundle, http, client_id=_CLIENT_ID, client_secret=_CLIENT_SECRET
        )
    assert nuevo.access_token == "tok-yt-nuevo"
    assert nuevo.refresh_token == "refresh-yt-1"
    assert nuevo.scopes == connector.oauth.scopes


@pytest.mark.asyncio
@respx.mock
async def test_channel_stats():
    respx.get(f"{YOUTUBE_API_BASE}/channels").mock(
        return_value=httpx.Response(
            200, json={"items": [{"statistics": {"subscriberCount": "10"}}]}
        )
    )
    bundle = TokenBundle(access_token="tok-yt", scopes=[])
    async with httpx.AsyncClient() as http:
        resultado = await channel_stats(http, bundle)
    assert resultado["items"][0]["statistics"]["subscriberCount"] == "10"


@pytest.mark.asyncio
@respx.mock
async def test_upload_video_flujo_resumable_en_dos_pasos():
    upload_session_url = (
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&upload_id=xyz"
    )
    paso1 = respx.post(YOUTUBE_UPLOAD_URL).mock(
        return_value=httpx.Response(200, headers={"Location": upload_session_url})
    )
    paso2 = respx.put(upload_session_url).mock(
        return_value=httpx.Response(
            200, json={"id": "video-1", "status": {"uploadStatus": "uploaded"}}
        )
    )
    bundle = TokenBundle(access_token="tok-yt", scopes=[])
    async with httpx.AsyncClient() as http:
        resultado = await upload_video(
            http, bundle, "Título", "Descripción", b"contenido-falso-del-video"
        )
    assert paso1.called
    assert paso2.called
    assert resultado["id"] == "video-1"


@pytest.mark.asyncio
@respx.mock
async def test_upload_video_sin_location_lanza_connector_error():
    respx.post(YOUTUBE_UPLOAD_URL).mock(return_value=httpx.Response(200))
    bundle = TokenBundle(access_token="tok-yt", scopes=[])
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorError):
            await upload_video(http, bundle, "T", "D", b"x")
