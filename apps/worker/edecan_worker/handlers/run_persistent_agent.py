"""Ejecuta una tarea explícita de un worker persistente.

El job no se dispara por configuración: solo nace de una invocación humana
explícita. Reutiliza el runner headless seguro, limita tools a las declaradas
por el worker y guarda checkpoints en sesiones cortas.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from edecan_core.tools import ToolContext, ToolRegistry
from edecan_schemas import PLANES, JobEnvelope, PersonaConfig
from sqlalchemy import text

from edecan_worker.deps import Deps

logger = logging.getLogger(__name__)

DEFAULT_LEASE_SECONDS = 120.0
MAX_LEASE_SECONDS = 3600.0


def _lease_seconds(budget: Any) -> float:
    """Normaliza el lease sin permitir que un worker muerto quede huérfano horas."""
    raw = (budget or {}).get("lease_seconds", DEFAULT_LEASE_SECONDS)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return DEFAULT_LEASE_SECONDS
    return max(30.0, min(float(raw), MAX_LEASE_SECONDS))


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def _load_worker(session: Any, tenant_id: UUID, worker_id: UUID) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT id, tenant_id, user_id, name, purpose, tools, permissions, budget, "
            "status, enabled FROM persistent_agents "
            "WHERE tenant_id = :tenant_id AND id = :id"
        ),
        {"tenant_id": str(tenant_id), "id": str(worker_id)},
    )
    row = result.mappings().first()
    return dict(row) if row is not None else None


async def _save_checkpoint(
    deps: Deps,
    tenant_id: UUID,
    worker_id: UUID,
    *,
    task_id: str,
    status: str,
    detail: dict[str, Any],
) -> None:
    async with deps.session_factory(None) as session:
        await session.execute(
            text(
                "UPDATE persistent_agents SET status = :status, "
                "last_checkpoint = :checkpoint ::jsonb, "
                "updated_at = now() WHERE tenant_id = :tenant_id AND id = :id "
                "AND last_checkpoint->>'task_id' = :task_id"
            ),
            {
                "status": status,
                "checkpoint": json.dumps(detail),
                "tenant_id": str(tenant_id),
                "id": str(worker_id),
                "task_id": task_id,
            },
        )


async def _heartbeat(deps: Deps, tenant_id: UUID, worker_id: UUID, task_id: str) -> None:
    """Renueva el lease mientras el runner está vivo; un fallo no mata el trabajo."""
    while True:
        await asyncio.sleep(30.0)
        try:
            async with deps.session_factory(None) as session:
                await session.execute(
                    text(
                        "UPDATE persistent_agents SET updated_at = now() "
                        "WHERE tenant_id = :tenant_id AND id = :id AND status = 'running' "
                        "AND last_checkpoint->>'task_id' = :task_id"
                    ),
                    {"tenant_id": str(tenant_id), "id": str(worker_id), "task_id": task_id},
                )
        except Exception:  # noqa: BLE001
            logger.warning(
                "no se pudo renovar lease worker=%s task=%s",
                worker_id,
                task_id,
                exc_info=True,
            )


async def _save_handoff_status(
    deps: Deps,
    tenant_id: UUID,
    handoff_id: UUID,
    status: str,
    result: dict[str, Any] | None = None,
) -> None:
    async with deps.session_factory(None) as session:
        await session.execute(
            text(
                "UPDATE persistent_agent_handoffs SET status = :status, result = :result ::jsonb, "
                "updated_at = now() WHERE tenant_id = :tenant_id AND id = :id"
            ),
            {
                "status": status,
                "result": json.dumps(result) if result is not None else None,
                "tenant_id": str(tenant_id),
                "id": str(handoff_id),
            },
        )


async def handle(env: JobEnvelope, deps: Deps) -> None:
    if env.tenant_id is None:
        raise ValueError("run_persistent_agent requiere tenant_id")
    tenant_id = env.tenant_id
    worker_id = UUID(str(env.payload["worker_id"]))
    handoff_id_raw = env.payload.get("handoff_id")
    handoff_id = UUID(str(handoff_id_raw)) if handoff_id_raw else None
    instruction = str(env.payload.get("instruction") or "").strip()
    task_id = str(env.payload.get("task_id") or env.job_id)
    if not instruction:
        raise ValueError("run_persistent_agent requiere instruction")

    async with deps.session_factory(None) as session:
        worker = await _load_worker(session, tenant_id, worker_id)
        if (
            worker is None
            or not worker["enabled"]
            or worker["status"] in ("paused", "disabled")
        ):
            logger.info("worker persistente %s no está disponible para ejecutar", worker_id)
            return
        if handoff_id is not None:
            handoff_result = await session.execute(
                text(
                    "SELECT destination_worker_id, task_id, envelope, status "
                    "FROM persistent_agent_handoffs WHERE tenant_id = :tenant_id AND id = :id"
                ),
                {"tenant_id": str(tenant_id), "id": str(handoff_id)},
            )
            handoff = handoff_result.mappings().first()
            if (
                handoff is None
                or str(handoff["destination_worker_id"]) != str(worker_id)
                or handoff["status"] != "approved"
            ):
                logger.warning("handoff %s no está aprobado para worker=%s", handoff_id, worker_id)
                return
            envelope = handoff["envelope"]
            if isinstance(envelope, str):
                envelope = json.loads(envelope)
            instruction = str((envelope or {}).get("instruction") or "").strip()
            if not instruction:
                await _save_handoff_status(
                    deps, tenant_id, handoff_id, "error", {"error": "handoff sin instrucción"}
                )
                return
            await session.execute(
                text(
                    "UPDATE persistent_agent_handoffs SET status = 'running', updated_at = now() "
                    "WHERE tenant_id = :tenant_id AND id = :id AND status = 'approved'"
                ),
                {"tenant_id": str(tenant_id), "id": str(handoff_id)},
            )
        claim = await session.execute(
            text(
                "UPDATE persistent_agents SET status = 'running', "
                "last_checkpoint = :checkpoint ::jsonb, "
                "updated_at = now() WHERE tenant_id = :tenant_id AND id = :id "
                "AND (status = 'idle' OR (status = 'running' "
                "AND updated_at < now() - make_interval(secs => :lease_seconds)))"
            ),
            {
                "checkpoint": json.dumps(
                    {"task_id": task_id, "status": "running", "started_at": _now()}
                ),
                "tenant_id": str(tenant_id),
                "id": str(worker_id),
                "lease_seconds": _lease_seconds(worker.get("budget")),
            },
        )
        if getattr(claim, "rowcount", 1) == 0:
            logger.info("worker persistente %s ya fue reclamado por otro job", worker_id)
            return
        tenant_result = await session.execute(
            text("SELECT plan_key FROM tenants WHERE id = :id"), {"id": str(tenant_id)}
        )
        tenant = tenant_result.mappings().first()
        plan_key = tenant["plan_key"] if tenant else "free_selfhost"
        flags = dict(PLANES.get(plan_key, PLANES["free_selfhost"]).flags)

    from edecan_automations.runner import RunnerDeps, run_automation

    from edecan_worker.handlers.run_automation import _build_registry

    timeout_seconds = float((worker.get("budget") or {}).get("time", 300))
    timeout_seconds = max(1.0, min(timeout_seconds, 900.0))
    async with deps.session_factory(None) as session:
        llm_router = await deps.llm_router_for(tenant_id)
        from edecan_core.companion_access import companion_para

        companion = companion_para(tenant_id)
        full_registry = _build_registry()
        registry = ToolRegistry()
        for tool_name in worker.get("tools") or []:
            tool = full_registry.get(str(tool_name))
            if tool is None:
                continue
            if tool.dangerous and tool.name != "usar_computadora":
                continue
            if tool.name == "usar_computadora" and companion is None:
                continue
            registry.register(tool)
        if companion is not None:
            mac = full_registry.get("usar_computadora")
            if mac is not None:
                registry.register(mac)
        extras: dict[str, Any] = {
            "flags": flags,
            "approved_tool_calls": {"usar_computadora"} if companion is not None else set(),
        }
        if companion is not None:
            extras["companion"] = companion
        ctx = ToolContext(
            tenant_id=tenant_id,
            user_id=UUID(str(worker["user_id"])),
            session=session,
            settings=deps.settings,
            llm=llm_router,
            vault=deps.vault(session),
            extras=extras,
        )
        persona = PersonaConfig(
            nombre_asistente=str(worker["name"]),
            idioma="es",
            instrucciones=str(worker["purpose"]),
            memoria_activada=False,
        )
        detail: dict[str, Any] = {
            "task_id": task_id,
            "instruction_hash": hashlib.sha256(instruction.encode("utf-8")).hexdigest()[:16],
        }

        heartbeat = asyncio.create_task(_heartbeat(deps, tenant_id, worker_id, task_id))

        async def save_run(status: str, payload: dict[str, Any]) -> None:
            if handoff_id is not None:
                await _save_handoff_status(
                    deps,
                    tenant_id,
                    handoff_id,
                    "done" if status == "done" else "error" if status == "error" else "running",
                    payload,
                )
            await _save_checkpoint(
                deps,
                tenant_id,
                worker_id,
                task_id=task_id,
                status="idle" if status in ("done", "error") else "paused",
                detail={**detail, "status": status, "finished_at": _now(), "result": payload},
            )

        run_deps = RunnerDeps(
            ctx=ctx,
            llm_router=llm_router,
            registry=registry,
            persona=persona,
            flags=flags,
            save_run=save_run,
        )
        try:
            await asyncio.wait_for(
                run_automation({"accion": {"instruccion": instruction}}, run_deps),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            if handoff_id is not None:
                await _save_handoff_status(
                    deps, tenant_id, handoff_id, "error", {"error": "worker timeout"}
                )
            await _save_checkpoint(
                deps,
                tenant_id,
                worker_id,
                task_id=task_id,
                status="idle",
                detail={
                    **detail,
                    "status": "error",
                    "error": "worker timeout",
                    "finished_at": _now(),
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("run_persistent_agent falló worker=%s", worker_id)
            if handoff_id is not None:
                await _save_handoff_status(
                    deps, tenant_id, handoff_id, "error", {"error": str(exc)}
                )
            await _save_checkpoint(
                deps,
                tenant_id,
                worker_id,
                task_id=task_id,
                status="idle",
                detail={**detail, "status": "error", "error": str(exc), "finished_at": _now()},
            )
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
