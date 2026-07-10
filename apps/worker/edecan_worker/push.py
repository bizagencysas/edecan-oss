"""Envío de notificaciones push nativas (APNs/FCM) — 100% bring-your-own por
tenant (`ARCHITECTURE.md` §14, dueño WP-V5-13; ver `docs/notificaciones-push.md`).

Mismo patrón anti-fuga que el resto de proveedores bring-your-own de Edecán
(`packages/llm/edecan_llm/router.py::_build_provider_from_config`, el
hallazgo crítico documentado en `DIRECCION_ACTUAL.md` "v4 completado" — la
clase de bug más seria vista hasta ahora en este repo: un campo vacío del
tenant cayendo en silencio a un secreto de PLATAFORMA): un tenant sin
credencial propia JAMÁS recibe un fallback silencioso a ningún secreto
compartido. No existe NINGÚN campo de push en `edecan_worker.config.Settings`
— a propósito, para que sea estructuralmente imposible que este módulo lea
una credencial que no sea la del propio tenant. `cargar_credenciales_push`
lee EXCLUSIVAMENTE del `TokenVault` del tenant (`connector_account` con
`connector_key="push"`, escrita por `PUT /v1/devices/push/credentials`,
`apps/api/edecan_api/routers/devices.py`) — cualquier ausencia (tenant sin
conectar nada, vault caído, JSON corrupto) se trata igual: log de advertencia
+ "sin credenciales", nunca una excepción que tumbe el job ni un secreto
prestado. Mismo criterio de lectura (SQL parametrizado directo sobre
`connector_accounts` + `vault.get`) que `edecan_premium.telephony.for_tenant`
(Twilio) y `edecan_messaging._creds.resolver_credenciales`.

El envío en sí es SIEMPRE best-effort (`edecan_worker.handlers.send_reminder`,
canal `"mobile"`): el recordatorio ya quedó guardado como mensaje de chat
ANTES de intentar el push, así que cualquier fallo de push —sin credenciales,
sin dispositivos, red caída, token vencido— nunca hace que el job falle ni
que el recordatorio "se pierda". `enviar_push_a_usuario` nunca lanza: siempre
devuelve un `ResultadoEnvioPush`, en el peor caso `(0, 0)` con una advertencia
logueada.

`pyjwt`/`cryptography` SÍ están declarados en `apps/worker/pyproject.toml`
(WP-V5-01) — igual, `_construir_jwt_apns`/`_construir_jwt_fcm` los importan de
forma perezosa (dentro de la función, no al tope del módulo) con
`try/except ImportError` y un mensaje claro, mismo criterio defensivo que el
resto de este paquete (`edecan_worker.deps` con `edecan_core`/`edecan_db`)
para tolerar un self-host con un checkout parcial o un lockfile
desincronizado.

## HTTP/2 en APNs — decisión documentada

El endpoint moderno de APNs (`api(.sandbox).push.apple.com`) está pensado
para HTTP/2, pero este repo NO declara el extra `h2` de httpx (`uv.lock` no
trae el paquete `h2` — verificado a mano). `httpx.AsyncClient` sin
`http2=True` (el default de este módulo) habla HTTP/1.1 puro; pedirle
`http2=True` sin `h2` instalado directamente lanzaría un `ImportError` de
httpx. Se deja así a propósito (no se toca `apps/worker/pyproject.toml`,
fuera del alcance de este paquete de trabajo): la negociación ALPN de un
cliente sin soporte h2 simplemente no ofrece "h2" como protocolo durante el
handshake TLS, y el servidor de Apple (que soporta ALPN estándar) sirve la
misma request igual sobre HTTP/1.1 en la inmensa mayoría de los casos
reportados en la práctica. Si algún tenant reporta pushes de APNs que no
llegan, la primera hipótesis a descartar es esta: agregar `h2>=4` a las
dependencias del worker y pasar `http2=True` a los `httpx.AsyncClient` de
este módulo restauraría el camino "oficial" recomendado por Apple.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import text as sql_text

from edecan_worker.deps import Deps

logger = logging.getLogger(__name__)

PUSH_CONNECTOR_KEY = "push"

_APNS_HOST_PRODUCTION = "api.push.apple.com"
_APNS_HOST_SANDBOX = "api.sandbox.push.apple.com"
_APNS_TIMEOUT_SECONDS = 10.0

_FCM_TOKEN_URL = "https://oauth2.googleapis.com/token"
_FCM_TOKEN_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
_FCM_SEND_BASE_URL = "https://fcm.googleapis.com/v1/projects"
_FCM_TOKEN_LIFETIME_SECONDS = 3600
_FCM_TIMEOUT_SECONDS = 10.0

# Status HTTP que, para cada plataforma, Edecán interpreta como "este
# push_token ya no sirve, límpialo" (ver `_despachar_a_dispositivo`):
# - APNs: 410 (Unregistered) es la señal oficial de Apple de "el token ya no
#   es válido para este topic, bórralo" (docs de Apple, apns-response reason).
#   400 (BadDeviceToken, el token no tiene la forma correcta) también implica
#   "esto nunca va a funcionar". 404 se incluye por completitud de este WP
#   aunque, en la práctica, APNs solo lo devuelve para una ruta HTTP mal
#   formada (bug del cliente) — como este módulo siempre construye la URL a
#   partir de un `push_token` guardado tal cual llegó, un 404 real en este
#   codepath es indistinguible de un token corrupto, así que limpiarlo es la
#   opción segura por defecto.
# - FCM: 404 (NOT_FOUND / UNREGISTERED) es la señal oficial de Google de
#   "este token de registro ya no existe".
_APNS_TOKEN_INVALIDO_STATUSES = frozenset({400, 404, 410})
_FCM_TOKEN_INVALIDO_STATUSES = frozenset({404})


class PushNoDisponibleError(Exception):
    """`pyjwt`/`cryptography` no están instalados en este entorno — ver el
    docstring del módulo (import perezoso defensivo)."""


@dataclass(frozen=True)
class ResultadoEnvioPush:
    """Conteo de un `enviar_push_a_usuario`: nunca lanza, en el peor caso
    devuelve `ResultadoEnvioPush(0, 0)` (ver docstring del módulo)."""

    enviados: int
    fallidos: int


# ---------------------------------------------------------------------------
# Credenciales bring-your-own del tenant (ver docstring del módulo).
# ---------------------------------------------------------------------------


async def cargar_credenciales_push(
    session: Any, vault: Any, tenant_id: UUID
) -> dict[str, Any] | None:
    """Config push del tenant: `{"apns": {...}}` y/o `{"fcm": {...}}` (las
    formas exactas que guarda `PUT /v1/devices/push/credentials`), o `None`
    si no conectó nada o cualquier paso de la lectura falla.

    Calca el patrón de `edecan_messaging._creds.resolver_credenciales`/
    `edecan_premium.telephony.for_tenant`: busca la `connector_account` más
    reciente con `connector_key="push"` para `tenant_id` y le pide el
    `TokenBundle` a `vault`. Nunca lanza: cualquier excepción (sesión caída,
    vault caído, JSON corrupto, forma inesperada) se trata igual que "el
    tenant no conectó nada" — nunca revienta el job por esto, y JAMÁS cae a
    ningún secreto de `Settings`/plataforma (no existe ninguno de push, ver
    docstring del módulo).
    """
    try:
        row = (
            await session.execute(
                sql_text(
                    "SELECT id FROM connector_accounts WHERE tenant_id = :tenant_id "
                    "AND connector_key = :connector_key ORDER BY created_at DESC LIMIT 1"
                ),
                {"tenant_id": tenant_id, "connector_key": PUSH_CONNECTOR_KEY},
            )
        ).mappings().first()
        if row is None:
            return None

        bundle = await vault.get(tenant_id=tenant_id, connector_account_id=row["id"])
        if bundle is None or not getattr(bundle, "access_token", None):
            return None

        data = json.loads(bundle.access_token)
        return data if isinstance(data, dict) else None
    except Exception:
        logger.warning(
            "push: no se pudo cargar la config push del tenant_id=%s; se trata "
            "como 'sin credenciales conectadas' (nunca se usa un secreto de "
            "plataforma).",
            tenant_id,
            exc_info=True,
        )
        return None


# ---------------------------------------------------------------------------
# APNs
# ---------------------------------------------------------------------------


def _construir_jwt_apns(cred_apns: dict[str, Any]) -> str:
    try:
        import jwt as pyjwt
    except ImportError as exc:  # pragma: no cover - defensivo, ver docstring del módulo
        raise PushNoDisponibleError(
            "Enviar push por APNs requiere el paquete 'pyjwt' (con 'cryptography' "
            "como backend de firma ES256) instalado en el worker."
        ) from exc

    ahora = int(time.time())
    return pyjwt.encode(
        {"iss": cred_apns["team_id"], "iat": ahora},
        cred_apns["p8_key"],
        algorithm="ES256",
        headers={"kid": cred_apns["key_id"]},
    )


async def enviar_apns(
    cred_apns: dict[str, Any], push_token: str, titulo: str, cuerpo: str
) -> httpx.Response:
    """`POST /3/device/{push_token}` de APNs con un JWT de proveedor firmado
    ES256 con la `.p8` del propio tenant.

    `cred_apns` es la forma exacta guardada por `PUT /v1/devices/push/
    credentials` (`team_id`, `key_id`, `bundle_id`, `p8_key`, `environment`
    opcional, `"production"` por defecto). No lanza por un status HTTP
    distinto de 200 — quien llama decide qué hacer (incluida la limpieza de
    tokens inválidos, ver `_despachar_a_dispositivo`) — pero sí puede lanzar
    `PushNoDisponibleError` (dependencia faltante) o una excepción de `httpx`
    (red caída).
    """
    token_proveedor = _construir_jwt_apns(cred_apns)
    host = (
        _APNS_HOST_SANDBOX
        if cred_apns.get("environment") == "sandbox"
        else _APNS_HOST_PRODUCTION
    )
    headers = {
        "authorization": f"bearer {token_proveedor}",
        "apns-topic": cred_apns["bundle_id"],
        "apns-push-type": "alert",
    }
    body = {"aps": {"alert": {"title": titulo, "body": cuerpo}, "sound": "default"}}

    async with httpx.AsyncClient(timeout=_APNS_TIMEOUT_SECONDS) as client:
        return await client.post(
            f"https://{host}/3/device/{push_token}", headers=headers, json=body
        )


# ---------------------------------------------------------------------------
# FCM
# ---------------------------------------------------------------------------


def _construir_jwt_fcm(service_account: dict[str, Any]) -> str:
    try:
        import jwt as pyjwt
    except ImportError as exc:  # pragma: no cover - defensivo, ver docstring del módulo
        raise PushNoDisponibleError(
            "Enviar push por FCM requiere el paquete 'pyjwt' (con 'cryptography' "
            "como backend de firma RS256) instalado en el worker."
        ) from exc

    ahora = int(time.time())
    payload = {
        "iss": service_account["client_email"],
        "scope": _FCM_TOKEN_SCOPE,
        "aud": _FCM_TOKEN_URL,
        "iat": ahora,
        "exp": ahora + _FCM_TOKEN_LIFETIME_SECONDS,
    }
    return pyjwt.encode(payload, service_account["private_key"], algorithm="RS256")


async def _canjear_access_token_fcm(
    service_account: dict[str, Any], *, client: httpx.AsyncClient
) -> str:
    assertion = _construir_jwt_fcm(service_account)
    response = await client.post(
        _FCM_TOKEN_URL,
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        },
    )
    response.raise_for_status()
    return response.json()["access_token"]


async def enviar_fcm(
    cred_fcm: dict[str, Any], push_token: str, titulo: str, cuerpo: str
) -> httpx.Response:
    """OAuth2 JWT-bearer contra `oauth2.googleapis.com/token` (firmado RS256
    con la `private_key` del service account del propio tenant) y luego
    `POST fcm.googleapis.com/v1/projects/{project_id}/messages:send`.

    `cred_fcm` es la forma exacta guardada por `PUT /v1/devices/push/
    credentials`: `service_account_json` (el JSON completo del service
    account de GCP del tenant, como string) y `project_id` (ya resuelto por
    el router al guardar, ver `apps/api/edecan_api/routers/devices.py`).
    Puede lanzar `PushNoDisponibleError`, un `httpx.HTTPStatusError` (si
    Google rechaza el canje del token) o una excepción de red — quien llama
    (`_despachar_a_dispositivo`) decide qué hacer con cualquiera de las tres.
    """
    raw = cred_fcm["service_account_json"]
    service_account = json.loads(raw) if isinstance(raw, str) else raw
    project_id = cred_fcm.get("project_id") or service_account.get("project_id")

    async with httpx.AsyncClient(timeout=_FCM_TIMEOUT_SECONDS) as client:
        access_token = await _canjear_access_token_fcm(service_account, client=client)
        return await client.post(
            f"{_FCM_SEND_BASE_URL}/{project_id}/messages:send",
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "message": {
                    "token": push_token,
                    "notification": {"title": titulo, "body": cuerpo},
                }
            },
        )


# ---------------------------------------------------------------------------
# Despacho a todos los dispositivos activos del usuario.
# ---------------------------------------------------------------------------


async def _listar_dispositivos_con_push(
    session: Any, tenant_id: UUID, user_id: UUID
) -> list[dict[str, Any]]:
    result = await session.execute(
        sql_text(
            "SELECT id, push_token, push_platform FROM devices "
            "WHERE tenant_id = :tenant_id AND user_id = :user_id "
            "AND status = 'active' AND push_token IS NOT NULL"
        ),
        {"tenant_id": tenant_id, "user_id": user_id},
    )
    return [dict(row) for row in result.mappings().all()]


async def _limpiar_push_token(session: Any, device_id: Any) -> None:
    """Se llama SOLO cuando el proveedor (APNs/FCM) confirmó que el token ya
    no sirve (ver `_APNS_TOKEN_INVALIDO_STATUSES`/`_FCM_TOKEN_INVALIDO_STATUSES`
    arriba). Limpia `push_token` Y `push_platform` (no solo el token) a
    propósito: deja el dispositivo en un estado coherente de "sin push
    configurado" — igual que uno recién emparejado que todavía no registró
    ninguno — en vez de un `push_platform` huérfano sin token que lo
    acompañe. Nunca lanza: si la propia limpieza falla, el peor caso es
    reintentar el envío una vez más la próxima vez (no es grave — todo este
    módulo es best-effort, ver su docstring)."""
    try:
        await session.execute(
            sql_text(
                "UPDATE devices SET push_token = NULL, push_platform = NULL, "
                "updated_at = now() WHERE id = :id"
            ),
            {"id": device_id},
        )
    except Exception:
        logger.warning(
            "push: no se pudo limpiar el push_token del device_id=%s", device_id, exc_info=True
        )


async def _despachar_a_dispositivo(
    session: Any, config: dict[str, Any], dispositivo: dict[str, Any], titulo: str, cuerpo: str
) -> bool:
    """`True` si el proveedor confirmó la entrega (HTTP 200). Nunca lanza:
    dependencia faltante, red caída, o cualquier otro error del proveedor
    cuenta como fallo de ESTE dispositivo y no debe frenar el resto del lote
    (ver `enviar_push_a_usuario`)."""
    plataforma = dispositivo.get("push_platform")
    push_token = dispositivo.get("push_token")
    if not push_token or plataforma not in ("apns", "fcm"):
        return False

    cred = config.get(plataforma)
    if not cred:
        logger.warning(
            "push: dispositivo %s pide '%s' pero el tenant no conectó esa credencial",
            dispositivo.get("id"),
            plataforma,
        )
        return False

    try:
        if plataforma == "apns":
            response = await enviar_apns(cred, push_token, titulo, cuerpo)
            statuses_invalidos = _APNS_TOKEN_INVALIDO_STATUSES
        else:
            response = await enviar_fcm(cred, push_token, titulo, cuerpo)
            statuses_invalidos = _FCM_TOKEN_INVALIDO_STATUSES
    except Exception:
        logger.warning(
            "push: fallo enviando a device_id=%s (%s)",
            dispositivo.get("id"),
            plataforma,
            exc_info=True,
        )
        return False

    if response.status_code == 200:
        return True

    logger.warning(
        "push: %s respondió %s para device_id=%s",
        plataforma,
        response.status_code,
        dispositivo.get("id"),
    )
    if response.status_code in statuses_invalidos:
        await _limpiar_push_token(session, dispositivo["id"])
    return False


async def enviar_push_a_usuario(
    deps: Deps, *, tenant_id: UUID, user_id: UUID, titulo: str, cuerpo: str
) -> ResultadoEnvioPush:
    """Envía un push a TODOS los dispositivos `active` de `user_id` que
    tengan `push_token` registrado, despachando por `push_platform`.

    SIEMPRE best-effort: nunca lanza (ver docstring del módulo) — cualquier
    ausencia de credencial/dispositivo es `ResultadoEnvioPush(0, 0)` con una
    advertencia logueada, nunca una excepción que interrumpa a quien llama
    (`edecan_worker.handlers.send_reminder`, canal `"mobile"`). Un fallo
    parcial (algunos dispositivos sí, otros no) nunca frena el resto del
    lote — ver `_despachar_a_dispositivo`.
    """
    try:
        async with deps.session_factory(None) as session:
            vault = deps.vault(session)
            config = await cargar_credenciales_push(session, vault, tenant_id)
            if config is None:
                logger.warning(
                    "push: tenant_id=%s sin credenciales push conectadas; 0 enviados",
                    tenant_id,
                )
                return ResultadoEnvioPush(0, 0)

            dispositivos = await _listar_dispositivos_con_push(session, tenant_id, user_id)
            if not dispositivos:
                logger.warning(
                    "push: user_id=%s sin dispositivos activos con push_token; 0 enviados",
                    user_id,
                )
                return ResultadoEnvioPush(0, 0)

            enviados = 0
            fallidos = 0
            for dispositivo in dispositivos:
                exito = await _despachar_a_dispositivo(session, config, dispositivo, titulo, cuerpo)
                if exito:
                    enviados += 1
                else:
                    fallidos += 1
            return ResultadoEnvioPush(enviados, fallidos)
    except Exception:
        logger.warning(
            "push: fallo inesperado despachando a tenant_id=%s user_id=%s",
            tenant_id,
            user_id,
            exc_info=True,
        )
        return ResultadoEnvioPush(0, 0)
