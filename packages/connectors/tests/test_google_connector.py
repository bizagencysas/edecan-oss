"""Tests de `edecan_connectors.google.connector.GoogleConnector`."""

from __future__ import annotations

from urllib.parse import parse_qsl

import httpx
import pytest
import respx
from edecan_connectors.base import ConnectorError, _pkce_challenge
from edecan_connectors.google.connector import GOOGLE_OAUTH, GoogleConnector

_CLIENT_ID = "google-client-id"
_CLIENT_SECRET = "google-client-secret"


def test_key_and_scopes() -> None:
    connector = GoogleConnector()
    assert connector.key == "google"
    assert "https://www.googleapis.com/auth/gmail.send" in GOOGLE_OAUTH.scopes
    assert "https://www.googleapis.com/auth/calendar.events" in GOOGLE_OAUTH.scopes
    assert GOOGLE_OAUTH.pkce is True


def test_auth_url_contains_scopes_and_pkce_challenge() -> None:
    connector = GoogleConnector()
    url = connector.auth_url(
        "https://app.example.com/v1/connectors/google/callback", "state-1", client_id=_CLIENT_ID
    )

    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "client_id=google-client-id" in url
    assert "gmail.readonly" in url
    assert "calendar.events" in url
    assert f"code_challenge={_pkce_challenge('state-1')}" in url
    assert "code_challenge_method=S256" in url
    assert "access_type=offline" in url
    assert "prompt=consent" in url


@respx.mock
async def test_exchange_code_sends_code_verifier_and_parses_bundle() -> None:
    route = respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "acc-1",
                "refresh_token": "ref-1",
                "expires_in": 3600,
                "scope": "gmail.readonly gmail.send",
                "token_type": "Bearer",
            },
        )
    )
    async with httpx.AsyncClient() as client:
        bundle = await GoogleConnector().exchange_code(
            "auth-code",
            "https://app.example.com/cb",
            client,
            client_id=_CLIENT_ID,
            client_secret=_CLIENT_SECRET,
            code_verifier="verifier-abc",
        )

    assert bundle.access_token == "acc-1"
    assert bundle.refresh_token == "ref-1"
    sent_params = dict(parse_qsl(route.calls.last.request.content.decode()))
    assert sent_params["grant_type"] == "authorization_code"
    assert sent_params["code"] == "auth-code"
    assert sent_params["code_verifier"] == "verifier-abc"
    assert sent_params["client_id"] == "google-client-id"
    assert sent_params["client_secret"] == "google-client-secret"


async def test_exchange_code_without_client_secret_raises() -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(ConnectorError):
            await GoogleConnector().exchange_code(
                "auth-code",
                "https://app.example.com/cb",
                client,
                client_id=_CLIENT_ID,
                client_secret=None,
            )


@respx.mock
async def test_refresh_preserves_refresh_token_when_provider_omits_it(token_bundle) -> None:
    respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=httpx.Response(200, json={"access_token": "new-acc", "expires_in": 3600})
    )
    async with httpx.AsyncClient() as client:
        refreshed = await GoogleConnector().refresh(
            token_bundle, client, client_id=_CLIENT_ID, client_secret=_CLIENT_SECRET
        )

    assert refreshed.access_token == "new-acc"
    assert refreshed.refresh_token == token_bundle.refresh_token
    assert refreshed.scopes == token_bundle.scopes


async def test_refresh_without_refresh_token_raises(token_bundle) -> None:
    token_bundle.refresh_token = None
    async with httpx.AsyncClient() as client:
        with pytest.raises(ConnectorError):
            await GoogleConnector().refresh(
                token_bundle, client, client_id=_CLIENT_ID, client_secret=_CLIENT_SECRET
            )


async def test_refresh_without_client_secret_raises(token_bundle) -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(ConnectorError):
            await GoogleConnector().refresh(
                token_bundle, client, client_id=_CLIENT_ID, client_secret=None
            )
