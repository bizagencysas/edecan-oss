"""Tests de `edecan_meetings.stt` — resolución bring-your-own del STT del
tenant, offline (session/vault fakes, cero red real)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
from edecan_meetings.stt import (
    VOICE_STT_CONNECTOR_KEY,
    resolver_config_stt_del_tenant,
    resolver_stt_del_tenant,
)
from edecan_voice.deepgram import DeepgramSTT
from edecan_voice.stubs import StubSTT


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _FakeResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


@dataclass
class _FakeSession:
    respuestas: list[list[dict[str, Any]]] = field(default_factory=list)
    llamadas: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    explota: bool = False

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        if self.explota:
            raise RuntimeError("Postgres caído")
        self.llamadas.append((str(stmt), dict(params or {})))
        filas = self.respuestas.pop(0) if self.respuestas else []
        return _FakeResult(filas)


@dataclass
class _FakeBundle:
    access_token: str


class _FakeVault:
    def __init__(self) -> None:
        self.store: dict[tuple[uuid.UUID, uuid.UUID], _FakeBundle] = {}

    async def get(
        self, *, tenant_id: uuid.UUID, connector_account_id: uuid.UUID
    ) -> _FakeBundle | None:
        return self.store.get((tenant_id, connector_account_id))


# ---------------------------------------------------------------------------
# resolver_config_stt_del_tenant
# ---------------------------------------------------------------------------


async def test_resolver_config_sin_cuenta_devuelve_none() -> None:
    session = _FakeSession(respuestas=[[]])
    vault = _FakeVault()
    tenant_id = uuid.uuid4()

    config = await resolver_config_stt_del_tenant(session=session, vault=vault, tenant_id=tenant_id)

    assert config is None
    sql, params = session.llamadas[0]
    assert "connector_accounts" in sql
    assert params["connector_key"] == VOICE_STT_CONNECTOR_KEY
    assert params["tenant_id"] == tenant_id


async def test_resolver_config_cuenta_sin_bundle_en_vault_devuelve_none() -> None:
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    session = _FakeSession(respuestas=[[{"id": account_id}]])
    vault = _FakeVault()  # nada guardado

    config = await resolver_config_stt_del_tenant(session=session, vault=vault, tenant_id=tenant_id)

    assert config is None


async def test_resolver_config_camino_feliz() -> None:
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    session = _FakeSession(respuestas=[[{"id": account_id}]])
    vault = _FakeVault()
    vault.store[(tenant_id, account_id)] = _FakeBundle(
        access_token=json.dumps({"provider": "deepgram", "api_key": "clave-del-tenant"})
    )

    config = await resolver_config_stt_del_tenant(session=session, vault=vault, tenant_id=tenant_id)

    assert config == {"provider": "deepgram", "api_key": "clave-del-tenant"}


async def test_resolver_config_json_corrupto_devuelve_none_sin_lanzar() -> None:
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    session = _FakeSession(respuestas=[[{"id": account_id}]])
    vault = _FakeVault()
    vault.store[(tenant_id, account_id)] = _FakeBundle(access_token="{no es json valido")

    config = await resolver_config_stt_del_tenant(session=session, vault=vault, tenant_id=tenant_id)

    assert config is None


async def test_resolver_config_excepcion_de_sesion_devuelve_none_sin_lanzar() -> None:
    session = _FakeSession(explota=True)
    vault = _FakeVault()

    config = await resolver_config_stt_del_tenant(
        session=session, vault=vault, tenant_id=uuid.uuid4()
    )

    assert config is None


# ---------------------------------------------------------------------------
# resolver_stt_del_tenant — fail-closed a StubSTT
# ---------------------------------------------------------------------------


async def test_resolver_stt_sin_credencial_cae_a_stub() -> None:
    session = _FakeSession(respuestas=[[]])
    vault = _FakeVault()

    stt = await resolver_stt_del_tenant(session=session, vault=vault, tenant_id=uuid.uuid4())

    assert isinstance(stt, StubSTT)


async def test_resolver_stt_provider_no_deepgram_cae_a_stub() -> None:
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    session = _FakeSession(respuestas=[[{"id": account_id}]])
    vault = _FakeVault()
    vault.store[(tenant_id, account_id)] = _FakeBundle(
        access_token=json.dumps({"provider": "otro-proveedor", "api_key": "x"})
    )

    stt = await resolver_stt_del_tenant(session=session, vault=vault, tenant_id=tenant_id)

    assert isinstance(stt, StubSTT)


async def test_resolver_stt_deepgram_sin_api_key_cae_a_stub() -> None:
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    session = _FakeSession(respuestas=[[{"id": account_id}]])
    vault = _FakeVault()
    vault.store[(tenant_id, account_id)] = _FakeBundle(
        access_token=json.dumps({"provider": "deepgram"})  # sin api_key
    )

    stt = await resolver_stt_del_tenant(session=session, vault=vault, tenant_id=tenant_id)

    assert isinstance(stt, StubSTT)


async def test_resolver_stt_camino_feliz_devuelve_deepgram_con_la_key_del_tenant() -> None:
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    session = _FakeSession(respuestas=[[{"id": account_id}]])
    vault = _FakeVault()
    vault.store[(tenant_id, account_id)] = _FakeBundle(
        access_token=json.dumps({"provider": "deepgram", "api_key": "clave-real-del-tenant"})
    )

    stt = await resolver_stt_del_tenant(session=session, vault=vault, tenant_id=tenant_id)

    assert isinstance(stt, DeepgramSTT)
    assert stt._api_key == "clave-real-del-tenant"


async def test_resolver_stt_nunca_usa_credencial_de_plataforma(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anti-fuga (`ARCHITECTURE.md` §13/§14, hallazgo #1 de v4): sin
    credencial del tenant, jamás se construye un `DeepgramSTT` con una clave
    de entorno/plataforma — este módulo ni siquiera acepta un parámetro
    `settings`/`api_key` de plataforma en su firma, así que no hay forma de
    que se cuele."""
    monkeypatch.setenv("DEEPGRAM_API_KEY", "CLAVE_DE_PLATAFORMA_NUNCA_DEBE_USARSE")
    session = _FakeSession(respuestas=[[]])
    vault = _FakeVault()

    stt = await resolver_stt_del_tenant(session=session, vault=vault, tenant_id=uuid.uuid4())

    assert isinstance(stt, StubSTT)
