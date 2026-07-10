"""Contrato común de conectores OAuth 2.0 (ARCHITECTURE.md §10.8).

`OAuthSpec` y `Connector` son el contrato EXACTO que consumen `apps/api` (flujo
`GET /v1/connectors/{key}/authorize` → `GET /v1/connectors/{key}/callback`) y
`packages/db` (TokenVault). Ningún conector concreto de este paquete almacena
tokens: siempre reciben un `TokenBundle` como argumento y devuelven uno nuevo.

`client_id`/`client_secret` SIEMPRE llegan como parámetros explícitos de quien
llama (`apps/api`), nunca se leen de variables de entorno del proceso (decisión
de producto, 2026-07-09 — ver `apps/api/edecan_api/oauth_app_credentials.py`):
Edecán se vende como pago único self-hosteado y NINGUNA app OAuth debe quedar
compartida entre tenants ni atada a quien vende el producto — cada tenant
registra y pega SU PROPIA app con cada proveedor. Ningún `Connector` concreto
de este paquete tiene atributos de instancia (ver `packages/connectors/tests/
test_byo_refresh.py`): los mismos objetos `CONNECTORS[...]` (singletons por
proceso, `registry.py`) siguen siendo seguros de compartir entre tenants
porque ahora NI SIQUIERA leen configuración global — todo entra por parámetro
en cada llamada.
"""

from __future__ import annotations

import base64
import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
from edecan_schemas import TokenBundle


class ConnectorError(RuntimeError):
    """Error al hablar con el proveedor OAuth: configuración faltante, respuesta
    de error del proveedor, o un cuerpo de respuesta con forma inesperada.
    """


@dataclass
class OAuthSpec:
    auth_url: str
    token_url: str
    scopes: list[str]
    pkce: bool = False
    extra_params: dict = field(default_factory=dict)


class Connector(ABC):
    key: str
    display_name: str
    oauth: OAuthSpec

    @abstractmethod
    def auth_url(self, redirect_uri: str, state: str, client_id: str) -> str:
        """Construye la URL a la que se redirige al usuario para autorizar.

        `client_id` es el de la app OAuth PROPIA del tenant (ver docstring del
        módulo) — nunca una constante ni una lectura de entorno.
        """

    @abstractmethod
    async def exchange_code(
        self,
        code: str,
        redirect_uri: str,
        http: httpx.AsyncClient,
        client_id: str,
        client_secret: str | None,
        code_verifier: str | None = None,
    ) -> TokenBundle:
        """Cambia el `code` del callback por un `TokenBundle`. No lo persiste.

        `client_secret` es `None` solo para proveedores cuyo flujo lo admite
        opcional (p. ej. X con apps públicas, protegidas por PKCE) — cada
        conector concreto valida si SU proveedor en particular lo exige.
        """

    @abstractmethod
    async def refresh(
        self,
        bundle: TokenBundle,
        http: httpx.AsyncClient,
        client_id: str,
        client_secret: str | None,
    ) -> TokenBundle:
        """Renueva `bundle` usando su `refresh_token`. No lo persiste."""


def _pkce_challenge(verifier: str) -> str:
    """Deriva el `code_challenge` método `S256` (RFC 7636) de un `code_verifier`."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def build_authorize_url(oauth: OAuthSpec, client_id: str, redirect_uri: str, state: str) -> str:
    """Helper compartido por los conectores concretos para construir su `auth_url`.

    Si `oauth.pkce` es `True`, el `code_challenge` (método `S256`) se deriva del
    propio `state`: quien llama (la capa API) debe generar `state` como un token
    aleatorio con entropía y alfabeto suficientes (p. ej. `secrets.token_urlsafe(32)`,
    válido como `code_verifier` de RFC 7636) y reenviar ese mismo valor como
    `code_verifier` al invocar `exchange_code(...)` tras el callback. Así no hace
    falta guardar el `code_verifier` por separado del `state`.
    """
    params: dict[str, str] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(oauth.scopes),
        "state": state,
    }
    if oauth.pkce:
        params["code_challenge"] = _pkce_challenge(state)
        params["code_challenge_method"] = "S256"
    params.update(oauth.extra_params)
    return f"{oauth.auth_url}?{urlencode(params)}"


async def _post_token(http: httpx.AsyncClient, url: str, data: dict[str, str]) -> TokenBundle:
    """POSTea `data` (form-encoded) a un endpoint de token OAuth y parsea la
    respuesta a un `TokenBundle`.

    Parsea `access_token`, `refresh_token` (puede venir ausente — ver nota en
    cada `Connector.refresh`), `expires_in` → `expires_at` (UTC, calculado desde
    "ahora") y `scope` (string separado por espacios → `list[str]`).

    Lanza `ConnectorError` si el proveedor responde con error, con un código
    >= 400, o con un cuerpo sin `access_token`.
    """
    try:
        response = await http.post(url, data=data, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise ConnectorError(f"No se pudo contactar el endpoint de token ({url}): {exc}") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise ConnectorError(
            f"Respuesta no-JSON del endpoint de token ({url}, HTTP {response.status_code})"
        ) from exc

    if not isinstance(payload, dict):
        raise ConnectorError(
            f"Respuesta con forma inesperada del endpoint de token "
            f"({url}, HTTP {response.status_code})"
        )

    if response.status_code >= 400 or "error" in payload:
        detail = payload.get("error_description") or payload.get("error") or response.text
        raise ConnectorError(f"Error de OAuth en {url}: {detail}")

    access_token = payload.get("access_token")
    if not access_token:
        raise ConnectorError(f"Respuesta de token sin access_token ({url})")

    expires_at: datetime | None = None
    expires_in = payload.get("expires_in")
    if expires_in is not None:
        try:
            expires_at = datetime.now(UTC) + timedelta(seconds=int(expires_in))
        except (TypeError, ValueError):
            expires_at = None

    scope_value = payload.get("scope")
    scopes = scope_value.split() if isinstance(scope_value, str) and scope_value else []

    return TokenBundle(
        access_token=access_token,
        refresh_token=payload.get("refresh_token"),
        expires_at=expires_at,
        scopes=scopes,
        token_type=payload.get("token_type", "bearer"),
    )
