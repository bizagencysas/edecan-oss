"""Contrato compartido del harness de Edecán Bots (chat interactivo + headless).

Bots builder: code/MCP/skills en el chat del bot, beats con `avisar_avance`, y
pre-aprobación de sandbox/read/code. Solo gatean conexores nuevos, envíos
externos y acciones destructivas de alto impacto.

Las tools MCP (`mcp_*`) NUNCA se pre-aprueban por prefijo (BOTS-06): requieren
un grant EXPLÍCITO del dueño por tool/operación, atado a una versión de
definición (`mcp_preapproved_tokens`).
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

# Conector nuevo, auth, reinicio, envíos externos, pagos, pentest activo y
# mensajes inter-agente (`enviar_mensaje_bot` delega/encola trabajo del receptor
# → es un `send`, jamás una lectura: un bot read_only no puede hacerla correr).
BOT_GATED_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "conectar_mcp",
        "desconectar_mcp",
        "reiniciar_servicio",
        "enviar_correo",
        "enviar_mensaje_personal",
        "enviar_mensaje_bot",
        "publicar_social",
        "preparar_pago",
        "configurar_credencial",
        "ejecutar_pentestgpt_autorizado",
    }
)

# Envíos EXTERNOS reales nativos (WhatsApp/Telegram, correo, publicación social,
# mensaje inter-agente): su efecto sale del tenant. Es la señal de `send` para
# tools nativas, independiente de su `category` declarada. `enviar_mensaje` NO
# estaba en `BOT_GATED_TOOL_NAMES` (auditoría C1): al ser `dangerous` clasificaba
# `write` y corría en `draft`, que promete "sin envío externo".
BOT_SEND_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "enviar_mensaje",
        "enviar_mensaje_bot",
        "publicar_social",
        "enviar_correo",
        "enviar_mensaje_personal",
    }
)

# Sandbox / code / skills / narración — fluyen sin tarjeta en turnos de bot.
BOT_SANDBOX_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "acceder_codigo_local",
        "navegar_web",
        "navegar_web_interactivo",
        "usar_computadora",
        "delegar_al_ide",
        "usar_skill",
        "buscar_skills",
        "listar_skills",
        "instalar_skill",
        "reparar_con_skill_local",
        "crear_herramienta",
        "listar_mcp",
        "avisar_avance",
    }
)


def bot_preapproved_tool_calls(
    *,
    companion_present: bool,
    local_mode: bool,
    mcp_tool_names: Iterable[str] = (),
) -> set[str]:
    """Nombres pre-aprobados para el turno de un bot builder.

    Solo tools sandbox/code/skills/narración de plataforma. Las tools MCP ya NO
    se pre-aprueban por el prefijo `mcp_` (BOTS-06): un grant explícito y
    versionado es el único camino, ver `mcp_preapproved_tokens`. `mcp_tool_names`
    se conserva por compatibilidad de firma (`bot_registry` y el wake headless
    siguen pasándolo) pero no aporta ninguna aprobación automática — fail-closed.
    """
    approved = set(BOT_SANDBOX_TOOL_NAMES)
    if not (companion_present or local_mode):
        approved.discard("usar_computadora")
        approved.discard("delegar_al_ide")
    return approved


def is_bot_gated_tool(name: str) -> bool:
    return str(name) in BOT_GATED_TOOL_NAMES


# ---------------------------------------------------------------------------
# Grants explícitos de tools MCP (BOTS-06). La pre-aprobación de una tool
# remota NUNCA se deriva de su prefijo `mcp_` ni de su nombre/descripción
# (entrada no confiable que reporta el servidor remoto). Solo un grant
# EXPLÍCITO del dueño, atado a una versión de definición, autoriza que la tool
# corra sin tarjeta de aprobación.
# ---------------------------------------------------------------------------

MCP_OPERATION_READ = "read"
MCP_OPERATION_WRITE = "write"
MCP_OPERATION_SEND = "send"

MCP_OPERATIONS: frozenset[str] = frozenset(
    {MCP_OPERATION_READ, MCP_OPERATION_WRITE, MCP_OPERATION_SEND}
)

# ---------------------------------------------------------------------------
# Clasificación LOCAL y determinista de la operación de una tool (BOTS-02/H3).
#
# El nombre/descripción/schema que reporta un servidor MCP remoto es DATO no
# confiable (puede contener instrucciones hostiles o mentir sobre lo que hace).
# Por eso la operación efectiva NUNCA se deduce de una annotation remota
# (`annotations.destructiveHint`, etc.): se calcula acá, con reglas propias
# deterministas sobre el nombre local (`mcp_...`, saneado a `[a-z0-9_]`) y el
# JSON Schema. Si no hay una señal clara, la tool queda SIN clasificar (None) y
# se trata fail-closed: ningún grant ni pre-aprobación automática la desbloquea.
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Verbos de ENVÍO EXTERNO (efecto fuera del tenant: correo, post, notificación…).
_SEND_HINTS: frozenset[str] = frozenset(
    {
        "send", "enviar", "post", "publish", "publicar", "tweet", "tuitear",
        "email", "mail", "correo", "notify", "notificar", "sms", "whatsapp",
        "share", "compartir", "broadcast", "emitir", "subscribe", "suscribir",
        "invite", "invitar", "pay", "pagar", "transfer", "transferir",
    }
)

# Verbos de ESCRITURA INTERNA (mutación local/estado: crear, borrar, instalar…).
_WRITE_HINTS: frozenset[str] = frozenset(
    {
        "write", "escribir", "delete", "borrar", "remove", "eliminar", "create",
        "crear", "update", "actualizar", "insert", "upsert", "put", "set",
        "install", "instalar", "uninstall", "desinstalar", "execute", "ejecutar",
        "run", "deploy", "desplegar", "modify", "modificar", "save", "guardar",
        "upload", "subir", "push", "commit", "destroy", "destruir", "drop",
        "truncate", "rename", "renombrar", "move", "mover", "copy", "copiar",
        "edit", "editar", "add", "agregar", "attach", "adjuntar", "store",
        "persist", "persistir", "append", "anexar",
    }
)

# Verbos de LECTURA (sin mutación). Se revisan DESPUÉS de write/send: un nombre
# mixto («read_and_delete») se clasifica conservadoramente como write.
_READ_HINTS: frozenset[str] = frozenset(
    {
        "get", "obtener", "list", "listar", "search", "buscar", "query",
        "consultar", "find", "encontrar", "read", "leer", "fetch", "lookup",
        "describe", "describir", "show", "mostrar", "count", "contar", "stat",
        "stats", "info", "inspect", "inspeccionar", "view", "ver", "preview",
        "status", "estado", "watch", "head", "tail", "top",
    }
)


def _name_tokens(name: str) -> list[str]:
    """Tokens `[a-z0-9]+` de un nombre (ya viene minúscula tras `sanear_slug`)."""
    return _TOKEN_RE.findall(str(name or "").lower())


def mcp_tool_local_operation(*, name: str, input_schema: Any = None) -> str | None:
    """Clasificación LOCAL y determinista de la operación de una tool MCP.

    Devuelve una de `read` | `write` | `send` | `None` (sin señal clara). La
    clasificación usa SOLO el nombre local (`mcp_...`) y el JSON Schema — nunca
    annotations del servidor remoto. Orden conservador: si hay señal de `send`
    gana `send`, luego `write`, luego `read`; sin señal → `None` (fail-closed).
    """
    tokens = _name_tokens(str(name))
    send = any(token in _SEND_HINTS for token in tokens)
    write = any(token in _WRITE_HINTS for token in tokens)
    read = any(token in _READ_HINTS for token in tokens)
    schema_write, schema_send = _schema_mutation_signals(input_schema)
    write = write or schema_write
    send = send or schema_send
    if send:
        return MCP_OPERATION_SEND
    if write:
        return MCP_OPERATION_WRITE
    if read:
        return MCP_OPERATION_READ
    return None


def _schema_mutation_signals(input_schema: Any) -> tuple[bool, bool]:
    """`(has_write, has_send)` a partir del JSON Schema de la tool.

    Solo cuentan nombres de PROPERTY que sean verbos de mutación EXACTOS
    (p. ej. una propiedad `delete_ids`, `send_to`, `action` con valor por
    defecto `delete`). Es una heurística local y conservadora: nunca convierte
    una tool de lectura en algo peor, y no confía en fields de `annotations`.
    """
    if not isinstance(input_schema, dict):
        return False, False
    properties = input_schema.get("properties")
    if not isinstance(properties, dict):
        return False, False
    write = False
    send = False
    for prop_name, prop_schema in properties.items():
        prop_tokens = _name_tokens(str(prop_name))
        if any(token in _SEND_HINTS for token in prop_tokens):
            send = True
        if any(token in _WRITE_HINTS for token in prop_tokens):
            write = True
        if isinstance(prop_schema, dict):
            default = prop_schema.get("default")
            if isinstance(default, str):
                for token in _name_tokens(default):
                    if token in _SEND_HINTS:
                        send = True
                    if token in _WRITE_HINTS:
                        write = True
    return write, send


def tool_local_operation(
    *,
    name: str,
    input_schema: Any = None,
    dangerous: bool = False,
    category: str | None = None,
) -> str | None:
    """Operación LOCAL de una tool (MCP o nativa), para la matriz de autonomía
    (BOTS-02) y el matching de grants MCP (H3).

    - Tool MCP (`mcp_*`): clasificación por nombre/schema (dato remoto no
      confiable → reglas propias, `None` si no hay señal).
    - Tool nativa: la `category` declarada en NUESTRO código (confiable) es la
      señal primaria — `read`/`vision` → `read`, `write` → `write`,
      `external_comm` → `send`. Cualquier otra categoría se trata fail-closed
      como `write` (no podemos probar que sea solo lectura). Antes (auditoría
      C1) la clasificación dependía solo de `dangerous` + lista manual, y tools
      de escritura no peligrosas corrían en `read_only`.
    - `BOT_SEND_TOOL_NAMES` (lista curada) fuerza `send` por encima de la
      `category`: son envíos externos reales cuyo efecto sale del tenant.
    - `category=None` (llamador legacy que aún no la pasa, p. ej.
      `edecan_automations.runner`) conserva la clasificación histórica
      (`BOT_GATED_TOOL_NAMES` → `send`, `dangerous` → `write`, resto → `read`)
      para no regresionar a los llamadores fuera de alcance.
    """
    nombre = str(name or "")
    if nombre.startswith("mcp_"):
        return mcp_tool_local_operation(name=nombre, input_schema=input_schema)
    if nombre in BOT_SEND_TOOL_NAMES:
        return MCP_OPERATION_SEND
    if category is not None:
        categoria = str(category).strip().casefold()
        if categoria in ("read", "vision"):
            return MCP_OPERATION_READ
        if categoria == "write":
            return MCP_OPERATION_WRITE
        if categoria == "external_comm":
            return MCP_OPERATION_SEND
        # Fail-closed: category desconocida → write (jamás asumir lectura).
        return MCP_OPERATION_WRITE
    # Llamador legacy sin `category`: conserva el comportamiento histórico.
    if nombre in BOT_GATED_TOOL_NAMES:
        return MCP_OPERATION_SEND
    if dangerous:
        return MCP_OPERATION_WRITE
    return MCP_OPERATION_READ


# ---------------------------------------------------------------------------
# Matriz de autonomía (BOTS-02): qué operaciones permite cada nivel ANTES de
# ejecutar un run o turno. `full` = sin restricción (funcionalidad autorizada
# completa); `ask` (default de la DB y de los bots existentes) = sin
# restricción de DISPONIBILIDAD — las tools peligrosas siguen pidiendo su
# confirmación por `dangerous`/gated, que es exactamente lo que "ask" promete;
# `draft` = lectura + escritura interna, sin envío externo; `read_only` (y
# cualquier valor desconocido) = solo lectura, fail-closed.
# ---------------------------------------------------------------------------

AUTONOMY_LEVEL_FULL = "full"
AUTONOMY_LEVEL_DRAFT = "draft"
AUTONOMY_LEVEL_READ_ONLY = "read_only"
AUTONOMY_LEVEL_ASK = "ask"

AUTONOMY_LEVELS: tuple[str, ...] = (
    AUTONOMY_LEVEL_ASK,
    AUTONOMY_LEVEL_READ_ONLY,
    AUTONOMY_LEVEL_DRAFT,
    AUTONOMY_LEVEL_FULL,
)


def autonomy_allowed_operations(level: str) -> frozenset[str] | None:
    """Operaciones permitidas por nivel, o `None` para sin restricción.

    Una tool sin operación clasificable (`None`) SOLO corre en `ask`/`full`: en
    `read_only`/`draft` se rechaza (no podemos probar que sea lectura).
    """
    nivel = str(level or "").strip()
    if nivel in (AUTONOMY_LEVEL_FULL, AUTONOMY_LEVEL_ASK):
        # ask = "pregunta antes de efectos": la disponibilidad no se restringe;
        # el freno es la confirmación de las tools peligrosas (dangerous/gated),
        # que ya existe. full = igual sin restricción (pre-aprobado sandbox).
        return None
    if nivel == AUTONOMY_LEVEL_DRAFT:
        return frozenset({MCP_OPERATION_READ, MCP_OPERATION_WRITE})
    # read_only / desconocido → solo lectura (fail-closed).
    return frozenset({MCP_OPERATION_READ})


def autonomy_allows_operation(level: str, operation: str | None) -> bool:
    """`True` si la operación está permitida en este nivel de autonomía."""
    allowed = autonomy_allowed_operations(level)
    if allowed is None:
        return True
    return operation is not None and operation in allowed


_MCP_GRANT_TOKEN_PREFIX = "mcp_grant:"


@dataclass(frozen=True)
class MCPToolGrant:
    """Autorización explícita del dueño para UNA tool MCP concreta.

    `operation` distingue lectura (`read`), escritura interna (`write`) y envío
    externo (`send`) — es una clasificación LOCAL decidida por el dueño al
    otorgar el grant, jamás deducida de lo que el servidor remoto reporta.

    `definition_version` es el fingerprint de la definición de la tool en el
    momento de otorgar el grant (`mcp_tool_definition_version`). Si el servidor
    remoto amplía su schema/capacidad (o cambia nombre/descripción), el
    fingerprint actual deja de coincidir y el grant queda INVALIDADO — no se
    hereda silenciosamente a la definición nueva.
    """

    tool_name: str
    operation: str
    definition_version: str


def mcp_tool_definition_version(
    *,
    name: str,
    description: str,
    input_schema: Any,
    server_name: str = "",
) -> str:
    """Fingerprint determinista de la definición de una tool MCP tal como la
    reportó el servidor remoto.

    Es SOLO detección de cambios (una ampliación de schema/capacidad cambia el
    hash e invalida el grant atado a la versión anterior). NO es una fuente de
    confianza sobre lo que la tool hace: nombre/descripción/schema vienen del
    servidor remoto y se tratan como dato no confiable.
    """
    payload = {
        "server": str(server_name),
        "name": str(name),
        "description": str(description),
        "schema": input_schema,
    }
    try:
        canonico = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    except TypeError:
        # `sort_keys=True` revienta si el schema trae claves no comparables
        # (p. ej. un int mezclado con str que manda un servidor malicioso).
        # Se degrada a orden de inserción: sigue siendo determinista para la
        # MISMA respuesta del servidor y nunca convierte un mal schema en un
        # crash del turno (el fingerprint es solo detección de cambios).
        canonico = json.dumps(payload, default=str, ensure_ascii=False)
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()[:16]


def mcp_grant_token(*, tool_name: str, operation: str, definition_version: str) -> str:
    """Token versionado y atado a la OPERACIÓN que el agente exige para saltar
    la tarjeta de una tool MCP (ver `agent._llamada_peligrosa_pendiente`).

    El nombre suelto nunca alcanza, y ahora tampoco alcanza un grant con la
    operación equivocada: el token codifica `{tool_name}:{operation}:{version}`,
    y solo se emite cuando `operation` coincide con la clasificación LOCAL de la
    tool (H3)."""
    return f"{_MCP_GRANT_TOKEN_PREFIX}{tool_name}:{operation}:{definition_version}"


def mcp_preapproved_tokens(
    *,
    tools: Iterable[Any],
    grants: Iterable[MCPToolGrant],
) -> set[str]:
    """Tokens versionados de las tools MCP pre-aprobadas para este turno.

    `tools` son objetos con `.name` y `.definition_version` (las tools MCP
    adaptadas por `edecan_mcp.tool_adapter`). Solo se pre-aprueba una tool que
    cumpla TODO:
      1. un grant con `operation` válida y `definition_version` coincidente con
         la versión ACTUAL de la definición (una definición cambiada produce un
         fingerprint distinto y el grant deja de aplicar);
      2. H3 — el grant `operation` coincide con la clasificación LOCAL
         determinista de la tool (`mcp_tool_local_operation`). Una tool sin
         clasificación local (`None`) NUNCA se pre-aprueba (fail-closed): solo
         la desbloquea una confirmación explícita del dueño en runtime.

    Un grant `operation="read"` ya NO desbloquea una tool clasificada como
    `write`/`send` (antes el `operation` se validaba pero se ignoraba).
    """
    actual: dict[str, str] = {}
    schema_por_nombre: dict[str, Any] = {}
    for tool in tools:
        nombre = str(getattr(tool, "name", "") or "")
        version = str(getattr(tool, "definition_version", "") or "")
        if nombre.startswith("mcp_") and version:
            actual[nombre] = version
            schema_por_nombre[nombre] = getattr(tool, "input_schema", None)

    tokens: set[str] = set()
    for grant in grants:
        nombre = str(getattr(grant, "tool_name", "") or "")
        operation = str(getattr(grant, "operation", "") or "")
        version = str(getattr(grant, "definition_version", "") or "")
        if operation not in MCP_OPERATIONS or not version:
            continue
        if nombre not in actual or actual[nombre] != version:
            continue
        local_op = mcp_tool_local_operation(
            name=nombre, input_schema=schema_por_nombre.get(nombre)
        )
        if local_op is None or local_op != operation:
            continue
        tokens.add(
            mcp_grant_token(tool_name=nombre, operation=operation, definition_version=version)
        )
    return tokens


def parse_mcp_grants(raw: Any) -> list[MCPToolGrant]:
    """Parsea grants MCP desde la config del worker (`approval_policy.mcp_grants`).

    Forma aceptada: lista de `{tool_name, operation, definition_version}`.
    Cualquier entrada inválida se IGNORA (fail-closed): un grant mal formado no
    autoriza nada. `raw` puede ser `None`, un dict, o una cadena JSON (asyncpg ya
    suele entregar JSONB como dict, pero se tolera string).
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, dict):
        return []

    entradas = raw.get("mcp_grants")
    if not isinstance(entradas, list):
        return []

    grants: list[MCPToolGrant] = []
    for entrada in entradas:
        if not isinstance(entrada, dict):
            continue
        nombre = str(entrada.get("tool_name") or "").strip()
        operation = str(entrada.get("operation") or "").strip()
        version = str(entrada.get("definition_version") or "").strip()
        if not nombre or operation not in MCP_OPERATIONS or not version:
            continue
        grants.append(
            MCPToolGrant(tool_name=nombre, operation=operation, definition_version=version)
        )
    return grants


async def build_skills_context(session: Any, tenant_id: uuid.UUID, user_id: uuid.UUID) -> str:
    """Índice compacto de skills instaladas (mismo contrato que el chat principal)."""
    try:
        from edecan_skills.store import list_skills

        filas = await list_skills(session, tenant_id, user_id, solo_enabled=True)
    except Exception:  # noqa: BLE001 - el índice nunca debe tumbar el turno
        return ""
    if not filas:
        return ""
    tope = 40
    lineas: list[str] = []
    for fila in filas[:tope]:
        nombre = str(fila.get("nombre") or "").strip()
        descripcion = str(fila.get("descripcion") or "").strip()
        if not nombre:
            continue
        linea = f"- {nombre}"
        if descripcion:
            linea += f" — {descripcion[:100]}"
        lineas.append(linea)
    if not lineas:
        return ""
    sobrantes = len(filas) - len(lineas)
    cola = (
        f"\n(y {sobrantes} más — búscalas con buscar_skills si el pedido "
        "coincide con alguna)" if sobrantes > 0 else ""
    )
    return (
        "\n\nSKILLS INSTALADAS DEL DUEÑO (si el pedido coincide con una, "
        "cárgala con usar_skill y sigue su workflow; no necesitas que el dueño "
        "la mencione):\n" + "\n".join(lineas) + cola
    )


def append_skills_to_persona(persona: Any, skills_context: str) -> None:
    context = str(skills_context or "").strip()
    if not context:
        return
    persona.instrucciones = (persona.instrucciones or "") + context


def worker_chat_extras(
    worker: Mapping[str, Any], conversation_id: uuid.UUID | str
) -> dict[str, str]:
    from edecan_core.bot_persona import worker_display_name
    from edecan_core.notifications import bot_push_avatar_fields

    return {
        "conversation_id": str(conversation_id),
        "worker_id": str(worker["id"]),
        "worker_name": worker_display_name(worker),
        **bot_push_avatar_fields(worker),
    }
