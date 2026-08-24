"""Seed opcional de desarrollo: tenant `demo`, usuario demo y persona default.

La app local de producción no usa estas credenciales ni requiere ejecutar este
módulo: crea o recupera su único dueño mediante el flujo loopback de
`POST /v1/auth/local`. Este seed existe solo para pruebas manuales del modo
hosted/desarrollo.

Uso: `uv run python -m edecan_db.seed` (usa `DATABASE_URL` del entorno/`.env`,
requiere que la migración `0001_initial` ya esté aplicada — `make db-migrate`).

Idempotente: si el tenant `demo` ya existe, no hace nada (no reescribe el
password ni la persona en corridas repetidas).
"""

from __future__ import annotations

import asyncio
import logging

from argon2 import PasswordHasher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_db.models import Membership, Persona, Tenant, User
from edecan_db.session import get_session

logger = logging.getLogger(__name__)

DEMO_TENANT_SLUG = "demo"
DEMO_TENANT_NAME = "Demo"
DEMO_PLAN_KEY = "free_selfhost"
DEMO_USER_EMAIL = "demo@example.com"
DEMO_USER_PASSWORD = "cambiame"  # noqa: S105 - contraseña de desarrollo fija a propósito, no un secreto real


async def seed(session: AsyncSession) -> Tenant:
    """Crea tenant/usuario/membership/persona de demo si no existen.

    Devuelve el `Tenant` (existente o recién creado). La sesión debe ser una
    conectada como dueño (`edecan_db.session.get_session(None)`) porque este
    seed escribe tanto en tablas globales (`tenants`, `users`) como en tablas
    con Row-Level Security (`memberships`, `personas`).
    """
    existing = (
        await session.execute(select(Tenant).where(Tenant.slug == DEMO_TENANT_SLUG))
    ).scalar_one_or_none()
    if existing is not None:
        logger.info(
            "El tenant demo (slug=%r) ya existe (id=%s); no se vuelve a sembrar.",
            DEMO_TENANT_SLUG,
            existing.id,
        )
        return existing

    tenant = Tenant(
        name=DEMO_TENANT_NAME,
        slug=DEMO_TENANT_SLUG,
        plan_key=DEMO_PLAN_KEY,
        status="active",
    )
    session.add(tenant)
    await session.flush()  # asigna tenant.id (server_default gen_random_uuid())

    hasher = PasswordHasher()
    user = User(
        email=DEMO_USER_EMAIL,
        password_hash=hasher.hash(DEMO_USER_PASSWORD),
        is_superadmin=False,
    )
    session.add(user)
    await session.flush()  # asigna user.id

    session.add(Membership(tenant_id=tenant.id, user_id=user.id, role="owner"))
    # Persona "default" del tenant (user_id=None): el resto de columnas toma
    # los valores por defecto de la tabla, idénticos a
    # `edecan_schemas.models.PersonaConfig()` (§10.5).
    session.add(Persona(tenant_id=tenant.id, user_id=None))

    await session.flush()
    logger.info(
        "Tenant demo creado: slug=%r plan=%r, usuario=%r.",
        DEMO_TENANT_SLUG,
        DEMO_PLAN_KEY,
        DEMO_USER_EMAIL,
    )
    return tenant


async def main() -> None:
    logging.basicConfig(level="INFO")
    async with get_session(None) as session:
        await seed(session)


if __name__ == "__main__":
    asyncio.run(main())
