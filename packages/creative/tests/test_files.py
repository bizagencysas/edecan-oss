"""Tests de `edecan_creative._files.subir_archivo`: sube a S3 e inserta la fila
en `files` (`ARCHITECTURE.md` §10.3, §10.14). `aioboto3.Session` se sustituye
con `monkeypatch` — mismo patrón que `apps/api/tests/test_files.py` — para
correr offline y sin tocar AWS real (regla dura del proyecto).

`get_session` también se sustituye, por la misma razón de correr offline: el
INSERT dejó de vivir en `ctx.session` y ahora abre su PROPIA transacción con
`edecan_db.session.get_session(tenant_id)` (arregla la carrera en la que la
fila no era visible a mitad de un turno en streaming — ver el docstring de
`subir_archivo`). Sin sustituirlo, estos tests intentarían conectarse a
Postgres de verdad y fallan con `InvalidPasswordError`. El fake devuelve la
misma `FakeSession` de `ctx.session`, así que los asserts sobre
`session.llamadas` siguen verificando exactamente el mismo INSERT.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import edecan_creative._files as files_module


class _FakeS3Client:
    def __init__(self, calls: list[dict]) -> None:
        self._calls = calls

    async def __aenter__(self) -> _FakeS3Client:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def put_object(self, **kwargs: object) -> None:
        self._calls.append(kwargs)


class _FakeAioboto3Session:
    def __init__(self, calls: list[dict]) -> None:
        self._calls = calls

    def client(self, service_name: str, **kwargs: object) -> _FakeS3Client:
        assert service_name == "s3"
        return _FakeS3Client(self._calls)


def _patch_s3(monkeypatch, *, calls: list[dict]) -> None:
    monkeypatch.setattr(files_module.aioboto3, "Session", lambda: _FakeAioboto3Session(calls))


class _FakeSessionCtx:
    """Sustituye el `async with get_session(...)` de `subir_archivo` sin tocar Postgres."""

    def __init__(self, session: object, aperturas: list[object]) -> None:
        self._session = session
        self._aperturas = aperturas

    async def __aenter__(self) -> object:
        self._aperturas.append(self._session)
        return self._session

    async def __aexit__(self, *exc_info: object) -> None:
        return None


def _patch_session(monkeypatch, session: object) -> list[object]:
    """Devuelve la lista de aperturas, para poder afirmar que se abrió UNA transacción propia."""
    aperturas: list[object] = []
    monkeypatch.setattr(
        files_module, "get_session", lambda tenant_id: _FakeSessionCtx(session, aperturas)
    )
    return aperturas


async def test_subir_archivo_sube_a_s3_y_registra_el_insert(monkeypatch, make_ctx, make_session):
    s3_calls: list[dict] = []
    _patch_s3(monkeypatch, calls=s3_calls)
    session = make_session()
    aperturas = _patch_session(monkeypatch, session)
    ctx = make_ctx(
        session=session,
        settings=SimpleNamespace(S3_BUCKET="mi-bucket", AWS_REGION="us-west-2"),
    )

    file_id, filename = await files_module.subir_archivo(
        ctx, data=b"contenido-del-pdf", filename="reporte.pdf", mime="application/pdf"
    )

    assert isinstance(file_id, UUID)
    assert filename == "reporte.pdf"

    assert len(s3_calls) == 1
    assert s3_calls[0]["Bucket"] == "mi-bucket"
    assert s3_calls[0]["Key"] == f"tenants/{ctx.tenant_id}/files/{file_id}/reporte.pdf"
    assert s3_calls[0]["Body"] == b"contenido-del-pdf"
    assert s3_calls[0]["ContentType"] == "application/pdf"

    # El INSERT vive en su PROPIA transacción, no en `ctx.session`: se abrió exactamente una.
    assert len(aperturas) == 1
    assert aperturas[0] is session

    assert len(session.llamadas) == 1
    sql, params = session.llamadas[0]
    assert "INSERT INTO files" in sql
    assert "'ready'" in sql  # nace listo: no pasa por ingest_file
    assert params["id"] == file_id
    assert params["tenant_id"] == str(ctx.tenant_id)
    assert params["user_id"] == str(ctx.user_id)
    assert params["s3_key"] == s3_calls[0]["Key"]
    assert params["filename"] == "reporte.pdf"
    assert params["mime"] == "application/pdf"
    assert params["size_bytes"] == len(b"contenido-del-pdf")


async def test_subir_archivo_usa_defaults_si_settings_no_trae_s3_bucket_ni_region(
    monkeypatch, make_ctx, make_session
):
    s3_calls: list[dict] = []
    _patch_s3(monkeypatch, calls=s3_calls)
    session = make_session()
    _patch_session(monkeypatch, session)
    ctx = make_ctx(session=session, settings=SimpleNamespace())  # sin ningún campo AWS

    await files_module.subir_archivo(ctx, data=b"x", filename="a.png", mime="image/png")

    assert s3_calls[0]["Bucket"] == files_module.DEFAULT_S3_BUCKET


async def test_subir_archivo_dos_llamadas_generan_ids_distintos(
    monkeypatch, make_ctx, make_session
):
    s3_calls: list[dict] = []
    _patch_s3(monkeypatch, calls=s3_calls)
    session = make_session()
    _patch_session(monkeypatch, session)
    ctx = make_ctx(session=session)

    id_1, _ = await files_module.subir_archivo(ctx, data=b"a", filename="a.png", mime="image/png")
    id_2, _ = await files_module.subir_archivo(ctx, data=b"b", filename="b.png", mime="image/png")

    assert id_1 != id_2
