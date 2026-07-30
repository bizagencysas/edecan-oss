"""Tests del job `run_campaign_step`: sin `edecan_premium` instalado, termina ok."""

from __future__ import annotations

import sys
import types
import uuid
from contextlib import asynccontextmanager

import edecan_worker.handlers.run_campaign_step as run_campaign_step_module
import pytest
from edecan_schemas import JobEnvelope
from fakes import make_deps


def _envelope() -> JobEnvelope:
    return JobEnvelope(
        job_id=uuid.uuid4(), tenant_id=uuid.uuid4(), type="run_campaign_step", payload={}
    )


async def test_sin_premium_instalado_termina_sin_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # `edecan_premium` es un miembro real de este workspace uv (ver
    # pyproject.toml raíz, [tool.uv.workspace].members) y suele estar
    # instalado en el venv compartido, así que borrarlo de `sys.modules` no
    # basta: el import volvería a resolverlo desde el paquete real. Poner
    # `None` en `sys.modules` fuerza un `ImportError` determinista (truco
    # estándar de `importlib._bootstrap`: `sys.modules[name] is None` =>
    # `ImportError`), ejerciendo así el `try/except ImportError` del handler
    # tal como ocurriría en un self-host sin el paquete comercial instalado
    # (ARCHITECTURE.md §6, §10.10).
    monkeypatch.setitem(sys.modules, "edecan_premium", None)

    deps = make_deps()
    await run_campaign_step_module.handle(_envelope(), deps)  # no debe lanzar


async def test_con_premium_instalado_delega_en_campaigns_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llamadas = []

    async def fake_handle(env, deps):
        llamadas.append((env, deps))

    fake_campaigns_module = types.ModuleType("edecan_premium.campaigns")
    fake_campaigns_module.handle = fake_handle  # type: ignore[attr-defined]
    fake_premium_module = types.ModuleType("edecan_premium")
    fake_premium_module.campaigns = fake_campaigns_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "edecan_premium", fake_premium_module)
    monkeypatch.setitem(sys.modules, "edecan_premium.campaigns", fake_campaigns_module)

    deps = make_deps()
    env = _envelope()
    await run_campaign_step_module.handle(env, deps)

    assert len(llamadas) == 1
    assert llamadas[0][0] is env
    assert llamadas[0][1] is deps


class _NoCampaignResult:
    """`.execute()` de `_load_campaign` solo llama `.first()` en este test."""

    def first(self):
        return None


class _NoCampaignSession:
    """Fake mínimo que sí entiende `.execute()`/`.flush()` de verdad (a
    diferencia de `fakes.FakeSession`, que es un placeholder vacío) — lo
    justo para que `edecan_premium.campaigns._load_campaign` reciba un
    resultado válido (`None`, "campaña no encontrada") y `handle` retorne
    limpio sin necesitar emular `campaign_targets`/Twilio."""

    async def execute(self, clause, params=None):
        return _NoCampaignResult()

    async def flush(self):
        pass


async def test_con_premium_instalado_ejercita_campaigns_handle_real(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Integración real de `run_campaign_step` -> `edecan_premium.campaigns.handle`,
    SIN fakear `campaigns.handle` (a diferencia del test de arriba, que solo
    verifica la delegación). Este es el test que habría atrapado el bug real
    de integración: `campaigns.handle` hacía `session = deps.session`, pero
    `edecan_worker.deps.Deps` (el que arma `make_deps()`, igual que el real
    `build_deps`) nunca tuvo ese atributo -- solo `session_factory`. Con el
    fix (`async with deps.session_factory(None) as session:`), este test
    pasa; con el bug original, fallaría con
    `AttributeError: 'Deps' object has no attribute 'session'`.
    """

    @asynccontextmanager
    async def fake_session_factory_sin_campana(tenant_id):
        yield _NoCampaignSession()

    deps = make_deps(session_factory=fake_session_factory_sin_campana)
    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        type="run_campaign_step",
        payload={"campaign_id": str(uuid.uuid4())},
    )

    await run_campaign_step_module.handle(env, deps)  # no debe lanzar AttributeError
