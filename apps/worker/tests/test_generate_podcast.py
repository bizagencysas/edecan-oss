"""Tests del job `generate_podcast`: sintetiza un podcast con el TTS bring-
your-own del tenant, lo guarda como archivo, y mantiene la fila `podcasts`
al día (`ARCHITECTURE.md` §14, WP-V5-11 + WP-V6-04 — ver el docstring del
propio handler para el contrato completo de los dos payloads/"una sola ruta
de estado").

`_FakeSession`/`_FakeS3ConPut` son fakes PROPIOS de este archivo (no se toca
`apps/worker/tests/fakes.py` compartido, tal como exige el paquete de
trabajo — mismo criterio que `apps/worker/tests/test_llm_por_tenant.py`):
`fakes.FakeSession` es un placeholder sin `execute()` (los handlers v1
hablan SIEMPRE con `SqlRepo`, nunca con `session.execute` directo, ver su
docstring), y este handler SÍ necesita `session.execute` crudo (mismo motivo
que `edecan_creative._files.subir_archivo`: `files`/`podcasts` no tienen un
método dedicado en `edecan_worker.repo.Repo`). `fakes.FakeS3` tampoco
implementa `put_object` — ningún handler v1..v4 sube archivos NUEVOS a S3,
solo los leen (`ingest_file`).

`_session_factory_de` entrega SIEMPRE la MISMA `_FakeSession`, sin importar
cuántas veces `handle()` abra `deps.session_factory(None)` (el handler abre
varias: crear la fila si el payload es viejo, marcar `'running'`, el trabajo
principal, y — ante error — marcar `'error'`) — así que `session.llamadas`
acumula TODAS las queries de una corrida completa en el orden exacto en que
se piden, y cada test programa `session.respuestas` con esa misma secuencia.
"""

from __future__ import annotations

import asyncio
import io
import json
import uuid
import wave
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import edecan_worker.handlers.generate_podcast as generate_podcast_module
import httpx
import pytest
import respx
from edecan_creative import podcast as podcast_module
from edecan_creative.podcast import GuionInvalidoError
from edecan_schemas import JobEnvelope
from edecan_worker.config import Settings
from fakes import FakeTokenBundle, FakeVault, make_deps, utcnow

_SENTINEL = "FUGA_DE_PLATAFORMA_NO_DEBE_APARECER"


# ---------------------------------------------------------------------------
# Fakes locales — ver docstring del módulo.
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]] | None) -> None:
        self._rows = rows or []

    def mappings(self) -> _FakeResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class _FakeSession:
    """Entiende cualquier `execute()` crudo: SELECT/INSERT/UPDATE sobre
    `podcasts`, el SELECT sobre `connector_accounts` de
    `resolver_config_tts_tenant`, y el INSERT final sobre `files`. Devuelve
    respuestas programadas en orden de llegada — mismo patrón que
    `packages/creative/tests/conftest.py::FakeSession`.

    `fallar_en_llamada` (barrido WP-V7-03, "¿audio huérfano en el object
    store?"): índice (0-based, en el orden real en que el handler pide cada
    `execute()`) en el que este fake debe lanzar en vez de responder — para
    simular un blip de DB justo DESPUÉS de que `deps.s3.put_object` ya tuvo
    éxito (ver `test_generate_podcast_fallo_db_tras_subir_a_s3_...`)."""

    def __init__(
        self,
        respuestas: list[list[dict[str, Any]]] | None = None,
        *,
        fallar_en_llamada: int | None = None,
    ) -> None:
        self.respuestas: list[list[dict[str, Any]]] = list(respuestas or [])
        self.llamadas: list[tuple[str, dict[str, Any]]] = []
        self._fallar_en_llamada = fallar_en_llamada

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        indice = len(self.llamadas)
        self.llamadas.append((str(stmt), dict(params or {})))
        if self._fallar_en_llamada is not None and indice == self._fallar_en_llamada:
            raise RuntimeError("blip de conectividad simulado (barrido WP-V7-03)")
        filas = self.respuestas.pop(0) if self.respuestas else []
        return _FakeResult(filas)


def _session_factory_de(session: Any):
    """`SessionFactory` que siempre entrega `session`, sin importar el
    `tenant_id` pedido — mismo patrón que
    `apps/worker/tests/test_llm_por_tenant.py::_session_factory_de`."""

    @asynccontextmanager
    async def _factory(tenant_id: uuid.UUID | None):
        yield session

    return _factory


class _FakeS3ConPut:
    """`fakes.FakeS3` (compartido) no implementa `put_object` — ver
    docstring del módulo. También implementa `delete_object` (barrido
    WP-V7-03, "¿audio huérfano en el object store?", ver el docstring de
    `generate_podcast.handle`, sección "Audio huérfano en S3...") — el
    constructor acepta `fallar_delete=True` para ejercitar el camino donde el
    propio best-effort de borrado también falla."""

    def __init__(self, *, fallar_delete: bool = False) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.put_calls: list[dict[str, Any]] = []
        self.delete_calls: list[dict[str, Any]] = []
        self._fallar_delete = fallar_delete

    async def put_object(
        self, *, Bucket: str, Key: str, Body: bytes, ContentType: str
    ) -> dict[str, Any]:
        self.objects[(Bucket, Key)] = Body
        self.put_calls.append(
            {"Bucket": Bucket, "Key": Key, "Body": Body, "ContentType": ContentType}
        )
        return {}

    async def delete_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self.delete_calls.append({"Bucket": Bucket, "Key": Key})
        if self._fallar_delete:
            raise RuntimeError("S3 no disponible para el borrado best-effort")
        self.objects.pop((Bucket, Key), None)
        return {}


class _FakeFFmpegProcess:
    def __init__(self, *, returncode: int = 0, stderr: bytes = b"") -> None:
        self.returncode = returncode
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", self._stderr

    def kill(self) -> None:
        pass

    async def wait(self) -> None:
        return None


def _env(*, tenant_id: uuid.UUID, payload: dict[str, Any]) -> JobEnvelope:
    return JobEnvelope(
        job_id=uuid.uuid4(), tenant_id=tenant_id, type="generate_podcast", payload=payload
    )


def _podcast_row(**overrides: Any) -> dict[str, Any]:
    now = utcnow()
    base = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "titulo": "Mi Podcast",
        "guion": [{"texto": "hola", "voz": None}],
        "status": "running",
        "file_id": None,
        "error": None,
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Guardas tempranas (comunes a ambos payloads, o específicas del viejo)
# ---------------------------------------------------------------------------


async def test_generate_podcast_sin_tenant_id_lanza():
    with pytest.raises(ValueError, match="tenant_id"):
        await generate_podcast_module.handle(
            JobEnvelope(
                job_id=uuid.uuid4(),
                tenant_id=None,
                type="generate_podcast",
                payload={"podcast_id": str(uuid.uuid4())},
            ),
            make_deps(),
        )


async def test_generate_podcast_payload_legacy_sin_user_id_lanza_y_no_toca_nada():
    session = _FakeSession()
    s3 = _FakeS3ConPut()
    deps = make_deps(session_factory=_session_factory_de(session), s3=s3)

    env = _env(
        tenant_id=uuid.uuid4(), payload={"titulo": "T", "segmentos": [{"texto": "hola"}]}
    )

    with pytest.raises(ValueError, match="user_id"):
        await generate_podcast_module.handle(env, deps)

    assert session.llamadas == []
    assert s3.put_calls == []


async def test_generate_podcast_payload_legacy_guion_vacio_nunca_llama_tts_ni_s3():
    """Un guion vacío/malformado (`GuionInvalidoError`, subclase de
    `ValueError`) se deja propagar SIN abrir sesión, sin crear la fila
    `podcasts` y sin tocar S3 — se valida antes de todo lo demás (ver
    docstring del handler, 'Errores: se marcan ... y se dejan propagar')."""
    session = _FakeSession()
    s3 = _FakeS3ConPut()
    deps = make_deps(session_factory=_session_factory_de(session), s3=s3)

    env = _env(
        tenant_id=uuid.uuid4(),
        payload={"titulo": "T", "user_id": str(uuid.uuid4()), "segmentos": []},
    )

    with pytest.raises(GuionInvalidoError):
        await generate_podcast_module.handle(env, deps)

    assert session.llamadas == []
    assert s3.put_calls == []


async def test_generate_podcast_payload_legacy_segmento_sin_texto_lanza_guion_invalido():
    deps = make_deps(session_factory=_session_factory_de(_FakeSession()), s3=_FakeS3ConPut())
    env = _env(
        tenant_id=uuid.uuid4(),
        payload={"titulo": "T", "user_id": str(uuid.uuid4()), "segmentos": [{"orador": "Ana"}]},
    )

    with pytest.raises(GuionInvalidoError, match="texto"):
        await generate_podcast_module.handle(env, deps)


# ---------------------------------------------------------------------------
# Payload NUEVO ({"podcast_id": ...}) — camino feliz stub
# ---------------------------------------------------------------------------


async def test_generate_podcast_nuevo_payload_stub_end_to_end_sube_wav_y_marca_done():
    tenant_id, user_id, podcast_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    fila = _podcast_row(
        id=podcast_id,
        tenant_id=tenant_id,
        user_id=user_id,
        titulo="Podcast Nuevo",
        guion=[{"texto": "Hola a todos", "voz": None}, {"texto": "Segundo segmento", "voz": None}],
    )
    # [0] UPDATE running RETURNING *, [1] SELECT connector_accounts (sin cuenta -> stub)
    session = _FakeSession(respuestas=[[fila], []])
    s3 = _FakeS3ConPut()
    deps = make_deps(session_factory=_session_factory_de(session), s3=s3)

    env = _env(tenant_id=tenant_id, payload={"podcast_id": str(podcast_id)})

    await generate_podcast_module.handle(env, deps)

    assert len(s3.put_calls) == 1
    subida = s3.put_calls[0]
    assert subida["ContentType"] == "audio/wav"
    assert subida["Key"].startswith(f"tenants/{tenant_id}/files/")
    assert subida["Key"].endswith(".wav")

    with wave.open(io.BytesIO(subida["Body"]), "rb") as clip:
        assert clip.getnchannels() == 1
        assert clip.getnframes() == int(16_000 * 0.5) * 2

    assert len(session.llamadas) == 4
    sql_running, params_running = session.llamadas[0]
    assert "UPDATE podcasts" in sql_running
    assert "running" in sql_running
    assert params_running == {"id": podcast_id, "tenant_id": tenant_id}

    sql_select, _ = session.llamadas[1]
    assert "connector_accounts" in sql_select

    sql_files, params_files = session.llamadas[2]
    assert "INSERT INTO files" in sql_files
    assert params_files["tenant_id"] == tenant_id
    assert params_files["user_id"] == user_id
    assert params_files["filename"].startswith("podcast-nuevo")
    assert params_files["s3_key"] == subida["Key"]

    sql_done, params_done = session.llamadas[3]
    assert "UPDATE podcasts" in sql_done
    assert "'done'" in sql_done
    assert params_done["id"] == podcast_id
    assert params_done["tenant_id"] == tenant_id
    assert params_done["file_id"] is not None


async def test_generate_podcast_nuevo_podcast_id_no_encontrado_lanza_y_no_marca_error():
    """Sin fila que actualizar, no hay ninguna evidencia de la que dejar
    constancia — el `ValueError` se deja propagar directo, sin abrir una
    segunda sesión para `_marcar_error` (ver docstring del handler)."""
    tenant_id, podcast_id = uuid.uuid4(), uuid.uuid4()
    session = _FakeSession(respuestas=[[]])  # UPDATE running -> ninguna fila
    deps = make_deps(session_factory=_session_factory_de(session), s3=_FakeS3ConPut())

    env = _env(tenant_id=tenant_id, payload={"podcast_id": str(podcast_id)})

    with pytest.raises(ValueError, match=str(podcast_id)):
        await generate_podcast_module.handle(env, deps)

    assert len(session.llamadas) == 1


# ---------------------------------------------------------------------------
# Payload VIEJO (tool de chat) — crea la fila `podcasts` al vuelo, luego la
# MISMA ruta de estado que el payload nuevo.
# ---------------------------------------------------------------------------


async def test_generate_podcast_legacy_crea_fila_podcasts_y_stub_end_to_end():
    tenant_id, user_id, podcast_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    fila_running = _podcast_row(
        id=podcast_id,
        tenant_id=tenant_id,
        user_id=user_id,
        titulo="Mi Podcast",
        guion=[{"texto": "Hola a todos", "voz": None}, {"texto": "Segundo segmento", "voz": None}],
    )
    # [0] INSERT INTO podcasts RETURNING id, [1] UPDATE running RETURNING *,
    # [2] SELECT connector_accounts (sin cuenta -> stub).
    session = _FakeSession(respuestas=[[{"id": podcast_id}], [fila_running], []])
    s3 = _FakeS3ConPut()
    deps = make_deps(session_factory=_session_factory_de(session), s3=s3)

    env = _env(
        tenant_id=tenant_id,
        payload={
            "titulo": "Mi Podcast",
            "user_id": str(user_id),
            "segmentos": [
                {"orador": "Ana", "texto": "Hola a todos"},
                {"texto": "Segundo segmento"},
            ],
        },
    )

    await generate_podcast_module.handle(env, deps)

    assert len(s3.put_calls) == 1
    assert s3.put_calls[0]["ContentType"] == "audio/wav"

    assert len(session.llamadas) == 5

    sql_insert, params_insert = session.llamadas[0]
    assert "INSERT INTO podcasts" in sql_insert
    assert params_insert["tenant_id"] == tenant_id
    assert params_insert["user_id"] == user_id
    assert params_insert["titulo"] == "Mi Podcast"
    guion_enviado = json.loads(params_insert["guion"])
    assert guion_enviado == [
        {"texto": "Hola a todos", "voz": None},
        {"texto": "Segundo segmento", "voz": None},
    ]

    sql_running, params_running = session.llamadas[1]
    assert "UPDATE podcasts" in sql_running and "running" in sql_running
    assert params_running["id"] == podcast_id

    sql_select, _ = session.llamadas[2]
    assert "connector_accounts" in sql_select

    sql_files, params_files = session.llamadas[3]
    assert "INSERT INTO files" in sql_files
    assert params_files["user_id"] == user_id

    sql_done, params_done = session.llamadas[4]
    assert "UPDATE podcasts" in sql_done and "'done'" in sql_done
    assert params_done["id"] == podcast_id


async def test_generate_podcast_legacy_titulo_ausente_usa_default():
    tenant_id, user_id, podcast_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    fila_running = _podcast_row(
        id=podcast_id,
        tenant_id=tenant_id,
        user_id=user_id,
        titulo="podcast",
        guion=[{"texto": "hola", "voz": None}],
    )
    session = _FakeSession(respuestas=[[{"id": podcast_id}], [fila_running], []])
    s3 = _FakeS3ConPut()
    deps = make_deps(session_factory=_session_factory_de(session), s3=s3)

    env = _env(
        tenant_id=tenant_id,
        payload={"user_id": str(user_id), "segmentos": [{"texto": "hola"}]},
    )

    await generate_podcast_module.handle(env, deps)

    assert s3.put_calls[0]["Key"].endswith("/podcast.wav")
    _sql_insert, params_insert = session.llamadas[0]
    assert params_insert["titulo"] == "podcast"


# ---------------------------------------------------------------------------
# Anti-fuga: sin credencial del tenant, jamás el centinela de plataforma
# ---------------------------------------------------------------------------


async def test_generate_podcast_sin_credencial_del_tenant_nunca_usa_centinela_de_plataforma(
    monkeypatch,
):
    tenant_id, podcast_id = uuid.uuid4(), uuid.uuid4()
    fila = _podcast_row(id=podcast_id, tenant_id=tenant_id)
    session = _FakeSession(respuestas=[[fila], []])  # el tenant NUNCA conectó "voice_tts"
    s3 = _FakeS3ConPut()
    settings = Settings(
        SQS_QUEUE_URL="http://localhost:4566/000000000000/edecan-jobs",
        S3_BUCKET="edecan-files-test",
        ELEVENLABS_API_KEY=_SENTINEL,  # "plataforma" SÍ tiene una key real disponible
    )
    deps = make_deps(session_factory=_session_factory_de(session), s3=s3, settings=settings)

    def _explota(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("no debía llamar a ElevenLabs sin credencial del tenant")

    monkeypatch.setattr(podcast_module, "_elevenlabs_text_to_speech", _explota)
    monkeypatch.setattr(podcast_module, "_elevenlabs_sound_generation", _explota)

    env = _env(tenant_id=tenant_id, payload={"podcast_id": str(podcast_id)})

    await generate_podcast_module.handle(env, deps)

    subida = s3.put_calls[0]
    assert subida["ContentType"] == "audio/wav"
    assert _SENTINEL.encode() not in subida["Body"]


# ---------------------------------------------------------------------------
# Camino feliz — ElevenLabs real (mockeado con respx), payload nuevo
# ---------------------------------------------------------------------------


@respx.mock
async def test_generate_podcast_nuevo_payload_un_segmento_con_elevenlabs_sube_mp3_sin_ffmpeg():
    """Un solo segmento no requiere ensamblar nada (`ensamblar_podcast` con
    un único clip lo devuelve tal cual) — este test NO mockea ffmpeg a
    propósito, para probar que el camino de 1 segmento nunca lo necesita."""
    tenant_id, podcast_id = uuid.uuid4(), uuid.uuid4()
    cuenta_id = "11111111-1111-1111-1111-111111111111"
    fila = _podcast_row(
        id=podcast_id, tenant_id=tenant_id, guion=[{"texto": "un solo segmento", "voz": None}]
    )
    session = _FakeSession(respuestas=[[fila], [{"id": cuenta_id}]])
    vault = FakeVault()
    vault.store[(tenant_id, cuenta_id)] = FakeTokenBundle(
        access_token=json.dumps(
            {"provider": "elevenlabs", "api_key": "clave-del-tenant", "voice_id": "voz-x"}
        )
    )
    s3 = _FakeS3ConPut()
    deps = make_deps(
        session_factory=_session_factory_de(session), s3=s3, vault=lambda sess: vault
    )

    ruta = respx.post("https://api.elevenlabs.io/v1/text-to-speech/voz-x").mock(
        return_value=httpx.Response(200, content=b"mp3-real-de-elevenlabs")
    )

    env = _env(tenant_id=tenant_id, payload={"podcast_id": str(podcast_id)})

    await generate_podcast_module.handle(env, deps)

    assert ruta.called
    assert ruta.calls.last.request.headers["xi-api-key"] == "clave-del-tenant"
    assert len(s3.put_calls) == 1
    subida = s3.put_calls[0]
    assert subida["Body"] == b"mp3-real-de-elevenlabs"
    assert subida["ContentType"] == "audio/mpeg"
    assert subida["Key"].endswith(".mp3")

    _sql_insert, params_files = session.llamadas[2]
    assert params_files["mime"] == "audio/mpeg"
    assert params_files["filename"].endswith(".mp3")

    _sql_done, params_done = session.llamadas[3]
    assert "'done'" in _sql_done


@respx.mock
async def test_generate_podcast_nuevo_payload_dos_segmentos_ensambla_con_ffmpeg_mockeado(
    monkeypatch,
):
    tenant_id, podcast_id = uuid.uuid4(), uuid.uuid4()
    cuenta_id = "22222222-2222-2222-2222-222222222222"
    fila = _podcast_row(
        id=podcast_id,
        tenant_id=tenant_id,
        guion=[{"texto": "uno", "voz": None}, {"texto": "dos", "voz": None}],
    )
    session = _FakeSession(respuestas=[[fila], [{"id": cuenta_id}]])
    vault = FakeVault()
    vault.store[(tenant_id, cuenta_id)] = FakeTokenBundle(
        access_token=json.dumps(
            {"provider": "elevenlabs", "api_key": "clave-del-tenant", "voice_id": "voz-y"}
        )
    )
    s3 = _FakeS3ConPut()
    deps = make_deps(
        session_factory=_session_factory_de(session), s3=s3, vault=lambda sess: vault
    )

    respx.post("https://api.elevenlabs.io/v1/text-to-speech/voz-y").mock(
        return_value=httpx.Response(200, content=b"mp3-segmento")
    )

    monkeypatch.setattr(podcast_module, "ffmpeg_disponible", lambda: "/usr/bin/ffmpeg")

    async def _fake_exec(*args: str, **kwargs: Any) -> _FakeFFmpegProcess:
        Path(args[-1]).write_bytes(b"mp3-concatenado-por-ffmpeg")
        return _FakeFFmpegProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    env = _env(tenant_id=tenant_id, payload={"podcast_id": str(podcast_id)})

    await generate_podcast_module.handle(env, deps)

    subida = s3.put_calls[0]
    assert subida["Body"] == b"mp3-concatenado-por-ffmpeg"
    assert subida["ContentType"] == "audio/mpeg"


# ---------------------------------------------------------------------------
# Fallo real de ElevenLabs: marca 'error' en `podcasts` y re-lanza
# ---------------------------------------------------------------------------


@respx.mock
async def test_generate_podcast_fallo_elevenlabs_marca_error_y_relanza_nunca_cae_a_stub():
    """El tenant SÍ conectó una credencial: un fallo real hablando con
    ElevenLabs debe propagarse (reintento/DLQ del despachador), nunca
    degradar en silencio a un podcast stub — Y debe dejar constancia en
    `podcasts.status='error'`/`podcasts.error` en una sesión nueva (ver
    docstring del handler)."""
    tenant_id, podcast_id = uuid.uuid4(), uuid.uuid4()
    cuenta_id = "33333333-3333-3333-3333-333333333333"
    fila = _podcast_row(id=podcast_id, tenant_id=tenant_id, guion=[{"texto": "hola", "voz": None}])
    session = _FakeSession(respuestas=[[fila], [{"id": cuenta_id}]])
    vault = FakeVault()
    vault.store[(tenant_id, cuenta_id)] = FakeTokenBundle(
        access_token=json.dumps(
            {"provider": "elevenlabs", "api_key": "clave-invalida", "voice_id": "voz-z"}
        )
    )
    s3 = _FakeS3ConPut()
    deps = make_deps(
        session_factory=_session_factory_de(session), s3=s3, vault=lambda sess: vault
    )

    respx.post("https://api.elevenlabs.io/v1/text-to-speech/voz-z").mock(
        return_value=httpx.Response(401, text="unauthorized")
    )

    env = _env(tenant_id=tenant_id, payload={"podcast_id": str(podcast_id)})

    with pytest.raises(podcast_module.SintesisError):
        await generate_podcast_module.handle(env, deps)

    assert s3.put_calls == []
    # El fallo ocurrió ANTES de llegar a `put_object` (sintetizando el
    # segmento) -- `s3_subido` nunca se puso en `True`, así que el
    # best-effort de limpieza de la sección "Audio huérfano en S3" del
    # docstring del handler ni siquiera se intenta (nada que borrar).
    assert s3.delete_calls == []

    # [0] UPDATE running, [1] SELECT connector_accounts, [2] UPDATE error.
    assert len(session.llamadas) == 3
    sql_error, params_error = session.llamadas[2]
    assert "UPDATE podcasts" in sql_error
    assert "'error'" in sql_error
    assert params_error["id"] == podcast_id
    assert params_error["tenant_id"] == tenant_id
    assert "401" in params_error["error"]
    assert "clave-invalida" not in params_error["error"]


# ---------------------------------------------------------------------------
# Audio huérfano en S3 tras un fallo de DB posterior a la subida (barrido
# WP-V7-03, ver docstring del handler sección "Audio huérfano en S3...").
# ---------------------------------------------------------------------------


async def test_generate_podcast_fallo_db_tras_subir_a_s3_borra_el_objeto_huerfano():
    """`put_object` tiene éxito pero el `INSERT INTO files` que sigue (misma
    transacción) revienta con un blip de DB -- el handler debe intentar un
    best-effort de `delete_object` para no dejar el audio huérfano sin
    ninguna fila que lo referencie, y de todas formas marcar
    `podcasts.status='error'` con la excepción ORIGINAL (no una de
    limpieza) antes de volver a lanzarla."""
    tenant_id, podcast_id = uuid.uuid4(), uuid.uuid4()
    fila = _podcast_row(id=podcast_id, tenant_id=tenant_id)
    # [0] UPDATE running, [1] SELECT connector_accounts (sin cuenta -> stub),
    # [2] INSERT INTO files -> revienta.
    session = _FakeSession(respuestas=[[fila], []], fallar_en_llamada=2)
    s3 = _FakeS3ConPut()
    deps = make_deps(session_factory=_session_factory_de(session), s3=s3)

    env = _env(tenant_id=tenant_id, payload={"podcast_id": str(podcast_id)})

    with pytest.raises(RuntimeError, match="blip de conectividad"):
        await generate_podcast_module.handle(env, deps)

    # El audio SÍ se subió (`put_object` corrió antes del fallo de DB)...
    assert len(s3.put_calls) == 1
    subida = s3.put_calls[0]
    # ...pero el best-effort de limpieza lo volvió a borrar.
    assert len(s3.delete_calls) == 1
    assert s3.delete_calls[0] == {"Bucket": subida["Bucket"], "Key": subida["Key"]}
    assert (subida["Bucket"], subida["Key"]) not in s3.objects

    # [0] UPDATE running, [1] SELECT connector_accounts, [2] INSERT INTO
    # files (revienta), [3] UPDATE error (sesión nueva y corta).
    assert len(session.llamadas) == 4
    sql_error, params_error = session.llamadas[3]
    assert "UPDATE podcasts" in sql_error
    assert "'error'" in sql_error
    assert params_error["id"] == podcast_id
    assert params_error["tenant_id"] == tenant_id
    assert "blip de conectividad" in params_error["error"]


async def test_generate_podcast_fallo_al_borrar_huerfano_no_enmascara_el_error_original():
    """Si el propio `delete_object` best-effort TAMBIÉN falla (S3 caído),
    el mensaje que termina en `podcasts.error` sigue siendo el ORIGINAL (el
    de la escritura de DB que disparó todo esto) -- nunca el de la limpieza
    -- y la excepción que se vuelve a lanzar (para que el despachador
    reintente/DLQ) tampoco cambia."""
    tenant_id, podcast_id = uuid.uuid4(), uuid.uuid4()
    fila = _podcast_row(id=podcast_id, tenant_id=tenant_id)
    session = _FakeSession(respuestas=[[fila], []], fallar_en_llamada=2)
    s3 = _FakeS3ConPut(fallar_delete=True)
    deps = make_deps(session_factory=_session_factory_de(session), s3=s3)

    env = _env(tenant_id=tenant_id, payload={"podcast_id": str(podcast_id)})

    with pytest.raises(RuntimeError, match="blip de conectividad"):
        await generate_podcast_module.handle(env, deps)

    assert len(s3.delete_calls) == 1  # lo intentó, best-effort...
    assert len(s3.put_calls) == 1
    subida = s3.put_calls[0]
    # ...pero como el propio borrado falló, el objeto sigue "existiendo".
    assert (subida["Bucket"], subida["Key"]) in s3.objects

    sql_error, params_error = session.llamadas[3]
    assert "'error'" in sql_error
    assert "blip de conectividad" in params_error["error"]  # el ORIGINAL, no el de limpieza


# ---------------------------------------------------------------------------
# Reintento: una fila que quedó 'running'/'error' de un intento previo se
# reprocesa sin ninguna limpieza especial (ver docstring del handler).
# ---------------------------------------------------------------------------


async def test_generate_podcast_reintento_sobre_la_misma_fila_reprocesa_normalmente():
    """Simula el dispatcher redirigiendo el MISMO `podcast_id` a una segunda
    corrida (p. ej. tras un `error` previo, o un proceso que murió a mitad
    de un `running`) — `_marcar_running` no necesita saber el estado previo,
    así que la segunda corrida completa el pipeline igual que si fuera la
    primera."""
    tenant_id, podcast_id = uuid.uuid4(), uuid.uuid4()
    fila = _podcast_row(id=podcast_id, tenant_id=tenant_id)
    session = _FakeSession(respuestas=[[fila], []])
    s3 = _FakeS3ConPut()
    deps = make_deps(session_factory=_session_factory_de(session), s3=s3)

    env = _env(tenant_id=tenant_id, payload={"podcast_id": str(podcast_id)})
    await generate_podcast_module.handle(env, deps)

    assert len(s3.put_calls) == 1


async def test_marcar_running_where_no_filtra_por_status_actual_es_idempotente():
    """Regresión estructural: el `UPDATE` de `_marcar_running` solo debe
    filtrar por `id`/`tenant_id` en el `WHERE` — nunca por el `status`
    ACTUAL. Si algún día se agrega una condición `AND status = 'pending'`,
    un reintento sobre una fila `'running'`/`'error'` abandonada dejaría de
    poder recuperarse jamás (ver docstring del módulo)."""
    session = _FakeSession(respuestas=[[_podcast_row()]])
    tenant_id, podcast_id = uuid.uuid4(), uuid.uuid4()

    await generate_podcast_module._marcar_running(
        session, tenant_id=tenant_id, podcast_id=podcast_id
    )

    sql, params = session.llamadas[0]
    where_clause = sql.split("WHERE", 1)[1].lower()
    assert "status" not in where_clause
    assert params == {"id": podcast_id, "tenant_id": tenant_id}


# ---------------------------------------------------------------------------
# `_guion_desde_jsonb` — decodificación defensiva (unit, sin pasar por `handle`)
# ---------------------------------------------------------------------------


def test_guion_desde_jsonb_ya_decodificada_como_lista():
    assert generate_podcast_module._guion_desde_jsonb([{"texto": "a"}]) == [{"texto": "a"}]


def test_guion_desde_jsonb_string_json_valido():
    assert generate_podcast_module._guion_desde_jsonb(json.dumps([{"texto": "a"}])) == [
        {"texto": "a"}
    ]


def test_guion_desde_jsonb_string_invalido_devuelve_lista_vacia():
    assert generate_podcast_module._guion_desde_jsonb("esto-no-es-json{") == []


def test_guion_desde_jsonb_tipo_inesperado_devuelve_lista_vacia():
    assert generate_podcast_module._guion_desde_jsonb(None) == []
    assert generate_podcast_module._guion_desde_jsonb(42) == []
    assert generate_podcast_module._guion_desde_jsonb({"no": "es lista"}) == []
