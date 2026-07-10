"""Regresión anti-fuga dedicada de `edecan_creative.providers.get_tenant_image_provider`
(Barrido de seguridad v5).

`test_providers.py` ya cubre exhaustivamente, rama por rama, que
`get_tenant_image_provider` nunca degrada a `get_image_provider(ctx.settings)`
(sección "`_fake_settings_plataforma_completa`" de ese archivo — el mismo WP
que corrigió el hallazgo real de v3/v4 para imágenes). Este archivo agrega la
pieza que falta ahí: la request HTTP REAL que sale hacia el proveedor
`openai_compat`, capturada con `respx`, inspeccionada byte a byte para
confirmar que el header `authorization` lleva EXCLUSIVAMENTE la credencial
del tenant — nunca el valor centinela de `IMAGES_API_KEY` de plataforma,
aunque esté perfectamente configurado y disponible en `ctx.settings` al mismo
tiempo que la credencial del tenant.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import respx
from edecan_creative.providers import (
    IMAGES_CONNECTOR_KEY,
    OpenAICompatImagesProvider,
    get_tenant_image_provider,
)

_SENTINEL = "FUGA_DE_PLATAFORMA_NO_DEBE_APARECER"


def _fake_settings_plataforma_con_centinela(fake_settings):
    return fake_settings(
        IMAGES_PROVIDER="openai_compat",
        IMAGES_BASE_URL="https://images.plataforma-nunca-se-usa.example/v1",
        IMAGES_API_KEY=_SENTINEL,
        IMAGES_MODEL="modelo-de-plataforma",
    )


@respx.mock
async def test_get_tenant_image_provider_request_real_nunca_lleva_el_centinela_de_plataforma(
    make_ctx, make_session, make_vault, fake_settings
):
    route = respx.post("https://images-del-tenant.example/v1/images/generations").mock(
        return_value=httpx.Response(
            200, json={"data": [{"b64_json": "aGVsbG8="}]}  # base64("hello")
        )
    )
    cuenta_id = "77777777-7777-7777-7777-777777777777"
    bundle_tenant = {
        "base_url": "https://images-del-tenant.example/v1",
        "api_key": "clave-real-del-tenant",
        "model": "modelo-del-tenant",
    }
    from types import SimpleNamespace

    ctx = make_ctx(
        settings=_fake_settings_plataforma_con_centinela(fake_settings),
        session=make_session([[{"id": cuenta_id}]]),
        vault=make_vault(bundle=SimpleNamespace(access_token=json.dumps(bundle_tenant))),
    )

    provider = await get_tenant_image_provider(ctx)
    assert isinstance(provider, OpenAICompatImagesProvider)

    await provider.generate("un gato programando en Python")

    assert route.called
    request = route.calls.last.request
    auth_header = request.headers["authorization"]
    assert auth_header == "Bearer clave-real-del-tenant"
    assert _SENTINEL not in auth_header
    # Tampoco se coló como parte de la URL/base_url golpeada.
    assert "plataforma-nunca-se-usa" not in str(request.url)
    await provider.aclose()


async def test_get_tenant_image_provider_sin_credencial_nunca_construye_con_el_centinela(
    make_ctx, make_session, make_vault, fake_settings
):
    """Sin cuenta conectada: el `StubImageProvider` resultante es 100%
    offline (no expone ningún atributo de credencial en absoluto), así que ni
    siquiera existe la posibilidad de que el centinela termine en un objeto
    proveedor construido para este tenant."""
    ctx = make_ctx(
        settings=_fake_settings_plataforma_con_centinela(fake_settings),
        session=make_session([[]]),
        vault=make_vault(),
    )
    provider = await get_tenant_image_provider(ctx)
    assert not hasattr(provider, "_api_key")
    imagen = await provider.generate("prueba")
    assert isinstance(imagen, bytes) and imagen[:8] == b"\x89PNG\r\n\x1a\n"


def test_images_connector_key_es_el_mismo_que_usa_el_router_de_credenciales():
    """Regresión de nombres: si este string se desincroniza del literal
    duplicado en `apps/api/edecan_api/routers/credentials.py::IMAGES_CONNECTOR_KEY`
    (duplicado a propósito, ver docstring de `providers.py`), `PUT
    /v1/credentials/images` y `get_tenant_image_provider` dejarían de
    hablarse — el tenant vería su credencial guardada pero jamás usada."""
    assert IMAGES_CONNECTOR_KEY == "images"


@respx.mock
async def test_get_tenant_image_provider_dos_tenants_seguidos_nunca_mezclan_credenciales(
    make_ctx, make_session, make_vault, fake_settings
):
    """Dos resoluciones seguidas (simula dos tenants distintos en la misma
    ventana de tiempo del proceso) — cada `generate()` sale con la key de SU
    PROPIO tenant, nunca la del otro ni la de plataforma."""
    route = respx.post("https://images-del-tenant.example/v1/images/generations").mock(
        return_value=httpx.Response(200, json={"data": [{"b64_json": "aGVsbG8="}]})
    )
    from types import SimpleNamespace

    async def _provider_para(clave: str) -> Any:
        ctx = make_ctx(
            settings=_fake_settings_plataforma_con_centinela(fake_settings),
            session=make_session([[{"id": "cta-1"}]]),
            vault=make_vault(
                bundle=SimpleNamespace(
                    access_token=json.dumps(
                        {
                            "base_url": "https://images-del-tenant.example/v1",
                            "api_key": clave,
                            "model": "modelo",
                        }
                    )
                )
            ),
        )
        return await get_tenant_image_provider(ctx)

    proveedor_a = await _provider_para("clave-tenant-A")
    proveedor_b = await _provider_para("clave-tenant-B")
    await proveedor_a.generate("prompt A")
    await proveedor_b.generate("prompt B")

    headers_enviados = [call.request.headers["authorization"] for call in route.calls]
    assert headers_enviados == ["Bearer clave-tenant-A", "Bearer clave-tenant-B"]
    await proveedor_a.aclose()
    await proveedor_b.aclose()
