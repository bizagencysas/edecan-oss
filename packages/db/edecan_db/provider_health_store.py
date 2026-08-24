"""Writer async compartido para historia operacional de ProviderHealth."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)
_VALID_STATUSES = frozenset({"success", "failure", "rate_limited"})
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_.:@/-]{1,200}$")


class ProviderHealthEventStore:
    """Cola acotada y best-effort; nunca bloquea un job o un turno LLM."""

    def __init__(
        self,
        session_factory: Callable[[None], AbstractAsyncContextManager[AsyncSession]],
        *,
        max_queue: int = 1000,
        retention_days: int = 30,
    ) -> None:
        self._session_factory = session_factory
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=max_queue)
        self._retention_days = max(1, retention_days)
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self.dropped_events = 0

    def enqueue(self, event: dict[str, Any]) -> None:
        provider = str(event.get("provider") or "")
        status = str(event.get("status") or "")
        if not _SAFE_VALUE.fullmatch(provider) or status not in _VALID_STATUSES:
            return
        try:
            latency_ms = round(max(0.0, float(event.get("latency") or 0.0)) * 1000.0, 3)
            observed_at = float(event.get("at") or 0.0)
        except (TypeError, ValueError):
            return
        if observed_at <= 0:
            return
        safe: dict[str, Any] = {
            "provider": provider,
            "status": status,
            "latency_ms": latency_ms,
            "observed_at": observed_at,
        }
        for key in ("model", "model_alias"):
            value = str(event.get(key) or "").strip()
            if value and _SAFE_VALUE.fullmatch(value):
                safe[key] = value
        try:
            self._queue.put_nowait(safe)
        except asyncio.QueueFull:
            self.dropped_events += 1

    async def start(self) -> None:
        if self._task is None:
            self._stopping = False
            self._task = asyncio.create_task(self._run(), name="provider-health-writer")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        self._stopping = True
        await task

    async def _run(self) -> None:
        while not self._queue.empty() or not self._stopping:
            try:
                first = await asyncio.wait_for(self._queue.get(), timeout=0.5)
            except TimeoutError:
                if self._stopping:
                    break
                continue
            batch = [first]
            while len(batch) < 100:
                try:
                    batch.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            await self._flush(batch)

    async def _flush(self, events: list[dict[str, Any]]) -> None:
        try:
            async with self._session_factory(None) as session:
                await session.execute(
                    text(
                        "INSERT INTO provider_health_events "
                        "(id, provider, model, model_alias, status, latency_ms, observed_at) "
                        "VALUES (:id, :provider, :model, :model_alias, :status, "
                        ":latency_ms, :observed_at)"
                    ),
                    [
                        {
                            "id": uuid.uuid4(),
                            "provider": event["provider"],
                            "model": event.get("model"),
                            "model_alias": event.get("model_alias"),
                            "status": event["status"],
                            "latency_ms": event["latency_ms"],
                            "observed_at": datetime.fromtimestamp(
                                float(event["observed_at"]), tz=UTC
                            ),
                        }
                        for event in events
                    ],
                )
                await session.execute(
                    text("DELETE FROM provider_health_events WHERE observed_at < :cutoff"),
                    {"cutoff": datetime.now(UTC) - timedelta(days=self._retention_days)},
                )
        except Exception:  # noqa: BLE001 - telemetría best-effort
            self.dropped_events += len(events)
            logger.warning("provider_health_persistence_failed", exc_info=True)

    async def recent_events(self, *, limit: int = 50) -> list[dict[str, Any]]:
        bounded = max(0, min(int(limit), 200))
        if bounded == 0:
            return []
        async with self._session_factory(None) as session:
            result = await session.execute(
                text(
                    "SELECT provider, model, model_alias, status, latency_ms, observed_at "
                    "FROM provider_health_events ORDER BY observed_at DESC LIMIT :limit"
                ),
                {"limit": bounded},
            )
            return [
                {
                    "provider": row["provider"],
                    "model": row["model"],
                    "model_alias": row["model_alias"],
                    "status": row["status"],
                    "latency_ms": float(row["latency_ms"] or 0.0),
                    "at": row["observed_at"].isoformat(),
                }
                for row in result.mappings().all()
            ]
