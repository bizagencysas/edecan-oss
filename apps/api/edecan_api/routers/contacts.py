"""CRUD `/v1/contacts` (ARCHITECTURE.md §10.12, §10.3), más `/v1/contacts/import/*`
— importar contactos desde Google (People API, reusa el conector OAuth `google` ya
usado para Gmail/Calendar) y desde iCloud (CardDAV, Apple ID + contraseña específica
de app — iCloud NO tiene una API REST pública para terceros, CardDAV es el único
camino real).

## Google (`POST /v1/contacts/import/google`)

Reusa la MISMA cuenta `connector_key="google"` que ya conectó el tenant para Gmail/
Calendar (`routers/connectors.py`) — se le suma el scope `contacts.readonly` en
`packages/connectors/edecan_connectors/google/connector.py::GOOGLE_OAUTH` (un tenant
que ya había conectado Google ANTES de este cambio necesita reconectar una vez para
que Google le pida ese scope nuevo; `PUT /v1/connectors/google/authorize` con
`prompt=consent` ya fuerza ese re-consentimiento). Si el access token está por
expirar, se refresca con `edecan_connectors.registry.CONNECTORS["google"].refresh`
antes de llamar a la People API — mismo mecanismo que usa el job periódico
`sync_connector`, pero acá se hace en el momento porque el usuario está esperando la
respuesta.

## iCloud (`PUT /v1/contacts/import/icloud/credentials` + `POST /v1/contacts/import/icloud`)

Nueva `connector_key="icloud_contacts"`, mismo patrón "pegar y validar" que
`routers/viajes.py`/`routers/finance.py` — pero la credencial es un PAR
(`apple_id` + `app_specific_password`, generada en https://appleid.apple.com/,
Apple NO permite usar la contraseña real de la cuenta para protocolos como CardDAV),
no una sola key.

CardDAV (RFC 6352) exige un baile de descubrimiento en 3 pasos antes de poder pedir
las vCards — no hay un endpoint fijo "dame mis contactos", a diferencia de una API
REST:

1. `PROPFIND /` (Depth 0) → `<current-user-principal>`: quién sos.
2. `PROPFIND <principal>` (Depth 0) → `<addressbook-home-set>`: dónde viven tus
   libretas de contactos.
3. `PROPFIND <home>` (Depth 1) → cada `<response>` cuyo `<resourcetype>` incluye
   `<C:addressbook>` es una libreta real.
4. `REPORT <libreta>` (`addressbook-query`, Depth 1) → el `<C:address-data>` de cada
   `<response>` es una vCard cruda (texto `BEGIN:VCARD...END:VCARD`).

`vobject` (dependencia nueva, `apps/api/pyproject.toml`) parsea cada vCard. Sin
columna dedicada para el id externo (igual que `transactions`/Stripe, ver docstring
de `routers/finance.py`): el `UID` de la vCard (o, si no trae uno, el nombre —
fallback débil pero determinístico) se guarda como marcador en `contacts.tags` para
no duplicar en una sincronización repetida.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from xml.etree import ElementTree as ET

import httpx
import vobject
from edecan_connectors.registry import CONNECTORS
from edecan_db.vault import TokenVault
from edecan_schemas import TokenBundle
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from edecan_api.deps import CurrentUser, get_current_user, get_repo, get_vault, rate_limit
from edecan_api.oauth_app_credentials import get_oauth_app_credentials
from edecan_api.repo import Repo

router = APIRouter(prefix="/v1/contacts", tags=["contacts"], dependencies=[Depends(rate_limit)])


class ContactIn(BaseModel):
    nombre: str = Field(min_length=1)
    emails: list[str] = Field(default_factory=list)
    phones: list[str] = Field(default_factory=list)
    empresa: str | None = None
    notas: str | None = None
    tags: list[str] = Field(default_factory=list)


class ContactPatch(BaseModel):
    nombre: str | None = None
    emails: list[str] | None = None
    phones: list[str] | None = None
    empresa: str | None = None
    notas: str | None = None
    tags: list[str] | None = None


@router.get("")
async def list_contacts(
    q: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> list[dict[str, Any]]:
    return await repo.list_contacts(
        tenant_id=current_user.tenant_id, user_id=current_user.user_id, q=q
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_contact(
    body: ContactIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    return await repo.create_contact(
        tenant_id=current_user.tenant_id, user_id=current_user.user_id, fields=body.model_dump()
    )


@router.get("/{contact_id}")
async def get_contact(
    contact_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    row = await repo.get_contact(tenant_id=current_user.tenant_id, contact_id=contact_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contacto no encontrado.")
    return row


@router.put("/{contact_id}")
async def update_contact(
    contact_id: uuid.UUID,
    body: ContactPatch,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    row = await repo.update_contact(
        tenant_id=current_user.tenant_id, contact_id=contact_id, fields=fields
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contacto no encontrado.")
    return row


@router.delete("/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_contact(
    contact_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> None:
    deleted = await repo.delete_contact(tenant_id=current_user.tenant_id, contact_id=contact_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contacto no encontrado.")


# ---------------------------------------------------------------------------
# POST /v1/contacts/import/google
# ---------------------------------------------------------------------------

_GOOGLE_CONNECTOR_KEY = "google"
_GOOGLE_PEOPLE_API_BASE = "https://people.googleapis.com/v1"
_GOOGLE_CONTACT_MARKER_RE = re.compile(r"^google:(.+)$")


async def _find_connector_account(
    repo: Repo, tenant_id: uuid.UUID, connector_key: str
) -> dict[str, Any] | None:
    accounts = await repo.list_connector_accounts(tenant_id=tenant_id)
    matches = [a for a in accounts if a["connector_key"] == connector_key]
    if not matches:
        return None
    return min(matches, key=lambda a: a["created_at"])


def _extraer_marca(tag: str, patron: re.Pattern[str]) -> str | None:
    match = patron.match(tag)
    return match.group(1) if match else None


async def _marcas_ya_importadas(
    repo: Repo, tenant_id: uuid.UUID, user_id: uuid.UUID, patron: re.Pattern[str]
) -> set[str]:
    existentes = await repo.list_contacts(tenant_id=tenant_id, user_id=user_id, q=None)
    return {
        marca
        for c in existentes
        for tag in (c.get("tags") or [])
        if (marca := _extraer_marca(tag, patron)) is not None
    }


@router.post("/import/google")
async def import_contacts_google(
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
) -> dict[str, Any]:
    account = await _find_connector_account(repo, current_user.tenant_id, _GOOGLE_CONNECTOR_KEY)
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Conecta tu cuenta de Google primero (Conectores → Google).",
        )
    bundle = await vault.get(current_user.tenant_id, account["id"])
    if bundle is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Conecta tu cuenta de Google primero (Conectores → Google).",
        )

    async with httpx.AsyncClient(timeout=15.0) as http:
        # Refresca perezoso si el token está por vencer -- mismo umbral que el
        # job periódico `sync_connector`, pero acá se hace en el momento
        # porque el usuario está esperando la respuesta (ver docstring del
        # módulo).
        if bundle.expires_at is not None and bundle.expires_at <= datetime.now(UTC) + timedelta(
            minutes=2
        ):
            creds = await get_oauth_app_credentials(
                repo, vault, current_user.tenant_id, _GOOGLE_CONNECTOR_KEY
            )
            if creds is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Configura tu propia app OAuth de Google primero (Conectores → Google).",
                )
            client_id, client_secret = creds
            bundle = await CONNECTORS["google"].refresh(
                bundle, http, client_id=client_id, client_secret=client_secret
            )
            await vault.put(current_user.tenant_id, account["id"], bundle)

        response = await http.get(
            f"{_GOOGLE_PEOPLE_API_BASE}/people/me/connections",
            params={
                "personFields": "names,emailAddresses,phoneNumbers,organizations",
                "pageSize": 200,
            },
            headers={"Authorization": f"Bearer {bundle.access_token}"},
        )
    if response.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Google rechazó la sincronización: {response.text[:300]}",
        )

    personas = response.json().get("connections", [])
    ya_importados = await _marcas_ya_importadas(
        repo, current_user.tenant_id, current_user.user_id, _GOOGLE_CONTACT_MARKER_RE
    )

    importados = 0
    for persona in personas:
        resource_name = persona.get("resourceName")
        if not resource_name or resource_name in ya_importados:
            continue
        nombres = persona.get("names") or []
        nombre = nombres[0].get("displayName") if nombres else None
        if not nombre:
            continue  # sin nombre no vale la pena importarlo
        emails = [e["value"] for e in persona.get("emailAddresses", []) if e.get("value")]
        phones = [p["value"] for p in persona.get("phoneNumbers", []) if p.get("value")]
        orgs = persona.get("organizations") or []
        empresa = orgs[0].get("name") if orgs and orgs[0].get("name") else None
        await repo.create_contact(
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            fields={
                "nombre": nombre,
                "emails": emails,
                "phones": phones,
                "empresa": empresa,
                "notas": None,
                "tags": [f"google:{resource_name}"],
            },
        )
        importados += 1

    return {"importados": importados, "total_google": len(personas)}


# ---------------------------------------------------------------------------
# PUT /v1/contacts/import/icloud/credentials + POST /v1/contacts/import/icloud
# ---------------------------------------------------------------------------

_ICLOUD_CONNECTOR_KEY = "icloud_contacts"
_ICLOUD_CARDDAV_BASE = "https://contacts.icloud.com"
_ICLOUD_TIMEOUT_SECONDS = 15.0
_ICLOUD_CONTACT_MARKER_RE = re.compile(r"^icloud:(.+)$")

_DAV_NS = {"D": "DAV:", "C": "urn:ietf:params:xml:ns:carddav"}

_PROPFIND_CURRENT_USER_PRINCIPAL = (
    '<?xml version="1.0" encoding="utf-8" ?>'
    '<D:propfind xmlns:D="DAV:"><D:prop><D:current-user-principal/></D:prop></D:propfind>'
)
_PROPFIND_ADDRESSBOOK_HOME = (
    '<?xml version="1.0" encoding="utf-8" ?>'
    '<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:carddav">'
    "<D:prop><C:addressbook-home-set/></D:prop></D:propfind>"
)
_PROPFIND_ADDRESSBOOKS = (
    '<?xml version="1.0" encoding="utf-8" ?>'
    '<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:carddav">'
    "<D:prop><D:resourcetype/><D:displayname/></D:prop></D:propfind>"
)
_REPORT_ADDRESSBOOK_QUERY = (
    '<?xml version="1.0" encoding="utf-8" ?>'
    '<C:addressbook-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:carddav">'
    "<D:prop><D:getetag/><C:address-data/></D:prop><C:filter/></C:addressbook-query>"
)


class ICloudCredentialsIn(BaseModel):
    apple_id: str
    app_specific_password: str


def _propfind_prop_hrefs(xml_bytes: bytes, prop_tag: str) -> list[str]:
    """`<D:response>`s con status 200 cuyo `<D:prop>` trae `prop_tag`, devuelve
    el `<D:href>` de adentro de cada uno."""
    root = ET.fromstring(xml_bytes)
    hrefs = []
    for resp in root.findall("D:response", _DAV_NS):
        for propstat in resp.findall("D:propstat", _DAV_NS):
            status_el = propstat.find("D:status", _DAV_NS)
            if status_el is None or "200" not in (status_el.text or ""):
                continue
            prop_el = propstat.find("D:prop", _DAV_NS)
            if prop_el is None:
                continue
            target = prop_el.find(prop_tag, _DAV_NS)
            if target is None:
                continue
            href_el = target.find("D:href", _DAV_NS)
            if href_el is not None and href_el.text:
                hrefs.append(href_el.text)
    return hrefs


def _assert_multistatus(response: httpx.Response, *, contexto: str) -> None:
    if response.status_code == 401:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "iCloud rechazó el Apple ID o la contraseña de app -- confirma que "
                "generaste una contraseña específica de app en appleid.apple.com."
            ),
        )
    if response.status_code not in (207, 200):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"iCloud respondió {response.status_code} en {contexto}.",
        )


async def _ping_icloud(apple_id: str, app_specific_password: str, *, timeout: float) -> None:
    try:
        async with httpx.AsyncClient(base_url=_ICLOUD_CARDDAV_BASE, timeout=timeout) as http:
            response = await http.request(
                "PROPFIND",
                "/",
                content=_PROPFIND_CURRENT_USER_PRINCIPAL,
                headers={"Depth": "0", "Content-Type": "application/xml"},
                auth=(apple_id, app_specific_password),
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"No pudimos conectar con iCloud: {exc}"
        ) from exc
    _assert_multistatus(response, contexto="la validación inicial")


@router.put("/import/icloud/credentials", status_code=status.HTTP_204_NO_CONTENT)
async def put_icloud_credentials(
    payload: ICloudCredentialsIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
) -> None:
    apple_id = payload.apple_id.strip()
    password = payload.app_specific_password.strip()
    if not apple_id or not password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El Apple ID y la contraseña de app no pueden estar vacíos.",
        )
    await _ping_icloud(apple_id, password, timeout=_ICLOUD_TIMEOUT_SECONDS)

    account = await _find_connector_account(repo, current_user.tenant_id, _ICLOUD_CONNECTOR_KEY)
    if account is None:
        account = await repo.create_connector_account(
            tenant_id=current_user.tenant_id,
            connector_key=_ICLOUD_CONNECTOR_KEY,
            external_account_id=apple_id,
            display_name=f"iCloud ({apple_id})",
            scopes=[],
        )
    await vault.put(
        current_user.tenant_id,
        account["id"],
        TokenBundle(
            access_token=json.dumps({"apple_id": apple_id, "app_specific_password": password}),
            token_type="config",
            scopes=["icloud"],
        ),
    )
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="contacts.icloud_connected",
        target=_ICLOUD_CONNECTOR_KEY,
    )


@router.delete("/import/icloud/credentials", status_code=status.HTTP_204_NO_CONTENT)
async def delete_icloud_credentials(
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> None:
    account = await _find_connector_account(repo, current_user.tenant_id, _ICLOUD_CONNECTOR_KEY)
    if account is None:
        return  # idempotente: nada que borrar ya es un estado válido de "desconectado".
    await repo.delete_connector_account(tenant_id=current_user.tenant_id, account_id=account["id"])
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="contacts.icloud_disconnected",
        target=_ICLOUD_CONNECTOR_KEY,
    )


@router.get("/import/icloud/status")
async def get_icloud_status(
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    account = await _find_connector_account(repo, current_user.tenant_id, _ICLOUD_CONNECTOR_KEY)
    return {
        "connected": account is not None,
        "apple_id": account["external_account_id"] if account else None,
    }


async def _icloud_discover_addressbooks(
    http: httpx.AsyncClient, auth: tuple[str, str]
) -> list[str]:
    """Baile de descubrimiento CardDAV en 3 pasos -- ver docstring del módulo."""
    r1 = await http.request(
        "PROPFIND",
        "/",
        content=_PROPFIND_CURRENT_USER_PRINCIPAL,
        headers={"Depth": "0", "Content-Type": "application/xml"},
        auth=auth,
    )
    _assert_multistatus(r1, contexto="el paso 1 (principal)")
    principales = _propfind_prop_hrefs(r1.content, "D:current-user-principal")
    if not principales:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="iCloud no devolvió un principal de usuario para esta cuenta.",
        )

    r2 = await http.request(
        "PROPFIND",
        principales[0],
        content=_PROPFIND_ADDRESSBOOK_HOME,
        headers={"Depth": "0", "Content-Type": "application/xml"},
        auth=auth,
    )
    _assert_multistatus(r2, contexto="el paso 2 (addressbook-home-set)")
    homes = _propfind_prop_hrefs(r2.content, "C:addressbook-home-set")
    if not homes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="iCloud no devolvió ninguna libreta de contactos para esta cuenta.",
        )

    r3 = await http.request(
        "PROPFIND",
        homes[0],
        content=_PROPFIND_ADDRESSBOOKS,
        headers={"Depth": "1", "Content-Type": "application/xml"},
        auth=auth,
    )
    _assert_multistatus(r3, contexto="el paso 3 (listado de libretas)")
    root = ET.fromstring(r3.content)
    libretas = []
    for resp in root.findall("D:response", _DAV_NS):
        href_el = resp.find("D:href", _DAV_NS)
        if href_el is None or not href_el.text:
            continue
        for propstat in resp.findall("D:propstat", _DAV_NS):
            prop_el = propstat.find("D:prop", _DAV_NS)
            if prop_el is None:
                continue
            resourcetype = prop_el.find("D:resourcetype", _DAV_NS)
            if resourcetype is not None and resourcetype.find("C:addressbook", _DAV_NS) is not None:
                libretas.append(href_el.text)
    return libretas


async def _icloud_fetch_vcards(
    http: httpx.AsyncClient, auth: tuple[str, str], libreta_href: str
) -> list[str]:
    response = await http.request(
        "REPORT",
        libreta_href,
        content=_REPORT_ADDRESSBOOK_QUERY,
        headers={"Depth": "1", "Content-Type": "application/xml"},
        auth=auth,
    )
    _assert_multistatus(response, contexto="la descarga de contactos")
    root = ET.fromstring(response.content)
    vcards = []
    for resp in root.findall("D:response", _DAV_NS):
        for propstat in resp.findall("D:propstat", _DAV_NS):
            prop_el = propstat.find("D:prop", _DAV_NS)
            if prop_el is None:
                continue
            data_el = prop_el.find("C:address-data", _DAV_NS)
            if data_el is not None and data_el.text:
                vcards.append(data_el.text)
    return vcards


def _parse_vcard(raw: str) -> dict[str, Any] | None:
    """`None` si la vCard no trae ni siquiera un nombre — no vale la pena
    importarla. El `UID` de la vCard es el marcador de dedup preferido; si no
    trae uno (vCards viejas/mal formadas), se usa el nombre como respaldo
    débil pero determinístico (ver docstring del módulo)."""
    try:
        vcard = vobject.readOne(raw)
    except Exception:
        return None

    nombre = getattr(vcard, "fn", None)
    nombre = nombre.value.strip() if nombre and nombre.value else None
    if not nombre:
        return None

    emails = [e.value for e in getattr(vcard, "email_list", []) if getattr(e, "value", None)]
    phones = [t.value for t in getattr(vcard, "tel_list", []) if getattr(t, "value", None)]

    empresa = None
    org = getattr(vcard, "org", None)
    if org is not None:
        org_value = org.value
        if isinstance(org_value, list) and org_value:
            empresa = str(org_value[0])
        elif isinstance(org_value, str) and org_value:
            empresa = org_value

    uid_el = getattr(vcard, "uid", None)
    marcador = uid_el.value.strip() if uid_el and uid_el.value else nombre

    return {
        "nombre": nombre,
        "emails": emails,
        "phones": phones,
        "empresa": empresa,
        "marcador": marcador,
    }


@router.post("/import/icloud")
async def import_contacts_icloud(
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
) -> dict[str, Any]:
    account = await _find_connector_account(repo, current_user.tenant_id, _ICLOUD_CONNECTOR_KEY)
    bundle = await vault.get(current_user.tenant_id, account["id"]) if account else None
    if bundle is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Conecta tu cuenta de iCloud primero "
                "(PUT /v1/contacts/import/icloud/credentials)."
            ),
        )
    config = json.loads(bundle.access_token)
    auth = (config["apple_id"], config["app_specific_password"])

    async with httpx.AsyncClient(
        base_url=_ICLOUD_CARDDAV_BASE, timeout=_ICLOUD_TIMEOUT_SECONDS
    ) as http:
        libretas = await _icloud_discover_addressbooks(http, auth)
        vcards_crudas: list[str] = []
        for libreta in libretas:
            vcards_crudas.extend(await _icloud_fetch_vcards(http, auth, libreta))

    ya_importados = await _marcas_ya_importadas(
        repo, current_user.tenant_id, current_user.user_id, _ICLOUD_CONTACT_MARKER_RE
    )

    importados = 0
    for cruda in vcards_crudas:
        parsed = _parse_vcard(cruda)
        if parsed is None or parsed["marcador"] in ya_importados:
            continue
        await repo.create_contact(
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            fields={
                "nombre": parsed["nombre"],
                "emails": parsed["emails"],
                "phones": parsed["phones"],
                "empresa": parsed["empresa"],
                "notas": None,
                "tags": [f"icloud:{parsed['marcador']}"],
            },
        )
        importados += 1
        ya_importados.add(parsed["marcador"])

    return {"importados": importados, "total_icloud": len(vcards_crudas)}
