"""`GET /v1/admin/tenants|usage` — solo superadmin (ARCHITECTURE.md §10.12)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends

from edecan_api.deps import get_platform_repo, require_superadmin
from edecan_api.repo import Repo

router = APIRouter(prefix="/v1/admin", tags=["admin"], dependencies=[Depends(require_superadmin)])


def _tenant_out(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "slug": row["slug"],
        "plan_key": row["plan_key"],
        "status": row["status"],
        "created_at": row.get("created_at"),
    }


@router.get("/tenants")
async def list_tenants(repo: Repo = Depends(get_platform_repo)) -> list[dict[str, Any]]:
    rows = await repo.list_tenants()
    return [_tenant_out(r) for r in rows]


@router.get("/usage")
async def all_usage(
    days: int = 30, repo: Repo = Depends(get_platform_repo)
) -> list[dict[str, Any]]:
    since = datetime.now(UTC) - timedelta(days=days)
    return await repo.sum_usage_all_tenants_since(since=since)
