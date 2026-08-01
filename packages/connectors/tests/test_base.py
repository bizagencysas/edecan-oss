"""Tests de `edecan_connectors.base`: `OAuthSpec`, `build_authorize_url` (PKCE
incluido) y `_post_token`. No importan `edecan_schemas` (ver `conftest.py`).
"""

from __future__ import annotations

import httpx
import pytest
import respx
from edecan_connectors.base import (
    ConnectorError,
    OAuthSpec,
    _pkce_challenge,
    _post_token,
    build_authorize_url,
)

TOKEN_URL = "https://provider.example.com/oauth/token"


def test_pkce_challenge_matches_rfc7636_test_vector() -> None:
    # RFC 7636 Apéndice B.
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert _pkce_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_build_authorize_url_contains_scopes_and_state() -> None:
    oauth = OAuthSpec(
        auth_url="https://provider.example.com/authorize",
        token_url=TOKEN_URL,
        scopes=["scope.one", "scope.two"],
        pkce=False,
    )
    url = build_authorize_url(oauth, "client-abc", "https://app.example.com/cb", "state-123")
    assert url.startswith("https://provider.example.com/authorize?")
    assert "client_id=client-abc" in url
    assert "state=state-123" in url
    assert "scope=scope.one+scope.two" in url
    assert "code_challenge" not in url


def test_build_authorize_url_with_pkce_and_extra_params() -> None:
    oauth = OAuthSpec(
        auth_url="https://provider.example.com/authorize",
        token_url=TOKEN_URL,
        scopes=["scope.one"],
        pkce=True,
        extra_params={"prompt": "consent"},
    )
    url = build_authorize_url(oauth, "client-abc", "https://app.example.com/cb", "state-xyz")
    assert f"code_challenge={_pkce_challenge('state-xyz')}" in url
    assert "code_challenge_method=S256" in url
    assert "prompt=consent" in url


@respx.mock
async def test_post_token_parses_success_response() -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "acc-1",
                "refresh_token": "ref-1",
                "expires_in": 3600,
                "scope": "a b c",
                "token_type": "Bearer",
            },
        )
    )
    async with httpx.AsyncClient() as client:
        bundle = await _post_token(client, TOKEN_URL, {"grant_type": "authorization_code"})

    assert bundle.access_token == "acc-1"
    assert bundle.refresh_token == "ref-1"
    assert bundle.scopes == ["a", "b", "c"]
    assert bundle.token_type == "Bearer"
    assert bundle.expires_at is not None


@respx.mock
async def test_post_token_defaults_scopes_to_empty_list_when_absent() -> None:
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "acc-1"}))
    async with httpx.AsyncClient() as client:
        bundle = await _post_token(client, TOKEN_URL, {})
    assert bundle.scopes == []
    assert bundle.refresh_token is None
    assert bundle.token_type == "bearer"


@respx.mock
async def test_post_token_raises_connector_error_on_oauth_error() -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            400, json={"error": "invalid_grant", "error_description": "Código expirado"}
        )
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(ConnectorError, match="Código expirado"):
            await _post_token(client, TOKEN_URL, {})


@respx.mock
async def test_post_token_raises_connector_error_when_access_token_missing() -> None:
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"token_type": "bearer"}))
    async with httpx.AsyncClient() as client:
        with pytest.raises(ConnectorError):
            await _post_token(client, TOKEN_URL, {})
