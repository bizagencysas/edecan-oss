"""Puente de proceso para la Mac del dueño.

El companion vive en el sidecar (API + worker en el mismo proceso). El chat
ya lo inyecta en `ToolContext.extras["companion"]`. Workers y misiones no
tienen `Request`, así que registramos aquí el mismo callable.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

CompanionCaller = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
CompanionFactory = Callable[[UUID], CompanionCaller | None]

_factory: CompanionFactory | None = None


def register_companion_factory(factory: CompanionFactory | None) -> None:
    global _factory
    _factory = factory


def companion_para(tenant_id: UUID) -> CompanionCaller | None:
    if _factory is None:
        return None
    return _factory(tenant_id)
