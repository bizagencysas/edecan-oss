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

El envío en sí es SIEMPRE best-effort. Los recordatorios guardan primero su
mensaje y `edecan_worker.universal_notifications` guarda primero una actividad
idempotente; solo después llaman este módulo. Cualquier fallo de push —sin
credenciales, sin dispositivos, red caída, token vencido— nunca hace que el
resultado durable "se pierda". `enviar_push_a_usuario` nunca lanza: siempre
devuelve un `ResultadoEnvioPush`, en el peor caso `(0, 0)` con una advertencia
logueada.

`pyjwt`/`cryptography` SÍ están declarados en `apps/worker/pyproject.toml`
(WP-V5-01) — igual, `_construir_jwt_apns`/`_construir_jwt_fcm` los importan de
forma perezosa (dentro de la función, no al tope del módulo) con
`try/except ImportError` y un mensaje claro, mismo criterio defensivo que el
resto de este paquete (`edecan_worker.deps` con `edecan_core`/`edecan_db`)
para tolerar un self-host con un checkout parcial o un lockfile
desincronizado.

## Por qué cada intento deja una fila en `audit_log`

Best-effort no puede significar "sin rastro". En la instalación de escritorio
el worker corre como sidecar de Tauri y su stdout termina en `/dev/null`, así
que todo lo que este módulo contaba con `logger.warning` era irrecuperable: un
403, un 429 o una excepción de red se veían EXACTAMENTE igual que un envío
aceptado por Apple, y "no me llegó la notificación" no se podía diagnosticar
después. Por eso cada salida de `_despachar_a_dispositivo` —y también los dos
cortes tempranos de `enviar_push_a_usuario`— llama a
`edecan_core.notifications.record_push_delivery`. La fila guarda solo enums,
el status, el `reason` del proveedor ya saneado y el `apns-id`: nunca el
título, el cuerpo ni el push token.

## HTTP/2 en APNs

APNs Provider API exige HTTP/2. El worker declara el extra `httpx[http2]`
y el cliente de Apple se crea siempre con `http2=True`; no existe una ruta
HTTP/1.1 que pueda aparentar éxito local y fallar al llegar a Apple.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
from edecan_core.notifications import record_push_delivery
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

# Solo estas claves de navegación, todas opacas, pueden acompañar a una
# notificación. El allowlist evita que un productor termine enviando por
# accidente prompts, nombres de archivo, resultados o errores sensibles.
_PUSH_DATA_KEYS = frozenset(
    {
        "route",
        "kind",
        "event",
        "event_key",
        "chat_id",
        "artifact_id",
        "resource_id",
        "deeplink",
    }
)
_MAX_PUSH_DATA_VALUE_CHARS = 512

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
            (
                await session.execute(
                    sql_text(
                        "SELECT id FROM connector_accounts WHERE tenant_id = :tenant_id "
                        "AND connector_key = :connector_key ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"tenant_id": tenant_id, "connector_key": PUSH_CONNECTOR_KEY},
                )
            )
            .mappings()
            .first()
        )
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
    cred_apns: dict[str, Any],
    push_token: str,
    titulo: str,
    cuerpo: str,
    *,
    data: Mapping[str, str] | None = None,
    category: str | None = None,
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

    `category` (opcional) se inyecta DENTRO de `aps` (`aps.category`): es la
    categoría de notificación de APNs que el cliente usa para clasificar la
    entrega (p. ej. `"GYM_CHECKIN"`), no un campo de navegación libre.
    """
    token_proveedor = _construir_jwt_apns(cred_apns)
    host = (
        _APNS_HOST_SANDBOX if cred_apns.get("environment") == "sandbox" else _APNS_HOST_PRODUCTION
    )
    otro_host = _APNS_HOST_PRODUCTION if host == _APNS_HOST_SANDBOX else _APNS_HOST_SANDBOX
    headers = {
        "authorization": f"bearer {token_proveedor}",
        "apns-topic": cred_apns["bundle_id"],
        "apns-push-type": "alert",
    }
    aps: dict[str, Any] = {"alert": {"title": titulo, "body": cuerpo}, "sound": "default"}
    if category:
        aps["category"] = category
    body: dict[str, Any] = {"aps": aps}
    body.update(_normalizar_data_push(data))

    async with httpx.AsyncClient(timeout=_APNS_TIMEOUT_SECONDS, http2=True) as client:
        response = await client.post(
            f"https://{host}/3/device/{push_token}", headers=headers, json=body
        )
        # El token APNs es POR entorno: un build por cable registra en sandbox y
        # un build de TestFlight/App Store en production. Si el `environment`
        # configurado no coincide, Apple responde `BadDeviceToken`; se reintenta
        # UNA vez contra el otro host para que el push "simplemente funcione"
        # sin flipear credenciales a mano (fix del "mal push" 21-ago-2026).
        if response.status_code == 400 and _motivo_del_proveedor(response) == "BadDeviceToken":
            retry = await client.post(
                f"https://{otro_host}/3/device/{push_token}", headers=headers, json=body
            )
            if retry.status_code == 200:
                return retry
            if _motivo_del_proveedor(retry) == "BadDeviceToken":
                logger.error(
                    "push: BadDeviceToken en sandbox Y production para device=%s — "
                    "probable desalineación entre aps-environment del build iOS y las "
                    "credenciales APNs del vault. Corregir con "
                    "scripts/flip_push_environment.py (ver HANDOFF.md).",
                    push_token[:8] + "...",
                )
            response = retry
        return response


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
    cred_fcm: dict[str, Any],
    push_token: str,
    titulo: str,
    cuerpo: str,
    *,
    data: Mapping[str, str] | None = None,
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
        message: dict[str, Any] = {
            "token": push_token,
            "notification": {"title": titulo, "body": cuerpo},
        }
        normalized_data = _normalizar_data_push(data)
        if normalized_data:
            message["data"] = normalized_data
        return await client.post(
            f"{_FCM_SEND_BASE_URL}/{project_id}/messages:send",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"message": message},
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


async def _registrar_entrega(
    session: Any,
    *,
    tenant_id: UUID,
    user_id: UUID,
    platform: str,
    outcome: str,
    device_id: Any = None,
    status_code: int | None = None,
    reason: Any = None,
    provider_message_id: Any = None,
) -> None:
    """Persiste el resultado del intento. Nunca lanza: este módulo entero es
    best-effort y una observabilidad que pueda tumbar el envío sería peor que
    no tenerla."""
    try:
        await record_push_delivery(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            platform=platform,
            outcome=outcome,
            device_id=device_id,
            status_code=status_code,
            reason=reason,
            provider_message_id=provider_message_id,
        )
    except Exception:
        logger.warning(
            "push: no se pudo registrar la entrega en audit_log (device_id=%s)",
            device_id,
            exc_info=True,
        )


def _motivo_del_proveedor(response: httpx.Response) -> str | None:
    """El `reason` de APNs (`BadDeviceToken`, `ExpiredProviderToken`,
    `TooManyProviderTokenUpdates`...) o el `error.status` de FCM. Sin él, un
    429 y un 503 son indistinguibles y no hay forma de decidir si reintentar.
    """
    try:
        payload = response.json()
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    razon = payload.get("reason")
    if isinstance(razon, str) and razon:
        return razon
    error = payload.get("error")
    if isinstance(error, dict):
        estado = error.get("status")
        if isinstance(estado, str) and estado:
            return estado
    return None


async def _despachar_a_dispositivo(
    session: Any,
    config: dict[str, Any],
    dispositivo: dict[str, Any],
    titulo: str,
    cuerpo: str,
    data: Mapping[str, str] | None = None,
    *,
    tenant_id: UUID,
    user_id: UUID,
    category: str | None = None,
) -> bool:
    """`True` si el proveedor confirmó la entrega (HTTP 200). Nunca lanza:
    dependencia faltante, red caída, o cualquier otro error del proveedor
    cuenta como fallo de ESTE dispositivo y no debe frenar el resto del lote
    (ver `enviar_push_a_usuario`).

    Cada salida deja además una fila en `audit_log`
    (`edecan_core.notifications.record_push_delivery`). POR QUÉ: los
    `logger.warning` de abajo se escriben en un stdout que, en la app de
    escritorio empaquetada, va a `/dev/null` — o sea que hasta ahora un 403,
    un 429 o una excepción de red desaparecían por completo y "no me llegó el
    push" no se podía distinguir de "Apple respondió 200 y el teléfono no lo
    mostró". El log sigue estando; lo que decide el caso es la fila."""
    plataforma = dispositivo.get("push_platform")
    push_token = dispositivo.get("push_token")
    if not push_token or plataforma not in ("apns", "fcm"):
        await _registrar_entrega(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            platform=str(plataforma or "desconocida"),
            outcome="dispositivo_sin_destino",
            device_id=dispositivo.get("id"),
        )
        return False

    cred = config.get(plataforma)
    if not cred:
        logger.warning(
            "push: dispositivo %s pide '%s' pero el tenant no conectó esa credencial",
            dispositivo.get("id"),
            plataforma,
        )
        await _registrar_entrega(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            platform=plataforma,
            outcome="sin_credencial_de_esa_plataforma",
            device_id=dispositivo.get("id"),
        )
        return False

    try:
        if plataforma == "apns":
            response = (
                await enviar_apns(cred, push_token, titulo, cuerpo, data=data, category=category)
                if data
                else await enviar_apns(cred, push_token, titulo, cuerpo, category=category)
            )
            statuses_invalidos = _APNS_TOKEN_INVALIDO_STATUSES
        else:
            response = (
                await enviar_fcm(cred, push_token, titulo, cuerpo, data=data)
                if data
                else await enviar_fcm(cred, push_token, titulo, cuerpo)
            )
            statuses_invalidos = _FCM_TOKEN_INVALIDO_STATUSES
    except Exception as exc:
        logger.warning(
            "push: fallo enviando a device_id=%s (%s)",
            dispositivo.get("id"),
            plataforma,
            exc_info=True,
        )
        # El nombre de la clase, no el mensaje: `ConnectTimeout` o
        # `PushNoDisponibleError` alcanzan para clasificar, y un mensaje libre
        # podría arrastrar una URL o una credencial hasta `audit_log`.
        await _registrar_entrega(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            platform=plataforma,
            outcome="excepcion",
            device_id=dispositivo.get("id"),
            reason=type(exc).__name__,
        )
        return False

    # `apns-id` es el identificador que Apple pide citar al reportar una
    # entrega dudosa; sin guardarlo no hay nada concreto que reclamar.
    identificador = response.headers.get("apns-id") or response.headers.get("x-request-id")
    if response.status_code == 200:
        await _registrar_entrega(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            platform=plataforma,
            outcome="aceptado",
            device_id=dispositivo.get("id"),
            status_code=200,
            provider_message_id=identificador,
        )
        return True

    motivo = _motivo_del_proveedor(response)
    logger.warning(
        "push: %s respondió %s (%s) para device_id=%s",
        plataforma,
        response.status_code,
        motivo or "sin motivo",
        dispositivo.get("id"),
    )
    await _registrar_entrega(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        platform=plataforma,
        outcome="rechazado",
        device_id=dispositivo.get("id"),
        status_code=response.status_code,
        reason=motivo,
        provider_message_id=identificador,
    )
    if response.status_code in statuses_invalidos:
        await _limpiar_push_token(session, dispositivo["id"])
    return False


async def enviar_push_a_usuario(
    deps: Deps,
    *,
    tenant_id: UUID,
    user_id: UUID,
    titulo: str,
    cuerpo: str,
    data: Mapping[str, str] | None = None,
    category: str | None = None,
) -> ResultadoEnvioPush:
    """Envía un push a TODOS los dispositivos `active` de `user_id` que
    tengan `push_token` registrado, despachando por `push_platform`.

    `category` (opcional) es la categoría de notificación de APNs (`aps.category`)
    que se inyecta en el payload para clasificar la entrega (p. ej.
    `"GYM_CHECKIN"`). Para FCM no se inyecta (no tiene ese campo en su
    contrato de `notification`).

    SIEMPRE best-effort: nunca lanza (ver docstring del módulo) — cualquier
    ausencia de credencial/dispositivo es `ResultadoEnvioPush(0, 0)` con una
    advertencia logueada, nunca una excepción que interrumpa a quien llama
    (`send_reminder` o `universal_notifications`). Un fallo
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
                # Los dos `return` mudos de más abajo eran el otro agujero: un
                # tenant sin credencial o un usuario sin dispositivo daban
                # exactamente el mismo silencio que un fallo de Apple.
                await _registrar_entrega(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    platform="ninguna",
                    outcome="sin_credenciales",
                )
                return ResultadoEnvioPush(0, 0)

            dispositivos = await _listar_dispositivos_con_push(session, tenant_id, user_id)
            if not dispositivos:
                logger.warning(
                    "push: user_id=%s sin dispositivos activos con push_token; 0 enviados",
                    user_id,
                )
                await _registrar_entrega(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    platform="ninguna",
                    outcome="sin_dispositivos",
                )
                return ResultadoEnvioPush(0, 0)

            enviados = 0
            fallidos = 0
            for dispositivo in dispositivos:
                exito = await _despachar_a_dispositivo(
                    session,
                    config,
                    dispositivo,
                    titulo,
                    cuerpo,
                    data,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    category=category,
                )
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


def _normalizar_data_push(data: Mapping[str, str] | None) -> dict[str, str]:
    """Payload de navegación pequeño, textual y con claves controladas."""
    if not data:
        return {}
    unknown = set(data) - _PUSH_DATA_KEYS
    if unknown:
        raise ValueError(f"Claves de navegación push no permitidas: {', '.join(sorted(unknown))}")
    normalized: dict[str, str] = {}
    for key, raw_value in data.items():
        if not isinstance(raw_value, str):
            raise ValueError(f"El valor push {key!r} debe ser texto.")
        value = raw_value.strip()
        if not value or len(value) > _MAX_PUSH_DATA_VALUE_CHARS:
            raise ValueError(f"El valor push {key!r} está vacío o es demasiado largo.")
        normalized[key] = value
    return normalized
