"""Regresión anti-fuga dedicada de `edecan_api.routers.credentials` (Barrido
de seguridad v5) — complementa `test_credentials_router.py` (que ya cubre el
comportamiento funcional completo de los 5 recursos).

Ángulo específico de este archivo: los `_ping_*` de `credentials.py`
(`_ping_anthropic`, `_ping_openai_compat`, `_ping_deepgram`, `_ping_elevenlabs`,
`_ping_brave`, `_ping_tavily`) reciben la credencial SIEMPRE desde `payload`
(lo que el tenant acaba de pegar en el formulario de "Conectar"), nunca desde
`settings` — a diferencia del bug de referencia ya corregido en v4
(`packages/llm/edecan_llm/router.py::_build_provider_from_config`, un
`config.campo or getattr(self._settings, "X", None)`). Cada test de abajo
instala una `Settings` con un valor CENTINELA "de plataforma" en el campo
correspondiente (`ANTHROPIC_API_KEY`, `OPENAI_COMPAT_API_KEY`,
`DEEPGRAM_API_KEY`, `ELEVENLABS_API_KEY`, `BRAVE_API_KEY`, `IMAGES_API_KEY`) y
verifica, inspeccionando la request real capturada por `respx`, que el
header/query que sale hacia el proveedor lleva SOLO la credencial que el
tenant pegó en el body del `PUT`, nunca el centinela.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import respx
from conftest import TEST_JWT_SECRET, auth_headers
from edecan_schemas import TokenBundle

import edecan_api.deps as edecan_deps
from edecan_api.config import Settings, get_settings

_SENTINEL = "FUGA_DE_PLATAFORMA_NO_DEBE_APARECER"


class FakeVault:
    def __init__(self) -> None:
        self.puts: list[tuple[Any, Any, TokenBundle]] = []

    async def put(self, tenant_id: Any, account_id: Any, bundle: TokenBundle) -> None:
        self.puts.append((tenant_id, account_id, bundle))

    async def get(self, tenant_id: Any, account_id: Any) -> TokenBundle | None:
        return None


def _headers(**overrides: Any) -> dict[str, str]:
    import uuid

    return auth_headers(
        user_id=overrides.pop("user_id", uuid.uuid4()),
        tenant_id=overrides.pop("tenant_id", uuid.uuid4()),
        plan_key=overrides.pop("plan_key", "hosted_pro"),
    )


def _settings_con_centinela_de_plataforma(**overrides: Any) -> Settings:
    """`Settings` con TODAS las credenciales de plataforma que este router
    podría (incorrectamente) leer si el bug de v4 reapareciera aquí, todas
    fijadas al mismo valor centinela."""
    return Settings(
        JWT_SECRET=TEST_JWT_SECRET,
        WEB_BASE_URL="http://localhost:3000",
        PUBLIC_BASE_URL="http://localhost:8000",
        ANTHROPIC_API_KEY=_SENTINEL,
        OPENAI_COMPAT_BASE_URL="https://api.plataforma-nunca-se-usa.example/v1",
        OPENAI_COMPAT_API_KEY=_SENTINEL,
        DEEPGRAM_API_KEY=_SENTINEL,
        ELEVENLABS_API_KEY=_SENTINEL,
        BRAVE_API_KEY=_SENTINEL,
        TAVILY_API_KEY=_SENTINEL,
        IMAGES_API_KEY=_SENTINEL,
        IMAGES_BASE_URL="https://images.plataforma-nunca-se-usa.example/v1",
        IMAGES_PROVIDER="openai_compat",
        IMAGES_MODEL="modelo-de-plataforma",
        **overrides,
    )


def _install(app: Any) -> FakeVault:
    fake_vault = FakeVault()
    app.dependency_overrides[edecan_deps.get_vault] = lambda: fake_vault
    # `lambda: ...()` (cero argumentos), NUNCA la función `**overrides` misma:
    # FastAPI inspecciona la FIRMA del callable de `dependency_overrides` para
    # resolverlo como si fuera otra dependencia — un `**kwargs` desnudo no es
    # introspectable y dispara un 422 "Field required" en vez de invocarla.
    app.dependency_overrides[get_settings] = lambda: _settings_con_centinela_de_plataforma()
    return fake_vault


@respx.mock
async def test_put_llm_anthropic_sin_api_key_de_plataforma_en_el_header(client, app) -> None:
    route = respx.get("https://api.anthropic.com/v1/models").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    _install(app)

    response = await client.put(
        "/v1/credentials/llm",
        json={"kind": "anthropic", "api_key": "clave-real-del-tenant"},
        headers=_headers(),
    )

    assert response.status_code == 204
    assert route.called
    header = route.calls.last.request.headers["x-api-key"]
    assert header == "clave-real-del-tenant"
    assert _SENTINEL not in header


@respx.mock
async def test_put_llm_openai_compat_va_al_base_url_del_tenant_no_al_de_plataforma(
    client, app
) -> None:
    """El tenant elige SU PROPIO `base_url` (`kind="openai_compat"`); aunque
    `Settings.OPENAI_COMPAT_BASE_URL`/`OPENAI_COMPAT_API_KEY` de plataforma
    estén configurados (con el centinela), la validación pega EXCLUSIVAMENTE
    al endpoint y con la key que trajo el tenant — este es exactamente el
    escenario del hallazgo #1 de v4 (`HOTFIXES_PENDIENTES.md`), pero en la
    validación de `credentials.py` en vez de en `LLMRouter`."""
    route = respx.get("https://servidor-del-tenant.example/v1/models").mock(
        return_value=httpx.Response(
            200, json={"data": [{"id": "modelo-del-tenant", "created": 1}]}
        )
    )
    _install(app)

    response = await client.put(
        "/v1/credentials/llm",
        json={
            "kind": "openai_compat",
            "base_url": "https://servidor-del-tenant.example/v1",
            "api_key": "clave-real-del-tenant",
        },
        headers=_headers(),
    )

    assert response.status_code == 204
    assert route.called
    header = route.calls.last.request.headers["authorization"]
    assert header == "Bearer clave-real-del-tenant"
    assert _SENTINEL not in header


@respx.mock
async def test_put_voice_stt_deepgram_no_filtra_la_clave_de_plataforma(client, app) -> None:
    route = respx.get("https://api.deepgram.com/v1/projects").mock(
        return_value=httpx.Response(200, json={"projects": []})
    )
    _install(app)

    response = await client.put(
        "/v1/credentials/voice/stt",
        json={"provider": "deepgram", "api_key": "clave-real-del-tenant"},
        headers=_headers(),
    )

    assert response.status_code == 204
    header = route.calls.last.request.headers["Authorization"]
    assert header == "Token clave-real-del-tenant"
    assert _SENTINEL not in header


@respx.mock
async def test_put_voice_tts_elevenlabs_no_filtra_la_clave_de_plataforma(client, app) -> None:
    route = respx.get("https://api.elevenlabs.io/v1/user").mock(
        return_value=httpx.Response(200, json={})
    )
    _install(app)

    response = await client.put(
        "/v1/credentials/voice/tts",
        json={
            "provider": "elevenlabs",
            "api_key": "clave-real-del-tenant",
            "voice_id": "21m00Tcm4TlvDq8ikWAM",
        },
        headers=_headers(),
    )

    assert response.status_code == 204
    header = route.calls.last.request.headers["xi-api-key"]
    assert header == "clave-real-del-tenant"
    assert _SENTINEL not in header


@respx.mock
async def test_put_images_va_al_base_url_del_tenant_no_al_de_plataforma(client, app) -> None:
    route = respx.get("https://images-del-tenant.example/v1/models").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    _install(app)

    response = await client.put(
        "/v1/credentials/images",
        json={
            "base_url": "https://images-del-tenant.example/v1",
            "api_key": "clave-real-del-tenant",
            "model": "modelo-del-tenant",
        },
        headers=_headers(),
    )

    assert response.status_code == 204
    header = route.calls.last.request.headers["authorization"]
    assert header == "Bearer clave-real-del-tenant"
    assert _SENTINEL not in header


@respx.mock
async def test_put_search_brave_no_filtra_la_clave_de_plataforma(client, app) -> None:
    route = respx.get("https://api.search.brave.com/res/v1/web/search").mock(
        return_value=httpx.Response(200, json={"web": {"results": []}})
    )
    _install(app)

    response = await client.put(
        "/v1/credentials/search",
        json={"provider": "brave", "api_key": "clave-real-del-tenant"},
        headers=_headers(),
    )

    assert response.status_code == 204
    header = route.calls.last.request.headers["X-Subscription-Token"]
    assert header == "clave-real-del-tenant"
    assert _SENTINEL not in header


@respx.mock
async def test_put_search_tavily_no_filtra_la_clave_de_plataforma(client, app) -> None:
    route = respx.post("https://api.tavily.com/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    _install(app)

    response = await client.put(
        "/v1/credentials/search",
        json={"provider": "tavily", "api_key": "clave-real-del-tenant"},
        headers=_headers(),
    )

    assert response.status_code == 204
    body = route.calls.last.request.content.decode()
    assert '"api_key": "clave-real-del-tenant"' in body or "clave-real-del-tenant" in body
    assert _SENTINEL not in body


@respx.mock
async def test_put_llm_anthropic_key_del_tenant_nunca_aparece_junto_al_base_url_de_plataforma(
    client, app
) -> None:
    """Sentinel cruzado: aunque `Settings.OPENAI_COMPAT_BASE_URL` de
    plataforma esté configurado, seleccionar `kind="anthropic"` nunca hace
    una request a ese `base_url` de plataforma — la URL golpeada es siempre
    la de Anthropic."""
    route = respx.get("https://api.anthropic.com/v1/models").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    _install(app)

    response = await client.put(
        "/v1/credentials/llm",
        json={"kind": "anthropic", "api_key": "clave-real-del-tenant"},
        headers=_headers(),
    )

    assert response.status_code == 204
    assert route.called
    url_llamada = str(route.calls.last.request.url)
    assert "plataforma-nunca-se-usa.example" not in url_llamada
    assert urlparse(url_llamada).hostname == "api.anthropic.com"
    # Sanity check adicional: ningún query param filtra el centinela tampoco.
    assert _SENTINEL not in parse_qs(urlparse(url_llamada).query).__str__()
