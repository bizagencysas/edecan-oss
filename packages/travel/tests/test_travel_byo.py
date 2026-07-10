"""Regresión anti-fuga bring-your-own dedicada de `edecan_travel` (BARRIDO A,
WP-V7-02 — ver `docs/cumplimiento/barrido-v7-viajes.md`). Mismo patrón EXACTO que
`packages/voice/tests/test_voice_byo.py` (ver su docstring, y
`packages/smarthome/tests/test_byo_casa.py` para el patrón de `ctx.settings`
"veneno"): construye con una "clave de plataforma" centinela presente en el entorno,
y prueba — con la request HTTP real capturada por `respx` cuando aplica — que esa
credencial centinela NUNCA llega a la petición ni al resultado.

## Veredicto de la lectura línea por línea (BARRIDO A completo)

`amadeus.py`, `tracking.py`, `providers.py`, `tools.py` y
`apps/api/edecan_api/routers/viajes.py`: **LIMPIO**. A diferencia de
`edecan_voice.polly` (v5, corregido: un SDK de terceros — `aioboto3` — que resolvía
la cadena de credenciales AWS *ambiente* del proceso por dentro, invisible a un grep
de `self._settings`/`os.environ`), acá no hay NINGÚN SDK de terceros involucrado —
`AmadeusClient`/`AfterShipClient` son clientes `httpx.AsyncClient` puros, con la
credencial pasada SIEMPRE por el constructor. Aplicando la pregunta correcta que deja
`HOTFIXES_PENDIENTES.md` ("¿este proveedor tiene AL MENOS UN campo de credencial que
el tenant deba traer, y qué pasa si llega vacío?"): sí — `api_key`/`api_secret`
(Amadeus) y `api_key` (AfterShip) — y la respuesta es que un campo vacío/faltante
SIEMPRE degrada a `StubTravelProvider`/`StubTrackingProvider`
(`packages/travel/tests/test_providers.py` ya cubre exhaustivamente esos casos);
jamás existe un nivel intermedio de "credencial de plataforma" al que algo pueda
caer, porque no existe ningún campo `AMADEUS_*`/`AFTERSHIP_*` en
`edecan_api.config.Settings`/`.env.example` (verificado en
`apps/api/tests/test_v7_sweep_viajes.py::
test_settings_no_declara_ningun_campo_amadeus_ni_aftership` — este paquete no
declara `edecan-api` como dependencia, así que esa aserción vive del lado que sí
puede importarlo, siguiendo `ARCHITECTURE.md` §10.1 "los tests NO importan paquetes
hermanos").
"""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

import httpx
import respx
from edecan_travel.amadeus import AMADEUS_TEST_BASE_URL, AmadeusClient
from edecan_travel.providers import (
    StubTrackingProvider,
    StubTravelProvider,
    get_tenant_tracking_provider,
    get_tenant_travel_provider,
)
from edecan_travel.tracking import AFTERSHIP_BASE_URL, AfterShipClient

_SENTINEL = "FUGA_DE_PLATAFORMA_NO_DEBE_APARECER"


# ---------------------------------------------------------------------------
# Firma: ni AmadeusClient ni AfterShipClient aceptan "settings", ni ningún parámetro
# tipo "session ambiente" (a diferencia de PollyTTS, ver test_voice_byo.py).
# ---------------------------------------------------------------------------


def test_amadeus_client_no_acepta_settings_en_su_constructor():
    parametros = set(inspect.signature(AmadeusClient.__init__).parameters)
    assert "settings" not in parametros


def test_aftership_client_no_acepta_settings_en_su_constructor():
    parametros = set(inspect.signature(AfterShipClient.__init__).parameters)
    assert "settings" not in parametros


def test_amadeus_client_solo_tiene_parametros_de_credencial_y_config_explicita():
    """Los ÚNICOS parámetros configurables son la credencial del tenant + config no
    secreta (`environment`) + inyección de transporte para tests (`http_client`,
    `timeout`) — nunca un objeto tipo `session`/`boto3` que pudiera resolver una
    identidad ambiente por su cuenta."""
    parametros = set(inspect.signature(AmadeusClient.__init__).parameters) - {"self"}
    assert parametros == {"api_key", "api_secret", "environment", "http_client", "timeout"}


def test_aftership_client_solo_tiene_parametros_de_credencial_y_config_explicita():
    parametros = set(inspect.signature(AfterShipClient.__init__).parameters) - {"self"}
    assert parametros == {"api_key", "http_client", "timeout"}


# ---------------------------------------------------------------------------
# Request real (respx): con una "clave de plataforma" centinela presente en el
# entorno, la petición HTTP real solo debe llevar la credencial pasada
# EXPLÍCITAMENTE al constructor -- nunca el centinela. Estructuralmente ninguno de
# los dos clientes lee el entorno (confirmado arriba por firma), pero se prueba el
# comportamiento observable de todos modos, mismo criterio que
# packages/voice/tests/test_voice_byo.py.
# ---------------------------------------------------------------------------


@respx.mock
async def test_amadeus_get_token_no_filtra_variables_de_entorno(monkeypatch):
    monkeypatch.setenv("AMADEUS_API_KEY", _SENTINEL)
    monkeypatch.setenv("AMADEUS_API_SECRET", _SENTINEL)
    ruta = respx.post(f"{AMADEUS_TEST_BASE_URL}/v1/security/oauth2/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 1799})
    )

    client = AmadeusClient("key-del-tenant", "secret-del-tenant")
    await client._get_token()  # noqa: SLF001 - test interno, mismo patrón que test_amadeus.py

    enviado = ruta.calls.last.request.content.decode()
    assert "client_id=key-del-tenant" in enviado
    assert "client_secret=secret-del-tenant" in enviado
    assert _SENTINEL not in enviado


@respx.mock
async def test_amadeus_dos_tenants_seguidos_nunca_mezclan_credenciales(monkeypatch):
    """Dos resoluciones seguidas, cada una con SU PROPIA credencial (simula dos
    tenants distintos resueltos en la misma ventana de tiempo por
    `get_tenant_travel_provider`): cada request lleva SOLO la credencial de ESE
    tenant, nunca la del otro ni la del entorno."""
    monkeypatch.setenv("AMADEUS_API_KEY", _SENTINEL)
    monkeypatch.setenv("AMADEUS_API_SECRET", _SENTINEL)
    ruta = respx.post(f"{AMADEUS_TEST_BASE_URL}/v1/security/oauth2/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 1799})
    )

    await AmadeusClient("key-tenant-A", "secret-tenant-A")._get_token()  # noqa: SLF001
    await AmadeusClient("key-tenant-B", "secret-tenant-B")._get_token()  # noqa: SLF001

    enviados = [call.request.content.decode() for call in ruta.calls]
    assert "client_id=key-tenant-A" in enviados[0]
    assert "client_id=key-tenant-B" in enviados[1]
    assert "tenant-A" not in enviados[1]
    assert "tenant-B" not in enviados[0]
    assert all(_SENTINEL not in e for e in enviados)


@respx.mock
async def test_aftership_listar_couriers_no_filtra_variables_de_entorno(monkeypatch):
    monkeypatch.setenv("AFTERSHIP_API_KEY", _SENTINEL)
    ruta = respx.get(f"{AFTERSHIP_BASE_URL}/couriers").mock(
        return_value=httpx.Response(200, json={"data": {"couriers": []}})
    )

    client = AfterShipClient("key-del-tenant")
    await client.listar_couriers()

    header_enviado = ruta.calls.last.request.headers["as-api-key"]
    assert header_enviado == "key-del-tenant"
    assert _SENTINEL not in header_enviado


@respx.mock
async def test_aftership_dos_tenants_seguidos_nunca_mezclan_api_keys(monkeypatch):
    monkeypatch.setenv("AFTERSHIP_API_KEY", _SENTINEL)
    ruta = respx.get(f"{AFTERSHIP_BASE_URL}/couriers").mock(
        return_value=httpx.Response(200, json={"data": {"couriers": []}})
    )

    await AfterShipClient("key-tenant-A").listar_couriers()
    await AfterShipClient("key-tenant-B").listar_couriers()

    headers_enviados = [call.request.headers["as-api-key"] for call in ruta.calls]
    assert headers_enviados == ["key-tenant-A", "key-tenant-B"]


def test_construir_clientes_con_env_sentinela_no_cambia_la_credencial_usada(monkeypatch):
    """Guardrail final (mismo que `test_os_environ_sentinel_no_se_filtra_por_accidente_
    via_getenv` de `test_voice_byo.py`): fijar las variables de entorno típicas de
    plataforma no cambia en nada la credencial con la que queda construido el cliente."""
    monkeypatch.setenv("AMADEUS_API_KEY", _SENTINEL)
    monkeypatch.setenv("AMADEUS_API_SECRET", _SENTINEL)
    monkeypatch.setenv("AFTERSHIP_API_KEY", _SENTINEL)

    amadeus = AmadeusClient("clave-tenant", "secreto-tenant")
    aftership = AfterShipClient("clave-tenant-aftership")

    assert amadeus._api_key == "clave-tenant"  # noqa: SLF001
    assert amadeus._api_secret == "secreto-tenant"  # noqa: SLF001
    assert aftership._api_key == "clave-tenant-aftership"  # noqa: SLF001


# ---------------------------------------------------------------------------
# ctx.settings "veneno": get_tenant_travel_provider/get_tenant_tracking_provider NO
# deben tocar NINGÚN atributo de `ctx.settings` -- ni siquiera un campo NO-secreto
# (a diferencia de `edecan_smarthome._cliente_desde_vault`, que sí lee
# `HOMEASSISTANT_TIMEOUT_SECONDS` legítimamente; acá no hay ningún campo de
# `settings` que este resolver necesite, ver docstring del módulo `providers.py`).
# Se ejercita el camino MÁS PROFUNDO posible (bundle real completo -> construye un
# AmadeusClient/AfterShipClient) para que la aserción cubra cada línea de la función,
# no solo el atajo temprano de "sin vault".
# ---------------------------------------------------------------------------


class _SettingsVeneno:
    """Revienta ante CUALQUIER acceso a atributo."""

    def __getattr__(self, name: str):
        raise AssertionError(
            f"get_tenant_travel_provider/get_tenant_tracking_provider leyó "
            f"ctx.settings.{name} -- este resolver no debe tocar `settings` en absoluto "
            "(no existe ningún AMADEUS_*/AFTERSHIP_* de plataforma que pudiera justificarlo)."
        )


async def test_get_tenant_travel_provider_nunca_toca_ctx_settings_con_credencial_real(
    make_ctx, make_session, make_vault
):
    bundle = SimpleNamespace(
        access_token=json.dumps({"api_key": "k", "api_secret": "s", "environment": "test"})
    )
    ctx = make_ctx(
        session=make_session([[{"id": "11111111-1111-1111-1111-111111111111"}]]),
        vault=make_vault(bundle=bundle),
        settings=_SettingsVeneno(),
    )

    provider = await get_tenant_travel_provider(ctx)

    assert isinstance(provider, AmadeusClient)


async def test_get_tenant_travel_provider_nunca_toca_ctx_settings_sin_cuenta_conectada(
    make_ctx, make_session
):
    ctx = make_ctx(session=make_session([[]]), settings=_SettingsVeneno())
    provider = await get_tenant_travel_provider(ctx)
    assert isinstance(provider, StubTravelProvider)


async def test_get_tenant_tracking_provider_nunca_toca_ctx_settings_con_credencial_real(
    make_ctx, make_session, make_vault
):
    bundle = SimpleNamespace(access_token=json.dumps({"api_key": "k"}))
    ctx = make_ctx(
        session=make_session([[{"id": "22222222-2222-2222-2222-222222222222"}]]),
        vault=make_vault(bundle=bundle),
        settings=_SettingsVeneno(),
    )

    provider = await get_tenant_tracking_provider(ctx)

    assert isinstance(provider, AfterShipClient)


async def test_get_tenant_tracking_provider_nunca_toca_ctx_settings_sin_cuenta_conectada(
    make_ctx, make_session
):
    ctx = make_ctx(session=make_session([[]]), settings=_SettingsVeneno())
    provider = await get_tenant_tracking_provider(ctx)
    assert isinstance(provider, StubTrackingProvider)


# ---------------------------------------------------------------------------
# Guardrail de dinero: ni AmadeusClient ni AfterShipClient exponen NUNCA un método
# de booking/pago/escritura -- ver docs/viajes.md "Guardrail de dinero".
# ---------------------------------------------------------------------------


def test_amadeus_client_solo_expone_metodos_de_busqueda_e_informacion():
    """Si alguien agrega un método nuevo a `AmadeusClient` (p. ej. `crear_reserva`),
    este test lo obliga a una decisión consciente -- actualizar este test Y
    `docs/viajes.md` -- en vez de colarse en silencio. Guardrail de dinero: ninguna
    ruta de este paquete debe llegar jamás a una API de booking/pago de Amadeus."""
    metodos_publicos = {
        nombre
        for nombre in dir(AmadeusClient)
        if not nombre.startswith("_") and callable(getattr(AmadeusClient, nombre))
    }
    assert metodos_publicos == {"buscar_vuelos", "buscar_hoteles", "estado_vuelo", "aclose"}


def test_aftership_client_solo_expone_metodos_de_solo_lectura():
    """Mismo criterio: AfterShip nunca debe ganar un método de escritura (crear/
    actualizar/borrar un tracking en la cuenta del tenant) -- ver el docstring del
    módulo `tracking.py`."""
    metodos_publicos = {
        nombre
        for nombre in dir(AfterShipClient)
        if not nombre.startswith("_") and callable(getattr(AfterShipClient, nombre))
    }
    assert metodos_publicos == {"listar_couriers", "detectar_courier", "rastrear", "aclose"}
