"""Extracción de audio (WAV mono 16 kHz) de un archivo de audio o video, vía
`ffmpeg` del sistema (`ARCHITECTURE.md` §15, WP-V6-05; ver el README de este
paquete).

**Duplicación deliberada** (`ARCHITECTURE.md` §10.1): el patrón de subproceso
de este módulo (`shutil.which`/`FFMPEG_PATH`, `asyncio.create_subprocess_exec`
con una lista de argumentos — JAMÁS `shell=True`, `tempfile.TemporaryDirectory`,
timeout duro con `wait_for`/`kill`, mensaje instructivo si falta el binario)
calca línea por línea el de `packages/docanalysis/edecan_docanalysis/video.py`
(`ffmpeg_disponible`/`_ejecutar_ffmpeg`, el patrón canónico del repo para
invocar `ffmpeg`) — pero `edecan_docanalysis` es el paquete de OTRO work
package (WP-V6-06 en esta misma ola v6), así que este módulo NO lo importa:
se reimplementa localmente, mismo criterio que ya siguen
`edecan_creative.podcast` (ensamblado de podcasts con ffmpeg) y
`apps/worker/edecan_worker/handlers/ingest_file.py` (`_resolver_mime_imagen`
frente a `edecan_docanalysis.vision`) con sus propios paquetes hermanos.

A diferencia de `edecan_docanalysis.video` (que extrae FRAMES de imagen), este
módulo extrae la pista de AUDIO completa como WAV mono 16 kHz — el formato que
esperan la mayoría de proveedores STT (incluido Deepgram). `ffmpeg` decodifica
el audio de un contenedor de video (`.mp4`/`.mov`/...) exactamente igual que
el de un archivo de audio puro (`.mp3`/`.m4a`/...): un único filtro
`-ac 1 -ar 16000` sirve para ambos casos, así que no hace falta distinguir
"es video" de "es audio" en este módulo — esa distinción (validar que el mime
sea `audio/*`/`video/*`) vive en `tools.py`/el router HTTP, no aquí.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import shutil
import tempfile
import wave
from pathlib import Path

logger = logging.getLogger(__name__)

# Mismo criterio de tamaño máximo que `edecan_docanalysis.video`
# (`_TAMANO_MAXIMO_BYTES`, 80 MB) — reuniones grabadas pueden pesar más que un
# video corto, así que este módulo usa un tope más generoso (300 MB) acorde a
# audio/video de reuniones de hasta ~2-3 horas en calidad razonable.
_TAMANO_MAXIMO_BYTES = 300 * 1024 * 1024  # 300 MB
_TIMEOUT_SECONDS = 300.0  # extracción de audio de una reunión larga puede tardar

_SAMPLE_RATE_HZ = 16_000
_CHANNELS = 1

_EXT_POR_MIME = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/aac": ".aac",
    "audio/flac": ".flac",
    "video/mp4": ".mp4",
    "video/x-m4v": ".m4v",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "video/x-msvideo": ".avi",
    "video/x-matroska": ".mkv",
    "video/3gpp": ".3gp",
    "video/mpeg": ".mpeg",
}

FFMPEG_INSTALL_HINT = (
    "resumir_reunion necesita el binario ffmpeg instalado en esta máquina para "
    "extraer el audio de la reunión, y no lo encontré. Instálalo con "
    "'brew install ffmpeg' (macOS) o 'apt install ffmpeg' (Linux/Debian/Ubuntu); "
    "en Windows descarga el binario desde https://ffmpeg.org/download.html y "
    "agrégalo al PATH. Vuelve a intentar después de instalarlo."
)


class AudioExtractionError(Exception):
    """Error de negocio al extraer el audio de una reunión: tamaño excedido,
    ffmpeg ausente, ffmpeg terminó con error, o timeout. `str(exc)` ya es un
    mensaje en español listo para mostrarse al usuario/guardarse como
    `meetings.error` tal cual (sin traceback)."""


def ffmpeg_disponible() -> str | None:
    """Ruta al binario `ffmpeg`, o `None` si no está instalado / no está en el
    PATH. Respeta `FFMPEG_PATH` si el entorno lo fija — mismo criterio EXACTO
    que `edecan_docanalysis.video.ffmpeg_disponible`."""
    return os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg")


def _sufijo_por_mime(mime: str | None) -> str:
    normalizado = (mime or "").split(";")[0].strip().lower()
    return _EXT_POR_MIME.get(normalizado, ".bin")


async def extraer_audio_wav(
    data: bytes,
    mime: str | None = None,
    *,
    timeout_seconds: float = _TIMEOUT_SECONDS,
) -> bytes:
    """Extrae la pista de audio de `data` (bytes de un archivo de audio o
    video ya subido) como WAV mono de 16 kHz, vía `ffmpeg`.

    Rechaza `data` de más de `_TAMANO_MAXIMO_BYTES` (300 MB) ANTES de escribir
    nada a disco. Escribe `data` a un tmpfile dentro de un
    `tempfile.TemporaryDirectory` (nunca queda nada en disco al salir de esta
    función) y ejecuta `ffmpeg -i entrada -ac 1 -ar 16000 -f wav salida.wav`
    vía `asyncio.create_subprocess_exec` (JAMÁS `shell=True`) con un timeout
    duro (`wait_for` + `kill` si se excede). Devuelve los bytes del WAV
    resultante, ya leídos en memoria.

    Lanza `AudioExtractionError` (mensaje en español, sin traceback) si:
    `data` excede el tamaño máximo, `ffmpeg` no está instalado, el proceso
    hace timeout, termina con código de error, o no genera ningún archivo de
    salida.
    """
    if len(data) > _TAMANO_MAXIMO_BYTES:
        raise AudioExtractionError(
            f"El archivo pesa {len(data) / 1_048_576:.1f} MB; el máximo para "
            f"resumir_reunion es {_TAMANO_MAXIMO_BYTES // 1_048_576} MB."
        )
    if not data:
        raise AudioExtractionError("El archivo de la reunión está vacío.")

    ffmpeg_path = ffmpeg_disponible()
    if ffmpeg_path is None:
        raise AudioExtractionError(FFMPEG_INSTALL_HINT)

    sufijo = _sufijo_por_mime(mime)

    with tempfile.TemporaryDirectory(prefix="edecan_meeting_audio_") as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        entrada = tmpdir / f"in{sufijo}"
        salida = tmpdir / "out.wav"
        entrada.write_bytes(data)

        args = [
            ffmpeg_path,
            "-i",
            str(entrada),
            "-vn",  # sin video: solo interesa el audio, aunque la entrada sea video
            "-ac",
            str(_CHANNELS),
            "-ar",
            str(_SAMPLE_RATE_HZ),
            "-f",
            "wav",
            str(salida),
        ]
        await _ejecutar_ffmpeg(args, timeout_seconds=timeout_seconds)

        if not salida.exists() or salida.stat().st_size == 0:
            raise AudioExtractionError(
                "ffmpeg no generó ningún audio de este archivo — ¿el archivo está "
                "vacío, corrupto, o no tiene pista de audio?"
            )
        return salida.read_bytes()


async def _ejecutar_ffmpeg(args: list[str], *, timeout_seconds: float) -> None:
    """Corre `ffmpeg` (subproceso, JAMÁS `shell=True`) con stdin cerrado,
    timeout duro y stderr capturado para diagnóstico — mismo criterio EXACTO
    que `edecan_docanalysis.video._ejecutar_ffmpeg`."""
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise AudioExtractionError(FFMPEG_INSTALL_HINT) from exc
    except OSError as exc:
        raise AudioExtractionError(f"No pude ejecutar ffmpeg: {exc}") from exc

    try:
        _stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(), timeout=timeout_seconds
        )
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise AudioExtractionError(
            f"ffmpeg no terminó en {timeout_seconds:.0f}s — la reunión puede ser "
            "demasiado larga o pesada para procesar."
        ) from exc

    if process.returncode != 0:
        detalle = stderr_bytes.decode("utf-8", errors="replace").strip()
        raise AudioExtractionError(
            f"ffmpeg terminó con error (código {process.returncode}): "
            f"{detalle[-500:] or 'sin detalle'}"
        )


def duracion_wav_segundos(wav_bytes: bytes) -> float | None:
    """Duración en segundos de un WAV (puro-Python, módulo estándar `wave` —
    sin `ffprobe`/subproceso extra): como `extraer_audio_wav` ya normaliza
    TODO a mono 16 kHz PCM, basta con `nframes / framerate` del propio WAV ya
    en memoria — más simple y confiable que sondear el archivo ORIGINAL con
    `ffprobe` (que sí necesita `edecan_docanalysis.video.estimar_duracion_segundos`,
    porque ese módulo no produce un WAV normalizado del que leer esto gratis).

    `None` (nunca lanza) si `wav_bytes` no es un WAV válido — caso límite
    defensivo, no debería pasar con la salida de `extraer_audio_wav`.
    """
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
            frames = wav_file.getnframes()
            rate = wav_file.getframerate()
            if rate <= 0:
                return None
            return frames / rate
    except (wave.Error, EOFError, OSError):
        return None
