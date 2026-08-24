"""Event bus interno para desacoplar subsistemas (§118 del Master Directive).

Permite que memory, observability, notifications y otros reaccionen a
eventos del agente sin que el agente sepa quiénes están escuchando.

Uso::

    bus = EventBus()
    bus.subscribe("tool.completed", lambda evt: log.info(...))
    await bus.publish("tool.completed", {"name": "buscar_web", "duration": 1.2})

Los handlers son async; se ejecutan concurrentemente sin orden garantizado.
Errores en un handler NO propagan al emisor ni a otros handlers.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

EventHandler = Callable[[dict[str, Any]], Awaitable[None]]

_KNOWN_EVENTS = frozenset(
    {
        "message.created",
        "message.completed",
        "tool.started",
        "tool.completed",
        "tool.failed",
        "agent.spawned",
        "agent.completed",
        "artifact.created",
        "task.completed",
        "memory.updated",
        "memory.consolidated",
        "citation.added",
        "confidence.low",
        "provider.healthy",
        "provider.unhealthy",
        "budget.exceeded",
    }
)


class EventBus:
    """Bus de eventos async pub/sub con fire-and-forget seguro."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)

    def subscribe(self, event: str, handler: EventHandler) -> None:
        self._handlers[event].append(handler)

    def unsubscribe(self, event: str, handler: EventHandler) -> None:
        handlers = self._handlers.get(event, [])
        if handler in handlers:
            handlers.remove(handler)

    async def publish(self, event: str, payload: dict[str, Any] | None = None) -> None:
        handlers = self._handlers.get(event, [])
        if not handlers:
            return
        data = payload or {}
        results = await asyncio.gather(
            *[h(data) for h in handlers], return_exceptions=True
        )
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning(
                    "EventBus handler %d for '%s' failed: %s",
                    i,
                    event,
                    result,
                )
