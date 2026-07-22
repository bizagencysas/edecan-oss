"""Análisis de video por frames (`analizar_video`, ROADMAP_V2.md §6.3 — "Video:
extracción de frames (ffmpeg en el worker) + visión por lotes" — promovido de P2
documentado a código real por `DIRECCION_ACTUAL.md` ("Ambición: sin límite"),
WP-V3-14).

No hay ningún modelo de video-a-texto: la técnica es extraer una MUESTRA de
frames (imágenes fijas) con **`ffmpeg`** — binario del SISTEMA, nunca una
dependencia Python nueva (nada de `moviepy`/`opencv`, ver `docs/analista.md`) — y
mandarlos como una tanda de bloques de visión al mismo proveedor LLM que ya usa
`analizar_imagen` (`edecan_docanalysis.vision`), en una ÚNICA llamada a
`ctx.llm.complete`. El audio NUNCA se transcribe aquí (ver `docs/analista.md`,
sección "Video", para la alternativa de transcripción de voz).

Flujo de `AnalizarVideoTool.run`: resolver el archivo (mismo mecanismo que
`analizar_imagen`: `_s3.descargar_archivo` sobre un id UUID) → validar que el
mime sea `video/*` → confirmar que `ffmpeg` está instalado
(`ffmpeg_disponible()`; si no, un mensaje
instructivo, nunca un traceback) → `extraer_frames` (subproceso `ffmpeg`, JAMÁS
`shell=True`) → una sola llamada al LLM con los frames + una pregunta.

**Por qué los timestamps son "aproximados"**: `ffmpeg` no reporta de vuelta el
instante exacto de cada frame que decide muestrear con el filtro `fps=`; en vez
de parsear el log verboso de `ffmpeg` (frágil entre versiones y plataformas),
`construir_bloques_video` estima el instante de cada frame repartiendo
`duracion_estimada_s` (sondeada con `ffprobe`, best-effort — ver
`estimar_duracion_segundos`) en `len(frames)` pasos iguales. Coincide con cómo
`fps=` realmente muestrea (intervalos constantes desde el inicio del video),
salvo que el video sea más corto de lo que el `fps` calculado asume — en ese
caso `ffmpeg` simplemente entrega menos frames de los pedidos, y las etiquetas
igual quedan proporcionales a los frames que sí llegaron.

**ffmpeg es una dependencia del SISTEMA, no de Python**: en la app de escritorio
(`DIRECCION_ACTUAL.md`, stack Tauri) el cliente lo instala aparte;
`ffmpeg_disponible()` lo detecta en caliente (`shutil.which`, o `FFMPEG_PATH` si
el entorno lo fija — mismo criterio que `CLAUDE_CLI_PATH`/`CODEX_CLI_PATH` de
`edecan_llm.detect`) y la tool devuelve un mensaje instructivo si falta.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from edecan_core import Tool, ToolContext, ToolResult
from edecan_llm.base import ChatMessage, CompletionRequest

from . import _s3
from ._util import clamp_int, parse_uuid, tenant_flags
from .vision import _bloque_imagen

logger = logging.getLogger(__name__)

_TAMANO_MAXIMO_BYTES = 80 * 1024 * 1024  # 80 MB — ver docs/analista.md "Video"
_MIN_FRAMES = 1
_MAX_FRAMES = 16
_DEFAULT_MAX_FRAMES = 8
_ESCALA_PX_DEFECTO = 768

_TIMEOUT_SECONDS = 120.0
_FFPROBE_TIMEOUT_SECONDS = 15.0

# Sin duración conocida (ffprobe ausente o falló), se muestrea a este fps fijo;
# `-frames:v max_frames` igual garantiza que nunca se generen más de los pedidos.
_FPS_FALLBACK = 0.5
_FPS_MIN = 0.05
_FPS_MAX = 30.0

_EXT_A_MIME_VIDEO = {
    ".mp4": "video/mp4",
    ".m4v": "video/x-m4v",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".3gp": "video/3gpp",
    ".mpeg": "video/mpeg",
    ".mpg": "video/mpeg",
}
_MIME_A_EXT_VIDEO = {mime: ext for ext, mime in _EXT_A_MIME_VIDEO.items()}

_PREGUNTA_DEFECTO = "¿Qué ocurre en este video? Resume los eventos clave."

_SYSTEM_PROMPT = (
    "Eres un asistente de visión que analiza videos a partir de una muestra de "
    "frames extraídos (no el video completo ni el audio). Describe, en orden, los "
    "eventos clave que se ven en esos frames, sin inventar contenido que no esté "
    "en las imágenes — si algo no se puede determinar solo con los frames, dilo "
    "explícitamente. Responde en español salvo que la pregunta pida otro idioma."
)

FFMPEG_INSTALL_HINT = (
    "analizar_video necesita el binario ffmpeg instalado en esta máquina para "
    "extraer los frames del video, y no lo encontré. Instálalo con "
    "'brew install ffmpeg' (macOS) o 'apt install ffmpeg' (Linux/Debian/Ubuntu); "
    "en Windows descarga el binario desde https://ffmpeg.org/download.html y "
    "agrégalo al PATH. Vuelve a intentar después de instalarlo."
)


class VideoAnalysisError(Exception):
    """Error de negocio al extraer frames de un video: tamaño excedido, ffmpeg
    ausente, ffmpeg terminó con error, o timeout. `str(exc)` ya es un mensaje en
    español listo para mostrarse al usuario tal cual (sin traceback) — quien
    llama (`AnalizarVideoTool.run`) lo captura y lo devuelve como
    `ToolResult(content=str(exc))`."""


def ffmpeg_disponible() -> str | None:
    """Ruta al binario `ffmpeg`, o `None` si no está instalado / no está en el
    PATH. Respeta `FFMPEG_PATH` si el entorno lo fija (mismo criterio que
    `CLAUDE_CLI_PATH`/`CODEX_CLI_PATH` en `edecan_llm.detect`: un override
    explícito gana sobre la búsqueda automática)."""
    return os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg")


async def extraer_frames(
    video_bytes: bytes,
    *,
    max_frames: int,
    escala_px: int = _ESCALA_PX_DEFECTO,
    mime: str | None = None,
    timeout_seconds: float = _TIMEOUT_SECONDS,
) -> list[bytes]:
    """Extrae hasta `max_frames` frames JPEG de `video_bytes` con `ffmpeg`.

    Caps de seguridad: rechaza videos de más de `_TAMANO_MAXIMO_BYTES` (80 MB)
    ANTES de escribir nada a disco, y acota `max_frames` a
    [`_MIN_FRAMES`, `_MAX_FRAMES`] (1..16). Escribe `video_bytes` a un tmpfile
    dentro de un `tempfile.TemporaryDirectory` (nunca queda nada en disco tras
    salir de esta función), sondea la duración con `ffprobe` si está disponible
    (`which('ffprobe')`, sin override de entorno — a diferencia de `ffmpeg`) para
    calcular un `fps` de muestreo proporcional a `max_frames`; si no hay
    `ffprobe` o falla, cae a un `fps` fijo (`_FPS_FALLBACK`). Ejecuta `ffmpeg` vía
    `asyncio.create_subprocess_exec` (JAMÁS `shell=True`) con un timeout duro de
    `timeout_seconds` segundos (`wait_for` + `kill` si se excede). Devuelve los
    JPG resultantes, ordenados, ya leídos en memoria.

    Lanza `VideoAnalysisError` (mensaje en español, sin traceback) si: el video
    excede el tamaño máximo, `ffmpeg` no está instalado, el proceso hace timeout,
    termina con código de error, o no genera ningún frame.
    """
    if len(video_bytes) > _TAMANO_MAXIMO_BYTES:
        raise VideoAnalysisError(
            f"El video pesa {len(video_bytes) / 1_048_576:.1f} MB; el máximo para "
            f"analizar_video es {_TAMANO_MAXIMO_BYTES // 1_048_576} MB."
        )

    ffmpeg_path = ffmpeg_disponible()
    if ffmpeg_path is None:
        raise VideoAnalysisError(FFMPEG_INSTALL_HINT)

    max_frames = clamp_int(
        max_frames, default=_DEFAULT_MAX_FRAMES, minimo=_MIN_FRAMES, maximo=_MAX_FRAMES
    )
    sufijo = _sufijo_por_mime(mime)

    with tempfile.TemporaryDirectory(prefix="edecan_video_") as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        entrada = tmpdir / f"in{sufijo}"
        entrada.write_bytes(video_bytes)

        duracion = await _ffprobe_duracion(shutil.which("ffprobe"), str(entrada))
        fps = _calcular_fps(max_frames, duracion)

        patron_salida = tmpdir / "out_%03d.jpg"
        args = [
            ffmpeg_path,
            "-i",
            str(entrada),
            "-vf",
            f"fps={fps:.4f},scale={escala_px}:-2",
            "-frames:v",
            str(max_frames),
            "-q:v",
            "4",
            str(patron_salida),
        ]
        await _ejecutar_ffmpeg(args, timeout_seconds=timeout_seconds)

        rutas = sorted(tmpdir.glob("out_*.jpg"))
        if not rutas:
            raise VideoAnalysisError(
                "ffmpeg no generó ningún frame de este video — ¿el archivo está "
                "vacío o corrupto?"
            )
        return [ruta.read_bytes() for ruta in rutas]


async def estimar_duracion_segundos(video_bytes: bytes, *, mime: str | None = None) -> float | None:
    """Duración aproximada del video en segundos (best-effort vía `ffprobe`;
    `None` si `ffprobe` no está instalado o no se pudo determinar — nunca
    lanza). Usada por `AnalizarVideoTool` para las etiquetas de timestamp
    aproximado de `construir_bloques_video` y el campo `duracion_estimada_s`
    del resultado. Escribe `video_bytes` a un tmpfile propio (independiente del
    que usa `extraer_frames` para la extracción en sí)."""
    ffprobe_path = shutil.which("ffprobe")
    if not ffprobe_path:
        return None
    sufijo = _sufijo_por_mime(mime)
    try:
        with tempfile.TemporaryDirectory(prefix="edecan_video_probe_") as tmpdir_str:
            entrada = Path(tmpdir_str) / f"in{sufijo}"
            entrada.write_bytes(video_bytes)
            return await _ffprobe_duracion(ffprobe_path, str(entrada))
    except OSError:
        logger.warning("estimar_duracion_segundos: fallo escribiendo tmpfile", exc_info=True)
        return None


def construir_bloques_video(
    frames: list[bytes], pregunta: str, *, duracion_estimada_s: float | None = None
) -> list[dict[str, Any]]:
    """Arma los bloques multimodales para mandar los frames de un video al LLM:
    los mismos bloques base64 `image/jpeg` que ya usa `analizar_imagen`
    (`edecan_docanalysis.vision._bloque_imagen`, reutilizado sin duplicar la
    codificación), intercalando ANTES de cada imagen una etiqueta de texto
    `"Frame i de N"` (o `"Frame i de N, ~MM:SS"` si se conoce
    `duracion_estimada_s` — ver docstring del módulo para por qué es
    "aproximado") y cerrando con un bloque de texto final con `pregunta`."""
    total = len(frames)
    marcas = _timestamps_aproximados(total, duracion_estimada_s)
    bloques: list[dict[str, Any]] = []
    for idx, frame in enumerate(frames, start=1):
        etiqueta = f"Frame {idx} de {total}"
        if marcas is not None:
            etiqueta = f"{etiqueta}, ~{marcas[idx - 1]}"
        bloques.append({"type": "text", "text": etiqueta})
        bloques.append(_bloque_imagen("image/jpeg", frame))
    bloques.append({"type": "text", "text": pregunta})
    return bloques


def _timestamps_aproximados(total_frames: int, duracion_s: float | None) -> list[str] | None:
    if not duracion_s or duracion_s <= 0 or total_frames <= 0:
        return None
    paso = duracion_s / total_frames
    return [_formatear_mmss(idx * paso) for idx in range(total_frames)]


def _formatear_mmss(segundos: float) -> str:
    total = max(0, round(segundos))
    return f"{total // 60:02d}:{total % 60:02d}"


def _sufijo_por_mime(mime: str | None) -> str:
    """Extensión de archivo para el tmpfile de entrada de `ffmpeg`. Best-effort a
    propósito: `ffmpeg` detecta el contenedor por contenido, no por extensión,
    así que un sufijo "equivocado" en el peor caso no cambia el resultado — esto
    es solo para que el tmpfile tenga una pinta razonable en logs/diagnóstico."""
    normalizado = (mime or "").split(";")[0].strip().lower()
    return _MIME_A_EXT_VIDEO.get(normalizado, ".mp4")


def _resolver_mime_video(mime: str, filename: str) -> str | None:
    """Mismo patrón que `vision._resolver_mime`: usa el mime declarado si
    empieza con `"video/"`; si no (p. ej. `application/octet-stream` de una
    subida genérica), cae a la extensión del nombre de archivo."""
    normalizado = (mime or "").split(";")[0].strip().lower()
    if normalizado.startswith("video/"):
        return normalizado

    nombre = (filename or "").lower()
    for ext, mime_ext in _EXT_A_MIME_VIDEO.items():
        if nombre.endswith(ext):
            return mime_ext
    return None


def _calcular_fps(max_frames: int, duracion: float | None) -> float:
    """fps de muestreo para el filtro `fps=` de ffmpeg: `max_frames` repartidos
    a lo largo de la duración conocida, acotado a [`_FPS_MIN`, `_FPS_MAX`] para
    evitar un fps degenerado (video casi sin duración → fps altísimo; video muy
    largo → fps casi cero). Sin duración conocida, usa `_FPS_FALLBACK`."""
    if duracion is None or duracion <= 0:
        return _FPS_FALLBACK
    fps = max_frames / duracion
    return max(_FPS_MIN, min(_FPS_MAX, fps))


async def _ffprobe_duracion(ffprobe_path: str | None, entrada: str) -> float | None:
    """Duración en segundos vía `ffprobe -show_entries format=duration`.
    Best-effort: `None` (nunca lanza) si `ffprobe_path` es `None`, si el proceso
    no arranca, hace timeout, termina con error, o la salida no es un número."""
    if not ffprobe_path:
        return None
    try:
        process = await asyncio.create_subprocess_exec(
            ffprobe_path,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            entrada,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError:
        logger.warning("ffprobe: fallo al iniciar el proceso", exc_info=True)
        return None

    try:
        stdout_bytes, _stderr = await asyncio.wait_for(
            process.communicate(), timeout=_FFPROBE_TIMEOUT_SECONDS
        )
    except TimeoutError:
        process.kill()
        await process.wait()
        return None

    if process.returncode != 0:
        return None

    texto = stdout_bytes.decode("utf-8", errors="replace").strip()
    try:
        duracion = float(texto)
    except ValueError:
        return None
    return duracion if duracion > 0 else None


async def _ejecutar_ffmpeg(args: list[str], *, timeout_seconds: float) -> None:
    """Corre `ffmpeg` (subproceso, JAMÁS `shell=True`) con stdin cerrado (nunca
    espera un prompt interactivo), timeout duro (`wait_for` + `kill`) y stderr
    capturado para diagnóstico. Lanza `VideoAnalysisError` con un mensaje claro
    en español si el binario desaparece, hace timeout, o termina con error."""
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise VideoAnalysisError(FFMPEG_INSTALL_HINT) from exc
    except OSError as exc:
        raise VideoAnalysisError(f"No pude ejecutar ffmpeg: {exc}") from exc

    try:
        _stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(), timeout=timeout_seconds
        )
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise VideoAnalysisError(
            f"ffmpeg no terminó en {timeout_seconds:.0f}s — el video puede ser "
            "demasiado largo o pesado para procesar."
        ) from exc

    if process.returncode != 0:
        detalle = stderr_bytes.decode("utf-8", errors="replace").strip()
        raise VideoAnalysisError(
            f"ffmpeg terminó con error (código {process.returncode}): "
            f"{detalle[-500:] or 'sin detalle'}"
        )


class AnalizarVideoTool(Tool):
    name = "analizar_video"
    description = (
        "Analiza un video ya subido: extrae hasta 16 frames representativos con "
        "ffmpeg (no el video completo) y los envía a un modelo con visión para "
        "describir los eventos clave, o para responder una pregunta puntual. NO "
        "transcribe audio. Requiere ffmpeg instalado en esta máquina y que el "
        "modelo elegido tenga visión; si falta algo devuelve un error claro."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "archivo": {
                "type": "string",
                "description": "id (UUID) del video ya subido.",
            },
            "pregunta": {
                "type": "string",
                "description": (
                    "Pregunta puntual sobre el video. Si se omite, resume los "
                    "eventos clave."
                ),
            },
            "max_frames": {
                "type": "integer",
                "description": (
                    f"Cuántos frames extraer y analizar ({_MIN_FRAMES} a "
                    f"{_MAX_FRAMES}). Por defecto {_DEFAULT_MAX_FRAMES}."
                ),
            },
        },
        "required": ["archivo"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        file_id = parse_uuid(args.get("archivo"))
        if file_id is None:
            return ToolResult(content="'archivo' no es un identificador válido.")

        archivo = await _s3.descargar_archivo(ctx, file_id)
        if archivo is None:
            return ToolResult(content="No encontré ese archivo.")

        mime = _resolver_mime_video(archivo.mime, archivo.filename)
        if mime is None:
            return ToolResult(
                content=(
                    f"'{archivo.filename}' no parece un video — analizar_video "
                    "solo acepta archivos de video (mp4, mov, webm, avi, mkv, ...)."
                )
            )

        flags = tenant_flags(ctx)
        if ffmpeg_disponible() is None:
            return ToolResult(content=FFMPEG_INSTALL_HINT)

        max_frames = clamp_int(
            args.get("max_frames"),
            default=_DEFAULT_MAX_FRAMES,
            minimo=_MIN_FRAMES,
            maximo=_MAX_FRAMES,
        )

        try:
            frames = await extraer_frames(archivo.contenido, max_frames=max_frames, mime=mime)
        except VideoAnalysisError as exc:
            return ToolResult(content=str(exc))

        duracion = await estimar_duracion_segundos(archivo.contenido, mime=mime)
        pregunta = str(args.get("pregunta") or "").strip() or _PREGUNTA_DEFECTO
        bloques = construir_bloques_video(frames, pregunta, duracion_estimada_s=duracion)

        try:
            respuesta = await ctx.llm.complete(
                "principal",
                flags,
                CompletionRequest(
                    model="principal",
                    system=_SYSTEM_PROMPT,
                    messages=[ChatMessage(role="user", content=bloques)],
                    max_tokens=1536,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - proveedores/modelos heterogéneos
            return ToolResult(
                content=(
                    "Pude extraer los frames, pero el modelo elegido no pudo procesarlos. "
                    "Elige un modelo con visión o intenta de nuevo. "
                    f"Detalle: {type(exc).__name__}."
                )
            )

        texto = respuesta.text.strip()
        if not texto:
            return ToolResult(
                content="No logré analizar ese video; intenta reformular la pregunta."
            )
        return ToolResult(
            content=texto,
            data={"frames_analizados": len(frames), "duracion_estimada_s": duracion},
        )
