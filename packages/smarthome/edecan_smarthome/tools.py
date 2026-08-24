"""Las 3 herramientas de casa inteligente (`ARCHITECTURE.md` §12, §10.7;
`DIRECCION_ACTUAL.md`; WP-V3-12): `casa_dispositivos`, `casa_estado`,
`casa_controlar`.

Sin `requires_flags`: a diferencia de otros paquetes v2 (`edecan_browser`
gatea con `tools.browser`, `edecan_commerce` con `commerce.orders`), este
work package NO introduce un flag de plan nuevo (instrucción explícita del
paquete de trabajo, "SIN flag de plan nuevo — no toques edecan_schemas") —
las 3 tools siempre están disponibles para el modelo; lo que sí las gatea en
la práctica es si el tenant conectó su Home Assistant (`_cliente_desde_vault`
devuelve un `ToolResult` explicando cómo conectarlo si no) y, para
`casa_controlar`, el gate `dangerous=True` de `edecan_core.agent.Agent
.run_turn` (exige confirmación humana explícita antes de ejecutar cualquier
acción física en el hogar).

`_cliente_desde_vault` calca el patrón de
`edecan_toolkit._conectores.buscar_cuenta_conectada` /
`edecan_messaging._creds.resolver_credenciales`: consulta
`connector_accounts` por `connector_key` vía `ctx.session` (SQL parametrizado
directo, sin importar `edecan_db`) y le pide el `TokenBundle` a `ctx.vault`
— por duck typing, este paquete no depende de `edecan_db` (ver
`pyproject.toml`: solo `edecan-core` + `httpx`).
"""

from __future__ import annotations

from typing import Any

from edecan_core import Tool, ToolContext, ToolResult
from sqlalchemy import text

from .client import HomeAssistantClient, HomeAssistantError

# Clave EXACTA del conector (ARCHITECTURE.md §12.b, pinned): singleton por
# tenant, igual que "llm"/"voice_stt"/"voice_tts"/"whatsapp" — un tenant
# tiene UNA configuración de Home Assistant activa a la vez.
CONNECTOR_KEY = "homeassistant"

_RUTA_CONFIGURACION = "Configuración → Casa inteligente"

_DEFAULT_TIMEOUT_SECONDS = 15

# GUARDRAIL DE PRODUCTO (instrucción explícita del work package): el agente
# JAMÁS ejecuta una acción sobre el dominio "lock" — ni siquiera "encender"/
# "apagar"/"alternar" mapeados (que en Home Assistant, vía los servicios
# genéricos `homeassistant.turn_on`/`turn_off`/`toggle`, se traducen para una
# entidad `lock.*` en `lock.lock`/`lock.unlock`/toggle de la cerradura) ni un
# 'domain.service' explícito como 'lock.unlock'/'lock.open'. Se bloquea el
# DOMINIO COMPLETO (no solo "unlock"/"open") a propósito: es la postura más
# simple y más segura — nunca hay que reconstruir en código la tabla interna
# de Home Assistant de qué servicio genérico equivale a qué acción real sobre
# una cerradura para decidir caso por caso cuál es "segura". Ver
# `docs/casa-inteligente.md` para la política completa.
DOMINIOS_BLOQUEADOS: frozenset[str] = frozenset({"lock"})

_ACCIONES_MAPEADAS: dict[str, tuple[str, str]] = {
    "encender": ("homeassistant", "turn_on"),
    "apagar": ("homeassistant", "turn_off"),
    "alternar": ("homeassistant", "toggle"),
}


def _mensaje_no_configurado() -> ToolResult:
    return ToolResult(
        content=(
            "Todavía no conectaste tu Home Assistant. Configúralo en "
            f"{_RUTA_CONFIGURACION} (pega la URL de tu Home Assistant y un Long-Lived Access "
            "Token generado en tu perfil de Home Assistant) y vuelve a pedírmelo."
        )
    )


async def _cliente_desde_vault(ctx: ToolContext) -> HomeAssistantClient | ToolResult:
    """Arma un `HomeAssistantClient` con las credenciales del tenant, o
    devuelve un `ToolResult` explicando cómo conectarlo — NUNCA lanza. Uso en
    cada tool: `cliente = await _cliente_desde_vault(ctx); if
    isinstance(cliente, ToolResult): return cliente`.
    """
    resultado = await ctx.session.execute(
        text(
            """
            SELECT id FROM connector_accounts
            WHERE tenant_id = :tenant_id ::uuid AND connector_key = :connector_key
              AND status NOT IN ('revoked', 'disconnected', 'error')
            ORDER BY created_at DESC
            LIMIT 1
            """
        ),
        {"tenant_id": str(ctx.tenant_id), "connector_key": CONNECTOR_KEY},
    )
    fila = resultado.mappings().first()
    if fila is None:
        return _mensaje_no_configurado()

    bundle = await ctx.vault.get(ctx.tenant_id, fila["id"])
    if bundle is None or not getattr(bundle, "access_token", None) or not getattr(
        bundle, "scopes", None
    ):
        return _mensaje_no_configurado()

    timeout = getattr(ctx.settings, "HOMEASSISTANT_TIMEOUT_SECONDS", _DEFAULT_TIMEOUT_SECONDS)
    try:
        return HomeAssistantClient(
            base_url=bundle.scopes[0], token=bundle.access_token, timeout=float(timeout)
        )
    except HomeAssistantError as exc:
        return ToolResult(
            content=f"La configuración guardada de Home Assistant no es válida: {exc}"
        )


class CasaDispositivosTool(Tool):
    """Lista los dispositivos/entidades de la casa inteligente conectada."""

    name = "casa_dispositivos"
    description = (
        "Lista los dispositivos de tu casa inteligente conectada por Home Assistant: luces, "
        "enchufes, clima, sensores, cerraduras, etc., con su nombre y estado actual. Solo "
        "lectura, nunca cambia nada."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "dominio": {
                "type": "string",
                "description": (
                    "Filtra por tipo de dispositivo, p. ej. 'light', 'switch', 'climate', "
                    "'sensor', 'lock', 'cover'. Opcional: sin filtro devuelve todos (hasta 200)."
                ),
            },
        },
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        cliente = await _cliente_desde_vault(ctx)
        if isinstance(cliente, ToolResult):
            return cliente

        dominio = str(args.get("dominio") or "").strip().lower() or None
        try:
            entidades = await cliente.estados(dominio)
        except HomeAssistantError as exc:
            return ToolResult(content=str(exc))

        if not entidades:
            sufijo = f" del tipo '{dominio}'" if dominio else ""
            return ToolResult(
                content=f"No encontré dispositivos{sufijo} en tu Home Assistant.",
                data={"dispositivos": []},
            )

        lineas = [f"- {e['friendly_name']} ({e['entity_id']}): {e['state']}" for e in entidades]
        return ToolResult(content="\n".join(lineas), data={"dispositivos": entidades})


class CasaEstadoTool(Tool):
    """Estado + atributos de UNA entidad de la casa inteligente."""

    name = "casa_estado"
    description = (
        "Consulta el estado y los atributos relevantes (brillo, temperatura, batería, etc.) "
        "de UN dispositivo de tu casa inteligente, por su entity_id. Solo lectura."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": (
                    "entity_id exacto de Home Assistant, p. ej. 'light.sala' o "
                    "'climate.termostato' (usa 'casa_dispositivos' para verlos)."
                ),
            },
        },
        "required": ["entity_id"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        entity_id = str(args.get("entity_id", "")).strip()
        if not entity_id:
            return ToolResult(
                content=(
                    "Necesito el entity_id del dispositivo (usa 'casa_dispositivos' para verlos)."
                )
            )

        cliente = await _cliente_desde_vault(ctx)
        if isinstance(cliente, ToolResult):
            return cliente

        try:
            estado = await cliente.estado(entity_id)
        except HomeAssistantError as exc:
            return ToolResult(content=str(exc))

        atributos = estado.get("attributes") or {}
        nombre = atributos.get("friendly_name", entity_id)
        lineas = [f"{nombre} ({entity_id}): {estado.get('state')}"]
        lineas += [
            f"- {clave}: {valor}" for clave, valor in atributos.items() if clave != "friendly_name"
        ]

        return ToolResult(
            content="\n".join(lineas), data={"entity_id": entity_id, "estado": estado}
        )


def _dominio_de_entidad(entity_id: str) -> str:
    dominio, separador, _ = entity_id.partition(".")
    return dominio.strip().lower() if separador else ""


def _resolver_accion(accion: str) -> tuple[str, str] | None:
    """`accion` → `(domain, service)`, o `None` si no se entiende.

    Acepta las 3 acciones mapeadas ("encender"/"apagar"/"alternar") o un
    'domain.service' explícito (p. ej. 'cover.open_cover', 'climate
    .set_temperature') para lo que no cubran las 3 comunes.
    """
    normalizada = accion.strip().lower()
    if normalizada in _ACCIONES_MAPEADAS:
        return _ACCIONES_MAPEADAS[normalizada]
    if "." in normalizada:
        domain, _, service = normalizada.partition(".")
        domain, service = domain.strip(), service.strip()
        if domain and service:
            return domain, service
    return None


def _resultado_cerradura_bloqueada() -> ToolResult:
    return ToolResult(
        content=(
            "Desbloquear o controlar cerraduras remotamente está deshabilitado por seguridad "
            "en Edecán — no ejecuto ninguna acción sobre el dominio 'lock', ni siquiera si me "
            "lo pides explícitamente. Hazlo tú mismo, en persona o desde la app de Home "
            "Assistant."
        )
    )


class CasaControlarTool(Tool):
    """Ejecuta una acción real sobre un dispositivo de la casa inteligente."""

    name = "casa_controlar"
    description = (
        "Ejecuta una acción real sobre un dispositivo de tu casa inteligente (encender/"
        "apagar/alternar una luz o enchufe, mover una persiana, cambiar la temperatura del "
        "clima, etc.). Es una acción física en tu hogar: requiere tu confirmación explícita. "
        "NUNCA controla cerraduras (dominio 'lock') — eso está deshabilitado por seguridad."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "entity_id exacto del dispositivo a controlar, p. ej. 'light.sala'.",
            },
            "accion": {
                "type": "string",
                "description": (
                    "'encender', 'apagar', 'alternar', o un 'dominio.servicio' explícito de "
                    "Home Assistant para acciones más específicas (p. ej. 'cover.open_cover', "
                    "'climate.set_temperature')."
                ),
            },
            "parametros": {
                "type": "object",
                "description": (
                    "Parámetros adicionales del servicio de Home Assistant, opcional "
                    "(p. ej. {'brightness': 200} o {'temperature': 22})."
                ),
            },
        },
        "required": ["entity_id", "accion"],
    }
    dangerous = True

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        entity_id = str(args.get("entity_id", "")).strip()
        accion_in = str(args.get("accion", "")).strip()
        if not entity_id:
            return ToolResult(content="Necesito el entity_id del dispositivo a controlar.")
        if not accion_in:
            return ToolResult(
                content=(
                    "Necesito la acción a ejecutar: 'encender', 'apagar', 'alternar', o un "
                    "'dominio.servicio' explícito."
                )
            )

        resuelto = _resolver_accion(accion_in)
        if resuelto is None:
            return ToolResult(
                content=(
                    f"No entendí la acción «{accion_in}». Usa 'encender', 'apagar', 'alternar', "
                    "o un 'dominio.servicio' explícito (p. ej. 'cover.open_cover')."
                )
            )
        domain, service = resuelto

        # Guardrail de cerraduras: se evalúa ANTES de tocar el vault/la red —
        # tanto si el dominio explícito pedido es "lock" (p. ej. accion=
        # "lock.unlock") como si la entidad objetivo vive en el dominio
        # "lock" (p. ej. entity_id="lock.puerta_principal" con accion=
        # "apagar", que Home Assistant traduciría a un unlock real). Ver
        # docstring de `DOMINIOS_BLOQUEADOS`.
        if domain in DOMINIOS_BLOQUEADOS or _dominio_de_entidad(entity_id) in DOMINIOS_BLOQUEADOS:
            return _resultado_cerradura_bloqueada()

        cliente = await _cliente_desde_vault(ctx)
        if isinstance(cliente, ToolResult):
            return cliente

        parametros = args.get("parametros")
        service_data: dict[str, Any] = dict(parametros) if isinstance(parametros, dict) else {}
        service_data["entity_id"] = entity_id

        try:
            await cliente.llamar_servicio(domain, service, service_data)
        except HomeAssistantError as exc:
            return ToolResult(content=str(exc))

        return ToolResult(
            content=f"Listo: ejecuté {domain}.{service} sobre {entity_id}.",
            data={
                "entity_id": entity_id,
                "domain": domain,
                "service": service,
                "parametros": service_data,
            },
        )


def get_all_tools() -> list[Tool]:
    """Entry point `edecan.tools` (ver `pyproject.toml` y `ToolRegistry.load_entry_points`)."""
    return [CasaDispositivosTool(), CasaEstadoTool(), CasaControlarTool()]
