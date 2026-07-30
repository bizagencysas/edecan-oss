"""`enqueue` — encola en SQS vía `aioboto3` (ARCHITECTURE.md §10.7, §10.11).

Sin red real: se sustituye `edecan_core.queue.aioboto3` por un doble local
que registra las llamadas (ARCHITECTURE.md §0.4 "nunca llamadas de red
reales en tests").
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import edecan_core.queue as queue_module
import pytest
from edecan_core.queue import enqueue


class _FakeSqsClient:
    def __init__(self, sent: list[dict[str, Any]]) -> None:
        self._sent = sent

    async def __aenter__(self) -> _FakeSqsClient:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def send_message(self, **kwargs: Any) -> dict[str, str]:
        self._sent.append(kwargs)
        return {"MessageId": "fake-message-id"}


class _FakeSession:
    def __init__(self, sent: list[dict[str, Any]], client_calls: list[dict[str, Any]]) -> None:
        self._sent = sent
        self._client_calls = client_calls

    def client(self, service_name: str, **kwargs: Any) -> _FakeSqsClient:
        self._client_calls.append({"service_name": service_name, **kwargs})
        return _FakeSqsClient(self._sent)


@pytest.fixture
def fake_aioboto3(monkeypatch: pytest.MonkeyPatch) -> tuple[list[dict], list[dict]]:
    sent: list[dict[str, Any]] = []
    client_calls: list[dict[str, Any]] = []
    fake_module = SimpleNamespace(Session=lambda: _FakeSession(sent, client_calls))
    monkeypatch.setattr(queue_module, "aioboto3", fake_module)
    return sent, client_calls


def _settings(**overrides: Any) -> SimpleNamespace:
    base = dict(
        SQS_QUEUE_URL="http://localhost:4566/000000000000/edecan-jobs",
        AWS_ENDPOINT_URL="http://localhost:4566",
        AWS_REGION="us-east-1",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_enqueue_manda_mensaje_con_el_job_envelope(fake_aioboto3):
    sent, client_calls = fake_aioboto3
    tenant_id = uuid4()

    job_id = await enqueue(_settings(), "ingest_file", {"file_id": "f1"}, tenant_id)

    assert isinstance(job_id, UUID)
    assert len(sent) == 1
    assert sent[0]["QueueUrl"] == "http://localhost:4566/000000000000/edecan-jobs"
    body = json.loads(sent[0]["MessageBody"])
    assert body["job_id"] == str(job_id)
    assert body["tenant_id"] == str(tenant_id)
    assert body["type"] == "ingest_file"
    assert body["payload"] == {"file_id": "f1"}
    assert body["attempt"] == 0


@pytest.mark.asyncio
async def test_enqueue_usa_aws_endpoint_url_de_settings(fake_aioboto3):
    _, client_calls = fake_aioboto3
    await enqueue(_settings(AWS_ENDPOINT_URL="http://localhost:4566"), "sync_connector", {}, None)
    assert client_calls == [
        {
            "service_name": "sqs",
            "region_name": "us-east-1",
            "endpoint_url": "http://localhost:4566",
        }
    ]


@pytest.mark.asyncio
async def test_enqueue_tenant_id_none_para_jobs_globales(fake_aioboto3):
    sent, _ = fake_aioboto3
    await enqueue(_settings(), "send_reminder_scan", {}, None)
    body = json.loads(sent[0]["MessageBody"])
    assert body["tenant_id"] is None


@pytest.mark.asyncio
async def test_enqueue_job_type_invalido_lanza_value_error(fake_aioboto3):
    with pytest.raises(ValueError):
        await enqueue(_settings(), "borrar_todo", {}, None)


@pytest.mark.asyncio
async def test_enqueue_sin_sqs_queue_url_lanza_runtime_error(fake_aioboto3):
    with pytest.raises(RuntimeError):
        await enqueue(_settings(SQS_QUEUE_URL=None), "ingest_file", {}, None)
