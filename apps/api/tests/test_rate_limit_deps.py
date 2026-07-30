"""Aislamiento de los buckets de tráfico general y del IDE interactivo."""

from __future__ import annotations

import uuid

import pytest
from api_fakes import FakeRedis
from fastapi import HTTPException

from edecan_api.deps import (
    IDE_RATE_LIMIT_MAX_REQUESTS,
    RATE_LIMIT_MAX_REQUESTS,
    CurrentUser,
    TenantCtx,
    ide_rate_limit,
    rate_limit,
)


def _user() -> CurrentUser:
    return CurrentUser(
        user_id=uuid.uuid4(),
        tenant=TenantCtx(tenant_id=uuid.uuid4(), plan_key="local"),
    )


@pytest.mark.asyncio
async def test_ide_polling_no_consume_el_limite_general() -> None:
    redis = FakeRedis()
    user = _user()

    for _ in range(RATE_LIMIT_MAX_REQUESTS):
        await rate_limit(current_user=user, redis_client=redis)

    # El canal interactivo sigue disponible aunque la API general agotó su
    # bucket. Sin esta separación, un terminal activo bloqueaba todo Edecán.
    await ide_rate_limit(current_user=user, redis_client=redis)

    with pytest.raises(HTTPException) as exc:
        await rate_limit(current_user=user, redis_client=redis)
    assert exc.value.status_code == 429


@pytest.mark.asyncio
async def test_ide_conserva_un_limite_propio() -> None:
    redis = FakeRedis()
    user = _user()

    for _ in range(IDE_RATE_LIMIT_MAX_REQUESTS):
        await ide_rate_limit(current_user=user, redis_client=redis)

    with pytest.raises(HTTPException) as exc:
        await ide_rate_limit(current_user=user, redis_client=redis)
    assert exc.value.status_code == 429
    assert str(IDE_RATE_LIMIT_MAX_REQUESTS) in str(exc.value.detail)
