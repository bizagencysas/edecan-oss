"""Cliente REST de Home Assistant (`ARCHITECTURE.md` §12, WP-V3-12).

Home Assistant expone una **API REST oficial y documentada** sobre la propia
instancia del usuario (normalmente self-hosted, en su LAN) — bring-your-own
perfecto para `DIRECCION_ACTUAL.md` ("Modelo de credenciales: TODO lo trae el
cliente, siempre"): el usuario genera un **Long-Lived Access Token** en su
perfil de Home Assistant (ver `docs/casa-inteligente.md`) y este cliente solo
habla `http(s)://{base_url}/api/...` con ese token — cero scraping, cero
credencial compartida de plataforma.

## Protección SSRF deliberadamente INVERTIDA respecto a `edecan_browser.policy`

`edecan_browser.policy.check_navigation` navega URLs públicas arbitrarias que
pide el usuario/LLM, así que bloquea por defecto IPs privadas, loopback y
metadata de nube (ver su docstring) — esa es la postura correcta para
navegar la web abierta. Aquí es exactamente al revés: Home Assistant vive,
por diseño, en la red local del propio usuario
(`http://homeassistant.local:8123`, `http://192.168.1.50:8123`, etc.) — una
IP privada o un hostname `.local` (mDNS) es **el caso normal**, no una señal
de ataque, así que `_validar_base_url` NO los rechaza. Lo que sí rechaza es
lo que es peligroso o inválido en cualquier caso:

- Un esquema que no sea `http`/`https` (p. ej. `file://`, `javascript:`).
- Credenciales embebidas en la URL (`usuario:contraseña@host`) — el token va
  siempre en el header `Authorization`, nunca en la URL.

## Errores

Todos los métodos públicos lanzan `HomeAssistantError` con un mensaje en
español, accionable (nunca una excepción cruda de `httpx`) — mismo criterio
que `edecan_premium.telephony.TwilioApiError`/`edecan_messaging.clients
.MessagingClientError`. El texto del error siempre pasa por
`edecan_core.safety.redact` antes de propagarse (última red de seguridad
para que el token nunca quede en claro en un log o en la respuesta al
usuario, aunque en la práctica los errores de Home Assistant no suelen
citarlo).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
from edecan_core.safety import redact

DEFAULT_TIMEOUT_SECONDS = 15.0

_ESQUEMAS_PERMITIDOS = frozenset({"http", "https"})

# "cap de 200 entidades" (instrucción del work package): una instancia de
# Home Assistant típica puede exponer varios cientos/miles de entidades
# (cada sensor, escena, automatización... cuenta) — sin este tope, un
# `casa_dispositivos` sin filtro podría devolver una lista enorme al modelo.
MAX_ENTIDADES = 200


class HomeAssistantError(RuntimeError):
    """Error al hablar con la API REST de Home Assistant — mensaje en
    español, siempre accionable (ver docstring del módulo)."""


def _validar_base_url(base_url: str) -> str:
    """Valida `base_url` y devuelve la forma normalizada (sin `/` final).

    Lanza `HomeAssistantError` si el esquema no es http/https, si no trae
    host, o si trae credenciales embebidas — ver docstring del módulo para
    el porqué de NO bloquear IPs privadas/`.local` aquí (a propósito, es lo
    opuesto de `edecan_browser.policy`).
    """
    crudo = (base_url or "").strip()
    partes = urlsplit(crudo)
    if partes.scheme.lower() not in _ESQUEMAS_PERMITIDOS or not partes.hostname:
        raise HomeAssistantError(
            f"«{base_url}» no es una URL http/https válida para tu Home Assistant. Usa algo "
            "como 'http://homeassistant.local:8123' o 'http://192.168.1.50:8123'."
        )
    if partes.username or partes.password:
        raise HomeAssistantError(
            "La URL de Home Assistant no debe incluir credenciales embebidas "
            "(usuario:contraseña@host) — el Long-Lived Access Token va aparte, nunca en la URL."
        )
    return f"{partes.scheme}://{partes.netloc}{partes.path.rstrip('/')}"


class HomeAssistantClient:
    """Cliente REST de la instancia de Home Assistant de UN tenant.

    `base_url`/`token` son siempre bring-your-own del propio usuario (nunca
    compartidos, nunca de la plataforma). `http`, si se pasa, es el
    `httpx.AsyncClient` a usar (inyectado en tests con `respx`/
    `MockTransport`); si no, se crea y cierra uno nuevo por request (mismo
    patrón que `edecan_messaging.clients._do_request` y
    `edecan_premium.telephony.TwilioTenantClient._post`).
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        http: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = _validar_base_url(base_url)
        if not token or not token.strip():
            raise HomeAssistantError(
                "Falta el Long-Lived Access Token de Home Assistant (lo generas en tu perfil "
                "de Home Assistant → Seguridad, ver docs/casa-inteligente.md)."
            )
        self._token = token.strip()
        self._http = http
        self._timeout = timeout

    @property
    def base_url(self) -> str:
        return self._base_url

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        client = self._http
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=self._timeout)
        try:
            return await client.request(
                method, f"{self._base_url}{path}", headers=self._headers(), **kwargs
            )
        except httpx.HTTPError as exc:
            raise HomeAssistantError(
                f"No pude contactar tu Home Assistant en '{self._base_url}': {redact(str(exc))}. "
                "¿Está encendido tu Home Assistant? ¿Es alcanzable desde donde corre Edecán? "
                "¿El token sigue vigente?"
            ) from exc
        finally:
            if owns_client:
                await client.aclose()

    def _alzar_si_error(self, response: httpx.Response, *, contexto: str) -> None:
        if response.status_code == 401:
            raise HomeAssistantError(
                "Home Assistant rechazó el token (401) — ¿el Long-Lived Access Token sigue "
                "vigente? Genera uno nuevo en tu perfil de Home Assistant si lo revocaste."
            )
        if response.status_code >= 400:
            raise HomeAssistantError(
                f"Home Assistant respondió {response.status_code} {contexto}: "
                f"{redact(response.text[:300])}. ¿Está encendido tu Home Assistant?"
            )

    async def ping(self) -> bool:
        """`GET /api/` — `True` si Home Assistant responde y el token es válido.

        Lanza `HomeAssistantError` (mensaje accionable) si la red falla, el
        token es inválido (401) o Home Assistant responde con otro error.
        """
        response = await self._request("GET", "/api/")
        self._alzar_si_error(response, contexto="al comprobar la conexión (/api/)")
        return True

    async def estados(self, filtro_dominio: str | None = None) -> list[dict[str, Any]]:
        """`GET /api/states` — lista compacta de entidades (hasta `MAX_ENTIDADES`).

        Cada elemento: `{"entity_id", "state", "friendly_name"}`.
        `filtro_dominio` (p. ej. `"light"`, `"switch"`, `"climate"`) se
        aplica ANTES del tope de `MAX_ENTIDADES`, así una instancia con miles
        de entidades igual devuelve hasta 200 coincidencias del dominio
        pedido, no 200 entidades sin filtrar de las que solo unas pocas
        matchean.
        """
        response = await self._request("GET", "/api/states")
        self._alzar_si_error(response, contexto="al listar entidades (/api/states)")
        try:
            payload = response.json()
        except ValueError as exc:
            raise HomeAssistantError(
                "Home Assistant devolvió una respuesta no-JSON en /api/states."
            ) from exc
        if not isinstance(payload, list):
            raise HomeAssistantError(
                "Home Assistant devolvió un formato inesperado en /api/states."
            )

        dominio = (filtro_dominio or "").strip().lower()
        resultado: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            entity_id = str(item.get("entity_id", ""))
            if not entity_id:
                continue
            if dominio and not entity_id.startswith(f"{dominio}."):
                continue
            atributos = item.get("attributes") or {}
            resultado.append(
                {
                    "entity_id": entity_id,
                    "state": item.get("state"),
                    "friendly_name": atributos.get("friendly_name", entity_id),
                }
            )
            if len(resultado) >= MAX_ENTIDADES:
                break
        return resultado

    async def estado(self, entity_id: str) -> dict[str, Any]:
        """`GET /api/states/{entity_id}` — estado + atributos completos de UNA entidad.

        404 se traduce a un `HomeAssistantError` claro (entidad inexistente),
        distinto del resto de errores HTTP.
        """
        response = await self._request("GET", f"/api/states/{entity_id}")
        if response.status_code == 404:
            raise HomeAssistantError(
                f"No existe la entidad «{entity_id}» en tu Home Assistant. Revisa el entity_id "
                "exacto con 'casa_dispositivos'."
            )
        self._alzar_si_error(response, contexto=f"al consultar '{entity_id}'")
        try:
            return response.json()
        except ValueError as exc:
            raise HomeAssistantError(
                f"Home Assistant devolvió una respuesta no-JSON para '{entity_id}'."
            ) from exc

    async def llamar_servicio(
        self, domain: str, service: str, service_data: dict[str, Any] | None = None
    ) -> Any:
        """`POST /api/services/{domain}/{service}` — ejecuta una acción real
        (p. ej. `domain="light"`, `service="turn_on"`).

        `service_data` es el cuerpo JSON tal cual (normalmente incluye
        `entity_id`). Devuelve el JSON de respuesta de Home Assistant (lista
        de entidades cuyo estado cambió), o `None` si la respuesta viene
        vacía o no es JSON.
        """
        response = await self._request(
            "POST", f"/api/services/{domain}/{service}", json=service_data or {}
        )
        self._alzar_si_error(response, contexto=f"al ejecutar {domain}.{service}")
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return None


@dataclass(frozen=True)
class EntidadResumen:
    """Forma tipada opcional de una fila de `HomeAssistantClient.estados()` —
    las tools de `tools.py` trabajan directo con los `dict` (más simple para
    volcarlos en `ToolResult.data`), esta clase queda disponible para quien
    prefiera tipado fuerte."""

    entity_id: str
    state: str | None
    friendly_name: str
