"""Job `generate_podcast`: sintetiza un podcast completo con el TTS bring-
your-own del tenant (ElevenLabs o Stub) y lo guarda como archivo del usuario
(`ARCHITECTURE.md` §14, dueño WP-V5-01; work package real WP-V5-11 y, para el
vertical REST/UI + la fila `podcasts`, WP-V6-04).

## Dos payloads — una sola ruta de estado (WP-V6-04)

- **Nuevo** `{"podcast_id": "<uuid>"}` — encolado por `POST /v1/voz/podcasts`
  (`apps/api/edecan_api/routers/voz_avanzada.py`). La fila `podcasts` YA
  existe (`status='pending'`, `titulo`/`guion` ya guardados por el router) —
  este handler solo la carga, la procesa y actualiza su `status`.
- **Viejo** `{"titulo": str, "segmentos": list[dict], "formato": "wav"|"mp3"|
  None, "user_id": "<uuid>"}` — encolado por `CrearPodcastTool`
  (`packages/creative/edecan_creative/tools.py`), sin `podcast_id`. Sigue
  funcionando exactamente igual que antes de WP-V6-04, MÁS un efecto nuevo:
  al arrancar, este handler crea la fila `podcasts` al vuelo (mismo esquema
  que una fila creada por el router, `status='pending'`) para que los
  podcasts pedidos por chat también aparezcan en `GET /v1/voz/podcasts` —
  así hay UNA sola ruta de estado a partir de ahí, sin importar el origen.
  (`formato`, si viene en el payload, se ignora igual que antes de este WP:
  el formato real siempre lo decide el proveedor TTS resuelto, nunca una
  preferencia del caller — ver `edecan_creative.podcast.sintetizar_segmento`.)

A partir de tener un `podcast_id` (nuevo o recién creado), la ruta es
ÚNICA: `_marcar_running` → pipeline de síntesis/ensamblado → `_marcar_done`
o, ante excepción, `_marcar_error`.

## `status='running'` se comitea ANTES de empezar la síntesis

`_marcar_running` corre en su PROPIA sesión corta (`async with
deps.session_factory(None)`), que se cierra (commit implícito) ANTES de
resolver el TTS/sintetizar/ensamblar — si el proceso muere a mitad del
trabajo pesado, la fila queda en `'running'` (nunca `'pending'` fantasma sin
ninguna señal de que un worker la tomó). Un reintento posterior sobre una
fila `'running'` abandonada (o `'error'`) es IDEMPOTENTE: `_marcar_running`
hace un `UPDATE` incondicional (sin `WHERE status = ...`), así que
simplemente la vuelve a poner en `'running'` y repite el trabajo — no hay
ninguna otra limpieza especial que hacer.

## Bring-your-own, fail-closed (patrón anti-fuga v4)

Este handler SOLO usa el TTS que el PROPIO tenant conectó
(`edecan_creative.podcast.resolver_config_tts_tenant`, connector_key
`"voice_tts"` — mismo mecanismo que `apps/api/edecan_api/routers/voice.py`).
JAMÁS lee `ELEVENLABS_API_KEY`/`VOICE_TTS_PROVIDER` de `Settings`/`.env` de
PLATAFORMA: `deps.settings` ni siquiera se le pasa a esa resolución (ver
`edecan_creative.podcast`, cuyas funciones de TTS no aceptan un parámetro
`settings` en absoluto — citando el hallazgo #1 de `DIRECCION_ACTUAL.md`
"v4 completado", el bug de fuga de credencial más serio del proyecto hasta
ahora). Sin credencial propia, cada segmento se sintetiza como un WAV de
silencio (stub) y el podcast completo sale en formato `wav` — el job NUNCA
revienta por falta de configuración.

## Errores: se marcan en `podcasts.error` y se dejan propagar

Un guion inválido (`GuionInvalidoError`), un fallo real hablando con
ElevenLabs cuando el tenant SÍ conectó una credencial (`SintesisError`), o
ffmpeg ausente/con error al ensamblar mp3 (`EnsambladoError`) — cualquier
excepción que ocurra DESPUÉS de que la fila ya está en `'running'` se
captura, se persiste en `podcasts.status='error'`/`podcasts.error` (sesión
nueva y corta, igual criterio que `_marcar_running`) y se vuelve a lanzar tal
cual — el despachador del job (`edecan_worker.main`/
`edecan_local.worker_loop`) lo trata como cualquier otro fallo (reintento
con backoff, luego DLQ). Este handler nunca traga excepciones. Los guardas
tempranos (`tenant_id` ausente, payload viejo sin `user_id`, guion inválido
del payload viejo ANTES de crear la fila) se dejan propagar directo, sin
tocar `podcasts` — no hay ninguna fila todavía de la que dejar constancia.

### Audio huérfano en S3 si el fallo llega DESPUÉS de subir (barrido WP-V7-03)

El `put_object` a S3 y el `INSERT INTO files`/`_marcar_done` que lo siguen
viven en la MISMA transacción de Postgres, pero son DOS sistemas distintos
sin comit atómico conjunto: si el `put_object` tiene éxito y la escritura de
Postgres que sigue falla (p. ej. un blip de conectividad), el objeto ya
quedó en S3 sin ninguna fila `files` que lo referencie. Este handler hace un
best-effort de `deps.s3.delete_object(...)` en ese caso exacto (rastreado
con la bandera local `s3_subido`) ANTES de marcar `podcasts.status='error'`
— un fallo del propio borrado NUNCA enmascara ni bloquea la excepción
original, solo se registra con `logger.warning`. **Limitación conocida,
aceptada, no resuelta por este best-effort**: un reintento del job
(`podcast_id` reprocesado desde `'error'`/`'running'` abandonado) vuelve a
sintetizar TODOS los segmentos desde cero (gasto real si el proveedor TTS es
de pago) y sube un archivo NUEVO con un `file_id`/`s3_key` distintos — mismo
criterio "el job reprocesa completo en cada intento" que el resto del
despachador (`ingest_file`, `memory_consolidate`, etc., `ARCHITECTURE.md`
§10.11); no hay una clave de idempotencia por segmento que evite pagar la
síntesis dos veces. Si el best-effort de borrado en sí también falla (S3
caído en el peor momento posible), el objeto huérfano queda de verdad hasta
una limpieza manual/lifecycle policy del bucket — fuera de alcance de este
handler.

## Tabla `podcasts` — `ARCHITECTURE.md` §15.b, migración `0008_v6_expansion`

Ver `apps/api/edecan_api/routers/voz_avanzada.py`, sección "Tabla
`podcasts`", para el esquema completo y su justificación (`guion` es
NULLABLE a nivel de esquema — inofensivo aquí: `_crear_podcast` siempre
provee un valor explícito, y `_guion_desde_jsonb` trata `None` como `[]`).
Resumen de las columnas que toca este archivo:
`id, tenant_id, user_id, titulo, guion jsonb, status, file_id, error,
created_at, updated_at`. `status ∈ {'pending','running','done','error'}` —
duplicado como literales SQL aquí y en el router a propósito (mismo criterio
que `_FLAG_VOICE_CLONING` en ese router: nunca se comparte una constante
entre `apps/api` y `apps/worker`, dos deployables independientes,
`ARCHITECTURE.md` §10.1).

## Import perezoso de `edecan_creative`

`edecan-creative` SÍ es una dependencia declarada de
`apps/worker/pyproject.toml` — el import se hace igual con `try/except
ImportError` DENTRO de `handle` (no al tope del módulo), mismo criterio
defensivo que el resto del repo ante paquetes hermanos (`ARCHITECTURE.md`
§10.1): un entorno con un checkout parcial (`uv sync --all-packages` no
corrido tras sumar la dependencia) falla con un mensaje claro en vez de un
`ModuleNotFoundError` críptico en medio del despachador de jobs.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from edecan_schemas import JobEnvelope
from sqlalchemy import text as sql_text

from edecan_worker.deps import Deps

logger = logging.getLogger(__name__)

DEFAULT_S3_BUCKET = "edecan-files"
_MIME_POR_FORMATO = {"mp3": "audio/mpeg", "wav": "audio/wav"}


# ---------------------------------------------------------------------------
# `podcasts` — SQL parametrizado (ver docstring del módulo, "Tabla podcasts").
# El worker se conecta como "dueño" (bypassa RLS, `ARCHITECTURE.md` §2): TODAS
# las queries de abajo filtran `tenant_id` a mano, igual que
# `edecan_worker.repo.SqlRepo`/`handlers/run_mission.py`.
# ---------------------------------------------------------------------------


def _guion_desde_jsonb(value: Any) -> list[dict[str, Any]]:
    """`guion` puede llegar como `list` ya decodificada o como texto JSON
    crudo según el driver — mismo criterio defensivo que
    `edecan_api.routers.voz_avanzada._guion_desde_jsonb` (duplicado a
    propósito, `apps/api`/`apps/worker` son deployables independientes)."""
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str):
        try:
            cargado = json.loads(value)
        except (TypeError, ValueError):
            return []
        return cargado if isinstance(cargado, list) else []
    return []


async def _crear_podcast(
    session: Any,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    titulo: str,
    guion: list[dict[str, Any]],
) -> uuid.UUID:
    """Fila `podcasts` nueva, `status='pending'` — solo la usa el payload
    VIEJO (payload nuevo ya trae la fila creada por el router, ver docstring
    del módulo)."""
    row = (
        await session.execute(
            sql_text(
                "INSERT INTO podcasts (tenant_id, user_id, titulo, guion, status) "
                "VALUES (:tenant_id, :user_id, :titulo, CAST(:guion AS jsonb), 'pending') "
                "RETURNING id"
            ),
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "titulo": titulo,
                "guion": json.dumps(guion),
            },
        )
    ).mappings().first()
    if row is None:  # defensivo: Postgres no devolvió la fila recién insertada.
        raise RuntimeError("No se pudo crear la fila del podcast (payload de chat/legacy).")
    return row["id"]


async def _marcar_running(
    session: Any, *, tenant_id: uuid.UUID, podcast_id: uuid.UUID
) -> dict[str, Any] | None:
    """`UPDATE` incondicional (sin `WHERE status = ...`, ver docstring del
    módulo: idempotente ante reintentos) que además trae `titulo`/`guion` en
    el mismo viaje — `None` si la fila no existe para ese tenant."""
    row = (
        await session.execute(
            sql_text(
                "UPDATE podcasts SET status = 'running', updated_at = now() "
                "WHERE id = :id AND tenant_id = :tenant_id RETURNING *"
            ),
            {"id": podcast_id, "tenant_id": tenant_id},
        )
    ).mappings().first()
    return dict(row) if row is not None else None


async def _marcar_done(
    session: Any, *, tenant_id: uuid.UUID, podcast_id: uuid.UUID, file_id: uuid.UUID
) -> None:
    await session.execute(
        sql_text(
            "UPDATE podcasts SET status = 'done', file_id = :file_id, error = NULL, "
            "updated_at = now() WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"file_id": file_id, "id": podcast_id, "tenant_id": tenant_id},
    )


async def _marcar_error(
    session: Any, *, tenant_id: uuid.UUID, podcast_id: uuid.UUID, error: str
) -> None:
    await session.execute(
        sql_text(
            "UPDATE podcasts SET status = 'error', error = :error, updated_at = now() "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"error": error, "id": podcast_id, "tenant_id": tenant_id},
    )


async def handle(env: JobEnvelope, deps: Deps) -> None:
    if env.tenant_id is None:
        raise ValueError("generate_podcast requiere tenant_id")
    tenant_id: uuid.UUID = env.tenant_id

    try:
        from edecan_creative.podcast import (
            ensamblar_podcast,
            resolver_config_tts_tenant,
            sintetizar_segmento,
            slugify,
            validar_guion,
        )
    except ImportError as exc:  # pragma: no cover - ver docstring del módulo
        raise RuntimeError(
            "El paquete 'edecan-creative' no está disponible en este entorno — "
            "generate_podcast lo necesita (ver apps/worker/pyproject.toml; si es un "
            "checkout desactualizado corre 'uv sync --all-packages')."
        ) from exc

    podcast_id_raw = env.payload.get("podcast_id")
    if podcast_id_raw:
        podcast_id = uuid.UUID(str(podcast_id_raw))
    else:
        # Payload VIEJO (tool de chat) — ver docstring del módulo. Se valida
        # ANTES de tocar la sesión (deja propagar GuionInvalidoError tal
        # cual, "Errores: se dejan propagar"): un guion malformado no debe
        # crear ninguna fila `podcasts` ni resolver la credencial del tenant.
        if not env.payload.get("user_id"):
            raise ValueError("generate_podcast requiere 'user_id' en el payload")
        user_id_legacy = uuid.UUID(str(env.payload["user_id"]))
        titulo_legacy = str(env.payload.get("titulo") or "").strip() or "podcast"
        segmentos_legacy = validar_guion(env.payload.get("segmentos"))
        guion_legacy = [{"texto": s.texto, "voz": s.voice_id} for s in segmentos_legacy]

        async with deps.session_factory(None) as session:
            podcast_id = await _crear_podcast(
                session,
                tenant_id=tenant_id,
                user_id=user_id_legacy,
                titulo=titulo_legacy,
                guion=guion_legacy,
            )
        logger.info(
            "generate_podcast: fila podcasts creada al vuelo (payload de chat/legacy) "
            "podcast_id=%s tenant_id=%s",
            podcast_id,
            tenant_id,
        )

    # A partir de acá, UNA sola ruta de estado sin importar el origen del
    # payload (ver docstring del módulo).
    async with deps.session_factory(None) as session:
        fila = await _marcar_running(session, tenant_id=tenant_id, podcast_id=podcast_id)
    if fila is None:
        raise ValueError(
            f"generate_podcast: no se encontró el podcast {podcast_id} para el "
            f"tenant {tenant_id}"
        )

    # `s3_subido` (barrido WP-V7-03, "¿audio huérfano en el object store?"):
    # sigue en `False` hasta que `put_object` de más abajo termine con éxito
    # — el `except` de abajo lo usa para decidir si hace falta un best-effort
    # de limpieza. Declarado ANTES del `try` para que el `except` siempre lo
    # encuentre definido, sin importar en qué línea haya lanzado la excepción.
    s3_subido = False
    try:
        titulo = fila["titulo"]
        user_id = uuid.UUID(str(fila["user_id"]))
        segmentos = validar_guion(
            [
                {"texto": item.get("texto"), "voice_id": item.get("voz")}
                for item in _guion_desde_jsonb(fila.get("guion"))
            ]
        )

        async with deps.session_factory(None) as session:
            vault = deps.vault(session)
            # Una sola resolución para TODO el podcast (no una por
            # segmento): los N segmentos de un mismo job comparten siempre
            # el mismo proveedor — ver docstring de
            # `edecan_creative.podcast.sintetizar_segmento`.
            cfg = await resolver_config_tts_tenant(
                session=session, vault=vault, tenant_id=tenant_id
            )

            audios: list[bytes] = []
            formato_real: str | None = None
            for segmento in segmentos:
                audio = await sintetizar_segmento(
                    cfg,
                    texto=segmento.texto,
                    voice_id=segmento.voice_id,
                    tenant_id=tenant_id,
                )
                if formato_real is None:
                    formato_real = audio.formato
                audios.append(audio.data)

            assert formato_real is not None  # validar_guion garantiza >= 1 segmento

            podcast_bytes = await ensamblar_podcast(audios, formato_real)

            filename = f"{slugify(titulo)}.{formato_real}"
            mime = _MIME_POR_FORMATO[formato_real]
            bucket = getattr(deps.settings, "S3_BUCKET", None) or DEFAULT_S3_BUCKET
            file_id = uuid.uuid4()
            s3_key = f"tenants/{tenant_id}/files/{file_id}/{filename}"

            await deps.s3.put_object(
                Bucket=bucket, Key=s3_key, Body=podcast_bytes, ContentType=mime
            )
            s3_subido = True

            # Mismas columnas EXACTAS que `edecan_creative._files.subir_archivo`
            # (`ARCHITECTURE.md` §10.3, §10.14): nace `status='ready'` directo,
            # igual que cualquier archivo generado (no pasa por `ingest_file`).
            await session.execute(
                sql_text(
                    "INSERT INTO files "
                    "(id, tenant_id, user_id, s3_key, filename, mime, size_bytes, status, "
                    "created_at, updated_at) "
                    "VALUES (:id, :tenant_id, :user_id, :s3_key, :filename, :mime, "
                    ":size_bytes, 'ready', :now, :now)"
                ),
                {
                    "id": file_id,
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "s3_key": s3_key,
                    "filename": filename,
                    "mime": mime,
                    "size_bytes": len(podcast_bytes),
                    "now": datetime.now(UTC),
                },
            )
            await _marcar_done(session, tenant_id=tenant_id, podcast_id=podcast_id, file_id=file_id)
    except Exception as exc:
        if s3_subido:
            # El audio YA se subió a S3 (línea de arriba) pero un paso de DB
            # posterior en la MISMA transacción (INSERT INTO files o
            # `_marcar_done`) falló — sin este best-effort, ese objeto queda
            # huérfano en el object store para siempre (nada en `files`/
            # `podcasts` lo referencia, y un reintento del job sintetiza y
            # sube uno NUEVO con un `file_id` distinto). Mismo criterio
            # best-effort que `voz_avanzada.py::revocar_clon_voz`/
            # `edecan_voice.cloning.borrar_clon`: un fallo AQUÍ nunca
            # enmascara ni bloquea el `_marcar_error`/`raise` de la excepción
            # real de abajo — solo se registra con `logger.warning`.
            try:
                await deps.s3.delete_object(Bucket=bucket, Key=s3_key)
            except Exception:
                logger.warning(
                    "No se pudo borrar el audio huérfano (s3_key=%s) del podcast %s tras un "
                    "fallo posterior a la subida — queda huérfano en el object store hasta "
                    "una limpieza manual.",
                    s3_key,
                    podcast_id,
                    exc_info=True,
                )
        async with deps.session_factory(None) as session:
            await _marcar_error(session, tenant_id=tenant_id, podcast_id=podcast_id, error=str(exc))
        raise

    logger.info(
        "generate_podcast completado podcast_id=%s file_id=%s tenant_id=%s segmentos=%d "
        "formato=%s bytes=%d stub=%s",
        podcast_id,
        file_id,
        tenant_id,
        len(segmentos),
        formato_real,
        len(podcast_bytes),
        cfg is None,
    )
