"""Tests de `edecan_creative.providers`: `StubImageProvider`,
`OpenAICompatImagesProvider` y `get_image_provider` (`ROADMAP_V2.md` §7.5, §7.7)."""

from __future__ import annotations

import base64
import io
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx
from edecan_creative.providers import (
    IMAGES_CONNECTOR_KEY,
    ImageProviderRequestError,
    OpenAICompatImagesProvider,
    StubImageProvider,
    _parse_size,
    get_image_provider,
    get_tenant_image_provider,
)
from PIL import Image

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_IMAGES_URL = "https://images.example.com/v1/images/generations"


# --- StubImageProvider -------------------------------------------------------------


async def test_stub_provider_produces_valid_png_header():
    data = await StubImageProvider().generate("un gato programador")
    assert data.startswith(PNG_SIGNATURE)


async def test_stub_provider_is_deterministic_for_the_same_prompt_and_size():
    provider = StubImageProvider()
    primero = await provider.generate("el mismo prompt", size="256x256")
    segundo = await provider.generate("el mismo prompt", size="256x256")
    assert primero == segundo


async def test_stub_provider_differs_for_different_prompts():
    provider = StubImageProvider()
    a = await provider.generate("prompt uno")
    b = await provider.generate("prompt dos")
    assert a != b


async def test_stub_provider_respects_requested_size():
    data = await StubImageProvider().generate("tamaño personalizado", size="300x150")
    image = Image.open(io.BytesIO(data))
    assert image.size == (300, 150)


async def test_stub_provider_handles_empty_prompt_without_crashing():
    data = await StubImageProvider().generate("", size="128x128")
    assert data.startswith(PNG_SIGNATURE)


@pytest.mark.parametrize(
    ("size", "esperado"),
    [
        ("1024x1024", (1024, 1024)),
        ("64x64", (64, 64)),
        ("128X256", (128, 256)),  # mayúsculas también
        ("999999x10", (2048, 64)),  # se acota a [64, 2048]
        ("no-es-un-tamaño", (1024, 1024)),  # formato inválido -> default
        ("", (1024, 1024)),  # vacío -> default
    ],
)
def test_parse_size_clamps_and_falls_back_to_default(size: str, esperado: tuple[int, int]):
    assert _parse_size(size) == esperado


# --- OpenAICompatImagesProvider ----------------------------------------------------


@respx.mock
async def test_openai_compat_provider_sends_expected_request_and_decodes_b64():
    fake_png = b"fake-png-bytes"
    b64 = base64.b64encode(fake_png).decode("ascii")
    route = respx.post(_IMAGES_URL).mock(
        return_value=httpx.Response(200, json={"data": [{"b64_json": b64}]})
    )

    provider = OpenAICompatImagesProvider(
        base_url="https://images.example.com/v1", api_key="fake-key", model="fake-model"
    )
    data = await provider.generate("un perro con lentes", size="512x512")

    assert data == fake_png
    assert route.called
    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer fake-key"
    assert json.loads(request.content) == {
        "model": "fake-model",
        "prompt": "un perro con lentes",
        "size": "512x512",
    }


@respx.mock
async def test_openai_compat_provider_raises_value_error_on_missing_b64():
    respx.post(_IMAGES_URL).mock(return_value=httpx.Response(200, json={"data": [{}]}))
    provider = OpenAICompatImagesProvider(
        base_url="https://images.example.com/v1", api_key="k", model="m"
    )
    with pytest.raises(ValueError, match="b64_json"):
        await provider.generate("algo")


@respx.mock
async def test_openai_compat_provider_raises_on_http_error():
    respx.post(_IMAGES_URL).mock(
        return_value=httpx.Response(
            400,
            headers={"x-request-id": "req_image_123"},
            json={
                "error": {
                    "message": "Unknown parameter: response_format",
                    "code": "unknown_parameter",
                    "param": "response_format",
                }
            },
        )
    )
    provider = OpenAICompatImagesProvider(
        base_url="https://images.example.com/v1", api_key="k", model="m"
    )
    with pytest.raises(ImageProviderRequestError) as captured:
        await provider.generate("algo")
    error = captured.value
    assert error.status_code == 400
    assert error.code == "unknown_parameter"
    assert error.param == "response_format"
    assert error.request_id == "req_image_123"
    assert "Unknown parameter" in str(error)


@respx.mock
async def test_openai_compat_provider_recovers_from_rejected_social_size():
    source = Image.new("RGB", (1536, 1024), color=(20, 40, 80))
    buffer = io.BytesIO()
    source.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    route = respx.post(_IMAGES_URL).mock(
        side_effect=[
            httpx.Response(
                400,
                json={"error": {"message": "Invalid size", "param": "size"}},
            ),
            httpx.Response(200, json={"data": [{"b64_json": encoded}]}),
        ]
    )
    provider = OpenAICompatImagesProvider(
        base_url="https://images.example.com/v1",
        api_key="fake-key",
        model="fake-model",
    )

    result = await provider.generate("escena editorial", size="1200x627")

    assert route.call_count == 2
    assert json.loads(route.calls[0].request.content)["size"] == "1200x627"
    assert json.loads(route.calls[1].request.content)["size"] == "1536x1024"
    assert Image.open(io.BytesIO(result)).size == (1200, 627)


@respx.mock
async def test_openai_compat_provider_never_reflects_a_secret_from_upstream_error():
    respx.post(_IMAGES_URL).mock(
        return_value=httpx.Response(
            400,
            json={"error": {"message": "Rejected Bearer secret-value-123456789"}},
        )
    )
    provider = OpenAICompatImagesProvider(
        base_url="https://images.example.com/v1", api_key="k", model="m"
    )
    with pytest.raises(ImageProviderRequestError) as captured:
        await provider.generate("algo")
    assert "secret-value-123456789" not in str(captured.value)
    assert "[credencial protegida]" in str(captured.value)


# --- get_image_provider -------------------------------------------------------------


def test_get_image_provider_defaults_to_stub_when_settings_is_empty(fake_settings):
    assert isinstance(get_image_provider(fake_settings()), StubImageProvider)


def test_get_image_provider_falls_back_to_stub_without_full_openai_compat_config(fake_settings):
    settings = fake_settings(IMAGES_PROVIDER="openai_compat")  # falta base_url/api_key/model
    assert isinstance(get_image_provider(settings), StubImageProvider)


def test_get_image_provider_falls_back_to_stub_on_unknown_provider(fake_settings):
    settings = fake_settings(IMAGES_PROVIDER="dall-e-en-la-luna")
    assert isinstance(get_image_provider(settings), StubImageProvider)


def test_get_image_provider_returns_openai_compat_when_fully_configured(fake_settings):
    settings = fake_settings(
        IMAGES_PROVIDER="openai_compat",
        IMAGES_BASE_URL="https://images.example.com/v1",
        IMAGES_API_KEY="key",
        IMAGES_MODEL="model",
    )
    assert isinstance(get_image_provider(settings), OpenAICompatImagesProvider)


# --- get_tenant_image_provider (bring-your-own, auditoría "riesgo-legal-tos") -------
#
# Desde la corrección de diseño de `DIRECCION_ACTUAL.md` ("nunca una llave
# compartida de plataforma"), TODAS las ramas de fallback de
# `get_tenant_image_provider` caen DIRECTO a `StubImageProvider` — nunca a
# `get_image_provider(ctx.settings)` — mismo criterio "tenant → stub" que ya
# sigue `apps/api/edecan_api/routers/voice.py::_stt_para_tenant`. Por eso cada
# `fake_settings(...)` de esta sección deja configurado un
# `IMAGES_PROVIDER="openai_compat"` COMPLETO y válido a propósito: si alguna
# rama volviera a consultarlo (regresión al comportamiento viejo), el test
# fallaría al ver un `OpenAICompatImagesProvider` de PLATAFORMA en vez de un
# `StubImageProvider`.


def _fake_settings_plataforma_completa(fake_settings):
    """`IMAGES_PROVIDER=openai_compat` totalmente configurado y válido — para
    probar que `get_tenant_image_provider` JAMÁS lo usa como fallback."""
    return fake_settings(
        IMAGES_PROVIDER="openai_compat",
        IMAGES_BASE_URL="https://images.plataforma.example.com/v1",
        IMAGES_API_KEY="clave-de-plataforma",
        IMAGES_MODEL="modelo-de-plataforma",
    )


async def test_get_tenant_image_provider_sin_vault_cae_a_stub(make_ctx, fake_settings):
    """`ctx.vault is None` (default de `make_ctx`, ver conftest) — nunca cae
    a `get_image_provider(ctx.settings)`, aunque la plataforma esté
    perfectamente configurada."""
    ctx = make_ctx(settings=_fake_settings_plataforma_completa(fake_settings))
    provider = await get_tenant_image_provider(ctx)
    assert isinstance(provider, StubImageProvider)


async def test_get_tenant_image_provider_sin_cuenta_conectada_cae_a_stub(
    make_ctx, make_session, make_vault, fake_settings, caplog
):
    """El tenant nunca hizo `PUT /v1/credentials/images`: la consulta a
    `connector_accounts` no devuelve filas — cae directo a stub, NUNCA a la
    config de plataforma (aunque esté completa y válida: regresión directa
    del fix, antes de él esto habría devuelto un `OpenAICompatImagesProvider`
    de plataforma), y avisa por log cómo conectar una credencial propia."""
    ctx = make_ctx(
        settings=_fake_settings_plataforma_completa(fake_settings),
        session=make_session([[]]),
        vault=make_vault(),
    )
    with caplog.at_level("WARNING"):
        provider = await get_tenant_image_provider(ctx)
    assert isinstance(provider, StubImageProvider)
    assert "PUT /v1/credentials/images" in caplog.text


async def test_get_tenant_image_provider_vault_revienta_cae_a_stub_nunca_plataforma(
    make_ctx, make_session, fake_settings, caplog
):
    """El tenant SÍ tiene una cuenta conectada, pero leer el vault revienta
    (vault caído) — cae a stub, JAMÁS a `get_image_provider(ctx.settings)`
    aunque la plataforma esté perfectamente configurada."""

    class _VaultQueRevienta:
        async def get(self, tenant_id: Any, connector_account_id: Any) -> Any:
            raise RuntimeError("vault caído")

    ctx = make_ctx(
        settings=_fake_settings_plataforma_completa(fake_settings),
        session=make_session([[{"id": "11111111-1111-1111-1111-111111111111"}]]),
        vault=_VaultQueRevienta(),
    )
    with caplog.at_level("WARNING"):
        provider = await get_tenant_image_provider(ctx)
    assert isinstance(provider, StubImageProvider)
    assert "PUT /v1/credentials/images" in caplog.text


async def test_get_tenant_image_provider_usa_la_credencial_del_tenant(
    make_ctx, make_session, make_vault, fake_settings
):
    """El tenant SÍ conectó su propia credencial — se usa esa. `settings` trae
    una config de plataforma completa y válida a propósito (ver
    `_fake_settings_plataforma_completa`): si el resultado fuera el
    `OpenAICompatImagesProvider` de plataforma en vez del del tenant, este
    test lo detectaría por los valores (`base_url`/`api_key` de plataforma
    vs. del tenant no se comparan directo, pero la rama tomada sí: nunca pasa
    por `get_image_provider`)."""
    cuenta_id = "11111111-1111-1111-1111-111111111111"
    session = make_session([[{"id": cuenta_id}]])
    bundle = SimpleNamespace(
        access_token=json.dumps(
            {
                "base_url": "https://images.example.com/v1",
                "api_key": "clave-del-tenant",
                "model": "modelo-del-tenant",
            }
        )
    )
    vault = make_vault(bundle=bundle)
    ctx = make_ctx(
        settings=_fake_settings_plataforma_completa(fake_settings), session=session, vault=vault
    )

    provider = await get_tenant_image_provider(ctx)

    assert isinstance(provider, OpenAICompatImagesProvider)
    # La consulta filtró por el connector_key correcto.
    assert session.llamadas[0][1]["connector_key"] == IMAGES_CONNECTOR_KEY
    # `vault.get` se llamó con el id de cuenta que devolvió la consulta.
    assert vault.llamadas == [(ctx.tenant_id, cuenta_id)]


async def test_get_tenant_image_provider_bundle_vacio_cae_a_stub(
    make_ctx, make_session, make_vault, fake_settings
):
    """La cuenta existe pero `vault.get` no devuelve nada (p. ej. una fila a
    medio escribir) — se degrada a stub sin reventar, nunca a plataforma."""
    ctx = make_ctx(
        settings=_fake_settings_plataforma_completa(fake_settings),
        session=make_session([[{"id": "11111111-1111-1111-1111-111111111111"}]]),
        vault=make_vault(bundle=None),
    )
    provider = await get_tenant_image_provider(ctx)
    assert isinstance(provider, StubImageProvider)


async def test_get_tenant_image_provider_json_corrupto_cae_a_stub(
    make_ctx, make_session, make_vault, fake_settings
):
    """Config ilegible en el vault: se trata igual que "el tenant no conectó
    nada" (mismo criterio que `_read_config` en `credentials.py`), nunca
    revienta `generar_imagen` y nunca cae a plataforma."""
    ctx = make_ctx(
        settings=_fake_settings_plataforma_completa(fake_settings),
        session=make_session([[{"id": "11111111-1111-1111-1111-111111111111"}]]),
        vault=make_vault(bundle=SimpleNamespace(access_token="esto no es JSON")),
    )
    provider = await get_tenant_image_provider(ctx)
    assert isinstance(provider, StubImageProvider)


async def test_get_tenant_image_provider_campos_incompletos_cae_a_stub(
    make_ctx, make_session, make_vault, fake_settings
):
    """Falta `model` en la config guardada — se trata como "sin credencial
    utilizable" en vez de construir un `OpenAICompatImagesProvider` roto, y
    nunca cae a plataforma."""
    bundle = SimpleNamespace(
        access_token=json.dumps({"base_url": "https://images.example.com/v1", "api_key": "k"})
    )
    ctx = make_ctx(
        settings=_fake_settings_plataforma_completa(fake_settings),
        session=make_session([[{"id": "11111111-1111-1111-1111-111111111111"}]]),
        vault=make_vault(bundle=bundle),
    )
    provider = await get_tenant_image_provider(ctx)
    assert isinstance(provider, StubImageProvider)
