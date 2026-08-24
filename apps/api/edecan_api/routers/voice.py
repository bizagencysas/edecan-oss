"""`/v1/voice/transcribe` y `/v1/voice/speak` (ARCHITECTURE.md §10.12, §4, §10.9).

Gateadas por el flag de plan `voice.web` y por la cuota `limits.voice_minutes_month`.

## Resolución de proveedor: tenant → stub, SIN paso de plataforma (WP-V3-02)

Antes de este WP, `transcribe`/`speak` construían el STT/TTS SIEMPRE con
`get_stt(settings)`/`get_tts(settings)` — `DEEPGRAM_API_KEY`/`ELEVENLABS_API_KEY`
de `Settings`, un único `.env` de PLATAFORMA compartido por todos los tenants
(`DIRECCION_ACTUAL.md` "Modelo de credenciales", hallazgo confirmado en código).
`_stt_para_tenant`/`_tts_para_tenant` cierran ese hueco: SOLO dos niveles,
nunca un paso intermedio de "plataforma" que reutilice una API key real de
Deepgram/ElevenLabs entre tenants —

1. **Tenant**: si conectó su propia credencial (`PUT /v1/credentials/voice/stt`
   `/tts`, `edecan_api.routers.credentials`, `TokenVault` connector_key
   `"voice_stt"`/`"voice_tts"`), se usa ESA.
2. **Stub**: si no (o si algo falla leyéndola), `StubSTT`/`StubTTS`
   (`edecan_voice.stubs`) — offline, determinista, sin llamar a ningún
   proveedor real ni gastar la cuota de nadie. A diferencia del LLM (ver
   `edecan_api.deps.get_llm_router`, que corta la request con
   `HTTPException(400)` si el tenant no conectó nada), la voz web SÍ tiene un
   equivalente sin credencial que no es "una credencial compartida" —no hay
   nada que facturarle a un tercero por usar el stub—, así que aquí no hace
   falta lanzar: `docs/credenciales.md` documenta esta asimetría a propósito.
   `edecan_voice.registry.get_stt`/`get_tts` (que SÍ leen
   `DEEPGRAM_API_KEY`/`ELEVENLABS_API_KEY` de `Settings`) deliberadamente NO
   se llaman desde acá — ese es justo el paso de plataforma que este WP
   elimina; sigue existiendo como utilidad de `edecan_voice` para quien lo
   use fuera del contexto multi-tenant de `edecan_api` (p. ej. tests propios
   del paquete).

`vault: TokenVault | None = Depends(get_vault)` puede ser `None` (algunos tests
no lo necesitan y no lo sobreescriben — ver `apps/api/tests/conftest.py`, que
ya deja `get_vault` en `None` por defecto): en ese caso se salta directo al
paso 2, igual que un tenant sin credencial propia conectada. Leer la config del
tenant necesita `repo` (para encontrar la `connector_account` de esa
`connector_key`) ADEMÁS de `vault` (para descifrar el `TokenBundle` guardado
ahí) — el contrato de `TokenVault.get` (ARCHITECTURE.md §10.4) es por
`(tenant_id, connector_account_id)`, no por `connector_key` directo.

`provider="polly"` dentro del paso 1 es un caso especial: no tiene credencial
propia del tenant (`edecan_voice.polly.PollyTTS` se autentica con la cadena
de credenciales AWS del PROCESO, ver su docstring) — `_tts_para_tenant` solo
la construye si `getattr(settings, "EDECAN_LOCAL_MODE", False)`, igual que
`routers/credentials.py` solo deja GUARDAR esa config en ese mismo modo.
Fuera de `EDECAN_LOCAL_MODE` (o si la fila `polly` es de antes de ese gate, o
llegó por otra vía) cae al paso 2 (`StubTTS`) con `logger.warning`, nunca
comparte la identidad AWS del proceso entre tenants.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from edecan_core.veracidad import Fidelidad, InfoFidelidad, ProveedorDeclarado
from edecan_db.session import get_session
from edecan_db.vault import TokenVault
from edecan_schemas import UNLIMITED
from edecan_schemas.plans import FLAG_VOICE_WEB, LIMIT_VOICE_MINUTES_MONTH
from edecan_voice.base import STTProvider, TTSProvider
from edecan_voice.deepgram import DeepgramSTT
from edecan_voice.elevenlabs import DEFAULT_MODEL_ID, ElevenLabsTTS
from edecan_voice.polly import PollyTTS
from edecan_voice.realtime import RealtimeVoiceSession
from edecan_voice.stubs import StubSTT, StubTTS
from edecan_voice.voice_rewriter import rewrite_for_voice
from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from edecan_api.config import Settings, get_settings
from edecan_api.deps import (
    VOICE_STT_CONNECTOR_KEY,
    VOICE_TTS_CONNECTOR_KEY,
    CurrentUser,
    TenantCtx,
    build_key_provider,
    flags_for_plan,
    get_current_user,
    get_repo,
    get_vault,
    rate_limit,
)
from edecan_api.repo import Repo, SqlRepo
from edecan_api.security import TokenError, decode_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/voice", tags=["voice"], dependencies=[Depends(rate_limit)])
# WebSocket: el bearer llega en el primer frame, así que no puede usar la
# dependencia HTTP `rate_limit` del router anterior antes de aceptar el socket.
# El propio handshake valida JWT y el loop reserva cuota por turno.
realtime_router = APIRouter(prefix="/v1/voice", tags=["voice-realtime"])


class SpeakIn(BaseModel):
    text: str
    voice_id: str | None = None
    model_id: str | None = None
    voice_rewrite: bool = True


def _require_voice_web(tenant: TenantCtx) -> None:
    if not tenant.flags.get(FLAG_VOICE_WEB, False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La voz web no está disponible en tu plan.",
        )


async def _check_voice_quota(repo: Repo, tenant: TenantCtx, extra_seconds: float = 0.0) -> None:
    # Default `0` (fail-closed), NUNCA `UNLIMITED` (WP-V7-08, barrido v7): mismo
    # razonamiento que `routers/files.py::_check_storage_quota` -- un `plan_key`
    # huérfano (`edecan_api.deps.flags_for_plan` devuelve `{}`) no debe caer en
    # voz SIN NINGÚN límite. Hoy `_require_voice_web` (arriba) ya bloquea ese
    # caso con 403 antes de llegar acá (flags={} => FLAG_VOICE_WEB por defecto
    # False), así que este default es defensa en profundidad, no el único
    # candado -- pero evita que una futura reordenación de los dos chequeos (o
    # un `ctx.extras["flags"]` parcial, a diferencia del `TenantCtx.flags`
    # siempre completo-o-vacío de hoy) abra el mismo hueco fail-open que tenía
    # `files.py`. `LIMIT_VOICE_MINUTES_MONTH` SIEMPRE viene explícito en
    # `edecan_schemas.plans.PLANES` para los 4 planes reales, así que este
    # default nunca se alcanza en operación normal.
    limit_minutes = tenant.flags.get(LIMIT_VOICE_MINUTES_MONTH, 0)
    if limit_minutes == UNLIMITED:
        return
    since = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    used_seconds = await repo.sum_usage_since(
        tenant_id=tenant.tenant_id, kind="voice_seconds", since=since
    )
    if (used_seconds + extra_seconds) > limit_minutes * 60:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Alcanzaste tu límite de {limit_minutes} minutos de voz este mes de tu plan "
                f"'{tenant.plan_key}'."
            ),
        )


def _estimate_seconds_from_audio(raw_audio: bytes) -> float:
    """Aproximación: sin decodificar el audio no conocemos su duración exacta;
    se estima el tamaño asumiendo ~16 kbps (códec de voz comprimido típico)."""
    return round((len(raw_audio) * 8) / 16000.0, 2)


def _estimate_seconds_from_text(text: str) -> float:
    """Aproximación de duración hablada a ~150 palabras por minuto."""
    words = max(len(text.split()), 1)
    return round((words / 150.0) * 60.0, 2)


# Orden importa: bloques de código antes que inline code (para no dejar
# backticks sueltos), énfasis de 2 caracteres (**negrita**, __negrita__)
# antes que el de 1 (*cursiva*, _cursiva_) para no comerse un asterisco de
# cada par. `body.text` es SIEMPRE Markdown (el chat lo renderiza como tal,
# `apps/web/src/components/chat/utils.ts`), así que sin este paso el TTS lee
# literalmente "asterisco asterisco texto asterisco asterisco".
_MD_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_MD_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_MD_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*|__([^_]+)__")
_MD_ITALIC_RE = re.compile(r"\*([^*]+)\*|(?<!\w)_([^_]+)_(?!\w)")
_MD_STRIKETHROUGH_RE = re.compile(r"~~([^~]+)~~")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_HEADER_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MD_BULLET_RE = re.compile(r"^[ \t]*[-*+][ \t]+", re.MULTILINE)
_MD_HR_RE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})[ \t]*$", re.MULTILINE)
_MD_BLOCKQUOTE_RE = re.compile(r"^>[ \t]?", re.MULTILINE)


def _strip_markdown_for_speech(text: str) -> str:
    """Limpia Markdown antes de mandarlo a un TTS -- sin esto, un proveedor
    real (ElevenLabs/Polly/Deepgram) lee los símbolos literales
    ("asterisco asterisco...") en vez del énfasis que representan."""
    cleaned = _MD_CODE_BLOCK_RE.sub(" ", text)
    cleaned = _MD_INLINE_CODE_RE.sub(r"\1", cleaned)
    cleaned = _MD_BOLD_RE.sub(lambda m: m.group(1) or m.group(2), cleaned)
    cleaned = _MD_ITALIC_RE.sub(lambda m: m.group(1) or m.group(2), cleaned)
    cleaned = _MD_STRIKETHROUGH_RE.sub(r"\1", cleaned)
    cleaned = _MD_LINK_RE.sub(r"\1", cleaned)
    cleaned = _MD_HEADER_RE.sub("", cleaned)
    cleaned = _MD_BULLET_RE.sub("", cleaned)
    cleaned = _MD_HR_RE.sub(" ", cleaned)
    cleaned = _MD_BLOCKQUOTE_RE.sub("", cleaned)
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()


# ---------------------------------------------------------------------------
# Resolución de proveedor por tenant (ver docstring del módulo)
# ---------------------------------------------------------------------------


async def _read_tenant_voice_config(
    vault: TokenVault, repo: Repo, tenant_id: uuid.UUID, connector_key: str
) -> dict[str, Any] | None:
    """Config de voz del tenant para `connector_key` ya descifrada + parseada,
    o `None` si no conectó nada ahí (o si algo falla — el llamador decide qué
    hacer con `None`, nunca lanza)."""
    accounts = await repo.list_connector_accounts(tenant_id=tenant_id)
    account = next((a for a in accounts if a["connector_key"] == connector_key), None)
    if account is None:
        return None
    bundle = await vault.get(tenant_id, account["id"])
    if bundle is None:
        return None
    data = json.loads(bundle.access_token)
    return data if isinstance(data, dict) else None


async def _stt_para_tenant(
    vault: TokenVault | None, repo: Repo, tenant_id: uuid.UUID, settings: Settings
) -> STTProvider:
    """STT a usar para este tenant — tenant → stub, SIN paso de plataforma (ver
    docstring del módulo): un tenant sin credencial propia conectada nunca
    reutiliza `DEEPGRAM_API_KEY` de `Settings`. `settings` se conserva en la
    firma por simetría con `_tts_para_tenant` (que sí la necesita para el
    default de voz de Polly), aunque este resolver no la use."""
    if vault is not None:
        try:
            cfg = await _read_tenant_voice_config(vault, repo, tenant_id, VOICE_STT_CONNECTOR_KEY)
        except Exception:
            logger.warning(
                "No se pudo leer la credencial de voz (STT) del tenant_id=%s; uso stub.",
                tenant_id,
                exc_info=True,
            )
            cfg = None
        if cfg is not None and cfg.get("provider") == "deepgram" and cfg.get("api_key"):
            return DeepgramSTT(api_key=cfg["api_key"])

    return StubSTT()


async def _tts_para_tenant(
    vault: TokenVault | None, repo: Repo, tenant_id: uuid.UUID, settings: Settings
) -> TTSProvider:
    """TTS a usar para este tenant — tenant → stub, SIN paso de plataforma (ver
    docstring del módulo): un tenant sin credencial propia conectada nunca
    reutiliza `ELEVENLABS_API_KEY` de `Settings`.

    `provider="polly"` además exige `EDECAN_LOCAL_MODE` (ver docstring del
    módulo): sin eso, aunque el tenant SÍ tenga esa fila guardada, cae a
    `StubTTS` igual que si no hubiera nada — nunca comparte la identidad AWS
    del proceso entre tenants."""
    if vault is not None:
        try:
            cfg = await _read_tenant_voice_config(vault, repo, tenant_id, VOICE_TTS_CONNECTOR_KEY)
        except Exception:
            logger.warning(
                "No se pudo leer la credencial de voz (TTS) del tenant_id=%s; uso stub.",
                tenant_id,
                exc_info=True,
            )
            cfg = None
        if cfg is not None:
            provider = cfg.get("provider")
            if provider == "elevenlabs" and cfg.get("api_key"):
                return ElevenLabsTTS(
                    api_key=cfg["api_key"],
                    default_voice_id=cfg.get("voice_id"),
                    model_id=str(cfg.get("model_id") or "eleven_multilingual_v2"),
                    expressive=bool(cfg.get("expressive", False)),
                )
            if provider == "polly":
                if getattr(settings, "EDECAN_LOCAL_MODE", False):
                    # `allow_ambient_credentials=True`: seguro AQUÍ porque el
                    # `if` de arriba ya confirmó `EDECAN_LOCAL_MODE=True` (ver
                    # docstring del módulo y de `edecan_voice.polly`).
                    voice_id = (
                        cfg.get("voice") or getattr(settings, "POLLY_VOICE", "Lupe") or "Lupe"
                    )
                    return PollyTTS(
                        voice_id=voice_id,
                        region_name=getattr(settings, "AWS_REGION", None),
                        endpoint_url=getattr(settings, "AWS_ENDPOINT_URL", None),
                        allow_ambient_credentials=True,
                    )
                logger.warning(
                    "tenant_id=%s tiene credencial de voz (TTS) 'polly' guardada, pero el "
                    "servidor no corre en EDECAN_LOCAL_MODE: Polly usa la identidad AWS del "
                    "PROCESO, no una credencial propia del tenant, así que no se construye "
                    "fuera de modo local (ver docstring del módulo). Usando StubTTS.",
                    tenant_id,
                )

    return StubTTS()


# ---------------------------------------------------------------------------
# Contrato de veracidad (`edecan_core.veracidad`) — ver su docstring.
#
# `transcribe`/`speak` NO pasan por `Agent.run_turn` (son endpoints HTTP
# directos, llamados desde la página de Voz, no por el modelo), así que el
# `ToolResult.fidelidad` que reparte `edecan_core.agent` no aplica acá — esta
# es la sección "salidas que NO pasan por el agente" del diseño: la única vía
# para avisar es una cabecera HTTP, porque `/speak` devuelve bytes crudos de
# audio (no hay dónde meter un campo JSON) y `/transcribe` no debe romper el
# shape `{"text": ...}` que ya consumen los clientes existentes.
# ---------------------------------------------------------------------------

_MOTIVO_PROVEEDOR_SIN_DECLARAR = (
    "el proveedor de voz usado no declara si es real o simulado (ver "
    "edecan_core.veracidad.ProveedorDeclarado) — tratado como simulado por seguridad"
)


def _info_fidelidad(provider: Any, *, familia: str) -> InfoFidelidad:
    """Mismo criterio fail-closed que `edecan_voice.tools._info_fidelidad_tts`
    (duplicado a propósito, mismo motivo que el resto de este router: no
    depender de otro paquete solo por un helper de 6 líneas): si el
    proveedor no declara fidelidad, se trata como SIMULADO en vez de asumir
    que es real sin haberlo comprobado."""
    if isinstance(provider, ProveedorDeclarado):
        return provider.info_fidelidad()
    return InfoFidelidad(
        familia=familia,
        fidelidad=Fidelidad.SIMULADO,
        fuente=type(provider).__name__,
        motivo_simulado=_MOTIVO_PROVEEDOR_SIN_DECLARAR,
    )


def _set_headers_de_fidelidad(response: Response, info: InfoFidelidad) -> None:
    """Cabeceras `X-Edecan-Fidelidad`/`X-Edecan-Motivo` — ver comentario de
    sección arriba. `response.headers` es un `MutableHeaders` mutable de
    Starlette: modificarlo acá SÍ se refleja en la respuesta final aunque el
    handler devuelva otro objeto (`speak`) o un `dict` (`transcribe`)."""
    response.headers["X-Edecan-Fidelidad"] = info.fidelidad.value
    if info.motivo_simulado:
        response.headers["X-Edecan-Motivo"] = info.motivo_simulado


@router.post("/transcribe")
async def transcribe(
    audio: UploadFile,
    response: Response,
    language: str | None = Form(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    settings: Settings = Depends(get_settings),
    vault: TokenVault | None = Depends(get_vault),
) -> dict[str, str]:
    _require_voice_web(current_user.tenant)
    t_start = time.perf_counter()
    raw = await audio.read()
    estimated_seconds = _estimate_seconds_from_audio(raw)
    await _check_voice_quota(repo, current_user.tenant, estimated_seconds)

    stt = await _stt_para_tenant(vault, repo, current_user.tenant_id, settings)
    transcript = await stt.transcribe(raw, audio.content_type or "audio/webm", language)
    _set_headers_de_fidelidad(response, _info_fidelidad(stt, familia="stt"))

    await repo.add_usage_event(
        tenant_id=current_user.tenant_id, kind="voice_seconds", quantity=estimated_seconds
    )
    logger.info(
        "[/transcribe] latency_ms stt_total=%.1f audio_bytes=%d transcript_len=%d",
        (time.perf_counter() - t_start) * 1000.0,
        len(raw),
        len(transcript.text),
    )
    return {"text": transcript.text}


@router.post("/speak")
async def speak(
    body: SpeakIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    settings: Settings = Depends(get_settings),
    vault: TokenVault | None = Depends(get_vault),
) -> Response:
    _require_voice_web(current_user.tenant)
    t_start = time.perf_counter()
    if body.voice_rewrite:
        spoken_text = rewrite_for_voice(body.text)
    else:
        spoken_text = _strip_markdown_for_speech(body.text)
    logger.info(
        "[/speak] voice_id=%s model_id=%s voice_rewrite=%s has_speech_tags=%s text_preview=%r",
        body.voice_id, body.model_id, body.voice_rewrite,
        any(tag in spoken_text for tag in
            ["[warmly]", "[gently]", "[excited]", "[pause]", "[curious]", "[calmly]"]),
        spoken_text[:300],
    )
    estimated_seconds = _estimate_seconds_from_text(spoken_text)
    await _check_voice_quota(repo, current_user.tenant, estimated_seconds)

    tts = await _tts_para_tenant(vault, repo, current_user.tenant_id, settings)
    logger.info("[/speak] tts_provider=%s has_model_id=%s has_expressive=%s",
                type(tts).__name__, hasattr(tts, "_model_id"), hasattr(tts, "_expressive"))
    if body.model_id and hasattr(tts, "_model_id"):
        tts._model_id = body.model_id
        if body.model_id == "eleven_v3" and hasattr(tts, "_expressive"):
            tts._expressive = True
    audio_bytes = await tts.synthesize(spoken_text, voice_id=body.voice_id)
    logger.info(
        "[/speak] latency_ms first_audio=%.1f total=%.1f tts_provider=%s audio_bytes=%d",
        (time.perf_counter() - t_start) * 1000.0,
        (time.perf_counter() - t_start) * 1000.0,
        type(tts).__name__,
        len(audio_bytes),
    )

    await repo.add_usage_event(
        tenant_id=current_user.tenant_id, kind="voice_seconds", quantity=estimated_seconds
    )
    # StubTTS (proveedor por defecto si el tenant no conectó nada) genera WAV
    # real, no mp3 (ver edecan_voice.stubs.StubTTS); el resto de proveedores
    # (ElevenLabsTTS, PollyTTS, siempre del propio tenant) siempre producen
    # mp3. El Content-Type debe reflejar los bytes devueltos (ver docs/api.md
    # "Voz web").
    media_type = "audio/wav" if isinstance(tts, StubTTS) else "audio/mpeg"
    response = Response(content=audio_bytes, media_type=media_type)
    _set_headers_de_fidelidad(response, _info_fidelidad(tts, familia="tts"))
    return response


@router.post("/speak/stream")
async def speak_stream(
    body: SpeakIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    settings: Settings = Depends(get_settings),
    vault: TokenVault | None = Depends(get_vault),
) -> StreamingResponse:
    """Igual que `/speak`, pero devuelve un `StreamingResponse` en vez de los
    bytes completos (time-to-first-audio, PHASE2.md §9): el cliente empieza a
    reproducir el audio en cuanto llega el primer chunk de ElevenLabs, sin
    esperar a que termine la síntesis del texto completo.

    La resolución de proveedor, el plan, la cuota y el contrato de veracidad
    (cabeceras `X-Edecan-Fidelidad`/`X-Edecan-Motivo`) son los MISMOS que
    `/speak`: solo cambia el transporte. Con `StubTTS` (o Polly, que no tiene
    endpoint de streaming) el generador hereda el default de
    `TTSProvider.synthesize_stream` — rinde el audio completo como un único
    chunk, así que este endpoint degrada limpio al mismo resultado que
    `/speak`, nunca falla por "no soportar streaming"."""
    _require_voice_web(current_user.tenant)
    t_start = time.perf_counter()
    if body.voice_rewrite:
        spoken_text = rewrite_for_voice(body.text)
    else:
        spoken_text = _strip_markdown_for_speech(body.text)
    estimated_seconds = _estimate_seconds_from_text(spoken_text)
    await _check_voice_quota(repo, current_user.tenant, estimated_seconds)

    tts = await _tts_para_tenant(vault, repo, current_user.tenant_id, settings)
    if body.model_id and hasattr(tts, "_model_id"):
        tts._model_id = body.model_id
        if body.model_id == "eleven_v3" and hasattr(tts, "_expressive"):
            tts._expressive = True

    # StubTTS genera WAV; el resto (ElevenLabs/Polly, del propio tenant) mp3 —
    # mismo criterio que `/speak` (ver su comentario).
    media_type = "audio/wav" if isinstance(tts, StubTTS) else "audio/mpeg"
    # `model_id` viaja en el body del streaming de ElevenLabs; para el stub/
    # Polly (que lo ignoran en su `synthesize_stream` default) vale cualquier
    # valor, así que se resuelve del proveedor o cae al default de ElevenLabs.
    model_id = getattr(tts, "_model_id", None) or DEFAULT_MODEL_ID

    # La cuota se registra ANTES de emitir el stream: a diferencia de `/speak`
    # (donde `synthesize` ya terminó cuando se anota el uso), acá el cliente
    # puede cortar la conexión a mitad del streaming — anotar al inicio evita
    # subcontar minutos en el camino más común, y la cuota ya quedó reservada
    # por `_check_voice_quota` arriba.
    await repo.add_usage_event(
        tenant_id=current_user.tenant_id, kind="voice_seconds", quantity=estimated_seconds
    )

    async def _audio_stream() -> AsyncIterator[bytes]:
        first_chunk = True
        async for chunk in tts.synthesize_stream(
            spoken_text, voice_id=body.voice_id, model_id=model_id, mime=media_type
        ):
            if first_chunk:
                logger.info(
                    "[/speak/stream] latency_ms first_audio=%.1f tts_provider=%s",
                    (time.perf_counter() - t_start) * 1000.0,
                    type(tts).__name__,
                )
                first_chunk = False
            yield chunk
        logger.info(
            "[/speak/stream] latency_ms total=%.1f tts_provider=%s",
            (time.perf_counter() - t_start) * 1000.0,
            type(tts).__name__,
        )

    response = StreamingResponse(_audio_stream(), media_type=media_type)
    _set_headers_de_fidelidad(response, _info_fidelidad(tts, familia="tts"))
    return response


# ---------------------------------------------------------------------------
# WebSocket realtime de voz (PHASE3 §194-§198)
# ---------------------------------------------------------------------------

_REALTIME_AUTH_TIMEOUT_SECONDS = 10.0
_REALTIME_MAX_TEXT_CHARS = 20_000
_REALTIME_MAX_AUDIO_FRAME_BYTES = 512 * 1024
_REALTIME_MAX_AUDIO_BYTES = 20 * 1024 * 1024
_REALTIME_MAX_IMAGE_BYTES = 8 * 1024 * 1024
_REALTIME_CLOSE_AUTH = 4401
_REALTIME_CLOSE_FORBIDDEN = 4403
_REALTIME_CLOSE_PROTOCOL = 4400


def _realtime_user_from_token(token: str, settings: Settings) -> CurrentUser:
    try:
        decoded = decode_token(token.strip(), secret=settings.JWT_SECRET, expected_typ="access")
    except (TokenError, ValueError) as exc:
        raise ValueError("token inválido") from exc
    return CurrentUser(
        user_id=decoded.sub,
        tenant=TenantCtx(
            tenant_id=decoded.ten,
            plan_key=decoded.plan,
            flags=flags_for_plan(decoded.plan),
        ),
    )


async def _send_realtime_error(websocket: WebSocket, message: str) -> None:
    await websocket.send_json({"type": "error", "message": message})


@realtime_router.websocket("/realtime")
async def realtime_voice(websocket: WebSocket) -> None:
    """Transporte TTS realtime autenticado e interruptible.

    Protocolo mínimo:

    ``authenticate`` → ``ready`` → ``speak`` → ``audio``* → ``done``

    Un ``interrupt`` puede llegar mientras se emiten chunks: cancela la tarea
    de síntesis, invalida el token del turno y devuelve ``interrupted``. El
    primer frame lleva el bearer para no exponerlo en query strings, logs de
    proxy o historial de URL. Este endpoint no crea mensajes de chat: la
    conversación sigue teniendo un único dueño en `post_message`.
    """

    await websocket.accept()
    settings = get_settings()
    try:
        try:
            first = await asyncio.wait_for(
                websocket.receive_json(), timeout=_REALTIME_AUTH_TIMEOUT_SECONDS
            )
        except (TimeoutError, ValueError, WebSocketDisconnect):
            await websocket.close(code=_REALTIME_CLOSE_AUTH)
            return
        if not isinstance(first, dict) or first.get("type") != "authenticate":
            await websocket.close(code=_REALTIME_CLOSE_AUTH)
            return
        token = first.get("token")
        if not isinstance(token, str) or not token.strip():
            await websocket.close(code=_REALTIME_CLOSE_AUTH)
            return
        try:
            current_user = _realtime_user_from_token(token, settings)
        except ValueError:
            await websocket.close(code=_REALTIME_CLOSE_AUTH)
            return
        if not current_user.tenant.flags.get(FLAG_VOICE_WEB, False):
            await websocket.close(code=_REALTIME_CLOSE_FORBIDDEN)
            return
        voice_conversation_id: uuid.UUID | None = None
        raw_conversation_id = first.get("conversation_id")
        if raw_conversation_id is not None:
            try:
                voice_conversation_id = uuid.UUID(str(raw_conversation_id))
            except (ValueError, TypeError):
                await websocket.close(code=_REALTIME_CLOSE_PROTOCOL)
                return

        async with get_session(current_user.tenant_id) as db_session:
            repo = SqlRepo(db_session)
            vault = TokenVault(db_session, build_key_provider(settings))
            tts = await _tts_para_tenant(
                vault, repo, current_user.tenant_id, settings
            )
            stt = await _stt_para_tenant(
                vault, repo, current_user.tenant_id, settings
            )
            session = RealtimeVoiceSession()
            active_task: asyncio.Task[None] | None = None
            audio_buffer = bytearray()
            audio_turn_id: int | None = None
            audio_mime = "audio/wav"
            visual_content: dict[str, Any] | None = None

            await websocket.send_json(
                {
                    "type": "ready",
                    "protocol": "edecan.voice.realtime.v1",
                    "state": session.state,
                    "mime": "audio/wav" if isinstance(tts, StubTTS) else "audio/mpeg",
                    "conversation_id": (
                        str(voice_conversation_id) if voice_conversation_id else None
                    ),
                }
            )

            async def synthesize(turn_id: int, text: str) -> None:
                media_type = "audio/wav" if isinstance(tts, StubTTS) else "audio/mpeg"
                model_id = getattr(tts, "_model_id", None) or DEFAULT_MODEL_ID
                sequence = 0
                started_at = time.perf_counter()
                first_audio_at: float | None = None
                total_bytes = 0
                try:
                    async for chunk in tts.synthesize_stream(
                        rewrite_for_voice(text),
                        model_id=model_id,
                        mime=media_type,
                    ):
                        if not session.is_current(turn_id):
                            return
                        if first_audio_at is None:
                            first_audio_at = time.perf_counter()
                            logger.info(
                                "[voice_realtime] first_audio_ms=%.1f turn_id=%s provider=%s",
                                (first_audio_at - started_at) * 1000.0,
                                turn_id,
                                type(tts).__name__,
                            )
                        total_bytes += len(chunk)
                        await websocket.send_json(
                            {
                                "type": "audio",
                                "turn_id": turn_id,
                                "sequence": sequence,
                                "mime": media_type,
                                "data": base64.b64encode(chunk).decode("ascii"),
                            }
                        )
                        sequence += 1
                    if session.finish(turn_id):
                        logger.info(
                            "[voice_realtime] total_ms=%.1f turn_id=%s bytes=%d provider=%s",
                            (time.perf_counter() - started_at) * 1000.0,
                            turn_id,
                            total_bytes,
                            type(tts).__name__,
                        )
                        await websocket.send_json(
                            {"type": "done", "turn_id": turn_id, "state": session.state}
                        )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning("fallo en síntesis realtime de voz", exc_info=True)
                    if session.is_current(turn_id):
                        session.interrupt("provider_error")
                        await _send_realtime_error(
                            websocket, "No pude generar el audio de esta respuesta."
                        )

            async def process_audio(
                turn_id: int,
                audio: bytes,
                mime: str,
                image_for_turn: dict[str, Any] | None = None,
            ) -> None:
                estimated_seconds = _estimate_seconds_from_audio(audio)
                try:
                    await _check_voice_quota(
                        repo, current_user.tenant, estimated_seconds
                    )
                    await repo.add_usage_event(
                        tenant_id=current_user.tenant_id,
                        kind="voice_seconds",
                        quantity=estimated_seconds,
                    )
                    transcript = await stt.transcribe(audio, mime)
                    await websocket.send_json(
                        {
                            "type": "transcript",
                            "turn_id": turn_id,
                            "text": transcript.text,
                            "language": transcript.language,
                            "state": session.state,
                        }
                    )
                    if voice_conversation_id is None:
                        session.complete_input(turn_id)
                        return

                    from edecan_api.voice_turn_service import execute_voice_text_turn

                    result = await execute_voice_text_turn(
                        request=websocket,
                        session=db_session,
                        repo=repo,
                        vault=vault,
                        current_user=current_user,
                        settings=settings,
                        llm_router=websocket.app.state.llm_router,
                        conversation_id=voice_conversation_id,
                        user_text=transcript.text,
                        direct_user_content=(
                            [
                                {"type": "text", "text": transcript.text},
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": image_for_turn["mime"],
                                        "data": image_for_turn["data"],
                                    },
                                },
                            ]
                            if image_for_turn is not None
                            else None
                        ),
                    )
                    for event in result.events:
                        event_type = event.get("type")
                        if event_type in {
                            "text_delta",
                            "tool_start",
                            "tool_end",
                            "tool_progress",
                            "confirmation_required",
                        }:
                            await websocket.send_json({"type": "agent_event", "event": event})
                    if result.confirmation_required or not result.text.strip():
                        session.complete_input(turn_id)
                        return
                    tts_seconds = _estimate_seconds_from_text(result.text)
                    await _check_voice_quota(repo, current_user.tenant, tts_seconds)
                    await repo.add_usage_event(
                        tenant_id=current_user.tenant_id,
                        kind="voice_seconds",
                        quantity=tts_seconds,
                    )
                    if not session.begin_speaking(turn_id):
                        raise RuntimeError("el turno de voz perdió su token antes del TTS")
                    await synthesize(turn_id, result.text)
                except asyncio.CancelledError:
                    raise
                except HTTPException as exc:
                    session.interrupt("quota")
                    await _send_realtime_error(websocket, str(exc.detail))
                except Exception:
                    session.interrupt("voice_turn_error")
                    logger.warning("fallo en turno realtime de voz", exc_info=True)
                    await _send_realtime_error(
                        websocket, "No pude completar este turno de voz."
                    )

            try:
                while True:
                    message = await websocket.receive_json()
                    if not isinstance(message, dict):
                        await websocket.close(code=_REALTIME_CLOSE_PROTOCOL)
                        return
                    message_type = message.get("type")
                    if message_type == "ping":
                        await websocket.send_json({"type": "pong"})
                        continue
                    if message_type == "close":
                        return
                    if message_type == "interrupt":
                        if active_task is not None and not active_task.done():
                            session.interrupt("user")
                            active_task.cancel()
                            await websocket.send_json(
                                {"type": "interrupted", "state": session.state}
                            )
                            try:
                                await active_task
                            except asyncio.CancelledError:
                                pass
                        continue
                    if message_type == "audio":
                        raw_data = message.get("data")
                        requested_mime = message.get("mime", "audio/wav")
                        if not isinstance(raw_data, str) or requested_mime not in {
                            "audio/wav",
                            "audio/webm",
                            "audio/mpeg",
                        }:
                            await _send_realtime_error(
                                websocket, "Frame de audio inválido."
                            )
                            continue
                        try:
                            chunk = base64.b64decode(raw_data, validate=True)
                        except (ValueError, base64.binascii.Error):
                            await _send_realtime_error(
                                websocket, "El frame de audio no está codificado correctamente."
                            )
                            continue
                        if not chunk or len(chunk) > _REALTIME_MAX_AUDIO_FRAME_BYTES:
                            await _send_realtime_error(
                                websocket, "El frame de audio excede el límite permitido."
                            )
                            continue
                        if session.state == "idle":
                            audio_turn_id = session.begin_listening()
                            audio_mime = requested_mime
                        if session.state != "listening" or audio_turn_id is None:
                            await _send_realtime_error(
                                websocket, "Inicia un turno de audio antes de enviar frames."
                            )
                            continue
                        if len(audio_buffer) + len(chunk) > _REALTIME_MAX_AUDIO_BYTES:
                            session.interrupt("audio_too_large")
                            audio_buffer.clear()
                            audio_turn_id = None
                            await _send_realtime_error(
                                websocket, "El audio del turno excede el límite permitido."
                            )
                            continue
                        session.append_audio(len(chunk))
                        audio_buffer.extend(chunk)
                        await websocket.send_json(
                            {
                                "type": "audio.accepted",
                                "turn_id": audio_turn_id,
                                "bytes": len(audio_buffer),
                            }
                        )
                        continue
                    if message_type == "image":
                        raw_data = message.get("data")
                        image_mime = message.get("mime", "image/jpeg")
                        if not isinstance(raw_data, str) or image_mime not in {
                            "image/jpeg",
                            "image/png",
                            "image/webp",
                        }:
                            await _send_realtime_error(
                                websocket, "Frame de imagen inválido."
                            )
                            continue
                        try:
                            image_bytes = base64.b64decode(raw_data, validate=True)
                        except (ValueError, base64.binascii.Error):
                            await _send_realtime_error(
                                websocket, "La imagen no está codificada correctamente."
                            )
                            continue
                        if not image_bytes or len(image_bytes) > _REALTIME_MAX_IMAGE_BYTES:
                            await _send_realtime_error(
                                websocket, "La imagen excede el límite permitido."
                            )
                            continue
                        visual_content = {
                            "type": "text_image",
                            "mime": image_mime,
                            "data": base64.b64encode(image_bytes).decode("ascii"),
                        }
                        await websocket.send_json(
                            {"type": "image.accepted", "mime": image_mime}
                        )
                        continue
                    if message_type == "commit":
                        if audio_turn_id is None or not audio_buffer:
                            await _send_realtime_error(
                                websocket, "No hay audio pendiente para transcribir."
                            )
                            continue
                        if not session.commit_audio(audio_turn_id):
                            await _send_realtime_error(
                                websocket, "El turno de audio ya no está disponible."
                            )
                            continue
                        audio = bytes(audio_buffer)
                        turn_id = audio_turn_id
                        audio_buffer.clear()
                        audio_turn_id = None
                        image_for_turn = visual_content
                        visual_content = None
                        active_task = asyncio.create_task(
                            process_audio(turn_id, audio, audio_mime, image_for_turn)
                        )
                        continue
                    if message_type == "speak":
                        text = message.get("text")
                        if not isinstance(text, str) or not text.strip():
                            await _send_realtime_error(
                                websocket, "Falta el texto para sintetizar."
                            )
                            continue
                        if len(text) > _REALTIME_MAX_TEXT_CHARS:
                            await _send_realtime_error(
                                websocket, "El texto de voz es demasiado largo."
                            )
                            continue
                        estimated_seconds = _estimate_seconds_from_text(text)
                        try:
                            await _check_voice_quota(
                                repo, current_user.tenant, estimated_seconds
                            )
                            await repo.add_usage_event(
                                tenant_id=current_user.tenant_id,
                                kind="voice_seconds",
                                quantity=estimated_seconds,
                            )
                        except HTTPException as exc:
                            await _send_realtime_error(websocket, str(exc.detail))
                            continue
                        if active_task is not None and not active_task.done():
                            session.interrupt("superseded")
                            active_task.cancel()
                            try:
                                await active_task
                            except asyncio.CancelledError:
                                pass
                        try:
                            turn_id = session.begin_listening()
                            if not session.commit_audio(turn_id):
                                raise RuntimeError("no se pudo confirmar el turno")
                            if not session.begin_speaking(turn_id):
                                raise RuntimeError("no se pudo iniciar la reproducción")
                        except RuntimeError:
                            await _send_realtime_error(
                                websocket, "La sesión de voz no está disponible."
                            )
                            continue
                        await websocket.send_json(
                            {"type": "speaking", "turn_id": turn_id, "state": session.state}
                        )
                        active_task = asyncio.create_task(synthesize(turn_id, text.strip()))
                        continue
                    await websocket.close(code=_REALTIME_CLOSE_PROTOCOL)
                    return
            finally:
                if active_task is not None and not active_task.done():
                    session.interrupt("socket_closed")
                    active_task.cancel()
                    try:
                        await active_task
                    except asyncio.CancelledError:
                        pass
                session.close()
    except WebSocketDisconnect:
        return
    except Exception:
        logger.warning("fallo inesperado en WebSocket realtime de voz", exc_info=True)
        if websocket.client_state.name != "DISCONNECTED":
            await websocket.close(code=1011)
