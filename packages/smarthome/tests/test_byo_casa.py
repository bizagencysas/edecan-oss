"""Regresión anti-fuga dedicada de `edecan_smarthome.tools._cliente_desde_vault`
(Barrido de seguridad v5).

Veredicto de la auditoría (ver `docs/credenciales.md` sección "Auditoría v5"):
limpio — `base_url`/`token` SIEMPRE vienen de `bundle.scopes[0]`/
`bundle.access_token` (el vault del propio tenant, `ARCHITECTURE.md` §12.b,
`connector_key="homeassistant"`). `ctx.settings` solo se lee para
`HOMEASSISTANT_TIMEOUT_SECONDS` — un timeout en segundos, no un secreto (ver
`ARCHITECTURE.md` §0, "valores NO-secretos con default de plataforma") — y
esto es, a propósito, el ÚNICO campo de `settings` que toca esta función.
Este archivo lo prueba con la request HTTP real (capturada con `respx`) que
sale hacia la instancia de Home Assistant del tenant.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import respx
from edecan_smarthome.tools import CasaDispositivosTool, _cliente_desde_vault

_SENTINEL = "FUGA_DE_PLATAFORMA_NO_DEBE_APARECER"


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _VaultConBundle:
    def __init__(self, bundle):
        self._bundle = bundle

    async def get(self, tenant_id, account_id):
        return self._bundle


async def test_cliente_desde_vault_solo_lee_homeassistant_timeout_seconds_de_settings():
    """`_cliente_desde_vault` solo debe pedirle a `ctx.settings` el campo
    `HOMEASSISTANT_TIMEOUT_SECONDS` (un timeout, no un secreto) — un objeto
    que registra cada atributo pedido y revienta ante cualquier otro nombre
    confirma que ningún campo de credencial se lee de ahí."""
    accedidos: list[str] = []

    class _SettingsQueRegistraAccesos:
        def __getattr__(self, name: str):
            accedidos.append(name)
            if name == "HOMEASSISTANT_TIMEOUT_SECONDS":
                return 15
            raise AssertionError(
                f"_cliente_desde_vault leyó ctx.settings.{name} — solo debe leer "
                "HOMEASSISTANT_TIMEOUT_SECONDS, nunca un campo de credencial."
            )

    session = SimpleNamespace(execute=lambda stmt, params=None: _fake_execute())
    ctx = SimpleNamespace(
        tenant_id="tenant-1",
        session=session,
        vault=_VaultConBundle(
            SimpleNamespace(access_token="token-del-tenant", scopes=["http://ha.local:8123"])
        ),
        settings=_SettingsQueRegistraAccesos(),
    )

    async def _fake_execute():
        return _FakeResult({"id": "cuenta-1"})

    resultado = await _cliente_desde_vault(ctx)

    assert not isinstance(resultado, dict)  # no cayó a ToolResult de error
    assert accedidos == ["HOMEASSISTANT_TIMEOUT_SECONDS"]


@respx.mock
async def test_casa_dispositivos_request_real_solo_lleva_el_token_del_tenant(
    make_ctx, make_session, make_vault
):
    base_url = "http://ha-del-tenant.local:8123"
    route = respx.get(f"{base_url}/api/states").mock(return_value=httpx.Response(200, json=[]))
    bundle = SimpleNamespace(access_token="token-real-del-tenant", scopes=[base_url])
    ctx = make_ctx(
        settings=SimpleNamespace(HOMEASSISTANT_TIMEOUT_SECONDS=5),
        session=make_session([[{"id": "cuenta-1"}]]),
        vault=make_vault(bundle=bundle),
    )

    resultado = await CasaDispositivosTool().run(ctx, {})

    assert route.called
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer token-real-del-tenant"
    assert _SENTINEL not in request.headers["Authorization"]
    assert resultado.data == {"dispositivos": []}


@respx.mock
async def test_casa_dispositivos_dos_tenants_seguidos_nunca_mezclan_tokens(
    make_ctx, make_session, make_vault
):
    base_url = "http://ha-del-tenant.local:8123"
    route = respx.get(f"{base_url}/api/states").mock(return_value=httpx.Response(200, json=[]))

    async def _run(token: str):
        ctx = make_ctx(
            session=make_session([[{"id": "cuenta-1"}]]),
            vault=make_vault(bundle=SimpleNamespace(access_token=token, scopes=[base_url])),
        )
        await CasaDispositivosTool().run(ctx, {})

    await _run("token-tenant-A")
    await _run("token-tenant-B")

    headers_enviados = [call.request.headers["Authorization"] for call in route.calls]
    assert headers_enviados == ["Bearer token-tenant-A", "Bearer token-tenant-B"]
