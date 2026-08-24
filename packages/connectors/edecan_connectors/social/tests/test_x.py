"""Tests offline (respx) del conector de X. Sin red real — ARCHITECTURE.md §10.15."""

from __future__ import annotations

import httpx
import pytest
import respx
from edecan_schemas import TokenBundle

from edecan_connectors.base import ConnectorError, build_authorize_url
from edecan_connectors.social.x import (
    TOKEN_URL,
    TWEETS_URL,
    USERS_ME_URL,
    XConnector,
    get_me,
    get_mentions,
    post_tweet,
)

_CLIENT_ID = "x-client-test"
_CLIENT_SECRET = "x-secret-test"


def test_auth_url_delega_en_build_authorize_url_de_base():
    """`state` debe funcionar directamente como `code_verifier` (igual que
    Google/Microsoft): `auth_url` no deriva nada propio, solo delega."""
    connector = XConnector()
    state = "estado-x-1-con-suficiente-entropia"
    url = connector.auth_url("https://app.example.com/callback", state=state, client_id=_CLIENT_ID)
    esperado = build_authorize_url(
        connector.oauth, _CLIENT_ID, "https://app.example.com/callback", state
    )
    assert url == esperado
    assert url.startswith("https://twitter.com/i/oauth2/authorize?")
    assert "code_challenge_method=S256" in url
    assert "code_challenge=" in url


@pytest.mark.asyncio
async def test_exchange_code_sin_verifier_falla_con_connector_error():
    connector = XConnector()
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorError):
            await connector.exchange_code(
                "code-1",
                "https://app.example.com/callback",
                http,
                client_id=_CLIENT_ID,
                client_secret=_CLIENT_SECRET,
            )


@pytest.mark.asyncio
@respx.mock
async def test_exchange_code_usa_state_como_code_verifier():
    ruta = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "tok-x-1",
                "refresh_token": "refresh-x-1",
                "expires_in": 7200,
                "scope": "tweet.read tweet.write",
                "token_type": "bearer",
            },
        )
    )
    connector = XConnector()
    estado = "estado-x-2-suficiente-entropia-tambien"
    async with httpx.AsyncClient() as http:
        bundle = await connector.exchange_code(
            "code-2",
            "https://app.example.com/callback",
            http,
            client_id=_CLIENT_ID,
            client_secret=_CLIENT_SECRET,
            code_verifier=estado,
        )
    assert bundle.access_token == "tok-x-1"
    assert bundle.refresh_token == "refresh-x-1"
    assert bundle.scopes == ["tweet.read", "tweet.write"]
    assert bundle.expires_at is not None
    enviado = ruta.calls.last.request
    assert enviado.headers["Authorization"].startswith("Basic ")  # Basic Auth: hay client secret


@pytest.mark.asyncio
@respx.mock
async def test_exchange_code_sin_client_secret_omite_basic_auth():
    ruta = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "tok-x-pub", "token_type": "bearer"})
    )
    connector = XConnector()
    async with httpx.AsyncClient() as http:
        bundle = await connector.exchange_code(
            "code-pub",
            "https://app.example.com/callback",
            http,
            client_id=_CLIENT_ID,
            client_secret=None,
            code_verifier="estado-publico",
        )
    assert bundle.access_token == "tok-x-pub"
    assert "Authorization" not in ruta.calls.last.request.headers


@pytest.mark.asyncio
async def test_refresh_sin_refresh_token_falla_con_connector_error():
    connector = XConnector()
    bundle = TokenBundle(access_token="a", refresh_token=None, scopes=[])
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorError):
            await connector.refresh(
                bundle, http, client_id=_CLIENT_ID, client_secret=_CLIENT_SECRET
            )


@pytest.mark.asyncio
@respx.mock
async def test_refresh_con_refresh_token():
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200, json={"access_token": "tok-x-nuevo", "token_type": "bearer"}
        )
    )
    connector = XConnector()
    bundle = TokenBundle(access_token="a", refresh_token="refresh-x-1", scopes=["tweet.read"])
    async with httpx.AsyncClient() as http:
        nuevo = await connector.refresh(
            bundle, http, client_id=_CLIENT_ID, client_secret=_CLIENT_SECRET
        )
    assert nuevo.access_token == "tok-x-nuevo"
    assert nuevo.refresh_token == "refresh-x-1"


@pytest.mark.asyncio
@respx.mock
async def test_refresh_propaga_error_como_connector_error():
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(401, text="invalid_grant"))
    connector = XConnector()
    bundle = TokenBundle(access_token="a", refresh_token="refresh-vencido", scopes=[])
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorError):
            await connector.refresh(
                bundle, http, client_id=_CLIENT_ID, client_secret=_CLIENT_SECRET
            )


@pytest.mark.asyncio
@respx.mock
async def test_post_tweet():
    ruta = respx.post(TWEETS_URL).mock(
        return_value=httpx.Response(201, json={"data": {"id": "tw-1", "text": "hola"}})
    )
    bundle = TokenBundle(access_token="tok-x", scopes=[])
    async with httpx.AsyncClient() as http:
        resultado = await post_tweet(http, bundle, "hola")
    assert resultado["data"]["id"] == "tw-1"
    assert ruta.calls.last.request.headers["Authorization"] == "Bearer tok-x"


@pytest.mark.asyncio
@respx.mock
async def test_get_me():
    respx.get(USERS_ME_URL).mock(
        return_value=httpx.Response(200, json={"data": {"id": "u1", "username": "yo"}})
    )
    bundle = TokenBundle(access_token="tok-x", scopes=[])
    async with httpx.AsyncClient() as http:
        resultado = await get_me(http, bundle)
    assert resultado["data"]["username"] == "yo"


@pytest.mark.asyncio
@respx.mock
async def test_get_mentions():
    respx.get("https://api.twitter.com/2/users/u1/mentions").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    bundle = TokenBundle(access_token="tok-x", scopes=[])
    async with httpx.AsyncClient() as http:
        resultado = await get_mentions(http, bundle, "u1")
    assert resultado == {"data": []}
