"""Utilidades internas compartidas por los conectores sociales.

Uso exclusivo dentro de `edecan_connectors.social` — no forma parte del
contrato público de `edecan_connectors` (ver `ARCHITECTURE.md` §10.8). La
lectura de variables de entorno obligatorias vive en `edecan_connectors.base`
(`_require_env`) y se reutiliza directamente desde ahí para mantener un único
tipo de error (`ConnectorError`) en todo el paquete.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def bearer_header(access_token: str) -> dict[str, str]:
    """Header estándar de autorización Bearer (RFC 6750)."""
    return {"Authorization": f"Bearer {access_token}"}


def expires_at_from_seconds(expires_in: int | str | None) -> datetime | None:
    """Convierte un `expires_in` (segundos desde ahora, típico de OAuth2) en un
    `datetime` absoluto y consciente de zona horaria (UTC) para `TokenBundle.expires_at`.

    Solo lo usa `meta.py`: Graph API no pasa por el helper genérico
    `edecan_connectors.base._post_token` (que ya calcula esto mismo para el
    resto de proveedores), porque el intercambio/renovación de token de Meta
    es `GET`, no `POST` form-encoded.

    Devuelve `None` si el proveedor no informó `expires_in` (p. ej. algunos
    tokens de Meta de larga duración) o si el valor no es numérico.
    """
    if expires_in is None:
        return None
    try:
        seconds = int(expires_in)
    except (TypeError, ValueError):
        return None
    return datetime.now(UTC) + timedelta(seconds=seconds)
