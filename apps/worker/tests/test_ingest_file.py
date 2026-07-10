"""`ingest de un .txt produce chunks correctos` (job `ingest_file`)."""

from __future__ import annotations

import uuid

import edecan_worker.handlers.ingest_file as ingest_file_module
from edecan_schemas import JobEnvelope
from edecan_worker.handlers.ingest_file import chunk_text
from fakes import FakeRepo, make_deps


def test_chunk_text_respeta_tamano_y_solapamiento() -> None:
    # Secuencia no homogénea para que la igualdad posicional sea una
    # verificación real del solapamiento (no un match trivial tipo "x" in "x").
    texto = "".join(str(i % 10) for i in range(3000))
    trozos = chunk_text(texto, size=1200, overlap=200)
    # paso = 1200 - 200 = 1000 -> arranques en 0, 1000, 2000 sobre 3000 chars
    assert len(trozos) == 3
    assert all(len(t) <= 1200 for t in trozos)
    assert trozos[0] == texto[0:1200]
    assert trozos[1] == texto[1000:2200]
    assert trozos[2] == texto[2000:3000]
    # los últimos 200 caracteres de un trozo son los primeros 200 del siguiente
    assert trozos[0][-200:] == trozos[1][:200]
    assert trozos[1][-200:] == trozos[2][:200]


def test_chunk_text_texto_vacio_no_produce_trozos() -> None:
    assert chunk_text("   \n  ") == []


def test_chunk_text_texto_corto_produce_un_solo_trozo() -> None:
    assert chunk_text("hola mundo") == ["hola mundo"]


def _archivo_de_prueba(*, tenant_id: uuid.UUID, mime: str, filename: str, s3_key: str) -> dict:
    return {
        "id": None,  # se sobreescribe por el llamador
        "tenant_id": tenant_id,
        "s3_key": s3_key,
        "filename": filename,
        "mime": mime,
        "size_bytes": 0,
        "status": "uploaded",
    }


async def test_ingest_txt_produce_chunks_correctos(monkeypatch) -> None:
    fake_repo = FakeRepo()
    monkeypatch.setattr(ingest_file_module, "SqlRepo", lambda session: fake_repo)

    tenant_id = uuid.uuid4()
    file_id = uuid.uuid4()
    s3_key = "tenants/t1/files/f1/nota.txt"
    file_row = _archivo_de_prueba(
        tenant_id=tenant_id, mime="text/plain", filename="nota.txt", s3_key=s3_key
    )
    file_row["id"] = file_id
    fake_repo.files[file_id] = file_row

    deps = make_deps()
    parrafo = "Hola mundo, este es un párrafo de prueba para trocear. " * 40
    contenido = parrafo.strip().encode("utf-8")
    assert len(contenido) > 1200  # asegura más de un chunk
    deps.s3.put(deps.settings.S3_BUCKET, s3_key, contenido)

    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="ingest_file",
        payload={"file_id": str(file_id)},
    )
    await ingest_file_module.handle(env, deps)

    esperado = chunk_text(contenido.decode("utf-8"))
    assert len(esperado) > 1
    assert [c["text"] for c in fake_repo.file_chunks] == esperado
    assert [c["seq"] for c in fake_repo.file_chunks] == list(range(len(esperado)))
    assert all(c["tenant_id"] == tenant_id for c in fake_repo.file_chunks)
    assert all(c["file_id"] == file_id for c in fake_repo.file_chunks)
    assert all(len(c["embedding"]) == deps.embedder.dim for c in fake_repo.file_chunks)

    assert fake_repo.files[file_id]["status"] == "ready"
    # El storage ya se contabilizó una vez en la API al subir el archivo
    # (edecan_api/routers/files.py, justo tras el `s3.put_object`); el worker
    # NO debe volver a registrar `storage_bytes` o duplicaría el consumo
    # medido contra `limits.storage_mb` (ver comentario en ingest_file.py).
    assert fake_repo.usage_events == []

    # el embedder se llamó en lotes de a lo sumo 32 textos
    assert all(len(batch) <= 32 for batch in deps.embedder.calls)


async def test_ingest_mime_no_soportado_marca_status_error(monkeypatch) -> None:
    fake_repo = FakeRepo()
    monkeypatch.setattr(ingest_file_module, "SqlRepo", lambda session: fake_repo)

    tenant_id = uuid.uuid4()
    file_id = uuid.uuid4()
    s3_key = "tenants/t1/files/f2/audio.mp3"
    file_row = _archivo_de_prueba(
        tenant_id=tenant_id, mime="audio/mpeg", filename="audio.mp3", s3_key=s3_key
    )
    file_row["id"] = file_id
    fake_repo.files[file_id] = file_row

    deps = make_deps()
    deps.s3.put(deps.settings.S3_BUCKET, s3_key, b"\x00\x01\x02binario")

    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="ingest_file",
        payload={"file_id": str(file_id)},
    )
    await ingest_file_module.handle(env, deps)

    assert fake_repo.files[file_id]["status"] == "error"
    assert fake_repo.file_chunks == []
    assert fake_repo.usage_events == []


async def test_ingest_archivo_inexistente_no_falla_ni_escribe_nada(monkeypatch) -> None:
    fake_repo = FakeRepo()
    monkeypatch.setattr(ingest_file_module, "SqlRepo", lambda session: fake_repo)

    deps = make_deps()
    tenant_id = uuid.uuid4()
    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="ingest_file",
        payload={"file_id": str(uuid.uuid4())},
    )
    await ingest_file_module.handle(env, deps)  # no debe lanzar

    assert fake_repo.file_chunks == []
    assert fake_repo.usage_events == []
