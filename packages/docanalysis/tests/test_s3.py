"""Tests de `edecan_docanalysis._s3` — fakea `aioboto3` (nunca red real)."""

from __future__ import annotations

import sys
import types
from typing import Any
from uuid import uuid4

import pytest
from edecan_docanalysis import _s3


class _FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def read(self) -> bytes:
        return self._data


class _FakeS3Client:
    def __init__(self, almacen: dict[tuple[str, str], bytes]) -> None:
        self._almacen = almacen
        self.puestos: list[tuple[str, str, bytes, str]] = []

    async def __aenter__(self) -> _FakeS3Client:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        return {"Body": _FakeBody(self._almacen[(Bucket, Key)])}

    async def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> None:
        self._almacen[(Bucket, Key)] = Body
        self.puestos.append((Bucket, Key, Body, ContentType))


class _FakeBotoSession:
    def __init__(self, almacen: dict[tuple[str, str], bytes]) -> None:
        self._almacen = almacen
        self.clientes: list[_FakeS3Client] = []
        self.kwargs_pedidos: list[dict[str, Any]] = []

    def client(self, servicio: str, **kwargs: Any) -> _FakeS3Client:
        assert servicio == "s3"
        self.kwargs_pedidos.append(kwargs)
        cliente = _FakeS3Client(self._almacen)
        self.clientes.append(cliente)
        return cliente


@pytest.fixture
def fake_aioboto3(monkeypatch):
    """Registra un `aioboto3` falso en `sys.modules` (mismo criterio que
    `apps/worker/tests/fakes.py::install_fake_edecan_core_queue`): `_s3.py`
    hace `import aioboto3` perezoso DENTRO de cada función, así que basta con
    pre-registrar el módulo falso antes de invocar `_s3.descargar_archivo`/
    `subir_resultado` — el `import` de Python lo encuentra en `sys.modules` y
    nunca abre un socket real."""
    almacen: dict[tuple[str, str], bytes] = {}
    sesiones: list[_FakeBotoSession] = []

    def _nueva_sesion() -> _FakeBotoSession:
        sesion = _FakeBotoSession(almacen)
        sesiones.append(sesion)
        return sesion

    fake_modulo = types.ModuleType("aioboto3")
    fake_modulo.Session = _nueva_sesion  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "aioboto3", fake_modulo)
    return types.SimpleNamespace(almacen=almacen, sesiones=sesiones)


async def test_descargar_archivo_lee_fila_y_baja_bytes(make_ctx, make_session, fake_aioboto3):
    tenant_id = uuid4()
    file_id = uuid4()
    s3_key = f"tenants/{tenant_id}/files/{file_id}/foo.csv"
    session = make_session(
        [
            [
                {
                    "id": file_id,
                    "s3_key": s3_key,
                    "filename": "foo.csv",
                    "mime": "text/csv",
                    "size_bytes": 4,
                }
            ]
        ]
    )
    ctx = make_ctx(session=session, tenant_id=tenant_id)
    fake_aioboto3.almacen[("edecan-files-test", s3_key)] = b"a,b\n"

    resultado = await _s3.descargar_archivo(ctx, file_id)

    assert resultado is not None
    assert resultado.contenido == b"a,b\n"
    assert resultado.filename == "foo.csv"
    assert resultado.mime == "text/csv"
    assert resultado.size_bytes == 4

    sql, params = session.llamadas[0]
    assert "SELECT" in sql and "files" in sql
    assert params == {"tenant_id": str(tenant_id), "id": str(file_id)}


async def test_descargar_archivo_devuelve_none_si_no_existe(make_ctx, make_session, fake_aioboto3):
    session = make_session([[]])
    ctx = make_ctx(session=session)

    resultado = await _s3.descargar_archivo(ctx, uuid4())

    assert resultado is None
    # Sin fila en `files` no se llega a tocar S3.
    assert fake_aioboto3.sesiones == []


async def test_subir_resultado_sube_a_s3_e_inserta_fila_files(
    make_ctx, make_session, fake_aioboto3
):
    session = make_session([])
    tenant_id = uuid4()
    ctx = make_ctx(session=session, tenant_id=tenant_id)

    file_id = await _s3.subir_resultado(
        ctx, filename="grafico.svg", mime="image/svg+xml", contenido=b"<svg/>"
    )

    clave = ("edecan-files-test", f"tenants/{tenant_id}/files/{file_id}/grafico.svg")
    assert fake_aioboto3.almacen[clave] == b"<svg/>"

    sql, params = session.llamadas[0]
    assert "INSERT INTO files" in sql
    assert "'ready'" in sql
    assert params["tenant_id"] == str(tenant_id)
    assert params["user_id"] == str(ctx.user_id)
    assert params["filename"] == "grafico.svg"
    assert params["mime"] == "image/svg+xml"
    assert params["size_bytes"] == len(b"<svg/>")
    assert params["s3_key"] == clave[1]


async def test_subir_resultado_usa_endpoint_url_si_esta_configurado(
    make_ctx, make_session, fake_aioboto3, fake_settings
):
    session = make_session([])
    settings = fake_settings(AWS_ENDPOINT_URL="http://localhost:4566")
    ctx = make_ctx(session=session, settings=settings)

    await _s3.subir_resultado(ctx, filename="x.svg", mime="image/svg+xml", contenido=b"<svg/>")

    kwargs = fake_aioboto3.sesiones[0].kwargs_pedidos[0]
    assert kwargs["endpoint_url"] == "http://localhost:4566"
    assert kwargs["region_name"] == "us-east-1"


async def test_bucket_cae_a_default_si_falta_en_settings(make_ctx, make_session, fake_aioboto3):
    session = make_session([])
    settings = types.SimpleNamespace()  # sin S3_BUCKET ni AWS_REGION ni AWS_ENDPOINT_URL
    ctx = make_ctx(session=session, settings=settings)

    file_id = await _s3.subir_resultado(
        ctx, filename="x.svg", mime="image/svg+xml", contenido=b"1"
    )

    clave = ("edecan-files", f"tenants/{ctx.tenant_id}/files/{file_id}/x.svg")
    assert fake_aioboto3.almacen[clave] == b"1"


# ---------------------------------------------------------------------------
# `descargar_archivo_de_tenant` — superficie pública (WP-V6-06, `docs/analista.md`
# "Pantalla Analista"): mismo contrato que `descargar_archivo`, pero sin exigir un
# `ToolContext` completo — solo `session`/`settings`/`tenant_id` explícitos, lo que ya
# tiene a mano un router HTTP (`apps/api/edecan_api/routers/analista.py`).
# ---------------------------------------------------------------------------


async def test_descargar_archivo_de_tenant_lee_fila_y_baja_bytes(
    make_session, fake_settings, fake_aioboto3
):
    tenant_id = uuid4()
    file_id = uuid4()
    s3_key = f"tenants/{tenant_id}/files/{file_id}/foo.csv"
    session = make_session(
        [
            [
                {
                    "id": file_id,
                    "s3_key": s3_key,
                    "filename": "foo.csv",
                    "mime": "text/csv",
                    "size_bytes": 4,
                }
            ]
        ]
    )
    settings = fake_settings()
    fake_aioboto3.almacen[("edecan-files-test", s3_key)] = b"a,b\n"

    resultado = await _s3.descargar_archivo_de_tenant(session, settings, tenant_id, file_id)

    assert resultado is not None
    assert resultado.contenido == b"a,b\n"
    assert resultado.filename == "foo.csv"
    assert resultado.mime == "text/csv"

    sql, params = session.llamadas[0]
    assert "SELECT" in sql and "files" in sql
    assert params == {"tenant_id": str(tenant_id), "id": str(file_id)}


async def test_descargar_archivo_de_tenant_devuelve_none_si_no_existe(
    make_session, fake_settings, fake_aioboto3
):
    session = make_session([[]])
    resultado = await _s3.descargar_archivo_de_tenant(
        session, fake_settings(), uuid4(), uuid4()
    )
    assert resultado is None
    assert fake_aioboto3.sesiones == []


async def test_descargar_archivo_de_tenant_no_filtra_ningun_otro_atributo_del_ctx(
    make_session, fake_settings, fake_aioboto3
):
    """El shim interno (`_CtxDescarga`) solo trae `tenant_id`/`session`/`settings` — esta
    prueba documenta que la función pública funciona sin `user_id`/`llm`/`vault`/`extras`,
    confirmando que `descargar_archivo`/`_get_file_row`/`_bucket`/`_client_kwargs` de verdad
    no los tocan (si algún día alguno de esos cuatro empezara a leerlos, este test explota con
    un `AttributeError` claro en vez de fallar en silencio)."""
    tenant_id = uuid4()
    file_id = uuid4()
    s3_key = f"tenants/{tenant_id}/files/{file_id}/x.csv"
    fila = {
        "id": file_id,
        "s3_key": s3_key,
        "filename": "x.csv",
        "mime": "text/csv",
        "size_bytes": 1,
    }
    session = make_session([[fila]])
    fake_aioboto3.almacen[("edecan-files-test", s3_key)] = b"1"

    resultado = await _s3.descargar_archivo_de_tenant(session, fake_settings(), tenant_id, file_id)
    assert resultado is not None
