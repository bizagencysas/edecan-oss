"""Frontera de seguridad para las capacidades IDE avanzadas.

Un JWT normal sirve para el IDE web legacy, pero no basta para abrir una
terminal, lanzar un agente o mutar Git en la computadora de una persona. Las
rutas avanzadas exigen además la credencial durable de un móvil emparejado y
un transporte que no exponga esa credencial en una LAN.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_api.deps import CurrentUser, get_current_user, get_tenant_session

DEVICE_ID_HEADER = "X-Edecan-Device-Id"
DEVICE_TOKEN_HEADER = "X-Edecan-Device-Token"


@dataclass(frozen=True)
class PairedIDEDevice:
    device_id: uuid.UUID


def _is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    normalized = host.strip().strip("[]")
    if normalized.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _uses_secure_ide_transport(request: Request) -> bool:
    """Acepta TLS real, TLS delegado por un proxy local o loopback directo.

    ``X-Forwarded-Proto`` solo se confía cuando el peer inmediato es loopback
    (p. ej. cloudflared local). Un cliente de la LAN no puede convertir HTTP
    plano en HTTPS simplemente inventando ese header.
    """

    if request.url.scheme.lower() in {"https", "wss"}:
        return True
    peer_host = request.client.host if request.client is not None else None
    if _is_loopback_host(peer_host):
        forwarded_proto = request.headers.get("X-Forwarded-Proto", "")
        if forwarded_proto.split(",", 1)[0].strip().lower() == "https":
            return True
        # Loopback nunca abandona la computadora y se conserva para el IDE
        # web local/desarrollo.
        return True
    return False


async def require_paired_ide_device(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
    device_id_value: str | None = Header(default=None, alias=DEVICE_ID_HEADER),
    device_token: str | None = Header(default=None, alias=DEVICE_TOKEN_HEADER),
) -> PairedIDEDevice:
    """Valida transporte, identidad, tenant, dueño, estado y secreto durable."""

    if not _uses_secure_ide_transport(request):
        raise HTTPException(
            status_code=status.HTTP_426_UPGRADE_REQUIRED,
            detail=(
                "El IDE avanzado requiere HTTPS. Vuelve a conectar este teléfono "
                "mediante el enlace seguro de Edecán."
            ),
        )
    if not device_id_value or not device_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Esta función requiere un teléfono emparejado con Edecán.",
        )
    try:
        device_id = uuid.UUID(device_id_value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La identidad del dispositivo no es válida.",
        ) from exc
    if len(device_token) < 32 or len(device_token) > 512:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La credencial del dispositivo no es válida.",
        )

    result = await session.execute(
        text(
            "SELECT pairing_secret_hash FROM devices "
            "WHERE id = :device_id ::uuid AND tenant_id = :tenant_id ::uuid "
            "AND user_id = :user_id ::uuid AND kind = 'mobile' "
            "AND status = 'active' AND pairing_secret_hash IS NOT NULL"
        ),
        {
            "device_id": str(device_id),
            "tenant_id": str(current_user.tenant_id),
            "user_id": str(current_user.user_id),
        },
    )
    row: Any = result.mappings().first()
    stored_hash = str(row["pairing_secret_hash"]) if row is not None else ""
    supplied_hash = hashlib.sha256(device_token.encode("utf-8")).hexdigest()
    if not stored_hash or not hmac.compare_digest(stored_hash, supplied_hash):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Este dispositivo ya no está emparejado con Edecán.",
        )
    return PairedIDEDevice(device_id=device_id)
