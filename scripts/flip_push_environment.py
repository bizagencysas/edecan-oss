#!/usr/bin/env python
"""Voltea el `environment` de las credenciales APNs del tenant entre
`production` y `sandbox`, sin tocar la .p8 ni el resto de credenciales.

Por qué existe: la app instalada por cable usa `aps-environment=development`
(sandbox), pero las credenciales APNs quedaron en `production` (default), así
que Apple rechaza el token con `BadDeviceToken`. Este script solo cambia el
campo `environment` dentro del JSON cifrado en el vault.

Uso (desde la raíz del repo):
    .venv/bin/python scripts/flip_push_environment.py sandbox
    .venv/bin/python scripts/flip_push_environment.py production
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from edecan_db.models import ConnectorAccount
from edecan_db.vault import LocalKeyProvider, TokenVault
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

SOCKET_DIR_RAW = os.environ.get("EDECAN_LOCAL_PG_SOCKET_DIR", "").strip()
SECRETS_PATH_RAW = os.environ.get("EDECAN_LOCAL_SECRETS_PATH", "").strip()
SOCKET_DIR = Path(SOCKET_DIR_RAW) if SOCKET_DIR_RAW else None
SECRETS_PATH = Path(SECRETS_PATH_RAW) if SECRETS_PATH_RAW else None


def _master_key() -> str:
    if SECRETS_PATH is None or not SECRETS_PATH.is_file():
        raise RuntimeError("Configura EDECAN_LOCAL_SECRETS_PATH con el archivo local de secretos")
    return json.loads(SECRETS_PATH.read_text(encoding="utf-8"))["LOCAL_MASTER_KEY"]


async def main(target: str) -> int:
    if target not in ("sandbox", "production"):
        print(f"Uso: {sys.argv[0]} sandbox|production", file=sys.stderr)
        return 2
    if SOCKET_DIR is None:
        print("Configura EDECAN_LOCAL_PG_SOCKET_DIR.", file=sys.stderr)
        return 2

    engine = create_async_engine(
        f"postgresql+asyncpg://postgres@/postgres?host={SOCKET_DIR}",
        echo=False,
    )
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        vault = TokenVault(session, LocalKeyProvider(_master_key()))

        account = (
            (
                await session.execute(
                    select(ConnectorAccount).where(ConnectorAccount.connector_key == "push")
                )
            )
            .scalars()
            .first()
        )
        if account is None:
            print("No hay cuenta conectora 'push'.", file=sys.stderr)
            return 1

        bundle = await vault.get(account.tenant_id, account.id)
        if bundle is None:
            print("No hay credenciales APNs guardadas en el vault.", file=sys.stderr)
            return 1

        data = json.loads(bundle.access_token)
        actual = data.get("environment", "production")
        if actual == target:
            print(f"environment ya está en '{target}'; no hice cambios.")
            return 0

        data["environment"] = target
        bundle = bundle.model_copy(update={"access_token": json.dumps(data)})
        await vault.put(account.tenant_id, account.id, bundle)
        await session.commit()

        print(f"environment APNs cambiado: {actual} -> {target}")

    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "")))
