"""`/v1/mensajes` — bandeja de mensajería unificada en la web (Telegram/Discord/Slack/
WhatsApp), `ARCHITECTURE.md` §13, WP-V4-11. Ver `docs/mensajeria.md` sección "Bandeja
unificada (web)" para el contrato completo (asimetrías por canal, formato de `fecha`, etc.).

`edecan_api.main` YA monta este router de forma defensiva (`V4_ROUTER_NAMES` incluye
`"mensajes"`, WP-V4-01 en paralelo) — este módulo solo necesita existir y exportar `router`.

## Por qué este router NO reimplementa nada de `packages/messaging/`

`packages/messaging/edecan_messaging` (WP-V2-05 + WP-V3-13) YA implementa las tools del
agente (`enviar_mensaje`/`leer_mensajes`) contra las cuatro APIs oficiales — este router es
SOLO la superficie HTTP para que la web (`apps/web/.../app/mensajes`) pueda leer/enviar sin
pasar por el chat. REGLA DURA de este paquete de trabajo: `packages/messaging/` no se toca.
Por eso:

- `edecan_messaging._creds.resolver_credenciales(ctx, canal)` resuelve la credencial del
  tenant desde el `TokenVault` — se REUTILIZA tal cual (nunca se reimplementa la consulta a
  `connector_accounts`). Como esa función espera un `edecan_core.ToolContext` (duck-typed:
  solo toca `.tenant_id`/`.session`/`.vault`), `_build_ctx()` arma uno mínimo por request
  (`settings`/`llm` en `None`, `extras={}` — ninguno de los dos lo usa `resolver_credenciales`).
- `edecan_messaging.clients`/`edecan_messaging.whatsapp` (los 4 clientes HTTP oficiales) se
  llaman directo con la credencial ya resuelta — `_leer_crudo`/`_enviar_crudo` son el único
  código "propio" de este router, y son simple plomería de despacho por canal (un `if/elif` de
  una línea cada uno), no lógica de resolución de credenciales.

## Endpoints

- `GET /canales` — estado de las 4 plataformas para el tenant actual: `{canal, conectado,
  puede_leer}`. `puede_leer=False` SOLO para WhatsApp: la Cloud API de Meta entrega mensajes
  entrantes por webhook, que este router no monta (misma limitación que ya documenta
  `edecan_messaging.tools`) — se expone en la API en vez de dejar que cada consumidor
  (la web, cualquier cliente futuro) tenga que memorizar esa asimetría por su cuenta.
- `GET ""` — últimos mensajes de UN canal ya conectado (`canal` + `origen` según la
  plataforma + `limite`). `400` si el canal no existe, si es `whatsapp` (sin soporte de
  lectura), si falta `origen` donde es obligatorio, o si el canal no está conectado.
- `POST /enviar` — envía un mensaje real vía el cliente oficial del canal y deja un rastro en
  `audit_log` (`repo.add_audit_log`): el click del propio humano en el compositor de la web ES
  la confirmación explícita — no hay un segundo paso de "aprobar" porque el envío YA es la
  acción intencional de la persona frente a la pantalla, a diferencia de `enviar_mensaje` vía
  el AGENTE (`dangerous=True`, exige `confirmation_required` de `ARCHITECTURE.md` §10.7
  porque ahí es un LLM quien decide enviar, no un humano haciendo click).

Gateado por el flag de plan `connectors.messaging` (`edecan_schemas.plans.
FLAG_CONNECTORS_MESSAGING`, ya pinned y en `True` en los 4 planes hoy) — mismo flag que ya
usan `EnviarMensajeTool`/`LeerMensajesTool` (`requires_flags`), aplicado aquí con el mismo
criterio de dependencia-gate que `edecan_api.routers.erp._require_erp_inventory`.

## Estilo

SQL parametrizado NUNCA aparece en este archivo: la única lectura contra `connector_accounts`
vive en `resolver_credenciales` (paquete hermano, reutilizado); este router no toca
`edecan_api/repo.py` salvo `repo.add_audit_log` (inyectado vía `edecan_api.deps.get_repo`,
igual que `devices.py`/`erp.py`).
"""

from __future__ import annotations

import logging
from typing import Any

from edecan_core import ToolContext
from edecan_db.vault import TokenVault
from edecan_messaging._creds import (
    CONNECTOR_KEYS,
    CredencialMensajeria,
    MessagingNotConnectedError,
    display_name,
    resolver_credenciales,
)
from edecan_messaging.clients import (
    DiscordClient,
    MessagingClientError,
    SlackClient,
    TelegramClient,
    clamp_limit,
)
from edecan_messaging.whatsapp import WhatsAppClient
from edecan_schemas.plans import FLAG_CONNECTORS_MESSAGING
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/mensajes", tags=["mensajes"], dependencies=[Depends(rate_limit)])

_WHATSAPP_NO_LECTURA = (
    "WhatsApp no soporta lectura de mensajes en Edecán: la Cloud API de Meta entrega mensajes "
    "entrantes solo por webhook (requiere una URL pública verificada), que este endpoint no "
    "monta todavía. Puedes seguir enviando por WhatsApp normalmente."
)
_PREVIEW_AUDIT_MAX = 160  # mismo tamaño que `edecan_messaging.tools._VISTA_PREVIA_MAX`.


# ---------------------------------------------------------------------------
# Gate de flag de plan (mismo criterio que `edecan_api.routers.erp._require_erp_inventory`)
# ---------------------------------------------------------------------------


async def _require_messaging(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    if not current_user.tenant.flags.get(FLAG_CONNECTORS_MESSAGING, False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La mensajería (Telegram/Discord/Slack/WhatsApp) no está disponible en tu plan.",
        )
    return current_user


# ---------------------------------------------------------------------------
# Esquemas
# ---------------------------------------------------------------------------


class CanalEstadoOut(BaseModel):
    canal: str
    conectado: bool
    puede_leer: bool


class MensajeOut(BaseModel):
    canal: str
    remitente: str
    texto: str
    fecha: str
    chat_id: str


class EnviarMensajeIn(BaseModel):
    canal: str = Field(min_length=1)
    destinatario: str = Field(min_length=1)
    texto: str = Field(min_length=1)
    # Solo WhatsApp — opcionales, mismo par de argumentos que `EnviarMensajeTool` (ver
    # `edecan_messaging.tools`). El compositor simple de la web (destinatario + texto +
    # Enviar, sin campo de plantilla) nunca los manda, pero quedan disponibles para
    # cualquier otro consumidor de esta API — sin esto, un envío de WhatsApp fuera de la
    # ventana de 24h quedaría permanentemente irrecuperable desde este endpoint (ver
    # `docs/mensajeria.md`).
    plantilla: str | None = Field(
        default=None,
        description=(
            "Solo WhatsApp: nombre de una plantilla ya aprobada por Meta. Obligatoria fuera "
            "de la ventana de 24h desde el último mensaje del destinatario."
        ),
    )
    idioma: str = Field(
        default="es", description="Solo WhatsApp: código de idioma de la plantilla."
    )

    @field_validator("canal")
    @classmethod
    def _normalizar_canal(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("destinatario", "texto")
    @classmethod
    def _recortar_y_exigir(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("no puede estar vacío.")
        return value


class EnviarMensajeOut(BaseModel):
    canal: str
    destinatario: str
    resultado: dict[str, Any]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_ctx(current_user: CurrentUser, session: AsyncSession, vault: TokenVault) -> ToolContext:
    """`ToolContext` mínimo para reutilizar `resolver_credenciales` sin duplicarla (ver
    docstring del módulo) — `settings`/`llm` en `None` porque esa función no los toca."""
    return ToolContext(
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        session=session,
        settings=None,
        llm=None,
        vault=vault,
        extras={},
    )


def _canal_no_soportado(canal: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            f"«{canal}» no es un canal de mensajería soportado. Usa uno de: "
            f"{', '.join(CONNECTOR_KEYS)}."
        ),
    )


def _acortar(texto: str, *, maximo: int) -> str:
    texto = texto.strip()
    if len(texto) <= maximo:
        return texto
    return texto[: maximo - 1].rstrip() + "…"


def _whatsapp_phone_number_id(credencial: CredencialMensajeria) -> str:
    """`credencial.scopes[0]` (ver `edecan_messaging._creds`, `CredencialMensajeria.scopes`
    guarda `[phone_number_id]` para WhatsApp) — defensivo: en la práctica siempre está
    poblado, porque `connect_whatsapp` (`edecan_api.routers.connectors`) siempre lo guarda."""
    if not credencial.scopes:
        raise MessagingClientError(
            "La cuenta de WhatsApp conectada no tiene un phone_number_id guardado; "
            "reconéctala en /app/conectores."
        )
    return credencial.scopes[0]


async def _leer_crudo(
    canal: str, credencial: CredencialMensajeria, origen: str, limite: int
) -> list[dict[str, Any]]:
    """Despacho por canal usando los clientes públicos de `edecan_messaging` — WhatsApp queda
    fuera a propósito (el handler nunca llega aquí para `"whatsapp"`, ver
    `_WHATSAPP_NO_LECTURA`)."""
    if canal == "telegram":
        return await TelegramClient(credencial.access_token).get_updates(limite)
    if canal == "discord":
        return await DiscordClient(credencial.access_token).list(origen, limite)
    return await SlackClient(credencial.access_token).conversations_history(origen, limite)


async def _enviar_crudo(
    canal: str,
    credencial: CredencialMensajeria,
    destinatario: str,
    texto: str,
    *,
    plantilla: str | None,
    idioma: str,
) -> dict[str, Any]:
    if canal == "telegram":
        return await TelegramClient(credencial.access_token).send_message(destinatario, texto)
    if canal == "discord":
        return await DiscordClient(credencial.access_token).send(destinatario, texto)
    if canal == "whatsapp":
        cliente = WhatsAppClient(credencial.access_token, _whatsapp_phone_number_id(credencial))
        if plantilla:
            return await cliente.enviar_plantilla(destinatario, plantilla, idioma)
        return await cliente.enviar_texto(destinatario, texto)
    return await SlackClient(credencial.access_token).chat_post_message(destinatario, texto)


def _normalizar(canal: str, origen: str, mensaje: dict[str, Any]) -> MensajeOut:
    """Formato de `fecha` DELIBERADAMENTE crudo (string tal cual, sin reinterpretar): las tres
    plataformas usan formatos incompatibles entre sí (Telegram: epoch en segundos; Discord:
    ISO 8601; Slack: "segundos.microsegundos") — reinterpretarlas aquí arriesgaría introducir
    errores de zona horaria/parseo; la honestidad sobre esa asimetría es más segura que una
    conversión aproximada (ver `docs/mensajeria.md`, que documenta el formato exacto de cada
    canal para que quien consuma esta API decida cómo mostrarlo)."""
    if canal == "telegram":
        interior = mensaje.get("message") or {}
        remitente = str((interior.get("from") or {}).get("first_name") or "desconocido")
        return MensajeOut(
            canal=canal,
            remitente=remitente,
            texto=str(interior.get("text", "")),
            fecha=str(interior.get("date", "")),
            chat_id=str((interior.get("chat") or {}).get("id", "")),
        )
    if canal == "discord":
        autor = str((mensaje.get("author") or {}).get("username") or "desconocido")
        return MensajeOut(
            canal=canal,
            remitente=autor,
            texto=str(mensaje.get("content", "")),
            fecha=str(mensaje.get("timestamp", "")),
            chat_id=str(mensaje.get("channel_id") or origen),
        )
    # slack
    remitente = str(mensaje.get("user") or mensaje.get("username") or "desconocido")
    return MensajeOut(
        canal=canal,
        remitente=remitente,
        texto=str(mensaje.get("text", "")),
        fecha=str(mensaje.get("ts", "")),
        chat_id=origen,
    )


# ---------------------------------------------------------------------------
# GET /canales
# ---------------------------------------------------------------------------


@router.get("/canales", response_model=list[CanalEstadoOut])
async def list_canales(
    current_user: CurrentUser = Depends(_require_messaging),
    session: AsyncSession = Depends(get_tenant_session),
    vault: TokenVault = Depends(get_vault),
) -> list[CanalEstadoOut]:
    ctx = _build_ctx(current_user, session, vault)
    estados: list[CanalEstadoOut] = []
    for canal in CONNECTOR_KEYS:
        try:
            await resolver_credenciales(ctx, canal)
            conectado = True
        except MessagingNotConnectedError:
            conectado = False
        estados.append(
            CanalEstadoOut(canal=canal, conectado=conectado, puede_leer=canal != "whatsapp")
        )
    return estados


# ---------------------------------------------------------------------------
# GET "" — leer mensajes de un canal
# ---------------------------------------------------------------------------


@router.get("", response_model=list[MensajeOut])
async def list_mensajes(
    canal: str = Query(..., description="telegram | discord | slack | whatsapp"),
    origen: str | None = Query(
        default=None, description="chat_id/canal — opcional solo en Telegram."
    ),
    limite: int | None = Query(
        default=None, description="Máximo de mensajes (1-20, por defecto 20)."
    ),
    current_user: CurrentUser = Depends(_require_messaging),
    session: AsyncSession = Depends(get_tenant_session),
    vault: TokenVault = Depends(get_vault),
) -> list[MensajeOut]:
    canal = canal.strip().lower()
    if canal not in CONNECTOR_KEYS:
        raise _canal_no_soportado(canal)
    if canal == "whatsapp":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_WHATSAPP_NO_LECTURA)

    origen = (origen or "").strip()
    if canal != "telegram" and not origen:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Falta 'origen' (chat/canal) para leer mensajes de {display_name(canal)}.",
        )

    ctx = _build_ctx(current_user, session, vault)
    try:
        credencial = await resolver_credenciales(ctx, canal)
    except MessagingNotConnectedError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        crudos = await _leer_crudo(canal, credencial, origen, clamp_limit(limite))
    except MessagingClientError as exc:
        logger.warning("Fallo al leer mensajes de %s: %s", canal, exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return [_normalizar(canal, origen, m) for m in crudos]


# ---------------------------------------------------------------------------
# POST /enviar
# ---------------------------------------------------------------------------


@router.post("/enviar", response_model=EnviarMensajeOut)
async def enviar_mensaje(
    body: EnviarMensajeIn,
    current_user: CurrentUser = Depends(_require_messaging),
    session: AsyncSession = Depends(get_tenant_session),
    vault: TokenVault = Depends(get_vault),
    repo: Repo = Depends(get_repo),
) -> EnviarMensajeOut:
    if body.canal not in CONNECTOR_KEYS:
        raise _canal_no_soportado(body.canal)

    ctx = _build_ctx(current_user, session, vault)
    try:
        credencial = await resolver_credenciales(ctx, body.canal)
    except MessagingNotConnectedError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    plantilla = (body.plantilla or "").strip() or None
    idioma = (body.idioma or "es").strip() or "es"
    try:
        resultado = await _enviar_crudo(
            body.canal,
            credencial,
            body.destinatario,
            body.texto,
            plantilla=plantilla,
            idioma=idioma,
        )
    except MessagingClientError as exc:
        logger.warning(
            "Fallo al enviar mensaje por %s a %s: %s", body.canal, body.destinatario, exc
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="mensajes.enviado",
        target=f"{body.canal}:{body.destinatario}",
        meta={"preview": _acortar(body.texto, maximo=_PREVIEW_AUDIT_MAX)},
    )

    return EnviarMensajeOut(canal=body.canal, destinatario=body.destinatario, resultado=resultado)
