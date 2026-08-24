"""`GET /v1/usage` — uso del mes vs límites del plan (ARCHITECTURE.md §10.12, §10.13)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from edecan_schemas.plans import (
    BOOL_FLAGS,
    LIMIT_MESSAGES_PER_DAY,
    LIMIT_PHONE_NUMBERS,
    LIMIT_SEATS,
    LIMIT_STORAGE_MB,
    LIMIT_VOICE_MINUTES_MONTH,
    UNLIMITED,
)
from fastapi import APIRouter, Depends

from edecan_api.deps import CurrentUser, get_current_user, get_repo, rate_limit
from edecan_api.repo import Repo

router = APIRouter(prefix="/v1/usage", tags=["usage"], dependencies=[Depends(rate_limit)])


@router.get("")
async def get_usage(
    current_user: CurrentUser = Depends(get_current_user), repo: Repo = Depends(get_repo)
) -> dict[str, Any]:
    tenant = current_user.tenant
    period_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    used_by_kind = await repo.sum_usage_by_kind_since(
        tenant_id=tenant.tenant_id, since=period_start
    )

    limits = {
        LIMIT_MESSAGES_PER_DAY: tenant.flags.get(LIMIT_MESSAGES_PER_DAY, UNLIMITED),
        LIMIT_VOICE_MINUTES_MONTH: tenant.flags.get(LIMIT_VOICE_MINUTES_MONTH, UNLIMITED),
        LIMIT_STORAGE_MB: tenant.flags.get(LIMIT_STORAGE_MB, UNLIMITED),
        LIMIT_PHONE_NUMBERS: tenant.flags.get(LIMIT_PHONE_NUMBERS, UNLIMITED),
        LIMIT_SEATS: tenant.flags.get(LIMIT_SEATS, UNLIMITED),
    }
    flags = {name: bool(tenant.flags.get(name, False)) for name in BOOL_FLAGS}

    return {
        "plan_key": tenant.plan_key,
        "period_start": period_start.date().isoformat(),
        "usage": used_by_kind,
        "limits": limits,
        "flags": flags,
    }
