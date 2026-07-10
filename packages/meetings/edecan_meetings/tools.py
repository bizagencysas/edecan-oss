"""`ResumirReunionTool` (`resumir_reunion`) — la única tool de este paquete
(`ARCHITECTURE.md` §15, WP-V6-05).

Valida que el archivo exista, pertenezca al tenant actual y sea audio/video,
encola el job `process_meeting` (que hace TODO el trabajo pesado: extraer
audio, transcribir con el STT del tenant, generar minutas con el LLM del
tenant — ver `apps/worker/edecan_worker/handlers/process_meeting.py`) y
devuelve el disclaimer de consentimiento obligatorio. Mismo patrón "la tool
solo valida + encola, el worker hace el trabajo real" que
`edecan_creative.tools.CrearPodcastTool` /
`apps/worker/edecan_worker/handlers/generate_podcast.py`.

## Validación de archivo — mismo contrato que `analizar_imagen`/`analizar_video`

`_cargar_fila_archivo` calca el patrón de resolución de archivo de
`packages/docanalysis/edecan_docanalysis/vision.py`/`video.py`
(`_s3.descargar_archivo`/`_get_file_row`: SQL parametrizado directo contra
`files` sobre `ctx.session`, que ya trae Row-Level Security activo para
`ctx.tenant_id` — ARCHITECTURE.md §2) — pero SIN importar `edecan_docanalysis`
(paquete de OTRO work package, WP-V6-06 en esta misma ola v6,
`ARCHITECTURE.md` §10.1) y SIN descargar el contenido de S3: esta tool solo
necesita `filename`/`mime` para validar, la descarga real del contenido la
hace `process_meeting` en el worker (evita traer potencialmente cientos de MB
de audio/video a la memoria del proceso de la API solo para validar un mime).

## Por qué la tool NO inserta la fila `meetings` ella misma

El paquete de trabajo dejaba dos caminos abiertos ("si las tools no tienen
escritura directa, encola con payload {file_id, titulo} y que el handler cree
la fila — decide leyendo el código real y documenta"). `ToolContext.session`
SÍ tiene escritura directa (`edecan_docanalysis._s3.subir_resultado` hace
exactamente un `INSERT` así sobre `ctx.session`) — pero un turno de chat
puede seguir corriendo mucho más allá de esta tool (más tool calls, la
respuesta final del modelo) antes de que esa sesión haga commit
(`edecan_api.routers.conversations`, que envuelve el turno completo). Si esta
tool insertara la fila `meetings` y encolara `process_meeting` de inmediato,
el worker podría recibir el mensaje de SQS y consultar
`SELECT * FROM meetings WHERE id = ...` ANTES de que esa transacción llegue a
commitear — una carrera de "lee tu propia escritura" mucho más ancha que la
de un único request HTTP (que sí crea+encola dentro de la MISMA transacción
corta, ver `apps/api/edecan_api/routers/reuniones.py::crear_reunion`).
`edecan_creative.tools.CrearPodcastTool` evita exactamente este problema
nunca tocando la base de datos — delega el `INSERT` en
`apps/worker/edecan_worker/handlers/generate_podcast.py`, que arma la fila
desde cero con lo que trae el payload. Esta tool sigue el mismo criterio:
encola `process_meeting` con `{"file_id", "titulo", "user_id"}` (sin
`meeting_id` — no existe todavía) y dedica el primer bloque de
`process_meeting.handle()` a crear la fila `meetings` en su propia
transacción corta, recién ahí.
"""

from __future__ import annotations

import uuid
from typing import Any

from edecan_core import Tool, ToolContext, ToolResult
from edecan_core.queue import enqueue
from sqlalchemy import text as sql_text

# Disclaimer de consentimiento OBLIGATORIO — string EXACTO, testeado. Se
# repite (duplicado a propósito, `ARCHITECTURE.md` §10.1) en el router HTTP
# (`apps/api/edecan_api/routers/reuniones.py`) y en el banner de la UI web
# (`apps/web/src/app/(app)/app/reuniones/page.tsx`) — los tres deben decir
# EXACTAMENTE lo mismo, byte por byte.
DISCLAIMER_CONSENTIMIENTO = (
    "Recuerda: asegúrate de contar con el consentimiento de todos los "
    "participantes para grabar y transcribir esta reunión."
)

_MAX_TITULO_CHARS = 200
_JOB_TYPE = "process_meeting"


def _parse_uuid(value: Any) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


async def _cargar_fila_archivo(ctx: ToolContext, file_id: uuid.UUID) -> dict[str, Any] | None:
    """`{id, filename, mime}` de `files.id = file_id` del tenant actual, o
    `None` si no existe / no le pertenece — mismo criterio que
    `edecan_docanalysis._s3._get_file_row` (no importado, ver docstring del
    módulo)."""
    resultado = await ctx.session.execute(
        sql_text(
            "SELECT id, filename, mime FROM files WHERE tenant_id = :tenant_id AND id = :id"
        ),
        {"tenant_id": str(ctx.tenant_id), "id": str(file_id)},
    )
    fila = resultado.mappings().first()
    return dict(fila) if fila is not None else None


def _es_audio_o_video(mime: str | None) -> bool:
    normalizado = (mime or "").split(";")[0].strip().lower()
    return normalizado.startswith("audio/") or normalizado.startswith("video/")


class ResumirReunionTool(Tool):
    name = "resumir_reunion"
    description = (
        "Transcribe una reunión grabada (audio o video ya subido) con el proveedor de "
        "voz (STT) de tu cuenta y genera minutas (resumen, decisiones, acciones, temas) "
        "con tu modelo de lenguaje. El procesamiento corre en segundo plano — no "
        "devuelve el resultado de inmediato, consúltalo luego en /app/reuniones o con "
        "'buscar_correo'-style listados. Requiere el consentimiento de todos los "
        "participantes para grabar y transcribir."
    )
    requires_flags = frozenset({"tools.meetings"})
    dangerous = False
    input_schema = {
        "type": "object",
        "properties": {
            "archivo": {
                "type": "string",
                "description": "id (UUID) del audio o video de la reunión, ya subido.",
            },
            "titulo": {
                "type": "string",
                "description": "Título de la reunión (opcional).",
            },
        },
        "required": ["archivo"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        file_id = _parse_uuid(args.get("archivo"))
        if file_id is None:
            return ToolResult(content="'archivo' no es un identificador válido.")

        archivo = await _cargar_fila_archivo(ctx, file_id)
        if archivo is None:
            return ToolResult(content="No encontré ese archivo.")

        if not _es_audio_o_video(archivo.get("mime")):
            return ToolResult(
                content=(
                    f"'{archivo['filename']}' no parece un audio o video — "
                    "resumir_reunion solo acepta grabaciones de audio o video."
                )
            )

        titulo = str(args.get("titulo") or "").strip()[:_MAX_TITULO_CHARS] or archivo["filename"]

        await enqueue(
            ctx.settings,
            _JOB_TYPE,
            {"file_id": str(file_id), "titulo": titulo, "user_id": str(ctx.user_id)},
            ctx.tenant_id,
        )

        return ToolResult(
            content=(
                f"Encolé la transcripción y minutas de '{titulo}' — te avisaré cuando esté "
                f"lista (también la puedes ver en /app/reuniones). {DISCLAIMER_CONSENTIMIENTO}"
            ),
            data={"file_id": str(file_id), "titulo": titulo},
        )


def get_all_tools() -> list[Tool]:
    """Entry point `edecan.tools` (`ARCHITECTURE.md` §10.7) — resuelve a un
    callable sin argumentos que devuelve `list[Tool]`."""
    return [ResumirReunionTool()]
