from __future__ import annotations

import uuid

from conftest import auth_headers
from edecan_core import ToolResult
from edecan_llm.base import CompletionResponse, Usage

from edecan_api import deps
from edecan_api.routers import content_studio


class FakeLLMRouter:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = []

    async def complete(self, alias, flags, request):  # noqa: ANN001
        self.calls.append((alias, flags, request))
        return CompletionResponse(
            text=self.text,
            usage=Usage(input_tokens=31, output_tokens=47),
            stop_reason="end",
        )


class FakeSocialTool:
    calls = []

    async def run(self, ctx, args):  # noqa: ANN001
        self.calls.append((ctx, args))
        return ToolResult(
            content="Borrador listo.",
            data={
                "artifacts": [
                    {"file_id": str(uuid.uuid4()), "filename": "post.md", "mime": "text/markdown"},
                    {"file_id": str(uuid.uuid4()), "filename": "post.png", "mime": "image/png"},
                ],
                "platform": args["plataforma"],
                "offline_visual": True,
                "copy": args["texto"],
                "parts": [args["texto"]],
                "alt_text": args["alt_text"],
            },
        )


async def test_content_studio_creates_private_editable_package(
    client, app, fake_repo, monkeypatch
):
    user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    llm = FakeLLMRouter(
        """```json
        {"texto":"Una idea útil, explicada sin humo.",
         "titular_visual":"Menos humo, más utilidad",
         "visual_prompt":"Una mesa de trabajo luminosa",
         "alt_text":"Mesa con cuaderno y luz natural.",
         "hashtags":["Producto"]}
        ```"""
    )
    FakeSocialTool.calls = []
    queued = []

    async def fake_enqueue(settings, job_type, payload, tenant_id):  # noqa: ANN001
        queued.append((job_type, payload, tenant_id))
        return uuid.uuid4()

    app.dependency_overrides[deps.get_llm_router] = lambda: llm
    monkeypatch.setattr(content_studio, "CrearContenidoSocialTool", FakeSocialTool)
    monkeypatch.setattr(content_studio, "enqueue", fake_enqueue)

    response = await client.post(
        "/v1/content/social",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
        json={
            "platform": "linkedin",
            "topic": "Cómo explicar un producto con claridad",
            "objective": "Enseñar algo útil",
            "tone": "Claro y humano",
            "with_image": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["copy"] == "Una idea útil, explicada sin humo."
    assert body["alt_text"] == "Mesa con cuaderno y luz natural."
    assert body["requires_human_confirmation"] is True
    assert body["offline_visual"] is True
    assert [artifact["mime"] for artifact in body["artifacts"]] == [
        "text/markdown",
        "image/png",
    ]
    assert FakeSocialTool.calls[0][1]["con_imagen"] is True
    assert FakeSocialTool.calls[0][0].tenant_id == tenant_id
    assert llm.calls[0][0] == "principal"
    event = fake_repo.usage_events[-1]
    assert event["kind"] == "llm_tokens"
    assert event["quantity"] == 78
    assert event["meta"]["job"] == "content_studio_social"
    assert queued[0][0] == "notify_important_event"
    assert queued[0][1]["kind"] == "content_created"
    assert queued[0][1]["event_id"] == body["artifacts"][0]["file_id"]
    assert queued[0][1]["artifact_id"] == body["artifacts"][0]["file_id"]
    assert queued[0][2] == tenant_id


async def test_content_studio_accepts_plain_text_from_local_model(client, app, monkeypatch):
    user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    app.dependency_overrides[deps.get_llm_router] = lambda: FakeLLMRouter(
        "Un post sencillo aunque el modelo local no haya devuelto JSON."
    )
    FakeSocialTool.calls = []
    monkeypatch.setattr(content_studio, "CrearContenidoSocialTool", FakeSocialTool)

    response = await client.post(
        "/v1/content/social",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
        json={"platform": "x", "topic": "Modelos locales", "with_image": False},
    )

    assert response.status_code == 200
    assert response.json()["copy"].startswith("Un post sencillo")
    assert FakeSocialTool.calls[0][1]["con_imagen"] is False


async def test_content_studio_rejects_unsupported_network_and_requires_auth(client):
    response = await client.post(
        "/v1/content/social",
        json={"platform": "instagram", "topic": "Una idea"},
    )
    assert response.status_code == 401

    response = await client.post(
        "/v1/content/social",
        headers=auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4()),
        json={"platform": "instagram", "topic": "Una idea"},
    )
    assert response.status_code == 422
