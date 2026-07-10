"""Job `process_meeting`: transcribe una reunión (audio/video ya subido) con
el STT bring-your-own del tenant y genera minutas con el LLM bring-your-own
del tenant (`ARCHITECTURE.md` §15, WP-V6-05; ver `docs/reuniones.md`).

## Payload — dos formas (ver `packages/meetings/edecan_meetings/tools.py`,
   sección "Por qué la tool NO inserta la fila `meetings` ella misma")

- `{"meeting_id": "<uuid>"}` — la fila `meetings` YA existe (la creó
  `apps/api/edecan_api/routers/reuniones.py::crear_reunion`, dentro de la
  MISMA transacción HTTP que encoló este job — sin carrera de "lee tu propia
  escritura" porque el `INSERT`+`enqueue` comparten una única transacción
  corta). Este handler la carga y la pasa a `status='running'`.
- `{"file_id": "<uuid>", "titulo": "<str>", "user_id": "<uuid>"}` — no existe
  fila todavía (la encoló `ResumirReunionTool`, que NUNCA toca la base de
  datos ella misma — ver su docstring). Este handler CREA la fila `meetings`
  desde cero (`status='running'` directo, sin pasar por `'queued'`: no hubo
  ninguna fila previa que estuviera "en cola").

Una reunión con `meeting_id` que ya no existe (borrada mientras el job
esperaba en la cola) se ignora sin error — mismo criterio que
`run_mission`/`ingest_file` con una fila que desapareció.

## Tabla `meetings` — SQL parametrizado directo (sin ORM)

Igual criterio que `apps/worker/edecan_worker/handlers/generate_podcast.py`
con `files` y `apps/api/edecan_api/routers/voz_avanzada.py` con
`voice_consents`: `meetings` es una tabla nueva de v6 sin método dedicado en
`edecan_worker.repo.Repo` (ese archivo está fuera de las rutas que este
paquete de trabajo puede tocar) — se habla SQL parametrizado directo contra
el esquema REAL que aterrizó `packages/db/alembic/versions/
0008_v6_expansion.py` + `edecan_db.models.Meeting`: `source_file_id` (no
`file_id`) y un único `minutos` JSONB (no columnas separadas `decisiones`/
`acciones`/`temas`). El pin especulativo de `packages/meetings/README.md`
("Tabla `meetings` — pinned aquí") se escribió ANTES de que esa migración
aterrizara y nunca se reconcilió con el shape final — la migración/ORM
mandan, no ese README.

## Flujo

1. Carga o crea la fila `meetings`, `status='running'` (sesión corta).
2. Descarga el archivo fuente de S3 (mismo patrón que
   `ingest_file.py::_read_s3_object` / `generate_podcast.py`).
3. `edecan_meetings.audio.extraer_audio_wav` (ffmpeg) → WAV mono 16 kHz +
   duración (`duracion_wav_segundos`, puro-Python).
4. STT del TENANT (`edecan_meetings.stt.resolver_stt_del_tenant` — fail-closed
   a `StubSTT`, JAMÁS una credencial de plataforma) → transcribe. Si el
   resultado vino del stub (el tenant no conectó Deepgram), la reunión
   igual se completa (`status='done'`, mismo criterio "el job nunca revienta
   por falta de configuración" que `generate_podcast`) pero
   `meetings.error` queda con un aviso claro y accionable — NO es un fallo
   del job, es una nota visible en la UI (ver `docs/reuniones.md`).
5. Sube la transcripción como una fila `files` nueva (`text/plain`, mismas
   columnas EXACTAS que `generate_podcast.py` usa para `files`).
6. LLM bring-your-own del tenant (`deps.llm_router_for`, **PEREZOSO** — se
   resuelve recién AQUÍ, después de tener transcript, mismo criterio que
   `ingest_file`/`run_mission`/`generate_content`): construye el prompt de
   minutas (`edecan_meetings.minutas.construir_prompt_minutas`), llama al
   proveedor, parsea la respuesta (`parsear_minutas`, tolerante a fences).
   Si el tenant no conectó un LLM propio, `TenantLLMNotConnectedError` se
   deja propagar tal cual (ver "Errores" abajo) — NUNCA cae a un LLM de
   plataforma.
7. Guarda resumen/`minutos` (decisiones/acciones/temas)/`transcript_file_id`/
   duración, `status='done'`.

## Errores: `status='error'` + mensaje en sesión NUEVA + re-raise

A diferencia de `generate_podcast` (que nace la fila `files` recién al FINAL
y por eso no tiene un estado intermedio que "revertir"), `meetings` SÍ nace
antes del trabajo pesado (paso 1) — así que un fallo a mitad de camino (S3
caído, ffmpeg ausente, error de red hablando con Deepgram, LLM no conectado)
debe dejar la fila en un estado terminal legible (`status='error'`,
`error=str(exc)`) en vez de "running" para siempre. Ese `UPDATE` se hace en
una transacción NUEVA y CORTA (la fila `meetings` ya existe desde el paso 1,
en su propia transacción ya comiteada — no hay nada que un rollback de la
sesión que estaba procesando pudiera perder) y LUEGO se re-lanza la
excepción original: el despachador del job (`edecan_worker.main`/
`edecan_local.worker_loop`) la trata igual que cualquier otro fallo
(reintento con backoff hasta `MAX_ATTEMPTS`, luego DLQ) — reintentar no
arregla un `ffmpeg` ausente, pero tampoco hace daño, y cada intento deja el
mismo mensaje claro en `meetings.error` para que la UI lo muestre de
inmediato sin esperar al agotamiento de reintentos.

## Import perezoso de `edecan_meetings`

`edecan-meetings` es un paquete nuevo de v6 — se importa DENTRO de `handle()`
(no al tope del módulo), mismo criterio defensivo que `generate_podcast.py`
con `edecan_creative`: un entorno con un checkout parcial (`uv sync
--all-packages` no corrido tras sumar el paquete al workspace) falla con un
mensaje claro en vez de un `ModuleNotFoundError` críptico en medio del
despachador de jobs. `edecan_voice` en cambio SÍ es una dependencia
declarada de `apps/worker` desde v5 (`ARCHITECTURE.md` §14.f) — se importa
normal, al tope del módulo.

## Reintento y duplicación (BARRIDO C, WP-V7-04)

`_cargar_o_crear_reunion` nunca vuelve a hacer trabajo real sobre una
reunión que YA está en un estado terminal (`_ESTADOS_TERMINALES =
("done", "error", "cancelled")`) — así que un reintento del despachador
sobre un job que ya terminó (éxito o error) es un no-op seguro: carga la
fila, ve que es terminal, y `handle()` retorna sin tocar nada más. Esto
también significa que un fallo transitorio (ej. Deepgram con un error 500
pasajero) deja la reunión en `status='error'` de forma PERMANENTE — ver
"Errores" arriba, es una decisión deliberada ("reintentar no arregla un
ffmpeg ausente, pero tampoco hace daño"), no un bug: el reintento automático
del despachador no vuelve a intentar el trabajo pesado, solo confirma que ya
terminó y sale.

El único escenario donde SÍ podría haber duplicación real es un reintento
que llega mientras la fila sigue en `status='running'` (no terminal) — esto
requeriría que el PROCESO MISMO se caiga a mitad de `handle()` (OOM, kill
del pod, crash duro) sin que el `except`/`_marcar_error` llegue a correr, y
que el broker (SQS, por `visibility timeout`) redespache el mismo mensaje.
En ese caso `_cargar_o_crear_reunion` encontraría `status='running'` (no
terminal), llamaría `_marcar_running` de nuevo (idempotente) y
`process_meeting` repetiría TODO el trabajo pesado — incluida
`_subir_transcript`, que SIEMPRE inserta una fila `files` + objeto S3
NUEVOS (`uuid.uuid4()` fresco en cada llamada, nunca reutiliza uno
anterior). El resultado final (`meetings.transcript_file_id`/`resumen`/
`minutos`) queda correcto (la última escritura gana), pero el intento
anterior deja un archivo `files`/S3 huérfano — duplicación de
almacenamiento, no de datos visibles al tenant. Este riesgo es idéntico al
que ya asume cualquier otro handler de `apps/worker` con múltiples pasos de
escritura bajo semántica at-least-once del broker (`generate_podcast.py`,
`ingest_file.py`) — no hay un mecanismo de idempotencia/deduplicación
general en este repo para ese caso límite, y este WP no introduce uno nuevo
solo para `process_meeting` (fuera de alcance: exigiría una pieza de
arquitectura compartida, no un fix local). Revisado y documentado a
propósito, no un hallazgo nuevo que arreglar acá.

## Re-verificación empírica v7 (BARRIDO D, WP-V7-04)

El esquema de `meetings` (columnas, vocabulario de `status`, `minutos`
JSONB) ya estaba correcto desde el fix de v6 (`DIRECCION_ACTUAL.md` "v6
completado") — re-verificado columna por columna contra
`0008_v6_expansion.py`/`edecan_db.models.Meeting` Y, por primera vez, contra
Postgres real (Docker desechable + `alembic upgrade head`, nunca se había
hecho antes: ver `docs/cumplimiento/barrido-v7-reuniones-analista.md`).
Dos hallazgos reales de esa verificación empírica, ambos corregidos en este
módulo:

1. `duracion_segundos` (INTEGER real) recibía un `float` sin redondear — ver
   el comentario en `_guardar_resultado`. `FakeSession` nunca lo detectó
   porque no aplica tipos de columna; Postgres real tampoco lo rechazaba
   (asyncpg trunca en silencio), así que era invisible incluso corriendo
   contra Postgres sin mirar el valor YA guardado.
2. `add_usage_event` corría en la MISMA transacción que `_guardar_resultado`
   — ver el comentario al final de `handle()`, sección "usage_events". Este
   sí es del mismo patrón que `HOTFIXES_PENDIENTES.md` puntos 8/9.

El resto de las ~10 sentencias SQL parametrizadas de este archivo (INSERT/
SELECT/UPDATE sobre `meetings`, INSERT sobre `files` en `_subir_transcript`)
se ejercitaron de punta a punta contra Postgres real sin encontrar más
discrepancias — columnas, tipos y casts (`::uuid`, `::jsonb`) coinciden con
la migración real.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from json import dumps as json_dumps
from typing import Any

from edecan_llm.base import ChatMessage, CompletionRequest
from edecan_schemas import PLANES, JobEnvelope
from edecan_voice.stubs import StubSTT
from sqlalchemy import text as sql_text

from edecan_worker.deps import Deps
from edecan_worker.repo import SqlRepo

logger = logging.getLogger(__name__)

DEFAULT_S3_BUCKET = "edecan-files"
_MAX_TOKENS_MINUTAS = 1536
_ESTADOS_TERMINALES = ("done", "error", "cancelled")

_AVISO_STUB_STT = (
    "No conectaste tu propio proveedor de voz (STT) — esta transcripción se generó con "
    "un STT de prueba (offline), no es la transcripción real de la reunión. Conecta "
    "Deepgram en Configuración (PUT /v1/credentials/voice/stt) y vuelve a intentarlo."
)


def _slugify(texto: str) -> str:
    normalizado = "".join(c if c.isalnum() else "-" for c in texto.strip().lower())
    while "--" in normalizado:
        normalizado = normalizado.replace("--", "-")
    return normalizado.strip("-") or "reunion"


async def _read_s3_object(deps: Deps, key: str) -> bytes:
    bucket = getattr(deps.settings, "S3_BUCKET", None) or DEFAULT_S3_BUCKET
    response = await deps.s3.get_object(Bucket=bucket, Key=key)
    return await response["Body"].read()


# ---------------------------------------------------------------------------
# `meetings` — SQL parametrizado (ver docstring del módulo).
# ---------------------------------------------------------------------------


async def _seleccionar_reunion(
    session: Any, *, tenant_id: uuid.UUID, meeting_id: uuid.UUID
) -> dict[str, Any] | None:
    fila = (
        await session.execute(
            sql_text("SELECT * FROM meetings WHERE tenant_id = :tenant_id AND id = :id"),
            {"tenant_id": tenant_id, "id": meeting_id},
        )
    ).mappings().first()
    return dict(fila) if fila is not None else None


async def _insertar_reunion(
    session: Any,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    file_id: uuid.UUID,
    titulo: str,
) -> dict[str, Any]:
    ahora = datetime.now(UTC)
    fila = (
        await session.execute(
            sql_text(
                "INSERT INTO meetings ("
                "  id, tenant_id, user_id, source_file_id, titulo, status,"
                "  created_at, updated_at"
                ") VALUES ("
                "  :id, :tenant_id, :user_id, :source_file_id, :titulo, 'running',"
                "  :now, :now"
                ") RETURNING *"
            ),
            {
                "id": uuid.uuid4(),
                "tenant_id": tenant_id,
                "user_id": user_id,
                "source_file_id": file_id,
                "titulo": titulo,
                "now": ahora,
            },
        )
    ).mappings().first()
    assert fila is not None
    return dict(fila)


async def _marcar_running(session: Any, *, tenant_id: uuid.UUID, meeting_id: uuid.UUID) -> None:
    await session.execute(
        sql_text(
            "UPDATE meetings SET status = 'running', updated_at = :now "
            "WHERE tenant_id = :tenant_id AND id = :id"
        ),
        {"now": datetime.now(UTC), "tenant_id": tenant_id, "id": meeting_id},
    )


async def _cargar_o_crear_reunion(
    session: Any, *, tenant_id: uuid.UUID, payload: dict[str, Any]
) -> dict[str, Any] | None:
    """Ver docstring del módulo, sección "Payload — dos formas". `None` si
    trae `meeting_id` pero la fila ya no existe (se ignora sin error, mismo
    criterio que `run_mission`/`ingest_file`)."""
    meeting_id_raw = payload.get("meeting_id")
    if meeting_id_raw:
        meeting_id = uuid.UUID(str(meeting_id_raw))
        fila = await _seleccionar_reunion(session, tenant_id=tenant_id, meeting_id=meeting_id)
        if fila is None:
            return None
        if fila["status"] in _ESTADOS_TERMINALES:
            return fila
        await _marcar_running(session, tenant_id=tenant_id, meeting_id=meeting_id)
        fila["status"] = "running"
        return fila

    if not payload.get("file_id"):
        raise ValueError("process_meeting requiere 'meeting_id' o 'file_id' en el payload")
    if not payload.get("user_id"):
        raise ValueError(
            "process_meeting requiere 'user_id' en el payload cuando no trae 'meeting_id'"
        )

    file_id = uuid.UUID(str(payload["file_id"]))
    user_id = uuid.UUID(str(payload["user_id"]))
    titulo = str(payload.get("titulo") or "").strip() or "Reunión"
    return await _insertar_reunion(
        session, tenant_id=tenant_id, user_id=user_id, file_id=file_id, titulo=titulo
    )


async def _guardar_resultado(
    session: Any,
    *,
    tenant_id: uuid.UUID,
    meeting_id: uuid.UUID,
    resumen: str,
    decisiones: list[str],
    acciones: list[dict[str, Any]],
    temas: list[str],
    transcript_file_id: uuid.UUID,
    duracion_segundos: float | None,
    error: str | None,
) -> None:
    # `meetings` real (`0008_v6_expansion.py` + `edecan_db.models.Meeting`) no
    # tiene columnas separadas `decisiones`/`acciones`/`temas` — un único
    # `minutos` JSONB junto al `resumen` TEXT (ver docstring del módulo).
    minutos = {"decisiones": decisiones, "acciones": acciones, "temas": temas}
    # `duracion_segundos` es INTEGER en Postgres real (migración + ORM
    # coinciden), pese a que `duracion_wav_segundos` (puro Python,
    # `frames / framerate`) devuelve un `float` con precisión de
    # sub-segundo — verificado EMPÍRICAMENTE contra Postgres real (BARRIDO D,
    # WP-V7-04, re-verificación v7): asyncpg NUNCA rechaza el float (no
    # revienta, invisible con `FakeSession`), pero lo TRUNCA en silencio hacia
    # cero al bindearlo contra la columna `integer` (`1.9 -> 1`, `2.5 -> 2`,
    # nunca redondea) — hasta un segundo completo de precisión perdido de
    # forma silenciosa y dependiente de un detalle no documentado del driver.
    # Se redondea EXPLÍCITAMENTE acá (más correcto que la truncación
    # implícita) para que el comportamiento sea intencional.
    duracion_redondeada = round(duracion_segundos) if duracion_segundos is not None else None
    await session.execute(
        sql_text(
            "UPDATE meetings SET "
            "  status = 'done', resumen = :resumen, minutos = :minutos ::jsonb,"
            "  transcript_file_id = :transcript_file_id, duracion_segundos = :duracion_segundos,"
            "  error = :error, updated_at = :now "
            "WHERE tenant_id = :tenant_id AND id = :id"
        ),
        {
            "resumen": resumen,
            "minutos": json_dumps(minutos),
            "transcript_file_id": transcript_file_id,
            "duracion_segundos": duracion_redondeada,
            "error": error,
            "now": datetime.now(UTC),
            "tenant_id": tenant_id,
            "id": meeting_id,
        },
    )


async def _marcar_error(
    session: Any, *, tenant_id: uuid.UUID, meeting_id: uuid.UUID, mensaje: str
) -> None:
    await session.execute(
        sql_text(
            "UPDATE meetings SET status = 'error', error = :error, updated_at = :now "
            "WHERE tenant_id = :tenant_id AND id = :id"
        ),
        {
            "error": mensaje[:2000],
            "now": datetime.now(UTC),
            "tenant_id": tenant_id,
            "id": meeting_id,
        },
    )


async def _subir_transcript(
    deps: Deps,
    session: Any,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    titulo: str,
    texto: str,
) -> uuid.UUID:
    """Sube la transcripción como una fila `files` nueva — MISMAS columnas
    EXACTAS que `generate_podcast.py` usa para `files` (ver su docstring)."""
    file_id = uuid.uuid4()
    filename = f"{_slugify(titulo)}-transcript.txt"
    mime = "text/plain"
    contenido = texto.encode("utf-8")
    bucket = getattr(deps.settings, "S3_BUCKET", None) or DEFAULT_S3_BUCKET
    s3_key = f"tenants/{tenant_id}/files/{file_id}/{filename}"

    await deps.s3.put_object(Bucket=bucket, Key=s3_key, Body=contenido, ContentType=mime)

    ahora = datetime.now(UTC)
    await session.execute(
        sql_text(
            "INSERT INTO files "
            "(id, tenant_id, user_id, s3_key, filename, mime, size_bytes, status, "
            "created_at, updated_at) "
            "VALUES (:id, :tenant_id, :user_id, :s3_key, :filename, :mime, :size_bytes, "
            "'ready', :now, :now)"
        ),
        {
            "id": file_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "s3_key": s3_key,
            "filename": filename,
            "mime": mime,
            "size_bytes": len(contenido),
            "now": ahora,
        },
    )
    return file_id


# ---------------------------------------------------------------------------
# handle
# ---------------------------------------------------------------------------


async def handle(env: JobEnvelope, deps: Deps) -> None:
    if env.tenant_id is None:
        raise ValueError("process_meeting requiere tenant_id")
    tenant_id = env.tenant_id

    # Import perezoso, ver docstring del módulo.
    try:
        from edecan_meetings.audio import (
            AudioExtractionError,
            duracion_wav_segundos,
            extraer_audio_wav,
        )
        from edecan_meetings.minutas import construir_prompt_minutas, parsear_minutas
        from edecan_meetings.stt import resolver_stt_del_tenant
    except ImportError as exc:  # pragma: no cover - ver docstring del módulo
        raise RuntimeError(
            "El paquete 'edecan-meetings' no está disponible en este entorno — "
            "process_meeting lo necesita (ver packages/meetings/README.md; si es un "
            "checkout desactualizado corre 'uv sync --all-packages')."
        ) from exc

    async with deps.session_factory(None) as session:
        reunion = await _cargar_o_crear_reunion(session, tenant_id=tenant_id, payload=env.payload)
    if reunion is None:
        logger.error(
            "process_meeting: reunión no encontrada (payload=%r) tenant_id=%s",
            env.payload,
            tenant_id,
        )
        return
    if reunion["status"] in _ESTADOS_TERMINALES:
        # `_ESTADOS_TERMINALES` nunca incluye "running" (ver su definición) —
        # si `_cargar_o_crear_reunion` devolvió un estado terminal es porque
        # la reunión YA se resolvió antes (done/error/cancelled) y este job
        # llegó tarde/duplicado; se ignora sin reintentar, mismo criterio que
        # `run_mission` con una misión ya terminal.
        logger.info(
            "process_meeting: reunión %s ya está en estado terminal (%s); se ignora.",
            reunion["id"],
            reunion["status"],
        )
        return

    meeting_id = reunion["id"]
    file_id = reunion["source_file_id"]

    try:
        async with deps.session_factory(None) as session:
            repo = SqlRepo(session)
            file_row = await repo.get_file(tenant_id=tenant_id, file_id=file_id)
        if file_row is None:
            raise ValueError(f"El archivo de origen (file_id={file_id}) ya no existe.")

        raw_bytes = await _read_s3_object(deps, file_row["s3_key"])
        try:
            wav_bytes = await extraer_audio_wav(raw_bytes, mime=file_row.get("mime"))
        except AudioExtractionError as exc:
            raise ValueError(str(exc)) from exc
        duracion_segundos = duracion_wav_segundos(wav_bytes)

        # STT bring-your-own del TENANT — fail-closed a StubSTT (ver docstring
        # del módulo y de `edecan_meetings.stt`).
        async with deps.session_factory(None) as session:
            vault = deps.vault(session)
            stt = await resolver_stt_del_tenant(session=session, vault=vault, tenant_id=tenant_id)
        transcript = await stt.transcribe(wav_bytes, "audio/wav", None)
        transcript_texto = transcript.text
        aviso: str | None = _AVISO_STUB_STT if isinstance(stt, StubSTT) else None

        async with deps.session_factory(None) as session:
            transcript_file_id = await _subir_transcript(
                deps,
                session,
                tenant_id=tenant_id,
                user_id=reunion["user_id"],
                titulo=reunion["titulo"],
                texto=transcript_texto,
            )

        # LLM bring-your-own del TENANT — PEREZOSO, recién ahora que ya hay
        # transcript (ver docstring del módulo, "Flujo" paso 6). Lanza
        # `TenantLLMNotConnectedError` si el tenant no conectó nada — se deja
        # propagar tal cual hasta el `except` de abajo.
        llm_router = await deps.llm_router_for(tenant_id)

        async with deps.session_factory(None) as session:
            repo = SqlRepo(session)
            tenant_row = await repo.get_tenant(tenant_id=tenant_id)
        plan_key = tenant_row["plan_key"] if tenant_row else "free_selfhost"
        flags = dict(PLANES.get(plan_key, PLANES["free_selfhost"]).flags)

        provider, model = llm_router.resolve("principal", flags)
        prompt = construir_prompt_minutas(transcript_texto, reunion["titulo"])
        response = await provider.complete(
            CompletionRequest(
                model=model,
                system=None,
                messages=[ChatMessage(role="user", content=prompt)],
                max_tokens=_MAX_TOKENS_MINUTAS,
            )
        )
        minutas = parsear_minutas(response.text)

        async with deps.session_factory(None) as session:
            await _guardar_resultado(
                session,
                tenant_id=tenant_id,
                meeting_id=meeting_id,
                resumen=minutas.resumen,
                decisiones=list(minutas.decisiones),
                acciones=[a.to_dict() for a in minutas.acciones],
                temas=list(minutas.temas),
                transcript_file_id=transcript_file_id,
                duracion_segundos=duracion_segundos,
                error=aviso,
            )
    except Exception as exc:
        async with deps.session_factory(None) as session:
            await _marcar_error(
                session, tenant_id=tenant_id, meeting_id=meeting_id, mensaje=str(exc)
            )
        raise

    # `usage_events` (telemetría de facturación) se registra DESPUÉS, en su
    # PROPIA transacción corta, deliberadamente FUERA del `try` de arriba —
    # regla de oro de `HOTFIXES_PENDIENTES.md` puntos 8/9 (BARRIDO C,
    # WP-V7-04): antes de este fix, `add_usage_event` corría DENTRO de la
    # MISMA transacción que `_guardar_resultado` — un fallo ahí (una fila con
    # constraint que choca, un blip de conexión) se hubiera llevado puesto el
    # `UPDATE status='done'` YA EXITOSO, y el `except` exterior habría
    # marcado la reunión `status='error'` pese a que la transcripción+minutas
    # SÍ se generaron — y como `'error'` es un estado TERMINAL
    # (`_ESTADOS_TERMINALES`), ningún reintento automático la habría vuelto a
    # procesar jamás (ver `_cargar_o_crear_reunion`). Ahora un fallo acá se
    # registra pero NUNCA revierte ni reintenta el resultado ya entregado —
    # es best-effort, igual que cualquier telemetría secundaria (mismo
    # criterio que ya aplican `hooks.py::trigger_hook` y
    # `voz_avanzada.py::crear_clon_voz` para su propia evidencia primaria).
    try:
        async with deps.session_factory(None) as session:
            repo = SqlRepo(session)
            await repo.add_usage_event(
                tenant_id=tenant_id,
                kind="llm_tokens",
                quantity=float(response.usage.input_tokens + response.usage.output_tokens),
                meta={"model": model, "alias": "principal", "meeting_id": str(meeting_id)},
            )
    except Exception:
        logger.exception(
            "process_meeting: no se pudo registrar usage_event (meeting_id=%s tenant_id=%s) "
            "— la reunión SÍ quedó 'done' con su resumen/minutos; solo se perdió la "
            "telemetría de uso de este turno.",
            meeting_id,
            tenant_id,
        )

    logger.info(
        "process_meeting completado meeting_id=%s tenant_id=%s duracion_segundos=%s stub_stt=%s",
        meeting_id,
        tenant_id,
        duracion_segundos,
        aviso is not None,
    )
