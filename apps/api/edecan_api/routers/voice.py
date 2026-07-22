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

import json
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from edecan_db.vault import TokenVault
from edecan_schemas import UNLIMITED
from edecan_schemas.plans import FLAG_VOICE_WEB, LIMIT_VOICE_MINUTES_MONTH
from edecan_voice.base import STTProvider, TTSProvider
from edecan_voice.deepgram import DeepgramSTT
from edecan_voice.elevenlabs import ElevenLabsTTS
from edecan_voice.polly import PollyTTS
from edecan_voice.stubs import StubSTT, StubTTS
from fastapi import APIRouter, Depends, Form, HTTPException, Response, UploadFile, status
from pydantic import BaseModel

from edecan_api.config import Settings, get_settings
from edecan_api.deps import (
    VOICE_STT_CONNECTOR_KEY,
    VOICE_TTS_CONNECTOR_KEY,
    CurrentUser,
    TenantCtx,
    get_current_user,
    get_repo,
    get_vault,
    rate_limit,
)
from edecan_api.repo import Repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/voice", tags=["voice"], dependencies=[Depends(rate_limit)])


class SpeakIn(BaseModel):
    text: str
    voice_id: str | None = None


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


@router.post("/transcribe")
async def transcribe(
    audio: UploadFile,
    language: str | None = Form(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    settings: Settings = Depends(get_settings),
    vault: TokenVault | None = Depends(get_vault),
) -> dict[str, str]:
    _require_voice_web(current_user.tenant)
    raw = await audio.read()
    estimated_seconds = _estimate_seconds_from_audio(raw)
    await _check_voice_quota(repo, current_user.tenant, estimated_seconds)

    stt = await _stt_para_tenant(vault, repo, current_user.tenant_id, settings)
    transcript = await stt.transcribe(raw, audio.content_type or "audio/webm", language)

    await repo.add_usage_event(
        tenant_id=current_user.tenant_id, kind="voice_seconds", quantity=estimated_seconds
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
    spoken_text = _strip_markdown_for_speech(body.text)
    estimated_seconds = _estimate_seconds_from_text(spoken_text)
    await _check_voice_quota(repo, current_user.tenant, estimated_seconds)

    tts = await _tts_para_tenant(vault, repo, current_user.tenant_id, settings)
    audio_bytes = await tts.synthesize(spoken_text, voice_id=body.voice_id)

    await repo.add_usage_event(
        tenant_id=current_user.tenant_id, kind="voice_seconds", quantity=estimated_seconds
    )
    # StubTTS (proveedor por defecto si el tenant no conectó nada) genera WAV
    # real, no mp3 (ver edecan_voice.stubs.StubTTS); el resto de proveedores
    # (ElevenLabsTTS, PollyTTS, siempre del propio tenant) siempre producen
    # mp3. El Content-Type debe reflejar los bytes devueltos (ver docs/api.md
    # "Voz web").
    media_type = "audio/wav" if isinstance(tts, StubTTS) else "audio/mpeg"
    return Response(content=audio_bytes, media_type=media_type)
