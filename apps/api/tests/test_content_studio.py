from __future__ import annotations

import uuid

from conftest import auth_headers
from edecan_core import ToolResult
from edecan_llm.base import CompletionResponse, Usage
from edecan_schemas import TokenBundle

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


class FakeProjectTool:
    calls = []

    async def run(self, ctx, args):  # noqa: ANN001
        self.calls.append((ctx, args))
        action = args["accion"]
        if action == "list":
            payload = {"ok": True, "projects": [{"id": "proj_1", "name": "Demo"}]}
            artifacts = []
        else:
            payload = {
                "ok": True,
                "action": action,
                "project": {"id": args.get("projectId", "proj_new"), "name": "Demo"},
                "revision": "rev_1",
            }
            artifacts = [
                {"file_id": str(uuid.uuid4()), "filename": "preview.png", "mime": "image/png"}
            ]
        return ToolResult(
            content="Studio terminó la operación.",
            data={"action": action, "result": payload, "artifacts": artifacts},
            presentation=[{"media_kind": "image", "file_id": artifacts[0]["file_id"]}]
            if artifacts
            else None,
        )


async def test_content_studio_creates_private_editable_package(client, app, fake_repo, monkeypatch):
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
    # `target` no es un concepto de la tool: solo este endpoint lo conoce
    # (default "personal") y se lo pasa como "destino" para que la card de
    # chat equivalente (`ToolResult.presentation`) sepa cuál variante mostrar.
    assert FakeSocialTool.calls[0][1]["destino"] == "personal"
    assert FakeSocialTool.calls[0][0].tenant_id == tenant_id
    assert llm.calls[0][0] == "profundo"
    request = llm.calls[0][2]
    assert "La memoria del modelo es antigua por definición" in request.system
    assert "Fuentes oficiales actuales" in request.system
    # El bloque de destino ahora se arma con `edecan_creative.marcas.voice_prompt_block`
    # (perfil personal por defecto, sin ningún nombre de marca fijo en el código).
    assert "Destino: Perfil personal." in request.messages[0].content
    assert "publica como PERSONA" in request.messages[0].content
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
    # X no tiene destino personal/Acme: la tool nunca recibe "destino".
    assert "destino" not in FakeSocialTool.calls[0][1]


def test_content_studio_compacts_large_editorial_profile_for_prompt():
    large_profile = {
        "platform": "linkedin_personal",
        "configured": True,
        "version": 12,
        "purpose": "p" * 5_000,
        "audience": "a" * 5_000,
        "voice": "v" * 5_000,
        "visual_identity": "i" * 5_000,
        "image_rules": "r" * 5_000,
        "calls_to_action": "c" * 5_000,
        "avoid": "x" * 5_000,
        "notes": "n" * 20_000,
        "content_pillars": [f"pillar {idx}" * 40 for idx in range(20)],
        "preferred_formats": [f"format {idx}" * 40 for idx in range(20)],
    }

    compact = content_studio._compact_editorial_profile_for_prompt(large_profile)

    encoded = content_studio.json.dumps(compact, ensure_ascii=False)
    assert len(encoded) <= content_studio._EDITORIAL_PROMPT_TOTAL_LIMIT
    assert compact["configured"] is True
    assert compact["platform"] == "linkedin_personal"
    assert len(compact["content_pillars"]) <= content_studio._EDITORIAL_PROMPT_LIST_LIMIT
    assert len(compact["preferred_formats"]) <= content_studio._EDITORIAL_PROMPT_LIST_LIMIT
    assert len(compact["purpose"]) <= content_studio._EDITORIAL_PROMPT_FIELD_LIMITS["purpose"]


def test_content_studio_maps_fydesign_like_aria_image_defaults():
    body = content_studio.SocialContentCreateIn(
        platform="linkedin",
        topic="Post sobre Gemini 3.6 Flash",
        objective="Presentar un producto",
        tone="Claro, humano y con criterio",
        with_image=True,
    )

    class Settings:
        EDECAN_CONTENT_IMAGE_PROVIDER = "fydesign"
        EDECAN_CONTENT_IMAGE_MODEL = "gpt-image-2"
        EDECAN_CONTENT_IMAGE_QUALITY = "standard"
        EDECAN_CONTENT_IMAGE_STYLE = "premium editorial"
        EDECAN_CONTENT_IMAGE_BRAND = "acme"

    args = content_studio._content_studio_fydesign_args(
        body=body,
        generated={
            "texto": "Copy final del post",
            "visual_prompt": "Una escena editorial limpia",
            "titular_visual": "Más claridad",
        },
        settings=Settings(),
    )

    assert args["platform"] == "linkedin"
    assert args["engine"] == "gpt-image-2"
    assert args["quality"] == "standard"
    assert args["style"] == "premium editorial"
    assert args["brand"] == "acme"
    assert "Copy final del post" in args["prompt"]


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


async def test_linkedin_publish_requires_confirmation_and_uses_connected_account(
    client, app, fake_repo, monkeypatch
):
    user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    account = await fake_repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key="linkedin",
        external_account_id="person-1",
        display_name="Ada",
        scopes=["w_member_social"],
    )

    class FakeVault:
        async def get(self, requested_tenant_id, account_id):  # noqa: ANN001
            assert requested_tenant_id == tenant_id
            assert account_id == account["id"]
            return TokenBundle(access_token="linkedin-token", scopes=["w_member_social"])

    published = []
    queued = []

    async def fake_create_post(http, bundle, **kwargs):  # noqa: ANN001
        published.append((bundle, kwargs))
        return {"id": "urn:li:share:123"}

    async def fake_enqueue(settings, job_type, payload, requested_tenant_id):  # noqa: ANN001
        queued.append((job_type, payload, requested_tenant_id))
        return uuid.uuid4()

    app.dependency_overrides[deps.get_vault] = lambda: FakeVault()
    monkeypatch.setattr(content_studio, "create_linkedin_post", fake_create_post)
    monkeypatch.setattr(content_studio, "enqueue", fake_enqueue)
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)

    unconfirmed = await client.post(
        "/v1/content/social/publish",
        headers=headers,
        json={
            "platform": "linkedin",
            "text": "Una idea útil.",
            "confirmed": False,
        },
    )
    assert unconfirmed.status_code == 409
    assert published == []

    confirmed = await client.post(
        "/v1/content/social/publish",
        headers=headers,
        json={
            "platform": "linkedin",
            "text": "Una idea útil.",
            "confirmed": True,
        },
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["provider_id"] == "urn:li:share:123"
    assert published[0][0].access_token == "linkedin-token"
    assert published[0][1]["text"] == "Una idea útil."
    assert published[0][1]["image"] is None
    assert fake_repo.audit_log[-1]["action"] == "content.linkedin_published"
    assert queued[-1][1]["kind"] == "content_published"


async def test_full_studio_api_lists_and_creates_private_projects(client, app, monkeypatch):
    user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    FakeProjectTool.calls = []
    queued = []

    async def fake_enqueue(settings, job_type, payload, tenant_id):  # noqa: ANN001
        queued.append((job_type, payload, tenant_id))
        return uuid.uuid4()

    monkeypatch.setattr(content_studio, "VerProyectosCreativosTool", FakeProjectTool)
    monkeypatch.setattr(content_studio, "CrearEditarProyectoCreativoTool", FakeProjectTool)
    monkeypatch.setattr(content_studio, "enqueue", fake_enqueue)

    listed = await client.post(
        "/v1/content/studio/actions",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
        json={"action": "list", "includeArchived": False},
    )
    assert listed.status_code == 200
    assert listed.json()["result"]["projects"][0]["name"] == "Demo"
    assert FakeProjectTool.calls[0][1] == {
        "accion": "list",
        "includeArchived": False,
    }

    created = await client.post(
        "/v1/content/studio/actions",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
        json={
            "action": "create",
            "prompt": "Crea una app financiera elegante",
            "projectName": "Acme",
            "mode": "mockup",
            "count": 3,
            "quality": "max",
            "files": [str(uuid.uuid4())],
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["action"] == "create"
    assert body["result"]["project"]["id"] == "proj_new"
    assert body["artifacts"][0]["mime"] == "image/png"
    assert body["presentation"][0]["media_kind"] == "image"
    create_args = FakeProjectTool.calls[1][1]
    assert create_args["accion"] == "create"
    assert create_args["projectName"] == "Acme"
    assert create_args["archivos"]
    assert "files" not in create_args
    assert queued[0][0] == "notify_important_event"
    assert queued[0][1]["kind"] == "design_ready"
    assert uuid.UUID(queued[0][1]["event_id"])
    assert queued[0][1]["artifact_id"] == body["artifacts"][0]["file_id"]
    assert queued[0][2] == tenant_id


async def test_full_studio_api_requires_confirmation_and_rejects_host_paths(client, monkeypatch):
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    FakeProjectTool.calls = []
    monkeypatch.setattr(content_studio, "AdministrarProyectoCreativoTool", FakeProjectTool)

    unconfirmed = await client.post(
        "/v1/content/studio/actions",
        headers=headers,
        json={"action": "archive", "projectId": "proj_1"},
    )
    assert unconfirmed.status_code == 422
    assert FakeProjectTool.calls == []

    confirmed = await client.post(
        "/v1/content/studio/actions",
        headers=headers,
        json={"action": "archive", "projectId": "proj_1", "confirmed": True},
    )
    assert confirmed.status_code == 200
    assert FakeProjectTool.calls[0][1] == {
        "accion": "archive",
        "projectId": "proj_1",
    }

    unsafe_shape = await client.post(
        "/v1/content/studio/actions",
        headers=headers,
        json={
            "action": "create",
            "prompt": "copia archivos",
            "assetPaths": ["/etc/passwd"],
        },
    )
    assert unsafe_shape.status_code == 422
