"""Tests de la rama de imágenes de `ingest_file` (ver docstring del módulo
bajo prueba: `apps/worker/edecan_worker/handlers/ingest_file.py`).

No importa `edecan_docanalysis` (paquete hermano, ARCHITECTURE.md §10.1):
fakea un proveedor LLM local con `.name = "anthropic"` y `.complete(...)`
para simular "hay visión configurada", igual que `fakes.FakeLLMRouter`/
`FakeProvider` ya hacen para el resto de la suite (que, al no tener
`.name`, sirven tal cual para probar la rama "SIN proveedor de visión").
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import edecan_worker.handlers.ingest_file as ingest_file_module
from edecan_schemas import JobEnvelope
from fakes import FakeRepo, make_deps


async def _use_platform_router_as_tenant_router(deps, monkeypatch) -> None:
    """`Deps.llm_router_for` (bring-your-own, WP-V3-02) ahora resuelve un
    `LLMRouter` REAL a partir de la config guardada del tenant — simular acá
    "el tenant conectó algo" con un vault falso arriesgaría una llamada de
    red real al describir la imagen, que es justo lo que estos tests NO
    deben hacer (usan `deps.llm_router`, en memoria, para poder aserts sin
    red). Este archivo no prueba la resolución bring-your-own en sí — eso ya
    lo cubre `apps/worker/tests/test_llm_por_tenant.py` — así que alcanza
    con monkeypatchear `llm_router_for` para que devuelva directo
    `deps.llm_router` (el fake que cada test ya arma), como si fuera el
    resultado ya resuelto de la config propia del tenant."""

    async def _fake_llm_router_for(tenant_id):
        return deps.llm_router

    monkeypatch.setattr(deps, "llm_router_for", _fake_llm_router_for)


def _archivo_imagen(
    *,
    tenant_id: uuid.UUID,
    mime: str = "image/png",
    filename: str = "foto.png",
    s3_key: str = "tenants/t1/files/f1/foto.png",
) -> dict[str, Any]:
    return {
        "id": None,  # se sobreescribe por el llamador
        "tenant_id": tenant_id,
        "s3_key": s3_key,
        "filename": filename,
        "mime": mime,
        "size_bytes": 0,
        "status": "uploaded",
    }


@dataclass
class _FakeVisionUsage:
    input_tokens: int = 5
    output_tokens: int = 15


@dataclass
class _FakeVisionResponse:
    text: str
    usage: _FakeVisionUsage = field(default_factory=_FakeVisionUsage)
    tool_calls: list[Any] = field(default_factory=list)
    stop_reason: str = "end"


@dataclass
class _FakeVisionProvider:
    """`ctx.llm`-equivalente CON soporte de visión (`.name == "anthropic"`)."""

    name: str = "anthropic"
    texto: str = "Una foto de un gato sobre un sofá."
    requests: list[Any] = field(default_factory=list)

    async def complete(self, req: Any) -> _FakeVisionResponse:
        self.requests.append(req)
        return _FakeVisionResponse(text=self.texto)


@dataclass
class _FakeVisionRouter:
    """Imita `edecan_llm.router.LLMRouter.resolve` con un proveedor fijo."""

    provider: Any
    modelo: str = "modelo-vision-fake"
    resolved: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def resolve(self, alias: str, tenant_flags: dict[str, Any]) -> tuple[Any, str]:
        self.resolved.append((alias, tenant_flags))
        return self.provider, self.modelo


def _env_para(file_id: uuid.UUID, tenant_id: uuid.UUID) -> JobEnvelope:
    return JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="ingest_file",
        payload={"file_id": str(file_id)},
    )


async def test_imagen_con_proveedor_vision_genera_chunk_seq0_y_status_ready(monkeypatch) -> None:
    fake_repo = FakeRepo()
    monkeypatch.setattr(ingest_file_module, "SqlRepo", lambda session: fake_repo)

    tenant_id = uuid.uuid4()
    file_id = uuid.uuid4()
    s3_key = "tenants/t1/files/f1/gato.png"
    file_row = _archivo_imagen(tenant_id=tenant_id, s3_key=s3_key)
    file_row["id"] = file_id
    fake_repo.files[file_id] = file_row

    proveedor = _FakeVisionProvider(texto="Una foto de un gato sobre un sofá.")
    deps = make_deps(llm_router=_FakeVisionRouter(provider=proveedor))
    await _use_platform_router_as_tenant_router(deps, monkeypatch)
    contenido_png = b"\x89PNG\r\n\x1a\nfalso-pero-basta-para-el-test"
    deps.s3.put(deps.settings.S3_BUCKET, s3_key, contenido_png)

    await ingest_file_module.handle(_env_para(file_id, tenant_id), deps)

    assert fake_repo.files[file_id]["status"] == "ready"
    assert len(fake_repo.file_chunks) == 1
    chunk = fake_repo.file_chunks[0]
    assert chunk["seq"] == 0
    assert chunk["text"] == "Una foto de un gato sobre un sofá."
    assert chunk["tenant_id"] == tenant_id
    assert chunk["file_id"] == file_id
    assert len(chunk["embedding"]) == deps.embedder.dim
    assert fake_repo.usage_events == []

    # Se le mandó a la vez la imagen en base64 y una pregunta/instrucción de texto.
    assert len(proveedor.requests) == 1
    bloques = proveedor.requests[0].messages[0].content
    assert bloques[0]["type"] == "image"
    assert bloques[0]["source"]["media_type"] == "image/png"
    assert bloques[1]["type"] == "text"

    # Alias "rapido" (job automático, no la tool interactiva) y sin tenant_flags.
    assert deps.llm_router.resolved == [("rapido", {})]


async def test_imagen_sin_proveedor_vision_marca_status_error_igual_que_antes(monkeypatch) -> None:
    fake_repo = FakeRepo()
    monkeypatch.setattr(ingest_file_module, "SqlRepo", lambda session: fake_repo)

    tenant_id = uuid.uuid4()
    file_id = uuid.uuid4()
    s3_key = "tenants/t1/files/f1/foto.jpg"
    file_row = _archivo_imagen(
        tenant_id=tenant_id, mime="image/jpeg", filename="foto.jpg", s3_key=s3_key
    )
    file_row["id"] = file_id
    fake_repo.files[file_id] = file_row

    # `make_deps()` sin overrides usa `FakeLLMRouter`/`FakeProvider` de
    # `fakes.py`, que no tienen `.name` — mismo comportamiento que "proveedor
    # sin soporte de visión" (`getattr(provider, "name", "") != "anthropic"`).
    deps = make_deps()
    await _use_platform_router_as_tenant_router(deps, monkeypatch)
    deps.s3.put(deps.settings.S3_BUCKET, s3_key, b"\xff\xd8\xff\xe0falso-jpeg")

    await ingest_file_module.handle(_env_para(file_id, tenant_id), deps)

    assert fake_repo.files[file_id]["status"] == "error"
    assert fake_repo.file_chunks == []
    assert fake_repo.usage_events == []


async def test_imagen_demasiado_grande_marca_error_sin_llamar_al_llm(monkeypatch) -> None:
    fake_repo = FakeRepo()
    monkeypatch.setattr(ingest_file_module, "SqlRepo", lambda session: fake_repo)
    monkeypatch.setattr(ingest_file_module, "_MAX_IMAGEN_BYTES", 10)  # límite bajo para el test

    tenant_id = uuid.uuid4()
    file_id = uuid.uuid4()
    s3_key = "tenants/t1/files/f1/grande.png"
    file_row = _archivo_imagen(tenant_id=tenant_id, s3_key=s3_key)
    file_row["id"] = file_id
    fake_repo.files[file_id] = file_row

    proveedor = _FakeVisionProvider()
    deps = make_deps(llm_router=_FakeVisionRouter(provider=proveedor))
    await _use_platform_router_as_tenant_router(deps, monkeypatch)
    deps.s3.put(deps.settings.S3_BUCKET, s3_key, b"0123456789ABCDEF")  # 16 bytes > límite de 10

    await ingest_file_module.handle(_env_para(file_id, tenant_id), deps)

    assert fake_repo.files[file_id]["status"] == "error"
    assert fake_repo.file_chunks == []
    assert proveedor.requests == []  # nunca se llegó a llamar al LLM


async def test_imagen_con_mime_generico_resuelve_por_extension(monkeypatch) -> None:
    fake_repo = FakeRepo()
    monkeypatch.setattr(ingest_file_module, "SqlRepo", lambda session: fake_repo)

    tenant_id = uuid.uuid4()
    file_id = uuid.uuid4()
    s3_key = "tenants/t1/files/f1/captura.webp"
    file_row = _archivo_imagen(
        tenant_id=tenant_id,
        mime="application/octet-stream",
        filename="captura.webp",
        s3_key=s3_key,
    )
    file_row["id"] = file_id
    fake_repo.files[file_id] = file_row

    proveedor = _FakeVisionProvider(texto="Una captura de pantalla.")
    deps = make_deps(llm_router=_FakeVisionRouter(provider=proveedor))
    await _use_platform_router_as_tenant_router(deps, monkeypatch)
    deps.s3.put(deps.settings.S3_BUCKET, s3_key, b"RIFF....WEBPfalso")

    await ingest_file_module.handle(_env_para(file_id, tenant_id), deps)

    assert fake_repo.files[file_id]["status"] == "ready"
    bloques = proveedor.requests[0].messages[0].content
    assert bloques[0]["source"]["media_type"] == "image/webp"


async def test_imagen_con_respuesta_vacia_del_llm_usa_texto_de_respaldo(monkeypatch) -> None:
    fake_repo = FakeRepo()
    monkeypatch.setattr(ingest_file_module, "SqlRepo", lambda session: fake_repo)

    tenant_id = uuid.uuid4()
    file_id = uuid.uuid4()
    s3_key = "tenants/t1/files/f1/vacio.png"
    file_row = _archivo_imagen(tenant_id=tenant_id, s3_key=s3_key)
    file_row["id"] = file_id
    fake_repo.files[file_id] = file_row

    proveedor = _FakeVisionProvider(texto="   ")
    deps = make_deps(llm_router=_FakeVisionRouter(provider=proveedor))
    await _use_platform_router_as_tenant_router(deps, monkeypatch)
    deps.s3.put(deps.settings.S3_BUCKET, s3_key, b"\x89PNGfalso")

    await ingest_file_module.handle(_env_para(file_id, tenant_id), deps)

    assert fake_repo.files[file_id]["status"] == "ready"
    assert fake_repo.file_chunks[0]["text"] == "Imagen sin descripción disponible."


async def test_txt_no_toma_la_rama_de_imagenes(monkeypatch) -> None:
    """No rompe el comportamiento existente: un .txt sigue sin pasar por
    `_ingest_image` (`_resolver_mime_imagen` devuelve `None` para texto)."""
    fake_repo = FakeRepo()
    monkeypatch.setattr(ingest_file_module, "SqlRepo", lambda session: fake_repo)

    tenant_id = uuid.uuid4()
    file_id = uuid.uuid4()
    s3_key = "tenants/t1/files/f1/nota.txt"
    file_row = {
        "id": file_id,
        "tenant_id": tenant_id,
        "s3_key": s3_key,
        "filename": "nota.txt",
        "mime": "text/plain",
        "size_bytes": 0,
        "status": "uploaded",
    }
    fake_repo.files[file_id] = file_row

    proveedor = _FakeVisionProvider()
    deps = make_deps(llm_router=_FakeVisionRouter(provider=proveedor))
    await _use_platform_router_as_tenant_router(deps, monkeypatch)
    deps.s3.put(deps.settings.S3_BUCKET, s3_key, b"hola mundo")

    await ingest_file_module.handle(_env_para(file_id, tenant_id), deps)

    assert fake_repo.files[file_id]["status"] == "ready"
    assert fake_repo.file_chunks[0]["text"] == "hola mundo"
    assert proveedor.requests == []  # el LLM de visión nunca se llamó
