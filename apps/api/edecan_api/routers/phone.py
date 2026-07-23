"""Llamadas entrantes/salientes como canal de una conversación de Edecan.

Las salientes siempre son de dos pasos: `prepare` fija destino/objetivo y
`confirm` exige que el cliente repita ambos y marque las dos verificaciones.
Twilio nunca se invoca durante `prepare`.
"""

from __future__ import annotations

import base64
import logging
import re
import secrets
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
from edecan_core.persona import build_system_prompt
from edecan_core.queue import enqueue
from edecan_db.vault import TokenVault
from edecan_llm.base import ChatMessage, CompletionRequest
from edecan_llm.router import LLMRouter
from edecan_schemas.plans import FLAG_VOICE_TELEPHONY, PLANES
from edecan_voice.stubs import StubTTS
from edecan_voice.telephony import (
    TelephonyError,
    TwilioCredentials,
    TwilioVoiceClient,
    conversation_twiml,
    normalize_e164,
    normalize_goal,
    normalize_twilio_status,
    reject_twiml,
    verify_twilio_signature,
)
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator, model_validator

from edecan_api.config import Settings
from edecan_api.deps import (
    CurrentUser,
    build_key_provider,
    get_current_user,
    get_platform_repo,
    get_platform_vault,
    get_redis,
    get_repo,
    get_vault,
    load_tenant_llm_config,
    rate_limit,
)
from edecan_api.repo import Repo, SqlRepo
from edecan_api.routers.connectors import (
    _configure_twilio_incoming_webhook,
    _verify_twilio_phone_ownership,
)
from edecan_api.routers.persona import persona_from_row
from edecan_api.routers.voice import _estimate_seconds_from_text, _tts_para_tenant

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/phone", tags=["phone"])

TWILIO_CONNECTOR_KEY = "twilio"
ACTIVE_STATUSES = frozenset({"confirmed", "queued", "ringing", "in_progress"})
TERMINAL_STATUSES = frozenset({"completed", "failed", "busy", "no_answer", "cancelled"})
STATUS_ORDER = {"draft": 0, "confirmed": 1, "queued": 2, "ringing": 3, "in_progress": 4}
PHONE_AGENT_TEMPLATE_LIMIT = 20
PHONE_SUMMARY_JOB = "notify_phone_call_summary"
PHONE_INCOMING_JOB = "notify_incoming_phone_call"
PHONE_AUDIO_TTL_SECONDS = 5 * 60

_COMMITMENT_MARKERS = (
    "me comprometo",
    "nos comprometemos",
    "voy a",
    "vamos a",
    "te enviaré",
    "le enviaré",
    "quedamos en",
    "acordamos",
    "confirmo que",
)
_NEXT_STEP_MARKERS = (
    "próximo paso",
    "siguiente paso",
    "mañana",
    "volver a llamar",
    "te contactaré",
    "le contactaré",
    "agendar",
    "enviar",
    "revisar",
    "confirmar",
)


def _template_snapshot(template: dict[str, Any] | None) -> dict[str, Any]:
    if template is None:
        return {
            "agent_template_id": None,
            "agent_template_name": None,
            "agent_name": None,
            "agent_prompt": None,
            "opening_message": None,
            "voice_id": None,
            "agent_operating_profile": None,
        }
    prompt_parts = [str(template["persona_prompt"]).strip()]
    operating_profile = dict(template.get("operating_profile") or {})
    profile_sections = (
        ("funcion_y_mision", "FUNCIÓN Y MISIÓN"),
        ("capabilities", "PROBLEMAS QUE SÍ PUEDES RESOLVER"),
        ("out_of_scope", "PROBLEMAS FUERA DE TU ALCANCE"),
        ("allowed_actions", "ACCIONES QUE SÍ PUEDES REALIZAR"),
        ("prohibited_actions", "LÍMITES Y ACCIONES PROHIBIDAS"),
        ("escalation_rules", "CUÁNDO ESCALAR O TOMAR UN RECADO"),
        ("success_criteria", "CRITERIO DE ÉXITO Y CIERRE"),
    )
    for key, heading in profile_sections:
        content = str(operating_profile.get(key) or "").strip()
        if content:
            prompt_parts.extend([f"<{key}>", f"{heading}:\n{content}", f"</{key}>"])
    knowledge_context = str(template.get("knowledge_context") or "").strip()
    required_information = str(template.get("required_information") or "").strip()
    if knowledge_context:
        prompt_parts.extend(
            [
                "<contexto_autorizado_para_esta_llamada>",
                knowledge_context,
                "</contexto_autorizado_para_esta_llamada>",
            ]
        )
    if required_information:
        prompt_parts.extend(
            [
                "<informacion_que_debes_obtener>",
                required_information,
                "</informacion_que_debes_obtener>",
            ]
        )
    return {
        "agent_template_id": template["id"],
        "agent_template_name": template["name"],
        "agent_name": template["agent_name"],
        "agent_prompt": "\n".join(prompt_parts),
        "opening_message": template["opening_message"] or None,
        "voice_id": template.get("voice_id") or None,
        "agent_operating_profile": operating_profile,
    }


def _monotonic_status(current: str, incoming: str) -> str:
    """Nunca revive ni hace retroceder una llamada por webhooks fuera de orden."""
    if current in TERMINAL_STATUSES:
        return current
    if incoming in TERMINAL_STATUSES:
        return incoming
    if STATUS_ORDER.get(incoming, -1) < STATUS_ORDER.get(current, -1):
        return current
    return incoming


def _dedupe_phrases(values: list[str], *, limit: int = 6) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = " ".join(value.split()).strip(" -\n\t")[:500]
        key = clean.casefold()
        if clean and key not in seen:
            result.append(clean)
            seen.add(key)
        if len(result) >= limit:
            break
    return result


def _sentences(text: str) -> list[str]:
    return [piece for piece in re.split(r"(?<=[.!?])\s+|\n+", text) if piece.strip()]


def _normalize_recipient_name(value: Any) -> str:
    name = " ".join(str(value or "").split()).strip()
    if len(name) < 2:
        raise ValueError("Indica a quién pertenece el número antes de llamar.")
    return name[:160]


def _phone_audio_cache_key(call_id: uuid.UUID, token: str) -> str:
    return f"phone:audio:{call_id}:{token}"


async def _twilio_play_url(
    request: Request,
    *,
    repo: Repo,
    vault: TokenVault | None,
    redis_client: Any,
    call: dict[str, Any],
    text: str,
) -> str | None:
    """Sintetiza una respuesta con el TTS del tenant y la expone brevemente a Twilio.

    El URL contiene un token aleatorio de 256 bits y caduca a los cinco
    minutos. Los bytes viven en Redis/fakeredis, nunca en una ruta pública
    permanente ni en la memoria de otro tenant.
    """
    if vault is None:
        return None
    try:
        tts = await _tts_para_tenant(
            vault,
            repo,
            call["tenant_id"],
            request.app.state.settings,
        )
        if isinstance(tts, StubTTS):
            return None
        audio = await tts.synthesize(text, voice_id=call.get("voice_id") or None)
        token = secrets.token_urlsafe(32)
        await redis_client.set(
            _phone_audio_cache_key(call["id"], token),
            base64.b64encode(audio).decode("ascii"),
            ex=PHONE_AUDIO_TTL_SECONDS,
        )
        await repo.add_usage_event(
            tenant_id=call["tenant_id"],
            kind="voice_seconds",
            quantity=_estimate_seconds_from_text(text),
            meta={"channel": "phone", "call_id": str(call["id"])},
        )
        base_url = request.app.state.settings.PUBLIC_BASE_URL.rstrip("/")
        return f"{base_url}/v1/phone/twilio/calls/{call['id']}/audio/{token}"
    except Exception:
        logger.warning(
            "phone_tts_fallback call_id=%s tenant_id=%s",
            call.get("id"),
            call.get("tenant_id"),
            exc_info=True,
        )
        return None


def _build_phone_call_summary(call: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    """Resumen determinista: siempre disponible, incluso sin LLM o transcripción."""
    turns: list[dict[str, str]] = []
    for event in events:
        if event.get("event_type") != "transcript":
            continue
        payload = event.get("payload") or {}
        role = str(payload.get("role") or "").strip()
        text = " ".join(str(payload.get("text") or "").split()).strip()[:2000]
        if role in {"caller", "assistant"} and text:
            turns.append({"role": role, "text": text})

    caller_turns = [turn["text"] for turn in turns if turn["role"] == "caller"]
    source_turns = caller_turns or [turn["text"] for turn in turns]
    points = _dedupe_phrases(source_turns, limit=5)

    all_sentences = [sentence for turn in turns for sentence in _sentences(turn["text"])]
    commitments = _dedupe_phrases(
        [
            sentence
            for sentence in all_sentences
            if any(marker in sentence.casefold() for marker in _COMMITMENT_MARKERS)
        ],
        limit=5,
    )
    next_steps = _dedupe_phrases(
        [
            sentence
            for sentence in all_sentences
            if any(marker in sentence.casefold() for marker in _NEXT_STEP_MARKERS)
        ],
        limit=5,
    )

    status_value = str(call.get("status") or "failed")
    if not points:
        points = [
            "No hubo transcripción disponible."
            if not turns
            else "La transcripción no contiene intervenciones claras del interlocutor."
        ]
    if not next_steps:
        if status_value == "completed":
            next_steps = ["Revisar el resumen y continuar desde Edecan si hace falta."]
        else:
            next_steps = ["Revisar el estado de la llamada y decidir si conviene reintentarlo."]

    owner_phone = call["from_e164"] if call["direction"] == "outgoing" else call["to_e164"]
    external_phone = call["to_e164"] if call["direction"] == "outgoing" else call["from_e164"]
    return {
        "version": 1,
        "status": status_value,
        "direction": call["direction"],
        "participants": [
            {
                "role": "assistant",
                "name": call.get("agent_name") or "Edecan",
                "phone_e164": owner_phone,
            },
            {"role": "external", "name": None, "phone_e164": external_phone},
        ],
        "duration_seconds": call.get("duration_seconds"),
        "key_points": points,
        "commitments": commitments,
        "next_steps": next_steps,
        "transcript": {"available": bool(turns), "turn_count": len(turns)},
    }


async def _finalize_phone_call_summary(repo: Repo, call: dict[str, Any]) -> bool:
    """Persiste resumen+actividad solo para el primer cierre terminal."""
    if call.get("status") not in TERMINAL_STATUSES or call.get("summary") is not None:
        return False
    events = await repo.list_phone_call_events(tenant_id=call["tenant_id"], call_id=call["id"])
    summary = _build_phone_call_summary(call, events)
    summarized = await repo.set_phone_call_summary_if_absent(
        tenant_id=call["tenant_id"], call_id=call["id"], summary=summary
    )
    if summarized is None:
        return False
    await repo.add_phone_call_event(
        tenant_id=call["tenant_id"],
        call_id=call["id"],
        event_type="activity",
        payload={
            "kind": "phone_call_finished",
            "status": call["status"],
            "direction": call["direction"],
            "summary_available": True,
        },
    )
    return True


async def _enqueue_phone_summary_push(
    settings: Settings, *, tenant_id: uuid.UUID, call_id: uuid.UUID
) -> None:
    """El resumen ya está committed; una cola caída nunca rompe el cierre."""
    try:
        await enqueue(
            settings,
            PHONE_SUMMARY_JOB,
            {"call_id": str(call_id)},
            tenant_id,
        )
    except Exception:
        logger.warning(
            "phone_summary_push_enqueue_failed call_id=%s tenant_id=%s",
            call_id,
            tenant_id,
            exc_info=True,
        )


async def _enqueue_incoming_phone_call_push(
    settings: Settings, *, tenant_id: uuid.UUID, call_id: uuid.UUID
) -> None:
    """La llamada+evento ya hicieron commit; la entrega es best-effort."""
    try:
        await enqueue(
            settings,
            PHONE_INCOMING_JOB,
            {"call_id": str(call_id)},
            tenant_id,
        )
    except Exception:
        logger.warning(
            "incoming_phone_push_enqueue_failed call_id=%s tenant_id=%s",
            call_id,
            tenant_id,
            exc_info=True,
        )


class PhoneGateway(Protocol):
    async def create_call(
        self, *, to_e164: str, voice_url: str, status_callback_url: str
    ) -> Any: ...


RepoTransactionFactory = Callable[[uuid.UUID], Any]
SummaryReadyCallback = Callable[[uuid.UUID, uuid.UUID], Awaitable[None]]


class TransactionalPhoneDispatcher:
    """Persiste y COMMITTEA antes del primer side effect hacia Twilio.

    `repo_transaction` debe ser un context manager que haga commit al salir.
    El estado final del proveedor se escribe en una segunda transacción.
    """

    def __init__(
        self,
        *,
        repo_transaction: RepoTransactionFactory,
        gateway: PhoneGateway,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        public_base_url: str,
        on_summary_ready: SummaryReadyCallback | None = None,
    ) -> None:
        self._repo_transaction = repo_transaction
        self._gateway = gateway
        self._tenant_id = tenant_id
        self._user_id = user_id
        self._public_base = public_base_url.rstrip("/")
        self._on_summary_ready = on_summary_ready

    async def create_and_dispatch(
        self,
        *,
        to_e164: str,
        recipient_name: str,
        goal: str,
        agent_template_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        destination = normalize_e164(to_e164)
        recipient = _normalize_recipient_name(recipient_name)
        normalized_goal = normalize_goal(goal)
        async with self._repo_transaction(self._tenant_id) as repo:
            account = await _twilio_account(repo, self._tenant_id)
            template = await _selected_template(
                repo,
                tenant_id=self._tenant_id,
                user_id=self._user_id,
                template_id=agent_template_id,
            )
            if not await repo.has_phone_consent(
                tenant_id=self._tenant_id, phone_e164=destination, kind="voice"
            ):
                raise TelephonyError(
                    f"Falta consentimiento de voz vigente y verificable para {destination}."
                )
            # Una llamada habla con un tercero no autenticado: siempre usa su
            # propio hilo phone. Nunca hereda el historial personal del chat.
            conversation = await repo.create_conversation(
                tenant_id=self._tenant_id,
                user_id=self._user_id,
                title=f"Llamada a {recipient}",
                channel="phone",
            )
            call = await repo.create_phone_call(
                tenant_id=self._tenant_id,
                user_id=self._user_id,
                conversation_id=conversation["id"],
                direction="outgoing",
                from_e164=account["external_account_id"],
                to_e164=destination,
                recipient_name=recipient,
                goal=normalized_goal,
                status="confirmed",
                **_template_snapshot(template),
            )
            call = await repo.update_phone_call(
                tenant_id=self._tenant_id,
                call_id=call["id"],
                fields={"confirmed_at": datetime.now(UTC)},
            )
            assert call is not None
            await repo.add_message(
                tenant_id=self._tenant_id,
                conversation_id=conversation["id"],
                role="user",
                content={
                    "text": (
                        f"Llama a {recipient} ({destination}) con el agente "
                        f"{template['name']}. Objetivo: {normalized_goal}"
                    )
                },
            )
            await repo.add_phone_call_event(
                tenant_id=self._tenant_id,
                call_id=call["id"],
                event_type="confirmed",
                payload={
                    "destination_verified": True,
                    "recipient_verified": True,
                    "goal_verified": True,
                    "agent_verified": True,
                },
            )
            await repo.add_audit_log(
                tenant_id=self._tenant_id,
                actor_user_id=self._user_id,
                action="phone.call_confirmed",
                target=str(call["id"]),
                meta={
                    "to_e164": destination,
                    "recipient_name": recipient,
                    "goal": normalized_goal,
                    "agent_template_id": str(template["id"]),
                },
            )
        # El context manager ya cerró/committeó aquí. Solo ahora sale red.
        return await self._send_persisted(call)

    async def confirm_and_dispatch(self, *, call_id: uuid.UUID) -> dict[str, Any]:
        async with self._repo_transaction(self._tenant_id) as repo:
            call = await repo.get_phone_call(tenant_id=self._tenant_id, call_id=call_id)
            if call is None or call["user_id"] != self._user_id:
                raise TelephonyError("Llamada no encontrada.")
            if call["status"] != "draft":
                raise TelephonyError("Esta llamada ya fue procesada.")
            if not await repo.has_phone_consent(
                tenant_id=self._tenant_id, phone_e164=call["to_e164"], kind="voice"
            ):
                raise TelephonyError("El consentimiento ya no está vigente.")
            call = await repo.update_phone_call(
                tenant_id=self._tenant_id,
                call_id=call_id,
                fields={"status": "confirmed", "confirmed_at": datetime.now(UTC)},
            )
            assert call is not None
            await repo.add_phone_call_event(
                tenant_id=self._tenant_id,
                call_id=call_id,
                event_type="confirmed",
                payload={
                    "destination_verified": True,
                    "recipient_verified": True,
                    "goal_verified": True,
                    "agent_verified": True,
                },
            )
            await repo.add_audit_log(
                tenant_id=self._tenant_id,
                actor_user_id=self._user_id,
                action="phone.call_confirmed",
                target=str(call_id),
                meta={"to_e164": call["to_e164"], "goal": call["goal"]},
            )
        return await self._send_persisted(call)

    async def _send_persisted(self, call: dict[str, Any]) -> dict[str, Any]:
        call_id = call["id"]
        try:
            provider_call = await self._gateway.create_call(
                to_e164=call["to_e164"],
                voice_url=f"{self._public_base}/v1/phone/twilio/calls/{call_id}/voice",
                status_callback_url=(f"{self._public_base}/v1/phone/twilio/calls/{call_id}/status"),
            )
        except TelephonyError as exc:
            summary_created = False
            async with self._repo_transaction(self._tenant_id) as repo:
                failed_call = await repo.update_phone_call(
                    tenant_id=self._tenant_id,
                    call_id=call_id,
                    fields={
                        "status": "failed",
                        "ended_at": datetime.now(UTC),
                        "error": str(exc),
                    },
                )
                await repo.add_phone_call_event(
                    tenant_id=self._tenant_id,
                    call_id=call_id,
                    event_type="failed",
                    payload={"message": str(exc)},
                )
                assert failed_call is not None
                summary_created = await _finalize_phone_call_summary(repo, failed_call)
            if summary_created and self._on_summary_ready is not None:
                await self._on_summary_ready(self._tenant_id, call_id)
            raise

        async with self._repo_transaction(self._tenant_id) as repo:
            current = await repo.get_phone_call(tenant_id=self._tenant_id, call_id=call_id)
            if current is None:
                raise TelephonyError("La llamada persistida ya no existe.")
            existing_sid = current.get("provider_call_sid")
            if existing_sid and existing_sid != provider_call.sid:
                raise TelephonyError("Twilio devolvió una identidad de llamada inconsistente.")
            # Un webhook puede adelantarse a esta segunda transacción. Nunca
            # degradamos ringing/in_progress/terminal de vuelta a queued.
            advanced = {"ringing", "in_progress", *TERMINAL_STATUSES}
            status_to_store = (
                current["status"] if current.get("status") in advanced else provider_call.status
            )
            updated = await repo.update_phone_call(
                tenant_id=self._tenant_id,
                call_id=call_id,
                fields={
                    "status": status_to_store,
                    "provider_call_sid": provider_call.sid,
                },
            )
            events = await repo.list_phone_call_events(tenant_id=self._tenant_id, call_id=call_id)
            if not any(event.get("event_type") == "provider_queued" for event in events):
                await repo.add_phone_call_event(
                    tenant_id=self._tenant_id,
                    call_id=call_id,
                    event_type="provider_queued",
                    payload={"status": provider_call.status},
                )
        assert updated is not None
        return updated


class PhoneAgentOperatingProfileIn(BaseModel):
    funcion_y_mision: str = Field(min_length=1, max_length=2000)
    capabilities: str = Field(min_length=1, max_length=4000)
    out_of_scope: str = Field(min_length=1, max_length=4000)
    allowed_actions: str = Field(min_length=1, max_length=4000)
    prohibited_actions: str = Field(min_length=1, max_length=4000)
    escalation_rules: str = Field(default="", max_length=3000)
    success_criteria: str = Field(default="", max_length=2000)

    @field_validator(
        "funcion_y_mision",
        "capabilities",
        "out_of_scope",
        "allowed_actions",
        "prohibited_actions",
    )
    @classmethod
    def _required_profile_text(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("Este campo de identidad operativa no puede quedar vacío.")
        return clean

    @field_validator("escalation_rules", "success_criteria")
    @classmethod
    def _optional_profile_text(cls, value: str) -> str:
        return value.strip()


class PhoneAgentTemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    agent_name: str = Field(min_length=1, max_length=80)
    persona_prompt: str = Field(min_length=1, max_length=4000)
    default_goal: str = Field(min_length=1, max_length=500)
    opening_message: str = Field(default="", max_length=700)
    knowledge_context: str = Field(default="", max_length=6000)
    required_information: str = Field(default="", max_length=3000)
    voice_id: str = Field(default="", max_length=200)
    operating_profile: PhoneAgentOperatingProfileIn
    handles_inbound: bool = True
    handles_outbound: bool = True
    is_default: bool = False
    is_inbound_default: bool = False

    @field_validator("name", "agent_name", "persona_prompt", "default_goal")
    @classmethod
    def _required_text(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("Este campo no puede quedar vacío.")
        return clean

    @field_validator(
        "opening_message",
        "knowledge_context",
        "required_information",
        "voice_id",
    )
    @classmethod
    def _optional_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _directions_are_coherent(self) -> PhoneAgentTemplateIn:
        if not self.handles_inbound and not self.handles_outbound:
            raise ValueError("El agente debe atender llamadas entrantes, salientes o ambas.")
        if self.is_default and not self.handles_outbound:
            raise ValueError("El agente saliente predeterminado debe permitir llamadas salientes.")
        if self.is_inbound_default and not self.handles_inbound:
            raise ValueError("El agente entrante predeterminado debe permitir llamadas entrantes.")
        return self


class PrepareCallIn(BaseModel):
    to_e164: str = Field(min_length=1)
    recipient_name: str = Field(min_length=1, max_length=160)
    goal: str | None = Field(default=None, max_length=500)
    conversation_id: uuid.UUID | None = None
    agent_template_id: uuid.UUID | None = None


class ConfirmCallIn(BaseModel):
    expected_to_e164: str = Field(min_length=1)
    expected_recipient_name: str = Field(min_length=1, max_length=160)
    expected_goal: str = Field(min_length=1, max_length=500)
    expected_agent_template_id: uuid.UUID
    confirmed_destination: bool
    confirmed_recipient: bool
    confirmed_goal: bool
    confirmed_agent: bool


def _require_telephony(user: CurrentUser) -> None:
    if not user.tenant.flags.get(FLAG_VOICE_TELEPHONY, False):
        raise HTTPException(status_code=403, detail="La telefonía no está habilitada.")


def _out(row: dict[str, Any], *, events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    result = {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "direction": row["direction"],
        "from_e164": row["from_e164"],
        "to_e164": row["to_e164"],
        "recipient_name": row.get("recipient_name"),
        "goal": row["goal"],
        "agent": (
            {
                "template_id": row.get("agent_template_id"),
                "template_name": row.get("agent_template_name"),
                "name": row.get("agent_name"),
            }
            if row.get("agent_template_name") or row.get("agent_name")
            else None
        ),
        "status": row["status"],
        "confirmed_at": row.get("confirmed_at"),
        "started_at": row.get("started_at"),
        "ended_at": row.get("ended_at"),
        "duration_seconds": row.get("duration_seconds"),
        "error": row.get("error"),
        "summary": row.get("summary"),
        "summary_generated_at": row.get("summary_generated_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }
    if events is not None:
        result["events"] = [
            {
                "id": event["id"],
                "event_type": event["event_type"],
                "payload": event.get("payload") or {},
                "occurred_at": event.get("occurred_at"),
            }
            for event in events
        ]
    return result


def _template_out(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "agent_name": row["agent_name"],
        "persona_prompt": row["persona_prompt"],
        "default_goal": row["default_goal"],
        "opening_message": row.get("opening_message") or "",
        "knowledge_context": row.get("knowledge_context") or "",
        "required_information": row.get("required_information") or "",
        "voice_id": row.get("voice_id") or "",
        "operating_profile": row.get("operating_profile") or {},
        "handles_inbound": bool(row.get("handles_inbound", True)),
        "handles_outbound": bool(row.get("handles_outbound", True)),
        "is_default": bool(row.get("is_default")),
        "is_inbound_default": bool(row.get("is_inbound_default")),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


async def _selected_template(
    repo: Repo,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    template_id: uuid.UUID | None,
) -> dict[str, Any]:
    if template_id is None:
        template = await repo.get_default_phone_agent_template(tenant_id=tenant_id, user_id=user_id)
        if template is None:
            raise HTTPException(
                status_code=409,
                detail="Elige un agente de llamadas antes de continuar.",
            )
        if not template.get("handles_outbound", True):
            raise HTTPException(
                status_code=409,
                detail="El agente predeterminado no está habilitado para llamadas salientes.",
            )
        return template
    template = await repo.get_phone_agent_template(tenant_id=tenant_id, template_id=template_id)
    if template is None or template["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="Agente de llamada no encontrado.")
    if not template.get("handles_outbound", True):
        raise HTTPException(
            status_code=409,
            detail=f"El agente «{template['name']}» no atiende llamadas salientes.",
        )
    return template


async def _twilio_account(repo: Repo, tenant_id: uuid.UUID) -> dict[str, Any]:
    accounts = await repo.list_connector_accounts(tenant_id=tenant_id)
    account = next(
        (
            row
            for row in accounts
            if row.get("connector_key") == TWILIO_CONNECTOR_KEY
            and row.get("status", "active") == "active"
        ),
        None,
    )
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Conecta tu propio número de Twilio en Configuración antes de llamar.",
        )
    return account


async def _credentials(repo: Repo, vault: Any, tenant_id: uuid.UUID) -> TwilioCredentials:
    account = await _twilio_account(repo, tenant_id)
    bundle = await vault.get(tenant_id, account["id"])
    if bundle is None or not bundle.access_token:
        raise HTTPException(
            status_code=409,
            detail="Las credenciales de Twilio no están disponibles.",
        )
    scopes = account.get("scopes") or bundle.scopes or []
    account_sid = str(scopes[0]) if scopes else ""
    try:
        return TwilioCredentials(
            account_sid=account_sid,
            auth_token=bundle.access_token,
            phone_number=account["external_account_id"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


async def get_phone_gateway(
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    vault: Any = Depends(get_vault),
) -> PhoneGateway:
    return TwilioVoiceClient(await _credentials(repo, vault, current_user.tenant_id))


def _repo_transactions(request: Request) -> RepoTransactionFactory:
    override = getattr(request.app.state, "phone_repo_transaction_factory", None)
    if override is not None:
        return override

    @asynccontextmanager
    async def transaction(tenant_id: uuid.UUID) -> AsyncIterator[Repo]:
        async with request.app.state.get_session(tenant_id) as session:
            yield SqlRepo(session)

    return transaction


async def get_phone_dispatcher(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    gateway: PhoneGateway = Depends(get_phone_gateway),
) -> TransactionalPhoneDispatcher:
    async def summary_ready(tenant_id: uuid.UUID, call_id: uuid.UUID) -> None:
        await _enqueue_phone_summary_push(
            request.app.state.settings, tenant_id=tenant_id, call_id=call_id
        )

    return TransactionalPhoneDispatcher(
        repo_transaction=_repo_transactions(request),
        gateway=gateway,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        public_base_url=request.app.state.settings.PUBLIC_BASE_URL,
        on_summary_ready=summary_ready,
    )


def phone_tool_dispatcher_for(
    *,
    request: Request,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    repo: Repo,
    vault: Any,
) -> Callable[..., Any]:
    """Closure que la tool usa sin importar `apps/api`; cada ejecución abre
    una transacción independiente, la cierra y recién entonces llama Twilio."""

    async def dispatch(
        *,
        to_e164: str,
        recipient_name: str,
        goal: str,
        agent_ref: str,
    ) -> dict[str, Any]:
        try:
            if not agent_ref.strip():
                raise TelephonyError(
                    "Falta elegir el agente de llamada. Pregunta cuál debe usar antes de llamar."
                )
            templates = await repo.list_phone_agent_templates(tenant_id=tenant_id, user_id=user_id)
            reference = " ".join(agent_ref.split()).casefold()
            exact = [
                item
                for item in templates
                if reference
                in {
                    str(item.get("name") or "").casefold(),
                    str(item.get("agent_name") or "").casefold(),
                }
            ]
            candidates = exact or [
                item
                for item in templates
                if reference in str(item.get("name") or "").casefold()
                or reference in str(item.get("agent_name") or "").casefold()
            ]
            if len(candidates) != 1:
                available = ", ".join(str(item["name"]) for item in templates) or "ninguno"
                if not candidates:
                    raise TelephonyError(
                        f"No encontré el agente «{agent_ref}». Agentes disponibles: {available}."
                    )
                matches = ", ".join(str(item["name"]) for item in candidates)
                raise TelephonyError(
                    f"«{agent_ref}» coincide con varios agentes: {matches}. "
                    "Indica el nombre exacto."
                )
            selected = candidates[0]
            if not selected.get("handles_outbound", True):
                raise TelephonyError(
                    f"El agente «{selected['name']}» no está habilitado para llamadas salientes."
                )
            selected_id = selected["id"]
            gateway = TwilioVoiceClient(await _credentials(repo, vault, tenant_id))
            service = TransactionalPhoneDispatcher(
                repo_transaction=_repo_transactions(request),
                gateway=gateway,
                tenant_id=tenant_id,
                user_id=user_id,
                public_base_url=request.app.state.settings.PUBLIC_BASE_URL,
                on_summary_ready=lambda resolved_tenant_id, call_id: _enqueue_phone_summary_push(
                    request.app.state.settings,
                    tenant_id=resolved_tenant_id,
                    call_id=call_id,
                ),
            )
            return await service.create_and_dispatch(
                to_e164=to_e164,
                recipient_name=recipient_name,
                goal=goal,
                agent_template_id=selected_id,
            )
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else "Telefonía no configurada."
            raise TelephonyError(detail) from exc

    return dispatch


async def _webhook_auth_token(request: Request, repo: Repo, tenant_id: uuid.UUID) -> str:
    override = getattr(request.app.state, "phone_webhook_token_loader", None)
    if override is not None:
        value = override(tenant_id)
        if hasattr(value, "__await__"):
            value = await value
        return str(value)

    get_session = request.app.state.get_session
    async with get_session(None) as session:
        vault = TokenVault(session, build_key_provider(request.app.state.settings))
        credentials = await _credentials(repo, vault, tenant_id)
        return credentials.auth_token


async def _form(request: Request) -> dict[str, str]:
    form = await request.form()
    return {str(key): str(value) for key, value in form.multi_items()}


async def _verify_webhook(
    request: Request,
    *,
    params: dict[str, str],
    repo: Repo,
    tenant_id: uuid.UUID,
) -> None:
    token = await _webhook_auth_token(request, repo, tenant_id)
    public_url = f"{request.app.state.settings.PUBLIC_BASE_URL.rstrip('/')}{request.url.path}"
    if request.url.query:
        public_url += f"?{request.url.query}"
    if not verify_twilio_signature(
        url=public_url,
        params=params,
        auth_token=token,
        supplied_signature=request.headers.get("X-Twilio-Signature"),
    ):
        raise HTTPException(status_code=403, detail="Firma de Twilio inválida.")


@router.get("/agent-templates", dependencies=[Depends(rate_limit)])
async def list_phone_agent_templates(
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> list[dict[str, Any]]:
    _require_telephony(current_user)
    rows = await repo.list_phone_agent_templates(
        tenant_id=current_user.tenant_id, user_id=current_user.user_id
    )
    return [_template_out(row) for row in rows]


@router.post(
    "/agent-templates",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit)],
)
async def create_phone_agent_template(
    body: PhoneAgentTemplateIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    _require_telephony(current_user)
    existing = await repo.list_phone_agent_templates(
        tenant_id=current_user.tenant_id, user_id=current_user.user_id
    )
    if len(existing) >= PHONE_AGENT_TEMPLATE_LIMIT:
        raise HTTPException(
            status_code=409,
            detail=f"Puedes guardar hasta {PHONE_AGENT_TEMPLATE_LIMIT} agentes de llamada.",
        )
    if any(row["name"].casefold() == body.name.casefold() for row in existing):
        raise HTTPException(status_code=409, detail="Ya tienes un agente con ese nombre.")

    make_default = body.is_default or (not existing and body.handles_outbound)
    make_inbound_default = body.is_inbound_default or (not existing and body.handles_inbound)
    if make_default:
        await repo.clear_default_phone_agent_template(
            tenant_id=current_user.tenant_id, user_id=current_user.user_id
        )
    if make_inbound_default:
        await repo.clear_inbound_phone_agent_template(
            tenant_id=current_user.tenant_id, user_id=current_user.user_id
        )
    created = await repo.create_phone_agent_template(
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        name=body.name,
        agent_name=body.agent_name,
        persona_prompt=body.persona_prompt,
        default_goal=body.default_goal,
        opening_message=body.opening_message,
        knowledge_context=body.knowledge_context,
        required_information=body.required_information,
        voice_id=body.voice_id or None,
        operating_profile=body.operating_profile.model_dump(),
        handles_inbound=body.handles_inbound,
        handles_outbound=body.handles_outbound,
        is_default=make_default,
        is_inbound_default=make_inbound_default,
    )
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="phone.agent_template_created",
        target=str(created["id"]),
        meta={
            "name": created["name"],
            "is_default": make_default,
            "is_inbound_default": make_inbound_default,
        },
    )
    return _template_out(created)


@router.put("/agent-templates/{template_id}", dependencies=[Depends(rate_limit)])
async def update_phone_agent_template(
    template_id: uuid.UUID,
    body: PhoneAgentTemplateIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    _require_telephony(current_user)
    current = await repo.get_phone_agent_template(
        tenant_id=current_user.tenant_id, template_id=template_id
    )
    if current is None or current["user_id"] != current_user.user_id:
        raise HTTPException(status_code=404, detail="Agente de llamada no encontrado.")
    existing = await repo.list_phone_agent_templates(
        tenant_id=current_user.tenant_id, user_id=current_user.user_id
    )
    if any(
        row["id"] != template_id and row["name"].casefold() == body.name.casefold()
        for row in existing
    ):
        raise HTTPException(status_code=409, detail="Ya tienes un agente con ese nombre.")
    if body.is_default:
        await repo.clear_default_phone_agent_template(
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            except_id=template_id,
        )
    if body.is_inbound_default:
        await repo.clear_inbound_phone_agent_template(
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            except_id=template_id,
        )
    updated = await repo.update_phone_agent_template(
        tenant_id=current_user.tenant_id,
        template_id=template_id,
        fields={
            "name": body.name,
            "agent_name": body.agent_name,
            "persona_prompt": body.persona_prompt,
            "default_goal": body.default_goal,
            "opening_message": body.opening_message,
            "knowledge_context": body.knowledge_context,
            "required_information": body.required_information,
            "voice_id": body.voice_id or None,
            "operating_profile": body.operating_profile.model_dump(),
            "handles_inbound": body.handles_inbound,
            "handles_outbound": body.handles_outbound,
            "is_default": body.is_default,
            "is_inbound_default": body.is_inbound_default,
        },
    )
    assert updated is not None
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="phone.agent_template_updated",
        target=str(template_id),
        meta={
            "name": updated["name"],
            "is_default": bool(updated["is_default"]),
            "is_inbound_default": bool(updated["is_inbound_default"]),
        },
    )
    return _template_out(updated)


@router.delete("/agent-templates/{template_id}", dependencies=[Depends(rate_limit)])
async def delete_phone_agent_template(
    template_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> Response:
    _require_telephony(current_user)
    current = await repo.get_phone_agent_template(
        tenant_id=current_user.tenant_id, template_id=template_id
    )
    if current is None or current["user_id"] != current_user.user_id:
        raise HTTPException(status_code=404, detail="Agente de llamada no encontrado.")
    deleted = await repo.delete_phone_agent_template(
        tenant_id=current_user.tenant_id, template_id=template_id
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Agente de llamada no encontrado.")
    remaining = await repo.list_phone_agent_templates(
        tenant_id=current_user.tenant_id, user_id=current_user.user_id
    )
    if current.get("is_default"):
        outbound = next((item for item in remaining if item.get("handles_outbound", True)), None)
        if outbound:
            await repo.update_phone_agent_template(
                tenant_id=current_user.tenant_id,
                template_id=outbound["id"],
                fields={"is_default": True},
            )
    if current.get("is_inbound_default"):
        inbound = next((item for item in remaining if item.get("handles_inbound", True)), None)
        if inbound:
            await repo.update_phone_agent_template(
                tenant_id=current_user.tenant_id,
                template_id=inbound["id"],
                fields={"is_inbound_default": True},
            )
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="phone.agent_template_deleted",
        target=str(template_id),
        meta={"name": current["name"]},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/calls", dependencies=[Depends(rate_limit)])
async def list_calls(
    current_user: CurrentUser = Depends(get_current_user), repo: Repo = Depends(get_repo)
) -> list[dict[str, Any]]:
    _require_telephony(current_user)
    rows = await repo.list_phone_calls(
        tenant_id=current_user.tenant_id, user_id=current_user.user_id
    )
    return [_out(row) for row in rows]


@router.post("/incoming/setup", dependencies=[Depends(rate_limit)])
async def setup_incoming_calls(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
) -> dict[str, Any]:
    """Configura o repara en Twilio la recepción de llamadas de este número."""
    _require_telephony(current_user)
    inbound_agent = await repo.get_inbound_phone_agent_template(
        tenant_id=current_user.tenant_id, user_id=current_user.user_id
    )
    if inbound_agent is None:
        raise HTTPException(
            status_code=409,
            detail="Elige primero qué agente atenderá las llamadas entrantes.",
        )
    credentials = await _credentials(repo, vault, current_user.tenant_id)
    async with httpx.AsyncClient(timeout=12.0) as http_client:
        phone_sid = await _verify_twilio_phone_ownership(
            credentials.account_sid,
            credentials.auth_token,
            credentials.phone_number,
            http_client=http_client,
        )
        incoming_url = (
            f"{request.app.state.settings.PUBLIC_BASE_URL.rstrip('/')}/v1/phone/twilio/incoming"
        )
        await _configure_twilio_incoming_webhook(
            credentials.account_sid,
            credentials.auth_token,
            phone_sid,
            incoming_url,
            http_client=http_client,
        )
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="phone.incoming_configured",
        target=credentials.phone_number,
        meta={"provider": "twilio"},
    )
    return {
        "status": "ready",
        "phone_number": credentials.phone_number,
        "agent_name": inbound_agent["agent_name"],
        "agent_template_name": inbound_agent["name"],
    }


@router.get("/calls/{call_id}", dependencies=[Depends(rate_limit)])
async def get_call(
    call_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    _require_telephony(current_user)
    row = await repo.get_phone_call(tenant_id=current_user.tenant_id, call_id=call_id)
    if row is None or row["user_id"] != current_user.user_id:
        raise HTTPException(status_code=404, detail="Llamada no encontrada.")
    events = await repo.list_phone_call_events(tenant_id=current_user.tenant_id, call_id=call_id)
    return _out(row, events=events)


@router.post(
    "/calls/prepare",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit)],
)
async def prepare_call(
    body: PrepareCallIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    _require_telephony(current_user)
    template = await _selected_template(
        repo,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        template_id=body.agent_template_id,
    )
    try:
        destination = normalize_e164(body.to_e164)
        recipient_name = _normalize_recipient_name(body.recipient_name)
        goal = normalize_goal(body.goal or template.get("default_goal"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    account = await _twilio_account(repo, current_user.tenant_id)
    if not await repo.has_phone_consent(
        tenant_id=current_user.tenant_id, phone_e164=destination, kind="voice"
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "No hay consentimiento de voz vigente para ese destinatario. "
                "Registra la evidencia antes de preparar la llamada."
            ),
        )

    if body.conversation_id is not None:
        conversation = await repo.get_conversation(
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            conversation_id=body.conversation_id,
        )
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversación no encontrada.")
    else:
        conversation = await repo.create_conversation(
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            title=f"Llamada a {recipient_name}",
            channel="phone",
        )

    call = await repo.create_phone_call(
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        conversation_id=conversation["id"],
        direction="outgoing",
        from_e164=account["external_account_id"],
        to_e164=destination,
        recipient_name=recipient_name,
        goal=goal,
        **_template_snapshot(template),
    )
    await repo.add_phone_call_event(
        tenant_id=current_user.tenant_id,
        call_id=call["id"],
        event_type="prepared",
        payload={
            "destination_verified": False,
            "recipient_verified": False,
            "goal_verified": False,
            "agent_verified": False,
            "agent_template_id": str(template["id"]),
        },
    )
    await repo.add_message(
        tenant_id=current_user.tenant_id,
        conversation_id=conversation["id"],
        role="user",
        content={
            "text": (
                f"Llama a {recipient_name} ({destination}) con el agente "
                f"{template['name']}. Objetivo: {goal}"
            )
        },
    )
    return {
        **_out(call),
        "requires_confirmation": True,
        "verification": {
            "to_e164": destination,
            "recipient_name": recipient_name,
            "goal": goal,
            "agent_template_id": str(template["id"]),
            "agent_template_name": template["name"],
            "agent_name": template["agent_name"],
        },
    }


@router.post("/calls/{call_id}/confirm", dependencies=[Depends(rate_limit)])
async def confirm_call(
    call_id: uuid.UUID,
    body: ConfirmCallIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    dispatcher: TransactionalPhoneDispatcher = Depends(get_phone_dispatcher),
) -> dict[str, Any]:
    _require_telephony(current_user)
    call = await repo.get_phone_call(tenant_id=current_user.tenant_id, call_id=call_id)
    if call is None or call["user_id"] != current_user.user_id:
        raise HTTPException(status_code=404, detail="Llamada no encontrada.")
    if call["status"] != "draft":
        raise HTTPException(status_code=409, detail="Esta llamada ya fue procesada.")
    if not all(
        (
            body.confirmed_destination,
            body.confirmed_recipient,
            body.confirmed_goal,
            body.confirmed_agent,
        )
    ):
        raise HTTPException(
            status_code=422,
            detail="Confirma explícitamente la persona, el número, el agente y el objetivo.",
        )
    try:
        expected_destination = normalize_e164(body.expected_to_e164)
        expected_recipient = _normalize_recipient_name(body.expected_recipient_name)
        expected_goal = normalize_goal(body.expected_goal)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if (
        expected_destination != call["to_e164"]
        or expected_recipient.casefold() != str(call.get("recipient_name") or "").casefold()
        or expected_goal != call["goal"]
        or body.expected_agent_template_id != call.get("agent_template_id")
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "La persona, el número, el agente o el objetivo cambiaron. "
                "Revisa de nuevo antes de llamar."
            ),
        )
    if not await repo.has_phone_consent(
        tenant_id=current_user.tenant_id, phone_e164=call["to_e164"], kind="voice"
    ):
        raise HTTPException(status_code=409, detail="El consentimiento ya no está vigente.")

    try:
        updated = await dispatcher.confirm_and_dispatch(call_id=call_id)
    except TelephonyError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _out(updated)


@router.delete("/calls/{call_id}", dependencies=[Depends(rate_limit)])
async def cancel_draft(
    call_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    call = await repo.get_phone_call(tenant_id=current_user.tenant_id, call_id=call_id)
    if call is None or call["user_id"] != current_user.user_id:
        raise HTTPException(status_code=404, detail="Llamada no encontrada.")
    if call["status"] != "draft":
        raise HTTPException(
            status_code=409,
            detail="Solo se puede cancelar aquí una llamada que todavía no salió a Twilio.",
        )
    updated = await repo.update_phone_call(
        tenant_id=current_user.tenant_id,
        call_id=call_id,
        fields={"status": "cancelled", "ended_at": datetime.now(UTC)},
    )
    await repo.add_phone_call_event(
        tenant_id=current_user.tenant_id, call_id=call_id, event_type="cancelled"
    )
    assert updated is not None
    return _out(updated)


def _external_phone_persona(row: dict[str, Any] | None) -> Any:
    """Persona segura para interlocutores no autenticados.

    Conserva identidad/idioma/tono/formalidad, pero no memorias, rasgos,
    instrucciones privadas ni el estilo de relación del propietario.
    """
    persona = persona_from_row(row)
    return persona.model_copy(
        update={
            "instrucciones": "",
            "rasgos": [],
            "memoria_activada": False,
            "estilo_relacion": "profesional",
            "adulto_confirmado": False,
            "consentimiento_romantico": False,
        }
    )


def _phone_operating_context(call: dict[str, Any]) -> str:
    """Contexto de llamada aislado, con reglas duras después de la plantilla."""
    parts = [
        "Estás hablando por teléfono. Responde con frases breves y naturales, sin Markdown.",
    ]
    if call.get("agent_prompt"):
        parts.extend(
            [
                f"Perfil seleccionado: {call.get('agent_template_name') or 'Agente de llamada'}.",
                (
                    f"Tu identidad durante esta llamada es "
                    f"{call.get('agent_name') or call.get('agent_template_name')}. "
                    "No adoptes la personalidad de otro agente ni la identidad general de Edecan."
                ),
                "<instrucciones_agente_llamada>",
                str(call["agent_prompt"]),
                "</instrucciones_agente_llamada>",
            ]
        )
    parts.extend(
        [
            f"Objetivo de esta llamada: {call['goal']}.",
            (
                f"Persona destinataria indicada por el propietario: "
                f"{call.get('recipient_name') or 'no identificada'}."
            ),
            (
                "La plantilla define tono, argumentos y preguntas, pero nunca autoriza acciones "
                "sensibles. No ejecutes ni afirmes que enviaste, compraste, reservaste, cambiaste "
                "datos o asumiste compromisos; deja cualquier acción adicional pendiente de "
                "confirmación en la app. Identifícate honestamente como asistente automatizado."
            ),
            (
                "No reveles memorias privadas del propietario. Solo puedes usar frente al "
                "interlocutor el contexto incluido explícitamente dentro de "
                "<contexto_autorizado_para_esta_llamada>."
            ),
        ]
    )
    return "\n".join(parts)


async def _phone_reply(
    request: Request,
    *,
    call: dict[str, Any],
    repo: Repo,
    speech: str,
) -> str:
    override = getattr(request.app.state, "phone_turn_runner", None)
    if override is not None:
        result = override(call, speech)
        if hasattr(result, "__await__"):
            result = await result
        return normalize_goal(result, max_chars=700)

    get_session = request.app.state.get_session
    settings: Settings = request.app.state.settings
    tenant_id = call["tenant_id"]
    async with get_session(tenant_id) as session:
        tenant_repo = SqlRepo(session)
        tenant = await tenant_repo.get_tenant(tenant_id)
        plan = PLANES.get((tenant or {}).get("plan_key", ""), PLANES["free_selfhost"])
        plan_flags = dict(plan.flags)
        provider_config = await load_tenant_llm_config(session, settings, tenant_id)
        if provider_config is None:
            return "He guardado tu respuesta. El propietario podrá verla y continuar desde Edecan."
        llm = LLMRouter(settings, on_usage=None, provider_config=provider_config)
        rows = await tenant_repo.list_messages(
            tenant_id=tenant_id, conversation_id=call["conversation_id"], limit=20
        )
        persona_row = await tenant_repo.get_persona(tenant_id=tenant_id, user_id=call["user_id"])
        persona = _external_phone_persona(persona_row).model_copy(
            update={
                "nombre_asistente": str(
                    call.get("agent_name") or call.get("agent_template_name") or "Edecan"
                )
            }
        )
        # El interlocutor telefónico no está autenticado como propietario.
        # Se conserva la persona/tono, pero jamás se expone memoria privada.
        memories: list[str] = []
        messages: list[ChatMessage] = []
        for row in rows:
            content = row.get("content")
            text = content.get("text", "") if isinstance(content, dict) else str(content or "")
            role = row.get("role")
            if role in {"user", "assistant"} and text:
                messages.append(ChatMessage(role=role, content=text))
        response = await llm.complete(
            "rapido",
            plan_flags,
            CompletionRequest(
                model="",
                system=build_system_prompt(
                    persona,
                    memories,
                    extra_context=_phone_operating_context(call),
                ),
                messages=messages,
                max_tokens=180,
                temperature=0.3,
            ),
        )
        await tenant_repo.add_usage_event(
            tenant_id=tenant_id,
            kind="llm_tokens",
            quantity=float(response.usage.input_tokens + response.usage.output_tokens),
            meta={"conversation_id": str(call["conversation_id"]), "channel": "phone"},
        )
        return normalize_goal(response.text, max_chars=700)


@router.post("/twilio/calls/{call_id}/voice")
async def outgoing_voice(
    call_id: uuid.UUID,
    request: Request,
    repo: Repo = Depends(get_platform_repo),
    vault: TokenVault | None = Depends(get_platform_vault),
    redis_client: Any = Depends(get_redis),
) -> Response:
    params = await _form(request)
    call_sid = params.get("CallSid", "")
    call = await repo.get_phone_call_by_provider_sid(provider_call_sid=call_sid)
    if call is None:
        call = await repo.get_phone_call_global(call_id=call_id)
    if call is None:
        return Response(
            reject_twiml("Esta llamada ya no está disponible."),
            media_type="application/xml",
        )
    if call["id"] != call_id:
        raise HTTPException(status_code=404, detail="Llamada no encontrada.")
    await _verify_webhook(request, params=params, repo=repo, tenant_id=call["tenant_id"])
    base_url = request.app.state.settings.PUBLIC_BASE_URL.rstrip("/")
    gather_url = f"{base_url}/v1/phone/twilio/calls/{call_id}/gather"
    persona = _external_phone_persona(
        await repo.get_persona(tenant_id=call["tenant_id"], user_id=call["user_id"])
    )
    agent_name = call.get("agent_name") or persona.nombre_asistente
    identity = f"Hola. Soy {agent_name}, un asistente automatizado."
    opening = str(call.get("opening_message") or "").strip()
    purpose = opening or f"El motivo es: {call['goal']}"
    message = f"{identity} {purpose}"
    play_url = await _twilio_play_url(
        request,
        repo=repo,
        vault=vault,
        redis_client=redis_client,
        call=call,
        text=message,
    )
    return Response(
        conversation_twiml(message=message, gather_url=gather_url, play_url=play_url),
        media_type="application/xml",
    )


@router.post("/twilio/incoming")
async def incoming_voice(
    request: Request,
    repo: Repo = Depends(get_platform_repo),
    vault: TokenVault | None = Depends(get_platform_vault),
    redis_client: Any = Depends(get_redis),
) -> Response:
    """Resuelve el tenant por su número Twilio y abre una conversación telefónica."""
    params = await _form(request)
    try:
        to_e164 = normalize_e164(params.get("To"))
        from_e164 = normalize_e164(params.get("From"))
    except ValueError:
        return Response(
            reject_twiml("No pudimos identificar esta llamada."),
            media_type="application/xml",
        )
    account = await repo.get_connector_account_by_external_id(
        connector_key=TWILIO_CONNECTOR_KEY, external_account_id=to_e164
    )
    if account is None:
        return Response(
            reject_twiml("Este número no está conectado a Edecan."),
            media_type="application/xml",
        )
    tenant_id = account["tenant_id"]
    await _verify_webhook(request, params=params, repo=repo, tenant_id=tenant_id)

    call_sid = params.get("CallSid", "")
    call = await repo.get_phone_call_by_provider_sid(provider_call_sid=call_sid)
    call_created = False
    if call is None:
        # La transacción cierra antes de encolar. Así el worker solo puede
        # notificar una llamada y un evento `incoming` ya visibles y durables.
        async with _repo_transactions(request)(tenant_id) as tenant_repo:
            call = await tenant_repo.get_phone_call_by_provider_sid(provider_call_sid=call_sid)
            if call is None:
                user_id = await tenant_repo.get_first_user_id_for_tenant(tenant_id)
                if user_id is None:
                    return Response(
                        reject_twiml("Esta cuenta todavía no tiene una persona responsable."),
                        media_type="application/xml",
                    )
                inbound_template = await tenant_repo.get_inbound_phone_agent_template(
                    tenant_id=tenant_id, user_id=user_id
                )
                if inbound_template is None:
                    return Response(
                        reject_twiml(
                            "Este número todavía no tiene un agente configurado "
                            "para recibir llamadas."
                        ),
                        media_type="application/xml",
                    )
                conversation = await tenant_repo.create_conversation(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    title=f"Llamada de {from_e164}",
                    channel="phone",
                )
                call = await tenant_repo.create_phone_call(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    conversation_id=conversation["id"],
                    direction="incoming",
                    from_e164=from_e164,
                    to_e164=to_e164,
                    goal="Atender la llamada entrante y ayudar a la persona.",
                    status="in_progress",
                    provider_call_sid=call_sid,
                    **_template_snapshot(inbound_template),
                )
                await tenant_repo.add_phone_call_event(
                    tenant_id=tenant_id,
                    call_id=call["id"],
                    event_type="incoming",
                    payload={"status": "in_progress"},
                )
                call_created = True

    assert call is not None
    if call_created:
        await _enqueue_incoming_phone_call_push(
            request.app.state.settings,
            tenant_id=tenant_id,
            call_id=call["id"],
        )

    gather_url = (
        f"{request.app.state.settings.PUBLIC_BASE_URL.rstrip('/')}/v1/phone/twilio/"
        f"calls/{call['id']}/gather"
    )
    persona = _external_phone_persona(
        await repo.get_persona(tenant_id=tenant_id, user_id=call["user_id"])
    )
    agent_name = call.get("agent_name") or persona.nombre_asistente
    opening = str(call.get("opening_message") or "").strip()
    welcome = (
        opening
        or "Esta conversación quedará registrada para poder ayudarte. ¿En qué puedo ayudarte?"
    )
    message = f"Hola, soy {agent_name}, un asistente automatizado. {welcome}"
    play_url = await _twilio_play_url(
        request,
        repo=repo,
        vault=vault,
        redis_client=redis_client,
        call=call,
        text=message,
    )
    return Response(
        conversation_twiml(
            message=message,
            gather_url=gather_url,
            play_url=play_url,
        ),
        media_type="application/xml",
    )


@router.post("/twilio/calls/{call_id}/gather")
async def gather_turn(
    call_id: uuid.UUID,
    request: Request,
    repo: Repo = Depends(get_platform_repo),
    vault: TokenVault | None = Depends(get_platform_vault),
    redis_client: Any = Depends(get_redis),
) -> Response:
    params = await _form(request)
    call = await repo.get_phone_call_by_provider_sid(provider_call_sid=params.get("CallSid", ""))
    if call is None:
        call = await repo.get_phone_call_global(call_id=call_id)
    if call is None:
        raise HTTPException(status_code=404, detail="Llamada no encontrada.")
    if call["id"] != call_id:
        raise HTTPException(status_code=404, detail="Llamada no encontrada.")
    await _verify_webhook(request, params=params, repo=repo, tenant_id=call["tenant_id"])
    speech = " ".join(params.get("SpeechResult", "").split())[:2000]
    base_url = request.app.state.settings.PUBLIC_BASE_URL.rstrip("/")
    gather_url = f"{base_url}/v1/phone/twilio/calls/{call_id}/gather"
    if not speech:
        return Response(
            conversation_twiml(message="No alcancé a escucharte.", gather_url=gather_url),
            media_type="application/xml",
        )
    previous_events = await repo.list_phone_call_events(
        tenant_id=call["tenant_id"], call_id=call_id
    )
    previous_caller_turns = sum(
        1
        for event in previous_events
        if event.get("event_type") == "transcript"
        and (event.get("payload") or {}).get("role") == "caller"
    )
    max_turns = max(1, int(getattr(request.app.state.settings, "PHONE_MAX_TURNS", 8)))
    is_last_turn = previous_caller_turns + 1 >= max_turns
    await repo.add_phone_call_event(
        tenant_id=call["tenant_id"],
        call_id=call_id,
        event_type="transcript",
        payload={"role": "caller", "text": speech},
    )
    await repo.add_message(
        tenant_id=call["tenant_id"],
        conversation_id=call["conversation_id"],
        role="user",
        content={"text": speech},
    )
    try:
        reply = await _phone_reply(request, call=call, repo=repo, speech=speech)
    except Exception:
        logger.warning(
            "phone_turn_failed call_id=%s tenant_id=%s error_type=%s",
            call_id,
            call["tenant_id"],
            "provider_or_memory_error",
        )
        reply = "Guardé tu respuesta. El propietario podrá verla y continuar desde su Edecan."
        await repo.add_phone_call_event(
            tenant_id=call["tenant_id"],
            call_id=call_id,
            event_type="assistant_error",
            payload={"code": "PHONE_TURN_FAILED"},
        )
    if is_last_turn:
        reply = f"{reply} Gracias por llamar. Para controlar la duración, terminaremos aquí."
    await repo.add_message(
        tenant_id=call["tenant_id"],
        conversation_id=call["conversation_id"],
        role="assistant",
        content={"text": reply},
    )
    await repo.add_phone_call_event(
        tenant_id=call["tenant_id"],
        call_id=call_id,
        event_type="transcript",
        payload={"role": "assistant", "text": reply},
    )
    play_url = await _twilio_play_url(
        request,
        repo=repo,
        vault=vault,
        redis_client=redis_client,
        call=call,
        text=reply,
    )
    return Response(
        conversation_twiml(
            message=reply,
            gather_url=gather_url,
            end_after_message=is_last_turn,
            play_url=play_url,
        ),
        media_type="application/xml",
    )


@router.get("/twilio/calls/{call_id}/audio/{token}")
async def phone_audio(
    call_id: uuid.UUID,
    token: str,
    redis_client: Any = Depends(get_redis),
) -> Response:
    """Entrega a Twilio audio efímero mediante un token opaco de alta entropía."""
    if len(token) < 32 or len(token) > 100:
        raise HTTPException(status_code=404, detail="Audio no encontrado.")
    encoded = await redis_client.get(_phone_audio_cache_key(call_id, token))
    if not encoded:
        raise HTTPException(status_code=404, detail="Audio no encontrado.")
    try:
        audio = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Audio no encontrado.") from None
    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={
            "Cache-Control": "private, max-age=240",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/twilio/calls/{call_id}/status", status_code=204)
async def call_status(
    call_id: uuid.UUID, request: Request, repo: Repo = Depends(get_platform_repo)
) -> None:
    params = await _form(request)
    call = await repo.get_phone_call_by_provider_sid(provider_call_sid=params.get("CallSid", ""))
    if call is None:
        call = await repo.get_phone_call_global(call_id=call_id)
    if call is None:
        raise HTTPException(status_code=404, detail="Llamada no encontrada.")
    if call["id"] != call_id:
        raise HTTPException(status_code=404, detail="Llamada no encontrada.")
    await _verify_webhook(request, params=params, repo=repo, tenant_id=call["tenant_id"])
    provider_status = normalize_twilio_status(params.get("CallStatus"))
    summary_created = False
    # Esta transacción termina antes de encolar el push. Así el worker nunca
    # observa un job cuyo resumen todavía no sea visible en PostgreSQL.
    async with _repo_transactions(request)(call["tenant_id"]) as tenant_repo:
        current = await tenant_repo.get_phone_call(tenant_id=call["tenant_id"], call_id=call_id)
        if current is None:
            raise HTTPException(status_code=404, detail="Llamada no encontrada.")
        effective_status = _monotonic_status(current["status"], provider_status)
        fields: dict[str, Any] = {"status": effective_status}
        if not current.get("provider_call_sid") and params.get("CallSid"):
            fields["provider_call_sid"] = params["CallSid"]
        if effective_status == "in_progress" and current.get("started_at") is None:
            fields["started_at"] = datetime.now(UTC)
        if effective_status in TERMINAL_STATUSES:
            if current.get("ended_at") is None:
                fields["ended_at"] = datetime.now(UTC)
            if (
                effective_status == "completed"
                and current.get("duration_seconds") is None
                and params.get("CallDuration", "").isdigit()
            ):
                fields["duration_seconds"] = int(params["CallDuration"])
        updated = await tenant_repo.update_phone_call(
            tenant_id=call["tenant_id"], call_id=call_id, fields=fields
        )
        assert updated is not None
        await tenant_repo.add_phone_call_event(
            tenant_id=call["tenant_id"],
            call_id=call_id,
            event_type="status",
            payload={"status": effective_status, "provider_status": provider_status},
        )
        summary_created = await _finalize_phone_call_summary(tenant_repo, updated)

    if summary_created:
        await _enqueue_phone_summary_push(
            request.app.state.settings,
            tenant_id=call["tenant_id"],
            call_id=call_id,
        )
