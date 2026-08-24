"""Tests offline (respx) del conector de Meta. Sin red real — ARCHITECTURE.md §10.15."""

from __future__ import annotations

import httpx
import pytest
import respx
from edecan_schemas import TokenBundle

from edecan_connectors.base import ConnectorError
from edecan_connectors.social.meta import (
    GRAPH_BASE_URL,
    MetaConnector,
    get_ig_user,
    get_page_insights,
    list_pages,
    publish_ig_photo,
    publish_page_post,
)

_CLIENT_ID = "app-id-test"
_CLIENT_SECRET = "app-secret-test"


def test_auth_url_incluye_client_id_state_y_scopes_separados_por_coma():
    connector = MetaConnector()
    url = connector.auth_url(
        "https://app.example.com/callback", state="estado-123", client_id=_CLIENT_ID
    )
    assert url.startswith("https://www.facebook.com/v21.0/dialog/oauth?")
    assert "client_id=app-id-test" in url
    assert "state=estado-123" in url
    assert "pages_manage_posts%2Cpages_read_engagement" in url  # "," -> %2C


@pytest.mark.asyncio
@respx.mock
async def test_exchange_code_devuelve_token_bundle():
    respx.get(f"{GRAPH_BASE_URL}/oauth/access_token").mock(
        return_value=httpx.Response(
            200, json={"access_token": "tok-1", "expires_in": 5184000, "token_type": "bearer"}
        )
    )
    connector = MetaConnector()
    async with httpx.AsyncClient() as http:
        bundle = await connector.exchange_code(
            "code-abc",
            "https://app.example.com/callback",
            http,
            client_id=_CLIENT_ID,
            client_secret=_CLIENT_SECRET,
        )
    assert isinstance(bundle, TokenBundle)
    assert bundle.access_token == "tok-1"
    assert bundle.refresh_token is None
    assert bundle.expires_at is not None


@pytest.mark.asyncio
async def test_exchange_code_sin_client_secret_lanza_connector_error():
    connector = MetaConnector()
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorError):
            await connector.exchange_code(
                "code-abc",
                "https://app.example.com/callback",
                http,
                client_id=_CLIENT_ID,
                client_secret=None,
            )


@pytest.mark.asyncio
@respx.mock
async def test_exchange_code_propaga_error_de_graph_api_como_connector_error():
    respx.get(f"{GRAPH_BASE_URL}/oauth/access_token").mock(
        return_value=httpx.Response(
            400, json={"error": {"message": "Código inválido o expirado", "code": 100}}
        )
    )
    connector = MetaConnector()
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorError, match="Código inválido o expirado"):
            await connector.exchange_code(
                "code-malo",
                "https://app.example.com/callback",
                http,
                client_id=_CLIENT_ID,
                client_secret=_CLIENT_SECRET,
            )


@pytest.mark.asyncio
@respx.mock
async def test_refresh_usa_fb_exchange_token():
    ruta = respx.get(f"{GRAPH_BASE_URL}/oauth/access_token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok-2", "token_type": "bearer"})
    )
    connector = MetaConnector()
    bundle_previo = TokenBundle(access_token="tok-viejo", scopes=connector.oauth.scopes)
    async with httpx.AsyncClient() as http:
        nuevo = await connector.refresh(
            bundle_previo, http, client_id=_CLIENT_ID, client_secret=_CLIENT_SECRET
        )
    assert nuevo.access_token == "tok-2"
    assert ruta.calls.last.request.url.params["grant_type"] == "fb_exchange_token"


@pytest.mark.asyncio
async def test_refresh_sin_client_secret_lanza_connector_error():
    connector = MetaConnector()
    bundle_previo = TokenBundle(access_token="tok-viejo", scopes=connector.oauth.scopes)
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorError):
            await connector.refresh(
                bundle_previo, http, client_id=_CLIENT_ID, client_secret=None
            )


@pytest.mark.asyncio
@respx.mock
async def test_list_pages():
    respx.get(f"{GRAPH_BASE_URL}/me/accounts").mock(
        return_value=httpx.Response(
            200, json={"data": [{"id": "p1", "name": "Mi Página", "access_token": "page-tok"}]}
        )
    )
    bundle = TokenBundle(access_token="user-tok", scopes=[])
    async with httpx.AsyncClient() as http:
        paginas = await list_pages(http, bundle)
    assert paginas[0]["id"] == "p1"


@pytest.mark.asyncio
@respx.mock
async def test_publish_page_post():
    ruta = respx.post(f"{GRAPH_BASE_URL}/p1/feed").mock(
        return_value=httpx.Response(200, json={"id": "p1_post1"})
    )
    async with httpx.AsyncClient() as http:
        resultado = await publish_page_post(http, "p1", "page-tok", "Hola mundo")
    assert resultado["id"] == "p1_post1"
    assert ruta.calls.last.request.headers["Authorization"] == "Bearer page-tok"


@pytest.mark.asyncio
@respx.mock
async def test_get_ig_user():
    respx.get(f"{GRAPH_BASE_URL}/p1").mock(
        return_value=httpx.Response(200, json={"instagram_business_account": {"id": "ig-1"}})
    )
    async with httpx.AsyncClient() as http:
        ig_id = await get_ig_user(http, "p1", "page-tok")
    assert ig_id == "ig-1"


@pytest.mark.asyncio
@respx.mock
async def test_get_ig_user_sin_cuenta_conectada():
    respx.get(f"{GRAPH_BASE_URL}/p2").mock(return_value=httpx.Response(200, json={}))
    async with httpx.AsyncClient() as http:
        ig_id = await get_ig_user(http, "p2", "page-tok")
    assert ig_id is None


@pytest.mark.asyncio
@respx.mock
async def test_publish_ig_photo_dos_pasos():
    crear = respx.post(f"{GRAPH_BASE_URL}/ig-1/media").mock(
        return_value=httpx.Response(200, json={"id": "creation-1"})
    )
    publicar = respx.post(f"{GRAPH_BASE_URL}/ig-1/media_publish").mock(
        return_value=httpx.Response(200, json={"id": "media-final-1"})
    )
    async with httpx.AsyncClient() as http:
        resultado = await publish_ig_photo(
            http, "ig-1", "page-tok", "https://cdn.example.com/foto.jpg", "Mi caption"
        )
    assert crear.called
    assert publicar.called
    assert publicar.calls.last.request.content == b"creation_id=creation-1"
    assert resultado["id"] == "media-final-1"


@pytest.mark.asyncio
@respx.mock
async def test_publish_ig_photo_propaga_error_del_primer_paso():
    respx.post(f"{GRAPH_BASE_URL}/ig-1/media").mock(
        return_value=httpx.Response(400, json={"error": {"message": "image_url inválida"}})
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorError, match="image_url inválida"):
            await publish_ig_photo(http, "ig-1", "page-tok", "no-es-una-url", "caption")


@pytest.mark.asyncio
@respx.mock
async def test_get_page_insights():
    respx.get(f"{GRAPH_BASE_URL}/p1/insights").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    async with httpx.AsyncClient() as http:
        resultado = await get_page_insights(http, "p1", "page-tok")
    assert resultado == {"data": []}
