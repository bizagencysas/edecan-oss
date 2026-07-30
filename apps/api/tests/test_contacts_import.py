"""`/v1/contacts/import/*` — Google (People API, reusa el conector OAuth `google`)
e iCloud (CardDAV) — ver docstring de `edecan_api.routers.contacts`.

`FakeVault` local (duplicada a propósito, ARCHITECTURE.md §10.1): a diferencia
del patrón single-bundle que basta en `test_ads_router.py`/`test_finance.py`
(una sola credencial por tenant), acá conviven DOS credenciales distintas por
tenant desde que `authorize`/`callback`/el refresh perezoso de Google exigen
la app OAuth propia del tenant (`google__app_config`, ver
`edecan_api.oauth_app_credentials`) ADEMÁS del token de la cuenta ya
conectada (`google`) -- así que el fake queda keyed por `(tenant_id,
account_id)`, igual que `edecan_db.vault.TokenVault` real. `.bundle` se deja
como alias de compatibilidad (el último `put()`), suficiente para las
aserciones existentes que solo revisan "la credencial más reciente".
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import respx
from conftest import auth_headers
from edecan_schemas import TokenBundle
from httpx import Response

from edecan_api import deps as edecan_deps

_PEOPLE_API_BASE = "https://people.googleapis.com/v1"
_ICLOUD_BASE = "https://contacts.icloud.com"


@dataclass
class FakeVault:
    store: dict[tuple[uuid.UUID, uuid.UUID], TokenBundle] = field(default_factory=dict)
    puts: list[tuple[uuid.UUID, uuid.UUID, TokenBundle]] = field(default_factory=list)
    bundle: TokenBundle | None = None  # alias de compatibilidad: el último put()

    async def put(self, tenant_id: uuid.UUID, account_id: uuid.UUID, bundle: TokenBundle) -> None:
        self.store[(tenant_id, account_id)] = bundle
        self.puts.append((tenant_id, account_id, bundle))
        self.bundle = bundle

    async def get(self, tenant_id: uuid.UUID, account_id: uuid.UUID) -> TokenBundle | None:
        return self.store.get((tenant_id, account_id))


def _with_vault(app) -> FakeVault:
    fake = FakeVault()
    app.dependency_overrides[edecan_deps.get_vault] = lambda: fake
    return fake


# ---------------------------------------------------------------------------
# POST /v1/contacts/import/google
# ---------------------------------------------------------------------------


async def _conectar_google(
    fake_repo, fake_vault: FakeVault, tenant_id: uuid.UUID, *, expires_at=None
):
    account = await fake_repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key="google",
        external_account_id="me",
        display_name="Google",
        scopes=["contacts.readonly"],
    )
    bundle = TokenBundle(
        access_token="access-token-vieja",
        refresh_token="refresh-token",
        expires_at=expires_at,
        scopes=["contacts.readonly"],
    )
    await fake_vault.put(tenant_id, account["id"], bundle)
    return account


async def _conectar_google_app(fake_repo, fake_vault: FakeVault, tenant_id: uuid.UUID):
    """Simula que el tenant ya pegó su propia app OAuth de Google (`PUT
    /v1/connectors/google/app-credentials`) -- desde que el refresh perezoso
    de `import_contacts_google` empezó a exigirla (ver docstring del
    módulo), cualquier test que fuerce ese refresh necesita esto además de
    `_conectar_google`."""
    account = await fake_repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key="google__app_config",
        external_account_id="google-client-id-test",
        display_name="App OAuth propia de Google",
        scopes=[],
    )
    await fake_vault.put(
        tenant_id,
        account["id"],
        TokenBundle(
            access_token="google-client-secret-test",
            token_type="oauth_app_secret",
            scopes=["google"],
        ),
    )
    return account


async def test_import_google_requires_authentication(client) -> None:
    response = await client.post("/v1/contacts/import/google")
    assert response.status_code == 401


async def test_import_google_sin_cuenta_conectada_devuelve_400(client, app) -> None:
    _with_vault(app)
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.post("/v1/contacts/import/google", headers=headers)
    assert response.status_code == 400


def _google_person(resource_name: str, nombre: str, *, email: str = "", telefono: str = "") -> dict:
    persona: dict = {"resourceName": resource_name, "names": [{"displayName": nombre}]}
    if email:
        persona["emailAddresses"] = [{"value": email}]
    if telefono:
        persona["phoneNumbers"] = [{"value": telefono}]
    return persona


@respx.mock
async def test_import_google_crea_contactos_nuevos(client, app, fake_repo) -> None:
    fake_vault = _with_vault(app)
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    await _conectar_google(
        fake_repo, fake_vault, tenant_id, expires_at=datetime.now(UTC) + timedelta(hours=1)
    )

    respx.get(f"{_PEOPLE_API_BASE}/people/me/connections").mock(
        return_value=Response(
            200,
            json={
                "connections": [
                    _google_person("people/c1", "Juan Pérez", email="juan@example.com"),
                    _google_person("people/c2", "Ana López", telefono="+1234567890"),
                ]
            },
        )
    )

    response = await client.post("/v1/contacts/import/google", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"importados": 2, "total_google": 2}

    listed = await client.get("/v1/contacts", headers=headers)
    assert {c["nombre"] for c in listed.json()} == {"Juan Pérez", "Ana López"}


@respx.mock
async def test_import_google_no_duplica_en_segunda_corrida(client, app, fake_repo) -> None:
    fake_vault = _with_vault(app)
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    await _conectar_google(
        fake_repo, fake_vault, tenant_id, expires_at=datetime.now(UTC) + timedelta(hours=1)
    )
    respx.get(f"{_PEOPLE_API_BASE}/people/me/connections").mock(
        return_value=Response(
            200, json={"connections": [_google_person("people/c1", "Juan Pérez")]}
        )
    )

    primera = await client.post("/v1/contacts/import/google", headers=headers)
    segunda = await client.post("/v1/contacts/import/google", headers=headers)

    assert primera.json()["importados"] == 1
    assert segunda.json()["importados"] == 0
    listed = await client.get("/v1/contacts", headers=headers)
    assert len(listed.json()) == 1


@respx.mock
async def test_import_google_ignora_personas_sin_nombre(client, app, fake_repo) -> None:
    fake_vault = _with_vault(app)
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    await _conectar_google(
        fake_repo, fake_vault, tenant_id, expires_at=datetime.now(UTC) + timedelta(hours=1)
    )
    respx.get(f"{_PEOPLE_API_BASE}/people/me/connections").mock(
        return_value=Response(200, json={"connections": [{"resourceName": "people/c1"}]})
    )

    response = await client.post("/v1/contacts/import/google", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"importados": 0, "total_google": 1}


@respx.mock
async def test_import_google_refresca_token_expirado(client, app, fake_repo) -> None:
    fake_vault = _with_vault(app)
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    await _conectar_google_app(fake_repo, fake_vault, tenant_id)
    await _conectar_google(
        fake_repo, fake_vault, tenant_id, expires_at=datetime.now(UTC) - timedelta(minutes=1)
    )

    respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=Response(
            200,
            json={
                "access_token": "access-token-nueva",
                "expires_in": 3600,
                "token_type": "Bearer",
            },
        )
    )
    ruta_people = respx.get(f"{_PEOPLE_API_BASE}/people/me/connections").mock(
        return_value=Response(200, json={"connections": []})
    )

    response = await client.post("/v1/contacts/import/google", headers=headers)

    assert response.status_code == 200
    assert ruta_people.calls.last.request.headers["Authorization"] == "Bearer access-token-nueva"
    assert fake_vault.bundle.access_token == "access-token-nueva"


@respx.mock
async def test_import_google_rechaza_error_de_google(client, app, fake_repo) -> None:
    fake_vault = _with_vault(app)
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    await _conectar_google(
        fake_repo, fake_vault, tenant_id, expires_at=datetime.now(UTC) + timedelta(hours=1)
    )
    respx.get(f"{_PEOPLE_API_BASE}/people/me/connections").mock(return_value=Response(403))

    response = await client.post("/v1/contacts/import/google", headers=headers)
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# PUT/DELETE/GET /v1/contacts/import/icloud/credentials
# ---------------------------------------------------------------------------

_PRINCIPAL_XML = """<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:">
  <D:response>
    <D:href>/</D:href>
    <D:propstat>
      <D:prop><D:current-user-principal><D:href>/123/principal/</D:href></D:current-user-principal></D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>"""

_HOME_XML = """<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:carddav">
  <D:response>
    <D:href>/123/principal/</D:href>
    <D:propstat>
      <D:prop><C:addressbook-home-set><D:href>/123/carddavhome/</D:href></C:addressbook-home-set></D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>"""

_ADDRESSBOOKS_XML = """<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:carddav">
  <D:response>
    <D:href>/123/carddavhome/card/</D:href>
    <D:propstat>
      <D:prop>
        <D:resourcetype><D:collection/><C:addressbook/></D:resourcetype>
        <D:displayname>Contacts</D:displayname>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>"""


def _vcards_xml(*vcards: tuple[str, str]) -> str:
    responses = "".join(
        f"""<D:response>
    <D:href>/123/carddavhome/card/{href}.vcf</D:href>
    <D:propstat>
      <D:prop>
        <D:getetag>"etag"</D:getetag>
        <C:address-data>{data}</C:address-data>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>"""
        for href, data in vcards
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:carddav">'
        f"{responses}</D:multistatus>"
    )


def _vcard(nombre: str, uid: str, *, email: str = "") -> str:
    lineas = ["BEGIN:VCARD", "VERSION:3.0", f"FN:{nombre}"]
    if email:
        lineas.append(f"EMAIL:{email}")
    lineas.extend([f"UID:{uid}", "END:VCARD"])
    return "\n".join(lineas)


async def test_put_icloud_credentials_rechaza_vacio(client, app) -> None:
    fake_vault = _with_vault(app)
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.put(
        "/v1/contacts/import/icloud/credentials",
        json={"apple_id": "", "app_specific_password": ""},
        headers=headers,
    )
    assert response.status_code == 400
    assert fake_vault.puts == []


@respx.mock
async def test_put_icloud_credentials_valida_y_guarda(client, app) -> None:
    fake_vault = _with_vault(app)
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    respx.request("PROPFIND", f"{_ICLOUD_BASE}/").mock(
        return_value=Response(207, text=_PRINCIPAL_XML)
    )

    response = await client.put(
        "/v1/contacts/import/icloud/credentials",
        json={"apple_id": "yo@icloud.com", "app_specific_password": "abcd-efgh-ijkl-mnop"},
        headers=headers,
    )

    assert response.status_code == 204
    assert len(fake_vault.puts) == 1


@respx.mock
async def test_put_icloud_credentials_rechaza_401(client, app) -> None:
    fake_vault = _with_vault(app)
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    respx.request("PROPFIND", f"{_ICLOUD_BASE}/").mock(return_value=Response(401))

    response = await client.put(
        "/v1/contacts/import/icloud/credentials",
        json={"apple_id": "yo@icloud.com", "app_specific_password": "mala"},
        headers=headers,
    )

    assert response.status_code == 400
    assert fake_vault.puts == []


async def test_get_icloud_status_desconectado_por_defecto(client, app) -> None:
    _with_vault(app)
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.get("/v1/contacts/import/icloud/status", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"connected": False, "apple_id": None}


async def test_delete_icloud_credentials_es_idempotente(client, app) -> None:
    _with_vault(app)
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.delete("/v1/contacts/import/icloud/credentials", headers=headers)
    assert response.status_code == 204


# ---------------------------------------------------------------------------
# POST /v1/contacts/import/icloud
# ---------------------------------------------------------------------------


async def _conectar_icloud(client, app, headers) -> None:
    _with_vault(app)
    with respx.mock:
        respx.request("PROPFIND", f"{_ICLOUD_BASE}/").mock(
            return_value=Response(207, text=_PRINCIPAL_XML)
        )
        response = await client.put(
            "/v1/contacts/import/icloud/credentials",
            json={"apple_id": "yo@icloud.com", "app_specific_password": "abcd-efgh-ijkl-mnop"},
            headers=headers,
        )
        assert response.status_code == 204


async def test_import_icloud_sin_conectar_devuelve_400(client, app) -> None:
    _with_vault(app)
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.post("/v1/contacts/import/icloud", headers=headers)
    assert response.status_code == 400


@respx.mock
async def test_import_icloud_recorre_el_baile_completo_y_crea_contactos(client, app) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    await _conectar_icloud(client, app, headers)

    respx.request("PROPFIND", f"{_ICLOUD_BASE}/").mock(
        return_value=Response(207, text=_PRINCIPAL_XML)
    )
    respx.request("PROPFIND", f"{_ICLOUD_BASE}/123/principal/").mock(
        return_value=Response(207, text=_HOME_XML)
    )
    respx.request("PROPFIND", f"{_ICLOUD_BASE}/123/carddavhome/").mock(
        return_value=Response(207, text=_ADDRESSBOOKS_XML)
    )
    respx.request("REPORT", f"{_ICLOUD_BASE}/123/carddavhome/card/").mock(
        return_value=Response(
            207,
            text=_vcards_xml(
                ("juan", _vcard("Juan Pérez", "uid-1", email="juan@example.com")),
                ("ana", _vcard("Ana López", "uid-2")),
            ),
        )
    )

    response = await client.post("/v1/contacts/import/icloud", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"importados": 2, "total_icloud": 2}
    listed = await client.get("/v1/contacts", headers=headers)
    assert {c["nombre"] for c in listed.json()} == {"Juan Pérez", "Ana López"}


@respx.mock
async def test_import_icloud_no_duplica_en_segunda_corrida(client, app) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    await _conectar_icloud(client, app, headers)

    respx.request("PROPFIND", f"{_ICLOUD_BASE}/").mock(
        return_value=Response(207, text=_PRINCIPAL_XML)
    )
    respx.request("PROPFIND", f"{_ICLOUD_BASE}/123/principal/").mock(
        return_value=Response(207, text=_HOME_XML)
    )
    respx.request("PROPFIND", f"{_ICLOUD_BASE}/123/carddavhome/").mock(
        return_value=Response(207, text=_ADDRESSBOOKS_XML)
    )
    respx.request("REPORT", f"{_ICLOUD_BASE}/123/carddavhome/card/").mock(
        return_value=Response(207, text=_vcards_xml(("juan", _vcard("Juan Pérez", "uid-1"))))
    )

    primera = await client.post("/v1/contacts/import/icloud", headers=headers)
    segunda = await client.post("/v1/contacts/import/icloud", headers=headers)

    assert primera.json()["importados"] == 1
    assert segunda.json()["importados"] == 0
    listed = await client.get("/v1/contacts", headers=headers)
    assert len(listed.json()) == 1
