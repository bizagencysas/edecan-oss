"""Conector oficial de Meta — Páginas de Facebook e Instagram Business (Graph API).

Cada tenant autoriza SU PROPIA app de Meta vía OAuth 2.0 (sin PKCE, flujo de
servidor clásico). Este módulo nunca persiste tokens: recibe siempre un
`TokenBundle` (o un `page_access_token` derivado de él) ya vigente y lo usa
para llamar a la Graph API oficial. Ver `ARCHITECTURE.md` §5 y §10.8.

Solo se usan endpoints documentados de `graph.facebook.com` / `www.facebook.com`.
No hay scraping ni endpoints no oficiales.

Nota: a diferencia de Google/Microsoft/X, Meta NO sigue aquí los helpers
genéricos `build_authorize_url`/`_post_token` de `..base`: (1) el diálogo de
Facebook exige el `scope` separado por COMAS (no por espacios, como exige el
resto de proveedores OAuth2 estándar); y (2) el intercambio/renovación de
token de Graph API son `GET` con parámetros de query, no `POST` con cuerpo
form-encoded. Sí se reutiliza `ConnectorError` para mantener el mismo estilo
de errores que el resto del paquete. `client_id`/`client_secret` son la app
OAuth PROPIA de cada tenant (ver docstring de `..base`), nunca vienen de
variables de entorno.
"""

from __future__ import annotations

from urllib.parse import urlencode

import httpx
from edecan_schemas import TokenBundle

from ..base import Connector, ConnectorError, OAuthSpec
from ._util import bearer_header, expires_at_from_seconds

GRAPH_API_VERSION = "v21.0"
GRAPH_BASE_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
AUTH_DIALOG_URL = f"https://www.facebook.com/{GRAPH_API_VERSION}/dialog/oauth"
TOKEN_URL = f"{GRAPH_BASE_URL}/oauth/access_token"

SCOPES = [
    "pages_manage_posts",
    "pages_read_engagement",
    "pages_show_list",
    "instagram_basic",
    "instagram_content_publish",
]

META_OAUTH = OAuthSpec(
    auth_url=AUTH_DIALOG_URL,
    token_url=TOKEN_URL,
    scopes=SCOPES,
    pkce=False,
)


def _raise_for_meta_error(response: httpx.Response) -> None:
    """Traduce un error de Graph API (`{"error": {"message": ...}}`) a `ConnectorError`."""
    if response.status_code < 400:
        return
    try:
        detail = response.json().get("error", {}).get("message", response.text)
    except ValueError:
        detail = response.text
    raise ConnectorError(f"Error de Graph API ({response.status_code}): {detail}")


class MetaConnector(Connector):
    """Conector de Meta: Páginas de Facebook + cuentas de Instagram Business asociadas."""

    key = "meta"
    display_name = "Meta (Facebook Pages e Instagram)"
    oauth = META_OAUTH

    def auth_url(self, redirect_uri: str, state: str, client_id: str) -> str:
        """Construye la URL del diálogo de autorización de Meta.

        `scope` va separado por comas: es lo que exige el diálogo de OAuth de
        Facebook (a diferencia del resto de proveedores, que usan espacios).
        """
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": ",".join(self.oauth.scopes),
            "response_type": "code",
        }
        return f"{self.oauth.auth_url}?{urlencode(params)}"

    async def exchange_code(
        self,
        code: str,
        redirect_uri: str,
        http: httpx.AsyncClient,
        client_id: str,
        client_secret: str | None,
        code_verifier: str | None = None,
    ) -> TokenBundle:
        """Canjea el `code` del callback por un access token de usuario."""
        if not client_secret:
            raise ConnectorError(
                "Falta el client_secret de tu app OAuth de Meta (Conectores → "
                "Meta → 'Configurar app OAuth')."
            )
        resp = await http.get(
            self.oauth.token_url,
            params={
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "code": code,
            },
        )
        _raise_for_meta_error(resp)
        payload = resp.json()
        return TokenBundle(
            access_token=payload["access_token"],
            refresh_token=None,
            expires_at=expires_at_from_seconds(payload.get("expires_in")),
            scopes=self.oauth.scopes,
            token_type=payload.get("token_type", "bearer"),
        )

    async def refresh(
        self,
        bundle: TokenBundle,
        http: httpx.AsyncClient,
        client_id: str,
        client_secret: str | None,
    ) -> TokenBundle:
        """Renueva el token de usuario.

        Meta no usa `refresh_token`: el token vigente (de corta o larga
        duración) se canjea por uno nuevo de larga duración (~60 días) con
        `grant_type=fb_exchange_token` contra el mismo `token_url`.
        """
        if not client_secret:
            raise ConnectorError(
                "Falta el client_secret de tu app OAuth de Meta (Conectores → "
                "Meta → 'Configurar app OAuth')."
            )
        resp = await http.get(
            self.oauth.token_url,
            params={
                "grant_type": "fb_exchange_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "fb_exchange_token": bundle.access_token,
            },
        )
        _raise_for_meta_error(resp)
        payload = resp.json()
        return TokenBundle(
            access_token=payload["access_token"],
            refresh_token=None,
            expires_at=expires_at_from_seconds(payload.get("expires_in")),
            scopes=bundle.scopes,
            token_type=payload.get("token_type", "bearer"),
        )


async def list_pages(http: httpx.AsyncClient, bundle: TokenBundle) -> list[dict]:
    """Lista las Páginas de Facebook administradas por el usuario autenticado.

    Cada elemento incluye su propio `access_token` de página (necesario para
    `publish_page_post`, `get_ig_user`, `publish_ig_photo` y `get_page_insights`).
    """
    resp = await http.get(
        f"{GRAPH_BASE_URL}/me/accounts",
        headers=bearer_header(bundle.access_token),
        params={"fields": "id,name,access_token,category"},
    )
    _raise_for_meta_error(resp)
    return resp.json().get("data", [])


async def publish_page_post(
    http: httpx.AsyncClient, page_id: str, page_access_token: str, message: str
) -> dict:
    """Publica un post de texto en el muro de una Página de Facebook."""
    resp = await http.post(
        f"{GRAPH_BASE_URL}/{page_id}/feed",
        headers=bearer_header(page_access_token),
        data={"message": message},
    )
    _raise_for_meta_error(resp)
    return resp.json()


async def get_ig_user(http: httpx.AsyncClient, page_id: str, page_access_token: str) -> str | None:
    """Devuelve el ID de la cuenta de Instagram Business conectada a la Página, si existe."""
    resp = await http.get(
        f"{GRAPH_BASE_URL}/{page_id}",
        headers=bearer_header(page_access_token),
        params={"fields": "instagram_business_account"},
    )
    _raise_for_meta_error(resp)
    cuenta = resp.json().get("instagram_business_account")
    return cuenta.get("id") if cuenta else None


async def publish_ig_photo(
    http: httpx.AsyncClient,
    ig_user_id: str,
    page_access_token: str,
    image_url: str,
    caption: str,
) -> dict:
    """Publica una foto en Instagram Business en dos pasos (Graph API oficial):

    1. `POST /{ig_user_id}/media` crea el contenedor y devuelve un `creation_id`.
    2. `POST /{ig_user_id}/media_publish` publica ese contenedor.
    """
    crear = await http.post(
        f"{GRAPH_BASE_URL}/{ig_user_id}/media",
        headers=bearer_header(page_access_token),
        data={"image_url": image_url, "caption": caption},
    )
    _raise_for_meta_error(crear)
    creation_id = crear.json()["id"]

    publicar = await http.post(
        f"{GRAPH_BASE_URL}/{ig_user_id}/media_publish",
        headers=bearer_header(page_access_token),
        data={"creation_id": creation_id},
    )
    _raise_for_meta_error(publicar)
    return publicar.json()


async def get_page_insights(
    http: httpx.AsyncClient,
    page_id: str,
    page_access_token: str,
    metrics: str = "page_impressions,page_engaged_users",
) -> dict:
    """Métricas básicas de la Página (insights oficiales de Graph API)."""
    resp = await http.get(
        f"{GRAPH_BASE_URL}/{page_id}/insights",
        headers=bearer_header(page_access_token),
        params={"metric": metrics},
    )
    _raise_for_meta_error(resp)
    return resp.json()
