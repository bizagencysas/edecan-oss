from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

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
    # `fake_create_post` no manda `verified` (a diferencia del conector real, que
    # siempre lo incluye tras releer el post): el endpoint lo trata como confirmado
    # por defecto, así que el contrato viejo de estos tests no se rompe.
    assert confirmed.json()["verified"] == "confirmed"
    assert published[0][0].access_token == "linkedin-token"
    assert published[0][1]["text"] == "Una idea útil."
    assert published[0][1]["image"] is None
    assert fake_repo.audit_log[-1]["action"] == "content.linkedin_published"
    assert queued[-1][1]["kind"] == "content_published"


async def test_linkedin_publish_guarda_autor_y_verificacion_en_el_audit_log(
    client, app, fake_repo, monkeypatch
):
    """Defecto 1c: sin esto, un post salido con el autor equivocado (el bug de hoy:
    token personal publicando "como" la página) no dejaba rastro de a nombre de quién
    quedó realmente publicado -- solo lo que la persona PIDIÓ, nunca lo que LinkedIn
    aceptó de verdad."""
    user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    await fake_repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key="linkedin",
        external_account_id="urn:li:organization:119793977",
        display_name="Página",
        scopes=["w_organization_social"],
    )

    class FakeVault:
        async def get(self, requested_tenant_id, account_id):  # noqa: ANN001
            return TokenBundle(access_token="linkedin-token", scopes=["w_organization_social"])

    async def fake_create_post(http, bundle, **kwargs):  # noqa: ANN001
        return {
            "id": "urn:li:share:555",
            "author": "urn:li:organization:119793977",
            "verified": "confirmed",
            "verification_note": "",
        }

    async def fake_enqueue(settings, job_type, payload, requested_tenant_id):  # noqa: ANN001
        return uuid.uuid4()

    app.dependency_overrides[deps.get_vault] = lambda: FakeVault()
    monkeypatch.setattr(content_studio, "create_linkedin_post", fake_create_post)
    monkeypatch.setattr(content_studio, "enqueue", fake_enqueue)

    response = await client.post(
        "/v1/content/social/publish",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
        json={"platform": "linkedin", "text": "Una idea útil.", "confirmed": True},
    )
    assert response.status_code == 200
    entry = fake_repo.audit_log[-1]
    assert entry["action"] == "content.linkedin_published"
    assert entry["meta"]["author"] == "urn:li:organization:119793977"
    assert entry["meta"]["verified"] == "confirmed"


async def test_linkedin_publish_no_confirmado_no_dice_exito_llano(
    client, app, fake_repo, monkeypatch
):
    """Defecto 1a: si `create_post` no pudo releer el post (típicamente por falta de
    permiso de lectura), el endpoint NUNCA debe reportarlo como el mismo éxito llano de
    siempre -- ni al cliente (`verified`) ni al audit log."""
    user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    await fake_repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key="linkedin",
        external_account_id="person-1",
        display_name="Ada",
        scopes=["w_member_social"],
    )

    class FakeVault:
        async def get(self, requested_tenant_id, account_id):  # noqa: ANN001
            return TokenBundle(access_token="linkedin-token", scopes=["w_member_social"])

    async def fake_create_post(http, bundle, **kwargs):  # noqa: ANN001
        return {
            "id": "urn:li:share:777",
            "author": "urn:li:person:abc123",
            "verified": "unknown",
            "verification_note": "LinkedIn no dio permiso para releer el post (403).",
        }

    async def fake_enqueue(settings, job_type, payload, requested_tenant_id):  # noqa: ANN001
        return uuid.uuid4()

    app.dependency_overrides[deps.get_vault] = lambda: FakeVault()
    monkeypatch.setattr(content_studio, "create_linkedin_post", fake_create_post)
    monkeypatch.setattr(content_studio, "enqueue", fake_enqueue)

    response = await client.post(
        "/v1/content/social/publish",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
        json={"platform": "linkedin", "text": "Una idea útil.", "confirmed": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["verified"] == "unknown"
    assert body["verification_note"]
    assert fake_repo.audit_log[-1]["meta"]["verified"] == "unknown"


# ---------------------------------------------------------------------------
# `POST /v1/content/social/drafts/{draft_id}/publish` — el botón "Aprobar y
# publicar" de una card publicando de verdad.
#
# `FakeDraftsSession` (local a este módulo, duplicada a propósito igual que la
# de `test_ads_router.py`: nunca se toca `conftest.py`/`api_fakes.py`) emula la
# tabla `social_drafts` aplicando de verdad el filtro por `tenant_id` del
# `WHERE`. Eso es lo que hace honesto el test del borrador ajeno: la fila
# EXISTE, pero es de otro tenant, y aun así el endpoint no la ve.
# ---------------------------------------------------------------------------


class FakeResult:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> FakeResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)


@dataclass
class FakeDraftsSession:
    """`get_tenant_session` falso con una `social_drafts` en memoria."""

    filas: list[dict[str, Any]] = field(default_factory=list)
    sentencias: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def _coinciden(self, fila: dict[str, Any], params: dict[str, Any]) -> bool:
        return str(fila["tenant_id"]) == str(params.get("tenant_id")) and fila[
            "draft_id"
        ] == params.get("draft_id")

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> FakeResult:
        sql = " ".join(str(stmt).split())
        valores = dict(params or {})
        self.sentencias.append((sql, valores))
        if sql.startswith("SELECT") and "social_drafts" in sql:
            assert "FOR UPDATE" in sql, "el borrador debe leerse bloqueado (candado anti-doble)"
            return FakeResult([dict(f) for f in self.filas if self._coinciden(f, valores)])
        if sql.startswith("UPDATE social_drafts"):
            for fila in self.filas:
                # `AND status = 'borrador'` va en el SQL real: acá se replica
                # para que el test note si alguien lo quita.
                if self._coinciden(fila, valores) and fila["status"] == "borrador":
                    fila["status"] = "publicado"
                    fila["published_provider_id"] = valores.get("provider_id")
            return FakeResult()
        raise AssertionError(f"SQL inesperado en el test: {sql}")

    async def commit(self) -> None:  # pragma: no cover - el endpoint no comitea a mano
        pass


def _sembrar_borrador(
    session: FakeDraftsSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    draft_id: str = "linkedin-3f9a1c22",
    platform: str = "linkedin",
    target: str = "acme",
    texto: str = "Un borrador que el motor ya escribió y auditó.",
    status: str = "borrador",
    published_provider_id: str | None = None,
) -> dict[str, Any]:
    fila = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "draft_id": draft_id,
        "platform": platform,
        "target": target,
        # Sin `image_file_id` en estos tests: la imagen privada se lee de S3 y
        # ese camino (`_load_private_publish_image`) es de `publish_social_content`,
        # que ya está probado aparte. Acá se prueba el cable del `draft_id`.
        "text": texto,
        "image_file_id": None,
        "status": status,
        "published_provider_id": published_provider_id,
    }
    session.filas.append(fila)
    return fila


def _mensajes_del_chat(fake_repo) -> list[str]:  # noqa: ANN001
    return [
        str((m["content"] or {}).get("text") or "")
        for mensajes in fake_repo.messages.values()
        for m in mensajes
    ]


async def _preparar_publicacion(
    app, fake_repo, monkeypatch, *, tenant_id: uuid.UUID, con_cuenta: bool = True
) -> tuple[FakeDraftsSession, list[tuple[Any, dict[str, Any]]]]:
    """Cuenta de LinkedIn conectada + vault + `create_post` falso + cola falsa."""

    account = None
    if con_cuenta:
        account = await fake_repo.create_connector_account(
            tenant_id=tenant_id,
            connector_key="linkedin",
            external_account_id="urn:li:organization:119793977",
            display_name="Página de la organización",
            scopes=["w_organization_social"],
        )

    class FakeVault:
        async def get(self, requested_tenant_id, account_id):  # noqa: ANN001
            assert requested_tenant_id == tenant_id
            assert account is not None and account_id == account["id"]
            return TokenBundle(access_token="linkedin-token", scopes=["w_organization_social"])

    publicados: list[tuple[Any, dict[str, Any]]] = []

    async def fake_create_post(http, bundle, **kwargs):  # noqa: ANN001
        publicados.append((bundle, kwargs))
        return {"id": "urn:li:share:900"}

    async def fake_enqueue(settings, job_type, payload, requested_tenant_id):  # noqa: ANN001
        return uuid.uuid4()

    sesion = FakeDraftsSession()
    app.dependency_overrides[deps.get_vault] = lambda: FakeVault()
    app.dependency_overrides[deps.get_tenant_session] = lambda: sesion
    monkeypatch.setattr(content_studio, "create_linkedin_post", fake_create_post)
    monkeypatch.setattr(content_studio, "enqueue", fake_enqueue)
    return sesion, publicados


async def test_publicar_borrador_por_draft_id_publica_de_verdad_y_lo_cuenta_en_el_chat(
    client, app, fake_repo, monkeypatch
):
    """El botón por fin cumple: un toque -> post en LinkedIn + confirmación escrita.

    Lo que se publica sale de la FILA, no del cuerpo de la petición (que va
    vacío): el teléfono manda el id y nada más, así el texto no se puede alterar
    entre que se generó y se aprobó.
    """
    user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    sesion, publicados = await _preparar_publicacion(
        app, fake_repo, monkeypatch, tenant_id=tenant_id
    )
    fila = _sembrar_borrador(sesion, tenant_id=tenant_id, user_id=user_id)

    respuesta = await client.post(
        "/v1/content/social/drafts/linkedin-3f9a1c22/publish",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
    )

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["status"] == "published"
    assert cuerpo["provider_id"] == "urn:li:share:900"
    assert cuerpo["draft_id"] == "linkedin-3f9a1c22"
    assert cuerpo["already_published"] is False
    # El texto publicado es el guardado, y el destino de organización viajó
    # como página (`org_urn`) gracias a `publish_social_content`, que NO se
    # reescribió: este endpoint solo la llama.
    assert publicados[0][1]["text"] == "Un borrador que el motor ya escribió y auditó."
    assert publicados[0][1]["org_urn"] == "urn:li:organization:119793977"
    # La fila queda cerrada con el id que devolvió LinkedIn: es el candado de
    # la idempotencia para el siguiente toque.
    assert fila["status"] == "publicado"
    assert fila["published_provider_id"] == "urn:li:share:900"
    assert fake_repo.audit_log[-1]["action"] == "content.linkedin_published"
    assert _mensajes_del_chat(fake_repo) == ["Publicado en tu página de LinkedIn (acme). ✅"]


async def test_publicar_borrador_sin_confirmar_no_pone_el_check_en_el_chat(
    client, app, fake_repo, monkeypatch
):
    """Defecto 1a en el camino del botón: si `create_linkedin_post` no pudo releer el
    post (típicamente falta de permiso de lectura, no falta de publicación real), el
    mensaje que queda escrito en el chat NUNCA debe llevar el ✅ -- debe decir con todas
    sus letras que no se pudo confirmar y cómo comprobarlo la propia persona."""
    user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    sesion, _ = await _preparar_publicacion(app, fake_repo, monkeypatch, tenant_id=tenant_id)
    _sembrar_borrador(sesion, tenant_id=tenant_id, user_id=user_id)

    async def fake_create_post_sin_confirmar(http, bundle, **kwargs):  # noqa: ANN001
        return {
            "id": "urn:li:share:900",
            "author": "urn:li:organization:119793977",
            "verified": "unknown",
            "verification_note": "LinkedIn no dio permiso para releer el post (403).",
        }

    monkeypatch.setattr(content_studio, "create_linkedin_post", fake_create_post_sin_confirmar)

    respuesta = await client.post(
        "/v1/content/social/drafts/linkedin-3f9a1c22/publish",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
    )

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["verified"] == "unknown"
    mensajes = _mensajes_del_chat(fake_repo)
    assert len(mensajes) == 1
    assert "✅" not in mensajes[0]
    assert "no pude releerlo" in mensajes[0].lower()
    # La fila SÍ queda marcada `publicado`: el POST a LinkedIn devolvió 2xx y un id
    # válido -- lo único que falló fue la relectura de confirmación, no la publicación.
    # No marcarla dejaría un botón de "publicar" listo para mandar un segundo post real
    # sobre uno que, hasta donde se sabe, ya salió.
    assert fake_repo.audit_log[-1]["meta"]["verified"] == "unknown"


async def test_publicar_dos_veces_el_mismo_borrador_no_publica_dos_veces(
    client, app, fake_repo, monkeypatch
):
    """Doble toque = un solo post. Publicar dos veces en LinkedIn se ve y da pena.

    El segundo toque devuelve el resultado de la primera vez (mismo
    `provider_id`, `already_published=true`), sin hablar con LinkedIn y sin
    volver a narrar la publicación en el chat.
    """
    user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    sesion, publicados = await _preparar_publicacion(
        app, fake_repo, monkeypatch, tenant_id=tenant_id
    )
    _sembrar_borrador(sesion, tenant_id=tenant_id, user_id=user_id)
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)
    ruta = "/v1/content/social/drafts/linkedin-3f9a1c22/publish"

    primera = await client.post(ruta, headers=headers)
    segunda = await client.post(ruta, headers=headers)

    assert primera.status_code == 200
    assert segunda.status_code == 200
    assert len(publicados) == 1, "el segundo toque volvió a publicar en LinkedIn"
    assert segunda.json()["provider_id"] == primera.json()["provider_id"]
    assert segunda.json()["already_published"] is True
    assert len(_mensajes_del_chat(fake_repo)) == 1
    # Y un solo audit log de publicación, no dos.
    assert [e["action"] for e in fake_repo.audit_log] == ["content.linkedin_published"]


async def test_publicar_borrador_de_otro_tenant_da_404_y_no_publica_nada(
    client, app, fake_repo, monkeypatch
):
    """La revalidación server-side que promete `ApproveDraftAction`.

    La fila existe (con el mismo `draft_id`), pero es de OTRO tenant: 404, y
    404 a secas — la respuesta no debe dejar distinguir "no existe" de "no es
    tuyo".
    """
    user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    otro_tenant = uuid.uuid4()
    sesion, publicados = await _preparar_publicacion(
        app, fake_repo, monkeypatch, tenant_id=tenant_id
    )
    ajena = _sembrar_borrador(
        sesion, tenant_id=otro_tenant, user_id=uuid.uuid4(), texto="El post de otra empresa."
    )

    respuesta = await client.post(
        "/v1/content/social/drafts/linkedin-3f9a1c22/publish",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
    )

    assert respuesta.status_code == 404
    assert publicados == []
    assert ajena["status"] == "borrador"
    assert _mensajes_del_chat(fake_repo) == []


async def test_publicar_borrador_sin_cuenta_conectada_dice_que_la_conecte(
    client, app, fake_repo, monkeypatch
):
    """Sin cuenta de LinkedIn: 400 accionable, no un 500 mudo.

    Y el borrador NO se marca publicado: sigue disponible para cuando la
    persona conecte la cuenta y vuelva a tocar el botón.
    """
    user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    sesion, publicados = await _preparar_publicacion(
        app, fake_repo, monkeypatch, tenant_id=tenant_id, con_cuenta=False
    )
    fila = _sembrar_borrador(sesion, tenant_id=tenant_id, user_id=user_id)

    respuesta = await client.post(
        "/v1/content/social/drafts/linkedin-3f9a1c22/publish",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
    )

    assert respuesta.status_code == 400
    assert "Conecta tu cuenta" in respuesta.json()["detail"]
    assert publicados == []
    assert fila["status"] == "borrador"
    assert _mensajes_del_chat(fake_repo) == []


async def test_publicar_borrador_de_una_red_sin_motor_lo_dice_en_vez_de_reventar(
    client, app, fake_repo, monkeypatch
):
    """`x` tiene conector pero todavía no camino de publicación: 409 explicativo."""
    user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    sesion, publicados = await _preparar_publicacion(
        app, fake_repo, monkeypatch, tenant_id=tenant_id
    )
    _sembrar_borrador(sesion, tenant_id=tenant_id, user_id=user_id, platform="x", target="personal")

    respuesta = await client.post(
        "/v1/content/social/drafts/linkedin-3f9a1c22/publish",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
    )

    assert respuesta.status_code == 409
    assert "x" in respuesta.json()["detail"]
    assert publicados == []


async def test_publicar_borrador_personal_confirma_sin_nombrar_ninguna_pagina(
    client, app, fake_repo, monkeypatch
):
    """El destino sale de la fila, y el texto del chat se arma con `marcas`.

    Nada de nombres de marca fijos en el código: `personal` habla del perfil,
    un id de organización habla de "tu página" con la etiqueta de ESE tenant.
    """
    user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    sesion, publicados = await _preparar_publicacion(
        app, fake_repo, monkeypatch, tenant_id=tenant_id
    )
    _sembrar_borrador(sesion, tenant_id=tenant_id, user_id=user_id, target="personal")

    respuesta = await client.post(
        "/v1/content/social/drafts/linkedin-3f9a1c22/publish",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
    )

    assert respuesta.status_code == 200
    assert respuesta.json()["target"] == "personal"
    # Perfil personal: se publica como la persona, sin `org_urn`.
    assert publicados[0][1]["org_urn"] is None
    assert _mensajes_del_chat(fake_repo) == ["Publicado en tu LinkedIn. ✅"]


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
