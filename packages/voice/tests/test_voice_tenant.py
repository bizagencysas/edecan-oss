"""Tests de `edecan_voice.tenant` (resolución de TTS bring-your-own del
tenant, WP-V5-10) — "tenant → stub", SIN paso de plataforma.

Fakes deliberadamente ligeros, por duck typing (mismo criterio que
`packages/creative/tests/conftest.py`, ver su docstring): `ctx` es un
`SimpleNamespace`, no `edecan_core.ToolContext` — `edecan_voice.tenant` nunca
importa `edecan_core` (ver su docstring), así que tampoco hace falta esa
importación aquí. Sin `conftest.py` propio (no está en la lista de archivos
de este paquete de trabajo): los fixtures se definen directo en este módulo,
pytest los descubre igual.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from edecan_voice.elevenlabs import ElevenLabsTTS
from edecan_voice.polly import PollyTTS
from edecan_voice.stubs import StubTTS
from edecan_voice.tenant import (
    VOICE_TTS_CONNECTOR_KEY,
    resolver_config_tts_del_tenant,
    resolver_tts_del_tenant,
)


class FakeResult:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> FakeResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


@dataclass
class FakeSession:
    respuestas: list[list[dict[str, Any]]] = field(default_factory=list)
    llamadas: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> FakeResult:
        self.llamadas.append((str(stmt), dict(params or {})))
        filas = self.respuestas.pop(0) if self.respuestas else []
        return FakeResult(filas)


@dataclass
class FakeVault:
    bundle: Any = None
    llamadas: list[tuple[Any, Any]] = field(default_factory=list)

    async def get(self, tenant_id: Any, connector_account_id: Any) -> Any:
        self.llamadas.append((tenant_id, connector_account_id))
        return self.bundle


class RaisingVault:
    async def get(self, tenant_id: Any, connector_account_id: Any) -> Any:
        raise RuntimeError("vault caído de verdad")


@pytest.fixture
def make_session():
    def _make_session(respuestas: list[list[dict[str, Any]]] | None = None) -> FakeSession:
        return FakeSession(respuestas=list(respuestas or []))

    return _make_session


@pytest.fixture
def make_vault():
    def _make_vault(bundle: Any = None) -> FakeVault:
        return FakeVault(bundle=bundle)

    return _make_vault


@pytest.fixture
def make_ctx():
    def _make_ctx(
        *,
        session: Any = None,
        settings: Any = None,
        vault: Any = None,
        tenant_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            tenant_id=tenant_id or uuid4(),
            user_id=user_id or uuid4(),
            session=session,
            settings=settings if settings is not None else SimpleNamespace(),
            llm=None,
            vault=vault,
            extras={},
        )

    return _make_ctx


CUENTA_ID = "11111111-1111-1111-1111-111111111111"


def _bundle(config: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(access_token=json.dumps(config))


# ---------------------------------------------------------------------------
# resolver_config_tts_del_tenant — falta de contexto suficiente
# ---------------------------------------------------------------------------


async def test_config_sin_session_ni_vault_devuelve_none(make_ctx):
    ctx = make_ctx()  # session=None, vault=None por defecto
    assert await resolver_config_tts_del_tenant(ctx) is None


async def test_config_sin_vault_devuelve_none(make_ctx, make_session):
    ctx = make_ctx(session=make_session())
    assert await resolver_config_tts_del_tenant(ctx) is None


async def test_config_sin_cuenta_conectada_devuelve_none(make_ctx, make_session, make_vault):
    ctx = make_ctx(session=make_session([[]]), vault=make_vault())
    assert await resolver_config_tts_del_tenant(ctx) is None


async def test_config_consulta_el_connector_key_correcto(make_ctx, make_session, make_vault):
    session = make_session([[{"id": CUENTA_ID}]])
    vault = make_vault(bundle=_bundle({"provider": "elevenlabs", "api_key": "k"}))
    ctx = make_ctx(session=session, vault=vault)

    config = await resolver_config_tts_del_tenant(ctx)

    assert config == {"provider": "elevenlabs", "api_key": "k"}
    assert session.llamadas[0][1]["connector_key"] == VOICE_TTS_CONNECTOR_KEY
    assert vault.llamadas == [(ctx.tenant_id, CUENTA_ID)]


async def test_config_bundle_vacio_devuelve_none(make_ctx, make_session, make_vault):
    ctx = make_ctx(session=make_session([[{"id": CUENTA_ID}]]), vault=make_vault(bundle=None))
    assert await resolver_config_tts_del_tenant(ctx) is None


async def test_config_json_corrupto_devuelve_none(make_ctx, make_session, make_vault):
    bundle = SimpleNamespace(access_token="{esto no es json")
    ctx = make_ctx(session=make_session([[{"id": CUENTA_ID}]]), vault=make_vault(bundle=bundle))
    assert await resolver_config_tts_del_tenant(ctx) is None


async def test_config_no_es_un_dict_devuelve_none(make_ctx, make_session, make_vault):
    bundle = SimpleNamespace(access_token=json.dumps(["no", "es", "un", "dict"]))
    ctx = make_ctx(session=make_session([[{"id": CUENTA_ID}]]), vault=make_vault(bundle=bundle))
    assert await resolver_config_tts_del_tenant(ctx) is None


async def test_config_vault_revienta_devuelve_none(make_ctx, make_session, caplog):
    ctx = make_ctx(session=make_session([[{"id": CUENTA_ID}]]), vault=RaisingVault())
    with caplog.at_level("WARNING"):
        config = await resolver_config_tts_del_tenant(ctx)
    assert config is None
    assert "tenant_id" in caplog.text


# ---------------------------------------------------------------------------
# resolver_tts_del_tenant — "tenant → stub", SIN paso de plataforma
# ---------------------------------------------------------------------------


async def test_resolver_sin_credencial_cae_a_stub_con_aviso_accionable(
    make_ctx, make_session, make_vault, caplog
):
    ctx = make_ctx(session=make_session([[]]), vault=make_vault())
    with caplog.at_level("WARNING"):
        provider = await resolver_tts_del_tenant(ctx)
    assert isinstance(provider, StubTTS)
    assert "PUT /v1/credentials/voice/tts" in caplog.text


async def test_resolver_usa_elevenlabs_del_tenant(make_ctx, make_session, make_vault):
    session = make_session([[{"id": CUENTA_ID}]])
    vault = make_vault(
        bundle=_bundle({"provider": "elevenlabs", "api_key": "clave-tenant", "voice_id": "voz-x"})
    )
    ctx = make_ctx(session=session, vault=vault)

    provider = await resolver_tts_del_tenant(ctx)

    assert isinstance(provider, ElevenLabsTTS)
    # Verifica que se construyó con la credencial del tenant (atributos
    # privados, pero es la forma más directa de confirmar sin pegarle a la
    # red real de ElevenLabs desde este test).
    assert provider._api_key == "clave-tenant"
    assert provider._default_voice_id == "voz-x"


async def test_resolver_elevenlabs_sin_api_key_cae_a_stub(make_ctx, make_session, make_vault):
    """Config guardada con `provider=elevenlabs` pero sin `api_key` (fila a
    medio escribir) — no debe reventar ni usar ninguna clave ajena."""
    session = make_session([[{"id": CUENTA_ID}]])
    vault = make_vault(bundle=_bundle({"provider": "elevenlabs"}))
    ctx = make_ctx(session=session, vault=vault)

    provider = await resolver_tts_del_tenant(ctx)

    assert isinstance(provider, StubTTS)


async def test_resolver_usa_polly_del_tenant_en_modo_local(make_ctx, make_session, make_vault):
    """`provider="polly"` SOLO construye `PollyTTS` real con
    `EDECAN_LOCAL_MODE=True` (ver docstring del módulo — Polly no tiene
    credencial propia del tenant, usa la identidad AWS del PROCESO, que solo
    es "la del tenant" en self-host de un único tenant)."""
    session = make_session([[{"id": CUENTA_ID}]])
    vault = make_vault(bundle=_bundle({"provider": "polly", "voice": "Mia"}))
    ctx = make_ctx(session=session, vault=vault, settings=SimpleNamespace(EDECAN_LOCAL_MODE=True))

    provider = await resolver_tts_del_tenant(ctx)

    assert isinstance(provider, PollyTTS)
    assert provider._voice_id == "Mia"


async def test_resolver_polly_sin_voz_usa_default_en_modo_local(make_ctx, make_session, make_vault):
    session = make_session([[{"id": CUENTA_ID}]])
    vault = make_vault(bundle=_bundle({"provider": "polly"}))
    ctx = make_ctx(session=session, vault=vault, settings=SimpleNamespace(EDECAN_LOCAL_MODE=True))

    provider = await resolver_tts_del_tenant(ctx)

    assert isinstance(provider, PollyTTS)
    assert provider._voice_id == "Lupe"


async def test_resolver_polly_fuera_de_modo_local_cae_a_stub(make_ctx, make_session, make_vault):
    """Sin `EDECAN_LOCAL_MODE=True` (default, hosted compartido) — el tenant
    SÍ tiene una fila `polly` guardada, pero igual cae a `StubTTS`: nunca
    comparte la identidad AWS del proceso entre tenants (hallazgo
    "riesgo-legal-tos", ver `HOTFIXES_PENDIENTES.md`)."""
    session = make_session([[{"id": CUENTA_ID}]])
    vault = make_vault(bundle=_bundle({"provider": "polly", "voice": "Mia"}))
    ctx = make_ctx(session=session, vault=vault)  # settings=SimpleNamespace() por defecto

    provider = await resolver_tts_del_tenant(ctx)

    assert isinstance(provider, StubTTS)


async def test_resolver_proveedor_desconocido_cae_a_stub(make_ctx, make_session, make_vault):
    session = make_session([[{"id": CUENTA_ID}]])
    vault = make_vault(bundle=_bundle({"provider": "algo-inventado"}))
    ctx = make_ctx(session=session, vault=vault)

    provider = await resolver_tts_del_tenant(ctx)

    assert isinstance(provider, StubTTS)


async def test_resolver_vault_revienta_cae_a_stub_nunca_rompe(make_ctx, make_session, caplog):
    ctx = make_ctx(session=make_session([[{"id": CUENTA_ID}]]), vault=RaisingVault())
    with caplog.at_level("WARNING"):
        provider = await resolver_tts_del_tenant(ctx)
    assert isinstance(provider, StubTTS)


async def test_resolver_sin_contexto_suficiente_cae_a_stub(make_ctx):
    ctx = make_ctx()  # session=None, vault=None
    provider = await resolver_tts_del_tenant(ctx)
    assert isinstance(provider, StubTTS)


# ---------------------------------------------------------------------------
# Regresión anti-fuga: config de plataforma en `ctx.settings` NUNCA se usa
# (mismo espíritu que `packages/creative/tests/test_providers.py`, sección
# "riesgo-legal-tos" — el patrón de bug más serio del proyecto, ver
# `DIRECCION_ACTUAL.md` "v4 completado", hallazgo #1).
# ---------------------------------------------------------------------------


def _settings_plataforma_con_credencial_real() -> SimpleNamespace:
    return SimpleNamespace(
        VOICE_TTS_PROVIDER="elevenlabs",
        ELEVENLABS_API_KEY="clave-de-plataforma-NUNCA-SE-USA",
        ELEVENLABS_VOICE_ID="voz-de-plataforma",
        POLLY_VOICE="Lupe",
    )


async def test_resolver_nunca_reusa_credencial_de_plataforma_sin_config_tenant(
    make_ctx, make_session, make_vault, caplog
):
    ctx = make_ctx(
        settings=_settings_plataforma_con_credencial_real(),
        session=make_session([[]]),
        vault=make_vault(),
    )
    with caplog.at_level("WARNING"):
        provider = await resolver_tts_del_tenant(ctx)
    assert isinstance(provider, StubTTS)  # NUNCA ElevenLabsTTS con la clave de plataforma.


async def test_resolver_nunca_reusa_credencial_de_plataforma_con_vault_caido(
    make_ctx, make_session
):
    ctx = make_ctx(
        settings=_settings_plataforma_con_credencial_real(),
        session=make_session([[{"id": CUENTA_ID}]]),
        vault=RaisingVault(),
    )
    provider = await resolver_tts_del_tenant(ctx)
    assert isinstance(provider, StubTTS)
