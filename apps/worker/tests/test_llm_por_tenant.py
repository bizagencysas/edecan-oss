"""El worker usa una única inferencia administrada, independiente del tenant."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fakes import FakeLLMRouter, FakeVault, make_deps


async def test_cualquier_tenant_recibe_el_router_global() -> None:
    router = FakeLLMRouter()
    deps = make_deps(llm_router=router)

    assert await deps.llm_router_for(uuid.uuid4()) is router
    assert await deps.llm_router_for(uuid.uuid4()) is router
    assert await deps.llm_router_for(None) is router


async def test_resolver_llm_no_abre_sesion_ni_vault_del_tenant() -> None:
    calls = 0

    @asynccontextmanager
    async def session_factory(_tenant_id):
        nonlocal calls
        calls += 1
        yield object()

    vault_calls = 0

    def vault_factory(_session):
        nonlocal vault_calls
        vault_calls += 1
        return FakeVault()

    deps = make_deps(session_factory=session_factory, vault=vault_factory)
    await deps.llm_router_for(uuid.uuid4())

    assert calls == 0
    assert vault_calls == 0


async def test_mismo_router_se_reutiliza_sin_cache_por_usuario() -> None:
    router = FakeLLMRouter()
    deps = make_deps(llm_router=router)
    tenant_id = uuid.uuid4()

    first = await deps.llm_router_for(tenant_id)
    second = await deps.llm_router_for(tenant_id)

    assert first is second is router
