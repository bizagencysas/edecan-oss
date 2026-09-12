"""Voz gestionada por Speech Engine de ElevenLabs (`docs/speech-engine.md`).

Contrato de punta a punta:

- El móvil/desktop se conecta a ElevenLabs por WebRTC con un
  `conversation_token` efímero. El backend NUNCA entrega API keys a clientes.
- ElevenLabs conecta al callback WSS público de Edecan
  (`/v1/voice/speech-engine/ws/{session_id}`) y envía transcripts; Edecan
  corre SU propio LLM/tools (interlocutor rápido + delegación al agente
  completo) y devuelve deltas de texto. Speech Engine se ocupa de ASR, TTS,
  detección de turno e interrupciones.

Seguridad:

- El `session_id` del path no autoriza nada: el callback exige el JWT del
  proveedor (`X-Elevenlabs-Speech-Engine-Authorization`, HS256 con
  SHA256(api_key) del TENANT dueño de la sesión) y estado/expiración válidos.
- El payload del proveedor es dato de reconocimiento no confiable: nunca
  elige tenant/chat por su cuenta, y solo el enunciado final NUEVO se
  persiste. Claims durables `(session_id, event_id)` impiden que replays
  dupliquen mensajes o efectos de tools.
- Speech Engine es un proveedor PAGO explícito: requiere credencial propia
  del tenant (`connector_key="speech_engine"`), preferencia `enabled=true` y
  `paid_consent=true` en CADA creación de sesión. Nunca se activa facturación
  por minuto a usuarios existentes en silencio.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from edecan_core.safety import redact
from edecan_db.session import get_session
from edecan_db.vault import TokenVault
from edecan_llm.task_router import (
    modelo_chat_permitido,
    modelo_chat_por_defecto,
    modelos_chat_disponibles,
)
from fastapi import APIRouter, Depends, HTTPException, WebSocket, status
from pydantic import BaseModel, ConfigDict, Field

from edecan_api.config import Settings, get_settings
from edecan_api.deps import (
    VOICE_TTS_CONNECTOR_KEY,
    CurrentUser,
    build_key_provider,
    flags_for_plan,
    get_current_user,
    get_repo,
    get_vault,
    rate_limit,
)
from edecan_api.repo import Repo, SqlRepo
from edecan_api.routers.voice import _read_tenant_voice_config
from edecan_api.speech_engine_turn_service import (
    execute_delegated_turn,
    execute_fast_interlocutor_turn,
    require_delegation,
)
from edecan_api.speech_engine_wire import SpeechEngineWireSession, TranscriptMessage

logger = logging.getLogger(__name__)

SPEECH_ENGINE_CONNECTOR_KEY = "speech_engine"

# Callback WSS: el bearer no existe (el auth es el JWT del proveedor en un
# header del handshake), así que este router NO lleva la dependencia HTTP
# `rate_limit` — el propio handshake valida y el loop no ejecuta nada sin él.
router = APIRouter(prefix="/v1/voice/speech-engine", tags=["voice-speech-engine"])
ws_router = APIRouter(prefix="/v1/voice/speech-engine", tags=["voice-speech-engine-ws"])
preferences_router = APIRouter(
    prefix="/v1/voice", tags=["voice-speech-engine"], dependencies=[Depends(rate_limit)]
)

_WS_CLOSE_AUTH = 4401
_WS_CLOSE_FORBIDDEN = 4403
_WS_CLOSE_PROTOCOL = 4400

_ESFUERZOS_VALIDOS = frozenset({"bajo", "medio", "alto"})
_MIN_DURATION_SECONDS = 60
_MAX_DURATION_SECONDS = 86_400
# Tope defensivo del enunciado transcrito: el chat tiene su propio límite,
# pero el callback del proveedor es una frontera y no debe entrar texto
# ilimitado al LLM ni a la persistencia.
_MAX_TRANSCRIPT_CHARS = 4_000


class VoicePreferencesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    provider: str = "elevenlabs"
    voice_model_id: str | None = Field(default=None, max_length=240)
    delegation_model_id: str | None = Field(default=None, max_length=240)
    delegation_effort: str | None = Field(default=None, max_length=24)
    voice_id: str | None = Field(default=None, max_length=240)
    tts_model_id: str | None = Field(default=None, max_length=240)
    max_duration_seconds: int = Field(default=900, ge=_MIN_DURATION_SECONDS, le=_MAX_DURATION_SECONDS)


class SpeechEngineSessionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: uuid.UUID | None = None
    paid_consent: bool = False


# ---------------------------------------------------------------------------
# Proveedor: seam de construcción para tests (nunca se llama con la key real
# en un test). La verificación del JWT del proveedor usa la función PÚBLICA
# `verify_speech_engine_jwt` del SDK oficial (verificado contra la fuente
# 2.68.0: HS256 con SHA256(api_key), issuer/sub fijos, exp/iat con leeway).
# ---------------------------------------------------------------------------


def _build_provider_client(api_key: str) -> Any:
    from elevenlabs import AsyncElevenLabs

    return AsyncElevenLabs(api_key=api_key)


async def _read_speech_engine_config(
    vault: Any, repo: Repo, tenant_id: uuid.UUID
) -> dict[str, Any] | None:
    """Config de Speech Engine del tenant (connector_key `speech_engine`),
    descifrada y parseada, o `None` si no conectó nada / no hay vault."""
    if vault is None:
        return None
    accounts = await repo.list_connector_accounts(tenant_id=tenant_id)
    account = next(
        (a for a in accounts if a["connector_key"] == SPEECH_ENGINE_CONNECTOR_KEY), None
    )
    if account is None:
        return None
    bundle = await vault.get(tenant_id, account["id"])
    if bundle is None:
        return None
    try:
        data = json.loads(bundle.access_token)
    except (TypeError, ValueError):
        logger.warning("Config ilegible de speech_engine del tenant_id=%s", tenant_id)
        return None
    return data if isinstance(data, dict) else None


async def _speech_engine_api_key(
    vault: Any, repo: Repo, tenant_id: uuid.UUID
) -> str | None:
    cfg = await _read_speech_engine_config(vault, repo, tenant_id)
    if not cfg or cfg.get("provider") != "elevenlabs":
        return None
    key = str(cfg.get("api_key") or "").strip()
    return key or None


def _callback_base(settings: Settings) -> str:
    base = (settings.SPEECH_ENGINE_CALLBACK_BASE or settings.PUBLIC_BASE_URL or "").strip()
    if not base:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "La voz gestionada requiere una URL pública de callback "
                "(SPEECH_ENGINE_CALLBACK_BASE o PUBLIC_BASE_URL)."
            ),
        )
    return base.replace("http://", "ws://").replace("https://", "wss://").rstrip("/")


async def _provider_tts_catalogs(api_key: str | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Catálogos TTS (voces y modelos) del proveedor con la key del tenant.

    Best-effort: sin key, sin red o con error del proveedor devuelve listas
    vacías — el default de configuración sigue funcionando y el cliente
    muestra "sin catálogo" en vez de fallar."""
    if not api_key:
        return [], []
    voices: list[dict[str, Any]] = []
    models: list[dict[str, Any]] = []
    try:
        client = _build_provider_client(api_key)
        try:
            voice_page = await client.voices.get_all()
            for voice in voice_page.voices or []:
                voices.append(
                    {"id": voice.voice_id, "name": voice.name or voice.voice_id}
                )
        except Exception:  # noqa: BLE001 - catálogo best-effort
            logger.warning("catálogo de voces de ElevenLabs no disponible", exc_info=True)
        try:
            model_page = await client.models.get_all()
            for model in model_page:
                if getattr(model, "can_do_text_to_speech", False):
                    models.append(
                        {"id": model.model_id, "name": model.name or model.model_id}
                    )
        except Exception:  # noqa: BLE001 - catálogo best-effort
            logger.warning("catálogo de modelos TTS de ElevenLabs no disponible", exc_info=True)
    except Exception:  # noqa: BLE001 - catálogo best-effort
        logger.warning("cliente de ElevenLabs no disponible para catálogos", exc_info=True)
    return voices, models


def _preference_effective(pref_row: dict[str, Any] | None) -> dict[str, Any] | None:
    if pref_row is None:
        return None
    return {
        "enabled": bool(pref_row.get("enabled")),
        "provider": pref_row.get("provider") or "elevenlabs",
        "voice_model_id": pref_row.get("voice_model_id"),
        "delegation_model_id": pref_row.get("delegation_model_id"),
        "delegation_effort": pref_row.get("delegation_effort"),
        "voice_id": pref_row.get("voice_id"),
        "tts_model_id": pref_row.get("tts_model_id"),
        "max_duration_seconds": int(pref_row.get("max_duration_seconds") or 900),
    }


# ---------------------------------------------------------------------------
# Preferencias (GET/PUT /v1/voice/preferences)
# ---------------------------------------------------------------------------


@preferences_router.get("/preferences")
async def get_voice_preferences(
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    vault: Any = Depends(get_vault),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    pref_row = await repo.get_voice_preference(
        tenant_id=current_user.tenant_id, user_id=current_user.user_id
    )
    api_key = await _speech_engine_api_key(vault, repo, current_user.tenant_id)
    voices, models = await _provider_tts_catalogs(api_key)
    chat_models = modelos_chat_disponibles()
    return {
        "preference": _preference_effective(pref_row),
        "credential_connected": api_key is not None,
        "paid_provider_notice": (
            "La voz gestionada usa Speech Engine de ElevenLabs con la API key "
            "del tenant: los minutos se facturan a ESA cuenta. Actívala "
            "explícitamente; nada se cobra sin tu key y tu consentimiento."
        ),
        "catalogs": {
            "voice_models": chat_models,
            "tts_voices": voices,
            "tts_models": models,
        },
        "defaults": {
            "voice_model_id": modelo_chat_por_defecto(),
            "tts_model_id": settings.SPEECH_ENGINE_DEFAULT_TTS_MODEL_ID,
            "max_duration_seconds": settings.SPEECH_ENGINE_DEFAULT_MAX_DURATION_SECONDS,
        },
    }


@preferences_router.put("/preferences")
async def put_voice_preferences(
    body: VoicePreferencesIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    vault: Any = Depends(get_vault),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    if body.provider.strip().lower() != "elevenlabs":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="provider debe ser 'elevenlabs' (único proveedor de voz gestionada).",
        )
    for campo, valor in (
        ("voice_model_id", body.voice_model_id),
        ("delegation_model_id", body.delegation_model_id),
    ):
        if valor is not None and not modelo_chat_permitido(valor):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{campo} no está en el catálogo de modelos del chat.",
            )
    if body.delegation_effort is not None and body.delegation_effort not in _ESFUERZOS_VALIDOS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"delegation_effort debe ser uno de {sorted(_ESFUERZOS_VALIDOS)}.",
        )

    api_key = await _speech_engine_api_key(vault, repo, current_user.tenant_id)
    if body.enabled and not api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Para activar la voz gestionada conecta primero tu API key de "
                "ElevenLabs (PUT /v1/credentials/voice/speech-engine). Es un "
                "proveedor pagado y la facturación es de tu cuenta."
            ),
        )

    row = await repo.upsert_voice_preference(
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        fields={
            "provider": "elevenlabs",
            "enabled": body.enabled,
            "voice_model_id": body.voice_model_id,
            "delegation_model_id": body.delegation_model_id,
            "delegation_effort": body.delegation_effort,
            "voice_id": body.voice_id,
            "tts_model_id": body.tts_model_id,
            "max_duration_seconds": body.max_duration_seconds,
        },
    )
    return {"preference": _preference_effective(row)}


# ---------------------------------------------------------------------------
# Sesiones (POST /v1/voice/speech-engine/sessions, GET, end)
# ---------------------------------------------------------------------------


async def _delete_provider_engine(api_key: str, engine_id: str | None) -> None:
    """Limpieza best-effort del recurso del proveedor (nunca rompe la request)."""
    if not engine_id or not api_key:
        return
    try:
        client = _build_provider_client(api_key)
        await client.speech_engine.delete(engine_id)
    except Exception:  # noqa: BLE001 - cleanup
        logger.warning("no se pudo eliminar el engine del proveedor %s", engine_id, exc_info=True)


@router.post("/sessions")
async def create_speech_engine_session(
    body: SpeechEngineSessionIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    vault: Any = Depends(get_vault),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    if not body.paid_consent:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "La voz gestionada es un proveedor pagado: envía "
                "paid_consent=true para confirmar que aceptas que los minutos "
                "se facturen a tu API key de ElevenLabs."
            ),
        )
    pref_row = await repo.get_voice_preference(
        tenant_id=current_user.tenant_id, user_id=current_user.user_id
    )
    if pref_row is None or not pref_row.get("enabled"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "La voz gestionada no está activada para tu cuenta. Configúrala "
                "en /v1/voice/preferences (requiere tu API key de ElevenLabs)."
            ),
        )
    api_key = await _speech_engine_api_key(vault, repo, current_user.tenant_id)
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No hay API key de ElevenLabs conectada para Speech Engine.",
        )

    conversation_id = body.conversation_id
    if conversation_id is not None:
        conversation = await repo.get_conversation(
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            conversation_id=conversation_id,
        )
        if conversation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversación no encontrada.",
            )
    else:
        # Chat nuevo: asegurar la conversación canónica ANTES de pedir voz.
        conversation = await repo.resolve_main_conversation(
            tenant_id=current_user.tenant_id, user_id=current_user.user_id
        )
        conversation_id = conversation["id"]

    now = datetime.now(UTC)
    active = await repo.list_active_speech_engine_sessions(
        tenant_id=current_user.tenant_id, user_id=current_user.user_id, now=now
    )
    if len(active) >= max(1, settings.SPEECH_ENGINE_MAX_ACTIVE_SESSIONS):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Tienes demasiadas sesiones de voz gestionada abiertas. Cierra una antes.",
        )

    max_duration = int(pref_row.get("max_duration_seconds") or settings.SPEECH_ENGINE_DEFAULT_MAX_DURATION_SECONDS)
    expires_at = now + timedelta(seconds=max_duration)
    session_row = await repo.create_speech_engine_session(
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        conversation_id=conversation_id,
        expires_at=expires_at,
        max_duration_seconds=max_duration,
        plan_key=current_user.tenant.plan_key,
    )
    session_id: uuid.UUID = session_row["id"]

    persona = await repo.get_persona(
        tenant_id=current_user.tenant_id, user_id=current_user.user_id
    )
    language = str((persona or {}).get("idioma") or "es").strip()[:8] or "es"
    voice_id = pref_row.get("voice_id")
    if not voice_id:
        # Hereda la voz ya elegida en la credencial TTS del tenant (si existe):
        # el usuario no vuelve a elegir voz para hablar gestionado.
        tts_cfg = await _read_tenant_voice_config(
            vault, repo, current_user.tenant_id, VOICE_TTS_CONNECTOR_KEY
        )
        voice_id = (tts_cfg or {}).get("voice_id")
    tts_model_id = pref_row.get("tts_model_id") or settings.SPEECH_ENGINE_DEFAULT_TTS_MODEL_ID

    ws_url = f"{_callback_base(settings)}/v1/voice/speech-engine/ws/{session_id}"
    try:
        from elevenlabs import (
            AgentCallLimits,
            PrivacyConfigInput,
            SpeechEngineConfig,
            TtsConversationalConfigInput,
        )

        client = _build_provider_client(api_key)
        engine = await client.speech_engine.create(
            speech_engine=SpeechEngineConfig(ws_url=ws_url),
            privacy=PrivacyConfigInput(
                record_voice=False,
                retention_days=int(settings.SPEECH_ENGINE_PRIVACY_RETENTION_DAYS),
            ),
            call_limits=AgentCallLimits(agent_concurrency_limit=1, bursting_enabled=False),
            tts=TtsConversationalConfigInput(
                model_id=tts_model_id,
                voice_id=voice_id,
            ),
            language=language,
        )
        provider_engine_id = str(engine.speech_engine_id)
        try:
            token_response = await client.conversational_ai.conversations.get_webrtc_token(
                agent_id=provider_engine_id
            )
        except Exception as exc:
            # El engine YA quedó creado en el proveedor: sin token la sesión no
            # sirve, y dejarlo vivo facturaría minutos huérfanos. Se elimina
            # best-effort antes de marcar la sesión como fallida.
            logger.warning("token WebRTC de speech engine falló", exc_info=True)
            await _delete_provider_engine(api_key, provider_engine_id)
            await repo.update_speech_engine_session(
                session_id=session_id, fields={"status": "failed", "ended_at": now}
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="No se pudo provisionar la sesión de voz gestionada en el proveedor.",
            ) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("provisionamiento de speech engine falló", exc_info=True)
        await repo.update_speech_engine_session(
            session_id=session_id, fields={"status": "failed", "ended_at": now}
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo provisionar la sesión de voz gestionada en el proveedor.",
        ) from exc

    token_expires_at = now + timedelta(seconds=max(1, settings.SPEECH_ENGINE_TOKEN_TTL_SECONDS))
    await repo.update_speech_engine_session(
        session_id=session_id,
        fields={
            "provider_engine_id": provider_engine_id,
            "token_expires_at": token_expires_at,
            "status": "active",
        },
    )
    conversation_model = (
        await repo.get_conversation(
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            conversation_id=conversation_id,
        )
        or {}
    ).get("chat_model")
    effective_delegation = pref_row.get("delegation_model_id") or conversation_model or None
    return {
        "session_id": str(session_id),
        "conversation_id": str(conversation_id),
        "conversation_token": str(token_response.token),
        "expires_at": expires_at.isoformat(),
        "token_expires_at": token_expires_at.isoformat(),
        "voice_model_id": pref_row.get("voice_model_id") or modelo_chat_por_defecto(),
        "delegation_model_id": effective_delegation,
        "tts_model_id": tts_model_id,
        "voice_id": voice_id,
        "language": language,
    }


@router.get("/sessions/{session_id}")
async def get_speech_engine_session_status(
    session_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    row = await repo.get_speech_engine_session_scoped(
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        session_id=session_id,
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sesión no encontrada.")
    return {
        "session_id": str(row["id"]),
        "conversation_id": str(row["conversation_id"]),
        "status": row["status"],
        "expires_at": row["expires_at"].isoformat(),
        "ended_at": row["ended_at"].isoformat() if row.get("ended_at") else None,
    }


@router.post("/sessions/{session_id}/end")
async def end_speech_engine_session(
    session_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    vault: Any = Depends(get_vault),
) -> dict[str, Any]:
    """Fin idempotente: marca `ended` + elimina el recurso del proveedor.

    Repetir el end sobre una sesión ya terminada devuelve lo mismo y no llama
    al proveedor dos veces (el engine_id ya se limpió la primera vez)."""
    row = await repo.get_speech_engine_session_scoped(
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        session_id=session_id,
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sesión no encontrada.")
    if row["status"] not in {"ended", "expired", "failed"}:
        now = datetime.now(UTC)
        engine_id = row.get("provider_engine_id")
        if engine_id:
            api_key = await _speech_engine_api_key(vault, repo, current_user.tenant_id)
            await _delete_provider_engine(api_key, engine_id)
            await repo.update_speech_engine_session(
                session_id=session_id,
                fields={"status": "ended", "ended_at": now, "provider_engine_id": None},
            )
        else:
            await repo.update_speech_engine_session(
                session_id=session_id,
                fields={"status": "ended", "ended_at": now},
            )
    return {"session_id": str(session_id), "ended": True}


# ---------------------------------------------------------------------------
# Callback WSS del proveedor (por sesión)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _platform_store(settings: Settings):
    """Sesión de plataforma (sin RLS) en transacciones CORTAS.

    El callback no tiene JWT de usuario; la autoridad de tenant sale de la fila
    de sesión, cuya legitimidad la garantiza el JWT del proveedor. Cada
    operación (validación, claim, turno, cierre) abre y cierra su propia
    transacción — nunca UNA transacción viva durante toda la llamada de audio.
    Devuelve `(repo, vault, db_session)` para que el turno pueda persistir
    unified_session dentro de la MISMA transacción del turno."""
    async with get_session(None) as db:
        yield SqlRepo(db), TokenVault(db, build_key_provider(settings)), db


async def _load_session_for_callback(
    websocket: WebSocket, session_id: uuid.UUID, settings: Settings
) -> dict[str, Any] | None:
    """Valida la fila de sesión para el callback y devuelve la fila (o `None`
    con el websocket ya cerrado con el código correspondiente). El repo/vault
    NO escapan de la transacción corta: cada operación del callback abre la
    suya."""
    try:
        async with _platform_store(settings) as (repo, vault, _db):
            row = await repo.get_speech_engine_session(session_id=session_id)
            if row is None:
                await websocket.close(code=_WS_CLOSE_FORBIDDEN)
                return None
            now = datetime.now(UTC)
            api_key = await _speech_engine_api_key(vault, repo, row["tenant_id"])
            if row["status"] != "active" or row["expires_at"] <= now:
                # Expiración detectada al (re)conectar: marca la sesión y
                # elimina el recurso pagado del proveedor (best-effort).
                if row["expires_at"] <= now and row.get("provider_engine_id"):
                    await repo.update_speech_engine_session(
                        session_id=session_id,
                        fields={
                            "status": "expired",
                            "ended_at": now,
                            "provider_engine_id": None,
                        },
                    )
                    await _delete_provider_engine(api_key, row.get("provider_engine_id"))
                await websocket.close(code=_WS_CLOSE_FORBIDDEN)
                return None
            if not row.get("provider_engine_id"):
                await websocket.close(code=_WS_CLOSE_FORBIDDEN)
                return None
            if not api_key:
                await websocket.close(code=_WS_CLOSE_AUTH)
                return None
            raw_auth = websocket.headers.get("x-elevenlabs-speech-engine-authorization")
            if not raw_auth:
                await websocket.close(code=_WS_CLOSE_AUTH)
                return None
            from elevenlabs.speech_engine import verify_speech_engine_jwt

            try:
                verify_speech_engine_jwt(str(raw_auth), api_key)
            except ValueError:
                await websocket.close(code=_WS_CLOSE_AUTH)
                return None
            return dict(row)
    except Exception:
        logger.warning("callback de speech engine: validación de sesión falló", exc_info=True)
        await websocket.close(code=_WS_CLOSE_AUTH)
        return None


def _nuevo_enunciado(transcript: list[TranscriptMessage]) -> str | None:
    """Último enunciado del usuario en el transcript del proveedor.

    El transcript trae la historia completa del turno; solo el enunciado final
    NUEVO se persiste. Si no hay texto del usuario, devuelve `None` (el turno
    no ejecuta nada y cierra la respuesta vacía)."""
    for message in reversed(transcript):
        if message.role == "user" and str(message.content or "").strip():
            return str(message.content).strip()[:_MAX_TRANSCRIPT_CHARS]
    return None


@ws_router.websocket("/ws/{session_id}")
async def speech_engine_callback(websocket: WebSocket, session_id: uuid.UUID) -> None:
    settings = get_settings()
    session_row = await _load_session_for_callback(websocket, session_id, settings)
    if session_row is None:
        return
    tenant_id: uuid.UUID = session_row["tenant_id"]
    user_id: uuid.UUID = session_row["user_id"]
    conversation_id: uuid.UUID = session_row["conversation_id"]
    plan_key = str(session_row.get("plan_key") or "")
    # Los flags del callback salen del plan SNAPSHOT de la sesión (persistido
    # al crearla con el usuario autenticado) — nunca del payload del proveedor.
    flags = flags_for_plan(plan_key)

    # El modelo de la sesión NO cambia a mitad de llamada: se resuelve UNA vez
    # al aceptar el callback y viaja en el turno como `seleccion` (nunca pisa
    # `conversations.chat_model`). La delegación hereda el modelo/esfuerzo que
    # YA tiene la conversación cuando la preferencia no fija uno explícito.
    from edecan_core.agent import SeleccionDeModelo

    async with _platform_store(settings) as (_pref_repo, _pref_vault, _pref_db):
        pref_row = await _pref_repo.get_voice_preference(tenant_id=tenant_id, user_id=user_id)
        fast_model_id = (pref_row or {}).get("voice_model_id") or None
        delegation_model_id = (pref_row or {}).get("delegation_model_id") or None
        delegation_effort = (pref_row or {}).get("delegation_effort") or None
        conversation_row = await _pref_repo.get_conversation(
            tenant_id=tenant_id, user_id=user_id, conversation_id=conversation_id
        )
    if delegation_model_id and modelo_chat_permitido(delegation_model_id):
        delegation_seleccion = SeleccionDeModelo(
            modelo=delegation_model_id, esfuerzo=delegation_effort
        )
    else:
        conversation_model = (conversation_row or {}).get("chat_model")
        conversation_effort = (conversation_row or {}).get("chat_effort")
        if conversation_model and modelo_chat_permitido(conversation_model):
            delegation_seleccion = SeleccionDeModelo(
                modelo=conversation_model,
                esfuerzo=delegation_effort or conversation_effort,
            )
        else:
            delegation_seleccion = SeleccionDeModelo(
                modelo=None, esfuerzo=delegation_effort
            )

    await websocket.accept()
    wire = SpeechEngineWireSession(websocket)
    llm_router = websocket.app.state.llm_router

    async def _on_init(provider_conversation_id: str) -> None:
        try:
            async with _platform_store(settings) as (init_repo, _init_vault, _init_db):
                await init_repo.update_speech_engine_session(
                    session_id=session_id,
                    fields={"provider_conversation_id": provider_conversation_id},
                )
        except Exception:  # noqa: BLE001 - metadato
            logger.warning("no se pudo persistir provider_conversation_id", exc_info=True)

    async def _on_close() -> None:
        try:
            async with _platform_store(settings) as (close_repo, close_vault, _close_db):
                session_row = await close_repo.get_speech_engine_session(session_id=session_id)
                api_key = await _speech_engine_api_key(close_vault, close_repo, tenant_id)
                engine_id = (session_row or {}).get("provider_engine_id")
                if engine_id:
                    # El proveedor factura por minuto: el cierre del callback
                    # debe eliminar el recurso, no dejarlo huérfano.
                    await _delete_provider_engine(api_key, engine_id)
                # No se pisa un estado terminal (expired/failed) por una
                # carrera con la expiración o con el end REST.
                estado_actual = (session_row or {}).get("status")
                if estado_actual in {"active", "provisioning"}:
                    await close_repo.update_speech_engine_session(
                        session_id=session_id,
                        fields={
                            "status": "ended",
                            "ended_at": datetime.now(UTC),
                            "provider_engine_id": None,
                        },
                    )
        except Exception:  # noqa: BLE001
            logger.warning("no se pudo marcar ended en close del callback", exc_info=True)

    async def _on_transcript(transcript: list[TranscriptMessage], event_id: int) -> None:
        texto: str | None = None
        assistant_text = ""
        final_enviado = False

        async def _cerrar_respuesta() -> None:
            nonlocal final_enviado
            if wire.is_open and not final_enviado:
                final_enviado = True
                await wire.send_agent_response("", is_final=True)
        try:
            # 1) Claim durable EN SU PROPIA transacción corta: commitea AUNQUE
            # el turno de abajo se cancele o se revierta. Sin esto, una
            # interrupción hacía rollback del claim y un replay del proveedor
            # podía re-ejecutar un turno con efectos externos ya cometidos.
            async with _platform_store(settings) as (claim_repo, _claim_vault, _claim_db):
                session_now = await claim_repo.get_speech_engine_session(session_id=session_id)
                if session_now is None or session_now["status"] != "active":
                    await websocket.close(code=_WS_CLOSE_FORBIDDEN)
                    return
                if session_now["expires_at"] <= datetime.now(UTC):
                    await claim_repo.update_speech_engine_session(
                        session_id=session_id,
                        fields={
                            "status": "expired",
                            "ended_at": datetime.now(UTC),
                            "provider_engine_id": None,
                        },
                    )
                    # Minutos facturados: la expiración también elimina el
                    # recurso del proveedor (best-effort, nunca bloquea).
                    if session_now.get("provider_engine_id"):
                        await _delete_provider_engine(
                            await _speech_engine_api_key(
                                _claim_vault, claim_repo, tenant_id
                            ),
                            session_now.get("provider_engine_id"),
                        )
                    await websocket.close(code=_WS_CLOSE_FORBIDDEN)
                    return
                claimed = await claim_repo.claim_speech_engine_event(
                    tenant_id=tenant_id, session_id=session_id, event_id=event_id
                )
            if not claimed:
                # Replay del proveedor: nunca se re-ejecuta. Se reenvía el texto
                # ya persistido para que el proveedor pueda re-renderizar sin
                # efectos nuevos.
                async with _platform_store(settings) as (replay_repo, _rv, _rd):
                    existing = await replay_repo.get_speech_engine_event(
                        session_id=session_id, event_id=event_id
                    )
                    persisted_text = (existing or {}).get("assistant_text") or ""
                    if persisted_text:
                        await wire.send_agent_response(persisted_text, is_final=False)
                await _cerrar_respuesta()
                return
            texto = _nuevo_enunciado(transcript)
            if texto is None:
                async with _platform_store(settings) as (_empty_repo, _ev, _ed):
                    await _empty_repo.update_speech_engine_event(
                        session_id=session_id,
                        event_id=event_id,
                        fields={"status": "persisted", "user_text": None, "assistant_text": None},
                    )
                await _cerrar_respuesta()
                return

            # Un enunciado que la redacción vacía por completo (p. ej. solo un
            # número de teléfono) no debe escribir un mensaje en blanco.
            clean_utterance = redact(texto).strip()
            if not clean_utterance:
                async with _platform_store(settings) as (_blank_repo, _bv, _bd):
                    await _blank_repo.update_speech_engine_event(
                        session_id=session_id,
                        event_id=event_id,
                        fields={"status": "persisted", "user_text": texto, "assistant_text": ""},
                    )
                await _cerrar_respuesta()
                return

            from edecan_api.deps import CurrentUser as _CU
            from edecan_api.deps import TenantCtx as _TC

            current_user = _CU(user_id=user_id, tenant=_TC(tenant_id=tenant_id, plan_key=plan_key, flags=flags))

            async def _emit_delta(delta_text: str) -> None:
                if delta_text:
                    await wire.send_agent_response(delta_text, is_final=False)

            # El enunciado del usuario se persiste en transacción CORTA y
            # commitea ANTES de correr el LLM: una interrupción (rollback del
            # turno) ya no pierde lo que la persona dijo.
            async with _platform_store(settings) as (_ut_repo, _ut_vault, _ut_db):
                await _ut_repo.add_message(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    role="user",
                    content={"text": clean_utterance},
                )

            # 2) Turno en OTRA transacción: su rollback no borra el claim ni
            # el enunciado ya commiteado. El timeout acota la conexión de la
            # pool aunque una tool o el LLM se cuelguen.
            try:
                async with asyncio.timeout(int(settings.SPEECH_ENGINE_TURN_TIMEOUT_SECONDS)):
                    async with _platform_store(settings) as (turn_repo, turn_vault, turn_db):
                        if require_delegation(texto):
                            outcome = await execute_delegated_turn(
                                request=websocket,
                                session=turn_db,
                                repo=turn_repo,
                                vault=turn_vault,
                                current_user=current_user,
                                settings=settings,
                                llm_router=llm_router,
                                conversation_id=conversation_id,
                                user_text=texto,
                                delegation_seleccion=delegation_seleccion,
                                on_text_delta=_emit_delta,
                            )
                        else:
                            outcome = await execute_fast_interlocutor_turn(
                                request=websocket,
                                session=turn_db,
                                repo=turn_repo,
                                vault=turn_vault,
                                current_user=current_user,
                                settings=settings,
                                llm_router=llm_router,
                                conversation_id=conversation_id,
                                user_text=texto,
                                fast_model_id=fast_model_id,
                                already_persisted_input=True,
                                on_text_delta=_emit_delta,
                            )
                            if outcome.delegated:
                                outcome = await execute_delegated_turn(
                                    request=websocket,
                                    session=turn_db,
                                    repo=turn_repo,
                                    vault=turn_vault,
                                    current_user=current_user,
                                    settings=settings,
                                    llm_router=llm_router,
                                    conversation_id=conversation_id,
                                    user_text=texto,
                                    delegation_seleccion=delegation_seleccion,
                                    on_text_delta=_emit_delta,
                                )
                        assistant_text = outcome.text
                        if outcome.confirmation_required:
                            await wire.send_agent_response(
                                "Esta acción requiere tu aprobación: revísala en el chat.",
                                is_final=False,
                            )
                        await turn_repo.update_speech_engine_event(
                            session_id=session_id,
                            event_id=event_id,
                            fields={
                                "status": "persisted",
                                "user_text": texto,
                                "assistant_text": assistant_text,
                            },
                        )
            except asyncio.CancelledError:
                # 3) Interrupción: la transacción del turno se revierte; el
                # estado del evento se escribe en transacción propia para que
                # el claim durado siga marcándolo como ya atendido.
                async with _platform_store(settings) as (_int_repo, _iv, _idb):
                    await _int_repo.update_speech_engine_event(
                        session_id=session_id,
                        event_id=event_id,
                        fields={
                            "status": "interrupted",
                            "user_text": texto,
                            "assistant_text": assistant_text,
                        },
                    )
                raise
            except Exception:
                logger.warning("turno gestionado de voz falló (event %s)", event_id, exc_info=True)
                async with _platform_store(settings) as (_fail_repo, _fv, _fdb):
                    await _fail_repo.update_speech_engine_event(
                        session_id=session_id,
                        event_id=event_id,
                        fields={
                            "status": "failed",
                            "user_text": texto,
                            "assistant_text": assistant_text,
                        },
                    )
                await wire.send_agent_response(
                    "No pude completar este turno. Inténtalo de nuevo.", is_final=False
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("callback: turno %s inmanejable", event_id, exc_info=True)
            del exc
        finally:
            await _cerrar_respuesta()

    wire.on("init", _on_init)
    wire.on("user_transcript", _on_transcript)
    # Solo el "close" explícito del proveedor termina la sesión: una caída de
    # red (disconnected) no debe marcarla `ended` — el proveedor puede
    # reconectar y repetir turnos, y los claims durables los deduplican.
    wire.on("close", _on_close)
    await wire.run()