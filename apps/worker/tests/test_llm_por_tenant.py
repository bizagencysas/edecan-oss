"""Tests de `Deps.llm_router_for` — bring-your-own de LLM por tenant en el
worker (WP-V3-02, `ARCHITECTURE.md` §12.b/§12.c, `DIRECCION_ACTUAL.md`
"Modelo de credenciales: TODO lo trae el cliente").

Cubre exactamente las propiedades que pide el paquete de trabajo (mismo
criterio fail-closed que `edecan_api.deps.get_llm_router`, corregido
2026-07-08 — ver `HOTFIXES_PENDIENTES.md`):

1. **Caché por tenant EN ÉXITO**: dos llamadas para el mismo `tenant_id` con
   una config válida devuelven el MISMO objeto `LLMRouter` y solo pagan un
   round-trip de sesión/vault (la segunda es puro caché en memoria de
   `Deps`). Un fallo NUNCA se cachea (ver `Deps.llm_router_for`): el próximo
   intento vuelve a consultar, por si el tenant conectó algo mientras tanto.
2. **Fail-closed, NUNCA fallback a plataforma**: sin `tenant_id` es el ÚNICO
   caso legítimo que devuelve `deps.llm_router` (jobs de sistema). Con
   `tenant_id`, sin `connector_account`, sin `TokenBundle`, o ante cualquier
   excepción resolviendo — SIEMPRE lanza `TenantLLMNotConnectedError`, nunca
   degrada a la credencial de plataforma.
3. **Un vault que lanza se traduce a `TenantLLMNotConnectedError`, con un
   mensaje presentable al tenant** — tanto a nivel de `Deps.llm_router_for`
   en aislamiento como en un handler real de punta a punta
   (`generate_content.handle`, reutilizado tal cual — no se edita ese módulo
   ni su test dedicado): la excepción se deja propagar, el despachador del
   job la trata como cualquier otro fallo (reintento/DLQ), nunca ejecuta el
   job contra la credencial de plataforma.

`FakeSession`/`FakeVault` son fakes PROPIOS de este archivo (no se toca
`apps/worker/tests/fakes.py` compartido, tal como exige el paquete de
trabajo): `fakes.FakeVault`/`fakes.FakeSession` ya existen para otros tests,
pero `Deps._resolve_tenant_llm_router` necesita una `AsyncSession` que
entienda el SELECT crudo sobre `connector_accounts` (`sqlalchemy.text`), que
ninguno de los fakes compartidos modela — de ahí los fakes locales.
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from typing import Any

import edecan_worker.handlers.generate_content as generate_content_module
import pytest
from edecan_llm.errors import LLMError
from edecan_schemas import JobEnvelope
from edecan_worker.deps import TenantLLMNotConnectedError
from fakes import FakeRepo, make_deps

# ---------------------------------------------------------------------------
# Fakes locales — ver docstring del módulo.
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def mappings(self) -> _FakeResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return self._row


class _FakeSession:
    """Entiende ÚNICAMENTE el `SELECT id FROM connector_accounts ...` que
    hace `Deps._resolve_tenant_llm_router` — `account_id=None` simula "el
    tenant nunca conectó nada" (`.first()` devuelve `None`)."""

    def __init__(self, *, account_id: uuid.UUID | None) -> None:
        self.account_id = account_id
        self.execute_calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        self.execute_calls.append((str(stmt), dict(params or {})))
        row = {"id": self.account_id} if self.account_id is not None else None
        return _FakeResult(row)


class _MultiTenantFakeSession:
    """Como `_FakeSession`, pero con un `account_id` DISTINTO por tenant (via
    `tenant_id -> account_id`) — para probar que el caché de éxito de
    `Deps.llm_router_for` es por tenant, no un solo valor global."""

    def __init__(self, accounts_by_tenant: dict[uuid.UUID, uuid.UUID]) -> None:
        self._accounts_by_tenant = accounts_by_tenant
        self.execute_calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        self.execute_calls.append((str(stmt), dict(params or {})))
        account_id = self._accounts_by_tenant.get(params["tenant_id"])
        row = {"id": account_id} if account_id is not None else None
        return _FakeResult(row)


class _RaisingSession:
    """Simula una sesión/DB caída: cualquier `execute` lanza."""

    async def execute(self, *args: Any, **kwargs: Any) -> _FakeResult:
        raise RuntimeError("la base de datos no respondió")


def _session_factory_de(session: Any):
    """`SessionFactory` (`edecan_worker.deps.SessionFactory`) que siempre
    entrega `session` sin importar el `tenant_id` pedido — mismo patrón que
    `apps/worker/tests/test_profile_consolidation.py::_session_factory_de`."""

    @asynccontextmanager
    async def _factory(tenant_id: uuid.UUID | None):
        yield session

    return _factory


class _FakeBundle:
    def __init__(self, access_token: str) -> None:
        self.access_token = access_token


class _FakeVault:
    """Doble de `edecan_db.vault.TokenVault`: solo implementa `get(...)`
    (lo único que usa `Deps._resolve_tenant_llm_router`), con la MISMA firma
    de kwargs (`tenant_id=`, `connector_account_id=`) que `deps.py` invoca."""

    def __init__(
        self, *, bundle: _FakeBundle | None = None, raise_exc: Exception | None = None
    ) -> None:
        self._bundle = bundle
        self._raise_exc = raise_exc
        self.get_calls: list[tuple[uuid.UUID, uuid.UUID]] = []

    async def get(
        self, *, tenant_id: uuid.UUID, connector_account_id: uuid.UUID
    ) -> _FakeBundle | None:
        self.get_calls.append((tenant_id, connector_account_id))
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._bundle


def _config_bundle(**overrides: Any) -> _FakeBundle:
    data = {
        "kind": "anthropic",
        "api_key": "sk-ant-test-key-de-prueba",
        "base_url": None,
        "model_principal": None,
        "model_rapido": None,
        "extra": {},
    }
    data.update(overrides)
    return _FakeBundle(access_token=json.dumps(data))


# ---------------------------------------------------------------------------
# 1. Fallback a plataforma
# ---------------------------------------------------------------------------


async def test_tenant_id_none_devuelve_router_de_plataforma_sin_tocar_nada() -> None:
    session = _FakeSession(account_id=None)
    vault = _FakeVault()
    deps = make_deps(session_factory=_session_factory_de(session), vault=lambda s: vault)

    router = await deps.llm_router_for(None)

    assert router is deps.llm_router
    assert session.execute_calls == []
    assert vault.get_calls == []


async def test_sin_connector_account_lanza_tenant_llm_not_connected() -> None:
    tenant_id = uuid.uuid4()
    session = _FakeSession(account_id=None)  # el tenant nunca conectó "llm"
    vault = _FakeVault()
    deps = make_deps(session_factory=_session_factory_de(session), vault=lambda s: vault)

    with pytest.raises(TenantLLMNotConnectedError):
        await deps.llm_router_for(tenant_id)

    assert len(session.execute_calls) == 1
    assert vault.get_calls == []  # sin account_id no hay nada que buscar en el vault


async def test_bundle_ausente_en_vault_lanza_tenant_llm_not_connected() -> None:
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    session = _FakeSession(account_id=account_id)
    vault = _FakeVault(bundle=None)  # cuenta existe, pero el vault no tiene nada guardado
    deps = make_deps(session_factory=_session_factory_de(session), vault=lambda s: vault)

    with pytest.raises(TenantLLMNotConnectedError):
        await deps.llm_router_for(tenant_id)

    assert len(vault.get_calls) == 1


async def test_kind_desconocido_en_config_lanza_tenant_llm_not_connected() -> None:
    """`_build_provider_from_config` lanza `LLMError` para un `kind` que no
    reconoce (config corrupta/de una versión futura) — `resolve()` (llamado
    perezosamente por el handler) es donde eso reventaría, así que el
    contrato de `llm_router_for` en sí no valida `kind`; esta prueba confirma
    que un `kind` corrupto detectado ANTES de construir el config (KeyError
    faltando la clave) sí lo atrapa `_resolve_tenant_llm_router` y se traduce
    a `TenantLLMNotConnectedError`, nunca cae a plataforma."""
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    session = _FakeSession(account_id=account_id)
    bundle = _FakeBundle(access_token=json.dumps({"api_key": "sk-sin-kind"}))  # falta "kind"
    vault = _FakeVault(bundle=bundle)
    deps = make_deps(session_factory=_session_factory_de(session), vault=lambda s: vault)

    with pytest.raises(TenantLLMNotConnectedError):
        await deps.llm_router_for(tenant_id)


# ---------------------------------------------------------------------------
# 2. Un vault (o una sesión) que lanza se traduce a TenantLLMNotConnectedError
# ---------------------------------------------------------------------------


async def test_vault_get_lanza_se_traduce_a_tenant_llm_not_connected() -> None:
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    session = _FakeSession(account_id=account_id)
    vault = _FakeVault(raise_exc=RuntimeError("el vault está caído"))
    deps = make_deps(session_factory=_session_factory_de(session), vault=lambda s: vault)

    with pytest.raises(TenantLLMNotConnectedError):
        await deps.llm_router_for(tenant_id)


async def test_session_execute_lanza_se_traduce_a_tenant_llm_not_connected() -> None:
    tenant_id = uuid.uuid4()
    deps = make_deps(
        session_factory=_session_factory_de(_RaisingSession()), vault=lambda s: _FakeVault()
    )

    with pytest.raises(TenantLLMNotConnectedError):
        await deps.llm_router_for(tenant_id)


async def test_handler_generate_content_propaga_tenant_llm_not_connected_si_el_vault_lanza(
    monkeypatch,
) -> None:
    """Extremo a extremo: un job real (`generate_content`) NO completa su
    trabajo si la resolución bring-your-own falla por completo — la
    excepción se propaga (el despachador del job la trata como cualquier
    otro fallo, reintento/DLQ), nunca se ejecuta contra el `LLMRouter` de
    plataforma."""
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

    account_id = uuid.uuid4()
    session = _FakeSession(account_id=account_id)
    vault = _FakeVault(raise_exc=RuntimeError("vault caído de verdad"))
    deps = make_deps(session_factory=_session_factory_de(session), vault=lambda s: vault)
    deps.llm_router.provider.reply = "esto nunca debe usarse (sería la plataforma)"

    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="generate_content",
        payload={"conversation_id": str(conversation_id), "brief": "brief cualquiera"},
    )

    with pytest.raises(TenantLLMNotConnectedError):
        await generate_content_module.handle(env, deps)

    # Nada se persistió ni se llamó al proveedor de plataforma.
    assert fake_repo.messages == []
    assert deps.llm_router.provider.requests == []


# ---------------------------------------------------------------------------
# 3. Caché por tenant
# ---------------------------------------------------------------------------


async def test_resultado_se_cachea_y_no_repite_el_round_trip() -> None:
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    session = _FakeSession(account_id=account_id)
    vault = _FakeVault(bundle=_config_bundle())
    deps = make_deps(session_factory=_session_factory_de(session), vault=lambda s: vault)

    first = await deps.llm_router_for(tenant_id)
    second = await deps.llm_router_for(tenant_id)

    assert first is second
    assert len(session.execute_calls) == 1
    assert len(vault.get_calls) == 1


async def test_un_fallo_nunca_se_cachea_cada_llamada_reintenta() -> None:
    """A diferencia del éxito (sí se cachea, ver
    `test_resultado_se_cachea_y_no_repite_el_round_trip`), un fallo NUNCA se
    guarda en `Deps._tenant_llm_routers` — cada llamada para el mismo tenant
    vuelve a intentar la resolución completa, por si conectó algo mientras
    tanto (ver el docstring de `Deps.llm_router_for`)."""
    tenant_id = uuid.uuid4()
    session = _FakeSession(account_id=None)
    vault = _FakeVault()
    deps = make_deps(session_factory=_session_factory_de(session), vault=lambda s: vault)

    with pytest.raises(TenantLLMNotConnectedError):
        await deps.llm_router_for(tenant_id)
    with pytest.raises(TenantLLMNotConnectedError):
        await deps.llm_router_for(tenant_id)

    # Dos llamadas, dos round-trips completos: nada se cacheó.
    assert len(session.execute_calls) == 2
    assert tenant_id not in deps._tenant_llm_routers


async def test_cache_de_exito_es_por_tenant_no_global() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    account_a, account_b = uuid.uuid4(), uuid.uuid4()
    session = _MultiTenantFakeSession({tenant_a: account_a, tenant_b: account_b})
    vault = _FakeVault(bundle=_config_bundle())
    deps = make_deps(session_factory=_session_factory_de(session), vault=lambda s: vault)

    router_a1 = await deps.llm_router_for(tenant_a)
    router_b = await deps.llm_router_for(tenant_b)
    router_a2 = await deps.llm_router_for(tenant_a)

    assert router_a1 is router_a2
    assert router_b is not router_a1  # cada tenant tiene su PROPIO router...
    # ...y cada uno pagó su propio round-trip: dos SELECTs en total, no tres.
    assert len(session.execute_calls) == 2


# ---------------------------------------------------------------------------
# Camino feliz: config propia del tenant, distinta de la de plataforma
# ---------------------------------------------------------------------------


async def test_tenant_con_config_propia_devuelve_router_distinto_de_plataforma() -> None:
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    session = _FakeSession(account_id=account_id)
    vault = _FakeVault(
        bundle=_config_bundle(model_principal="claude-opus-4-6", model_rapido="claude-haiku-4-6")
    )
    deps = make_deps(session_factory=_session_factory_de(session), vault=lambda s: vault)

    router = await deps.llm_router_for(tenant_id)

    assert router is not deps.llm_router
    _provider, model = router.resolve("principal", {})
    assert model == "claude-opus-4-6"


# ---------------------------------------------------------------------------
# Defensa en profundidad: un `kind` local-only ("claude_cli"/"codex_cli"/
# "ollama", `edecan_llm.router._LOCAL_ONLY_KINDS`) nunca debe ejecutarse
# desde el worker hospedado, aunque exista una `connector_account` guardada
# ---------------------------------------------------------------------------


async def test_tenant_con_config_cli_local_lanza_al_resolver_en_worker_hospedado() -> None:
    """`edecan_worker.config.Settings` NUNCA declara `EDECAN_LOCAL_MODE` (ver
    su propio docstring y el de `edecan_local.worker_loop`: el runner de
    escritorio arma `Deps` con `edecan_api.config.Settings`, no con este
    `Settings`, precisamente por esto). Si de todos modos existe una
    `connector_account` con `kind="claude_cli"` para el tenant (p. ej. la
    base de datos de una instalación local copiada a un servidor hospedado
    compartido), `Deps.llm_router_for` sigue devolviendo un `LLMRouter`
    propio del tenant — la config en sí es válida, no hay error de
    lectura/parseo — pero ese router se niega a construir el proveedor al
    resolver, así que el worker hospedado nunca llega a ejecutar el binario
    local de otro tenant (`edecan_llm.router._LOCAL_ONLY_KINDS`)."""
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    session = _FakeSession(account_id=account_id)
    vault = _FakeVault(
        bundle=_config_bundle(kind="claude_cli", extra={"binary_path": "/usr/local/bin/claude"})
    )
    deps = make_deps(session_factory=_session_factory_de(session), vault=lambda s: vault)

    router = await deps.llm_router_for(tenant_id)

    assert router is not deps.llm_router
    with pytest.raises(LLMError, match="EDECAN_LOCAL_MODE"):
        router.resolve("principal", {})
