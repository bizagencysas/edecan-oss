"""Tests del job `process_meeting`: transcribe con el STT del tenant y genera
minutas con el LLM del tenant (`ARCHITECTURE.md` §15, WP-V6-05).

`_FakeSession` (local, NO se toca `apps/worker/tests/fakes.py` compartido —
mismo criterio que `test_generate_podcast.py`) entiende cualquier
`execute()` crudo contra `meetings`/`connector_accounts` (las únicas tablas
que `process_meeting.py` toca por SQL directo — `files`/`tenants`/
`usage_events` van por `SqlRepo`, monkeypatcheado a `fakes.FakeRepo`, mismo
patrón que `test_generate_content.py`). `ffmpeg` NUNCA se ejecuta: se
monkeypatchea `edecan_meetings.audio.extraer_audio_wav` directo (el import es
perezoso DENTRO de `process_meeting.handle()`, así que patchear el atributo
del módulo origen SÍ se ve reflejado — `from X import Y` dentro de la función
se re-resuelve en cada llamada).

Al final del archivo hay, además, una sección de integración contra Postgres
real (BARRIDO D, WP-V7-04, gateada por `DATABASE_URL`) — ahí `SqlRepo` NO se
monkeypatchea a propósito: es justo lo que esa sección verifica."""

from __future__ import annotations

import asyncio
import io
import json
import os
import uuid
import wave
from contextlib import asynccontextmanager
from typing import Any

import edecan_meetings.audio as audio_module
import edecan_worker.handlers.process_meeting as process_meeting_module
import httpx
import pytest
import respx
from edecan_schemas import JobEnvelope
from edecan_worker.deps import TenantLLMNotConnectedError
from fakes import FakeRepo, FakeTokenBundle, FakeVault, make_deps

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
    """Entiende cualquier `execute()` crudo (INSERT/SELECT/UPDATE sobre
    `meetings`, SELECT sobre `connector_accounts`). Devuelve respuestas
    programadas en el ORDEN EXACTO en que el código las pide — mismo patrón
    que `test_generate_podcast.py::_FakeSession`."""

    def __init__(self, respuestas: list[list[dict[str, Any]]] | None = None) -> None:
        self.respuestas: list[list[dict[str, Any]]] = list(respuestas or [])
        self.llamadas: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        self.llamadas.append((str(stmt), dict(params or {})))
        filas = self.respuestas.pop(0) if self.respuestas else []
        return _FakeResult(filas)


def _session_factory_de(session: Any):
    @asynccontextmanager
    async def _factory(tenant_id: uuid.UUID | None):
        yield session

    return _factory


class _FakeS3Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def read(self) -> bytes:
        return self._data


class _FakeS3ConGetYPut:
    """`fakes.FakeS3` (compartido) no implementa `put_object` — este handler,
    a diferencia de `ingest_file`, SÍ necesita `get_object` (descarga el
    audio/video de origen) Y `put_object` (sube la transcripción) — mismo
    motivo que `test_generate_podcast.py::_FakeS3ConPut`, pero además con
    lectura."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.put_calls: list[dict[str, Any]] = []

    def preload(self, bucket: str, key: str, data: bytes) -> None:
        self.objects[(bucket, key)] = data

    async def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        data = self.objects.get((Bucket, Key))
        if data is None:
            raise KeyError(f"objeto S3 no encontrado: s3://{Bucket}/{Key}")
        return {"Body": _FakeS3Body(data)}

    async def put_object(
        self, *, Bucket: str, Key: str, Body: bytes, ContentType: str
    ) -> dict[str, Any]:
        self.objects[(Bucket, Key)] = Body
        self.put_calls.append(
            {"Bucket": Bucket, "Key": Key, "Body": Body, "ContentType": ContentType}
        )
        return {}


def _wav_bytes(*, seconds: float = 1.0, rate: int = 16_000) -> bytes:
    buffer = io.BytesIO()
    n_frames = int(rate * seconds)
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(rate)
        wav_file.writeframes(b"\x00\x00" * n_frames)
    return buffer.getvalue()


def _mockear_extraccion_de_audio(monkeypatch: pytest.MonkeyPatch, wav_bytes: bytes) -> None:
    """Ver docstring del módulo: nunca se ejecuta ffmpeg real, se inyectan
    bytes WAV ya "extraídos" — patcheando el módulo ORIGEN, no
    `process_meeting_module` (el import ahí es perezoso)."""

    async def _fake_extraer(data: bytes, mime: str | None = None, **kwargs: Any) -> bytes:
        return wav_bytes

    monkeypatch.setattr(audio_module, "extraer_audio_wav", _fake_extraer)


async def _usar_llm_de_plataforma_como_router_del_tenant(
    deps: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mismo patrón que `test_generate_content.py`: monkeypatchea
    `deps.llm_router_for` para devolver `deps.llm_router` (el `FakeLLMRouter`
    en memoria) — sin esto, `Deps.llm_router_for` REAL intentaría resolver
    contra el vault/Postgres."""

    async def _fake(tenant_id: Any) -> Any:
        return deps.llm_router

    monkeypatch.setattr(deps, "llm_router_for", _fake)


def _minutas_json(**overrides: Any) -> str:
    base = {
        "resumen": "Se discutió el roadmap del trimestre.",
        "decisiones": ["Lanzar en marzo"],
        "acciones": [{"tarea": "Escribir el plan", "responsable": "Ana"}],
        "temas": ["roadmap"],
    }
    base.update(overrides)
    return json.dumps(base)


def _env(*, tenant_id: uuid.UUID, payload: dict[str, Any]) -> JobEnvelope:
    return JobEnvelope(
        job_id=uuid.uuid4(), tenant_id=tenant_id, type="process_meeting", payload=payload
    )


def _meeting_row(
    *,
    meeting_id: uuid.UUID,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    file_id: uuid.UUID,
    titulo: str,
    status: str = "running",
) -> dict[str, Any]:
    """Fila `meetings` falsa — mismas columnas que `RETURNING *`/`SELECT *`
    devolverían en Postgres real (incluido `status`, que la producción SIEMPRE
    trae). La columna real es `source_file_id` (no `file_id`) — ver
    `packages/db/alembic/versions/0008_v6_expansion.py` +
    `edecan_db.models.Meeting`; el parámetro de este helper se llama `file_id`
    solo por comodidad de los llamadores de este test."""
    return {
        "id": meeting_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "source_file_id": file_id,
        "titulo": titulo,
        "status": status,
    }


def _preparar_deps(
    *,
    session: _FakeSession,
    fake_repo: FakeRepo,
    s3: _FakeS3ConGetYPut,
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
    monkeypatch.setattr(process_meeting_module, "SqlRepo", lambda _session: fake_repo)
    deps = make_deps(session_factory=_session_factory_de(session), s3=s3)
    return deps


# ---------------------------------------------------------------------------
# Guardas de payload / tenant_id
# ---------------------------------------------------------------------------


async def test_sin_tenant_id_lanza() -> None:
    with pytest.raises(ValueError, match="tenant_id"):
        await process_meeting_module.handle(
            JobEnvelope(job_id=uuid.uuid4(), tenant_id=None, type="process_meeting", payload={}),
            make_deps(),
        )


async def test_payload_sin_meeting_id_ni_file_id_lanza(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession()
    fake_repo = FakeRepo()
    s3 = _FakeS3ConGetYPut()
    deps = _preparar_deps(session=session, fake_repo=fake_repo, s3=s3, monkeypatch=monkeypatch)

    with pytest.raises(ValueError, match="meeting_id.*file_id|file_id.*meeting_id"):
        await process_meeting_module.handle(_env(tenant_id=uuid.uuid4(), payload={}), deps)


async def test_payload_con_file_id_sin_user_id_lanza(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession()
    fake_repo = FakeRepo()
    s3 = _FakeS3ConGetYPut()
    deps = _preparar_deps(session=session, fake_repo=fake_repo, s3=s3, monkeypatch=monkeypatch)

    with pytest.raises(ValueError, match="user_id"):
        await process_meeting_module.handle(
            _env(tenant_id=uuid.uuid4(), payload={"file_id": str(uuid.uuid4())}), deps
        )


async def test_meeting_id_inexistente_se_ignora_sin_lanzar(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession(respuestas=[[]])  # SELECT meetings -> nada
    fake_repo = FakeRepo()
    s3 = _FakeS3ConGetYPut()
    deps = _preparar_deps(session=session, fake_repo=fake_repo, s3=s3, monkeypatch=monkeypatch)

    await process_meeting_module.handle(
        _env(tenant_id=uuid.uuid4(), payload={"meeting_id": str(uuid.uuid4())}), deps
    )
    # No hubo INSERT/UPDATE adicional: solo el SELECT inicial.
    assert len(session.llamadas) == 1


async def test_meeting_id_ya_en_estado_terminal_se_ignora(monkeypatch: pytest.MonkeyPatch) -> None:
    meeting_id = uuid.uuid4()
    fila_terminal = {"id": meeting_id, "status": "done", "source_file_id": uuid.uuid4()}
    session = _FakeSession(respuestas=[[fila_terminal]])
    fake_repo = FakeRepo()
    s3 = _FakeS3ConGetYPut()
    deps = _preparar_deps(session=session, fake_repo=fake_repo, s3=s3, monkeypatch=monkeypatch)

    await process_meeting_module.handle(
        _env(tenant_id=uuid.uuid4(), payload={"meeting_id": str(meeting_id)}), deps
    )
    assert len(session.llamadas) == 1  # nunca llegó a marcar 'running'


# ---------------------------------------------------------------------------
# Camino feliz — creación desde {file_id, titulo, user_id} (payload de la tool)
# ---------------------------------------------------------------------------


async def test_camino_feliz_crea_reunion_desde_file_id_stub_stt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    file_id = uuid.uuid4()
    meeting_id = uuid.uuid4()

    fila_insertada = _meeting_row(
        meeting_id=meeting_id,
        tenant_id=tenant_id,
        user_id=user_id,
        file_id=file_id,
        titulo="Standup",
    )
    session = _FakeSession(
        respuestas=[
            [fila_insertada],  # INSERT meetings
            [],  # SELECT connector_accounts (voice_stt) -> sin cuenta -> stub
            [],  # INSERT files (transcript)
            [],  # UPDATE meetings (guardar_resultado)
        ]
    )
    fake_repo = FakeRepo()
    fake_repo.files[file_id] = {
        "id": file_id,
        "tenant_id": tenant_id,
        "s3_key": f"tenants/{tenant_id}/files/{file_id}/reunion.mp3",
        "filename": "reunion.mp3",
        "mime": "audio/mpeg",
    }
    fake_repo.tenants[tenant_id] = {"id": tenant_id, "plan_key": "hosted_pro"}
    s3 = _FakeS3ConGetYPut()
    s3.preload("edecan-files-test", fake_repo.files[file_id]["s3_key"], b"contenido-de-audio")

    deps = _preparar_deps(session=session, fake_repo=fake_repo, s3=s3, monkeypatch=monkeypatch)
    await _usar_llm_de_plataforma_como_router_del_tenant(deps, monkeypatch)
    deps.llm_router.provider.reply = _minutas_json()

    _mockear_extraccion_de_audio(monkeypatch, _wav_bytes(seconds=1.5))

    env = _env(
        tenant_id=tenant_id,
        payload={"file_id": str(file_id), "titulo": "Standup", "user_id": str(user_id)},
    )
    await process_meeting_module.handle(env, deps)

    # 1) INSERT meetings inicial
    sql_insert, params_insert = session.llamadas[0]
    assert "INSERT INTO meetings" in sql_insert
    assert params_insert["tenant_id"] == tenant_id
    assert params_insert["user_id"] == user_id
    assert params_insert["source_file_id"] == file_id
    assert params_insert["titulo"] == "Standup"

    # 2) transcripción subida a S3 y como fila files
    assert len(s3.put_calls) == 1
    subida = s3.put_calls[0]
    assert subida["ContentType"] == "text/plain"
    assert subida["Key"].endswith("-transcript.txt")
    assert b"transcripcion de prueba" in subida["Body"].lower() or b"prueba" in subida["Body"]

    sql_files, params_files = session.llamadas[2]
    assert "INSERT INTO files" in sql_files
    assert params_files["mime"] == "text/plain"

    # 3) UPDATE final: status='done', minutas guardadas, aviso de stub STT presente
    sql_update, params_update = session.llamadas[3]
    assert "UPDATE meetings" in sql_update
    assert "status = 'done'" in sql_update
    assert params_update["resumen"] == "Se discutió el roadmap del trimestre."
    minutos_guardados = json.loads(params_update["minutos"])
    assert minutos_guardados["decisiones"] == ["Lanzar en marzo"]
    assert minutos_guardados["acciones"] == [{"tarea": "Escribir el plan", "responsable": "Ana"}]
    assert minutos_guardados["temas"] == ["roadmap"]
    assert params_update["error"] is not None
    assert "STT" in params_update["error"] or "Deepgram" in params_update["error"]
    # `meetings.duracion_segundos` es INTEGER en Postgres real (no
    # NUMERIC/float) — verificado empíricamente contra Postgres real
    # (BARRIDO D, WP-V7-04): sin redondeo explícito, asyncpg trunca en
    # silencio hacia cero al bindear un float contra esa columna (nunca
    # revienta, así que `FakeSession` no lo detectaba). `_guardar_resultado`
    # ahora redondea antes de armar el parámetro — `round(1.5) == 2`
    # (Python: redondeo al par más cercano), no `1` (lo que habría truncado
    # el driver en silencio) ni `1.5` (lo que el código enviaba antes del
    # fix, pese a que la columna real nunca lo habría aceptado tal cual).
    assert params_update["duracion_segundos"] == 2
    assert isinstance(params_update["duracion_segundos"], int)

    # 4) uso de LLM registrado
    assert len(fake_repo.usage_events) == 1
    assert fake_repo.usage_events[0]["kind"] == "llm_tokens"

    # 5) el prompt de minutas SÍ llegó al proveedor LLM con el transcript
    assert len(deps.llm_router.provider.requests) == 1


# ---------------------------------------------------------------------------
# Camino feliz — payload {meeting_id} (creado por el router HTTP)
# ---------------------------------------------------------------------------


async def test_camino_feliz_con_meeting_id_ya_existente(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    file_id = uuid.uuid4()
    meeting_id = uuid.uuid4()

    fila_existente = _meeting_row(
        meeting_id=meeting_id,
        tenant_id=tenant_id,
        user_id=user_id,
        file_id=file_id,
        titulo="Retro",
        # Vocabulario real de `status` (`ARCHITECTURE.md` §15.b / CHECK de
        # `0008_v6_expansion.py`): `pending|running|done|error` — `'queued'`
        # NO es un valor válido contra Postgres real (BARRIDO D, WP-V7-04).
        # `POST /v1/reuniones` (`reuniones.py::crear_reunion`) siempre inserta
        # con `status='pending'`, así que esta fixture simula exactamente esa
        # fila tal como este handler la encuentra en el camino
        # `{"meeting_id"}`.
        status="pending",
    )
    session = _FakeSession(
        respuestas=[
            [fila_existente],  # SELECT meetings
            [],  # UPDATE meetings -> running
            [],  # SELECT connector_accounts -> stub
            [],  # INSERT files (transcript)
            [],  # UPDATE meetings (guardar_resultado)
        ]
    )
    fake_repo = FakeRepo()
    fake_repo.files[file_id] = {
        "id": file_id,
        "tenant_id": tenant_id,
        "s3_key": f"tenants/{tenant_id}/files/{file_id}/reunion.mp4",
        "filename": "reunion.mp4",
        "mime": "video/mp4",
    }
    fake_repo.tenants[tenant_id] = {"id": tenant_id, "plan_key": "hosted_pro"}
    s3 = _FakeS3ConGetYPut()
    s3.preload("edecan-files-test", fake_repo.files[file_id]["s3_key"], b"contenido-de-video")

    deps = _preparar_deps(session=session, fake_repo=fake_repo, s3=s3, monkeypatch=monkeypatch)
    await _usar_llm_de_plataforma_como_router_del_tenant(deps, monkeypatch)
    deps.llm_router.provider.reply = _minutas_json(resumen="Retro del sprint.")

    _mockear_extraccion_de_audio(monkeypatch, _wav_bytes(seconds=0.5))

    env = _env(tenant_id=tenant_id, payload={"meeting_id": str(meeting_id)})
    await process_meeting_module.handle(env, deps)

    sql_running, params_running = session.llamadas[1]
    assert "UPDATE meetings" in sql_running
    assert "status = 'running'" in sql_running
    assert params_running["id"] == meeting_id

    sql_update, params_update = session.llamadas[4]
    assert params_update["resumen"] == "Retro del sprint."


# ---------------------------------------------------------------------------
# STT real (Deepgram, respx-mockeado) — sin aviso de stub
# ---------------------------------------------------------------------------


@respx.mock
async def test_camino_feliz_con_deepgram_real_no_deja_aviso_de_stub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    file_id = uuid.uuid4()
    meeting_id = uuid.uuid4()
    cuenta_id = uuid.uuid4()

    fila_insertada = _meeting_row(
        meeting_id=meeting_id, tenant_id=tenant_id, user_id=user_id, file_id=file_id, titulo="1:1"
    )
    session = _FakeSession(
        respuestas=[
            [fila_insertada],  # INSERT meetings
            [{"id": cuenta_id}],  # SELECT connector_accounts -> hay cuenta
            [],  # INSERT files (transcript)
            [],  # UPDATE meetings (guardar_resultado)
        ]
    )
    fake_repo = FakeRepo()
    fake_repo.files[file_id] = {
        "id": file_id,
        "tenant_id": tenant_id,
        "s3_key": f"tenants/{tenant_id}/files/{file_id}/reunion.wav",
        "filename": "reunion.wav",
        "mime": "audio/wav",
    }
    fake_repo.tenants[tenant_id] = {"id": tenant_id, "plan_key": "hosted_pro"}
    s3 = _FakeS3ConGetYPut()
    s3.preload("edecan-files-test", fake_repo.files[file_id]["s3_key"], b"contenido-de-audio")

    fake_vault = FakeVault()
    config_deepgram = json.dumps({"provider": "deepgram", "api_key": "clave-real"})
    fake_vault.store[(tenant_id, cuenta_id)] = FakeTokenBundle(access_token=config_deepgram)

    deps = _preparar_deps(session=session, fake_repo=fake_repo, s3=s3, monkeypatch=monkeypatch)
    deps.vault = lambda _session: fake_vault
    await _usar_llm_de_plataforma_como_router_del_tenant(deps, monkeypatch)
    deps.llm_router.provider.reply = _minutas_json(resumen="Charla 1:1 real.")

    _mockear_extraccion_de_audio(monkeypatch, _wav_bytes(seconds=0.5))

    alternativa = {"transcript": "Hola, esta es la transcripción real.", "confidence": 0.9}
    ruta = respx.post("https://api.deepgram.com/v1/listen").mock(
        return_value=httpx.Response(
            200,
            json={"results": {"channels": [{"alternatives": [alternativa]}]}},
        )
    )

    env = _env(
        tenant_id=tenant_id,
        payload={"file_id": str(file_id), "titulo": "1:1", "user_id": str(user_id)},
    )
    await process_meeting_module.handle(env, deps)

    assert ruta.called
    assert ruta.calls.last.request.headers["Authorization"] == "Token clave-real"

    sql_update, params_update = session.llamadas[3]
    assert params_update["error"] is None  # sin aviso de stub: STT real conectado

    prompt_enviado = deps.llm_router.provider.requests[0].messages[0].content
    assert "Hola, esta es la transcripción real." in prompt_enviado


@respx.mock
async def test_error_real_de_deepgram_marca_error_y_relanza(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El tenant SÍ conectó una credencial real: un fallo real hablando con
    Deepgram (ej. clave inválida) debe marcar `status='error'` y propagarse
    (reintento/DLQ del despachador) — nunca degradar en silencio a stub, ni
    quedarse en `status='running'` para siempre."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    file_id = uuid.uuid4()
    meeting_id = uuid.uuid4()
    cuenta_id = uuid.uuid4()

    fila_insertada = _meeting_row(
        meeting_id=meeting_id, tenant_id=tenant_id, user_id=user_id, file_id=file_id, titulo="X"
    )
    session = _FakeSession(
        respuestas=[
            [fila_insertada],  # INSERT meetings
            [{"id": cuenta_id}],  # SELECT connector_accounts -> hay cuenta
            [],  # UPDATE meetings (marcar_error)
        ]
    )
    fake_repo = FakeRepo()
    fake_repo.files[file_id] = {
        "id": file_id,
        "tenant_id": tenant_id,
        "s3_key": f"tenants/{tenant_id}/files/{file_id}/r.wav",
        "filename": "r.wav",
        "mime": "audio/wav",
    }
    s3 = _FakeS3ConGetYPut()
    s3.preload("edecan-files-test", fake_repo.files[file_id]["s3_key"], b"audio")

    fake_vault = FakeVault()
    config_deepgram = json.dumps({"provider": "deepgram", "api_key": "clave-invalida"})
    fake_vault.store[(tenant_id, cuenta_id)] = FakeTokenBundle(access_token=config_deepgram)

    deps = _preparar_deps(session=session, fake_repo=fake_repo, s3=s3, monkeypatch=monkeypatch)
    deps.vault = lambda _session: fake_vault
    _mockear_extraccion_de_audio(monkeypatch, _wav_bytes(seconds=0.3))

    respx.post("https://api.deepgram.com/v1/listen").mock(
        return_value=httpx.Response(401, json={"err_msg": "invalid API key"})
    )

    env = _env(
        tenant_id=tenant_id,
        payload={"file_id": str(file_id), "titulo": "X", "user_id": str(user_id)},
    )
    with pytest.raises(httpx.HTTPStatusError):
        await process_meeting_module.handle(env, deps)

    sql_error, params_error = session.llamadas[-1]
    assert "UPDATE meetings" in sql_error
    assert "status = 'error'" in sql_error
    assert params_error["id"] == meeting_id
    assert "401" in params_error["error"]
    assert s3.put_calls == []  # nunca llegó a subir ningún transcript


# ---------------------------------------------------------------------------
# Errores: LLM no conectado / STT o extracción fallan → status='error' + re-raise
# ---------------------------------------------------------------------------


async def test_llm_no_conectado_marca_error_y_relanza(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    file_id = uuid.uuid4()
    meeting_id = uuid.uuid4()

    session = _FakeSession(
        respuestas=[
            [
                _meeting_row(
                    meeting_id=meeting_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    file_id=file_id,
                    titulo="X",
                )
            ],
            [],  # connector_accounts -> stub
            [],  # INSERT files (transcript)
            [],  # UPDATE meetings (marcar_error)
        ]
    )
    fake_repo = FakeRepo()
    fake_repo.files[file_id] = {
        "id": file_id,
        "tenant_id": tenant_id,
        "s3_key": f"tenants/{tenant_id}/files/{file_id}/r.mp3",
        "filename": "r.mp3",
        "mime": "audio/mpeg",
    }
    s3 = _FakeS3ConGetYPut()
    s3.preload("edecan-files-test", fake_repo.files[file_id]["s3_key"], b"audio")

    deps = _preparar_deps(session=session, fake_repo=fake_repo, s3=s3, monkeypatch=monkeypatch)

    async def _sin_llm(tenant_id: Any) -> Any:
        raise TenantLLMNotConnectedError(tenant_id)

    monkeypatch.setattr(deps, "llm_router_for", _sin_llm)
    _mockear_extraccion_de_audio(monkeypatch, _wav_bytes(seconds=0.2))

    env = _env(
        tenant_id=tenant_id,
        payload={"file_id": str(file_id), "titulo": "X", "user_id": str(user_id)},
    )
    with pytest.raises(TenantLLMNotConnectedError):
        await process_meeting_module.handle(env, deps)

    sql_error, params_error = session.llamadas[-1]
    assert "UPDATE meetings" in sql_error
    assert "status = 'error'" in sql_error
    assert params_error["id"] == meeting_id
    assert "proveedor de LLM propio" in params_error["error"]


async def test_error_extrayendo_audio_marca_error_y_relanza(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    file_id = uuid.uuid4()
    meeting_id = uuid.uuid4()

    session = _FakeSession(
        respuestas=[
            [
                _meeting_row(
                    meeting_id=meeting_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    file_id=file_id,
                    titulo="X",
                )
            ],
            [],  # UPDATE meetings (marcar_error)
        ]
    )
    fake_repo = FakeRepo()
    fake_repo.files[file_id] = {
        "id": file_id,
        "tenant_id": tenant_id,
        "s3_key": f"tenants/{tenant_id}/files/{file_id}/r.mp3",
        "filename": "r.mp3",
        "mime": "audio/mpeg",
    }
    s3 = _FakeS3ConGetYPut()
    s3.preload("edecan-files-test", fake_repo.files[file_id]["s3_key"], b"audio")

    deps = _preparar_deps(session=session, fake_repo=fake_repo, s3=s3, monkeypatch=monkeypatch)

    async def _fake_extraer_falla(data: bytes, mime: str | None = None, **kwargs: Any) -> bytes:
        raise audio_module.AudioExtractionError("ffmpeg no encontrado, instálalo primero.")

    monkeypatch.setattr(audio_module, "extraer_audio_wav", _fake_extraer_falla)

    env = _env(
        tenant_id=tenant_id,
        payload={"file_id": str(file_id), "titulo": "X", "user_id": str(user_id)},
    )
    with pytest.raises(ValueError, match="ffmpeg no encontrado"):
        await process_meeting_module.handle(env, deps)

    sql_error, params_error = session.llamadas[-1]
    assert "status = 'error'" in sql_error
    assert "ffmpeg no encontrado" in params_error["error"]


async def test_archivo_de_origen_no_encontrado_marca_error_y_relanza(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    file_id = uuid.uuid4()
    meeting_id = uuid.uuid4()

    session = _FakeSession(
        respuestas=[
            [
                _meeting_row(
                    meeting_id=meeting_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    file_id=file_id,
                    titulo="X",
                )
            ],
            [],  # UPDATE meetings (marcar_error)
        ]
    )
    fake_repo = FakeRepo()  # sin la fila files -> get_file devuelve None
    s3 = _FakeS3ConGetYPut()

    deps = _preparar_deps(session=session, fake_repo=fake_repo, s3=s3, monkeypatch=monkeypatch)

    env = _env(
        tenant_id=tenant_id,
        payload={"file_id": str(file_id), "titulo": "X", "user_id": str(user_id)},
    )
    with pytest.raises(ValueError, match="ya no existe"):
        await process_meeting_module.handle(env, deps)

    sql_error, params_error = session.llamadas[-1]
    assert "status = 'error'" in sql_error


# ---------------------------------------------------------------------------
# Anti-fuga: sin credencial STT del tenant, jamás el centinela de plataforma
# ---------------------------------------------------------------------------


async def test_sin_credencial_stt_nunca_llama_deepgram_real(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    file_id = uuid.uuid4()
    meeting_id = uuid.uuid4()

    session = _FakeSession(
        respuestas=[
            [
                _meeting_row(
                    meeting_id=meeting_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    file_id=file_id,
                    titulo="X",
                )
            ],
            [],  # connector_accounts -> sin cuenta -> stub
            [],  # INSERT files (transcript)
            [],  # UPDATE meetings (guardar_resultado)
        ]
    )
    fake_repo = FakeRepo()
    fake_repo.files[file_id] = {
        "id": file_id,
        "tenant_id": tenant_id,
        "s3_key": f"tenants/{tenant_id}/files/{file_id}/r.mp3",
        "filename": "r.mp3",
        "mime": "audio/mpeg",
    }
    fake_repo.tenants[tenant_id] = {"id": tenant_id, "plan_key": "hosted_pro"}
    s3 = _FakeS3ConGetYPut()
    s3.preload("edecan-files-test", fake_repo.files[file_id]["s3_key"], b"audio")

    deps = _preparar_deps(session=session, fake_repo=fake_repo, s3=s3, monkeypatch=monkeypatch)
    await _usar_llm_de_plataforma_como_router_del_tenant(deps, monkeypatch)
    deps.llm_router.provider.reply = _minutas_json()
    _mockear_extraccion_de_audio(monkeypatch, _wav_bytes(seconds=0.3))

    # Ningún respx.mock activo: si el código intentara hablar con Deepgram de
    # verdad, httpx fallaría por no tener mocks (o intentaría red real) — el
    # simple hecho de que este test pase sin `@respx.mock` demuestra que
    # nunca se llamó a la API real de Deepgram.
    env = _env(
        tenant_id=tenant_id,
        payload={"file_id": str(file_id), "titulo": "X", "user_id": str(user_id)},
    )
    await process_meeting_module.handle(env, deps)

    subida = s3.put_calls[0]
    assert _SENTINEL.encode() not in subida["Body"]


# ---------------------------------------------------------------------------
# BARRIDO C (WP-V7-04): un fallo en `add_usage_event` (best-effort, telemetría
# de facturación) NUNCA debe revertir ni reetiquetar como 'error' un
# resultado YA exitoso (`_guardar_resultado`, status='done') — regla de oro
# de `HOTFIXES_PENDIENTES.md` puntos 8/9, ver el docstring del módulo bajo
# "usage_events".
# ---------------------------------------------------------------------------


async def test_fallo_en_usage_event_no_revierte_el_resultado_ya_guardado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    file_id = uuid.uuid4()
    meeting_id = uuid.uuid4()

    fila_insertada = _meeting_row(
        meeting_id=meeting_id, tenant_id=tenant_id, user_id=user_id, file_id=file_id, titulo="X"
    )
    session = _FakeSession(
        respuestas=[
            [fila_insertada],  # INSERT meetings
            [],  # SELECT connector_accounts -> sin cuenta -> stub
            [],  # INSERT files (transcript)
            [],  # UPDATE meetings (guardar_resultado)
        ]
    )
    fake_repo = FakeRepo()
    fake_repo.files[file_id] = {
        "id": file_id,
        "tenant_id": tenant_id,
        "s3_key": f"tenants/{tenant_id}/files/{file_id}/r.mp3",
        "filename": "r.mp3",
        "mime": "audio/mpeg",
    }
    fake_repo.tenants[tenant_id] = {"id": tenant_id, "plan_key": "hosted_pro"}
    s3 = _FakeS3ConGetYPut()
    s3.preload("edecan-files-test", fake_repo.files[file_id]["s3_key"], b"audio")

    async def _add_usage_event_que_falla(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("usage_events: constraint violado (simulado)")

    monkeypatch.setattr(fake_repo, "add_usage_event", _add_usage_event_que_falla)

    deps = _preparar_deps(session=session, fake_repo=fake_repo, s3=s3, monkeypatch=monkeypatch)
    await _usar_llm_de_plataforma_como_router_del_tenant(deps, monkeypatch)
    deps.llm_router.provider.reply = _minutas_json()
    _mockear_extraccion_de_audio(monkeypatch, _wav_bytes(seconds=0.4))

    env = _env(
        tenant_id=tenant_id,
        payload={"file_id": str(file_id), "titulo": "X", "user_id": str(user_id)},
    )

    # ANTES del fix: `add_usage_event` corría en la MISMA transacción que
    # `_guardar_resultado` y su excepción se propagaba hasta el `except`
    # exterior, que marcaba `status='error'` (una SEGUNDA UPDATE) y
    # relanzaba — este `handle()` habría reventado con `RuntimeError` acá.
    # DESPUÉS del fix: la excepción se atrapa y se registra (`logger.exception`),
    # `handle()` retorna normalmente sin relanzar.
    await process_meeting_module.handle(env, deps)

    # El UPDATE final (`_guardar_resultado`) sigue siendo el ÚNICO/ÚLTIMO
    # UPDATE de `status` sobre `meetings` en toda la sesión — NUNCA un
    # segundo UPDATE con status='error' pisándolo encima.
    updates_de_status = [
        (sql, params)
        for sql, params in session.llamadas
        if "UPDATE meetings" in sql and "status" in sql
    ]
    assert len(updates_de_status) == 1
    sql_update, params_update = updates_de_status[0]
    assert "status = 'done'" in sql_update
    assert params_update["resumen"] == "Se discutió el roadmap del trimestre."

    # La telemetría de uso se intentó (y falló antes de poder registrar
    # nada) — no quedó silenciosamente completada, solo silenciosamente
    # descartada sin perjudicar el resultado ya entregado.
    assert fake_repo.usage_events == []


# ---------------------------------------------------------------------------
# Integración: contra Postgres real (BARRIDO D, WP-V7-04)
# ---------------------------------------------------------------------------
#
# El esquema de `meetings` ya se había corregido en v6, pero nunca contra
# Postgres real — solo contra `_FakeSession` (mockea filas con el MISMO
# esquema que asume el código, invisible a un `CheckViolationError`/
# `UndefinedColumnError`/error de tipo real). Gateada por `DATABASE_URL`, se
# salta sola si no está configurada/alcanzable — mismo patrón autocontenido
# que `apps/api/tests/test_repo_sql_integration.py` y la sección análoga de
# `apps/api/tests/test_reuniones_router.py` (cada módulo de test de
# integración duplica su propia lógica de skip/fixture a propósito).
#
# `session_factory=get_session` (el mismo `edecan_db.session.get_session`
# que usa `edecan_worker.deps.build_deps` en producción, ver su docstring) —
# STT/LLM siguen monkeypatcheados (cero red externa real, `ffmpeg` tampoco se
# ejecuta), pero CADA escritura SQL de este módulo (INSERT/UPDATE sobre
# `meetings`, SELECT sobre `connector_accounts`, INSERT sobre `files`) corre
# de verdad contra Postgres.


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL")


async def _es_alcanzable(url: str) -> bool:
    import asyncpg

    dsn = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    try:
        conn = await asyncpg.connect(dsn, timeout=2)
    except Exception:
        return False
    await conn.close()
    return True


def _skip_reason_integracion() -> str | None:
    url = _database_url()
    if not url:
        return "DATABASE_URL no está configurada"
    try:
        alcanzable = asyncio.run(_es_alcanzable(url))
    except Exception as exc:  # pragma: no cover - solo diagnóstico del skip
        return f"No se pudo probar la conexión a DATABASE_URL: {exc}"
    if not alcanzable:
        return f"Postgres no está alcanzable en DATABASE_URL={url!r}"
    return None


_SKIP_REASON_INTEGRACION = _skip_reason_integracion()


async def _aplicar_migraciones(database_url: str) -> None:
    """Aplica hasta `head` (idempotente: no-op si ya están aplicadas)."""
    from pathlib import Path

    from alembic.command import upgrade
    from alembic.config import Config

    db_dir = Path(__file__).resolve().parents[3] / "packages" / "db"
    cfg = Config(str(db_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(db_dir / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    await asyncio.to_thread(upgrade, cfg, "head")


@pytest.fixture
async def db_real(monkeypatch: pytest.MonkeyPatch):
    """Prepara `edecan_db.settings`/`engine` para apuntar a `DATABASE_URL`,
    aplica migraciones, y limpia sus cachés `lru_cache` al terminar — mismo
    patrón que `apps/api/tests/test_repo_sql_integration.py::db`."""
    from edecan_db import engine as engine_module
    from edecan_db import settings as settings_module

    database_url = _database_url()
    assert database_url  # el skipif del módulo ya garantizó que hay una

    monkeypatch.setenv("DATABASE_URL", database_url)
    settings_module.get_settings.cache_clear()
    engine_module.get_engine.cache_clear()

    await _aplicar_migraciones(database_url)

    yield

    settings_module.get_settings.cache_clear()
    engine_module.get_engine.cache_clear()


async def _seed_tenant_y_archivo(
    sufijo: str, *, contenido: bytes
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, str]:
    """Tenant + user + una fila `files` (el audio de origen) directo con el
    ORM de `edecan_db` contra Postgres real — `meetings`/`files` llevan FK
    reales a `tenants`/`users`, así que hacen falta filas padre de verdad."""
    from edecan_db.models import File, Tenant, User
    from edecan_db.session import get_session

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    file_id = uuid.uuid4()
    s3_key = f"tenants/{tenant_id}/files/{file_id}/reunion-{sufijo}.wav"
    async with get_session(None) as session:
        session.add(
            Tenant(
                id=tenant_id,
                name=f"v7 meet {sufijo}",
                slug=f"v7-meet-{sufijo}",
                plan_key="hosted_pro",
            )
        )
        session.add(User(id=user_id, email=f"v7-meet-{sufijo}@example.com", password_hash="x" * 20))
        await session.flush()
        session.add(
            File(
                id=file_id,
                tenant_id=tenant_id,
                user_id=user_id,
                s3_key=s3_key,
                filename=f"reunion-{sufijo}.wav",
                mime="audio/wav",
                size_bytes=len(contenido),
                status="ready",
            )
        )
    return tenant_id, user_id, file_id, s3_key


async def _cleanup_tenant(tenant_id: uuid.UUID, user_id: uuid.UUID) -> None:
    from edecan_db.models import Tenant, User
    from edecan_db.session import get_session
    from sqlalchemy import delete

    async with get_session(None) as session:
        # El tenant primero: `ON DELETE CASCADE` se lleva `files`/`meetings`.
        await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
    async with get_session(None) as session:
        await session.execute(delete(User).where(User.id == user_id))


@pytest.mark.integration
@pytest.mark.skipif(_SKIP_REASON_INTEGRACION is not None, reason=_SKIP_REASON_INTEGRACION or "")
async def test_integracion_camino_feliz_contra_postgres_real(
    db_real, monkeypatch: pytest.MonkeyPatch
) -> None:
    from edecan_db.session import get_session
    from sqlalchemy import text as sql_text_real

    sufijo = uuid.uuid4().hex[:8]
    contenido_audio = b"contenido-de-audio-de-prueba-v7"
    tenant_id, user_id, file_id, s3_key = await _seed_tenant_y_archivo(
        sufijo, contenido=contenido_audio
    )
    try:
        s3 = _FakeS3ConGetYPut()
        s3.preload("edecan-files-test", s3_key, contenido_audio)

        # `session_factory=get_session` real (ver docstring de esta sección) —
        # S3/LLM siguen fakes, `SqlRepo` sigue siendo el REAL
        # `edecan_worker.repo.SqlRepo` (no se monkeypatchea, a diferencia del
        # resto de este archivo) porque acá SÍ queremos que hable con
        # Postgres real.
        deps = make_deps(session_factory=get_session, s3=s3)
        await _usar_llm_de_plataforma_como_router_del_tenant(deps, monkeypatch)
        deps.llm_router.provider.reply = _minutas_json(resumen="Resumen contra Postgres real.")
        _mockear_extraccion_de_audio(monkeypatch, _wav_bytes(seconds=1.5))

        env = _env(
            tenant_id=tenant_id,
            payload={"file_id": str(file_id), "titulo": "Integración v7", "user_id": str(user_id)},
        )
        await process_meeting_module.handle(env, deps)

        async with get_session(None) as session:
            fila = (
                (
                    await session.execute(
                        sql_text_real("SELECT * FROM meetings WHERE tenant_id = :t"),
                        {"t": tenant_id},
                    )
                )
                .mappings()
                .first()
            )
        assert fila is not None
        assert fila["status"] == "done"
        assert fila["source_file_id"] == file_id
        assert fila["resumen"] == "Resumen contra Postgres real."
        assert fila["minutos"]["decisiones"] == ["Lanzar en marzo"]
        assert fila["minutos"]["acciones"] == [{"tarea": "Escribir el plan", "responsable": "Ana"}]
        # `duracion_segundos` es INTEGER real (BARRIDO D): el fix redondea
        # ANTES de escribir — `round(1.5) == 2`, nunca `1.5` (lo que el
        # código enviaba antes del fix) ni `1` (lo que el driver habría
        # truncado en silencio sin el fix).
        assert fila["duracion_segundos"] == 2
        assert isinstance(fila["duracion_segundos"], int)
        assert fila["transcript_file_id"] is not None
        # Aviso de stub STT: no se sembró ninguna `connector_accounts` real.
        assert fila["error"] is not None
        assert "STT" in fila["error"] or "Deepgram" in fila["error"]

        # La transcripción quedó como una fila `files` real, separada del
        # archivo de origen.
        async with get_session(None) as session:
            transcript_row = (
                (
                    await session.execute(
                        sql_text_real("SELECT * FROM files WHERE id = :id"),
                        {"id": fila["transcript_file_id"]},
                    )
                )
                .mappings()
                .first()
            )
        assert transcript_row is not None
        assert transcript_row["mime"] == "text/plain"
        assert transcript_row["status"] == "ready"

        # `usage_events` en su propia transacción (BARRIDO C, ver docstring
        # del módulo) — sigue registrándose en el camino feliz.
        async with get_session(None) as session:
            uso = (
                (
                    await session.execute(
                        sql_text_real(
                            "SELECT COUNT(*) AS n FROM usage_events "
                            "WHERE tenant_id = :t AND kind = 'llm_tokens'"
                        ),
                        {"t": tenant_id},
                    )
                )
                .mappings()
                .first()
            )
        assert uso["n"] == 1
    finally:
        await _cleanup_tenant(tenant_id, user_id)
