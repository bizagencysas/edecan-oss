"""`GET /v1/admin/tenants|usage` — solo superadmin (ARCHITECTURE.md §10.12)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query

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


@router.get("/quality")
async def quality_dashboard(
    days: int = Query(default=7, ge=1, le=90), repo: Repo = Depends(get_platform_repo)
) -> dict[str, Any]:
    """Snapshot histórico seguro para el dashboard interno de calidad.

    Solo expone agregados durables y enumera las métricas que todavía no tienen
    instrumentación suficiente. No convierte la ausencia de datos en un cero.
    """
    since = datetime.now(UTC) - timedelta(days=days)
    snapshot = await repo.quality_snapshot_all_tenants_since(since=since)
    return {
        "format": "edecan-quality-dashboard.v1",
        "period_days": days,
        "period_start": since.isoformat(),
        **snapshot,
    }
