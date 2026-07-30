"""`GET /v1/me` (ARCHITECTURE.md §10.12)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from edecan_api.deps import CurrentUser, get_current_user, get_platform_repo, rate_limit
from edecan_api.repo import Repo

router = APIRouter(prefix="/v1/me", tags=["me"], dependencies=[Depends(rate_limit)])


def _user_out(row: dict) -> dict:
    return {
        "id": row["id"],
        "email": row["email"],
        "is_superadmin": bool(row.get("is_superadmin", False)),
        "created_at": row["created_at"],
    }


def _tenant_out(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "slug": row["slug"],
        "plan_key": row["plan_key"],
        "status": row["status"],
        "created_at": row["created_at"],
    }


@router.get("")
async def get_me(
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_platform_repo),
) -> dict:
    user = await repo.get_user(current_user.user_id)
    tenant = await repo.get_tenant(current_user.tenant_id)
    if user is None or tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Usuario o tenant no encontrado."
        )

    return {
        "user": _user_out(user),
        "tenant": _tenant_out(tenant),
        "flags": current_user.tenant.flags,
    }
