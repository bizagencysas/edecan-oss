"""Cliente de la API oficial **AfterShip Tracking API v4** (`ARCHITECTURE.md` §14,
WP-V5-09).

`AfterShipClient` habla con `https://api.aftership.com/v4` usando el `api_key` **del
propio tenant** (header `as-api-key`) — Edecán nunca opera una cuenta de AfterShip
propia. `rastrear(tracking_number, courier_slug=None)` es de solo lectura: si no se
indica `courier_slug`, primero llama a `POST /couriers/detect` para identificar la
empresa de envío a partir del número de guía (documentado en
https://www.aftership.com/docs/tracking — "Detect courier" — antes de poder consultar
`GET /trackings/{slug}/{tracking_number}`, que exige el `slug` del courier).

Nunca crea, actualiza ni borra ningún tracking en la cuenta de AfterShip del tenant —
esas operaciones de escritura (`POST /trackings`, etc.) están deliberadamente fuera de
alcance de este módulo: Edecán solo consulta, nunca administra la cuenta de rastreo del
tenant en su nombre.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

AFTERSHIP_BASE_URL = "https://api.aftership.com/v4"
DEFAULT_TIMEOUT_SECONDS = 20.0
_API_KEY_HEADER = "as-api-key"


class TrackingError(RuntimeError):
    """Error al hablar con AfterShip — mensaje ya legible (extraído de `meta.message`
    de la respuesta cuando está disponible). Nunca incluye el `api_key` en el mensaje."""


@dataclass(frozen=True)
class CheckpointRastreo:
    fecha: str | None
    mensaje: str
    lugar: str | None


@dataclass(frozen=True)
class RastreoPaquete:
    estado: str
    courier: str | None
    checkpoints: list[CheckpointRastreo]
    entrega_estimada: str | None = None


def _extraer_error_aftership(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return f"AfterShip respondió {response.status_code}: {response.text[:300]}"
    meta = data.get("meta") if isinstance(data, dict) else None
    if isinstance(meta, dict) and meta.get("message"):
        codigo = meta.get("code")
        sufijo = f" (código {codigo})" if codigo is not None else ""
        return f"{meta['message']}{sufijo}"
    return f"AfterShip respondió {response.status_code}: {response.text[:300]}"


def _lugar_checkpoint(cp: dict[str, Any]) -> str | None:
    partes = [p for p in (cp.get("city"), cp.get("state"), cp.get("country_name")) if p]
    if partes:
        return ", ".join(dict.fromkeys(partes))  # sin duplicados, preserva el orden
    return cp.get("location")


def _parse_checkpoint(cp: dict[str, Any]) -> CheckpointRastreo:
    return CheckpointRastreo(
        fecha=cp.get("checkpoint_time") or cp.get("created_at"),
        mensaje=str(cp.get("message") or "(sin mensaje)"),
        lugar=_lugar_checkpoint(cp),
    )


def _parse_rastreo(tracking: dict[str, Any], *, slug_usado: str) -> RastreoPaquete:
    checkpoints_raw = tracking.get("checkpoints") or []
    checkpoints = [_parse_checkpoint(cp) for cp in checkpoints_raw if isinstance(cp, dict)]
    return RastreoPaquete(
        estado=str(tracking.get("tag") or "Unknown"),
        courier=tracking.get("slug") or slug_usado,
        checkpoints=checkpoints,
        entrega_estimada=tracking.get("expected_delivery"),
    )


class AfterShipClient:
    """Cliente de solo lectura de la Tracking API de AfterShip. Ver el docstring del
    módulo para el flujo de detección automática de courier."""

    name = "aftership"

    def __init__(
        self,
        api_key: str,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = api_key
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            base_url=AFTERSHIP_BASE_URL, timeout=timeout
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        return {_API_KEY_HEADER: self._api_key, "Content-Type": "application/json"}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(
                method, path, params=params, json=json_body, headers=self._headers()
            )
        except httpx.HTTPError as exc:
            raise TrackingError(f"No se pudo conectar con AfterShip: {exc}") from exc
        if response.status_code >= 400:
            raise TrackingError(_extraer_error_aftership(response))
        try:
            return response.json()
        except ValueError as exc:
            raise TrackingError("AfterShip devolvió una respuesta no-JSON inesperada.") from exc

    async def listar_couriers(self) -> list[dict[str, Any]]:
        """`GET /couriers` — lista los couriers activados en la cuenta. Ping barato de
        validación: no crea/consulta ningún tracking, solo confirma que el `api_key`
        sirve (`apps/api/edecan_api/routers/viajes.py::PUT /rastreo/credentials`)."""
        data = await self._request("GET", "/couriers")
        contenido = data.get("data") if isinstance(data.get("data"), dict) else {}
        return list(contenido.get("couriers") or [])

    async def detectar_courier(self, tracking_number: str) -> str | None:
        """`POST /couriers/detect` — identifica la empresa de envío a partir del número
        de guía. Devuelve `None` si AfterShip no pudo detectar ninguna."""
        data = await self._request(
            "POST", "/couriers/detect", json_body={"tracking": {"tracking_number": tracking_number}}
        )
        contenido = data.get("data") if isinstance(data.get("data"), dict) else {}
        couriers = contenido.get("couriers") or []
        if not couriers or not isinstance(couriers[0], dict):
            return None
        slug = couriers[0].get("slug")
        return str(slug) if slug else None

    async def rastrear(
        self, tracking_number: str, courier_slug: str | None = None
    ) -> RastreoPaquete:
        """`GET /trackings/{slug}/{tracking_number}` — estado + checkpoints, solo
        lectura. Si falta `courier_slug`, lo detecta primero (ver `detectar_courier`)."""
        slug = (courier_slug or "").strip() or await self.detectar_courier(tracking_number)
        if not slug:
            raise TrackingError(
                f"No pude identificar la empresa de envío del número '{tracking_number}'. "
                "Indica el 'courier_slug' manualmente (p. ej. 'dhl', 'fedex', 'ups')."
            )
        data = await self._request("GET", f"/trackings/{slug}/{tracking_number}")
        contenido = data.get("data") if isinstance(data.get("data"), dict) else {}
        tracking = contenido.get("tracking") if isinstance(contenido.get("tracking"), dict) else {}
        return _parse_rastreo(tracking, slug_usado=slug)


def rastreo_a_dict(rastreo: RastreoPaquete) -> dict[str, Any]:
    return asdict(rastreo)
