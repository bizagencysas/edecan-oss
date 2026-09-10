"""Contrato de composición del router LLM administrado por Workers AI."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from edecan_llm import CompletionRequest, LLMError
from edecan_llm.workers_ai import MODELO_POR_DEFECTO

from edecan_api import deps as edecan_deps
from edecan_api.config import Settings


def _current_user(tenant_id: uuid.UUID) -> edecan_deps.CurrentUser:
    tenant = edecan_deps.TenantCtx(tenant_id=tenant_id, plan_key="hosted_basic", flags={})
    return edecan_deps.CurrentUser(user_id=uuid.uuid4(), tenant=tenant)


def _request(settings: Settings) -> SimpleNamespace:
    from edecan_llm.router import LLMRouter
    from edecan_llm.workers_ai import WorkersAIProvider

    def provider_factory(s: object) -> WorkersAIProvider:
        return WorkersAIProvider(
            account_id=getattr(s, "CLOUDFLARE_ACCOUNT_ID", None),
            api_token=getattr(s, "CLOUDFLARE_API_TOKEN", None),
            timeout=float(getattr(s, "WORKERS_AI_TIMEOUT_SECONDS", 60.0)),
            env_file=None,
        )

    router = LLMRouter(settings, on_usage=None, provider_factory=provider_factory)
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(llm_router=router))
    )


async def test_get_llm_router_no_consulta_credencial_del_tenant() -> None:
    settings = Settings(
        JWT_SECRET="x" * 32,
        CLOUDFLARE_ACCOUNT_ID="account",
        CLOUDFLARE_API_TOKEN="token",
    )

    router = await edecan_deps.get_llm_router(
        request=_request(settings),
        current_user=_current_user(uuid.uuid4()),
        session=None,
    )

    provider, model = router.resolve("rapido", {})
    assert provider.name == "workers_ai"
    assert model == MODELO_POR_DEFECTO


async def test_resolve_worker_va_al_proveedor_workers_ai_con_modelo_cf() -> None:
    """C9b / E-LLM-1: los pasos de misión (alias `worker`) resuelven a un modelo
    `@cf/` sobre el proveedor de Workers AI — antes se mandaban al primario y
    morían 400. Verifica el ruteo sin pegarle a la red."""
    settings = Settings(
        JWT_SECRET="x" * 32,
        CLOUDFLARE_ACCOUNT_ID="account",
        CLOUDFLARE_API_TOKEN="token",
    )
    router = await edecan_deps.get_llm_router(
        request=_request(settings),
        current_user=_current_user(uuid.uuid4()),
        session=None,
    )

    provider, model = router.resolve("worker", {})
    assert provider.name == "workers_ai"
    assert str(model).startswith("@cf/")


async def test_task_router_bloquea_superficie_ide() -> None:
    settings = Settings(
        JWT_SECRET="x" * 32,
        CLOUDFLARE_ACCOUNT_ID="account",
        CLOUDFLARE_API_TOKEN="token",
    )
    router = await edecan_deps.get_llm_router(
        request=_request(settings),
        current_user=_current_user(uuid.uuid4()),
        session=None,
    )

    request = CompletionRequest(
        model="ignorado",
        messages=[{"role": "user", "content": "Refactoriza todo"}],
        metadata={"surface": "ide"},
    )
    with pytest.raises(LLMError, match="runtime de ingeniería separado"):
        router.route(request)
