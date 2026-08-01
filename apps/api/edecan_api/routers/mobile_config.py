"""Server-driven configuration for mobile clients."""

from __future__ import annotations

import json
import logging
from typing import Literal

from edecan_schemas import MobileServerConfig, default_mobile_server_config
from fastapi import APIRouter, Depends, Query

from edecan_api.config import Settings, get_settings
from edecan_api.deps import CurrentUser, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/mobile/config", tags=["mobile-config"])


@router.get("", response_model=MobileServerConfig)
async def get_mobile_config(
    platform: Literal["ios", "android", "web", "desktop"] = Query(default="ios"),
    build: int = Query(default=1, ge=1),
    current_user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> MobileServerConfig:
    """Return the tenant/user-safe server-driven app configuration.

    The override is deliberately JSON-only and schema-validated. A bad override
    never breaks the app: clients receive the OSS default and the backend logs
    the misconfiguration for the operator.
    """

    del current_user, build  # Auth gates the endpoint; defaults are tenant-neutral for now.
    base = default_mobile_server_config(version=settings.MOBILE_SERVER_CONFIG_VERSION)
    if settings.MOBILE_SERVER_CONFIG_JSON is None:
        return base.model_copy(update={"platform": platform})

    try:
        raw = json.loads(settings.MOBILE_SERVER_CONFIG_JSON)
        config = MobileServerConfig.model_validate(raw)
    except Exception:  # noqa: BLE001 - config error must not brick mobile clients
        logger.warning("MOBILE_SERVER_CONFIG_JSON inválido; usando config default", exc_info=True)
        return base.model_copy(update={"platform": platform})

    if config.platform not in ("all", platform):
        return base.model_copy(update={"platform": platform})
    return config.model_copy(update={"platform": platform})
