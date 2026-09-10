"""`delegar_mision` — herramienta de agente que crea una misión y la encola
para el `Orchestrator` (`ROADMAP_V2.md` §7.7, §7.9; `ARCHITECTURE.md` §10.7).

Igual que `edecan_toolkit.recordatorios.CrearRecordatorioTool`: inserta la
fila con SQL parametrizado contra el esquema pinned de `ROADMAP_V2.md` §7.4
(`edecan_schemas.missions.MissionOut` documenta la misma forma, y coincide
con el modelo `edecan_db.models.AgentMission` de la migración
`0003_v2_expansion`, dueño WP-V2-01, ya aterrizada) — deliberadamente NO
importa el ORM de `edecan_db.models`: esa forma interna no está fijada por
el contrato, mientras que los nombres de tabla/columna sí lo están (mismo
criterio que `recordatorios.py`, no una limitación temporal de este archivo).

La ejecución real de la misión (planificación + pasos + síntesis) ocurre
DESPUÉS, de forma asíncrona, en el worker
(`apps/worker/edecan_worker/handlers/run_mission.py`, job `"run_mission"` —
ya está en `edecan_schemas.JOB_TYPES`). Esta tool solo crea la fila en
`status="planning"` y encola el job: nunca importa ni llama al
`Orchestrator` directamente, para no bloquear el turno del agente principal
esperando una misión potencialmente larga.

## `limits.missions_per_day` (Hallazgo 2 de `docs/seguridad-modelo-amenazas.md`, RESUELTO)

`POST /v1/missions` (`apps/api/edecan_api/routers/missions.py::
_check_missions_quota`, WP-V6-10) y esta tool encolan el MISMO job
`run_mission` para la misma capacidad — cada uno dispara un turno completo de
agente headless en el worker (costo real de LLM), así que ambos caminos
deben respetar el mismo cupo diario, no solo el flag booleano
`agents.missions` (`requires_flags`, abajo). `_cupo_disponible` replica
exactamente el criterio de `_check_missions_quota`: lee `LIMIT_MISSIONS_PER_DAY`
de `ctx.extras["flags"]` (mismo dict de flags del tenant que
`conversations._build_ctx` ya deja ahí, ver `ToolContext.extras` en
`edecan_core.tools.base`), `-1` = ilimitado, `0` (o ausente) = sin cupo en
absoluto (fail closed, igual que el router), positivo = cuenta
`agent_missions` creadas hoy contra ese límite. A diferencia del router, que
levanta `HTTPException`, acá se devuelve un `ToolResult` explicando el cupo
agotado: esta tool nunca lanza por errores "de negocio" (ver
`Tool.run` en `edecan_core.tools.base`).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from edecan_core import Tool, ToolContext, ToolResult
from edecan_core.queue import enqueue, enqueue_outbox
from edecan_schemas.plans import LIMIT_MISSIONS_PER_DAY, UNLIMITED
from sqlalchemy import text

from .agent_bus import enviar_mensaje_agente
from .persistent_policy import MAX_HANDOFF_DEPTH, validate_handoff

logger = logging.getLogger(__name__)

DEFAULT_MAX_STEPS = 8
"""Mismo default que `orchestrator.DEFAULT_MAX_STEPS`/`ROADMAP_V2.md` §7.5
(`MISSIONS_MAX_STEPS`) — se duplica aquí como literal porque esta tool solo
necesita el número para congelarlo en `presupuesto`, no el resto del módulo
del Orchestrator."""

FLAG_AGENTS_MISSIONS = "agents.missions"

_MSG_CUPO_AGOTADO = (
    "Alcanzaste tu límite de misiones por día de tu plan. Vuelve a intentarlo "
    "mañana o mejora tu plan."
)

_MSG_CONTEXTO_BOT_INVALIDO = (
    "Esta herramienta requiere un contexto de bot válido para identificar al emisor."
)


def _worker_id_valido(raw: Any) -> str | None:
    """Normaliza un UUID de worker sin convertir ausencia en texto."""
    if raw is None:
        return None
    candidate = str(raw).strip()
    if not candidate:
        return None
    try:
        return str(UUID(candidate))
    except (AttributeError, TypeError, ValueError):
        return None


def _cadena_padre_verificada(ctx: ToolContext) -> tuple[int, list[str]] | ToolResult:
    """Lee la cadena del contexto interno; nunca de argumentos elegidos por el modelo."""
    extras = ctx.extras if isinstance(ctx.extras, dict) else {}
    depth_raw = extras.get("handoff_depth", 0)
    if isinstance(depth_raw, bool) or not isinstance(depth_raw, int):
        return ToolResult(content="El contexto verificado de la cadena de bots es inválido.")
    if depth_raw < 0 or depth_raw > MAX_HANDOFF_DEPTH:
        return ToolResult(content="El contexto verificado de la cadena de bots es inválido.")

    visited_raw = extras.get("handoff_visited", [])
    if isinstance(visited_raw, str):
        try:
            visited_raw = json.loads(visited_raw)
        except (TypeError, ValueError):
            return ToolResult(content="El contexto verificado de la cadena de bots es inválido.")
    if not isinstance(visited_raw, list):
        return ToolResult(content="El contexto verificado de la cadena de bots es inválido.")

    visited: list[str] = []
    for raw_worker_id in visited_raw:
        worker_id = _worker_id_valido(raw_worker_id)
        if worker_id is None:
            return ToolResult(content="El contexto verificado de la cadena de bots es inválido.")
        if worker_id not in visited:
            visited.append(worker_id)
    if len(visited) != depth_raw or len(visited) > MAX_HANDOFF_DEPTH:
        return ToolResult(content="El contexto verificado de la cadena de bots es inválido.")
    return depth_raw, visited


def _tenant_flags(ctx: ToolContext) -> dict[str, Any]:
    """Mismo patrón que `edecan_toolkit.contenido._tenant_flags`/
    `edecan_automations.tools._tenant_flags` (duplicado a propósito: este
    paquete no depende de ninguno de esos dos) — lee los flags de plan del
    tenant desde `ctx.extras["flags"]`, donde
    `apps.api.edecan_api.routers.conversations._build_ctx` los deja
    (`ARCHITECTURE.md` §10.7). `{}` si no están (mismo default "fail closed"
    que el resto de estos helpers duplicados)."""
    extras = ctx.extras if isinstance(ctx.extras, dict) else {}
    flags = extras.get("flags")
    return flags if isinstance(flags, dict) else {}


async def _crear_handoff(
    ctx: ToolContext,
    *,
    mission_id: Any,
    destino_worker_id: str,
    args: dict[str, Any],
) -> dict[str, Any] | ToolResult:
    """Persiste un `persistent_agent_handoffs` pendiente al delegar a otro worker.

    `source_worker_id` sale del contexto del worker que ejecuta la tool
    (`ctx.extras["worker_id"]`, lo inyecta `run_persistent_agent`); si no
    existe, la delegación entre workers no es válida y se explica en lugar
    de escribir una fila con un origen vacío (FK NOT NULL).
    """
    extras = ctx.extras if isinstance(ctx.extras, dict) else {}
    source_worker_id = _worker_id_valido(extras.get("worker_id"))
    destination_worker_id = _worker_id_valido(destino_worker_id)
    if source_worker_id is None:
        return ToolResult(
            content=(
                "Para delegar a otro worker hace falta un contexto de worker "
                "activo con un source_worker_id válido."
            )
        )
    if destination_worker_id is None:
        return ToolResult(content="El worker destino debe tener un UUID válido.")

    parent_chain = _cadena_padre_verificada(ctx)
    if isinstance(parent_chain, ToolResult):
        return parent_chain
    parent_depth, parent_visited = parent_chain
    try:
        envelope = validate_handoff(
            source_worker_id=source_worker_id,
            destination_worker_id=destination_worker_id,
            task_id=str(mission_id),
            depth=parent_depth,
            visited_worker_ids=parent_visited,
        )
    except ValueError as exc:
        return ToolResult(content=str(exc))

    if ctx.user_id:
        existe_destino = (
            await ctx.session.execute(
                text(
                    "SELECT 1 FROM persistent_agents "
                    "WHERE tenant_id = :tenant_id AND user_id = :user_id AND id = :id"
                ),
                {
                    "tenant_id": str(ctx.tenant_id),
                    "user_id": str(ctx.user_id),
                    "id": destination_worker_id,
                },
            )
        ).mappings().first()
        if existe_destino is None:
            return ToolResult(content="Ese bot no existe en tu equipo (o no es tuyo).")

    expected_output = str(args.get("expected_output") or "").strip()
    priority = str(args.get("priority") or "").strip() or "media"
    allowed_tools = args.get("allowed_tools") or []
    if not isinstance(allowed_tools, list):
        allowed_tools = []
    allowed_tools = [str(t) for t in allowed_tools]
    approval_boundary = str(args.get("approval_boundary") or "").strip()
    objetivo = str(args.get("objetivo", "")).strip()
    instruccion = objetivo
    if expected_output:
        instruccion = f"{objetivo}\n\nEntregable esperado: {expected_output}"

    envelope.update(
        {
            "goal": objetivo,
            "expected_output": expected_output or None,
            "priority": priority,
            "allowed_tools": allowed_tools,
            "approval_boundary": approval_boundary or None,
            "instruction": instruccion,
        }
    )

    fila_handoff = (
        await ctx.session.execute(
            text(
                "INSERT INTO persistent_agent_handoffs "
                "(id, tenant_id, source_worker_id, destination_worker_id, task_id, "
                "depth, visited_worker_ids, envelope) "
                "VALUES (gen_random_uuid(), :tenant_id, :source, :destination, :task_id, "
                ":depth, :visitados ::jsonb, :envelope ::jsonb) "
                "RETURNING id"
            ),
            {
                "tenant_id": str(ctx.tenant_id),
                "source": source_worker_id,
                "destination": destination_worker_id,
                "task_id": envelope["task_id"],
                "depth": envelope["depth"],
                "visitados": json.dumps(envelope["visited_worker_ids"]),
                "envelope": json.dumps(envelope, ensure_ascii=False),
            },
        )
    ).mappings().first()
    handoff_id = str(fila_handoff["id"]) if fila_handoff else None

    # Hilo VISIBLE para el dueño.
    try:
        filas_nombres = (
            await ctx.session.execute(
                text(
                    "SELECT id, COALESCE(display_name, name) AS nombre "
                    "FROM persistent_agents WHERE tenant_id = :tenant_id AND id = ANY(:ids)"
                ),
                {
                    "tenant_id": str(ctx.tenant_id),
                    "ids": [source_worker_id, destination_worker_id],
                },
            )
        ).mappings().all()
        nombres_por_id = {str(f["id"]): str(f["nombre"] or "Bot") for f in filas_nombres}
        await _asegurar_hilo_directo(
            ctx.session,
            tenant_id=str(ctx.tenant_id),
            user_id=str(ctx.user_id or ""),
            sender=source_worker_id,
            receiver=destination_worker_id,
            nombre_a=nombres_por_id.get(source_worker_id, "Bot"),
            nombre_b=nombres_por_id.get(destination_worker_id, "Bot"),
        )
    except Exception:  # noqa: BLE001 - el hilo es cosmético, no rompe el handoff
        logger.warning("delegar_mision: no se pudo asegurar el hilo directo.", exc_info=True)

    # Side-record en el protocolo inter-agente (product design): el handoff
    # entre workers también queda como mensaje HANDOFF durable, con solo
    # referencias de contexto (task_id + goal/expected_output), no el
    # transcript.
    await enviar_mensaje_agente(
        ctx.session,
        tenant_id=str(ctx.tenant_id),
        sender=source_worker_id,
        receiver=destination_worker_id,
        tipo="handoff",
        task_id=str(mission_id),
        goal=objetivo,
        expected_output=expected_output or None,
        priority=priority,
        allowed_tools=allowed_tools,
        approval_boundary=approval_boundary or None,
    )

    logger.info(
        "delegar_mision: handoff %s -> %s creado (task=%s)",
        source_worker_id,
        destination_worker_id,
        mission_id,
    )
    return {"handoff_id": handoff_id, "envelope": envelope}



class DelegarMisionTool(Tool):
    name = "delegar_mision"
    description = (
        "Crea una misión autónoma para un objetivo que requiere varios pasos "
        "encadenados de investigación, análisis de datos o generación de "
        "contenido. Un orquestador la planifica y la ejecuta en segundo "
        "plano delegando en sub-agentes especializados; el resultado queda "
        "disponible en la página Misiones cuando termina. No uses esta "
        "herramienta para preguntas simples que puedas responder tú mismo en "
        "este turno."
    )
    category = "admin"
    risk_level = "medium"
    latency_class = "background"
    input_schema = {
        "type": "object",
        "properties": {
            "objetivo": {
                "type": "string",
                "description": (
                    "Objetivo de la misión, descrito con el detalle suficiente "
                    "para que un planificador lo divida en pasos."
                ),
            },
            "destino_worker_id": {
                "type": "string",
                "description": (
                    "Opcional. ID del worker persistente al que se delega la "
                    "misión. Si se indica, además de crear la misión se escribe "
                    "un handoff pendiente (`persistent_agent_handoffs`) para que "
                    "el dueño lo apruebe y quede el enlace tarea → agente."
                ),
            },
            "destino_worker_nombre": {
                "type": "string",
                "description": (
                    "Opcional. NOMBRE de otro bot del equipo (mismo dueño) al "
                    "que delegar — alternativa humano-legible a "
                    "destino_worker_id. Se resuelve por nombre o display_name."
                ),
            },
            "expected_output": {
                "type": "string",
                "description": "Opcional. Qué entregable concreto se espera del worker.",
            },
            "priority": {
                "type": "string",
                "description": "Opcional. Prioridad: 'baja', 'media', 'alta' o 'urgente'.",
            },
            "allowed_tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Opcional. Herramientas permitidas para el worker delegado.",
            },
            "approval_boundary": {
                "type": "string",
                "description": (
                    "Opcional. Qué acciones del worker delegado requieren "
                    "aprobación humana antes de ejecutarse."
                ),
            },
        },
        "required": ["objetivo"],
    }
    requires_flags = frozenset({FLAG_AGENTS_MISSIONS})
    dangerous = False

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        objetivo = str(args.get("objetivo", "")).strip()
        if not objetivo:
            return ToolResult(content="Falta 'objetivo': describe qué debe lograr la misión.")

        if not await self._cupo_disponible(ctx):
            return ToolResult(content=_MSG_CUPO_AGOTADO)

        max_steps = getattr(ctx.settings, "MISSIONS_MAX_STEPS", DEFAULT_MAX_STEPS)
        mission_id = uuid4()
        presupuesto = json.dumps({"max_steps": max_steps})

        destino_worker_id = str(args.get("destino_worker_id") or "").strip()
        destino_worker_nombre = str(args.get("destino_worker_nombre") or "").strip()
        if not destino_worker_id and destino_worker_nombre:
            resuelto = await ctx.session.execute(
                text(
                    "SELECT id FROM persistent_agents "
                    "WHERE tenant_id = :tenant_id "
                    "AND (CAST(:owner AS uuid) IS NULL OR user_id = CAST(:owner AS uuid)) "
                    "AND (name = :exacto OR display_name = :exacto) "
                    "ORDER BY (name = :exacto) DESC LIMIT 1"
                ),
                {
                    "tenant_id": str(ctx.tenant_id),
                    "owner": str(ctx.user_id or ""),
                    "exacto": destino_worker_nombre,
                },
            )
            fila_nombre = resuelto.mappings().first()
            if fila_nombre is None:
                return ToolResult(
                    content=(
                        f"No encontré un bot llamado «{destino_worker_nombre}» en tu "
                        "equipo. Revísalo y vuelve a intentarlo."
                    )
                )
            destino_worker_id = str(fila_nombre["id"])
        # `owner_agent_id` NO está en `input_schema`: solo lo inyecta el
        # servicio de voz (determinista, no el LLM) para enlazar una misión al
        # worker nombrado SIN handoff — un turno de voz no es un worker, así
        # que no hay `source_worker_id` con el que escribir un handoff
        # (directiva §11-13, product design).
        owner_agent_id = str(args.get("owner_agent_id") or "").strip() or None
        if destino_worker_id:
            handoff = await _crear_handoff(
                ctx, mission_id=mission_id, destino_worker_id=destino_worker_id, args=args
            )
            if isinstance(handoff, ToolResult):
                return handoff
            owner_agent_id = destino_worker_id

        if owner_agent_id is not None:
            await ctx.session.execute(
                text(
                    "INSERT INTO agent_missions "
                    "(id, tenant_id, user_id, owner_agent_id, objetivo, status, plan, "
                    "resultado, presupuesto, error) "
                    "VALUES (:id, :tenant_id, :user_id, :owner_agent_id, :objetivo, "
                    "'planning', NULL, NULL, :presupuesto ::jsonb, NULL)"
                ),
                {
                    "id": str(mission_id),
                    "tenant_id": str(ctx.tenant_id),
                    "user_id": str(ctx.user_id),
                    "owner_agent_id": owner_agent_id,
                    "objetivo": objetivo,
                    "presupuesto": presupuesto,
                },
            )
            if not destino_worker_id:
                # owner-only (voz): sin handoff, el side-record TASK queda
                # dirigido al worker dueño para mantener el protocolo §12.
                await enviar_mensaje_agente(
                    ctx.session,
                    tenant_id=str(ctx.tenant_id),
                    sender=None,
                    receiver=owner_agent_id,
                    tipo="task",
                    task_id=str(mission_id),
                    goal=objetivo,
                )
        else:
            await ctx.session.execute(
                text(
                    "INSERT INTO agent_missions "
                    "(id, tenant_id, user_id, objetivo, status, plan, resultado, "
                    "presupuesto, error) "
                    "VALUES (:id, :tenant_id, :user_id, :objetivo, 'planning', NULL, "
                    "NULL, :presupuesto ::jsonb, NULL)"
                ),
                {
                    "id": str(mission_id),
                    "tenant_id": str(ctx.tenant_id),
                    "user_id": str(ctx.user_id),
                    "objetivo": objetivo,
                    "presupuesto": presupuesto,
                },
            )
            # Side-record en el protocolo inter-agente (product design): la
            # misión propia del asistente principal también queda visible como
            # TASK, con solo la referencia de contexto (task_id), no el
            # transcript.
            await enviar_mensaje_agente(
                ctx.session,
                tenant_id=str(ctx.tenant_id),
                sender=None,
                receiver=None,
                tipo="task",
                task_id=str(mission_id),
                goal=objetivo,
            )

        # Outbox TRANSACCIONAL: la misión se insertó arriba con ctx.session
        # (commitea al cierre del turno). Con el `enqueue` viejo (conexión
        # propia, commit inmediato) el worker corría el job ANTES del commit
        # y respondía "misión no encontrada" — la misión quedaba 'planning'
        # para siempre (bug real del 6-sep). Encolar en la MISMA sesión hace
        # que el job solo sea visible después del commit de la misión.
        try:
            await enqueue_outbox(
                ctx.session,
                tenant_id=ctx.tenant_id,
                job_type="run_mission",
                payload={"mission_id": str(mission_id)},
            )
        except Exception:
            # Degradación si el outbox no está disponible (p. ej. 0070 sin
            # aplicar): el camino viejo, con su carrera conocida.
            await enqueue(
                ctx.settings, "run_mission", {"mission_id": str(mission_id)}, ctx.tenant_id
            )

        unified_session = (ctx.extras or {}).get("unified_session")
        if unified_session is not None and hasattr(unified_session, "attach_task"):
            unified_session.attach_task(str(mission_id))

        logger.info(
            "delegar_mision: misión %s creada y encolada (tenant=%s)", mission_id, ctx.tenant_id
        )

        return ToolResult(
            content="Misión creada; sigue el avance en la página Misiones.",
            data={"mission_id": str(mission_id)},
        )

    async def _cupo_disponible(self, ctx: ToolContext) -> bool:
        """Mismo criterio que `missions.py::_check_missions_quota` (ver
        docstring del módulo, sección `limits.missions_per_day`): `-1`
        ilimitado, `0` (o ausente) sin cupo en absoluto, positivo se compara
        contra las `agent_missions` de este tenant creadas desde la
        medianoche UTC de hoy."""
        limite = _tenant_flags(ctx).get(LIMIT_MISSIONS_PER_DAY, 0)
        if limite == UNLIMITED:
            return True
        if limite == 0:
            return False

        since = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        resultado = await ctx.session.execute(
            text(
                "SELECT COUNT(*) FROM agent_missions "
                "WHERE tenant_id = :tenant_id AND owner_agent_id IN "
                "(SELECT id FROM persistent_agents WHERE user_id = :owner) "
                "AND created_at >= :since"
            ),
            {"tenant_id": str(ctx.tenant_id), "owner": str(ctx.user_id or ""), "since": since},
        )
        count = int(resultado.scalar() or 0)
        return count < limite


def get_all_tools() -> list[Tool]:
    """Entry point `edecan.tools` (ver `[project.entry-points]` en `pyproject.toml`)."""
    return [
        DelegarMisionTool(),
        EnviarMensajeBotTool(),
        ListarBotsTool(),
        EncargarAEquipoTool(),
    ]


class EnviarMensajeBotTool(Tool):
    """Mensaje asíncrono de bot a bot (el «SMS» de Grok Bot, product design).

    El RECEPTOR despierta con el mensaje como instrucción (`run_persistent_agent`,
    el mismo camino del protocolo inter-agente de `agent_messages.py`) y trabaja
    por su cuenta; el resultado le llega a su chat o al del dueño. La herramienta
    NO espera la respuesta: es asíncrona por diseño.
    """

    name = "enviar_mensaje_bot"
    description = (
        "Manda un mensaje a OTRO bot de Edecán por su nombre (ej. «BotAlpha»). Es "
        "asíncrono, como un SMS entre colegas: el otro despierta y responde al tono "
        "de tu mensaje — un saludo genera un saludo, un encargo genera trabajo. "
        "Úsalo para presentarte, conversar, coordinar o pedirle algo. NO sirve para "
        "hablar con el dueño."
    )
    category = "agents"
    risk_level = "low"
    latency_class = "fast"
    requires_flags = frozenset({FLAG_AGENTS_MISSIONS})
    dangerous = False
    input_schema = {
        "type": "object",
        "properties": {
            "bot": {
                "type": "string",
                "description": "Nombre del bot destino (ej. «BotAlpha»). Basta una parte del nombre.",
            },
            "mensaje": {
                "type": "string",
                "description": (
                    "Qué le encargas o le preguntas, con el contexto suficiente para que "
                    "trabaje sin preguntarte de vuelta."
                ),
            },
        },
        "required": ["bot", "mensaje"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        from edecan_core.queue import enqueue, enqueue_outbox

        objetivo = str(args.get("bot", "")).strip()
        mensaje = str(args.get("mensaje", "")).strip()
        if not objetivo or not mensaje:
            return ToolResult(content="Necesito a quién («bot») y qué le digo («mensaje»).")

        # Destino: bot del MISMO tenant y dueño, por nombre o display_name.
        # El emisor no puede escribirse a sí mismo (un SMS a uno mismo no
        # despierta a nadie).
        extras = ctx.extras if isinstance(ctx.extras, dict) else {}
        sender = _worker_id_valido(extras.get("worker_id"))
        if sender is None:
            return ToolResult(content=_MSG_CONTEXTO_BOT_INVALIDO)

        parent_chain = _cadena_padre_verificada(ctx)
        if isinstance(parent_chain, ToolResult):
            return parent_chain
        chain_depth, chain_visited = parent_chain
        if chain_depth >= MAX_HANDOFF_DEPTH:
            return ToolResult(
                content=(
                    "No puedo enviar mensajes: la cadena de delegación alcanzó su "
                    f"profundidad máxima ({MAX_HANDOFF_DEPTH} niveles)."
                )
            )
        result = await ctx.session.execute(
            text(
                "SELECT id, name, display_name FROM persistent_agents "
                "WHERE tenant_id = :tenant_id "
                "AND (CAST(:owner AS uuid) IS NULL OR user_id = CAST(:owner AS uuid)) "
                "AND (name ILIKE :q OR display_name ILIKE :q) "
                "AND (CAST(:sender_uuid AS uuid) IS NULL OR id::text <> :sender_text) "
                "ORDER BY updated_at DESC LIMIT 1"
            ),
            {
                # DOS parámetros, no uno: asyncpg infiere UN tipo por
                # placeholder y reusar el mismo valor como uuid (cast) y como
                # text (comparación) reventaba con «operator does not exist:
                # text <> uuid» — la tercera cara de la familia asyncpg.
                "tenant_id": str(ctx.tenant_id),
                "owner": str(ctx.user_id or ""),
                "q": f"%{objetivo}%",
                "sender_uuid": sender,
                "sender_text": sender,
            },
        )
        fila = result.mappings().first()
        if fila is None:
            return ToolResult(
                content=(
                    f"No encontré ningún bot llamado «{objetivo}». Los que conozco los "
                    "puedes ver con la herramienta listar_bots."
                )
            )

        receiver = str(fila["id"])
        receiver_id = _worker_id_valido(receiver)
        if receiver_id is None:
            return ToolResult(content="El bot encontrado no tiene una identidad válida.")
        receiver = receiver_id
        nombre = str(fila["display_name"] or fila["name"])
        message_id = uuid4()
        try:
            message_envelope = validate_handoff(
                source_worker_id=sender,
                destination_worker_id=receiver,
                task_id=str(message_id),
                depth=chain_depth,
                visited_worker_ids=chain_visited,
            )
        except ValueError as exc:
            message = str(exc)
            if "ciclo" in message:
                return ToolResult(content="No puedo enviar un mensaje: formaría un ciclo.")
            return ToolResult(content=message)

        # Nombre del EMISOR (para la instrucción del receptor y la firma del
        # mensaje en el hilo).
        nombre_emisor = None
        if sender:
            fila_emisor = (
                await ctx.session.execute(
                    text(
                        "SELECT display_name, name FROM persistent_agents "
                        "WHERE tenant_id = :tenant_id AND id = :id"
                    ),
                    {"tenant_id": str(ctx.tenant_id), "id": sender},
                )
            ).mappings().first()
            if fila_emisor is not None:
                nombre_emisor = str(fila_emisor["display_name"] or fila_emisor["name"])

        conversacion_id: str | None = None
        if sender:
            # Hilo VISIBLE entre ambos bots: chat directo (par canónica) +
            # conversación persistente — el dueño ve la discusión completa.
            fila_chat = (
                await ctx.session.execute(
                    text(
                        "INSERT INTO agent_direct_chats "
                        "(id, tenant_id, user_id, agent_a_id, agent_b_id) "
                        "VALUES (gen_random_uuid(), :tenant_id, :user_id, :a, :b) "
                        "ON CONFLICT (tenant_id, user_id, agent_a_id, agent_b_id) "
                        "DO UPDATE SET updated_at = now() "
                        "RETURNING id, conversation_id"
                    ),
                    {
                        "tenant_id": str(ctx.tenant_id),
                        "user_id": str(ctx.user_id),
                        "a": min(sender, receiver),
                        "b": max(sender, receiver),
                    },
                )
            ).mappings().first()
            assert fila_chat is not None
            conversacion_id = (
                str(fila_chat["conversation_id"])
                if fila_chat["conversation_id"] is not None
                else None
            )
            if conversacion_id is None:
                creada = (
                    await ctx.session.execute(
                        text(
                            "INSERT INTO conversations (id, tenant_id, user_id, title, channel) "
                            "VALUES (gen_random_uuid(), :tenant_id, :user_id, :titulo, 'web') "
                            "RETURNING id"
                        ),
                        {
                            "tenant_id": str(ctx.tenant_id),
                            "user_id": str(ctx.user_id),
                            "titulo": f"{nombre_emisor or 'Bot'} ↔ {nombre}",
                        },
                    )
                ).mappings().first()
                conversacion_id = str(creada["id"])
                await ctx.session.execute(
                    text(
                        "UPDATE agent_direct_chats SET conversation_id = :cid, updated_at = now() "
                        "WHERE tenant_id = :tenant_id AND id = :id"
                    ),
                    {
                        "cid": conversacion_id,
                        "tenant_id": str(ctx.tenant_id),
                        "id": str(fila_chat["id"]),
                    },
                )
            # El mensaje del emisor queda en el hilo (contrato normalizado de
            # `bot_turn_service.normalize_stored_message`).
            await ctx.session.execute(
                text(
                    "INSERT INTO messages (id, tenant_id, conversation_id, role, content) "
                    "VALUES (gen_random_uuid(), :tenant_id, :cid, 'assistant', :content ::jsonb)"
                ),
                {
                    "tenant_id": str(ctx.tenant_id),
                    "cid": conversacion_id,
                    "content": json.dumps(
                        {
                            "text": mensaje,
                            "sender_id": sender,
                            "sender_name": nombre_emisor or "Bot",
                        },
                        ensure_ascii=False,
                    ),
                },
            )

        await ctx.session.execute(
            text(
                "INSERT INTO agent_messages "
                "(id, tenant_id, sender_agent_id, receiver_agent_id, task_id, "
                "conversation_id, message_type, goal, status) "
                "VALUES (gen_random_uuid(), :tenant_id, :sender, :receiver, :task_id, "
                ":conversation_id, 'task', :goal, 'pending')"
            ),
            {
                "tenant_id": str(ctx.tenant_id),
                "sender": sender,
                "receiver": receiver,
                "task_id": str(message_id),
                "conversation_id": conversacion_id,
                "goal": mensaje,
            },
        )
        payload = {
            "worker_id": receiver,
            # Los nombres canónicos coinciden con el envelope validado por
            # el API; los aliases `chain_*` conservan el contrato vigente
            # del runner.
            "depth": message_envelope["depth"],
            "visited_worker_ids": message_envelope["visited_worker_ids"],
            "chain_depth": message_envelope["depth"],
            "chain_visited": json.dumps(message_envelope["visited_worker_ids"]),
            "instruction": (
                (
                    f"El bot {nombre_emisor} te escribe: «{mensaje}». "
                    "Esto es una CONVERSACIÓN entre colegas: responde al tono del "
                    "mensaje — un saludo se responde CON UN SALUDO (al menos una "
                    "frase real, con calidez: te presentas, dices quién eres y qué "
                    "agradeces), una pregunta con tu respuesta, un encargo con un "
                    "plan breve. NO inventes trabajo ni arranques proyectos por tu "
                    "cuenta: presentarse no es resolver nada. No escribas un "
                    "resumen de lo que hiciste ni un relato en tercera persona.\n"
                    "OBLIGATORIO — AVISA AL DUEÑO PRIMERO: tu mensaje final de este "
                    "turno debe decirle al dueño QUÉ te dijo el otro bot, con su "
                    "contenido, en una o dos frases de tu voz (ej.: «BotAlpha me "
                    "respondió: dice que los logos sí convienen, pero hay que "
                    "revisar las condiciones de uso de NVIDIA»). El dueño lee este "
                    "chat: nunca lo dejes esperando a preguntarte qué pasó. Si "
                    "además quieres contestarle al otro bot, usa "
                    f"enviar_mensaje_bot con bot=\"{nombre_emisor}\", pero el aviso "
                    "al dueño va SIEMPRE y es tu texto final."
                )
                if nombre_emisor
                else f"El dueño te encarga por medio de Edecán: «{mensaje}». "
                "Cuando termines, cuenta el resultado al dueño en tu chat."
            ),
            "task_id": str(message_id),
        }
        # Outbox TRANSACCIONAL (mismo criterio que `DelegarMisionTool` y
        # `agent_messages.send_message`, BOTS-08): el INSERT de `agent_messages`
        # + chat de arriba vive en `ctx.session` y commitea al cierre del turno.
        # Con el `enqueue` viejo (conexión propia, commit inmediato) el receptor
        # corría el job ANTES de ese commit y leía el mensaje aún no visible —
        # narración perdida y fila `pending` para siempre (hallazgo C8a).
        # Encolar en la MISMA sesión hace que el job solo sea visible después
        # del commit del mensaje.
        try:
            await enqueue_outbox(
                ctx.session,
                tenant_id=ctx.tenant_id,
                job_type="run_persistent_agent",
                payload=payload,
            )
        except Exception:
            # Degradación si el outbox no está disponible (migración no
            # aplicada): el camino viejo, con su carrera conocida.
            await enqueue(ctx.settings, "run_persistent_agent", payload, ctx.tenant_id)
        # Evento en el CHAT DEL EMISOR: «Escribió a X» — fila pequeña y
        # centrada con la cara del otro bot. Es un hecho, no opinión del
        # modelo: lo escribe la herramienta, no el LLM.
        avatar_receptor = (
            await ctx.session.execute(
                text(
                    "SELECT avatar FROM persistent_agents "
                    "WHERE tenant_id = :tenant_id AND id = :id"
                ),
                {"tenant_id": str(ctx.tenant_id), "id": receiver},
            )
        ).scalar()
        try:
            if conversacion_id:
                await ctx.session.execute(
                    text(
                        "INSERT INTO messages (id, tenant_id, conversation_id, role, content) "
                        "VALUES (gen_random_uuid(), :tenant_id, :cid, 'assistant', "
                        ":content ::jsonb)"
                    ),
                    {
                        "tenant_id": str(ctx.tenant_id),
                        "cid": conversacion_id,
                        "content": json.dumps(
                            {
                                "kind": "evento",
                                "evento": "escribio_a",
                                "text": f"Escribió a {nombre}",
                                "otro_nombre": nombre,
                                "cara": avatar_receptor,
                                "sender_id": sender,
                                "sender_name": nombre_emisor or "Bot",
                            },
                            ensure_ascii=False,
                        ),
                    },
                )
        except Exception:  # noqa: BLE001 - el evento es cosmético, nunca rompe el envío
            pass

        return ToolResult(
            content=(
                f"Le escribiste a {nombre}. El tono de tu mensaje define la conversación: "
                "un saludo genera un saludo, un encargo genera trabajo — no le inventes "
                "proyectos a un saludo. Cuéntaselo al dueño en una frase, en tu voz."
            ),
            data={
                "receiver": receiver,
                "receiver_nombre": nombre,
                "message_id": str(message_id),
                "conversation_id": conversacion_id,
            },
        )


class ListarBotsTool(Tool):
    """Los compañeros que existen: nombre + para qué sirve cada uno."""

    name = "listar_bots"
    description = (
        "Lista los otros bots de Edecán con su nombre y para qué sirven. Úsala "
        "antes de enviar_mensaje_bot si no recuerdas quién es quién."
    )
    category = "agents"
    risk_level = "low"
    latency_class = "fast"
    requires_flags = frozenset({FLAG_AGENTS_MISSIONS})
    dangerous = False
    input_schema = {"type": "object", "properties": {}}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        result = await ctx.session.execute(
            text(
                "SELECT name, display_name, COALESCE(purpose, '') AS purpose, status "
                "FROM persistent_agents WHERE tenant_id = :tenant_id AND enabled "
                "AND (CAST(:owner AS uuid) IS NULL OR user_id = CAST(:owner AS uuid)) "
                "ORDER BY updated_at DESC"
            ),
            {"tenant_id": str(ctx.tenant_id), "owner": str(ctx.user_id or "")},
        )
        filas = result.mappings().all()
        if not filas:
            return ToolResult(content="Todavía no hay otros bots creados.")
        lineas: list[str] = []
        for f in filas:
            nombre = str(f["display_name"] or f["name"])
            proposito = str(f["purpose"])[:110] or "(sin descripción)"
            lineas.append(f"- {nombre}: {proposito}")
        return ToolResult(content="Los bots que existen:\n" + "\n".join(lineas))

async def _asegurar_hilo_directo(
    session: Any,
    *,
    tenant_id: str,
    user_id: str,
    sender: str,
    receiver: str,
    nombre_a: str,
    nombre_b: str,
) -> str | None:
    """Hilo VISIBLE (par canónica + conversación) entre dos bots."""
    fila_chat = (
        await session.execute(
            text(
                "INSERT INTO agent_direct_chats "
                "(id, tenant_id, user_id, agent_a_id, agent_b_id) "
                "VALUES (gen_random_uuid(), :tenant_id, :user_id, :a, :b) "
                "ON CONFLICT (tenant_id, user_id, agent_a_id, agent_b_id) "
                "DO UPDATE SET updated_at = now() "
                "RETURNING id, conversation_id"
            ),
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "a": min(sender, receiver),
                "b": max(sender, receiver),
            },
        )
    ).mappings().first()
    if fila_chat is None:
        return None
    conversation_id = str(fila_chat["conversation_id"]) if fila_chat["conversation_id"] else None
    if conversation_id is None:
        creada = (
            await session.execute(
                text(
                    "INSERT INTO conversations (id, tenant_id, user_id, title, channel) "
                    "VALUES (gen_random_uuid(), :tenant_id, :user_id, :titulo, 'web') "
                    "RETURNING id"
                ),
                {
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "titulo": f"{nombre_a} ↔ {nombre_b}",
                },
            )
        ).mappings().first()
        conversation_id = str(creada["id"])
        await session.execute(
            text(
                "UPDATE agent_direct_chats SET conversation_id = :cid, updated_at = now() "
                "WHERE tenant_id = :tenant_id AND id = :id"
            ),
            {"cid": conversation_id, "tenant_id": tenant_id, "id": str(fila_chat["id"])},
        )
    return conversation_id


class EncargarAEquipoTool(Tool):
    """Encargo a equipo: el coordinador reparte sub-encargos entre los bots
    de un TEAM existente. Cada sub-encargo es un handoff que el DUEÑO aprueba;
    cuando todos terminan (o fallan), el coordinador recibe un turno de merge
    y le entrega al dueño UNA sola cosa final."""

    name = "encargar_a_equipo"
    description = (
        "Reparte un encargo ENTRE LOS BOTS DE UN EQUIPO que ya creaste: cada "
        "miembro recibe su sub-encargo, todos trabajan en paralelo (tras tu "
        "aprobación de cada uno) y tú coordinas la fusión en UN entregable "
        "final. Fíjate en la especialidad de cada bot al repartir."
    )
    category = "agents"
    risk_level = "medium"
    latency_class = "background"
    requires_flags = frozenset({FLAG_AGENTS_MISSIONS})
    dangerous = False
    input_schema = {
        "type": "object",
        "properties": {
            "nombre_equipo": {
                "type": "string",
                "description": "Nombre exacto del equipo (el que creaste en Teams).",
            },
            "pedido": {
                "type": "string",
                "description": "El encargo completo, para contexto de todos.",
            },
            "subencargos": {
                "type": "object",
                "description": (
                    "Reparto: {nombre_del_bot: sub_encargo}. Entre 1 y 5 bots. "
                    "Fíjate en la especialidad de cada uno."
                ),
                "additionalProperties": {"type": "string"},
            },
        },
        "required": ["nombre_equipo", "pedido", "subencargos"],
    }

    MAX_MIEMBROS = 5

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        extras = ctx.extras if isinstance(ctx.extras, dict) else {}
        coordinador = str(extras.get("worker_id") or "").strip()
        if not coordinador:
            return ToolResult(
                content="Para repartir en equipo hace falta ejecutar desde un bot (worker_id)."
            )

        nombre_equipo = str(args.get("nombre_equipo") or "").strip()
        pedido = str(args.get("pedido") or "").strip()
        subencargos = args.get("subencargos") or {}
        if not isinstance(subencargos, dict):
            return ToolResult(content="subencargos debe ser un objeto {bot: sub-encargo}.")
        subencargos = {
            str(k).strip(): str(v).strip()
            for k, v in subencargos.items()
            if str(k).strip() and str(v).strip()
        }
        if not nombre_equipo or not pedido or not subencargos:
            return ToolResult(content="Necesito nombre_equipo, pedido y al menos un subencargo.")
        if len(subencargos) > self.MAX_MIEMBROS:
            return ToolResult(content=f"Máximo {self.MAX_MIEMBROS} bots por encargo.")

        miembros_raw = (
            await ctx.session.execute(
                text(
                    "SELECT a.id, a.display_name, a.name "
                    "FROM teams t "
                    "JOIN team_members tm ON tm.team_id = t.id "
                    "JOIN persistent_agents a ON a.id = tm.agent_id "
                    "WHERE t.tenant_id = :tenant_id AND t.user_id = :user_id "
                    "AND t.name = :nombre_equipo ORDER BY a.name"
                ),
                {
                    "tenant_id": str(ctx.tenant_id),
                    "user_id": str(ctx.user_id or ""),
                    "nombre_equipo": nombre_equipo,
                },
            )
        ).mappings().all()
        if not miembros_raw:
            return ToolResult(content=f"No encontré el equipo «{nombre_equipo}» (o es de otro dueño).")
        por_nombre = {str(f["display_name"] or f["name"]): str(f["id"]) for f in miembros_raw}

        nombrados: list[tuple[str, str, str]] = []
        for nombre_bot, sub in subencargos.items():
            miembro_id = por_nombre.get(nombre_bot)
            if miembro_id is None:
                near = [n for n in por_nombre if nombre_bot.lower() in n.lower()]
                return ToolResult(
                    content=(
                        f"«{nombre_bot}» no es miembro de {nombre_equipo}."
                        + (f" ¿Quizá: {', '.join(near)}?" if near else "")
                    )
                )
            if miembro_id == coordinador:
                return ToolResult(content="No puedes repartirte un sub-encargo a ti mismo.")
            nombrados.append((nombre_bot, miembro_id, sub))

        fila_mision = (
            await ctx.session.execute(
                text(
                    "INSERT INTO team_missions "
                    "(tenant_id, user_id, coordinator_agent_id, pedido, status, "
                    "esperados, subencargos) "
                    "VALUES (:tenant_id, :user_id, :coordinador, :pedido, "
                    "'waiting_approval', :esperados, :subencargos ::jsonb) RETURNING id"
                ),
                {
                    "tenant_id": str(ctx.tenant_id),
                    "user_id": str(ctx.user_id or ""),
                    "coordinador": coordinador,
                    "pedido": pedido,
                    "esperados": len(nombrados),
                    "subencargos": json.dumps([nombre for nombre, _id, _s in nombrados]),
                },
            )
        ).mappings().first()
        mision_id = str(fila_mision["id"])

        encargos_creados: list[str] = []
        for nombre_bot, miembro_id, sub in nombrados:
            mano = await _crear_handoff(
                ctx,
                mission_id=f"team:{mision_id}:{miembro_id[:8]}",
                destino_worker_id=miembro_id,
                args={
                    "objetivo": sub,
                    "expected_output": "Tu resultado concreto (conciso, en español, sin voseo).",
                },
            )
            if isinstance(mano, ToolResult):
                logger.warning("encargar_a_equipo: falló un handoff: %s", mano.content)
                continue
            handoff_id = (mano or {}).get("handoff_id")
            if not handoff_id:
                continue
            await ctx.session.execute(
                text(
                    "INSERT INTO team_mission_results "
                    "(tenant_id, team_mission_id, agent_id, handoff_id, estado) "
                    "VALUES (:tenant_id, :mision, :agente, :handoff, 'pending')"
                ),
                {
                    "tenant_id": str(ctx.tenant_id),
                    "mision": mision_id,
                    "agente": miembro_id,
                    "handoff": handoff_id,
                },
            )
            encargos_creados.append(nombre_bot)

        await ctx.session.execute(
            text(
                "UPDATE team_missions SET esperados = :n, "
                "nota = :nota, updated_at = now() WHERE id = :mision"
            ),
            {
                "n": len(encargos_creados),
                "nota": (
                    f"Faltaron {len(nombrados) - len(encargos_creados)} sub-encargo(s) por "
                    "crear (límite/ciclo)." if len(encargos_creados) < len(nombrados) else None
                ),
                "mision": mision_id,
            },
        )
        if not encargos_creados:
            await ctx.session.execute(
                text(
                    "UPDATE team_missions SET status = 'failed', nota = 'Ningún handoff se creó.', "
                    "updated_at = now() WHERE id = :mision"
                ),
                {"mision": mision_id},
            )
            return ToolResult(content="No pude crear ningún sub-encargo del equipo (revisa los nombres).")
        return ToolResult(
            content=(
                f"Repartí el encargo en {len(encargos_creados)} sub-encargos del equipo "
                f"«{nombre_equipo}»: {', '.join(encargos_creados)}. Cada uno espera tu "
                "aprobación; cuando todos terminen te entrego UN resultado final."
            )
        )
