"""Job `run_companion_turn`: real proactive companion turn in the main chat.

The scheduler/event only wakes this job. The model decides whether to speak,
using the same `Agent.run_turn` runtime as user chat (memory, history, tools).
Never inserts canned assistant text.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from edecan_core.agent import Agent
from edecan_core.companion_wake import (
    DEFAULT_WAKE_INSTRUCTION,
    companion_always_on,
    companion_push_title,
    format_push_preview,
    is_substantive_assistant_text,
    record_companion_wake,
    rows_to_chat_messages,
    should_run_wake,
    stable_event_id,
)
from edecan_core.notifications import ImportantNotificationEvent
from edecan_core.tools import ToolContext, ToolRegistry
from edecan_schemas import PLANES, JobEnvelope, PersonaConfig
from sqlalchemy import text

from edecan_worker.deps import Deps
from edecan_worker.handlers.run_automation import (
    _apply_agent_profile,
    _build_registry,
    _persona_from_row,
)
from edecan_worker.repo import SqlRepo
from edecan_worker.universal_notifications import notify_important_event

logger = logging.getLogger(__name__)

EXCLUDED_TOOL_NAMES = frozenset({"delegar_mision", "gestionar_automatizacion"})


def _event_log_fn() -> Any | None:
    """`edecan_api.event_log.log_event_con_factory`; `None` si no existe (fail-open).

    Import perezoso por nombre de módulo (mismo criterio que el resto de
    hermanos, ARCHITECTURE.md §10.1): un worker sin la API sigue despertando
    al companion sin log de eventos.
    """
    try:
        from edecan_api.event_log import log_event_con_factory
    except ImportError:
        logger.debug("edecan_api.event_log no disponible; wake sin log de eventos.")
        return None
    return log_event_con_factory


async def _log_evento(
    deps: Deps,
    *,
    tenant_id: UUID | None,
    categoria: str,
    accion: str,
    detalle: dict[str, Any] | None = None,
) -> None:
    """Registra el hecho en `event_log` (plataforma de logging TOTAL).

    Fail-open en cada eslabón: el despertar jamás depende del log — un fallo
    acá no puede tumbar el turno ni callar un mensaje que debía salir.
    """
    fn = _event_log_fn()
    if fn is None:
        return
    try:
        await fn(
            deps.session_factory,
            tenant_id=tenant_id,
            categoria=categoria,
            accion=accion,
            detalle=detalle,
        )
    except Exception:  # noqa: BLE001 - el log es observabilidad, no requisito
        logger.warning(
            "run_companion_turn: no se pudo registrar en event_log (%s/%s)",
            categoria,
            accion,
            exc_info=True,
        )


def _build_safe_registry(
    full_registry: ToolRegistry,
    flags: dict[str, Any],
    *,
    dangerous_permitidas: set[str] | None = None,
) -> ToolRegistry:
    """Registry del turno con las tools `dangerous` excluidas, SALVO las
    `dangerous_permitidas` (p. ej. `usar_computadora` + `navegar` para el
    companion, o solo `navegar` para la vida digital -- la lectura es SOLO
    con el navegador del box, la Mac conectada no abre Chrome delante del
    dueño)."""
    permitidas = dangerous_permitidas or set()
    safe = ToolRegistry()
    for spec in full_registry.specs(flags):
        if spec.name in EXCLUDED_TOOL_NAMES:
            continue
        tool = full_registry.get(spec.name)
        if tool is None:
            continue
        if tool.dangerous and tool.name not in permitidas:
            continue
        safe.register(tool)
    return safe


def _event_to_dict(event: Any) -> dict[str, Any]:
    if isinstance(event, dict):
        return event
    if hasattr(event, "model_dump"):
        return event.model_dump()
    return dict(vars(event))


_BOGOTA = ZoneInfo("America/Bogota")

_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def _con_reloj_y_fecha(instruction: str) -> str:
    """Antepone la hora REAL del usuario (America/Bogota) a la instrucción.

    Los LLM no conocen la hora actual; sin este prefijo juzgan "hace un rato"
    o "recién" a ciegas, confunden días y fechan mal los sucesos.
    """
    ahora = datetime.now(UTC).astimezone(_BOGOTA)
    hora_hhmm = f"{ahora.hour % 12 or 12}:{ahora.minute:02d}"
    hora_hhmm += " " + ("a. m." if ahora.hour < 12 else "p. m.")
    cabecera = (
        f"[AHORA ES: {_DIAS[ahora.weekday()]}, {ahora.day} de "
        f"{_MESES[ahora.month - 1]}, {hora_hhmm}]"
    )
    return cabecera + "\n" + instruction


_RESTART_FLAG = "/opt/edecan/data/restart-requested"


def _es_despertar_de_contenido(payload: dict[str, Any]) -> bool:
    """True para wakes de contenido (vida_digital y afines): sin empujar
    avisos de fallo del motor — solo silencio/log/Actividad."""
    return str(payload.get("source") or "").strip() in {
        "vida_digital",
    }
# Tope del bucle de re-encolado del wake de reinicio: si el cron vigilante
# está roto, no se generan jobs para siempre.
_MAX_REINTENTOS_RESTART = 8


def _reinicio_todavia_pendiente() -> bool:
    """True si el flag del reinicio auto-gestionado sigue en disco (el cron
    root lo borra justo antes de reiniciar el servicio)."""
    import os

    return os.path.exists(_RESTART_FLAG)


async def run_companion_agent_turn(
    *,
    ctx: ToolContext,
    llm_router: Any,
    registry: ToolRegistry,
    persona: PersonaConfig,
    flags: dict[str, Any],
    history: list[Any],
    instruction: str,
    provider_health: Any | None = None,
    exigir_pregunta_opinion: bool = True,
    priorizar_modelo_fuerte: bool = False,
    es_vida_digital: bool = False,
) -> tuple[str, list[dict[str, Any]], dict[str, Any], str | None]:
    """Run one headless companion turn. Returns (text, tool_log, usage, terminal_error).

    `exigir_pregunta_opinion=False` relaja SOLO la sub-regla "voz real = trae
    pregunta o postura personal" del portón de voz humana — pensado para wakes
    de informe (p. ej. `phone_call_finished`: el resumen de una llamada es un
    REPORTES de hechos, no exige opinión). Los portones anti-volcado (listas,
    marcadores técnicos, voseo, longitud) quedan intactos.
    """
    # El turno del dueño puede usar la computadora si hay companion emparejado
    # (vida digital: abrir apps, clic, scroll, screenshot). La aprobación por
    # acción la resuelve el bridge local (ver _DESKTOP_OWNER_ACTIONS).
    from edecan_core.companion_access import companion_para

    companion = companion_para(ctx.tenant_id)
    ctx.extras["approved_tool_calls"] = (
        {"usar_computadora", "navegar_web_interactivo"} if companion is not None else set()
    )
    if companion is not None:
        ctx.extras["companion"] = companion
    ctx.extras.setdefault("flags", flags)

    permitidas = (
        {"navegar_web_interactivo"} if es_vida_digital else {"usar_computadora", "navegar_web_interactivo"}
    )
    safe_registry = _build_safe_registry(registry, flags, dangerous_permitidas=permitidas)
    agent_kwargs: dict[str, Any] = {
        # La exploración visual (abrir apps, clic, scroll, screenshots) consume
        # muchas iteraciones de tools; el presupuesto de un turno de chat
        # corto la dejaría a medio camino y Edecán escribiría que no pudo.
        "max_tool_iterations": 40,
    }
    if provider_health is not None:
        agent_kwargs["provider_health"] = provider_health
    agent = Agent(llm_router, safe_registry, **agent_kwargs)

    # Modelo del turno proactivo: Luna si el dueño la configuró (el tier
    # económico del directorio incluye visión). Una sola puerta sancionada en
    # `edecan_llm.task_router.modelo_luna_configurada` (sin literales acá).
    from edecan_core.agent import SeleccionDeModelo
    from edecan_llm.task_router import modelo_luna_configurada, modelo_sol_configurada

    # Un modelo barato a veces NO sigue la regla "no vuelques el análisis":
    # publica su salida O LA del visión sin asimilarla. Validamos que lo
    # publicado sea VOZ HUMANA (una nota de amigo): si es un volcado, se
    # reintenta con el tier estándar y un recordatorio explícito. Si vuelve a
    # fallar, no se publica nada (silencio > basura).
    _MARCADORES_VOLCADO = (
        "App al frente",
        "Título de la ventana",
        "Texto legible",
        "Chat abierto con",
        "Te resumo",
        "A modo de resumen",
        "Esto es lo que pasó",
        "Lo que pasó en tus chats",
        "Vista rápida",
        "Lo más importante",
        "Como resumen",
        "usar_computadora",
        "Ejecuté lo que hacía falta",
    )
    _BULLETS_ARG = (
        "- ",
        "* ",
        "• ",
        "1) ",
        "2) ",
        "3) ",
        "4) ",
        "5) ",
        "6) ",
        "7) ",
        "8) ",
        "9) ",
    )

    def _es_voz_humana(texto: str) -> bool:
        t = (texto or "").strip()
        if not t:
            return False
        if any(m in t for m in _MARCADORES_VOLCADO):
            return False
        if t.count("«") >= 2:
            return False
        if len(t) > 700:
            return False
        lineas = [ln for ln in t.splitlines() if ln.strip()]
        bullets = [ln for ln in lineas if ln.strip().startswith(_BULLETS_ARG)]
        if len(lineas) > 6 or len(bullets) >= 2:
            return False
        # VOZ REAL: la nota debe provocar respuesta — trae pregunta, o una
        # postura personal explícita. Un relato del estado no es voz.
        # (Relajable para wakes de informe — p. ej. resumen de llamada — con
        # `exigir_pregunta_opinion=False`; un reporte de hechos es legítimo.)
        if exigir_pregunta_opinion and "?" not in t and not any(
            marcador in t
            for marcador in (
                "me parece",
                "creo que",
                "te lo ",
                "cuéntame",
                "dime",
                "no lo dejaría",
                "mi consejo",
                "te digo",
            )
        ):
            return False
        # CERO VOSEO: nota en es-VE habla al dueño con "tú".
        if re.search(r"\b(vos|querés|tenés|podés|decime|contame|fijate|dejame)\b", t):
            return False
        if re.search(r"\b(escribí|preguntá|hablá|armá|usá|andá|sacá|poné|revisá|miralo|mirá)\b", t):
            return False
        return True

    async def _correr(intento: int):
        # Vida digital (exploración visual con navegador + screenshots)
        # Política de costos del dueño: LUNA primero SIEMPRE (la lectura de
        # vida digital ya es determinista — el sistema pre-lee el contenido y
        # lo inyecta). Sol queda SOLO como segundo intento si Luna viene
        # vacía, nunca como default de los wakes.
        modelos = [modelo_luna_configurada(), modelo_sol_configurada()]
        seleccion = SeleccionDeModelo(
            modelo=modelos[intento] if intento < len(modelos) else None
        )
        texto_extra = (
            ""
            if intento == 0
            else (
                "\n\n---\n\nIMPORTANTE: tu mensaje anterior fue técnico o el VOLCADO "
                "del análisis (el informe de la herramienta). Eso NO se publica. "
                "Escribí AHORA tu propia nota de amigo: 1-3 frases, tu reacción o "
                "pregunta, nada del informe literal, nada de listas, nada técnico "
                "(no menciones herramientas, names ni acciones)."
            )
        )
        partes: list[str] = []
        llaves = agent.run_turn(
            ctx=ctx,
            persona=persona,
            history=history,
            user_text=instruction + texto_extra,
            flags=flags,
            seleccion=seleccion,
        )
        async for raw_event in llaves:
            event = _event_to_dict(raw_event)
            event_type = event.get("type")
            if event_type == "text_delta":
                partes.append(str(event.get("text", "")))
            elif event_type in ("tool_start", "tool_end"):
                tool_log.append(event)
            elif event_type == "confirmation_required":
                return None, "confirmation_required"
            elif event_type == "error":
                return None, str(event.get("message") or "agent_error")
            elif event_type == "done":
                usage.update(event.get("usage") or {})
        return "".join(partes), None

    usage: dict[str, Any] = {}
    tool_log: list[dict[str, Any]] = []
    texto, error = await _correr(0)
    if error is not None:
        return "", tool_log, usage, error
    if not _es_voz_humana(texto):
        texto_acumulado = texto or ""
        texto, error = await _correr(1)
        if error is not None:
            return "", tool_log, usage, error
        if not _es_voz_humana(texto):
            logger.warning(
                "run_companion_turn: turno descartado (sin voz humana, "
                "ni con el fallback): %r",
                (texto_acumulado + (texto or ""))[:300],
            )
            return "", tool_log, usage, None

    return texto, tool_log, usage, None


async def _guardar_lo_dicho(
    deps: Deps,
    tenant_id: UUID,
    user_id: UUID,
    texto: str,
    wake_key: str,
) -> None:
    """Guarda el mensaje que el turno proactivo YA envió en `memory_items`.

    El RAG del próximo despertar (`store.search`) lo recupera y el modelo ve
    "ya le dije exactamente esto el sábado" — deja de repetir el mismo
    comentario de LinkedIn o la misma pregunta de vida digital. Best-effort:
    jamás tumba el turno si el INSERT falla.
    """
    resumen = texto.strip()
    if not resumen:
        return
    # Guarda el mensaje con su contexto de despertar (de dónde vino), hasta
    # ~500 caracteres: suficiente para que el RAG lo distinga del siguiente.
    contenido = f"[proactivo {wake_key}] {resumen}"[:500]
    try:
        async with deps.session_factory(tenant_id) as session:
            await session.execute(
                text(
                    "INSERT INTO memory_items ("
                    "id, tenant_id, user_id, kind, content, importance, confidence, source, "
                    "created_at, updated_at"
                    ") VALUES ("
                    ":id, :tenant_id, :user_id, 'event', :contenido, 0.5, 0.8, "
                    "'proactive', :now, :now"
                    ")"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "contenido": contenido,
                    "now": datetime.now(UTC),
                },
            )
            await session.commit()
        logger.info(
            "run_companion_turn: lo dicho guardado en memoria (wake_key=%s)", wake_key
        )
    except Exception:  # noqa: BLE001 - best-effort, jamás tumba el turno
        logger.exception(
            "run_companion_turn: no pude guardar lo dicho en memoria (wake_key=%s)",
            wake_key,
        )


async def handle(env: JobEnvelope, deps: Deps) -> None:
    if env.tenant_id is None:
        raise ValueError("run_companion_turn requiere tenant_id")

    tenant_id: UUID = env.tenant_id
    payload = env.payload or {}
    user_id = UUID(str(payload["user_id"]))
    wake_key = str(payload.get("wake_key") or "").strip()
    if not wake_key:
        raise ValueError("run_companion_turn requiere wake_key")

    # Continuidad tras un reinicio auto-gestionado (ver la tool
    # `reiniciar_servicio`): el wake se encola ANTES del reinicio con
    # `restart_pending=True`; si el flag del reinicio todavía existe, el
    # reinicio no ha pasado — se re-encola un wake fresco y se vuelve sin
    # escribir nada (el turno corre DE VERDAD después de volver).
    if bool(payload.get("restart_pending")) and _reinicio_todavia_pendiente():
        from edecan_core.queue import enqueue as _enqueue

        reintentos = wake_key.count("#retry")
        if reintentos >= _MAX_REINTENTOS_RESTART:
            logger.error(
                "run_companion_turn: el reinicio no llegó tras %d reintentos; "
                "abandono el wake (tenant=%s wake=%s)",
                reintentos,
                tenant_id,
                wake_key,
            )
            return
        await _enqueue(
            deps.settings,
            "run_companion_turn",
            {
                "user_id": str(user_id),
                "wake_key": f"{wake_key}#retry",
                "source": str(payload.get("source") or "post_restart"),
                "instruction": payload.get("instruction"),
                "require_message": bool(payload.get("require_message")),
                "restart_pending": True,
                "push": payload.get("push"),
            },
            tenant_id,
            # Sin demora el worker re-encola cada ~2s y agota el tope ANTES
            # de que el cron reinicie (~60s) — bug real visto en la prueba
            # del 4-sep: 8 reintentos en 16s. Con 30s por reintento, la
            # espera cubre el ciclo completo del cron.
            delay_seconds=30,
        )
        logger.info(
            "run_companion_turn: reinicio aún pendiente; wake re-encolado "
            "(tenant=%s wake=%s)",
            tenant_id,
            wake_key,
        )
        return

    instruction = str(payload.get("instruction") or DEFAULT_WAKE_INSTRUCTION).strip()
    instruction = _con_reloj_y_fecha(instruction)
    urgent = bool(payload.get("urgent"))
    require_message = bool(payload.get("require_message"))
    conversation_id_raw = payload.get("conversation_id")

    async with deps.session_factory(None) as session:
        repo = SqlRepo(session)
        tenant = await repo.get_tenant(tenant_id=tenant_id)
        plan_key = tenant["plan_key"] if tenant else "free_selfhost"
        flags = dict(PLANES.get(plan_key, PLANES["free_selfhost"]).flags)
        always_on = await companion_always_on(session, tenant_id=tenant_id, user_id=user_id)

        if not should_run_wake(urgent=urgent, companion_enabled=always_on):
            logger.info(
                "run_companion_turn: quiet hours, se difiere tenant_id=%s user_id=%s wake_key=%s",
                tenant_id,
                user_id,
                wake_key,
            )
            await _log_evento(
                deps,
                tenant_id=tenant_id,
                categoria="companion_wake",
                accion="diferido_horas_quietas",
                detalle={"wake_key": wake_key, "source": payload.get("source")},
            )
            return

        claimed = await record_companion_wake(
            session, tenant_id=tenant_id, user_id=user_id, wake_key=wake_key
        )
        if not claimed:
            logger.info(
                "run_companion_turn: wake_key duplicado tenant_id=%s user_id=%s wake_key=%s",
                tenant_id,
                user_id,
                wake_key,
            )
            await _log_evento(
                deps,
                tenant_id=tenant_id,
                categoria="companion_wake",
                accion="wake_duplicado",
                detalle={"wake_key": wake_key, "source": payload.get("source")},
            )
            return

        persona_row = await repo.get_persona(tenant_id=tenant_id, user_id=user_id)
        if conversation_id_raw:
            conversation_id = UUID(str(conversation_id_raw))
            conversation = await repo.get_conversation(
                tenant_id=tenant_id, conversation_id=conversation_id
            )
            if conversation is None:
                logger.warning(
                    "run_companion_turn: conversation_id=%s no encontrada; uso main",
                    conversation_id,
                )
                conversation = await repo.resolve_main_conversation(
                    tenant_id=tenant_id, user_id=user_id
                )
        else:
            conversation = await repo.resolve_main_conversation(
                tenant_id=tenant_id, user_id=user_id
            )
        conversation_id = conversation["id"]

        history_rows = await repo.list_messages(
            tenant_id=tenant_id, conversation_id=conversation_id, limit=40
        )
        history = rows_to_chat_messages(history_rows)

        llm_router = await deps.llm_router_for(tenant_id)
        base_registry = _build_registry(tenant_id)
        for mcp_tool in await deps.mcp_tools_para(tenant_id, session, flags):
            base_registry.register(mcp_tool)
        registry, persona = _apply_agent_profile(
            base_registry, _persona_from_row(persona_row), None
        )

        ctx = ToolContext(
            tenant_id=tenant_id,
            user_id=user_id,
            session=session,
            settings=deps.settings,
            llm=llm_router,
            vault=deps.vault(session),
            extras={
                "flags": flags,
                "companion_wake": True,
                "wake_key": wake_key,
                "source": payload.get("source"),
            },
        )

        # El resumen de una llamada terminada es un REPORTE de hechos: el
        # portón "voz real = trae pregunta o postura" lo descartaba siempre y
        # el dueño jamás recibía el resumen (4 llamadas reproducidas en base).
        # Para ese wake el portón se relaja (anti-volcado queda intacto).
        es_wake_de_llamada = (
            str(payload.get("source") or "").strip() == "phone_call_finished"
        )
        es_wake_de_reinicio = (
            str(payload.get("source") or "").strip() == "post_restart"
        )

        async def _intentar_turno() -> tuple[str, list, dict, str | None]:
            return await run_companion_agent_turn(
                ctx=ctx,
                llm_router=llm_router,
                registry=registry,
                persona=persona,
                flags=flags,
                history=history,
                instruction=instruction,
                provider_health=deps.provider_health,
                exigir_pregunta_opinion=not es_wake_de_llamada,
                priorizar_modelo_fuerte=str(payload.get("source") or "").strip()
                == "vida_digital",
                es_vida_digital=str(payload.get("source") or "").strip()
                == "vida_digital",
            )

        text, tool_log, usage, terminal_error = await _intentar_turno()

        deserto = terminal_error is not None or not is_substantive_assistant_text(text)
        if deserto and require_message:
            # El despertar exige mensaje y el primer intento vino vacío (fallo
            # del proveedor, texto no sustantivo). Un reintento ÚNICO antes de
            # declarar el corte: una racha del proveedor no puede dejar al
            # dueño sin el reporte (p. ej. el resumen de una llamada).
            logger.warning(
                "run_companion_turn: despertar exige mensaje y el intento vino vacío "
                "(terminal_error=%s); reintento único tenant_id=%s user_id=%s wake_key=%s",
                terminal_error,
                tenant_id,
                user_id,
                wake_key,
            )
            text, tool_log, usage, terminal_error = await _intentar_turno()
            deserto = terminal_error is not None or not is_substantive_assistant_text(
                text
            )

        if deserto and terminal_error is None and not es_wake_de_llamada and not es_wake_de_reinicio:
            # Wake de CONTENIDO (p. ej. vida_digital): el agente no encontró
            # nada que contar y no hubo fallo del proveedor -> SILENCIO válido.
            # Publicar un aviso "no pude" en cada slot se volvió spam de push
            # para el dueño. El wake calla; el detalle queda en Actividad/BD.
            logger.info(
                "run_companion_turn: sin novedad, silencio válido "
                "tenant_id=%s user_id=%s wake_key=%s",
                tenant_id,
                user_id,
                wake_key,
            )
            await _log_evento(
                deps,
                tenant_id=tenant_id,
                categoria="companion_wake",
                accion="silencio",
                detalle={
                    "wake_key": wake_key,
                    "source": payload.get("source"),
                    "motivo": "sin_novedad",
                },
            )
            return
        if deserto:
            if require_message and not _es_despertar_de_contenido(payload):
                # Garantía final SOLO para eventos REALES (p. ej. resumen de
                # una llamada): si el motor falló, el dueño recibe el aviso.
                # Los wakes de CONTENIDO (vida_digital) con el motor caído NO
                # empujan: solo log/Actividad — el aviso horario era spam.
                if es_wake_de_reinicio:
                    # El wake de CONTINUIDAD tras un reinicio siempre
                    # confirma que volvió — determinista, sin depender del
                    # modelo (el dueño debe ver que el bot despertó).
                    text = (
                        "Volví del reinicio y sigo operativo. Retomo el hilo "
                        "donde quedé."
                    )
                else:
                    text = (
                        "No pude generar el mensaje de este despertar: el motor de "
                        "lenguaje falló dos veces seguidas. El detalle del evento "
                        "quedó en Actividad."
                    )
                tool_log = tool_log if tool_log is not None else []
                usage = usage if usage is not None else {}
                logger.warning(
                    "run_companion_turn: reintentos agotados, publico aviso honesto "
                    "tenant_id=%s user_id=%s wake_key=%s",
                    tenant_id,
                    user_id,
                    wake_key,
                )
            else:
                logger.info(
                    "run_companion_turn: silencio válido tenant_id=%s user_id=%s wake_key=%s",
                    tenant_id,
                    user_id,
                    wake_key,
                )
                await _log_evento(
                    deps,
                    tenant_id=tenant_id,
                    categoria="companion_wake",
                    accion="silencio",
                    detalle={
                        "wake_key": wake_key,
                        "source": payload.get("source"),
                        "motivo": "motor_caido" if terminal_error else "texto_no_sustantivo",
                        "terminal_error": terminal_error,
                    },
                )
                return

        content: dict[str, Any] = {"text": text.strip()}
        presentation = payload.get("message_presentation")
        if presentation is not None:
            content["presentation"] = presentation
        attached_tool_calls = payload.get("message_tool_calls")
        if attached_tool_calls is not None:
            tool_calls_to_store = attached_tool_calls
        else:
            tool_calls_to_store = tool_log or None

        message = await repo.add_message(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            tool_calls=tool_calls_to_store,
            tokens_out=int(usage.get("completion_tokens") or usage.get("tokens_out") or 0),
            tokens_in=int(
                usage.get("prompt_tokens") or usage.get("tokens_in") or 0
            ),
        )

        # Lo que Edecán YA DIJO en un turno proactivo queda en su memoria
        # permanente (kind=event, source="proactive"): sin esto, el próximo
        # despertar vuelve a proponer lo mismo (mismo comentario de LinkedIn,
        # misma pregunta de vida digital). Determinístico — jamás depende de
        # que el modelo decida guardar; best-effort, un fallo no tumba el turno.
        await _guardar_lo_dicho(
            deps, tenant_id, user_id, text.strip(), wake_key
        )

    raw_notif = payload.get("notification")
    notification_cfg = raw_notif if isinstance(raw_notif, dict) else {}
    notification_kind = str(notification_cfg.get("kind") or "agent_message").strip()
    notification_event_raw = notification_cfg.get("event_id")
    if notification_event_raw:
        try:
            event_id = UUID(str(notification_event_raw))
        except ValueError:
            event_id = stable_event_id(tenant_id=tenant_id, wake_key=wake_key)
    else:
        event_id = stable_event_id(tenant_id=tenant_id, wake_key=wake_key)

    source = str(payload.get("source") or "").strip() or None
    push_cfg = payload.get("push") if isinstance(payload.get("push"), dict) else {}
    preview = format_push_preview(text)
    push_title = str(push_cfg.get("title") or companion_push_title(source)).strip()
    push_body = str(push_cfg.get("body") or preview).strip()

    # UN solo camino: `notify_important_event` es el único que registra el
    # evento en durable + deduplica por event_key + manda APNs con deeplink
    # al chat (`push_data()`). ANTES, si el payload traía `push.category` o
    # `push.data`, se mandaba además un SEGUNDO `enviar_push_a_usuario` →
    # dos APNs idénticos al mismo device (bug reportado: gym-checkin
    # duplicado a 0.5s de diferencia, 11:31:42.915/43.437).
    await notify_important_event(
        deps,
        ImportantNotificationEvent(
            tenant_id=tenant_id,
            user_id=user_id,
            kind=notification_kind,
            event_id=event_id,
            chat_id=conversation_id,
            apns_title=push_title,
            apns_body=push_body,
        ),
    )

    try:
        from edecan_core.queue import enqueue

        await enqueue(
            deps.settings, "memory_consolidate", {"user_id": str(user_id)}, tenant_id
        )
    except Exception:
        logger.warning(
            "run_companion_turn: no se pudo encolar memory_consolidate tenant_id=%s user_id=%s",
            tenant_id,
            user_id,
            exc_info=True,
        )

    logger.info(
        "run_companion_turn: mensaje publicado message_id=%s tenant_id=%s wake_key=%s",
        message.get("id"),
        tenant_id,
        wake_key,
    )
    await _log_evento(
        deps,
        tenant_id=tenant_id,
        categoria="companion_wake",
        accion="mensaje_publicado",
        detalle={
            "wake_key": wake_key,
            "source": source,
            "message_id": str(message.get("id") or ""),
            "conversation_id": str(conversation_id),
            "event_id": str(event_id),
            "kind": notification_kind,
            "push_title": push_title,
            "push_body": push_body,
            "tokens_in": int(usage.get("prompt_tokens") or usage.get("tokens_in") or 0),
            "tokens_out": int(usage.get("completion_tokens") or usage.get("tokens_out") or 0),
        },
    )


__all__ = ["handle", "run_companion_agent_turn"]
