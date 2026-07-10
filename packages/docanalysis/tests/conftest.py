"""Fixtures compartidas de `edecan_docanalysis` (ver ARCHITECTURE.md §10.1, §10.15).

Mismo criterio que `packages/toolkit/tests/conftest.py`: fakes ligeros por
duck typing, `ctx` como `SimpleNamespace` (no `edecan_core.ToolContext`) —
ningún test de este paquete importa `edecan_db`/`edecan_llm` reales para
construir sus dobles, ni hace llamadas de red.

`fake_s3` fakea `edecan_docanalysis._s3.descargar_archivo`/`subir_resultado`
directo (en vez de fakear `aioboto3` + Postgres para cada test de cada tool):
las 5 tools acceden a esas dos funciones vía `from . import _s3`, que
referencia el MISMO objeto módulo en `sys.modules` — parchearlo una vez aquí
alcanza para cualquier tool bajo prueba. `tests/test_s3.py` es el único
archivo que sí fakea `aioboto3` de verdad, para probar `_s3.py` en sí mismo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from edecan_docanalysis._s3 import ArchivoDescargado


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
class FakeProvider:
    """`ctx.llm.resolve(...)` devuelve una instancia de esto — solo importa
    `.name`, que es lo único que leen las tools de este paquete (`vision.py`)."""

    name: str = "anthropic"


@dataclass
class FakeLLM:
    """`ctx.llm` falso: imita `edecan_llm.router.LLMRouter` (`resolve` +
    `complete(alias, tenant_flags, req)`), sin red ni Pydantic real."""

    texto: str = "respuesta de prueba"
    proveedor_nombre: str = "anthropic"
    resueltos: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    llamadas: list[tuple[str, dict[str, Any], Any]] = field(default_factory=list)

    def resolve(self, alias: str, tenant_flags: dict[str, Any]) -> tuple[FakeProvider, str]:
        self.resueltos.append((alias, tenant_flags))
        return FakeProvider(name=self.proveedor_nombre), "modelo-fake"

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
    """Factory de `FakeLLM`: `make_llm(texto="...", proveedor_nombre="openai_compat")`."""

    def _make_llm(
        texto: str = "respuesta de prueba", proveedor_nombre: str = "anthropic"
    ) -> FakeLLM:
        return FakeLLM(texto=texto, proveedor_nombre=proveedor_nombre)

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
    """Factory de `ArchivoDescargado` (`edecan_docanalysis._s3`) para `fake_s3.archivo`."""

    def _make_archivo(*, contenido: bytes, filename: str, mime: str) -> ArchivoDescargado:
        return ArchivoDescargado(
            contenido=contenido, filename=filename, mime=mime, size_bytes=len(contenido)
        )

    return _make_archivo


@pytest.fixture
def fake_s3(monkeypatch):
    """Fakea `edecan_docanalysis._s3.descargar_archivo`/`subir_resultado` (ver
    docstring del módulo). Uso típico:

        async def test_algo(make_ctx, fake_s3, make_archivo):
            fake_s3.archivo = make_archivo(contenido=b"...", filename="x.csv", mime="text/csv")
            resultado = await MiTool().run(make_ctx(), {"file_id": str(uuid4())})
            assert fake_s3.subidas[0]["filename"] == "..."
    """
    from edecan_docanalysis import _s3 as modulo_s3

    estado = SimpleNamespace(archivo=None, subidas=[], siguiente_file_id=None)

    async def _descargar(ctx: Any, file_id: UUID) -> ArchivoDescargado | None:
        return estado.archivo

    async def _subir(ctx: Any, *, filename: str, mime: str, contenido: bytes) -> UUID:
        file_id = estado.siguiente_file_id or uuid4()
        estado.subidas.append(
            {"file_id": file_id, "filename": filename, "mime": mime, "contenido": contenido}
        )
        return file_id

    monkeypatch.setattr(modulo_s3, "descargar_archivo", _descargar)
    monkeypatch.setattr(modulo_s3, "subir_resultado", _subir)
    return estado
