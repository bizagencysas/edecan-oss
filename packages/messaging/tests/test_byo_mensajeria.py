"""Regresión anti-fuga dedicada de `edecan_messaging._creds.resolver_credenciales`
(Barrido de seguridad v5).

Veredicto de la auditoría (ver `docs/credenciales.md` sección "Auditoría v5"):
limpio — `resolver_credenciales(ctx, plataforma)` ni siquiera tiene un
parámetro `settings`/`ctx.settings` en su cuerpo: la única fuente posible de
credencial es `ctx.vault.get(...)` para la `connector_account` de ESE tenant.
Este archivo lo prueba empíricamente con un `ctx.settings` "veneno" que
revienta ante CUALQUIER acceso a un atributo — si `resolver_credenciales` (o
cualquier código que llame, `clients.py`/`tools.py`/`whatsapp.py`) tocara
`ctx.settings` en algún punto nuevo, este test lo detectaría de inmediato en
vez de solo confiar en la lectura del código.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from edecan_messaging._creds import MessagingNotConnectedError, resolver_credenciales
from edecan_messaging.clients import DiscordClient, SlackClient, TelegramClient

_SENTINEL = "FUGA_DE_PLATAFORMA_NO_DEBE_APARECER"


class _PoisonSettings:
    """Cualquier acceso a un atributo revienta — si algún código bajo prueba
    llegara a leer `ctx.settings.LO_QUE_SEA`, este objeto lo delata."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(
            f"resolver_credenciales (o algo que llame) leyó ctx.settings.{name} — "
            "las credenciales de mensajería NUNCA deben venir de settings/plataforma."
        )


@pytest.fixture
def ctx_con_settings_veneno(make_ctx, make_session, make_vault):
    def _make(*, plataforma: str, bundle: Any) -> Any:
        cuenta_id = "88888888-8888-8888-8888-888888888888"
        return make_ctx(
            settings=_PoisonSettings(),
            session=make_session([[{"id": cuenta_id}]]),
            vault=make_vault(bundle=bundle),
        )

    return _make


async def test_resolver_credenciales_telegram_nunca_toca_ctx_settings(ctx_con_settings_veneno):
    bundle = SimpleNamespace(access_token="token-telegram-del-tenant", scopes=[])
    ctx = ctx_con_settings_veneno(plataforma="telegram", bundle=bundle)

    credencial = await resolver_credenciales(ctx, "telegram")

    assert credencial.access_token == "token-telegram-del-tenant"
    assert credencial.access_token != _SENTINEL


async def test_resolver_credenciales_whatsapp_nunca_toca_ctx_settings(ctx_con_settings_veneno):
    bundle = SimpleNamespace(access_token="token-whatsapp-del-tenant", scopes=["phone-id-123"])
    ctx = ctx_con_settings_veneno(plataforma="whatsapp", bundle=bundle)

    credencial = await resolver_credenciales(ctx, "whatsapp")

    assert credencial.access_token == "token-whatsapp-del-tenant"
    assert credencial.scopes == ("phone-id-123",)


async def test_sin_cuenta_conectada_no_toca_settings_tampoco(make_ctx, make_session, make_vault):
    ctx = make_ctx(
        settings=_PoisonSettings(), session=make_session([[]]), vault=make_vault(bundle=None)
    )
    with pytest.raises(MessagingNotConnectedError):
        await resolver_credenciales(ctx, "discord")


# ---------------------------------------------------------------------------
# Aislamiento entre tenants: dos resoluciones seguidas con distinta
# credencial nunca se mezclan (mismo criterio que `EnviarMensajeTool.run`
# resuelve por request, nunca cachea entre tenants).
# ---------------------------------------------------------------------------


async def test_dos_tenants_seguidos_resuelven_credenciales_independientes(
    make_ctx, make_session, make_vault
):
    bundle_a = SimpleNamespace(access_token="clave-tenant-A", scopes=[])
    bundle_b = SimpleNamespace(access_token="clave-tenant-B", scopes=[])
    ctx_a = make_ctx(session=make_session([[{"id": "cta-a"}]]), vault=make_vault(bundle=bundle_a))
    ctx_b = make_ctx(session=make_session([[{"id": "cta-b"}]]), vault=make_vault(bundle=bundle_b))

    cred_a = await resolver_credenciales(ctx_a, "slack")
    cred_b = await resolver_credenciales(ctx_b, "slack")

    assert cred_a.access_token == "clave-tenant-A"
    assert cred_b.access_token == "clave-tenant-B"
    assert cred_a.access_token != cred_b.access_token


# ---------------------------------------------------------------------------
# Los clientes HTTP puros (clients.py) tampoco aceptan settings — mismo
# guardrail de firma que el resto del barrido.
# ---------------------------------------------------------------------------


def test_clientes_de_mensajeria_no_aceptan_settings_en_su_constructor():
    import inspect

    for cliente in (TelegramClient, DiscordClient, SlackClient):
        parametros = set(inspect.signature(cliente.__init__).parameters)
        assert "settings" not in parametros, f"{cliente.__name__} no debe aceptar settings"
