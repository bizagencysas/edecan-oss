"""Conector oficial de LinkedIn para identidad, imágenes y publicaciones.

Cada instalación registra su propia app en LinkedIn Developer Portal y usa
OAuth 2.0. Edecán nunca incluye un client_id, client_secret ni token
compartido. El acceso self-service necesita los productos ``Sign in with
LinkedIn using OpenID Connect`` y ``Share on LinkedIn``.

La publicación usa las APIs REST actuales:

* ``/v2/userinfo`` para identificar a la persona autorizada.
* ``/rest/images?action=initializeUpload`` para subir una imagen.
* ``/rest/posts`` para crear el post.

``Linkedin-Version`` se deriva del mes actual salvo que la instalación fije
``LINKEDIN_API_VERSION=YYYYMM``. Así el OSS no queda atado para siempre a una
versión obsoleta, pero una instalación puede fijarla durante una migración.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from edecan_schemas import TokenBundle

from ..base import Connector, ConnectorError, OAuthSpec, build_authorize_url
from ._util import expires_at_from_seconds

AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
IMAGES_URL = "https://api.linkedin.com/rest/images"
POSTS_URL = "https://api.linkedin.com/rest/posts"

SCOPES = ["openid", "profile", "email", "w_member_social"]

LINKEDIN_OAUTH = OAuthSpec(
    auth_url=AUTH_URL,
    token_url=TOKEN_URL,
    scopes=SCOPES,
    pkce=False,
)

_VERSION_RE = re.compile(r"^\d{6}$")
_UPLOAD_HOSTS = frozenset({"api.linkedin.com", "www.linkedin.com", "media.licdn.com"})


def linkedin_version(now: datetime | None = None) -> str:
    configured = os.getenv("LINKEDIN_API_VERSION", "").strip()
    if configured:
        if not _VERSION_RE.fullmatch(configured):
            raise ConnectorError("LINKEDIN_API_VERSION debe tener formato YYYYMM.")
        return configured
    current = now or datetime.now(UTC)
    return current.astimezone(UTC).strftime("%Y%m")


def _headers(bundle: TokenBundle, *, json: bool = True) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {bundle.access_token}",
        "Linkedin-Version": linkedin_version(),
        "X-Restli-Protocol-Version": "2.0.0",
    }
    if json:
        headers["Content-Type"] = "application/json"
    return headers


def _provider_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:800] or f"HTTP {response.status_code}"
    if isinstance(payload, dict):
        return str(
            payload.get("message")
            or payload.get("error_description")
            or payload.get("error")
            or payload
        )[:800]
    return str(payload)[:800]


def _raise_for_linkedin_error(response: httpx.Response, operation: str) -> None:
    if response.status_code >= 400:
        raise ConnectorError(
            f"LinkedIn rechazó {operation} ({response.status_code}): "
            f"{_provider_detail(response)}"
        )


def _parse_bundle(payload: dict[str, Any]) -> TokenBundle:
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise ConnectorError("La respuesta OAuth de LinkedIn no incluyó access_token.")
    scope_value = payload.get("scope")
    return TokenBundle(
        access_token=access_token,
        refresh_token=payload.get("refresh_token"),
        expires_at=expires_at_from_seconds(payload.get("expires_in")),
        scopes=scope_value.split() if isinstance(scope_value, str) else SCOPES,
        token_type=str(payload.get("token_type") or "bearer"),
    )


class LinkedInConnector(Connector):
    key = "linkedin"
    display_name = "LinkedIn"
    oauth = LINKEDIN_OAUTH

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
        del code_verifier
        if not client_secret:
            raise ConnectorError("LinkedIn requiere el Client secret de la app.")
        response = await http.post(
            self.oauth.token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={"Accept": "application/json"},
        )
        _raise_for_linkedin_error(response, "la autorización")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ConnectorError("LinkedIn devolvió una respuesta OAuth ilegible.") from exc
        if not isinstance(payload, dict):
            raise ConnectorError("LinkedIn devolvió una respuesta OAuth inesperada.")
        return _parse_bundle(payload)

    async def refresh(
        self,
        bundle: TokenBundle,
        http: httpx.AsyncClient,
        client_id: str,
        client_secret: str | None,
    ) -> TokenBundle:
        if not bundle.refresh_token:
            raise ConnectorError(
                "Esta cuenta de LinkedIn debe autorizarse de nuevo cuando venza el token. "
                "LinkedIn solo entrega refresh tokens a programas aprobados."
            )
        if not client_secret:
            raise ConnectorError("LinkedIn requiere el Client secret de la app.")
        response = await http.post(
            self.oauth.token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": bundle.refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={"Accept": "application/json"},
        )
        _raise_for_linkedin_error(response, "la renovación")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ConnectorError("LinkedIn devolvió una renovación ilegible.") from exc
        if not isinstance(payload, dict):
            raise ConnectorError("LinkedIn devolvió una renovación inesperada.")
        refreshed = _parse_bundle(payload)
        if not refreshed.refresh_token:
            refreshed = refreshed.model_copy(
                update={"refresh_token": bundle.refresh_token}
            )
        return refreshed


async def get_me(http: httpx.AsyncClient, bundle: TokenBundle) -> dict[str, Any]:
    response = await http.get(USERINFO_URL, headers=_headers(bundle, json=False))
    _raise_for_linkedin_error(response, "la lectura del perfil")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ConnectorError("LinkedIn devolvió un perfil ilegible.") from exc
    if not isinstance(payload, dict) or not payload.get("sub"):
        raise ConnectorError("LinkedIn no devolvió el identificador del perfil.")
    return payload


def person_urn(profile: dict[str, Any]) -> str:
    identifier = str(profile.get("sub") or "").strip()
    if not identifier:
        raise ConnectorError("No se pudo identificar a la persona autorizada en LinkedIn.")
    return f"urn:li:person:{identifier}"


def _validate_upload_url(url: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in _UPLOAD_HOSTS:
        raise ConnectorError("LinkedIn devolvió una URL de carga no confiable.")
    if parsed.username or parsed.password:
        raise ConnectorError("LinkedIn devolvió una URL de carga inválida.")


async def upload_image(
    http: httpx.AsyncClient,
    bundle: TokenBundle,
    *,
    owner: str,
    content: bytes,
    content_type: str = "image/png",
) -> str:
    response = await http.post(
        f"{IMAGES_URL}?action=initializeUpload",
        headers=_headers(bundle),
        json={"initializeUploadRequest": {"owner": owner}},
    )
    _raise_for_linkedin_error(response, "la preparación de la imagen")
    try:
        value = response.json()["value"]
        upload_url = str(value["uploadUrl"])
        image_urn = str(value["image"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ConnectorError("LinkedIn no devolvió los datos para cargar la imagen.") from exc
    _validate_upload_url(upload_url)
    uploaded = await http.put(
        upload_url,
        content=content,
        headers={
            "Authorization": f"Bearer {bundle.access_token}",
            "Content-Type": content_type,
        },
    )
    _raise_for_linkedin_error(uploaded, "la carga de la imagen")
    return image_urn


async def create_post(
    http: httpx.AsyncClient,
    bundle: TokenBundle,
    *,
    text: str,
    image: bytes | None = None,
    image_content_type: str = "image/png",
    alt_text: str = "",
) -> dict[str, Any]:
    clean_text = text.strip()
    if not clean_text:
        raise ConnectorError("El post de LinkedIn no puede estar vacío.")
    profile = await get_me(http, bundle)
    author = person_urn(profile)
    content: dict[str, Any] | None = None
    if image is not None:
        image_urn = await upload_image(
            http,
            bundle,
            owner=author,
            content=image,
            content_type=image_content_type,
        )
        media: dict[str, str] = {"id": image_urn}
        clean_alt = alt_text.strip()
        if clean_alt:
            media["altText"] = clean_alt[:4086]
        content = {"media": media}

    payload: dict[str, Any] = {
        "author": author,
        "commentary": clean_text[:3000],
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    if content is not None:
        payload["content"] = content

    response = await http.post(POSTS_URL, headers=_headers(bundle), json=payload)
    _raise_for_linkedin_error(response, "la publicación")
    post_id = response.headers.get("x-restli-id")
    result: dict[str, Any] = {"id": post_id, "author": author}
    if response.content:
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            result["response"] = body
    return result
