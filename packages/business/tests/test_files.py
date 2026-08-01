"""Tests de `edecan_business._files.subir_pdf`: sube a S3 e inserta la fila en `files`
(`ARCHITECTURE.md` §10.3, §10.14). `aioboto3.Session` se sustituye con `monkeypatch` — mismo
patrón que `apps/api/tests/test_files.py`/`edecan_creative/tests/test_files.py` — para
correr offline y sin tocar AWS real (regla dura del proyecto).
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import edecan_business._files as files_module


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


async def test_subir_pdf_sube_a_s3_y_registra_el_insert(monkeypatch, make_session):
    s3_calls: list[dict] = []
    _patch_s3(monkeypatch, calls=s3_calls)
    session = make_session()
    tenant_id, user_id = uuid4(), uuid4()

    file_id, filename = await files_module.subir_pdf(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        settings=SimpleNamespace(S3_BUCKET="mi-bucket", AWS_REGION="us-west-2"),
        data=b"contenido-del-pdf",
        filename="F-2026-0001.pdf",
    )

    assert isinstance(file_id, UUID)
    assert filename == "F-2026-0001.pdf"

    assert len(s3_calls) == 1
    assert s3_calls[0]["Bucket"] == "mi-bucket"
    assert s3_calls[0]["Key"] == f"tenants/{tenant_id}/files/{file_id}/F-2026-0001.pdf"
    assert s3_calls[0]["Body"] == b"contenido-del-pdf"
    assert s3_calls[0]["ContentType"] == "application/pdf"

    assert len(session.llamadas) == 1
    sql, params = session.llamadas[0]
    assert "INSERT INTO files" in sql
    assert "'ready'" in sql  # nace listo: no pasa por ingest_file
    assert params["id"] == file_id
    assert params["tenant_id"] == str(tenant_id)
    assert params["user_id"] == str(user_id)
    assert params["s3_key"] == s3_calls[0]["Key"]
    assert params["filename"] == "F-2026-0001.pdf"
    assert params["mime"] == "application/pdf"
    assert params["size_bytes"] == len(b"contenido-del-pdf")


async def test_subir_pdf_usa_defaults_si_settings_no_trae_s3_bucket_ni_region(
    monkeypatch, make_session
):
    s3_calls: list[dict] = []
    _patch_s3(monkeypatch, calls=s3_calls)

    await files_module.subir_pdf(
        make_session(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        settings=SimpleNamespace(),  # sin ningún campo AWS
        data=b"x",
        filename="a.pdf",
    )

    assert s3_calls[0]["Bucket"] == files_module.DEFAULT_S3_BUCKET


async def test_subir_pdf_dos_llamadas_generan_ids_distintos(monkeypatch, make_session):
    s3_calls: list[dict] = []
    _patch_s3(monkeypatch, calls=s3_calls)

    id_1, _ = await files_module.subir_pdf(
        make_session(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        settings=None,
        data=b"a",
        filename="a.pdf",
    )
    id_2, _ = await files_module.subir_pdf(
        make_session(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        settings=None,
        data=b"b",
        filename="b.pdf",
    )

    assert id_1 != id_2
