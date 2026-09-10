from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import edecan_api.bot_turn_service as bot_turn_service
import edecan_api.repo as repo_module
import edecan_api.routers.conversations as conversations
from edecan_api.deps import CurrentUser, TenantCtx
from edecan_api.routers.persistent_agents import PersistentAgentMessageIn


def test_message_contract_accepts_attachment_without_text_and_rejects_empty_body() -> None:
    file_id = str(uuid.uuid4())

    body = PersistentAgentMessageIn(text="", attachments=[file_id])

    assert body.text == ""
    assert body.attachments == [file_id]
    with pytest.raises(ValidationError, match="texto o al menos un archivo"):
        PersistentAgentMessageIn(text="   ", attachments=[])


async def test_attachment_only_turn_resolves_file_before_building_safe_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file_id = uuid.uuid4()
    persisted: list[dict[str, Any]] = []

    async def resolve(**_kwargs: Any) -> list[dict[str, str | None]]:
        return [{"file_id": str(file_id), "filename": "contrato.pdf", "mime": "application/pdf"}]

    async def persist(_session: Any, **kwargs: Any) -> None:
        persisted.append(kwargs)

    monkeypatch.setattr(conversations, "_resolve_message_attachments", resolve)
    monkeypatch.setattr(repo_module, "SqlRepo", lambda _session: object())
    monkeypatch.setattr(bot_turn_service, "persist_chat_message", persist)

    tenant_id = uuid.uuid4()
    user = CurrentUser(
        user_id=uuid.uuid4(),
        tenant=TenantCtx(tenant_id=tenant_id, plan_key="hosted_basic", flags={}),
    )
    chunks = [
        chunk
        async for chunk in bot_turn_service.stream_worker_turn(
            request=SimpleNamespace(),
            session=object(),
            user=user,
            settings=SimpleNamespace(),
            worker={"id": str(uuid.uuid4())},
            conversation_id=uuid.uuid4(),
            user_text="",
            attachments=[str(file_id)],
            run_turn=False,
        )
    ]

    assert len(persisted) == 1
    assert persisted[0]["texto"] == "Revisa los archivos adjuntos."
    assert persisted[0]["adjuntos"][0]["file_id"] == str(file_id)
    assert any("message.done" in chunk for chunk in chunks)


async def test_attachment_only_turn_does_not_hide_unknown_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def missing(**_kwargs: Any) -> list[dict[str, str | None]]:
        raise HTTPException(status_code=404, detail="Archivo adjunto no encontrado.")

    monkeypatch.setattr(conversations, "_resolve_message_attachments", missing)
    monkeypatch.setattr(repo_module, "SqlRepo", lambda _session: object())
    tenant_id = uuid.uuid4()
    user = CurrentUser(
        user_id=uuid.uuid4(),
        tenant=TenantCtx(tenant_id=tenant_id, plan_key="hosted_basic", flags={}),
    )

    with pytest.raises(HTTPException) as raised:
        async for _ in bot_turn_service.stream_worker_turn(
            request=SimpleNamespace(),
            session=object(),
            user=user,
            settings=SimpleNamespace(),
            worker={"id": str(uuid.uuid4())},
            conversation_id=uuid.uuid4(),
            user_text="",
            attachments=[str(uuid.uuid4())],
            run_turn=False,
        ):
            pass

    assert raised.value.status_code == 404
