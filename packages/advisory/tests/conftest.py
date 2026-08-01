"""Fixtures compartidas de `edecan_advisory` (ver `ARCHITECTURE.md` §10.1, §10.15).

Fakes deliberadamente ligeros, por duck typing: ningún test de este paquete
importa `edecan_db`/`edecan_llm` reales para construir sus dobles — `ctx` en
sí es un `SimpleNamespace` (no `edecan_core.ToolContext`), y `ctx.session`/
`ctx.llm` se completan con objetos locales que solo implementan lo que las
tools realmente usan. Mismo patrón que `packages/docanalysis/tests/conftest.py`
y `packages/toolkit/tests/conftest.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from edecan_advisory._texto import ArchivoDescargado


class FakeResult:
    """Imita lo mínimo de `sqlalchemy.engine.Result` que usa el paquete."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> FakeResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)


@dataclass
class FakeSession:
    """`ctx.session` falso: cada `execute()` consume la siguiente respuesta
    programada (una lista de filas-dict) y registra `(sql, params)` en `llamadas`."""

    respuestas: list[list[dict[str, Any]]] = field(default_factory=list)
    llamadas: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> FakeResult:
        self.llamadas.append((str(stmt), dict(params or {})))
        filas = self.respuestas.pop(0) if self.respuestas else []
        return FakeResult(filas)


@dataclass
class FakeCompletionResponse:
    text: str
    tool_calls: list[Any] = field(default_factory=list)
    usage: Any = None
    stop_reason: str = "end"


@dataclass
class FakeLLM:
    """`ctx.llm` falso: imita `edecan_llm.router.LLMRouter` (`resolve` +
    `complete(alias, tenant_flags, req)`), sin red ni Pydantic real. `texto`
    es la respuesta que devuelve CUALQUIER llamada a `complete` — cada test
    arma un `FakeLLM` fresco con el texto que necesita esa tool en particular
    (varias tools esperan un JSON con una forma distinta)."""

    texto: str = "respuesta de prueba"
    llamadas: list[tuple[str, dict[str, Any], Any]] = field(default_factory=list)

    def resolve(self, alias: str, tenant_flags: dict[str, Any]) -> tuple[Any, str]:
        return SimpleNamespace(name="anthropic"), "modelo-fake"

    async def complete(
        self, alias: str, tenant_flags: dict[str, Any], req: Any
    ) -> FakeCompletionResponse:
        self.llamadas.append((alias, tenant_flags, req))
        return FakeCompletionResponse(text=self.texto)


def _fake_settings(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "S3_BUCKET": "edecan-files-test",
        "AWS_REGION": "us-east-1",
        "AWS_ENDPOINT_URL": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def make_session():
    """Factory de `FakeSession`: `make_session([[{"id": "..."}], []])`."""

    def _make_session(respuestas: list[list[dict[str, Any]]] | None = None) -> FakeSession:
        return FakeSession(respuestas=list(respuestas or []))

    return _make_session


@pytest.fixture
def make_llm():
    """Factory de `FakeLLM`: `make_llm(texto="...")`."""

    def _make_llm(texto: str = "respuesta de prueba") -> FakeLLM:
        return FakeLLM(texto=texto)

    return _make_llm


@pytest.fixture
def fake_settings():
    """Factory de `ctx.settings` falso: `fake_settings(S3_BUCKET="otro")`."""

    return _fake_settings


@pytest.fixture
def make_ctx():
    """Factory de un `ToolContext` falso (`SimpleNamespace` duck-typed, ver arriba)."""

    def _make_ctx(
        *,
        session: Any = None,
        llm: Any = None,
        settings: Any = None,
        extras: dict[str, Any] | None = None,
        tenant_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            tenant_id=tenant_id or uuid4(),
            user_id=user_id or uuid4(),
            session=session if session is not None else FakeSession(),
            settings=settings if settings is not None else _fake_settings(),
            llm=llm if llm is not None else FakeLLM(),
            vault=None,
            extras=extras if extras is not None else {},
        )

    return _make_ctx


@pytest.fixture
def make_archivo():
    """Factory de `ArchivoDescargado` (`edecan_advisory._texto`)."""

    def _make_archivo(*, contenido: bytes, filename: str, mime: str) -> ArchivoDescargado:
        return ArchivoDescargado(
            contenido=contenido, filename=filename, mime=mime, size_bytes=len(contenido)
        )

    return _make_archivo


@pytest.fixture
def fake_texto(monkeypatch):
    """Fakea `edecan_advisory._texto.descargar_archivo`/`subir_resultado` (sin
    tocar `aioboto3` ni Postgres reales) — mismo criterio que `fake_s3` de
    `packages/docanalysis/tests/conftest.py`.

    `estado.archivos` es una COLA: cada `descargar_archivo` consume la
    siguiente entrada (`None` si no hay más → "archivo no encontrado").
    `comparar_contratos` descarga dos archivos por llamada, así que un test
    de esa tool debe programar dos entradas en orden (`file_id_a` primero).

    Uso típico:

        async def test_algo(make_ctx, fake_texto, make_archivo):
            archivo = make_archivo(contenido=b"...", filename="x.txt", mime="text/plain")
            fake_texto.archivos = [archivo]
            resultado = await MiTool().run(make_ctx(), {"file_id": str(uuid4())})
            assert fake_texto.subidas[0]["filename"] == "..."
    """
    from edecan_advisory import _texto as modulo_texto

    estado = SimpleNamespace(archivos=[], subidas=[], siguiente_file_id=None)

    async def _descargar(ctx: Any, file_id: UUID) -> ArchivoDescargado | None:
        if estado.archivos:
            return estado.archivos.pop(0)
        return None

    async def _subir(ctx: Any, *, filename: str, mime: str, contenido: bytes) -> UUID:
        file_id = estado.siguiente_file_id or uuid4()
        estado.subidas.append(
            {"file_id": file_id, "filename": filename, "mime": mime, "contenido": contenido}
        )
        return file_id

    monkeypatch.setattr(modulo_texto, "descargar_archivo", _descargar)
    monkeypatch.setattr(modulo_texto, "subir_resultado", _subir)
    return estado
