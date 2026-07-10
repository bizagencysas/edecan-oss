"""`/v1/devices` — gestión de dispositivos emparejados (companion de
escritorio y apps móviles, `ARCHITECTURE.md` §13, dueño WP-V4-01).

La tabla `devices` YA existe desde `0003_v2_expansion` (ROADMAP_V2.md §7.4,
`edecan_db.models.Device`) — este router es simplemente la primera superficie
HTTP CRUD sobre ella (antes solo la escribía `edecan_api.companion_manager`/
`routers.remote` de forma indirecta al emparejar un companion). No agrega
columnas ni migración nueva.

Mismo criterio que `edecan_api.routers.commerce` (WP-V2-10): SQL parametrizado
directo con `sqlalchemy.text` contra la sesión del tenant
(`edecan_api.deps.get_tenant_session`), nunca a través de `edecan_api.repo`
(paquete de trabajo pinned, este WP tiene prohibido tocarlo) — salvo para
`add_audit_log`, que SÍ vive en `Repo`/`SqlRepo` (§10.3 "auditoría") y no
tiene equivalente en SQL crudo propio de este router; se inyecta aparte vía
`edecan_api.deps.get_repo`. Como `get_repo` internamente depende de la MISMA
`get_tenant_session` (`edecan_api.deps.get_repo(session=Depends(get_tenant_session))`),
FastAPI cachea la dependencia por request y ambos colaboradores comparten una
única sesión/transacción — el `UPDATE ... RETURNING` de `revoke_device` y el
`INSERT INTO audit_log` de `repo.add_audit_log` quedan en el mismo commit
implícito de `edecan_db.session.get_session` al final de la request.

## Endpoints

- `GET ""` — lista los dispositivos del tenant (todos los usuarios del
  tenant, no solo el actual; mismo criterio que "companion" es una capacidad
  de cuenta, no estrictamente personal).
- `POST ""` — registra un dispositivo nuevo para el usuario actual. Si ya
  trae `fingerprint` (no nulo/no vacío) y existe un dispositivo `active` del
  MISMO usuario con ese fingerprint, es idempotente: actualiza
  `nombre`/`last_seen_at` del existente y responde `200` con él (en vez de
  crear un duplicado) — pensado para que una app móvil pueda "registrarse"
  en cada arranque sin acumular filas. Sin match (o sin fingerprint), crea
  uno nuevo y responde `201`.
- `POST "/{id}/heartbeat"` — `204`, refresca `last_seen_at`. `404` si el
  dispositivo no existe (o no es de este tenant).
- `POST "/{id}/revoke"` — `200`, pasa `status` a `revoked` y deja una entrada
  en `audit_log` (`repo.add_audit_log`). `404` si no existe.

Ningún endpoint de arriba gatea por flag de plan: `FLAG_COMPANION` (§10.13)
ya es `True` en los 4 planes — emparejar/gestionar dispositivos es una
capacidad base, no premium.

## Push nativo (APNs/FCM) — v5, `ARCHITECTURE.md` §14, dueño WP-V5-13

Cubre el gap de `docs/roadmap.md` ("push notifications nativas... un canal
`mobile` sería una extensión natural"). 100% bring-your-own, mismo patrón
exacto que `PUT /v1/ads/credentials` (`routers/ads.py`, léelo primero si vas
a tocar esto): cada tenant sube SU PROPIA `.p8` de APNs (de SU cuenta de
Apple Developer — cada cliente ya tiene la suya, ver `DIRECCION_ACTUAL.md`
"Apps móviles") y/o SU PROPIO service account de FCM (de SU proyecto
Firebase) — nunca una credencial de plataforma (ver `docs/notificaciones-
push.md`). El envío en sí vive en `edecan_worker.push` (otro deployable,
`ARCHITECTURE.md` §10.1: "apps/api y apps/worker no se importan entre sí");
este router SOLO administra el token del dispositivo y la credencial del
tenant en el `TokenVault`.

- `POST "/{id}/push-token" {push_token, push_platform: "apns"|"fcm"}` → `204`.
  A diferencia de `heartbeat`/`revoke` (que solo filtran por `tenant_id`,
  cualquier miembro del tenant puede tocar cualquier dispositivo del
  tenant), este endpoint TAMBIÉN filtra por `user_id` — un push_token es el
  buzón físico de UN dispositivo de UNA persona, nadie más del tenant debe
  poder redirigir push ajenos apuntando su propio token al dispositivo de
  otro. `404` si el dispositivo no existe, no es de este usuario/tenant, o
  no está `active`.
- `DELETE "/{id}/push-token"` → `204`, limpia ambas columnas (mismo filtro
  tenant+usuario). `404` si no existe/no es tuyo — mismo criterio que arriba,
  a diferencia del `DELETE /push/credentials` de abajo (que SÍ es
  idempotente, por ser un recurso singleton por tenant en vez de una fila
  puntual por id).
- `PUT "/push/credentials" {apns?: {...}, fcm?: {...}}` (al menos uno;
  parciales OK — un `PUT` con solo `apns` NUNCA borra un `fcm` ya guardado
  antes, y viceversa: se lee la config existente y se sobreescribe SOLO la
  clave que trae el body, ver `_cargar_config_push_existente`). Valida FORMA
  sin red (nunca llama a Apple/Google): `p8_key` debe parsear como clave
  privada EC vía `cryptography.hazmat.primitives.serialization.
  load_pem_private_key`; `service_account_json` debe parsear como JSON con
  `type == "service_account"` + `client_email`/`private_key`, y
  `project_id` se deriva del propio JSON si no vino en el body. `400` con el
  detalle exacto si algo no tiene forma válida — nada se guarda hasta que
  TODO lo que vino en el body pasa su validación. Guarda cifrado en el
  `TokenVault` del tenant, `connector_key="push"` (`TokenBundle(access_token
  =json.dumps(config), token_type="config")`, calcado de `ads.py`), más una
  fila en `connector_accounts` + auditoría (`devices.push_credentials.
  connected`).
- `GET "/push/status"` → `{apns: bool, fcm: bool, devices_con_token: int}` —
  `devices_con_token` es del TENANT completo (todos los usuarios), no solo
  el actual: la config push es una capacidad de cuenta, mismo criterio que
  `GET ""` (lista de dispositivos) de arriba.
- `DELETE "/push/credentials"` → `204`, desconecta (idempotente, mismo
  criterio que `ads.py`) + auditoría (`devices.push_credentials.
  disconnected`). NO toca ninguna fila de `devices` (los `push_token` ya
  registrados quedan intactos; un push posterior sin credencial conectada
  simplemente no se envía, ver `edecan_worker.push`).

Los 5 endpoints de arriba SÍ gatean con el flag `notifications.push`
(`edecan_schemas.plans.FLAG_NOTIFICATIONS_PUSH`, `True` en los 4 planes hoy
— infraestructura de cara al futuro, mismo mecanismo estándar que `ads.py`/
`erp.py`): `403` si el plan del tenant no lo tiene.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from edecan_db.vault import TokenVault
from edecan_schemas import TokenBundle
from edecan_schemas.plans import FLAG_NOTIFICATIONS_PUSH
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_api.deps import (
    CurrentUser,
    get_current_user,
    get_repo,
    get_tenant_session,
    get_vault,
    rate_limit,
)
from edecan_api.repo import Repo

router = APIRouter(prefix="/v1/devices", tags=["devices"], dependencies=[Depends(rate_limit)])


# ---------------------------------------------------------------------------
# Esquemas de entrada
# ---------------------------------------------------------------------------


class DeviceIn(BaseModel):
    """Mismo vocabulario que el CHECK de la tabla `devices` (`0003_v2_expansion`,
    `edecan_db.models.Device`): `kind IN ('companion', 'mobile')`."""

    nombre: str = Field(min_length=1)
    plataforma: str = Field(min_length=1)
    kind: Literal["companion", "mobile"]
    fingerprint: str | None = None


class PushTokenIn(BaseModel):
    push_token: str = Field(min_length=1)
    push_platform: Literal["apns", "fcm"]


class ApnsCredentialsIn(BaseModel):
    """Forma exacta que un tenant pega desde su cuenta de Apple Developer —
    ver `docs/notificaciones-push.md`."""

    team_id: str = Field(min_length=1)
    key_id: str = Field(min_length=1)
    bundle_id: str = Field(min_length=1)
    p8_key: str = Field(min_length=1)
    environment: Literal["production", "sandbox"] = "production"


class FcmCredentialsIn(BaseModel):
    """`service_account_json` es el JSON completo del service account de GCP
    del tenant, pegado tal cual (como string) — ver `docs/notificaciones-
    push.md`. `project_id` es opcional: si no viene, se deriva del propio
    JSON (`_validar_fcm`)."""

    service_account_json: str = Field(min_length=1)
    project_id: str | None = None


class PushCredentialsIn(BaseModel):
    apns: ApnsCredentialsIn | None = None
    fcm: FcmCredentialsIn | None = None


class PushStatusOut(BaseModel):
    apns: bool
    fcm: bool
    devices_con_token: int


# ---------------------------------------------------------------------------
# Helpers SQL — parametrizado directo, mismo criterio que
# `edecan_api.routers.commerce` (§10.3: el contrato pinnea tabla/columna, no
# una forma de ORM). Duplicados a propósito en vez de importados de otro
# router: cada router de este estilo mantiene su propio par `_first`/`_all`
# (ver el docstring de `commerce.py`).
# ---------------------------------------------------------------------------


async def _first(session: AsyncSession, stmt: str, params: dict[str, Any]) -> dict[str, Any] | None:
    result = await session.execute(text(stmt), params)
    row = result.mappings().first()
    return dict(row) if row is not None else None


async def _all(session: AsyncSession, stmt: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    result = await session.execute(text(stmt), params)
    return [dict(row) for row in result.mappings().all()]


# ---------------------------------------------------------------------------
# GET / POST "" — listar / registrar (con idempotencia por fingerprint)
# ---------------------------------------------------------------------------


@router.get("")
async def list_devices(
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[dict[str, Any]]:
    return await _all(
        session,
        "SELECT * FROM devices WHERE tenant_id = :tenant_id ::uuid ORDER BY created_at DESC",
        {"tenant_id": str(current_user.tenant_id)},
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_device(
    body: DeviceIn,
    response: Response,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    """Crea un dispositivo nuevo — o, si `fingerprint` no es nulo/vacío y ya
    existe un dispositivo `active` del mismo usuario con ese fingerprint,
    actualiza ese existente (`nombre`/`last_seen_at`) y responde `200` en vez
    de `201` (idempotencia para apps móviles que "se registran" en cada
    arranque, ver docstring del módulo)."""
    if body.fingerprint:
        existing = await _first(
            session,
            "SELECT * FROM devices WHERE tenant_id = :tenant_id ::uuid "
            "AND user_id = :user_id ::uuid AND fingerprint = :fingerprint "
            "AND status = 'active'",
            {
                "tenant_id": str(current_user.tenant_id),
                "user_id": str(current_user.user_id),
                "fingerprint": body.fingerprint,
            },
        )
        if existing is not None:
            updated = await _first(
                session,
                "UPDATE devices SET nombre = :nombre, last_seen_at = now(), updated_at = now() "
                "WHERE id = :id ::uuid RETURNING *",
                {"id": str(existing["id"]), "nombre": body.nombre},
            )
            response.status_code = status.HTTP_200_OK
            assert updated is not None  # defensivo: la fila de arriba existe en la misma tx
            return updated

    created = await _first(
        session,
        "INSERT INTO devices (tenant_id, user_id, nombre, plataforma, kind, fingerprint) "
        "VALUES (:tenant_id ::uuid, :user_id ::uuid, :nombre, :plataforma, :kind, :fingerprint) "
        "RETURNING *",
        {
            "tenant_id": str(current_user.tenant_id),
            "user_id": str(current_user.user_id),
            "nombre": body.nombre,
            "plataforma": body.plataforma,
            "kind": body.kind,
            "fingerprint": body.fingerprint,
        },
    )
    assert created is not None  # defensivo: INSERT ... RETURNING siempre devuelve la fila
    return created


# ---------------------------------------------------------------------------
# POST /{id}/heartbeat, POST /{id}/revoke
# ---------------------------------------------------------------------------


@router.post("/{device_id}/heartbeat", status_code=status.HTTP_204_NO_CONTENT)
async def heartbeat(
    device_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    updated = await _first(
        session,
        "UPDATE devices SET last_seen_at = now(), updated_at = now() "
        "WHERE id = :id ::uuid AND tenant_id = :tenant_id ::uuid RETURNING id",
        {"id": str(device_id), "tenant_id": str(current_user.tenant_id)},
    )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Dispositivo no encontrado."
        )


@router.post("/{device_id}/revoke")
async def revoke_device(
    device_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    updated = await _first(
        session,
        "UPDATE devices SET status = 'revoked', updated_at = now() "
        "WHERE id = :id ::uuid AND tenant_id = :tenant_id ::uuid RETURNING *",
        {"id": str(device_id), "tenant_id": str(current_user.tenant_id)},
    )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Dispositivo no encontrado."
        )
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="devices.revoked",
        target=str(device_id),
    )
    return updated


# ---------------------------------------------------------------------------
# Push nativo (APNs/FCM) — v5, ver docstring del módulo.
# ---------------------------------------------------------------------------

# Mismo valor que `edecan_worker.push.PUSH_CONNECTOR_KEY` — duplicado a
# propósito: `apps/api` y `apps/worker` son deployables independientes
# (`ARCHITECTURE.md` §10.1), no se importan entre sí (mismo criterio que
# `LLM_CONNECTOR_KEY` duplicado entre `edecan_api.deps`/`edecan_worker.deps`).
PUSH_CONNECTOR_KEY = "push"
_DISPLAY_NAME_PUSH = "Notificaciones push"
_APNS_PEM_HEADER = "-----BEGIN PRIVATE KEY-----"


async def _require_notifications_push(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    if not current_user.tenant.flags.get(FLAG_NOTIFICATIONS_PUSH, False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Las notificaciones push no están disponibles en tu plan.",
        )
    return current_user


# -- Validación de FORMA sin red (nunca llama a Apple/Google) ---------------


def _validar_apns(cred: ApnsCredentialsIn) -> dict[str, Any]:
    if _APNS_PEM_HEADER not in cred.p8_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "La clave .p8 de APNs no tiene forma de clave privada PEM "
                f"(falta '{_APNS_PEM_HEADER}')."
            ),
        )
    try:
        clave = serialization.load_pem_private_key(cred.p8_key.encode("utf-8"), password=None)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No se pudo leer la clave .p8 de APNs: {exc}",
        ) from exc
    if not isinstance(clave, ec.EllipticCurvePrivateKey):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La clave .p8 de APNs debe ser una clave privada de curva elíptica (EC).",
        )
    return {
        "team_id": cred.team_id.strip(),
        "key_id": cred.key_id.strip(),
        "bundle_id": cred.bundle_id.strip(),
        "p8_key": cred.p8_key.strip(),
        "environment": cred.environment,
    }


def _validar_fcm(cred: FcmCredentialsIn) -> dict[str, Any]:
    try:
        data = json.loads(cred.service_account_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"El service account JSON de FCM no es JSON válido: {exc}",
        ) from exc
    if not isinstance(data, dict) or data.get("type") != "service_account":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='El service account JSON de FCM debe tener "type": "service_account".',
        )
    if not data.get("client_email") or not data.get("private_key"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El service account JSON de FCM debe incluir client_email y private_key.",
        )
    project_id = (cred.project_id or data.get("project_id") or "").strip()
    if not project_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No se pudo determinar el project_id de FCM (ni en el body ni en el "
                "propio JSON del service account)."
            ),
        )
    return {"service_account_json": cred.service_account_json.strip(), "project_id": project_id}


# -- `connector_accounts` singleton por tenant (mismo patrón que `ads.py`) --


async def _find_push_account(repo: Repo, tenant_id: uuid.UUID) -> dict[str, Any] | None:
    accounts = await repo.list_connector_accounts(tenant_id=tenant_id)
    matches = [a for a in accounts if a["connector_key"] == PUSH_CONNECTOR_KEY]
    if not matches:
        return None
    return min(matches, key=lambda a: a["created_at"])


async def _find_or_create_push_account(repo: Repo, tenant_id: uuid.UUID) -> dict[str, Any]:
    existing = await _find_push_account(repo, tenant_id)
    if existing is not None:
        return existing
    return await repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key=PUSH_CONNECTOR_KEY,
        external_account_id=PUSH_CONNECTOR_KEY,
        display_name=_DISPLAY_NAME_PUSH,
        scopes=[],
    )


async def _cargar_config_push_existente(
    repo: Repo, vault: TokenVault, tenant_id: uuid.UUID
) -> dict[str, Any]:
    """Config guardada hoy (`{}` si el tenant no conectó nada, o si cualquier
    paso de la lectura falla) — usada para el merge parcial de `PUT
    /push/credentials` (ver docstring del módulo: un `PUT` con solo `apns`
    nunca debe borrar un `fcm` ya guardado)."""
    account = await _find_push_account(repo, tenant_id)
    if account is None:
        return {}
    bundle = await vault.get(tenant_id, account["id"])
    if bundle is None or not bundle.access_token:
        return {}
    try:
        data = json.loads(bundle.access_token)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


# -- POST/DELETE /{id}/push-token --------------------------------------------


@router.post("/{device_id}/push-token", status_code=status.HTTP_204_NO_CONTENT)
async def set_push_token(
    device_id: uuid.UUID,
    body: PushTokenIn,
    current_user: CurrentUser = Depends(_require_notifications_push),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    updated = await _first(
        session,
        "UPDATE devices SET push_token = :push_token, push_platform = :push_platform, "
        "updated_at = now() WHERE id = :id ::uuid AND tenant_id = :tenant_id ::uuid "
        "AND user_id = :user_id ::uuid AND status = 'active' RETURNING id",
        {
            "id": str(device_id),
            "tenant_id": str(current_user.tenant_id),
            "user_id": str(current_user.user_id),
            "push_token": body.push_token,
            "push_platform": body.push_platform,
        },
    )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dispositivo no encontrado (o no es tuyo, o no está activo).",
        )


@router.delete("/{device_id}/push-token", status_code=status.HTTP_204_NO_CONTENT)
async def delete_push_token(
    device_id: uuid.UUID,
    current_user: CurrentUser = Depends(_require_notifications_push),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    updated = await _first(
        session,
        "UPDATE devices SET push_token = NULL, push_platform = NULL, updated_at = now() "
        "WHERE id = :id ::uuid AND tenant_id = :tenant_id ::uuid "
        "AND user_id = :user_id ::uuid RETURNING id",
        {
            "id": str(device_id),
            "tenant_id": str(current_user.tenant_id),
            "user_id": str(current_user.user_id),
        },
    )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dispositivo no encontrado (o no es tuyo).",
        )


# -- PUT/DELETE /push/credentials, GET /push/status ---------------------------


@router.put("/push/credentials", status_code=status.HTTP_204_NO_CONTENT)
async def put_push_credentials(
    payload: PushCredentialsIn,
    current_user: CurrentUser = Depends(_require_notifications_push),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
) -> None:
    if payload.apns is None and payload.fcm is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Envía al menos una de las dos: 'apns' o 'fcm'.",
        )

    # Validar ANTES de tocar `connector_accounts`/vault — nunca se persiste
    # nada si la forma no es válida (mismo espíritu que "nunca se persiste
    # una credencial sin probarla" de `ads.py`, aquí sin red).
    nuevo_apns = _validar_apns(payload.apns) if payload.apns is not None else None
    nuevo_fcm = _validar_fcm(payload.fcm) if payload.fcm is not None else None

    config = await _cargar_config_push_existente(repo, vault, current_user.tenant_id)
    if nuevo_apns is not None:
        config["apns"] = nuevo_apns
    if nuevo_fcm is not None:
        config["fcm"] = nuevo_fcm

    account = await _find_or_create_push_account(repo, current_user.tenant_id)
    await vault.put(
        current_user.tenant_id,
        account["id"],
        TokenBundle(access_token=json.dumps(config), token_type="config"),
    )
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="devices.push_credentials.connected",
        target=PUSH_CONNECTOR_KEY,
        meta={"apns": nuevo_apns is not None, "fcm": nuevo_fcm is not None},
    )


@router.delete("/push/credentials", status_code=status.HTTP_204_NO_CONTENT)
async def delete_push_credentials(
    current_user: CurrentUser = Depends(_require_notifications_push),
    repo: Repo = Depends(get_repo),
) -> None:
    account = await _find_push_account(repo, current_user.tenant_id)
    if account is None:
        return  # idempotente: nada que borrar ya es un estado válido de "desconectado".
    await repo.delete_connector_account(tenant_id=current_user.tenant_id, account_id=account["id"])
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="devices.push_credentials.disconnected",
        target=PUSH_CONNECTOR_KEY,
    )


@router.get("/push/status", response_model=PushStatusOut)
async def get_push_status(
    current_user: CurrentUser = Depends(_require_notifications_push),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
    session: AsyncSession = Depends(get_tenant_session),
) -> PushStatusOut:
    config = await _cargar_config_push_existente(repo, vault, current_user.tenant_id)
    count_row = await _first(
        session,
        "SELECT COUNT(*) AS n FROM devices WHERE tenant_id = :tenant_id ::uuid "
        "AND push_token IS NOT NULL",
        {"tenant_id": str(current_user.tenant_id)},
    )
    devices_con_token = int(count_row["n"]) if count_row is not None else 0
    return PushStatusOut(
        apns=config.get("apns") is not None,
        fcm=config.get("fcm") is not None,
        devices_con_token=devices_con_token,
    )
