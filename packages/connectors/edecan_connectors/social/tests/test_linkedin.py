"""Pruebas offline del conector oficial de LinkedIn."""

from __future__ import annotations

from urllib.parse import quote

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
    verify_post,
)


def _post_url(post_id: str) -> str:
    """Misma construcción que `verify_post`: `GET {POSTS_URL}/{urn url-encoded}`."""
    return f"{POSTS_URL}/{quote(post_id, safe='')}"


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


def test_version_usa_default_fijo_no_el_mes_actual(monkeypatch):
    """El bug de hoy: `linkedin_version` derivaba del reloj y se rompía sola cada
    1° de mes. Ahora es un default fijo salvo `LINKEDIN_API_VERSION` explícito."""
    monkeypatch.delenv("LINKEDIN_API_VERSION", raising=False)
    assert linkedin_version() == "202606"


def test_version_respeta_override_explicito(monkeypatch):
    monkeypatch.setenv("LINKEDIN_API_VERSION", "202512")
    assert linkedin_version() == "202512"


def test_version_rechaza_formato_invalido(monkeypatch):
    monkeypatch.setenv("LINKEDIN_API_VERSION", "no-es-una-version")
    with pytest.raises(ConnectorError, match="YYYYMM"):
        linkedin_version()


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
    # `create_post` relee el post antes de confirmar (ver docstring de `verify_post`):
    # sin este mock la relectura no tendría con qué responder y el test fallaría por
    # una razón ajena a lo que prueba.
    respx.get(_post_url("urn:li:share:1")).mock(
        return_value=httpx.Response(200, json={"id": "urn:li:share:1"})
    )
    async with httpx.AsyncClient() as http:
        result = await create_post(
            http,
            TokenBundle(access_token="token", scopes=[]),
            text="Una idea útil.",
        )
    assert result["id"] == "urn:li:share:1"
    assert result["verified"] == "confirmed"
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
    # LinkedIn procesa la imagen DESPUÉS del PUT: primero responde `WAITING_UPLOAD` y solo
    # después `AVAILABLE`. Adjuntarla en ese hueco hace que el post se acepte con 201 y
    # nunca aparezca (bug real del 31-jul-2026, ver `_esperar_imagen_disponible`). Aquí se
    # simulan los dos estados en orden para probar que se espera de verdad.
    estado = respx.get(f"{IMAGES_URL}/urn%3Ali%3Aimage%3Aone").mock(
        side_effect=[
            httpx.Response(200, json={"status": "WAITING_UPLOAD"}),
            httpx.Response(200, json={"status": "AVAILABLE"}),
        ]
    )
    post = respx.post(POSTS_URL).mock(
        return_value=httpx.Response(201, headers={"x-restli-id": "urn:li:share:2"})
    )
    respx.get(_post_url("urn:li:share:2")).mock(
        return_value=httpx.Response(200, json={"id": "urn:li:share:2"})
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
    # Se consultó el estado hasta que estuvo lista: NO se adjuntó con `WAITING_UPLOAD`.
    assert estado.call_count == 2
    assert result["verified"] == "confirmed"
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


# ---------------------------------------------------------------------------
# `verify_post` / relectura post-publicación (defecto 1a: LinkedIn puede
# devolver 2xx + un `x-restli-id` con forma válida sin haber creado nada --
# pasó de verdad con un token de organización sin el scope correcto).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_verify_post_confirmado_cuando_get_devuelve_200():
    respx.get(_post_url("urn:li:share:9")).mock(
        return_value=httpx.Response(200, json={"id": "urn:li:share:9"})
    )
    async with httpx.AsyncClient() as http:
        estado, detalle = await verify_post(
            http, TokenBundle(access_token="token", scopes=[]), "urn:li:share:9"
        )
    assert estado == "confirmed"
    assert detalle == ""


@pytest.mark.asyncio
@respx.mock
async def test_verify_post_no_existe_cuando_get_devuelve_404():
    respx.get(_post_url("urn:li:share:9")).mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient() as http:
        estado, detalle = await verify_post(
            http, TokenBundle(access_token="token", scopes=[]), "urn:li:share:9"
        )
    assert estado == "not_found"
    assert detalle


@pytest.mark.asyncio
@respx.mock
async def test_verify_post_desconocido_cuando_falta_permiso_de_lectura():
    """El caso que de verdad pasó: 403 ACCESS_DENIED con un token que sí publicó, solo
    que sin scope de lectura. No es "no existe" -- es "no lo sé"."""
    respx.get(_post_url("urn:li:share:9")).mock(
        return_value=httpx.Response(403, json={"message": "ACCESS_DENIED"})
    )
    async with httpx.AsyncClient() as http:
        estado, detalle = await verify_post(
            http, TokenBundle(access_token="token", scopes=[]), "urn:li:share:9"
        )
    assert estado == "unknown"
    assert detalle


@pytest.mark.asyncio
@respx.mock
async def test_verify_post_desconocido_cuando_falla_la_red():
    respx.get(_post_url("urn:li:share:9")).mock(side_effect=httpx.ConnectError("sin red"))
    async with httpx.AsyncClient() as http:
        estado, detalle = await verify_post(
            http, TokenBundle(access_token="token", scopes=[]), "urn:li:share:9"
        )
    assert estado == "unknown"
    assert detalle


@pytest.mark.asyncio
@respx.mock
async def test_crea_post_levanta_error_si_la_relectura_confirma_que_no_existe():
    """El caso real de hoy: LinkedIn acepta el POST (201 + urn) pero no creó nada.
    `create_post` ya NO reporta esto como éxito -- levanta `ConnectorError`."""
    respx.get(USERINFO_URL).mock(return_value=httpx.Response(200, json={"sub": "abc123"}))
    respx.post(POSTS_URL).mock(
        return_value=httpx.Response(201, headers={"x-restli-id": "urn:li:share:404"})
    )
    respx.get(_post_url("urn:li:share:404")).mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorError, match="no se publicó nada de verdad"):
            await create_post(
                http,
                TokenBundle(access_token="token", scopes=[]),
                text="Un post que nunca se creó de verdad.",
            )


@pytest.mark.asyncio
@respx.mock
async def test_crea_post_devuelve_unknown_si_no_se_puede_releer():
    """Sin permiso de lectura, `create_post` NO revienta ni miente: devuelve
    `verified="unknown"` con el detalle, para que quien llame nunca lo muestre
    como un ✅ llano."""
    respx.get(USERINFO_URL).mock(return_value=httpx.Response(200, json={"sub": "abc123"}))
    respx.post(POSTS_URL).mock(
        return_value=httpx.Response(201, headers={"x-restli-id": "urn:li:share:403"})
    )
    respx.get(_post_url("urn:li:share:403")).mock(
        return_value=httpx.Response(403, json={"message": "ACCESS_DENIED"})
    )
    async with httpx.AsyncClient() as http:
        result = await create_post(
            http,
            TokenBundle(access_token="token", scopes=[]),
            text="Post publicado pero no releíble.",
        )
    assert result["id"] == "urn:li:share:403"
    assert result["verified"] == "unknown"
    assert result["verification_note"]


@pytest.mark.asyncio
@respx.mock
async def test_crea_post_levanta_error_si_linkedin_no_manda_el_id():
    """Sin `x-restli-id` no hay nada que releer -- tratar esto como éxito sería
    exactamente el fallo silencioso que toda esta verificación existe para evitar."""
    respx.get(USERINFO_URL).mock(return_value=httpx.Response(200, json={"sub": "abc123"}))
    respx.post(POSTS_URL).mock(return_value=httpx.Response(201))
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorError, match="identificador del post"):
            await create_post(
                http,
                TokenBundle(access_token="token", scopes=[]),
                text="Post sin id de respuesta.",
            )
