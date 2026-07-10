"""Tests offline (respx) del conector de mensajería (Slack) y de su merge en
el registry (`ARCHITECTURE.md` §10.15, §10.8; WP-V2-05).

Sigue el mismo patrón que `social/tests/test_x.py`: sin red real, sin
credenciales reales — `client_id`/`client_secret` de prueba pasados por
parámetro directo (ver docstring de `edecan_connectors.base`).
"""

from __future__ import annotations

import httpx
import pytest
import respx
from edecan_connectors.base import ConnectorError
from edecan_connectors.messaging import MESSAGING_CONNECTORS, SlackConnector
from edecan_connectors.messaging.slack import SLACK_OAUTH, TOKEN_URL
from edecan_connectors.registry import CONNECTORS
from edecan_schemas import TokenBundle

_CLIENT_ID = "slack-client-test"
_CLIENT_SECRET = "slack-secret-test"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_contiene_slack():
    assert "slack" in CONNECTORS
    assert isinstance(CONNECTORS["slack"], SlackConnector)
    assert CONNECTORS["slack"].display_name == "Slack"


def test_messaging_connectors_solo_incluye_slack():
    # Telegram y Discord NO son OAuth (ver docstring de
    # `edecan_connectors.messaging`): jamás deben aparecer aquí.
    assert set(MESSAGING_CONNECTORS.keys()) == {"slack"}


# ---------------------------------------------------------------------------
# auth_url
# ---------------------------------------------------------------------------


def test_auth_url_usa_scope_separado_por_comas():
    connector = SlackConnector()
    url = connector.auth_url(
        "https://app.example.com/callback", state="estado-slack-1", client_id=_CLIENT_ID
    )
    assert url.startswith("https://slack.com/oauth/v2/authorize?")
    assert "client_id=slack-client-test" in url
    assert "state=estado-slack-1" in url
    assert "scope=chat%3Awrite%2Cchannels%3Aread%2Cchannels%3Ahistory" in url
    # Nunca separado por espacios (a diferencia de Google/Microsoft/X):
    assert "chat%3Awrite+channels%3Aread" not in url


def test_scopes_minimos_pinned():
    assert SLACK_OAUTH.scopes == ["chat:write", "channels:read", "channels:history"]
    assert SLACK_OAUTH.pkce is False


# ---------------------------------------------------------------------------
# exchange_code
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_exchange_code_ok_parsea_scope_separado_por_comas():
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "access_token": "xoxb-slack-1",
                "token_type": "bot",
                "scope": "chat:write,channels:read,channels:history",
                "bot_user_id": "U0001",
                "team": {"id": "T0001", "name": "Equipo de prueba"},
            },
        )
    )
    connector = SlackConnector()
    async with httpx.AsyncClient() as http:
        bundle = await connector.exchange_code(
            "code-1",
            "https://app.example.com/callback",
            http,
            client_id=_CLIENT_ID,
            client_secret=_CLIENT_SECRET,
        )
    assert bundle.access_token == "xoxb-slack-1"
    assert bundle.refresh_token is None
    assert bundle.scopes == ["chat:write", "channels:read", "channels:history"]
    assert bundle.expires_at is None
    assert bundle.token_type == "bot"


@pytest.mark.asyncio
async def test_exchange_code_sin_client_secret_lanza_connector_error():
    connector = SlackConnector()
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorError):
            await connector.exchange_code(
                "code-1",
                "https://app.example.com/callback",
                http,
                client_id=_CLIENT_ID,
                client_secret=None,
            )


@pytest.mark.asyncio
@respx.mock
async def test_exchange_code_http_200_con_ok_false_lanza_connector_error():
    """Slack casi siempre responde HTTP 200 incluso en error: el fallo se
    señala con `{"ok": false, "error": "..."}` en el cuerpo."""
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "invalid_code"})
    )
    connector = SlackConnector()
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorError, match="invalid_code"):
            await connector.exchange_code(
                "code-malo",
                "https://app.example.com/callback",
                http,
                client_id=_CLIENT_ID,
                client_secret=_CLIENT_SECRET,
            )


@pytest.mark.asyncio
@respx.mock
async def test_exchange_code_http_error_status_tambien_lanza_connector_error():
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(500, text="internal error"))
    connector = SlackConnector()
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
async def test_exchange_code_sin_access_token_lanza_connector_error():
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
    connector = SlackConnector()
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorError, match="access_token"):
            await connector.exchange_code(
                "code-1",
                "https://app.example.com/callback",
                http,
                client_id=_CLIENT_ID,
                client_secret=_CLIENT_SECRET,
            )


# ---------------------------------------------------------------------------
# refresh
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_sin_refresh_token_falla_con_connector_error():
    connector = SlackConnector()
    bundle = TokenBundle(access_token="xoxb-1", refresh_token=None, scopes=[])
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorError):
            await connector.refresh(
                bundle, http, client_id=_CLIENT_ID, client_secret=_CLIENT_SECRET
            )


@pytest.mark.asyncio
async def test_refresh_sin_client_secret_lanza_connector_error():
    connector = SlackConnector()
    bundle = TokenBundle(access_token="xoxb-1", refresh_token="refresh-1", scopes=[])
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorError):
            await connector.refresh(bundle, http, client_id=_CLIENT_ID, client_secret=None)


@pytest.mark.asyncio
@respx.mock
async def test_refresh_con_refresh_token_rota_el_token():
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "access_token": "xoxe.xoxb-nuevo",
                "refresh_token": "xoxe-refresh-nuevo",
                "expires_in": 43200,
                "token_type": "bot",
            },
        )
    )
    connector = SlackConnector()
    bundle = TokenBundle(
        access_token="xoxe.xoxb-viejo", refresh_token="xoxe-refresh-viejo", scopes=["chat:write"]
    )
    async with httpx.AsyncClient() as http:
        nuevo = await connector.refresh(
            bundle, http, client_id=_CLIENT_ID, client_secret=_CLIENT_SECRET
        )
    assert nuevo.access_token == "xoxe.xoxb-nuevo"
    assert nuevo.refresh_token == "xoxe-refresh-nuevo"
    assert nuevo.expires_at is not None


@pytest.mark.asyncio
@respx.mock
async def test_refresh_propaga_error_como_connector_error():
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "invalid_refresh_token"})
    )
    connector = SlackConnector()
    bundle = TokenBundle(access_token="a", refresh_token="refresh-vencido", scopes=[])
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorError):
            await connector.refresh(
                bundle, http, client_id=_CLIENT_ID, client_secret=_CLIENT_SECRET
            )


# Nota sobre el guardrail de plataforma vetada (ARCHITECTURE.md §0.2): no se
# repite aquí una comprobación local — el guardrail de esta carpeta (el otro
# archivo de `tests/` dedicado a esto, excluido a propósito de su propio
# escaneo) ya cubre TODO este árbol, este archivo incluido, y falla el build
# si esa palabra aparece en cualquier parte. Un test propio que necesitara
# nombrarla para comprobar su ausencia dispararía ese mismo escaneo (no
# distingue "código real" de "assert de prueba"), así que la cobertura
# correcta es una sola: la del escaneo global.
