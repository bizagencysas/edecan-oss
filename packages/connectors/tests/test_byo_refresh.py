"""Regresión anti-fuga/anti-mezcla dedicada de `edecan_connectors` (Barrido
de seguridad v5; reescrito 2026-07-09 cuando `client_id`/`client_secret`
dejaron de leerse de variables de entorno del proceso y pasaron a ser
parámetros explícitos por llamada — ver docstring de `edecan_connectors.base`
y `apps/api/edecan_api/oauth_app_credentials.py`).

Veredicto de la auditoría (ver `docs/credenciales.md` sección "Auditoría v5"),
ahora MÁS estricto que la versión original de este archivo:

1. **Cada tenant trae su PROPIA app OAuth** (`client_id`/`client_secret`), no
   solo su propio token — a diferencia de la versión anterior de este
   archivo (donde el `client_id`/`client_secret` SÍ eran de una app
   compartida por la plataforma, patrón legítimo mientras no se mezclara
   entre tenants), ahora NO existe ninguna app compartida: cada credencial
   completa (app + token) es 100% del tenant que la pegó.
2. **Tampoco hay mezcla entre tenants**: `edecan_connectors.registry.CONNECTORS`
   sigue siendo un diccionario de instancias ÚNICAS por proceso (`{"google":
   GoogleConnector(), ...}`, ver `registry.py`) — un mismo objeto
   `GoogleConnector`/`MicrosoftConnector` atiende a TODOS los tenants
   (`apps/worker/edecan_worker/handlers/sync_connector.py` lo reutiliza tal
   cual para cada fila que refresca). Como esas clases no tienen NINGÚN
   atributo de instancia (ni `self._algo` asignado en ningún método) Y ya no
   leen NADA de variables de entorno, da igual que sea singleton: no hay
   estado global que un tenant pudiera pisarle a otro — todo entra por
   parámetro en cada llamada. Este archivo prueba las dos garantías con
   requests HTTP reales (capturadas con `respx`) usando la MISMA instancia
   para dos tenants "seguidos", cada uno con su PROPIO `client_id`/
   `client_secret`/`refresh_token`.
"""

from __future__ import annotations

from urllib.parse import parse_qsl

import httpx
import respx
from edecan_connectors.microsoft.connector import MicrosoftConnector
from edecan_connectors.registry import CONNECTORS

_SENTINEL = "FUGA_DE_PLATAFORMA_NO_DEBE_APARECER"


class _FakeBundle:
    def __init__(self, refresh_token: str, scopes: list[str] | None = None) -> None:
        self.access_token = "access-viejo"
        self.refresh_token = refresh_token
        self.scopes = scopes or []
        self.expires_at = None
        self.token_type = "bearer"


def test_connectors_registry_usa_instancias_singleton_sin_estado():
    """`CONNECTORS["google"]`/`["microsoft"]` son objetos compartidos por
    TODOS los tenants del proceso — solo es seguro porque no tienen ningún
    atributo de instancia (`vars(...)` vacío) NI leen configuración global:
    nada que un tenant pudiera dejarle pisado al siguiente."""
    assert vars(CONNECTORS["google"]) == {}
    assert vars(CONNECTORS["microsoft"]) == {}
    assert CONNECTORS["google"] is CONNECTORS["google"]  # mismo objeto en cada acceso


@respx.mock
async def test_google_connector_refresh_dos_tenants_seguidos_nunca_mezcla_credenciales():
    """La MISMA instancia `GoogleConnector` (como la usa `sync_connector.py`)
    refresca dos tenants seguidos — cada request lleva SOLO el `client_id`/
    `client_secret`/`refresh_token` de ESE tenant, nunca los del otro."""
    route = respx.post("https://oauth2.googleapis.com/token").mock(
        side_effect=[
            httpx.Response(200, json={"access_token": "nuevo-A", "expires_in": 3600}),
            httpx.Response(200, json={"access_token": "nuevo-B", "expires_in": 3600}),
        ]
    )
    connector = CONNECTORS["google"]
    bundle_a = _FakeBundle(refresh_token="refresh-tenant-A")
    bundle_b = _FakeBundle(refresh_token="refresh-tenant-B")

    async with httpx.AsyncClient() as http:
        refreshed_a = await connector.refresh(
            bundle_a, http, client_id="client-id-tenant-A", client_secret="client-secret-tenant-A"
        )
        refreshed_b = await connector.refresh(
            bundle_b, http, client_id="client-id-tenant-B", client_secret="client-secret-tenant-B"
        )

    assert refreshed_a.access_token == "nuevo-A"
    assert refreshed_b.access_token == "nuevo-B"
    enviados = [dict(parse_qsl(call.request.content.decode())) for call in route.calls]
    assert enviados[0]["refresh_token"] == "refresh-tenant-A"
    assert enviados[0]["client_id"] == "client-id-tenant-A"
    assert enviados[0]["client_secret"] == "client-secret-tenant-A"
    assert enviados[1]["refresh_token"] == "refresh-tenant-B"
    assert enviados[1]["client_id"] == "client-id-tenant-B"
    assert enviados[1]["client_secret"] == "client-secret-tenant-B"
    # Ninguna request del tenant A lleva ninguna credencial del tenant B ni viceversa.
    assert enviados[0]["refresh_token"] != enviados[1]["refresh_token"]
    assert enviados[0]["client_id"] != enviados[1]["client_id"]
    assert enviados[0]["client_secret"] != enviados[1]["client_secret"]


@respx.mock
async def test_microsoft_connector_refresh_dos_tenants_seguidos_nunca_mezcla_credenciales():
    route = respx.post("https://login.microsoftonline.com/common/oauth2/v2.0/token").mock(
        side_effect=[
            httpx.Response(200, json={"access_token": "nuevo-A", "expires_in": 3600}),
            httpx.Response(200, json={"access_token": "nuevo-B", "expires_in": 3600}),
        ]
    )
    connector = CONNECTORS["microsoft"]
    bundle_a = _FakeBundle(refresh_token="refresh-tenant-A")
    bundle_b = _FakeBundle(refresh_token="refresh-tenant-B")

    async with httpx.AsyncClient() as http:
        refreshed_a = await connector.refresh(
            bundle_a, http, client_id="client-id-tenant-A", client_secret="client-secret-tenant-A"
        )
        refreshed_b = await connector.refresh(
            bundle_b, http, client_id="client-id-tenant-B", client_secret="client-secret-tenant-B"
        )

    assert refreshed_a.access_token == "nuevo-A"
    assert refreshed_b.access_token == "nuevo-B"
    enviados = [dict(parse_qsl(call.request.content.decode())) for call in route.calls]
    assert enviados[0]["refresh_token"] == "refresh-tenant-A"
    assert enviados[0]["client_id"] == "client-id-tenant-A"
    assert enviados[1]["refresh_token"] == "refresh-tenant-B"
    assert enviados[1]["client_id"] == "client-id-tenant-B"


def test_microsoft_connector_no_tiene_ningun_atributo_de_instancia_mutable():
    connector = MicrosoftConnector()
    assert vars(connector) == {}
