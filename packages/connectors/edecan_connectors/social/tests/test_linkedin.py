"""Pruebas offline del conector oficial de LinkedIn."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx
from edecan_schemas import TokenBundle

from edecan_connectors.base import ConnectorError
from edecan_connectors.social.linkedin import (
    IMAGES_URL,
    POSTS_URL,
    TOKEN_URL,
    USERINFO_URL,
    LinkedInConnector,
    create_post,
    linkedin_version,
)


def test_auth_url_usa_scopes_self_service_y_sin_pkce():
    url = LinkedInConnector().auth_url(
        "https://edecan.example/v1/connectors/linkedin/callback",
        "state-seguro",
        "client-1",
    )
    assert url.startswith("https://www.linkedin.com/oauth/v2/authorization?")
    assert "w_member_social" in url
    assert "openid" in url
    assert "code_challenge" not in url


def test_version_actual_es_determinista():
    assert linkedin_version(datetime(2026, 7, 23, tzinfo=UTC)) == "202607"


@pytest.mark.asyncio
async def test_exchange_requiere_client_secret():
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorError, match="Client secret"):
            await LinkedInConnector().exchange_code(
                "code",
                "https://edecan.example/callback",
                http,
                client_id="client",
                client_secret=None,
            )


@pytest.mark.asyncio
@respx.mock
async def test_exchange_code():
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "linkedin-token",
                "expires_in": 3600,
                "scope": "openid profile w_member_social",
            },
        )
    )
    async with httpx.AsyncClient() as http:
        bundle = await LinkedInConnector().exchange_code(
            "code-1",
            "https://edecan.example/callback",
            http,
            client_id="client",
            client_secret="secret",
            code_verifier="ignored-state",
        )
    assert bundle.access_token == "linkedin-token"
    assert bundle.expires_at is not None
    assert route.calls.last.request.content


@pytest.mark.asyncio
@respx.mock
async def test_refresh_conserva_refresh_token_si_linkedin_no_lo_rota():
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "linkedin-token-nuevo",
                "expires_in": 3600,
                "scope": "openid profile w_member_social",
            },
        )
    )
    original = TokenBundle(
        access_token="linkedin-token-viejo",
        refresh_token="refresh-estable",
    )
    async with httpx.AsyncClient() as http:
        refreshed = await LinkedInConnector().refresh(
            original,
            http,
            client_id="client",
            client_secret="secret",
        )
    assert refreshed.access_token == "linkedin-token-nuevo"
    assert refreshed.refresh_token == "refresh-estable"
    assert original.access_token == "linkedin-token-viejo"


@pytest.mark.asyncio
@respx.mock
async def test_crea_post_de_texto():
    respx.get(USERINFO_URL).mock(
        return_value=httpx.Response(200, json={"sub": "abc123", "name": "Ada"})
    )
    route = respx.post(POSTS_URL).mock(
        return_value=httpx.Response(201, headers={"x-restli-id": "urn:li:share:1"})
    )
    async with httpx.AsyncClient() as http:
        result = await create_post(
            http,
            TokenBundle(access_token="token", scopes=[]),
            text="Una idea útil.",
        )
    assert result["id"] == "urn:li:share:1"
    request = route.calls.last.request
    assert request.headers["Linkedin-Version"]
    assert b"urn:li:person:abc123" in request.content


@pytest.mark.asyncio
@respx.mock
async def test_crea_post_con_imagen_y_alt_text():
    respx.get(USERINFO_URL).mock(return_value=httpx.Response(200, json={"sub": "abc123"}))
    respx.post(f"{IMAGES_URL}?action=initializeUpload").mock(
        return_value=httpx.Response(
            200,
            json={
                "value": {
                    "uploadUrl": "https://www.linkedin.com/dms-uploads/upload/one",
                    "image": "urn:li:image:one",
                }
            },
        )
    )
    upload = respx.put("https://www.linkedin.com/dms-uploads/upload/one").mock(
        return_value=httpx.Response(201)
    )
    post = respx.post(POSTS_URL).mock(
        return_value=httpx.Response(201, headers={"x-restli-id": "urn:li:share:2"})
    )
    async with httpx.AsyncClient() as http:
        result = await create_post(
            http,
            TokenBundle(access_token="token", scopes=[]),
            text="Post visual",
            image=b"fake-png",
            alt_text="Gráfico del post",
        )
    assert result["id"] == "urn:li:share:2"
    assert upload.called
    assert b"Gr\xc3\xa1fico del post" in post.calls.last.request.content


@pytest.mark.asyncio
@respx.mock
async def test_rechaza_url_de_upload_fuera_de_linkedin():
    respx.get(USERINFO_URL).mock(return_value=httpx.Response(200, json={"sub": "abc123"}))
    respx.post(f"{IMAGES_URL}?action=initializeUpload").mock(
        return_value=httpx.Response(
            200,
            json={
                "value": {
                    "uploadUrl": "https://attacker.example/steal",
                    "image": "urn:li:image:one",
                }
            },
        )
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorError, match="no confiable"):
            await create_post(
                http,
                TokenBundle(access_token="token", scopes=[]),
                text="Post",
                image=b"fake-png",
            )
