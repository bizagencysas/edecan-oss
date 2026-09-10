"""Callback `on_usage` del router global → `usage_events` (kind="llm_tokens").

El router de `main.py` se construye con `on_usage=None`; `get_llm_router`
(`edecan_api.deps`) lo cablea UNA sola vez con `make_llm_usage_persister`,
que lee el tenant del ContextVar fijado por `get_current_user` (task-local).
Este módulo verifica el contrato completo del persister con fakes de sesión y
repo — nunca Postgres real:

- persiste con kind="llm_tokens" y meta {model, input_tokens, output_tokens};
- el modelo es el id real que usa la pantalla "Uso de modelos"
  (meta->>'model', p. ej. "@cf/..." o un deployment de Azure);
- sin tenant en el contexto (flujos no autenticados, p. ej. webhooks de
  Twilio que ya persisten a mano) NO persiste — eso evita el doble conteo;
- fail-open: un fallo de persistencia nunca rompe la llamada LLM;
- `_ensure_usage_callback` es idempotente.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from edecan_llm.base import Usage

from edecan_api import deps


class _FakeSession:
    pass


class FakeSqlRepo:
    events: list[dict[str, Any]] = []

    def __init__(self, session: Any) -> None:
        self.session = session

    async def add_usage_event(
        self,
        *,
        tenant_id: uuid.UUID,
        kind: str,
        quantity: float,
        meta: dict[str, Any] | None = None,
        cost_usd: float | None = None,
    ) -> None:
        FakeSqlRepo.events.append(
            {
                "tenant_id": tenant_id,
                "kind": kind,
                "quantity": quantity,
                "meta": meta or {},
                "cost_usd": cost_usd,
            }
        )


@pytest.fixture
def fake_repo(monkeypatch: pytest.MonkeyPatch) -> type[FakeSqlRepo]:
    FakeSqlRepo.events = []

    @asynccontextmanager
    async def fake_get_session(tenant_id: uuid.UUID | None):
        yield _FakeSession()

    monkeypatch.setattr(deps, "get_session", fake_get_session)
    monkeypatch.setattr(deps, "SqlRepo", FakeSqlRepo)
    return FakeSqlRepo


def _persister() -> Any:
    return deps.make_llm_usage_persister(tenant_getter=deps._usage_tenant_ctx.get)


async def test_on_usage_persiste_llm_tokens_con_modelo_real(fake_repo) -> None:
    tenant_id = uuid.uuid4()
    token = deps._usage_tenant_ctx.set(tenant_id)
    try:
        await _persister()(
            "@cf/zai-org/glm-4.7-flash", Usage(input_tokens=7, output_tokens=3)
        )
    finally:
        deps._usage_tenant_ctx.reset(token)

    assert len(FakeSqlRepo.events) == 1
    event = FakeSqlRepo.events[0]
    assert event["tenant_id"] == tenant_id
    assert event["kind"] == "llm_tokens"
    assert event["quantity"] == 10.0
    assert event["meta"]["model"] == "@cf/zai-org/glm-4.7-flash"
    assert event["meta"]["input_tokens"] == 7
    assert event["meta"]["output_tokens"] == 3


async def test_on_usage_sin_tenant_en_el_contexto_no_persiste(fake_repo) -> None:
    await _persister()("m", Usage(input_tokens=1, output_tokens=1))

    assert FakeSqlRepo.events == []


async def test_on_usage_ignora_completions_sin_tokens(fake_repo) -> None:
    token = deps._usage_tenant_ctx.set(uuid.uuid4())
    try:
        await _persister()("m", Usage(input_tokens=0, output_tokens=0))
    finally:
        deps._usage_tenant_ctx.reset(token)

    assert FakeSqlRepo.events == []


async def test_on_usage_estima_costo_solo_para_modelos_conocidos(fake_repo) -> None:
    token = deps._usage_tenant_ctx.set(uuid.uuid4())
    try:
        await _persister()("gpt-4o", Usage(input_tokens=1_000_000, output_tokens=1_000_000))
        await _persister()("@cf/modelo/sin/pricing", Usage(input_tokens=5, output_tokens=5))
    finally:
        deps._usage_tenant_ctx.reset(token)

    conocido, desconocido = FakeSqlRepo.events
    assert conocido["meta"]["cost_status"] == "known"
    assert conocido["cost_usd"] == pytest.approx(2.5 + 10.0)
    assert desconocido["meta"]["cost_status"] == "unknown"
    assert desconocido["cost_usd"] is None


async def test_on_usage_fail_open_nunca_rompe_la_llamada_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    class RepoQueExplota:
        def __init__(self, session: Any) -> None:
            self.session = session

        async def add_usage_event(self, **kwargs: Any) -> None:
            raise RuntimeError("db caída")

    @asynccontextmanager
    async def fake_get_session(tenant_id: uuid.UUID | None):
        yield _FakeSession()

    monkeypatch.setattr(deps, "get_session", fake_get_session)
    monkeypatch.setattr(deps, "SqlRepo", RepoQueExplota)

    token = deps._usage_tenant_ctx.set(uuid.uuid4())
    try:
        await _persister()("m", Usage(input_tokens=1, output_tokens=1))
    finally:
        deps._usage_tenant_ctx.reset(token)


def test_ensure_usage_callback_cablea_una_sola_vez() -> None:
    router = SimpleNamespace(_on_usage=None)
    deps._ensure_usage_callback(router)
    first = router._on_usage
    assert first is not None
    deps._ensure_usage_callback(router)
    assert router._on_usage is first