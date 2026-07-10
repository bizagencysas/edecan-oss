"""Podcasts y efectos de sonido con el TTS del propio tenant (`ARCHITECTURE.md`
§14, dueño WP-V5-01; work package real WP-V5-11).

Cubre la categoría "Creatividad" del wishlist (`REQUISITOS_V2.md`) para
podcasts y una porción de música/audio: el guion se sintetiza segmento a
segmento con el proveedor **TTS del propio tenant** (ElevenLabs bring-your-
own, o un stub offline si no conectó nada) — nunca con una credencial de
PLATAFORMA (`DIRECCION_ACTUAL.md` "Modelo de credenciales: TODO lo trae el
cliente, siempre").

## Consolidación con `edecan_voice` (WP-V6-04, `ARCHITECTURE.md` §14/§10.1)

Hasta v5, este módulo duplicaba a propósito la resolución TTS bring-your-own
y la llamada a `sound-generation` de ElevenLabs que también construyó, en
paralelo, `WP-V5-10` (`packages/voice/edecan_voice`) — independencia de
aterrizaje entre los dos work packages mientras ninguno de los dos podía
asumir que el otro ya existía en el workspace. Ambos paquetes ya están
asentados (`edecan-voice` es dependencia declarada de `apps/api`/
`apps/worker` desde v5), así que `packages/creative/pyproject.toml` ahora
declara `edecan-voice` como dependencia real (workspace source, igual que
`edecan-core`/`edecan-schemas`/`edecan-db`) y este módulo delega la parte de
red en las implementaciones de `edecan_voice`:

- `resolver_config_tts_tenant` arma un `ctx` mínimo (`SimpleNamespace` con
  `tenant_id`/`session`/`vault`) y delega en
  **`edecan_voice.tenant.resolver_config_tts_del_tenant`** — NO en
  `edecan_voice.tenant.resolver_tts_del_tenant` (que también se evaluó, ver
  "Diferencia resuelta" abajo). Mismo contrato exacto `dict[str, Any] | None`
  que ya devolvía esta función: se verificó línea por línea contra la
  implementación de `edecan_creative` de antes de esta consolidación y son
  funcionalmente IDÉNTICAS (misma query SQL sobre `connector_accounts`,
  mismo `connector_key="voice_tts"`, mismo `vault.get(...)`, mismo
  `json.loads(bundle.access_token)`, mismo `except Exception` → `None` — solo
  cambia el texto del `logger.warning`, sin efecto observable).
- `_elevenlabs_sound_generation` delega en
  **`edecan_voice.cloning.generar_efecto`** para la llamada HTTP real
  (mismo endpoint `POST /v1/sound-generation`, mismos headers, mismo payload
  `{"text": ...}`), traduciendo `edecan_voice.cloning.VoiceCloningError` a
  `SintesisError` en el borde (ver "Diferencia resuelta" abajo) para que el
  contrato público de este módulo (la excepción que ven `tools.py`/
  `generate_podcast.py` y que ya cubren los tests existentes,
  `test_generar_efecto_elevenlabs_error_se_propaga`) no cambie.

### Diferencia resuelta: `resolver_tts_del_tenant` vs `resolver_config_tts_del_tenant`

El paquete de trabajo original de esta consolidación pedía delegar
`resolver_config_tts_tenant` en `edecan_voice.tenant.resolver_tts_del_tenant`
— pero esa función devuelve un `TTSProvider` YA CONSTRUIDO (`ElevenLabsTTS`/
`PollyTTS`/`StubTTS`, pensado para invocarse vía `.synthesize(...)`), no el
`dict` crudo (`{"provider", "api_key", "voice_id"}`) que necesita el resto de
este módulo: `sintetizar_segmento`/`generar_efecto`/`_cfg_elevenlabs_utilizable`
ya inspeccionan `cfg.get("provider")`/`cfg.get("api_key")` directo, y así lo
consumen `edecan_creative.tools.GenerarEfectoSonidoTool` y
`apps/worker/edecan_worker/handlers/generate_podcast.py` (que además resuelve
la config UNA sola vez por job y la reusa en cada segmento — no tiene sentido
con un `TTSProvider` ya atado a una única llamada). Delegar ahí habría roto
ese contrato en tiempo de ejecución (un `TTSProvider` no tiene `.get()`).
`resolver_config_tts_del_tenant` —la hermana de `resolver_tts_del_tenant`
dentro del mismo módulo `edecan_voice.tenant`— sí devuelve exactamente el
mismo `dict[str, Any] | None` que ya prometía `resolver_config_tts_tenant`,
así que es la que preserva el contrato público sin ningún cambio para
`tools.py`/`generate_podcast.py` (ninguno de los dos se tocó para esta
consolidación) ni para los tests existentes de este archivo.

### Duplicación que sigue igual (fuera de alcance de esta consolidación)

`sintetizar_segmento`/`_elevenlabs_text_to_speech` (síntesis de voz de un
segmento, a diferencia de un efecto de sonido) NO se tocaron: el paquete de
trabajo de esta consolidación solo pidió `resolver_config_tts_tenant` y
`_elevenlabs_sound_generation`/efectos. `edecan_voice.elevenlabs.ElevenLabsTTS`
expone la síntesis de voz detrás de la interfaz `TTSProvider.synthesize(...)`
(no una función suelta equivalente a `_elevenlabs_text_to_speech`), así que
consolidar esa mitad implicaría un cambio de forma mayor (pasar de una config
`dict` a instanciar y usar un `TTSProvider`) — se deja documentado aquí como
candidato para una ola futura, mismo criterio que `IMAGES_CONNECTOR_KEY`
duplicado entre `edecan_creative.providers` y
`apps/api/edecan_api/routers/credentials.py`, o `LLM_CONNECTOR_KEY`
duplicado entre `apps/api/edecan_api/deps.py` y
`apps/worker/edecan_worker/deps.py` (`ARCHITECTURE.md` §10.1: paquetes/apps
hermanos no se importan entre sí salvo consolidación explícita como esta).
Los stubs offline (`_wav_silencio`/`_beep_wav_stub`) tampoco se tocaron —
siguen 100% puro-Python, sin depender de `edecan_voice.stubs.StubTTS`.

## Patrón anti-fuga (bring-your-own, fail-closed)

`resolver_config_tts_tenant` SOLO lee la credencial que el propio tenant
conectó (`PUT /v1/credentials/voice/tts`, `TokenVault` connector_key
`"voice_tts"` — mismo string que `apps/api/edecan_api/deps.VOICE_TTS_CONNECTOR_KEY`
y `edecan_voice.tenant.VOICE_TTS_CONNECTOR_KEY`). Ninguna función de este
módulo acepta ni lee jamás un objeto `settings`/`.env` de plataforma: no hay
ningún parámetro por el que `ELEVENLABS_API_KEY` de PLATAFORMA pueda colarse
como fallback silencioso — el bug de fuga de credencial más serio del
proyecto hasta ahora (`packages/llm/edecan_llm/router.py::_build_provider_from_config`,
ver `ARCHITECTURE.md` §13 y `DIRECCION_ACTUAL.md` "v4 completado", hallazgo
#1) es la referencia exacta de qué NO hacer. Sin credencial utilizable (o si
cualquier paso de la resolución falla), todo cae a un stub 100% offline:
`sintetizar_segmento` produce un WAV de silencio corto y `generar_efecto`
produce un WAV de un beep de prueba — nunca una llamada de red real, nunca
gasta cuota de nadie.

## Ensamblado (`ensamblar_podcast`)

Los segmentos de un mismo podcast comparten SIEMPRE el mismo proveedor
resuelto (una sola resolución por job, ver
`apps/worker/edecan_worker/handlers/generate_podcast.py`), así que todos
salen en el mismo formato: `wav` (stub) o `mp3` (ElevenLabs). `wav` se
concatena PURO-PYTHON con el módulo estándar `wave` (sin ffmpeg: los stubs
son controlados por este mismo paquete, así que se garantiza que comparten
parámetros). `mp3` se concatena con **ffmpeg del sistema** (concat demuxer),
calcando el patrón de `edecan_docanalysis.video`
(`ffmpeg_disponible()`/`asyncio.create_subprocess_exec` en una lista de
argumentos, JAMÁS `shell=True`, `tempfile.TemporaryDirectory`, mensaje
instructivo si falta el binario) — duplicado localmente por el mismo motivo
de independencia de aterrizaje que arriba (`edecan_docanalysis` es un
paquete hermano, `ARCHITECTURE.md` §10.1).
"""

from __future__ import annotations

import asyncio
import io
import logging
import math
import os
import re
import shutil
import struct
import tempfile
import unicodedata
import wave
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import httpx
from edecan_voice.cloning import VoiceCloningError as _VoiceCloningError
from edecan_voice.cloning import generar_efecto as _generar_efecto_elevenlabs
from edecan_voice.tenant import resolver_config_tts_del_tenant as _resolver_config_tts_del_tenant

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Guion / segmentos
# ---------------------------------------------------------------------------

MIN_SEGMENTOS = 1
MAX_SEGMENTOS = 30
MAX_CHARS_TEXTO_SEGMENTO = 2000
MAX_CHARS_TOTAL_GUION = 30000
_MAX_CHARS_ORADOR = 200


@dataclass(frozen=True)
class SegmentoPodcast:
    """Un segmento (turno de un orador) del guion de un podcast."""

    orador: str
    texto: str
    voice_id: str | None = None


class GuionInvalidoError(ValueError):
    """Guion de podcast inválido — `str(exc)` ya es un mensaje en español
    accionable, listo para devolverse tal cual como `ToolResult.content`
    (`edecan_creative.tools.CrearPodcastTool`) o para dejarse propagar como
    fallo del job (`generate_podcast`, worker)."""


def _cap_str(value: Any, max_chars: int) -> str:
    return str(value or "").strip()[:max_chars]


def validar_guion(segmentos: Any) -> list[SegmentoPodcast]:
    """Valida y normaliza el guion crudo (lista de dicts, típicamente ya
    parseada de JSON — argumentos de tool o payload de job) a una lista de
    `SegmentoPodcast`.

    Reglas (errores en español, accionables — `GuionInvalidoError`):
    - Entre `MIN_SEGMENTOS` (1) y `MAX_SEGMENTOS` (30) segmentos.
    - Cada segmento necesita `texto` no vacío, de hasta
      `MAX_CHARS_TEXTO_SEGMENTO` (2000) caracteres.
    - El guion completo (suma de todos los `texto`) no puede superar
      `MAX_CHARS_TOTAL_GUION` (30000) caracteres.

    `orador` es opcional (por defecto `"Orador N"`) y se acota en silencio a
    `_MAX_CHARS_ORADOR` — no es una condición de error, solo higiene.
    `voice_id` pasa tal cual (o `None`) para que `sintetizar_segmento` decida
    la voz por segmento.
    """
    if not isinstance(segmentos, list) or not segmentos:
        raise GuionInvalidoError(
            f"El guion del podcast necesita entre {MIN_SEGMENTOS} y {MAX_SEGMENTOS} "
            "segmentos (cada uno con 'texto'); no llegó ninguno."
        )
    if len(segmentos) > MAX_SEGMENTOS:
        raise GuionInvalidoError(
            f"El guion tiene {len(segmentos)} segmentos; el máximo es {MAX_SEGMENTOS}."
        )

    resultado: list[SegmentoPodcast] = []
    total_chars = 0
    for idx, item in enumerate(segmentos, start=1):
        if not isinstance(item, dict):
            raise GuionInvalidoError(f"El segmento {idx} del guion no es un objeto válido.")
        texto = _cap_str(item.get("texto"), MAX_CHARS_TEXTO_SEGMENTO + 1)
        if not texto:
            raise GuionInvalidoError(f"El segmento {idx} del guion no tiene 'texto'.")
        if len(texto) > MAX_CHARS_TEXTO_SEGMENTO:
            raise GuionInvalidoError(
                f"El segmento {idx} tiene {len(texto)} caracteres; el máximo por "
                f"segmento es {MAX_CHARS_TEXTO_SEGMENTO}."
            )
        orador = _cap_str(item.get("orador"), _MAX_CHARS_ORADOR) or f"Orador {idx}"
        voice_id_raw = item.get("voice_id")
        voice_id = str(voice_id_raw).strip() or None if voice_id_raw else None

        total_chars += len(texto)
        resultado.append(SegmentoPodcast(orador=orador, texto=texto, voice_id=voice_id))

    if total_chars > MAX_CHARS_TOTAL_GUION:
        raise GuionInvalidoError(
            f"El guion completo tiene {total_chars} caracteres entre todos los "
            f"segmentos; el máximo total es {MAX_CHARS_TOTAL_GUION}."
        )
    return resultado


# ---------------------------------------------------------------------------
# TTS bring-your-own del tenant — ver docstring del módulo
# ---------------------------------------------------------------------------

# Mismo string EXACTO que `apps/api/edecan_api/deps.VOICE_TTS_CONNECTOR_KEY` y
# `edecan_voice.tenant.VOICE_TTS_CONNECTOR_KEY` (duplicado a propósito, ver
# docstring del módulo).
VOICE_TTS_CONNECTOR_KEY = "voice_tts"

_ELEVENLABS_TTS_URL_TEMPLATE = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
# Efectos de sonido ya no arman esta URL localmente — delegado en
# `edecan_voice.cloning.generar_efecto` (mismo valor que su `_BASE_URL +
# "/sound-generation"`), ver "Consolidación con edecan_voice" arriba.
_ELEVENLABS_MODEL_ID = "eleven_multilingual_v2"
_ELEVENLABS_TTS_TIMEOUT_SECONDS = 30.0
_ELEVENLABS_EFFECT_TIMEOUT_SECONDS = 60.0
_ERROR_SNIPPET_CHARS = 300


class SintesisError(Exception):
    """Error hablando con el proveedor TTS del tenant (ElevenLabs).

    El mensaje SIEMPRE se arma con el `status_code`/cuerpo de la RESPUESTA
    del proveedor — nunca con los headers de la petición saliente (donde
    vive `api_key`), mismo criterio que
    `edecan_voice.cloning.VoiceCloningError`."""


@dataclass(frozen=True)
class AudioGenerado:
    """Resultado de sintetizar un segmento o un efecto de sonido."""

    data: bytes
    formato: Literal["wav", "mp3"]
    es_stub: bool


async def resolver_config_tts_tenant(
    *, session: Any, vault: Any, tenant_id: Any
) -> dict[str, Any] | None:
    """Config de TTS del tenant ya descifrada + parseada (mismo shape que
    guarda `PUT /v1/credentials/voice/tts`:
    `{"provider": "elevenlabs", "api_key", "voice_id"}` u otro proveedor), o
    `None` si el tenant no conectó nada ahí (o si cualquier paso de la
    resolución falla — vault caído, JSON corrupto, etc.). NUNCA lanza y
    NUNCA lee configuración de plataforma (ver docstring del módulo): sin
    `session`/`vault`/`tenant_id` o sin cuenta conectada, se devuelve `None`
    directo — el llamador (`sintetizar_segmento`/`generar_efecto`) trata
    `None` como "usa el stub".

    Delegado en `edecan_voice.tenant.resolver_config_tts_del_tenant` desde la
    consolidación de WP-V6-04 (ver "Consolidación con edecan_voice" en el
    docstring del módulo) — mismo contrato `dict[str, Any] | None`
    exactamente, verificado línea por línea contra la implementación previa
    de este módulo. Se arma un `ctx` mínimo por duck-typing (esa función solo
    lee `ctx.tenant_id`/`ctx.session`/`ctx.vault`) para no tener que cambiar
    la firma de `session`/`vault`/`tenant_id` sueltos que ya usan
    `tools.py`/`generate_podcast.py`."""
    ctx = SimpleNamespace(tenant_id=tenant_id, session=session, vault=vault)
    return await _resolver_config_tts_del_tenant(ctx)


def _cfg_elevenlabs_utilizable(cfg: dict[str, Any] | None) -> bool:
    return bool(cfg) and cfg.get("provider") == "elevenlabs" and bool(cfg.get("api_key"))


def _aviso_stub(tenant_id: Any, *, para_que: str) -> None:
    logger.warning(
        "tenant_id=%s no tiene una credencial de voz (TTS) propia conectada (o no es "
        "utilizable); %s se genera con audio stub, NUNCA con una credencial de "
        "plataforma. Conecta tu propia credencial en Configuración -> "
        "PUT /v1/credentials/voice/tts.",
        tenant_id,
        para_que,
    )


async def sintetizar_segmento(
    cfg: dict[str, Any] | None,
    *,
    texto: str,
    voice_id: str | None = None,
    tenant_id: Any = None,
) -> AudioGenerado:
    """Sintetiza un segmento de podcast a partir de `cfg` (ya resuelto por
    `resolver_config_tts_tenant` — se pasa por parámetro, no se re-resuelve
    aquí, para que un podcast de N segmentos pague un solo round-trip de
    sesión/vault en vez de N).

    Con ElevenLabs conectado (`cfg["provider"] == "elevenlabs"`) devuelve
    mp3 real; un fallo real hablando con ElevenLabs (`SintesisError`, o
    cualquier error de red) se deja propagar tal cual — el tenant SÍ conectó
    una credencial, así que degradar en silencio a un stub sería engañoso
    (ver `apps/worker/edecan_worker/handlers/generate_podcast.py`, "Errores:
    deja propagar"). Sin credencial utilizable, devuelve un WAV corto de
    silencio determinista (`es_stub=True`) — nunca revienta."""
    if _cfg_elevenlabs_utilizable(cfg):
        assert cfg is not None  # para mypy/lectores: ya lo garantizó _cfg_elevenlabs_utilizable
        data = await _elevenlabs_text_to_speech(
            api_key=cfg["api_key"], texto=texto, voice_id=voice_id or cfg.get("voice_id")
        )
        return AudioGenerado(data=data, formato="mp3", es_stub=False)

    _aviso_stub(tenant_id, para_que="este segmento del podcast")
    return AudioGenerado(data=_wav_silencio(), formato="wav", es_stub=True)


async def generar_efecto(
    cfg: dict[str, Any] | None, *, descripcion: str, tenant_id: Any = None
) -> AudioGenerado:
    """Efecto de sonido (sin voz) a partir de `cfg` (ver `sintetizar_segmento`
    para el criterio de dónde se resuelve `cfg`). Con ElevenLabs conectado
    llama `POST /v1/sound-generation` (httpx directo — NO depende de
    `edecan_voice.cloning.generar_efecto`, ver docstring del módulo) y
    devuelve mp3 real; sin credencial cae a un beep WAV determinista de 1s
    marcado `es_stub=True`."""
    if _cfg_elevenlabs_utilizable(cfg):
        assert cfg is not None
        data = await _elevenlabs_sound_generation(api_key=cfg["api_key"], descripcion=descripcion)
        return AudioGenerado(data=data, formato="mp3", es_stub=False)

    _aviso_stub(tenant_id, para_que="el efecto de sonido")
    return AudioGenerado(data=_beep_wav_stub(), formato="wav", es_stub=True)


def _error_desde_respuesta(accion: str, response: httpx.Response) -> SintesisError:
    snippet = response.text[:_ERROR_SNIPPET_CHARS]
    return SintesisError(f"ElevenLabs rechazó {accion} (status {response.status_code}): {snippet}")


async def _elevenlabs_text_to_speech(
    *, api_key: str, texto: str, voice_id: str | None
) -> bytes:
    """`POST /v1/text-to-speech/{voice_id}` — mismo contrato que
    `edecan_voice.elevenlabs.ElevenLabsTTS`, duplicado aquí (ver docstring
    del módulo)."""
    if not voice_id:
        raise SintesisError(
            "Este segmento no trae 'voice_id' y tu credencial de ElevenLabs tampoco tiene "
            "una voz por defecto configurada (PUT /v1/credentials/voice/tts)."
        )
    headers = {"xi-api-key": api_key}
    payload = {"text": texto, "model_id": _ELEVENLABS_MODEL_ID}
    url = _ELEVENLABS_TTS_URL_TEMPLATE.format(voice_id=voice_id)
    async with httpx.AsyncClient(timeout=_ELEVENLABS_TTS_TIMEOUT_SECONDS) as client:
        try:
            response = await client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise SintesisError(f"No pudimos conectar con ElevenLabs: {exc}") from exc
    if response.status_code != 200:
        raise _error_desde_respuesta("la síntesis de voz de un segmento", response)
    return response.content


async def _elevenlabs_sound_generation(*, api_key: str, descripcion: str) -> bytes:
    """`POST /v1/sound-generation` — delegado en
    `edecan_voice.cloning.generar_efecto` desde la consolidación de WP-V6-04
    (ver "Consolidación con edecan_voice" en el docstring del módulo): mismo
    endpoint, mismos headers (`xi-api-key`), mismo payload `{"text": ...}`.
    La única diferencia real entre las dos implementaciones era el tipo de
    excepción (`edecan_voice.cloning.VoiceCloningError` vs. `SintesisError`
    de este módulo) — se traduce aquí, en el borde, para que el contrato
    público de `generar_efecto`/`GenerarEfectoSonidoTool` (que ya cubren
    `test_generar_efecto_elevenlabs_error_se_propaga` y
    `test_sin_credencial_del_tenant_nunca_llama_a_elevenlabs_aunque_haya_centinela`)
    no cambie."""
    try:
        return await _generar_efecto_elevenlabs(
            api_key, descripcion, timeout=_ELEVENLABS_EFFECT_TIMEOUT_SECONDS
        )
    except _VoiceCloningError as exc:
        raise SintesisError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Stubs de audio — puro `wave` (sin red, sin dependencias nuevas)
# ---------------------------------------------------------------------------

_SAMPLE_RATE_HZ = 16_000
_SAMPLE_WIDTH_BYTES = 2  # PCM de 16 bits
_CHANNELS = 1
_SILENCIO_SEGUNDOS = 0.5
_BEEP_SEGUNDOS = 1.0
_BEEP_FRECUENCIA_HZ = 440.0  # La central (A4), tono de prueba estándar
_BEEP_AMPLITUD = 12000  # < 32767 (int16) para evitar clipping


def _wav_silencio(*, duracion_s: float = _SILENCIO_SEGUNDOS) -> bytes:
    """WAV PCM de silencio — calcado de `edecan_voice.stubs.StubTTS`
    (duplicado localmente, ver docstring del módulo: `edecan_creative` no
    depende de `edecan_voice`)."""
    n_frames = int(_SAMPLE_RATE_HZ * duracion_s)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(_CHANNELS)
        wav_file.setsampwidth(_SAMPLE_WIDTH_BYTES)
        wav_file.setframerate(_SAMPLE_RATE_HZ)
        wav_file.writeframes(b"\x00" * n_frames * _SAMPLE_WIDTH_BYTES)
    return buffer.getvalue()


def _beep_wav_stub() -> bytes:
    """WAV PCM de un beep determinista de 1s (440 Hz) — puro `wave` + `math`
    + `struct` (stdlib), sin `audioop` (deprecado desde Python 3.11, PEP 594)
    y sin llamar jamás a ningún proveedor real: el resultado de
    `generar_efecto` cuando el tenant no tiene una credencial TTS propia
    conectada."""
    n_frames = int(_SAMPLE_RATE_HZ * _BEEP_SEGUNDOS)
    samples = bytearray()
    for i in range(n_frames):
        valor = int(
            _BEEP_AMPLITUD * math.sin(2 * math.pi * _BEEP_FRECUENCIA_HZ * i / _SAMPLE_RATE_HZ)
        )
        samples += struct.pack("<h", valor)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(_CHANNELS)
        wav_file.setsampwidth(_SAMPLE_WIDTH_BYTES)
        wav_file.setframerate(_SAMPLE_RATE_HZ)
        wav_file.writeframes(bytes(samples))
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Ensamblado final — ver docstring del módulo
# ---------------------------------------------------------------------------

FORMATOS_SOPORTADOS = ("wav", "mp3")

FFMPEG_INSTALL_HINT = (
    "ensamblar_podcast necesita el binario ffmpeg instalado en esta máquina para unir "
    "los segmentos mp3 del podcast, y no lo encontré. Instálalo con 'brew install ffmpeg' "
    "(macOS) o 'apt install ffmpeg' (Linux/Debian/Ubuntu); en Windows descarga el binario "
    "desde https://ffmpeg.org/download.html y agrégalo al PATH. Vuelve a intentar después "
    "de instalarlo."
)

_FFMPEG_TIMEOUT_SECONDS = 60.0


class EnsambladoError(Exception):
    """Error ensamblando el podcast final: guion vacío, WAVs con parámetros
    incompatibles, ffmpeg ausente/con error. Mensaje en español, listo para
    mostrarse (o para dejarse propagar como fallo de job)."""


def ffmpeg_disponible() -> str | None:
    """Ruta al binario `ffmpeg`, o `None` si no está instalado / no está en
    el PATH. Calcado de `edecan_docanalysis.video.ffmpeg_disponible` (mismo
    criterio de `FFMPEG_PATH`, duplicado aquí — ver docstring del módulo)."""
    return os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg")


def _concatenar_wav(archivos_audio: list[bytes]) -> bytes:
    """Concatena N clips WAV PCM con el módulo estándar `wave` — sin ffmpeg,
    sin dependencias nuevas. Todos los clips deben compartir canales/ancho de
    muestra/frecuencia (siempre el caso real: todos vienen del mismo stub de
    este módulo dentro de un mismo job) — si no, `EnsambladoError` con un
    mensaje claro en vez de producir un WAV corrupto."""
    with wave.open(io.BytesIO(archivos_audio[0]), "rb") as primero:
        params = primero.getparams()
        frames = [primero.readframes(primero.getnframes())]

    for idx, data in enumerate(archivos_audio[1:], start=2):
        with wave.open(io.BytesIO(data), "rb") as clip:
            actuales = clip.getparams()
            if (actuales.nchannels, actuales.sampwidth, actuales.framerate) != (
                params.nchannels,
                params.sampwidth,
                params.framerate,
            ):
                raise EnsambladoError(
                    f"El segmento {idx} tiene un formato WAV distinto al del primer "
                    "segmento (canales/resolución/frecuencia) y no se puede unir sin "
                    "ffmpeg."
                )
            frames.append(clip.readframes(clip.getnframes()))

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as salida:
        salida.setnchannels(params.nchannels)
        salida.setsampwidth(params.sampwidth)
        salida.setframerate(params.framerate)
        for chunk in frames:
            salida.writeframes(chunk)
    return buffer.getvalue()


async def _concatenar_mp3_ffmpeg(
    archivos_audio: list[bytes], *, timeout_seconds: float = _FFMPEG_TIMEOUT_SECONDS
) -> bytes:
    """Concatena N clips mp3 con el "concat demuxer" de ffmpeg — subproceso
    vía `asyncio.create_subprocess_exec` (JAMÁS `shell=True`), mismo patrón
    que `edecan_docanalysis.video._ejecutar_ffmpeg` (duplicado localmente,
    ver docstring del módulo)."""
    ffmpeg_path = ffmpeg_disponible()
    if ffmpeg_path is None:
        raise EnsambladoError(FFMPEG_INSTALL_HINT)

    with tempfile.TemporaryDirectory(prefix="edecan_podcast_") as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        lineas = []
        for idx, data in enumerate(archivos_audio):
            clip_path = tmpdir / f"segmento_{idx:03d}.mp3"
            clip_path.write_bytes(data)
            lineas.append(f"file '{clip_path.name}'")
        lista_path = tmpdir / "lista.txt"
        lista_path.write_text("\n".join(lineas), encoding="utf-8")

        salida_path = tmpdir / "salida.mp3"
        args = [
            ffmpeg_path,
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(lista_path),
            "-c",
            "copy",
            str(salida_path),
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(tmpdir),
            )
        except FileNotFoundError as exc:
            raise EnsambladoError(FFMPEG_INSTALL_HINT) from exc
        except OSError as exc:
            raise EnsambladoError(f"No pude ejecutar ffmpeg: {exc}") from exc

        try:
            _stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=timeout_seconds
            )
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise EnsambladoError(
                f"ffmpeg no terminó en {timeout_seconds:.0f}s ensamblando el podcast."
            ) from exc

        if process.returncode != 0:
            detalle = stderr_bytes.decode("utf-8", errors="replace").strip()
            raise EnsambladoError(
                f"ffmpeg terminó con error (código {process.returncode}) ensamblando el "
                f"podcast: {detalle[-500:] or 'sin detalle'}"
            )
        if not salida_path.exists():
            raise EnsambladoError("ffmpeg no generó el archivo final del podcast.")
        return salida_path.read_bytes()


async def ensamblar_podcast(archivos_audio: list[bytes], formato: Literal["wav", "mp3"]) -> bytes:
    """Ensambla los clips de audio (uno por segmento, en orden) en un único
    podcast. `formato` selecciona la estrategia — no se auto-detecta del
    contenido porque, dentro de un mismo job, todos los clips SIEMPRE
    comparten el mismo proveedor resuelto (ver docstring del módulo):

    - `"wav"` (caso StubTTS): concatena PURO-PYTHON con `wave`.
    - `"mp3"` (caso ElevenLabs): concatena con el ffmpeg del sistema (concat
      demuxer).

    Un solo clip se devuelve tal cual, sin invocar `wave` ni ffmpeg (no hay
    nada que unir)."""
    if not archivos_audio:
        raise EnsambladoError("No hay audio que ensamblar (el guion no produjo segmentos).")
    if len(archivos_audio) == 1:
        return archivos_audio[0]
    if formato == "wav":
        return _concatenar_wav(archivos_audio)
    if formato == "mp3":
        return await _concatenar_mp3_ffmpeg(archivos_audio)
    raise EnsambladoError(f"Formato de podcast no soportado: {formato!r} (usa 'wav' o 'mp3').")


# ---------------------------------------------------------------------------
# Nombre de archivo
# ---------------------------------------------------------------------------


def slugify(texto: str) -> str:
    """Nombre de archivo seguro derivado de `texto` — mismo algoritmo que
    `edecan_creative.tools._slug` (duplicado como función pública aquí para
    que `apps/worker/edecan_worker/handlers/generate_podcast.py` pueda
    importarlo sin tocar un símbolo privado de `tools.py`): minúsculas,
    ASCII, `-` como separador, nunca vacío (cae a `"podcast"`)."""
    normalizado = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", normalizado.strip().lower()).strip("-")
    return slug[:60] or "podcast"
