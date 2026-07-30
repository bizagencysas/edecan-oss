"""`/v1/connectors/*` — OAuth de conectores oficiales (ARCHITECTURE.md §10.12, §10.8).

`GET /{key}/authorize` firma un `state` corto (10 min) que codifica `tenant_id`
+ expiración, autenticado con HMAC-SHA256 (la firma también cubre `key`, para
que un `state` emitido para un conector no sirva en el callback de otro) y
devuelve la URL de autorización del proveedor. Para los conectores con
`oauth.pkce=True` (todos salvo `meta`), ese mismo `state` es ADEMÁS el
`code_verifier` PKCE (RFC 7636) que hay que reenviar tal cual a
`exchange_code(...)`: `edecan_connectors.base.build_authorize_url` deriva el
`code_challenge` directamente del `state` recibido, así que el valor que
vuelve del proveedor debe ser, carácter por carácter, un `code_verifier`
válido. Por eso `state` NO es un JWT: un JWT que cargue `tenant_id` supera
holgadamente los 128 caracteres que permite un `code_verifier` (RFC 7636 §4.1)
— ver `_create_state_token`. `GET /{key}/callback` (lo visita el navegador
tras la redirección del proveedor, así que NO trae nuestro propio
`Authorization: Bearer`) valida ese `state` para recuperar el tenant, lo
reenvía como `code_verifier` al cambiar el `code` por un `TokenBundle`, y lo
guarda cifrado en el `TokenVault`.

`PUT /twilio/credentials` es un flujo aparte para Twilio (ARCHITECTURE.md §7,
§10.10; `docs/voz-telefonia.md`): Twilio no es OAuth (no tiene
`authorize`/`callback`/`refresh`), así que a propósito NO vive en
`CONNECTORS`/`edecan_connectors.registry` — el tenant pega su Account SID +
Auth Token directamente (autenticado con su `Authorization: Bearer` normal) y
este router los guarda con la misma convención que ya leen
`edecan_premium.telephony.for_tenant` y
`edecan_premium.twilio_router._resolve_tenant_by_number`:
`connector_accounts.external_account_id` = número E.164 conectado,
`TokenBundle.access_token` = Auth Token, `TokenBundle.scopes[0]` = Account SID.

`account_sid`/`auth_token`/`phone_number` se validan primero por FORMATO
(regex) y LUEGO contra la API real de Twilio
(`_verify_twilio_phone_ownership`: `GET .../IncomingPhoneNumbers.json`) para
confirmar que `phone_number` de verdad pertenece a esa cuenta — el formato
por sí solo no prueba propiedad, cualquier tenant podría pegar el número
público de otro. Como defensa adicional (para que una carrera entre dos
requests concurrentes no pueda colar el mismo número dos veces), también se
comprueba contra `get_connector_account_by_external_id` (vía
`get_platform_repo`, que bypassa RLS — la sesión normal por tenant nunca
vería la fila de otro tenant) que ningún OTRO tenant ya tenga ese número
conectado, y `edecan_db.models.ConnectorAccount` tiene un índice único
parcial (`connector_key='twilio'`) que hace la garantía atómica a nivel de
base de datos.

`PUT /{key}/credentials` (`key` ∈ `BOT_TOKEN_CONNECTOR_KEYS` = `"telegram"`,
`"discord"`) generaliza ese mismo patrón no-OAuth a los bots de mensajería
(ROADMAP_V2.md §7.7, WP-V2-05; `docs/mensajeria.md`): ninguno de los dos
tiene API pública de OAuth (cada tenant crea su propio bot con BotFather o
en el Discord Developer Portal y pega el TOKEN DEL BOT directo), así que
tampoco viven en `CONNECTORS`. A diferencia de Twilio, aquí NO hay una API
de "propiedad" contra la que verificar el token antes de guardarlo (validar
que un bot token es utilizable de verdad exige llamarlo — `edecan_messaging`
lo hace en el primer envío/lectura real, no aquí): este endpoint solo valida
FORMATO (no vacío, longitud mínima razonable). Slack, en cambio, SÍ es OAuth
(`edecan_connectors.messaging.slack.SlackConnector`) y por tanto SÍ vive en
`CONNECTORS` — sale gratis del flujo genérico `authorize`/`callback` de
arriba, sin necesitar ninguna ruta nueva en este módulo.

`PUT /whatsapp/credentials` (WP-V3-13, dueño único y alcance cerrado — mismo
criterio de excepción pinned que WP-V2-05 dejó documentado en
`ROADMAP_V2.md` §7.13 para `"slack"`/bots de mensajería) extiende este
módulo con WhatsApp Business Platform (API oficial de Meta,
`graph.facebook.com`), con el MISMO patrón de ruta fija que Twilio: no vive
en `CONNECTORS` (no es OAuth — cada tenant trae el access token PERMANENTE
de su propia app de Meta, vía system user, más el `phone_number_id` de su
número de WhatsApp Business ya verificado) y necesita su propia ruta con su
propio Pydantic model, porque son dos campos, no uno (`docs/api.md`,
`docs/mensajeria.md`). A diferencia de Twilio (que permite varios números
por tenant, hasta la cuota del plan) y en línea con `connector_key="whatsapp"`
de `ARCHITECTURE.md` §12.b, esta cuenta es SINGLETON por tenant: `PUT` hace
upsert (reemplaza la única cuenta de WhatsApp del tenant si ya existía, en
vez de acumular varias). `Repo` no expone una operación de UPDATE sobre
`connector_accounts`, así que el upsert se implementa borrando la fila
anterior (si la había) y creando una nueva (`_upsert_whatsapp_account`), para
que `external_account_id`/`display_name` siempre reflejen la credencial más
reciente. `phone_number_id` se verifica contra la Graph API real de Meta
(`_verify_whatsapp_phone_ownership`,
`GET /{phone_number_id}?fields=display_phone_number,verified_name`) — mismo
espíritu "fail closed" que `_verify_twilio_phone_ownership` — pero SIN
chequeo de unicidad cruzada entre tenants: a diferencia de un E.164 real,
`phone_number_id` es un ID opaco interno de Meta que solo sirve combinado
con un access_token válido de la MISMA app, así que "declarar" el
`phone_number_id` de otro tenant sin tener también SU access_token no
consigue nada. Solo ENVÍO en v3: leer mensajes entrantes de WhatsApp exige un
webhook público verificado que este work package NO monta
(`edecan_messaging.LeerMensajesTool`, `docs/mensajeria.md`).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time
import uuid
from typing import Any

import httpx
from edecan_connectors.base import ConnectorError
from edecan_connectors.registry import CONNECTORS
from edecan_connectors.social.linkedin import get_me as get_linkedin_profile
from edecan_db.session import get_session
from edecan_db.vault import TokenVault
from edecan_schemas import UNLIMITED, TokenBundle
from edecan_schemas.plans import FLAG_VOICE_TELEPHONY, LIMIT_PHONE_NUMBERS
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from edecan_api.config import Settings, get_settings
from edecan_api.deps import (
    CurrentUser,
    TenantCtx,
    build_key_provider,
    get_current_user,
    get_platform_repo,
    get_repo,
    get_vault,
    rate_limit,
)
from edecan_api.oauth_app_credentials import (
    base_connector_key,
    delete_oauth_app_credentials,
    get_oauth_app_credentials,
    is_app_config_connector_key,
    mask_client_id,
    put_oauth_app_credentials,
)
from edecan_api.repo import Repo, SqlRepo
from edecan_api.security import TokenError

# `rate_limit` (`edecan_api/deps.py`) exige `Depends(get_current_user)`, así que
# NO puede vivir a nivel de router (`APIRouter(..., dependencies=[...])` se
# aplicaría a TODAS las rutas, `callback` incluido) — `callback` es la única
# ruta de este router que el navegador visita sin poder adjuntar
# `Authorization` (ver docstring arriba). Por eso `rate_limit` se declara por
# ruta más abajo, en cada endpoint salvo `callback`.
router = APIRouter(prefix="/v1/connectors", tags=["connectors"])

STATE_TTL_SECONDS = 600

# Formato binario de `state`/`code_verifier` (ver docstring del módulo):
# tenant_id crudo (16 bytes) + expiración unix como entero sin signo de 4
# bytes big-endian + firma HMAC-SHA256 truncada a 16 bytes — todo concatenado
# y codificado en base64url sin relleno. 36 bytes → 48 caracteres: dentro del
# rango [43, 128] que RFC 7636 §4.1 exige para un `code_verifier`.
_STATE_TENANT_LEN = 16
_STATE_EXP_LEN = 4
_STATE_SIG_LEN = 16

# Conector key reservado para Twilio (fuera de `CONNECTORS`: ver docstring del
# módulo). No es un connector OAuth, pero comparte tabla `connector_accounts`
# y `TokenVault` con los que sí lo son.
TWILIO_CONNECTOR_KEY = "twilio"
_TWILIO_DISPLAY_NAME = "Twilio (telefonía)"

# Formato pinned por Twilio: Account SID = "AC" + 32 hex; Auth Token = 32
# caracteres alfanuméricos (ver docs de Twilio). E.164: "+" y 2-15 dígitos,
# el primero distinto de cero.
_TWILIO_SID_RE = re.compile(r"^AC[0-9a-fA-F]{32}$")
_TWILIO_AUTH_TOKEN_RE = re.compile(r"^[0-9a-zA-Z]{32}$")
_E164_RE = re.compile(r"^\+[1-9]\d{1,14}$")

# Mismo valor que `edecan_premium.telephony.TWILIO_API_BASE`, duplicado a
# propósito: `apps/api` no depende de `edecan_premium` (paquete premium
# opcional, ver docstring de este módulo y `edecan_api/main.py`), así que
# este router —parte del core, siempre presente— no puede importarlo.
_TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"
_TWILIO_VERIFY_TIMEOUT_SECONDS = 15.0

# Conector keys reservados para bots de mensajería sin OAuth (ver docstring
# del módulo, y `edecan_connectors.messaging`/`docs/mensajeria.md`): cada
# tenant crea su propio bot y pega el TOKEN DEL BOT directo, mismo patrón
# no-OAuth que ya usa Twilio (§10.10) — comparten tabla `connector_accounts`
# y `TokenVault`, pero no pasan por `authorize`/`callback` ni viven en
# `CONNECTORS`. Slack SÍ es OAuth (`edecan_connectors.messaging.slack`) y por
# tanto SÍ vive en `CONNECTORS` — nada especial que hacer por Slack aquí.
BOT_TOKEN_CONNECTOR_KEYS = ("telegram", "discord")
_BOT_TOKEN_DISPLAY_NAMES = {"telegram": "Telegram", "discord": "Discord"}
_MIN_BOT_TOKEN_LEN = 10

# Conector key reservado para WhatsApp Business Platform (WP-V3-13, ver
# docstring del módulo y ARCHITECTURE.md §12.b): ruta fija propia, mismo
# patrón no-OAuth que Twilio, pero SINGLETON por tenant — no vive en
# `BOT_TOKEN_CONNECTOR_KEYS` (necesita dos campos, no uno: access_token +
# phone_number_id).
WHATSAPP_CONNECTOR_KEY = "whatsapp"
_WHATSAPP_DISPLAY_NAME = "WhatsApp Business Platform"
_WHATSAPP_TOKEN_MIN_LEN = 20
_WHATSAPP_PHONE_NUMBER_ID_RE = re.compile(r"^\d+$")
_WHATSAPP_API_BASE = "https://graph.facebook.com/v21.0"
_WHATSAPP_VERIFY_TIMEOUT_SECONDS = 15.0


class AuthorizeOut(BaseModel):
    url: str


class OAuthAppCredentialsIn(BaseModel):
    """`PUT /{key}/app-credentials` — la app OAuth PROPIA del tenant para un
    conector oficial (ver `edecan_api.oauth_app_credentials`). `client_secret`
    es opcional solo porque algunas apps (p. ej. X con PKCE puro) no lo
    exigen; cada `Connector` concreto valida si SU proveedor sí lo necesita."""

    client_id: str = Field(min_length=1)
    client_secret: str | None = None


class TwilioCredentialsIn(BaseModel):
    account_sid: str
    auth_token: str
    phone_number: str


class BotTokenCredentialsIn(BaseModel):
    bot_token: str


class WhatsAppCredentialsIn(BaseModel):
    # `validate_` con alias "validate" (no se llama `validate` a secas):
    # `pydantic.BaseModel` ya trae un método de clase deprecado con ese
    # nombre, y un campo igual dispara un `UserWarning` de shadowing en cada
    # import de este módulo (mismo criterio que `edecan_api.routers
    # .credentials.LLMCredentialsIn`).
    model_config = ConfigDict(populate_by_name=True)

    access_token: str
    phone_number_id: str
    validate_: bool = Field(default=True, alias="validate")


def _connector_account_out(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "connector_key": row["connector_key"],
        "external_account_id": row.get("external_account_id"),
        "display_name": row.get("display_name"),
        "status": row.get("status"),
        "scopes": row.get("scopes") or [],
        "created_at": row.get("created_at"),
    }


def _state_signature(payload: bytes, key: str, secret: str) -> bytes:
    mac = hmac.new(secret.encode("utf-8"), payload + key.encode("utf-8"), hashlib.sha256)
    return mac.digest()[:_STATE_SIG_LEN]


def _create_state_token(*, tenant_id: uuid.UUID, key: str, secret: str) -> str:
    exp = int(time.time()) + STATE_TTL_SECONDS
    payload = tenant_id.bytes + exp.to_bytes(_STATE_EXP_LEN, "big")
    signature = _state_signature(payload, key, secret)
    return base64.urlsafe_b64encode(payload + signature).rstrip(b"=").decode("ascii")


def _decode_state_token(token: str, *, secret: str, expected_key: str) -> uuid.UUID:
    padded = token + "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded)
    except ValueError as exc:
        raise TokenError(f"state inválido: {exc}") from exc

    if len(raw) != _STATE_TENANT_LEN + _STATE_EXP_LEN + _STATE_SIG_LEN:
        raise TokenError("state con formato inesperado.")

    payload, signature = raw[:-_STATE_SIG_LEN], raw[-_STATE_SIG_LEN:]
    if not hmac.compare_digest(signature, _state_signature(payload, expected_key, secret)):
        raise TokenError("state no corresponde a este conector.")

    tenant_bytes = payload[:_STATE_TENANT_LEN]
    exp = int.from_bytes(payload[_STATE_TENANT_LEN:], "big")
    if exp < int(time.time()):
        raise TokenError("state inválido o expirado.")
    return uuid.UUID(bytes=tenant_bytes)


def _bundle_account_hint(bundle: Any) -> str:
    """`external_account_id` best-effort: los conectores no siempre exponen el id
    de cuenta del proveedor en el propio `TokenBundle`, así que se usa un hash
    corto y estable del access token como identificador de conexión provisional.
    """
    return hashlib.sha256(bundle.access_token.encode("utf-8")).hexdigest()[:16]


async def _verify_twilio_phone_ownership(
    account_sid: str, auth_token: str, phone_number: str, *, http_client: httpx.AsyncClient
) -> str:
    """Confirma contra la API real de Twilio que `phone_number` es un número
    de la cuenta `account_sid` (autenticada con `auth_token`) — sin esto,
    `connect_twilio` solo validaba el FORMATO de las tres credenciales
    (regex), así que cualquier tenant autenticado podía declararse dueño del
    número E.164 de otro (hallazgo de auditoría aislamiento-multi-tenant).

    Lanza `HTTPException` (400 si las credenciales/número no verifican, 502
    si Twilio no respondió) y NO devuelve nada si la verificación pasa —
    "fail closed": ante cualquier duda no se persiste la credencial.
    """
    try:
        response = await http_client.get(
            f"{_TWILIO_API_BASE}/Accounts/{account_sid}/IncomingPhoneNumbers.json",
            params={"PhoneNumber": phone_number},
            auth=httpx.BasicAuth(account_sid, auth_token),
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No pudimos verificar el número con Twilio; inténtalo de nuevo en unos minutos.",
        ) from exc

    if response.status_code == 401:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Twilio rechazó el Account SID / Auth Token indicados.",
        )
    if response.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No pudimos verificar el número con Twilio (respondió {response.status_code}).",
        )

    try:
        numbers = response.json().get("incoming_phone_numbers") or []
    except ValueError:
        numbers = []
    owned = next((entry for entry in numbers if entry.get("phone_number") == phone_number), None)
    if owned is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ese número no pertenece a la cuenta de Twilio indicada.",
        )
    phone_sid = str(owned.get("sid") or "")
    if not phone_sid.startswith("PN"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Twilio no devolvió la identidad válida de ese número.",
        )
    return phone_sid


async def _configure_twilio_incoming_webhook(
    account_sid: str,
    auth_token: str,
    phone_sid: str,
    webhook_url: str,
    *,
    http_client: httpx.AsyncClient,
) -> None:
    """Apunta el número verificado al receptor de llamadas entrantes de Edecan."""
    try:
        response = await http_client.post(
            f"{_TWILIO_API_BASE}/Accounts/{account_sid}/IncomingPhoneNumbers/{phone_sid}.json",
            data={"VoiceUrl": webhook_url, "VoiceMethod": "POST"},
            auth=httpx.BasicAuth(account_sid, auth_token),
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "Verificamos el número, pero Twilio no respondió al activar las llamadas entrantes."
            ),
        ) from exc
    if response.status_code not in {200, 201}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Twilio verificó el número, pero no permitió configurar su recepción de llamadas."
            ),
        )


@router.get("", dependencies=[Depends(rate_limit)])
async def list_connectors(
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    settings: Settings = Depends(get_settings),
) -> list[dict[str, Any]]:
    accounts = await repo.list_connector_accounts(tenant_id=current_user.tenant_id)
    by_key: dict[str, list[dict[str, Any]]] = {}
    # Las filas de config de app OAuth propia (`"{key}__app_config"`, ver
    # `edecan_api.oauth_app_credentials`) NUNCA deben aparecer como si fueran
    # una "cuenta conectada" -- se filtran a un dict aparte, `app_client_ids`.
    app_client_ids: dict[str, str] = {}
    for account in accounts:
        connector_key = account["connector_key"]
        if is_app_config_connector_key(connector_key):
            app_client_ids[base_connector_key(connector_key)] = account["external_account_id"]
            continue
        by_key.setdefault(connector_key, []).append(_connector_account_out(account))

    oauth_connectors = [
        {
            "key": key,
            "display_name": connector.display_name,
            "accounts": by_key.get(key, []),
            "app_configured": key in app_client_ids,
            "app_client_id_masked": (
                mask_client_id(app_client_ids[key]) if key in app_client_ids else None
            ),
            # Mismo cálculo que `authorize`/`callback` de abajo -- el tenant
            # necesita este valor EXACTO para registrar su app en la consola
            # del proveedor (Google/Meta/etc.), así que sale servido desde
            # una sola fuente de verdad en vez de que el frontend lo arme
            # aparte (WP-V8: antes no existía ningún lugar en la UI donde
            # verlo -- el botón "Configurar app OAuth" del mensaje de error
            # tampoco existía).
            "oauth_redirect_uri": (
                f"{settings.PUBLIC_BASE_URL.rstrip('/')}/v1/connectors/{key}/callback"
            ),
        }
        for key, connector in CONNECTORS.items()
    ]
    # Twilio no es OAuth, así que no está en `CONNECTORS` (ver docstring del
    # módulo) — se añade a mano para que el panel también liste sus cuentas.
    twilio_entry = {
        "key": TWILIO_CONNECTOR_KEY,
        "display_name": _TWILIO_DISPLAY_NAME,
        "accounts": by_key.get(TWILIO_CONNECTOR_KEY, []),
    }
    # Telegram/Discord tampoco son OAuth (ver `BOT_TOKEN_CONNECTOR_KEYS`
    # arriba): mismo motivo, misma solución — se añaden a mano.
    bot_token_entries = [
        {
            "key": key,
            "display_name": _BOT_TOKEN_DISPLAY_NAMES[key],
            "accounts": by_key.get(key, []),
        }
        for key in BOT_TOKEN_CONNECTOR_KEYS
    ]
    # WhatsApp (WP-V3-13) tampoco es OAuth (ver docstring del módulo): mismo
    # motivo, misma solución que Twilio/Telegram/Discord — se añade a mano.
    whatsapp_entry = {
        "key": WHATSAPP_CONNECTOR_KEY,
        "display_name": _WHATSAPP_DISPLAY_NAME,
        "accounts": by_key.get(WHATSAPP_CONNECTOR_KEY, []),
    }
    return [*oauth_connectors, twilio_entry, *bot_token_entries, whatsapp_entry]


@router.put("/{key}/app-credentials", status_code=status.HTTP_204_NO_CONTENT)
async def put_app_credentials(
    key: str,
    payload: OAuthAppCredentialsIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
) -> None:
    """El tenant pega la app OAuth que registró él mismo con el proveedor
    (ver `edecan_api.oauth_app_credentials`) -- sin esto, `authorize` de abajo
    rechaza cualquier intento de conectar `key`."""
    connector = CONNECTORS.get(key)
    if connector is None:
        raise HTTPException(status_code=404, detail=f"Conector desconocido: {key}")
    client_id = payload.client_id.strip()
    if not client_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="El client_id no puede estar vacío."
        )
    client_secret = (payload.client_secret or "").strip() or None
    await put_oauth_app_credentials(
        repo, vault, current_user.tenant_id, key, connector.display_name, client_id, client_secret
    )
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="connectors.app_credentials_set",
        target=key,
    )


@router.delete("/{key}/app-credentials", status_code=status.HTTP_204_NO_CONTENT)
async def delete_app_credentials(
    key: str,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> None:
    connector = CONNECTORS.get(key)
    if connector is None:
        raise HTTPException(status_code=404, detail=f"Conector desconocido: {key}")
    await delete_oauth_app_credentials(repo, current_user.tenant_id, key)
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="connectors.app_credentials_deleted",
        target=key,
    )


@router.get("/{key}/authorize", response_model=AuthorizeOut, dependencies=[Depends(rate_limit)])
async def authorize(
    key: str,
    current_user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
) -> AuthorizeOut:
    connector = CONNECTORS.get(key)
    if connector is None:
        raise HTTPException(status_code=404, detail=f"Conector desconocido: {key}")

    creds = await get_oauth_app_credentials(repo, vault, current_user.tenant_id, key)
    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Configura tu propia app OAuth de {connector.display_name} primero "
                f"(botón 'Configurar app OAuth' en su tarjeta, en Conectores)."
            ),
        )
    client_id, _client_secret = creds

    state = _create_state_token(
        tenant_id=current_user.tenant_id, key=key, secret=settings.JWT_SECRET
    )
    redirect_uri = f"{settings.PUBLIC_BASE_URL.rstrip('/')}/v1/connectors/{key}/callback"
    try:
        url = connector.auth_url(redirect_uri, state, client_id=client_id)
    except ConnectorError as exc:
        # Defensa en profundidad: `client_id` ya viene de una fila que el
        # tenant pegó, así que esto no debería dispararse en operación
        # normal -- pero sin este catch, cualquier `ConnectorError` (p. ej.
        # un conector que valide algo más del `client_id`) se propaga sin
        # capturar y FastAPI la convierte en un 500 genérico sin explicación.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"El conector '{key}' no está configurado correctamente: {exc}",
        ) from exc
    return AuthorizeOut(url=url)


# Sin `Depends(rate_limit)` a propósito: esta ruta la visita el navegador del
# usuario redirigido por el proveedor OAuth, sin `Authorization: Bearer` (ver
# docstring del módulo) — `rate_limit` exige `Depends(get_current_user)`, así
# que aplicarlo aquí devolvería 401 antes de validar `state` y rompería el
# flujo real de conexión de conectores.
@router.get("/{key}/callback")
async def callback(
    key: str,
    code: str,
    state: str,
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    connector = CONNECTORS.get(key)
    if connector is None:
        raise HTTPException(status_code=404, detail=f"Conector desconocido: {key}")

    try:
        tenant_id = _decode_state_token(state, secret=settings.JWT_SECRET, expected_key=key)
    except TokenError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    redirect_uri = f"{settings.PUBLIC_BASE_URL.rstrip('/')}/v1/connectors/{key}/callback"
    async with get_session(tenant_id) as session:
        repo = SqlRepo(session)
        vault = TokenVault(session, build_key_provider(settings))

        creds = await get_oauth_app_credentials(repo, vault, tenant_id, key)
        if creds is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Configura tu propia app OAuth de {connector.display_name} primero.",
            )
        client_id, client_secret = creds

        external_account_id: str | None = None
        connected_display_name: str | None = None
        try:
            async with httpx.AsyncClient(timeout=20.0) as http_client:
                bundle = await connector.exchange_code(
                    code,
                    redirect_uri,
                    http_client,
                    client_id=client_id,
                    client_secret=client_secret,
                    code_verifier=state,
                )
                if key == "linkedin":
                    profile = await get_linkedin_profile(http_client, bundle)
                    external_account_id = str(profile["sub"])
                    connected_display_name = str(
                        profile.get("name") or profile.get("email") or connector.display_name
                    )
        except ConnectorError as exc:
            # El proveedor rechazó el code, o la app del tenant está mal
            # configurada (p. ej. redirect_uri no coincide) -- nunca debe
            # llegar como 500 sin explicación.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"No se pudo completar la conexión con '{key}': {exc}",
            ) from exc

        account = await repo.create_connector_account(
            tenant_id=tenant_id,
            connector_key=key,
            external_account_id=external_account_id or _bundle_account_hint(bundle),
            display_name=connected_display_name or connector.display_name,
            scopes=bundle.scopes,
        )
        await vault.put(tenant_id, account["id"], bundle)
        await repo.add_audit_log(
            tenant_id=tenant_id, actor_user_id=None, action="connectors.connected", target=key
        )

    web_base = settings.WEB_BASE_URL.rstrip("/")
    return RedirectResponse(url=f"{web_base}/app/conectores?ok=1")


def _require_voice_telephony(tenant: TenantCtx) -> None:
    if not tenant.flags.get(FLAG_VOICE_TELEPHONY, False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La telefonía (Twilio) no está disponible en tu plan.",
        )


async def _check_phone_number_quota(repo: Repo, tenant: TenantCtx) -> None:
    # Default `0` (fail-closed), NUNCA `UNLIMITED` (barrido v7, WP-V7-08 lo
    # encontró y corrigió en `files.py`/`voice.py`; este archivo quedó fuera
    # del alcance de ese WP y se aplica acá, WP-V7-12, con el mismo criterio):
    # un `plan_key` huérfano (`edecan_api.deps.flags_for_plan` devuelve `{}`)
    # no debe caer en números de teléfono SIN NINGÚN límite. Hoy
    # `_require_voice_telephony` (arriba, llamado antes que esta función) ya
    # bloquea ese caso con 403 (flags={} => FLAG_VOICE_TELEPHONY por defecto
    # False), así que este default es defensa en profundidad, no el único
    # candado -- mismo criterio que `routers/voice.py::_check_voice_quota`.
    # `LIMIT_PHONE_NUMBERS` SIEMPRE viene explícito en
    # `edecan_schemas.plans.PLANES` para los 4 planes reales, así que este
    # default nunca se alcanza en operación normal.
    limit = tenant.flags.get(LIMIT_PHONE_NUMBERS, 0)
    if limit == UNLIMITED:
        return
    accounts = await repo.list_connector_accounts(tenant_id=tenant.tenant_id)
    connected = sum(1 for a in accounts if a["connector_key"] == TWILIO_CONNECTOR_KEY)
    if connected >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Alcanzaste tu límite de {limit} número(s) de teléfono de tu plan "
                f"'{tenant.plan_key}'."
            ),
        )


@router.put(
    "/twilio/credentials",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit)],
)
async def connect_twilio(
    payload: TwilioCredentialsIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    platform_repo: Repo = Depends(get_platform_repo),
    vault: TokenVault = Depends(get_vault),
    settings: Settings = Depends(get_settings),
) -> None:
    """Guarda la cuenta de Twilio del tenant (Account SID + Auth Token + número).

    Ver docstring del módulo: Twilio no pasa por `authorize`/`callback`
    (no es OAuth), así que este endpoint recibe las credenciales directo del
    formulario del panel (ya autenticado vía `Authorization: Bearer`) y las
    guarda con la misma forma que espera `edecan_premium` al leerlas.

    Gateado por el flag de plan `voice.telephony` y por la cuota
    `limits.phone_numbers` (ARCHITECTURE.md §10.13) — ambos recalculados
    server-side desde el `plan_key` del tenant, nunca confiados del payload.

    `phone_number` se verifica contra la API real de Twilio
    (`_verify_twilio_phone_ownership`) y contra otros tenants
    (`platform_repo.get_connector_account_by_external_id`, sesión sin RLS)
    antes de persistir nada — ver docstring del módulo.
    """
    tenant = current_user.tenant
    _require_voice_telephony(tenant)

    account_sid = payload.account_sid.strip()
    auth_token = payload.auth_token.strip()
    phone_number = payload.phone_number.strip()

    if not _TWILIO_SID_RE.match(account_sid):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Account SID inválido: debe empezar con 'AC' seguido de 32 "
                "caracteres hexadecimales."
            ),
        )
    if not _TWILIO_AUTH_TOKEN_RE.match(auth_token):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Auth Token inválido: debe tener 32 caracteres alfanuméricos.",
        )
    if not _E164_RE.match(phone_number):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Número de teléfono inválido: usa formato E.164, p. ej. +525512345678.",
        )

    await _check_phone_number_quota(repo, tenant)

    async with httpx.AsyncClient(timeout=_TWILIO_VERIFY_TIMEOUT_SECONDS) as http_client:
        phone_sid = await _verify_twilio_phone_ownership(
            account_sid, auth_token, phone_number, http_client=http_client
        )
        # Los tests y adaptadores antiguos pueden sustituir el verificador por
        # un no-op. En producción siempre devuelve el PN SID real.
        if phone_sid:
            await _configure_twilio_incoming_webhook(
                account_sid,
                auth_token,
                phone_sid,
                (f"{settings.PUBLIC_BASE_URL.rstrip('/')}/v1/phone/twilio/incoming"),
                http_client=http_client,
            )

    # Chequeo aplicativo (mensaje 409 claro y testeable sin Postgres real) —
    # `platform_repo` bypassa RLS a propósito, ver su docstring. El índice
    # único parcial de `ConnectorAccount` (`packages/db/edecan_db/models.py`)
    # es el respaldo atómico ante una carrera entre dos requests concurrentes.
    claimed_by = await platform_repo.get_connector_account_by_external_id(
        connector_key=TWILIO_CONNECTOR_KEY, external_account_id=phone_number
    )
    if claimed_by is not None and claimed_by["tenant_id"] != current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ese número de teléfono ya está conectado a otra cuenta.",
        )

    account = await repo.create_connector_account(
        tenant_id=current_user.tenant_id,
        connector_key=TWILIO_CONNECTOR_KEY,
        external_account_id=phone_number,
        display_name=phone_number,
        scopes=[account_sid],
    )
    await vault.put(
        current_user.tenant_id,
        account["id"],
        TokenBundle(access_token=auth_token, scopes=[account_sid]),
    )
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="connectors.connected",
        target=TWILIO_CONNECTOR_KEY,
    )


async def _verify_whatsapp_phone_ownership(
    access_token: str, phone_number_id: str, *, http_client: httpx.AsyncClient
) -> str:
    """Confirma contra la Graph API real de Meta que `access_token` puede leer
    `phone_number_id` — sin esto, `connect_whatsapp` solo validaría el FORMATO
    de las dos credenciales (regex/longitud), igual que `_validate_bot_token`
    hace con los tokens de bot de Telegram/Discord; a diferencia de esos dos,
    aquí SÍ hay una forma barata de verificar contra la API real, mismo
    espíritu "fail closed" que `_verify_twilio_phone_ownership`.

    Devuelve `display_phone_number` (el número humano-legible que Meta asocia
    a `phone_number_id`) para usarlo como `display_name` de la
    `connector_account` — ver docstring del módulo. Lanza `HTTPException`
    (400 si Meta rechaza el access token o no encuentra el `phone_number_id`,
    502 si Meta no respondió) y no devuelve nada si la verificación no pasa.
    """
    try:
        response = await http_client.get(
            f"{_WHATSAPP_API_BASE}/{phone_number_id}",
            params={"fields": "display_phone_number,verified_name"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "No pudimos verificar el phone_number_id con la Graph API de Meta; "
                "inténtalo de nuevo en unos minutos."
            ),
        ) from exc

    if response.status_code in (401, 403):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Meta rechazó el access token indicado (no autorizado para ese phone_number_id)."
            ),
        )
    if response.status_code == 404:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Meta no encontró ese phone_number_id (revisa que sea el id, no el "
                "número de teléfono)."
            ),
        )
    if response.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"No pudimos verificar el phone_number_id con Meta "
                f"(respondió {response.status_code})."
            ),
        )

    try:
        payload = response.json()
    except ValueError:
        payload = {}
    display_phone_number = payload.get("display_phone_number")
    if not display_phone_number:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Meta no devolvió un display_phone_number para ese phone_number_id.",
        )
    return str(display_phone_number)


def _validate_whatsapp_credentials_format(access_token: str, phone_number_id: str) -> None:
    """Valida el FORMATO de las credenciales de WhatsApp (mismo criterio que
    `_validate_bot_token`: no vacío + longitud/patrón mínimo razonable). Que de
    verdad pertenezcan a una app/número real de Meta lo confirma
    `_verify_whatsapp_phone_ownership`, aparte."""
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El access token de WhatsApp no puede estar vacío.",
        )
    if len(access_token) < _WHATSAPP_TOKEN_MIN_LEN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"El access token de WhatsApp parece inválido (mínimo "
                f"{_WHATSAPP_TOKEN_MIN_LEN} caracteres)."
            ),
        )
    if not phone_number_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El phone_number_id de WhatsApp no puede estar vacío.",
        )
    if not _WHATSAPP_PHONE_NUMBER_ID_RE.match(phone_number_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El phone_number_id de WhatsApp debe ser numérico (solo dígitos, sin '+').",
        )


async def _upsert_whatsapp_account(
    repo: Repo, *, tenant_id: uuid.UUID, phone_number_id: str, display_name: str
) -> dict[str, Any]:
    """Reemplaza la (única) cuenta de WhatsApp del tenant si ya existía — ver
    docstring del módulo: a diferencia de Twilio (varias cuentas por tenant,
    hasta la cuota del plan), WhatsApp es SINGLETON por tenant, y `Repo` no
    expone una operación de UPDATE sobre `connector_accounts`, así que el
    upsert se implementa borrando la fila anterior (si la había) y creando
    una nueva."""
    existentes = await repo.list_connector_accounts(tenant_id=tenant_id)
    for cuenta in existentes:
        if cuenta["connector_key"] == WHATSAPP_CONNECTOR_KEY:
            await repo.delete_connector_account(tenant_id=tenant_id, account_id=cuenta["id"])
    return await repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key=WHATSAPP_CONNECTOR_KEY,
        external_account_id=phone_number_id,
        display_name=display_name,
        scopes=[phone_number_id],
    )


@router.put(
    "/whatsapp/credentials",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit)],
)
async def connect_whatsapp(
    payload: WhatsAppCredentialsIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
) -> None:
    """Guarda la cuenta de WhatsApp Business Platform del tenant (access token
    permanente de su app de Meta + `phone_number_id` de su número ya
    verificado) — ver docstring del módulo (WP-V3-13, `ARCHITECTURE.md` §12.b).

    Mismo patrón no-OAuth que `connect_twilio` (ruta fija, no vive en
    `CONNECTORS`), pero SINGLETON por tenant (`_upsert_whatsapp_account`) y
    sin flag de plan que la gatee (igual que `connect_bot_token`: el flag
    `connectors.messaging` gatea las TOOLS de mensajería, no la conexión de
    la credencial en sí — ver `packages/messaging/edecan_messaging/tools.py`).

    `payload.validate_` (alias `validate`, default `True`) controla si se hace
    el ping real a la Graph API de Meta (`_verify_whatsapp_phone_ownership`)
    antes de persistir — con `validate=False` solo se valida FORMATO. Con
    validación real, el `display_name` guardado es el `display_phone_number`
    que devuelve Meta; sin ella, cae al propio `phone_number_id`.
    """
    access_token = payload.access_token.strip()
    phone_number_id = payload.phone_number_id.strip()
    _validate_whatsapp_credentials_format(access_token, phone_number_id)

    display_name = phone_number_id
    if payload.validate_:
        async with httpx.AsyncClient(timeout=_WHATSAPP_VERIFY_TIMEOUT_SECONDS) as http_client:
            display_name = await _verify_whatsapp_phone_ownership(
                access_token, phone_number_id, http_client=http_client
            )

    account = await _upsert_whatsapp_account(
        repo,
        tenant_id=current_user.tenant_id,
        phone_number_id=phone_number_id,
        display_name=display_name,
    )
    await vault.put(
        current_user.tenant_id,
        account["id"],
        TokenBundle(access_token=access_token, scopes=[phone_number_id]),
    )
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="connectors.connected",
        target=WHATSAPP_CONNECTOR_KEY,
    )


def _validate_bot_token(key: str, bot_token: str) -> str:
    """Valida el FORMATO del token de bot (Telegram/Discord): solo exige que
    no esté vacío y tenga una longitud mínima razonable.

    A diferencia de Twilio (`_TWILIO_*_RE` + `_verify_twilio_phone_ownership`
    contra la API real), no hay aquí una forma barata y estable de verificar
    la propiedad/validez de un token de bot antes de guardarlo — la
    verificación real ocurre en el primer uso: si el token es inválido,
    `edecan_messaging` (`packages/messaging/`) lo reportará al enviar/leer.
    """
    token = bot_token.strip()
    nombre = _BOT_TOKEN_DISPLAY_NAMES.get(key, key)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"El token del bot de {nombre} no puede estar vacío.",
        )
    if len(token) < _MIN_BOT_TOKEN_LEN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"El token del bot de {nombre} parece inválido (muy corto).",
        )
    return token


@router.put(
    "/{key}/credentials",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit)],
)
async def connect_bot_token(
    key: str,
    payload: BotTokenCredentialsIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
) -> None:
    """Guarda el token de bot del tenant para Telegram o Discord (ninguno es
    OAuth — ver `BOT_TOKEN_CONNECTOR_KEYS` y el docstring del módulo).

    Ruta GENÉRICA por `key`, a propósito distinta de `PUT /twilio/credentials`
    y `PUT /whatsapp/credentials` (rutas fijas, con validación de
    formato/propiedad mucho más estricta): como esta función se declara
    DESPUÉS de `connect_twilio`/`connect_whatsapp` en este módulo, Starlette
    ya prueba esos literales exactos primero (las rutas se evalúan en el
    orden en que se declaran) — pero igual se rechazan aquí "twilio"/
    "whatsapp" explícitamente (404, ninguno está en
    `BOT_TOKEN_CONNECTOR_KEYS`) como defensa adicional ante un futuro
    reordenamiento accidental del archivo.

    Reutiliza `_bundle_account_hint` (el mismo helper que usa `callback` para
    los conectores OAuth) para derivar un `external_account_id` estable a
    partir del propio token, ya que ni Telegram ni Discord exponen aquí un id
    de cuenta natural sin llamarlos.
    """
    if key not in BOT_TOKEN_CONNECTOR_KEYS:
        raise HTTPException(status_code=404, detail=f"Conector desconocido: {key}")

    bot_token = _validate_bot_token(key, payload.bot_token)
    display_name = _BOT_TOKEN_DISPLAY_NAMES[key]
    bundle = TokenBundle(access_token=bot_token)

    account = await repo.create_connector_account(
        tenant_id=current_user.tenant_id,
        connector_key=key,
        external_account_id=_bundle_account_hint(bundle),
        display_name=display_name,
        scopes=[],
    )
    await vault.put(current_user.tenant_id, account["id"], bundle)
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="connectors.connected",
        target=key,
    )


@router.delete(
    "/{key}/{account_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit)],
)
async def disconnect(
    key: str,
    account_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> None:
    if (
        key not in CONNECTORS
        and key != TWILIO_CONNECTOR_KEY
        and key != WHATSAPP_CONNECTOR_KEY
        and key not in BOT_TOKEN_CONNECTOR_KEYS
    ):
        raise HTTPException(status_code=404, detail=f"Conector desconocido: {key}")
    deleted = await repo.delete_connector_account(
        tenant_id=current_user.tenant_id, account_id=account_id
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Cuenta de conector no encontrada.")
