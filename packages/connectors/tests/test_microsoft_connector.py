"""Tests de `edecan_connectors.microsoft.connector.MicrosoftConnector`."""

from __future__ import annotations

from urllib.parse import parse_qsl

import httpx
import pytest
import respx
from edecan_connectors.base import ConnectorError, _pkce_challenge
from edecan_connectors.microsoft.connector import MICROSOFT_OAUTH, MicrosoftConnector

TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"

_CLIENT_ID = "ms-client-id"
_CLIENT_SECRET = "ms-client-secret"


def test_key_and_scopes() -> None:
    connector = MicrosoftConnector()
    assert connector.key == "microsoft"
    assert "offline_access" in MICROSOFT_OAUTH.scopes
    assert "Mail.Send" in MICROSOFT_OAUTH.scopes
    assert "Calendars.ReadWrite" in MICROSOFT_OAUTH.scopes
    assert MICROSOFT_OAUTH.pkce is True


def test_auth_url_contains_scopes_and_pkce_challenge() -> None:
    connector = MicrosoftConnector()
    url = connector.auth_url(
        "https://app.example.com/v1/connectors/microsoft/callback", "state-1", client_id=_CLIENT_ID
    )

    assert url.startswith("https://login.microsoftonline.com/common/oauth2/v2.0/authorize?")
    assert "client_id=ms-client-id" in url
    assert "Mail.Send" in url
    assert "Calendars.ReadWrite" in url
    assert f"code_challenge={_pkce_challenge('state-1')}" in url
    assert "code_challenge_method=S256" in url


@respx.mock
async def test_exchange_code_includes_scope_and_code_verifier() -> None:
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "acc-1",
                "refresh_token": "ref-1",
                "expires_in": 3600,
                "scope": "offline_access Mail.Send",
            },
        )
    )
    async with httpx.AsyncClient() as client:
        bundle = await MicrosoftConnector().exchange_code(
            "auth-code",
            "https://app.example.com/cb",
            client,
            client_id=_CLIENT_ID,
            client_secret=_CLIENT_SECRET,
            code_verifier="verifier-abc",
        )

    assert bundle.access_token == "acc-1"
    sent_params = dict(parse_qsl(route.calls.last.request.content.decode()))
    assert sent_params["grant_type"] == "authorization_code"
    assert sent_params["code_verifier"] == "verifier-abc"
    assert sent_params["client_id"] == "ms-client-id"
    assert sent_params["client_secret"] == "ms-client-secret"
    assert "Mail.Send" in sent_params["scope"]
    assert "offline_access" in sent_params["scope"]


async def test_exchange_code_without_client_secret_raises() -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(ConnectorError):
            await MicrosoftConnector().exchange_code(
                "auth-code",
                "https://app.example.com/cb",
                client,
                client_id=_CLIENT_ID,
                client_secret=None,
            )


@respx.mock
async def test_refresh_preserves_refresh_token_when_provider_omits_it(token_bundle) -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "new-acc", "expires_in": 3600})
    )
    async with httpx.AsyncClient() as client:
        refreshed = await MicrosoftConnector().refresh(
            token_bundle, client, client_id=_CLIENT_ID, client_secret=_CLIENT_SECRET
        )

    assert refreshed.access_token == "new-acc"
    assert refreshed.refresh_token == token_bundle.refresh_token
    assert refreshed.scopes == token_bundle.scopes


async def test_refresh_without_refresh_token_raises(token_bundle) -> None:
    token_bundle.refresh_token = None
    async with httpx.AsyncClient() as client:
        with pytest.raises(ConnectorError):
            await MicrosoftConnector().refresh(
                token_bundle, client, client_id=_CLIENT_ID, client_secret=_CLIENT_SECRET
            )


async def test_refresh_without_client_secret_raises(token_bundle) -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(ConnectorError):
            await MicrosoftConnector().refresh(
                token_bundle, client, client_id=_CLIENT_ID, client_secret=None
            )
