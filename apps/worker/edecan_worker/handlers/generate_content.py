"""Job `generate_content`: genera contenido con el LLM (alias `"principal"`) a
partir de un brief y lo guarda como mensaje `assistant` en la conversación
indicada (ARCHITECTURE.md §10.6, §10.11).

Payload: `{"conversation_id": "<uuid>", "brief": "<texto>"}`. Requiere
`env.tenant_id`. Los flags del plan se recalculan siempre desde
`edecan_schemas.PLANES[plan_key]` (mismo criterio que `ARCHITECTURE.md`
§10.12 para la API: nunca se confían de datos externos al job).

`conversation_id` también llega del payload del job sin garantía de
pertenencia, así que antes de generar nada se verifica con
`repo.get_conversation(tenant_id=..., conversation_id=...)` (scoped por
tenant) que la conversación sea de `env.tenant_id` — igual que `ingest_file`
verifica `file_id` con `get_file` y `send_reminder` verifica `reminder_id`
con `get_reminder`. El worker corre con conexión "dueño" que bypassa Row-
Level Security (`edecan_worker.repo`), así que esta comprobación manual es
la única barrera contra escribir un mensaje generado por LLM en la
conversación de otro tenant.

NOTA — sin productor todavía: ningún endpoint de `edecan_api`, tool del
toolkit ni scheduler encola hoy un job `type="generate_content"` (repasar los
`enqueue(...)` de `apps/api/edecan_api/routers/`,
`apps/worker/edecan_worker/scheduler.py` y `premium/edecan_premium/`). El
camino de generación de contenido que sí corre en producción es la tool
síncrona `generar_contenido` (`GenerarContenidoTool` en
`packages/toolkit/edecan_toolkit/contenido.py`), que responde directo en el
turno del LLM y nunca delega en este job. Este handler queda implementado,
registrado en `HANDLERS`/`JOB_TYPES` (ARCHITECTURE.md §10.5, §10.11) y
cubierto por `apps/worker/tests/test_generate_content.py` a la espera de un
productor real (p. ej. contenido generado en background y entregado como
mensaje nuevo en la conversación) — hasta que algo lo encole, este código no
corre en producción.
"""

from __future__ import annotations

import logging
import uuid

from edecan_llm.base import ChatMessage, CompletionRequest
from edecan_schemas import PLANES, JobEnvelope

from edecan_worker.deps import Deps
from edecan_worker.repo import SqlRepo

logger = logging.getLogger(__name__)

MAX_TOKENS_CONTENIDO = 2048
SYSTEM_PROMPT = (
    "Eres el asistente de generación de contenido de Edecán. Escribe el "
    "contenido solicitado en el brief, listo para publicar, en español salvo "
    "que el brief pida explícitamente otro idioma."
)


async def handle(env: JobEnvelope, deps: Deps) -> None:
    if env.tenant_id is None:
        raise ValueError("generate_content requiere tenant_id")
    conversation_id = uuid.UUID(str(env.payload["conversation_id"]))
    brief = str(env.payload["brief"])

    async with deps.session_factory(None) as session:
        repo = SqlRepo(session)

        conversation = await repo.get_conversation(
            tenant_id=env.tenant_id, conversation_id=conversation_id
        )
        if conversation is None:
            logger.error(
                "generate_content: conversación no encontrada conversation_id=%s tenant_id=%s",
                conversation_id,
                env.tenant_id,
            )
            return

        # Bring-your-own por tenant (WP-V3-02, ver `Deps.llm_router_for`):
        # resuelto PEREZOSO acá, DESPUÉS del guarda de arriba (conversación
        # no encontrada) — ese caso no necesita jamás el LLM. Lanza
        # `TenantLLMNotConnectedError` (nunca cae a `deps.llm_router` de
        # plataforma) si no se puede resolver — se deja propagar, el
        # despachador del job la trata como cualquier otro fallo (reintento
        # con backoff, luego DLQ/`status='error'` con este mensaje claro en
        # `last_error`).
        llm_router = await deps.llm_router_for(env.tenant_id)

        tenant = await repo.get_tenant(tenant_id=env.tenant_id)
        plan_key = tenant["plan_key"] if tenant else "free_selfhost"
        plan = PLANES.get(plan_key, PLANES["free_selfhost"])

        provider, model = llm_router.resolve("principal", plan.flags)
        request = CompletionRequest(
            model=model,
            system=SYSTEM_PROMPT,
            messages=[ChatMessage(role="user", content=brief)],
            max_tokens=MAX_TOKENS_CONTENIDO,
        )
        response = await provider.complete(request)

        await repo.add_message(
            tenant_id=env.tenant_id,
            conversation_id=conversation_id,
            role="assistant",
            content={"text": response.text},
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
        )
        await repo.add_usage_event(
            tenant_id=env.tenant_id,
            kind="llm_tokens",
            quantity=float(response.usage.input_tokens + response.usage.output_tokens),
            meta={"model": model, "alias": "principal", "conversation_id": str(conversation_id)},
        )

    logger.info(
        "generate_content completado conversation_id=%s tenant_id=%s modelo=%s",
        conversation_id,
        env.tenant_id,
        model,
    )
