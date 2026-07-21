"""`POST|GET /v1/files` (ARCHITECTURE.md §10.12, §10.14).

`upload_file` habla con S3 real (`aioboto3`) y encola un job con
`edecan_core.queue.enqueue` (que a su vez habla con SQS real). Ambos símbolos
están importados en el propio módulo del router
(`edecan_api.routers.files`), así que se sustituyen ahí con `monkeypatch` —
mismo patrón que `monkeypatch.setattr(conversations_module, "Agent", ...)` en
`test_conversations.py` — para que estos tests corran offline y sin tocar
AWS real (reglas duras del proyecto).
"""

from __future__ import annotations

import uuid

from conftest import auth_headers


class _FakeS3Client:
    def __init__(self, calls: list[dict]) -> None:
        self._calls = calls

    async def __aenter__(self) -> _FakeS3Client:
        return self

    async def __aexit__(self, *exc_info) -> None:
        return None

    async def put_object(self, **kwargs) -> None:
        body = kwargs.get("Body")
        if hasattr(body, "read"):
            kwargs["Body"] = body.read()
        self._calls.append(kwargs)

    def _object(self, key: str) -> dict:
        for call in self._calls:
            if call.get("Key") == key:
                return call
        raise RuntimeError("objeto no encontrado")

    async def head_object(self, **kwargs) -> dict:
        stored = self._object(kwargs["Key"])
        return {"ContentLength": len(stored["Body"])}

    async def get_object(self, **kwargs) -> dict:
        stored = self._object(kwargs["Key"])
        return {"Body": _FakeStreamingBody(stored["Body"])}


class _FakeStreamingBody:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._offset = 0

    async def read(self, size: int) -> bytes:
        chunk = self._data[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


class _FakeAioboto3Session:
    def __init__(self, calls: list[dict]) -> None:
        self._calls = calls

    def client(self, service_name: str, **kwargs):
        assert service_name == "s3"
        return _FakeS3Client(self._calls)


def _patch_s3_and_queue(
    monkeypatch, files_module, *, s3_calls: list[dict], enqueue_calls: list[dict]
):
    monkeypatch.setattr(
        files_module.aioboto3, "Session", lambda: _FakeAioboto3Session(s3_calls)
    )

    async def fake_enqueue(settings, job_type, payload, tenant_id):
        enqueue_calls.append({"job_type": job_type, "payload": payload, "tenant_id": tenant_id})
        return uuid.uuid4()

    monkeypatch.setattr(files_module, "enqueue", fake_enqueue)


async def test_upload_file_stores_in_s3_and_enqueues_ingest_job(
    client, fake_repo, monkeypatch
) -> None:
    import edecan_api.routers.files as files_module

    s3_calls: list[dict] = []
    enqueue_calls: list[dict] = []
    _patch_s3_and_queue(monkeypatch, files_module, s3_calls=s3_calls, enqueue_calls=enqueue_calls)

    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")

    response = await client.post(
        "/v1/files",
        files={"file": ("informe.pdf", b"contenido-del-pdf", "application/pdf")},
        headers=headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["filename"] == "informe.pdf"
    assert body["mime"] == "application/pdf"
    assert body["status"] == "uploaded"

    assert len(s3_calls) == 1
    assert s3_calls[0]["Bucket"] == "edecan-files"
    assert s3_calls[0]["Key"] == f"tenants/{tenant_id}/files/{body['id']}/informe.pdf"
    assert s3_calls[0]["Body"] == b"contenido-del-pdf"

    assert len(enqueue_calls) == 1
    assert enqueue_calls[0]["job_type"] == "ingest_file"
    assert enqueue_calls[0]["payload"]["file_id"] == body["id"]
    assert enqueue_calls[0]["tenant_id"] == tenant_id

    usage_kinds = [e["kind"] for e in fake_repo.usage_events]
    assert usage_kinds.count("storage_bytes") == 1


async def test_list_files_is_scoped_per_tenant(client, monkeypatch) -> None:
    import edecan_api.routers.files as files_module

    s3_calls: list[dict] = []
    enqueue_calls: list[dict] = []
    _patch_s3_and_queue(monkeypatch, files_module, s3_calls=s3_calls, enqueue_calls=enqueue_calls)

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    headers_a = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_a, plan_key="hosted_basic")
    headers_b = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_b, plan_key="hosted_basic")

    await client.post(
        "/v1/files",
        files={"file": ("solo-a.pdf", b"x", "application/pdf")},
        headers=headers_a,
    )

    listed_a = await client.get("/v1/files", headers=headers_a)
    listed_b = await client.get("/v1/files", headers=headers_b)

    assert len(listed_a.json()) == 1
    assert listed_b.json() == []


async def test_get_file_by_id(client, monkeypatch) -> None:
    import edecan_api.routers.files as files_module

    _patch_s3_and_queue(monkeypatch, files_module, s3_calls=[], enqueue_calls=[])
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")

    uploaded = await client.post(
        "/v1/files",
        files={"file": ("doc.pdf", b"contenido", "application/pdf")},
        headers=headers,
    )
    file_id = uploaded.json()["id"]

    response = await client.get(f"/v1/files/{file_id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["filename"] == "doc.pdf"


async def test_get_unknown_file_returns_404(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.get(f"/v1/files/{uuid.uuid4()}", headers=headers)
    assert response.status_code == 404


async def test_download_file_streams_private_object_with_safe_headers(client, monkeypatch) -> None:
    import edecan_api.routers.files as files_module

    s3_calls: list[dict] = []
    _patch_s3_and_queue(monkeypatch, files_module, s3_calls=s3_calls, enqueue_calls=[])
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    uploaded = await client.post(
        "/v1/files",
        files={"file": ("reporte final.pdf", b"PDF-REAL", "application/pdf")},
        headers=headers,
    )
    file_id = uploaded.json()["id"]

    response = await client.get(f"/v1/files/{file_id}/download", headers=headers)

    assert response.status_code == 200
    assert response.content == b"PDF-REAL"
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.headers["content-disposition"] == (
        "attachment; filename*=UTF-8''reporte%20final.pdf"
    )
    assert response.headers["content-length"] == str(len(b"PDF-REAL"))
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_download_file_never_crosses_tenants(client, monkeypatch) -> None:
    import edecan_api.routers.files as files_module

    s3_calls: list[dict] = []
    _patch_s3_and_queue(monkeypatch, files_module, s3_calls=s3_calls, enqueue_calls=[])
    tenant_a = uuid.uuid4()
    uploaded = await client.post(
        "/v1/files",
        files={"file": ("privado.txt", b"secreto", "text/plain")},
        headers=auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_a),
    )

    response = await client.get(
        f"/v1/files/{uploaded.json()['id']}/download",
        headers=auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4()),
    )

    assert response.status_code == 404


async def test_download_file_requires_authentication(client) -> None:
    response = await client.get(f"/v1/files/{uuid.uuid4()}/download")
    assert response.status_code == 401


async def test_upload_rejects_file_above_configured_hard_limit(
    client, monkeypatch, test_settings
) -> None:
    import edecan_api.routers.files as files_module

    test_settings.MAX_UPLOAD_BYTES = 4
    s3_calls: list[dict] = []
    enqueue_calls: list[dict] = []
    _patch_s3_and_queue(
        monkeypatch, files_module, s3_calls=s3_calls, enqueue_calls=enqueue_calls
    )
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())

    response = await client.post(
        "/v1/files",
        files={"file": ("grande.bin", b"12345", "application/octet-stream")},
        headers=headers,
    )

    assert response.status_code == 413
    assert s3_calls == []
    assert enqueue_calls == []


async def test_upload_sanitizes_path_like_filename(client, monkeypatch) -> None:
    import edecan_api.routers.files as files_module

    s3_calls: list[dict] = []
    _patch_s3_and_queue(monkeypatch, files_module, s3_calls=s3_calls, enqueue_calls=[])
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id)

    response = await client.post(
        "/v1/files",
        files={"file": ("../../secret.txt", b"safe", "text/plain")},
        headers=headers,
    )

    assert response.status_code == 201
    assert response.json()["filename"] == "secret.txt"
    assert s3_calls[0]["Key"].endswith("/secret.txt")
