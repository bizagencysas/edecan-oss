from __future__ import annotations

import uuid

import edecan_worker.handlers.notify_important_event as handler
import pytest
from edecan_schemas import JobEnvelope
from fakes import make_deps


async def test_handler_builds_a_strict_uuid_only_event(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    event_id = uuid.uuid4()
    artifact_id = uuid.uuid4()
    seen = []

    async def notify(_deps, event):
        seen.append(event)

    monkeypatch.setattr(handler, "notify_important_event", notify)
    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="notify_important_event",
        payload={
            "user_id": str(user_id),
            "kind": "design_ready",
            "event_id": str(event_id),
            "artifact_id": str(artifact_id),
        },
    )

    await handler.handle(env, make_deps())

    assert len(seen) == 1
    assert seen[0].tenant_id == tenant_id
    assert seen[0].user_id == user_id
    assert seen[0].event_id == event_id
    assert seen[0].artifact_id == artifact_id


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"user_id": "not-a-uuid", "event_id": str(uuid.uuid4()), "kind": "design_ready"},
        {"user_id": str(uuid.uuid4()), "event_id": "free-text", "kind": "design_ready"},
        {"user_id": str(uuid.uuid4()), "event_id": str(uuid.uuid4()), "kind": "invented"},
    ],
)
async def test_handler_rejects_untrusted_payload(payload: dict[str, str]) -> None:
    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        type="notify_important_event",
        payload=payload,
    )
    with pytest.raises(ValueError):
        await handler.handle(env, make_deps())
