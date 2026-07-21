"""Llamadas entrantes/salientes como canal de una conversación de Edecan.

Las salientes siempre son de dos pasos: `prepare` fija destino/objetivo y
`confirm` exige que el cliente repita ambos y marque las dos verificaciones.
Twilio nunca se invoca durante `prepare`.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Protocol

from edecan_core.persona import build_system_prompt
from edecan_db.vault import TokenVault
from edecan_llm.base import ChatMessage, CompletionRequest
from edecan_llm.router import LLMRouter
from edecan_schemas.plans import FLAG_VOICE_TELEPHONY, PLANES
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
from pydantic import BaseModel, Field

from edecan_api.config import Settings
from edecan_api.deps import (
    CurrentUser,
    build_key_provider,
    get_current_user,
    get_platform_repo,
    get_repo,
    get_vault,
    load_tenant_llm_config,
    rate_limit,
)
from edecan_api.repo import Repo, SqlRepo
from edecan_api.routers.persona import persona_from_row

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/phone", tags=["phone"])

TWILIO_CONNECTOR_KEY = "twilio"
ACTIVE_STATUSES = frozenset({"confirmed", "queued", "ringing", "in_progress"})
TERMINAL_STATUSES = frozenset({"completed", "failed", "busy", "no_answer", "cancelled"})
STATUS_ORDER = {"draft": 0, "confirmed": 1, "queued": 2, "ringing": 3, "in_progress": 4}


def _monotonic_status(current: str, incoming: str) -> str:
    """Nunca revive ni hace retroceder una llamada por webhooks fuera de orden."""
    if current in TERMINAL_STATUSES:
        return current
    if incoming in TERMINAL_STATUSES:
        return incoming
    if STATUS_ORDER.get(incoming, -1) < STATUS_ORDER.get(current, -1):
        return current
    return incoming


class PhoneGateway(Protocol):
    async def create_call(
        self, *, to_e164: str, voice_url: str, status_callback_url: str
    ) -> Any: ...


RepoTransactionFactory = Callable[[uuid.UUID], Any]


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
    ) -> None:
        self._repo_transaction = repo_transaction
        self._gateway = gateway
        self._tenant_id = tenant_id
        self._user_id = user_id
        self._public_base = public_base_url.rstrip("/")

    async def create_and_dispatch(self, *, to_e164: str, goal: str) -> dict[str, Any]:
        destination = normalize_e164(to_e164)
        normalized_goal = normalize_goal(goal)
        async with self._repo_transaction(self._tenant_id) as repo:
            account = await _twilio_account(repo, self._tenant_id)
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
                title=f"Llamada a {destination}",
                channel="phone",
            )
            call = await repo.create_phone_call(
                tenant_id=self._tenant_id,
                user_id=self._user_id,
                conversation_id=conversation["id"],
                direction="outgoing",
                from_e164=account["external_account_id"],
                to_e164=destination,
                goal=normalized_goal,
                status="confirmed",
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
                content={"text": f"Llama a {destination}. Objetivo: {normalized_goal}"},
            )
            await repo.add_phone_call_event(
                tenant_id=self._tenant_id,
                call_id=call["id"],
                event_type="confirmed",
                payload={"destination_verified": True, "goal_verified": True},
            )
            await repo.add_audit_log(
                tenant_id=self._tenant_id,
                actor_user_id=self._user_id,
                action="phone.call_confirmed",
                target=str(call["id"]),
                meta={"to_e164": destination, "goal": normalized_goal},
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
                payload={"destination_verified": True, "goal_verified": True},
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
                status_callback_url=(
                    f"{self._public_base}/v1/phone/twilio/calls/{call_id}/status"
                ),
            )
        except TelephonyError as exc:
            async with self._repo_transaction(self._tenant_id) as repo:
                await repo.update_phone_call(
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
            raise

        async with self._repo_transaction(self._tenant_id) as repo:
            current = await repo.get_phone_call(
                tenant_id=self._tenant_id, call_id=call_id
            )
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
            events = await repo.list_phone_call_events(
                tenant_id=self._tenant_id, call_id=call_id
            )
            if not any(event.get("event_type") == "provider_queued" for event in events):
                await repo.add_phone_call_event(
                    tenant_id=self._tenant_id,
                    call_id=call_id,
                    event_type="provider_queued",
                    payload={"status": provider_call.status},
                )
        assert updated is not None
        return updated


class PrepareCallIn(BaseModel):
    to_e164: str = Field(min_length=1)
    goal: str = Field(min_length=1, max_length=500)
    conversation_id: uuid.UUID | None = None


class ConfirmCallIn(BaseModel):
    expected_to_e164: str = Field(min_length=1)
    expected_goal: str = Field(min_length=1, max_length=500)
    confirmed_destination: bool
    confirmed_goal: bool


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
        "goal": row["goal"],
        "status": row["status"],
        "confirmed_at": row.get("confirmed_at"),
        "started_at": row.get("started_at"),
        "ended_at": row.get("ended_at"),
        "duration_seconds": row.get("duration_seconds"),
        "error": row.get("error"),
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
    return TransactionalPhoneDispatcher(
        repo_transaction=_repo_transactions(request),
        gateway=gateway,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        public_base_url=request.app.state.settings.PUBLIC_BASE_URL,
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

    async def dispatch(*, to_e164: str, goal: str) -> dict[str, Any]:
        try:
            gateway = TwilioVoiceClient(await _credentials(repo, vault, tenant_id))
            service = TransactionalPhoneDispatcher(
                repo_transaction=_repo_transactions(request),
                gateway=gateway,
                tenant_id=tenant_id,
                user_id=user_id,
                public_base_url=request.app.state.settings.PUBLIC_BASE_URL,
            )
            return await service.create_and_dispatch(to_e164=to_e164, goal=goal)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else "Telefonía no configurada."
            raise TelephonyError(detail) from exc

    return dispatch


async def _webhook_auth_token(
    request: Request, repo: Repo, tenant_id: uuid.UUID
) -> str:
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


@router.get("/calls", dependencies=[Depends(rate_limit)])
async def list_calls(
    current_user: CurrentUser = Depends(get_current_user), repo: Repo = Depends(get_repo)
) -> list[dict[str, Any]]:
    _require_telephony(current_user)
    rows = await repo.list_phone_calls(
        tenant_id=current_user.tenant_id, user_id=current_user.user_id
    )
    return [_out(row) for row in rows]


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
    events = await repo.list_phone_call_events(
        tenant_id=current_user.tenant_id, call_id=call_id
    )
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
    try:
        destination = normalize_e164(body.to_e164)
        goal = normalize_goal(body.goal)
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
            tenant_id=current_user.tenant_id, conversation_id=body.conversation_id
        )
        if conversation is None or conversation["user_id"] != current_user.user_id:
            raise HTTPException(status_code=404, detail="Conversación no encontrada.")
    else:
        conversation = await repo.create_conversation(
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            title=f"Llamada a {destination}",
            channel="phone",
        )

    call = await repo.create_phone_call(
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        conversation_id=conversation["id"],
        direction="outgoing",
        from_e164=account["external_account_id"],
        to_e164=destination,
        goal=goal,
    )
    await repo.add_phone_call_event(
        tenant_id=current_user.tenant_id,
        call_id=call["id"],
        event_type="prepared",
        payload={"destination_verified": False, "goal_verified": False},
    )
    await repo.add_message(
        tenant_id=current_user.tenant_id,
        conversation_id=conversation["id"],
        role="user",
        content={"text": f"Llama a {destination}. Objetivo: {goal}"},
    )
    return {
        **_out(call),
        "requires_confirmation": True,
        "verification": {"to_e164": destination, "goal": goal},
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
    if not body.confirmed_destination or not body.confirmed_goal:
        raise HTTPException(
            status_code=422, detail="Confirma explícitamente el destino y el objetivo."
        )
    try:
        expected_destination = normalize_e164(body.expected_to_e164)
        expected_goal = normalize_goal(body.expected_goal)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if expected_destination != call["to_e164"] or expected_goal != call["goal"]:
        raise HTTPException(
            status_code=409,
            detail="El destino o el objetivo cambiaron. Revisa de nuevo antes de llamar.",
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
        persona = _external_phone_persona(persona_row)
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
                    extra_context=(
                        "Estás hablando por teléfono con el mismo asistente. "
                        "Responde con frases breves y naturales, sin Markdown. "
                        f"Objetivo de esta llamada: {call['goal']}. "
                        "No ejecutes acciones sensibles ni afirmes que se hicieron; "
                        "cualquier acción "
                        "adicional debe quedar pendiente de confirmación en la app."
                    ),
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
    call_id: uuid.UUID, request: Request, repo: Repo = Depends(get_platform_repo)
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
    message = (
        f"Hola. Soy {persona.nombre_asistente}, el asistente de quien te llama. "
        f"El motivo es: {call['goal']}"
    )
    return Response(
        conversation_twiml(message=message, gather_url=gather_url),
        media_type="application/xml",
    )


@router.post("/twilio/incoming")
async def incoming_voice(
    request: Request, repo: Repo = Depends(get_platform_repo)
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
    if call is None:
        user_id = await repo.get_first_user_id_for_tenant(tenant_id)
        if user_id is None:
            return Response(
                reject_twiml("Esta cuenta todavía no tiene una persona responsable."),
                media_type="application/xml",
            )
        conversation = await repo.create_conversation(
            tenant_id=tenant_id,
            user_id=user_id,
            title=f"Llamada de {from_e164}",
            channel="phone",
        )
        call = await repo.create_phone_call(
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation["id"],
            direction="incoming",
            from_e164=from_e164,
            to_e164=to_e164,
            goal="Atender la llamada entrante y ayudar a la persona.",
            status="in_progress",
            provider_call_sid=call_sid,
        )
        await repo.add_phone_call_event(
            tenant_id=tenant_id,
            call_id=call["id"],
            event_type="incoming",
            payload={"status": "in_progress"},
        )

    gather_url = (
        f"{request.app.state.settings.PUBLIC_BASE_URL.rstrip('/')}/v1/phone/twilio/"
        f"calls/{call['id']}/gather"
    )
    persona = _external_phone_persona(
        await repo.get_persona(tenant_id=tenant_id, user_id=call["user_id"])
    )
    return Response(
        conversation_twiml(
            message=(
                f"Hola, soy {persona.nombre_asistente}. Esta conversación quedará registrada "
                "para poder ayudarte. ¿En qué puedo ayudarte?"
            ),
            gather_url=gather_url,
        ),
        media_type="application/xml",
    )


@router.post("/twilio/calls/{call_id}/gather")
async def gather_turn(
    call_id: uuid.UUID, request: Request, repo: Repo = Depends(get_platform_repo)
) -> Response:
    params = await _form(request)
    call = await repo.get_phone_call_by_provider_sid(
        provider_call_sid=params.get("CallSid", "")
    )
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
    return Response(
        conversation_twiml(
            message=reply,
            gather_url=gather_url,
            end_after_message=is_last_turn,
        ),
        media_type="application/xml",
    )


@router.post("/twilio/calls/{call_id}/status", status_code=204)
async def call_status(
    call_id: uuid.UUID, request: Request, repo: Repo = Depends(get_platform_repo)
) -> None:
    params = await _form(request)
    call = await repo.get_phone_call_by_provider_sid(
        provider_call_sid=params.get("CallSid", "")
    )
    if call is None:
        call = await repo.get_phone_call_global(call_id=call_id)
    if call is None:
        raise HTTPException(status_code=404, detail="Llamada no encontrada.")
    if call["id"] != call_id:
        raise HTTPException(status_code=404, detail="Llamada no encontrada.")
    await _verify_webhook(request, params=params, repo=repo, tenant_id=call["tenant_id"])
    provider_status = normalize_twilio_status(params.get("CallStatus"))
    effective_status = _monotonic_status(call["status"], provider_status)
    fields: dict[str, Any] = {"status": effective_status}
    if not call.get("provider_call_sid") and params.get("CallSid"):
        fields["provider_call_sid"] = params["CallSid"]
    if effective_status == "in_progress" and call.get("started_at") is None:
        fields["started_at"] = datetime.now(UTC)
    if effective_status in TERMINAL_STATUSES:
        fields["ended_at"] = datetime.now(UTC)
        if effective_status == "completed" and params.get("CallDuration", "").isdigit():
            fields["duration_seconds"] = int(params["CallDuration"])
    await repo.update_phone_call(tenant_id=call["tenant_id"], call_id=call_id, fields=fields)
    await repo.add_phone_call_event(
        tenant_id=call["tenant_id"],
        call_id=call_id,
        event_type="status",
        payload={"status": effective_status, "provider_status": provider_status},
    )
