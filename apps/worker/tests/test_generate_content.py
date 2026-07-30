"""Tests del job `generate_content`: usa el LLM y guarda el resultado como mensaje."""

from __future__ import annotations

import uuid

import edecan_worker.handlers.generate_content as generate_content_module
from edecan_schemas import JobEnvelope
from fakes import FakeRepo, make_deps


async def _use_platform_router_as_tenant_router(deps, monkeypatch) -> None:
    """`Deps.llm_router_for` (bring-your-own, WP-V3-02) ahora resuelve un
    `LLMRouter` REAL (con un proveedor REAL, ej. Anthropic) a partir de la
    config guardada del tenant — simular acá "el tenant conectó algo" con un
    vault falso haría que este test intentara una llamada de red real al
    completar el contenido, que es exactamente lo que este test NO debe
    hacer (usa `deps.llm_router`, un `FakeLLMRouter` en memoria, para poder
    aserts sobre `provider.requests`/`resolved` sin red).

    Este archivo no prueba la resolución bring-your-own en sí — eso ya lo
    cubre `apps/worker/tests/test_llm_por_tenant.py` exhaustivamente — así
    que alcanza con monkeypatchear `llm_router_for` para que devuelva
    directo el router de plataforma (`deps.llm_router`, el fake), como si
    fuera el resultado ya resuelto de la config propia del tenant."""

    async def _fake_llm_router_for(tenant_id):
        return deps.llm_router

    monkeypatch.setattr(deps, "llm_router_for", _fake_llm_router_for)


async def test_generate_content_guarda_mensaje_y_uso(monkeypatch) -> None:
    fake_repo = FakeRepo()
    monkeypatch.setattr(generate_content_module, "SqlRepo", lambda session: fake_repo)

    tenant_id = uuid.uuid4()
    fake_repo.tenants[tenant_id] = {"id": tenant_id, "plan_key": "hosted_pro"}
    conversation_id = uuid.uuid4()
    fake_repo.conversations[conversation_id] = {
        "id": conversation_id,
        "tenant_id": tenant_id,
        "user_id": uuid.uuid4(),
        "title": "",
        "channel": "web",
    }

    deps = make_deps()
    await _use_platform_router_as_tenant_router(deps, monkeypatch)
    deps.llm_router.provider.reply = "Aquí va el post generado."

    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="generate_content",
        payload={
            "conversation_id": str(conversation_id),
            "brief": "Escribe un post sobre CFOs personales",
        },
    )
    await generate_content_module.handle(env, deps)

    assert len(fake_repo.messages) == 1
    message = fake_repo.messages[0]
    assert message["role"] == "assistant"
    assert message["content"] == {"text": "Aquí va el post generado."}
    assert message["conversation_id"] == conversation_id
    assert message["tokens_in"] == 10
    assert message["tokens_out"] == 20

    assert len(fake_repo.usage_events) == 1
    assert fake_repo.usage_events[0]["kind"] == "llm_tokens"
    assert fake_repo.usage_events[0]["quantity"] == 30

    # el brief llegó al proveedor LLM
    assert len(deps.llm_router.provider.requests) == 1
    sent_request = deps.llm_router.provider.requests[0]
    assert sent_request.messages[0].content == "Escribe un post sobre CFOs personales"

    # se resolvió el alias "principal" con los flags reales del plan del tenant
    assert deps.llm_router.resolved[0][0] == "principal"


async def test_generate_content_usa_plan_free_si_no_hay_tenant(monkeypatch) -> None:
    fake_repo = FakeRepo()
    monkeypatch.setattr(generate_content_module, "SqlRepo", lambda session: fake_repo)

    deps = make_deps()
    await _use_platform_router_as_tenant_router(deps, monkeypatch)
    tenant_id = uuid.uuid4()  # no existe en fake_repo.tenants
    conversation_id = uuid.uuid4()
    fake_repo.conversations[conversation_id] = {
        "id": conversation_id,
        "tenant_id": tenant_id,
        "user_id": uuid.uuid4(),
        "title": "",
        "channel": "web",
    }
    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="generate_content",
        payload={"conversation_id": str(conversation_id), "brief": "brief corto"},
    )
    await generate_content_module.handle(env, deps)

    _, flags_usados = deps.llm_router.resolved[0]
    assert flags_usados == generate_content_module.PLANES["free_selfhost"].flags
    assert len(fake_repo.messages) == 1


async def test_generate_content_conversacion_de_otro_tenant_no_escribe_nada(monkeypatch) -> None:
    """Regresión: el conversation_id del payload debe pertenecer a env.tenant_id.

    El worker corre con conexión "dueño" (bypassa RLS), así que sin esta
    verificación un job con un conversation_id ajeno escribiría el mensaje
    generado por el LLM en la conversación privada de otro tenant.
    """
    fake_repo = FakeRepo()
    monkeypatch.setattr(generate_content_module, "SqlRepo", lambda session: fake_repo)

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    fake_repo.tenants[tenant_a] = {"id": tenant_a, "plan_key": "hosted_pro"}

    # La conversación existe, pero pertenece a otro tenant (tenant_b).
    conversation_id = uuid.uuid4()
    fake_repo.conversations[conversation_id] = {
        "id": conversation_id,
        "tenant_id": tenant_b,
        "user_id": uuid.uuid4(),
        "title": "",
        "channel": "web",
    }

    deps = make_deps()
    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_a,
        type="generate_content",
        payload={"conversation_id": str(conversation_id), "brief": "brief ajeno"},
    )
    await generate_content_module.handle(env, deps)  # no debe lanzar

    assert fake_repo.messages == []
    assert fake_repo.usage_events == []
    assert deps.llm_router.provider.requests == []


async def test_generate_content_conversacion_inexistente_no_falla(monkeypatch) -> None:
    fake_repo = FakeRepo()
    monkeypatch.setattr(generate_content_module, "SqlRepo", lambda session: fake_repo)

    tenant_id = uuid.uuid4()
    fake_repo.tenants[tenant_id] = {"id": tenant_id, "plan_key": "hosted_pro"}

    deps = make_deps()
    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="generate_content",
        payload={"conversation_id": str(uuid.uuid4()), "brief": "brief corto"},
    )
    await generate_content_module.handle(env, deps)  # no debe lanzar

    assert fake_repo.messages == []
    assert deps.llm_router.provider.requests == []
