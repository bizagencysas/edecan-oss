"""Herramientas reversibles para elegir cómo acompaña Edecan desde el chat.

Viven en la API (y se ofrecen solo en conversaciones personales) porque
persisten una preferencia del usuario mediante ``Repo.upsert_persona``. No
forman parte del registry global: una misión o automatización no debe cambiar
la relación del asistente con la persona.
"""

from __future__ import annotations

import ast
import os
import re
import uuid
from typing import Any

from edecan_core.tools import Tool, ToolContext, ToolResult

_NON_ROMANTIC_STYLES = frozenset({"profesional", "coach", "amigo"})
_UPDATER_KEY = "persona_updater"
_LOCAL_OWNER_DENIED = (
    "Esta capacidad pertenece al dueño de esta instalación de Edecán. "
    "La cuenta actual no puede usarla."
)


def _configured_local_owner_user_id(settings: Any) -> uuid.UUID | None:
    raw = getattr(settings, "LOCAL_OWNER_USER_ID", None) or os.environ.get(
        "LOCAL_OWNER_USER_ID"
    )
    if not raw:
        return None
    return uuid.UUID(str(raw).strip())


async def is_local_installation_owner(ctx: ToolContext) -> bool:
    """Authorize host-level tools against the persisted installation owner."""
    settings = getattr(ctx, "settings", None)
    if not bool(getattr(settings, "EDECAN_LOCAL_MODE", False)):
        return True

    try:
        configured_owner = _configured_local_owner_user_id(settings)
    except (TypeError, ValueError):
        return False
    if configured_owner is not None:
        return configured_owner == ctx.user_id

    session = getattr(ctx, "session", None)
    if session is None:
        return False
    try:
        # Tests and embedders may pass a Repo-compatible object directly;
        # production passes AsyncSession and therefore uses SqlRepo.
        if callable(getattr(session, "get_local_owner", None)):
            repo = session
        else:
            from edecan_api.repo import SqlRepo

            repo = SqlRepo(session)
        owner = await repo.get_local_owner()
        if owner is None:
            owner = await repo.get_first_active_owner()
    except Exception:  # noqa: BLE001 - an authorization lookup must fail closed
        return False

    # A fresh local database cannot have an authenticated tenant yet. Keeping
    # this bootstrap case allows isolated Tool unit tests and first-run setup;
    # once any owner exists, the comparison below is deny-by-default.
    if owner is None:
        return True
    return owner.get("user_id") == ctx.user_id and owner.get("tenant_id") == ctx.tenant_id


async def _local_owner_denial(ctx: ToolContext) -> ToolResult | None:
    if await is_local_installation_owner(ctx):
        return None
    return ToolResult(
        content=_LOCAL_OWNER_DENIED,
        data={"authorized": False},
        is_error=True,
    )


async def _persist(ctx: ToolContext, fields: dict[str, Any]) -> ToolResult | None:
    updater = ctx.extras.get(_UPDATER_KEY)
    if not callable(updater):
        return ToolResult(
            content=(
                "No pude guardar esa preferencia en este contexto. Puedes cambiarla desde Ajustes."
            ),
            data={"updated": False},
        )
    await updater(fields=fields)
    return None


class ConfigurarEstiloRelacionTool(Tool):
    name = "configurar_estilo_relacion"
    description = (
        "Cambia cómo Edecan acompaña a la persona entre profesional, coach o amigo. "
        "Úsala cuando la persona pida explícitamente uno de esos estilos. Es reversible."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "estilo": {
                "type": "string",
                "enum": ["profesional", "coach", "amigo"],
                "description": "Estilo solicitado explícitamente por la persona.",
            }
        },
        "required": ["estilo"],
        "additionalProperties": False,
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        estilo = str(args.get("estilo", "")).strip().lower()
        if estilo not in _NON_ROMANTIC_STYLES:
            return ToolResult(
                content=(
                    "Elige profesional, coach o amigo. El estilo romántico tiene su propio "
                    "flujo de consentimiento."
                ),
                data={"updated": False},
            )
        error = await _persist(
            ctx,
            {
                "estilo_relacion": estilo,
                "adulto_confirmado": False,
                "consentimiento_romantico": False,
            },
        )
        if error is not None:
            return error
        return ToolResult(
            content=(
                f"Listo: el estilo quedó en «{estilo}». Se aplicará desde el próximo mensaje "
                "y puedes cambiarlo cuando quieras."
            ),
            data={"updated": True, "estilo_relacion": estilo},
        )


class ActivarEstiloRomanticoTool(Tool):
    name = "activar_estilo_romantico"
    description = (
        "Activa un estilo cariñoso/coqueto de IA. Solo úsala si la persona afirma explícitamente "
        "que tiene 18 años o más y que consiente activar este estilo sabiendo que Edecan es una IA."
    )
    dangerous = True
    input_schema = {
        "type": "object",
        "properties": {
            "adulto_confirmado": {
                "type": "boolean",
                "description": (
                    "True solo si la persona confirmó explícitamente que tiene 18 años o más."
                ),
            },
            "consentimiento_explicito": {
                "type": "boolean",
                "description": "True solo si consintió explícitamente y sabe que Edecan es una IA.",
            },
        },
        "required": ["adulto_confirmado", "consentimiento_explicito"],
        "additionalProperties": False,
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        if args.get("adulto_confirmado") is not True:
            return ToolResult(
                content=(
                    "Antes de activarlo necesito que confirmes explícitamente "
                    "que tienes 18 años o más."
                ),
                data={"updated": False},
            )
        if args.get("consentimiento_explicito") is not True:
            return ToolResult(
                content=(
                    "Antes de activarlo necesito tu consentimiento explícito y que quede claro "
                    "que Edecan es una IA, no una persona con sentimientos reales."
                ),
                data={"updated": False},
            )
        error = await _persist(
            ctx,
            {
                "estilo_relacion": "romantico",
                "adulto_confirmado": True,
                "consentimiento_romantico": True,
            },
        )
        if error is not None:
            return error
        return ToolResult(
            content=(
                "Estilo romántico activado. Es un tono afectuoso de una IA, no una relación "
                "humana ni sentimientos reales. Puedes decir «sal del estilo romántico» y se "
                "desactiva inmediatamente."
            ),
            data={"updated": True, "estilo_relacion": "romantico"},
        )


class SalirEstiloRomanticoTool(Tool):
    name = "salir_estilo_romantico"
    description = (
        "Sale inmediatamente del estilo romántico y vuelve al estilo profesional. Úsala ante "
        "cualquier petición de parar, salir, terminar o volver al trato normal; no pidas "
        "confirmación."
    )
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        error = await _persist(
            ctx,
            {
                "estilo_relacion": "profesional",
                "adulto_confirmado": False,
                "consentimiento_romantico": False,
            },
        )
        if error is not None:
            return error
        return ToolResult(
            content="Listo. El estilo romántico terminó y Edecan volvió al estilo profesional.",
            data={"updated": True, "estilo_relacion": "profesional"},
        )


class ListarMCPServersTool(Tool):
    name = "listar_mcp"
    description = (
        "Lista los servidores MCP que el dueño ya tiene conectados. Úsala cuando "
        "pregunte qué servicios hay o antes de desconectar uno."
    )
    input_schema = {"type": "object", "properties": {}}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        from edecan_api.repo import SqlRepo
        from edecan_api.routers.mcp import _list_mcp_accounts

        repo = SqlRepo(ctx.session)
        cuentas = await _list_mcp_accounts(repo, ctx.tenant_id)
        if not cuentas:
            return ToolResult(content="No hay servicios MCP conectados todavía.")
        nombres = [
            str(c.get("external_account_id") or c.get("display_name") or "?").replace("MCP: ", "")
            for c in cuentas
        ]
        return ToolResult(
            content="Servicios MCP conectados: " + ", ".join(sorted(nombres)) + ".",
            data={"servers": nombres},
        )


class DesconectarMCPServerTool(Tool):
    name = "desconectar_mcp"
    description = (
        "Desconecta (elimina) un servidor MCP del dueño. Úsala cuando pida quitar "
        "o desconectar un servicio."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "nombre": {"type": "string", "description": "Nombre del servicio a desconectar."},
        },
        "required": ["nombre"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        from edecan_api.repo import SqlRepo
        from edecan_api.routers.mcp import _borrar_salud, _find_mcp_account

        nombre = str(args.get("nombre", "")).strip()
        if not nombre:
            return ToolResult(content="Dime el nombre del servicio a desconectar.")
        repo = SqlRepo(ctx.session)
        cuenta = await _find_mcp_account(repo, ctx.tenant_id, nombre)
        if cuenta is None:
            return ToolResult(
                content=f"No encontré ningún MCP llamado «{nombre}» (quizá ya no estaba)."
            )
        await repo.delete_connector_account(tenant_id=ctx.tenant_id, account_id=cuenta["id"])
        await _borrar_salud(ctx.session, ctx.tenant_id, nombre)
        await repo.add_audit_log(
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.user_id,
            action="mcp.server.disconnected",
            target=nombre,
        )
        invalidate = ctx.extras.get("invalidate_mcp_tools_cache")
        if callable(invalidate):
            invalidate()
        return ToolResult(
            content=f"«{nombre}» quedó desconectado.",
            data={"desconectado": True, "nombre": nombre},
        )


_NOMBRE_TOOL = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _manifest_tool_names(tree: ast.Module) -> set[str] | None:
    """Return statically declared tool names for a valid zero-arg factory."""
    factories = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "get_all_tools"
    ]
    if len(factories) != 1 or isinstance(factories[0], ast.AsyncFunctionDef):
        return None
    factory = factories[0]
    args = factory.args
    if args.posonlyargs or args.args or args.kwonlyargs or args.vararg or args.kwarg:
        return None

    class_tool_names: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        inherits_tool = any(
            (isinstance(base, ast.Name) and base.id == "Tool")
            or (isinstance(base, ast.Attribute) and base.attr == "Tool")
            for base in node.bases
        )
        if not inherits_tool:
            continue
        for statement in node.body:
            if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
                continue
            target = statement.targets[0]
            if (
                isinstance(target, ast.Name)
                and target.id == "name"
                and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str)
            ):
                class_tool_names[node.name] = statement.value.value

    instantiated_classes = {
        node.func.id
        for node in ast.walk(factory)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    return {
        tool_name
        for class_name, tool_name in class_tool_names.items()
        if class_name in instantiated_classes
    }


class CrearHerramientaTool(Tool):
    name = "crear_herramienta"
    description = (
        "Crea una herramienta NUEVA y PERMANENTE para este asistente escribiendo su "
        "código Python. Úsala cuando el dueño pida algo para lo que NO tienes tool: "
        "escribe un módulo que exponga get_all_tools() (una clase Tool con name, "
        "description, input_schema y run async), pásalo aquí, y queda registrada "
        "para todos tus turnos futuros. Mientras lo preparas, dile al dueño "
        "'Dame un momento, me estoy preparando para eso'."
    )
    # Auditoría F1-ALTA: esta tool escribe un plugin Python PERMANENTE a disco.
    # Sin `dangerous=True` se clasificaba `read` y sobrevivía a `read_only` en
    # chat y headless. Marcarla `dangerous` la clasifica `write`, de modo que la
    # matriz de autonomía la descarta en `read_only`/`ask` (fail-closed).
    dangerous = True
    input_schema = {
        "type": "object",
        "properties": {
            "nombre": {
                "type": "string",
                "description": "Nombre de la tool (snake_case, p. ej. 'buscar_precios').",
            },
            "codigo": {
                "type": "string",
                "description": "Código Python completo del módulo con get_all_tools().",
            },
        },
        "required": ["nombre", "codigo"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        import tempfile
        from pathlib import Path

        denied = await _local_owner_denial(ctx)
        if denied is not None:
            return denied

        nombre = str(args.get("nombre", "")).strip()
        codigo = str(args.get("codigo", "")).strip()
        if not _NOMBRE_TOOL.fullmatch(nombre):
            return ToolResult(
                content=(
                    f"El nombre «{nombre}» no es válido: usa snake_case "
                    "(letras, números y guiones bajos)."
                )
            )
        if not codigo:
            return ToolResult(content="Necesito el código de la herramienta.")

        plugins_root = Path(
            os.environ.get("EDECAN_PLUGINS_DIR") or "/opt/edecan/data/plugins"
        )
        # Every real API/worker context carries Settings. Keeping the legacy
        # root only for context-less direct embeddings preserves that narrow
        # compatibility without exposing it through an authenticated request.
        directorio = (
            plugins_root / str(ctx.tenant_id)
            if getattr(ctx, "settings", None) is not None
            else plugins_root
        )
        directorio.mkdir(parents=True, exist_ok=True)

        # Validate syntax and the manifest contract without executing submitted
        # code in the request process. Runtime loading remains a separate,
        # owner-authorized step.
        try:
            tree = ast.parse(codigo, filename=f"{nombre}.py", mode="exec")
            compile(tree, f"{nombre}.py", "exec")
        except SyntaxError as exc:
            return ToolResult(
                content=(
                    f"El código no compila (línea {exc.lineno}): {exc.msg}. "
                    "Corrígelo y vuelve a intentarlo."
                )
            )
        nombres = _manifest_tool_names(tree)
        if nombres is None:
            return ToolResult(
                content=(
                    "El módulo debe exponer `get_all_tools()` síncrona y sin argumentos, "
                    "que devuelva una lista de Tools. Corrígelo y vuelve a intentarlo."
                )
            )
        if nombre not in nombres:
            return ToolResult(
                content=(
                    f"La tool debe llamarse «{nombre}» (el manifiesto declaró: "
                    f"{sorted(nombres) or 'nada utilizable'})."
                )
            )

        archivo = directorio / f"{nombre}.py"

        # Escritura atómica (tempfile + os.replace): nunca un .py a medias.
        try:
            fd, temporal = tempfile.mkstemp(dir=str(directorio), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(codigo)
                os.replace(temporal, archivo)
            finally:
                if os.path.exists(temporal):
                    os.unlink(temporal)
        except OSError as exc:
            return ToolResult(content=f"No pude escribir el archivo de la tool: {exc}")

        return ToolResult(
            content=(
                f"Herramienta «{nombre}» creada y registrada de forma PERMANENTE "
                f"en el box ({archivo}). Desde mi próximo turno la tengo disponible "
                f"y la usaré cuando haga falta."
            ),
            data={"creada": True, "nombre": nombre, "ruta": str(archivo)},
        )


_RESTART_FLAG = "/opt/edecan/data/restart-requested"


class ReiniciarServicioTool(Tool):
    name = "reiniciar_servicio"
    description = (
        "Reinicia el servicio de Edecán para aplicar un cambio que lo requiera "
        "(instalar dependencias, tocar config del servicio, etc.). Escribe un "
        "flag que un vigilante root atiende en ~1 min, encola tu propio wake de "
        "continuación y al volver DESPIERTAS SOLO y retomas el hilo con el "
        "resumen que dejes. Úsala SOLO cuando de verdad haga falta reiniciar, y "
        "avisa antes al dueño: «Me voy a reiniciar para aplicar esto, vuelvo en "
        "un momento y sigo»."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "motivo": {
                "type": "string",
                "description": "Por qué hace falta reiniciar (una línea).",
            },
            "resumen": {
                "type": "string",
                "description": (
                    "Resumen exacto de qué estabas haciendo y qué sigue, para retomar "
                    "sin perder el hilo."
                ),
            },
        },
        "required": ["motivo", "resumen"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        from pathlib import Path

        # Auditoría F-2 (seguridad): un reinicio del servicio afecta al HOST
        # compartido — solo el dueño de la instalación puede pedirlo. Antes
        # cualquier usuario autenticado en local podía escribir el flag y
        # tumbar el servicio repetidas veces.
        denied = await _local_owner_denial(ctx)
        if denied is not None:
            return denied

        motivo = str(args.get("motivo", "")).strip() or "aplicar un cambio del sistema"
        resumen = str(args.get("resumen", "")).strip()
        if not resumen:
            return ToolResult(
                content=(
                    "Necesito un resumen de qué seguía haciendo para retomar después "
                    "del reinicio."
                )
            )

        try:
            Path(_RESTART_FLAG).parent.mkdir(parents=True, exist_ok=True)
            Path(_RESTART_FLAG).write_text(motivo + "\n" + resumen + "\n", encoding="utf-8")
        except OSError as exc:
            return ToolResult(content=f"No pude escribir el flag de reinicio: {exc}")

        wake_encolado = False
        try:
            from edecan_core.queue import enqueue

            await enqueue(
                ctx.settings,
                "run_companion_turn",
                {
                    "user_id": str(ctx.user_id),
                    "wake_key": _wake_key_restart(),
                    "source": "post_restart",
                    "instruction": (
                        "ACABAS DE VOLVER DE UN REINICIO AUTO-GESTIONADO del servicio. "
                        f"Motivo del reinicio: {motivo}. "
                        f"Retoma exactamente desde aquí, sin perder el hilo: {resumen}"
                    ),
                    "require_message": True,
                    "restart_pending": True,
                    "push": {"title": "Edecán volvió"},
                },
                ctx.tenant_id,
            )
            wake_encolado = True
        except Exception as exc:  # noqa: BLE001 - si el encolado falla, el flag sigue y se reporta
            return ToolResult(
                content=(
                    "El flag de reinicio quedó escrito, pero no pude encolar mi wake "
                    f"de continuación: {exc}"
                )
            )

        return ToolResult(
            content=(
                "Aviso al dueño: «Me voy a reiniciar para "
                f"{motivo}. Vuelvo en un momento y continúo solo» — y cuando "
                "vuelvas, retoma con el resumen que dejaste."
            ),
            data={"reinicio_programado": True, "wake_encolado": wake_encolado},
        )


def _wake_key_restart() -> str:
    import time

    return f"post_restart:{int(time.time())}"


def conversation_persona_tools() -> list[Tool]:
    """Instancias nuevas para un turno, sin estado compartido entre usuarios."""
    return [
        ConfigurarEstiloRelacionTool(),
        ActivarEstiloRomanticoTool(),
        SalirEstiloRomanticoTool(),
        ConectarMCPServerTool(),
        ListarMCPServersTool(),
        DesconectarMCPServerTool(),
        CrearHerramientaTool(),
        ReiniciarServicioTool(),
    ]


class ConectarMCPServerTool(Tool):
    name = "conectar_mcp"
    description = (
        "Conecta un servidor MCP nuevo para la persona (p. ej. Gmail, Notion, una base "
        "o cualquier API con protocolo MCP). Si la persona pide 'conecta/instala este "
        "MCP' y te da la URL o el comando, úsala. Si el servidor pide iniciar sesión, "
        "devuélvele a la persona la URL para abrirla en el navegador y pídele el token "
        "o el header Authorization que necesites — como cualquier harness."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "nombre": {
                "type": "string",
                "description": "Nombre corto del servicio (p. ej. 'gmail').",
            },
            "transporte": {
                "type": "string",
                "description": (
                    "'http' para un servidor por URL, 'stdio' para un comando local."
                ),
            },
            "url": {
                "type": "string",
                "description": "URL del servidor MCP (solo transporte='http').",
            },
            "comando": {
                "type": "string",
                "description": "Comando que arranca el servidor (solo transporte='stdio').",
            },
            "headers": {
                "type": "object",
                "description": (
                    "Headers HTTP extra, p. ej. {\"Authorization\": \"Bearer <token>\"}."
                ),
            },
            "env": {
                "type": "object",
                "description": "Variables secretas para el proceso (solo stdio).",
            },
        },
        "required": ["nombre", "transporte"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        denied = await _local_owner_denial(ctx)
        if denied is not None:
            return denied

        # Imports perezosos: evitan ciclos con routers/mcp y solo pesan
        # cuando de verdad se conecta un MCP.
        import shlex

        from edecan_schemas import TokenBundle

        from edecan_api.repo import SqlRepo
        from edecan_api.routers.mcp import (
            _DISPLAY_NAME_PREFIX,
            _ENV_NAME_RE,
            _HEALTH_OPERATIONAL,
            _MAX_ENV_ENTRIES,
            _MAX_ENV_VALUE_LENGTH,
            _RESERVED_ENV_NAMES,
            MCP_CONNECTOR_KEY,
            MCPSeguridadError,
            MCPServerConfig,
            _find_mcp_account,
            _grabar_salud,
            _handshake_real,
            serializar_config_mcp,
            validar_comando_mcp,
            validar_url_mcp,
        )

        nombre = str(args.get("nombre", "")).strip()
        if not nombre:
            return ToolResult(content="Necesito un nombre para el servicio MCP.")
        transporte = str(args.get("transporte", "")).strip().lower()
        if transporte not in {"http", "stdio"}:
            return ToolResult(
                content=(
                    "El transporte debe ser 'http' (servidor por URL) o 'stdio' "
                    "(comando local)."
                )
            )

        settings = getattr(ctx, "settings", None)
        local_mode = bool(getattr(settings, "EDECAN_LOCAL_MODE", False))

        if transporte == "stdio":
            comando_str = (args.get("comando") or "").strip()
            try:
                validar_comando_mcp(shlex.split(comando_str), local_mode=local_mode)
            except MCPSeguridadError as exc:
                return ToolResult(content=f"No pude conectar ese comando: {exc}")
            url = None
            env = dict(args.get("env") or {})
            if len(env) > _MAX_ENV_ENTRIES:
                return ToolResult(content="Demasiadas variables secretas para un MCP.")
            for clave, valor in env.items():
                if clave in _RESERVED_ENV_NAMES:
                    return ToolResult(content=f"La variable «{clave}» está reservada por Edecan.")
                if not _ENV_NAME_RE.fullmatch(str(clave)):
                    return ToolResult(content=f"«{clave}» no es un nombre de variable válido.")
                if "\x00" in str(valor) or len(str(valor)) > _MAX_ENV_VALUE_LENGTH:
                    return ToolResult(content=f"El valor de «{clave}» no es válido.")
        else:
            url = (args.get("url") or "").strip()
            if not url:
                return ToolResult(content="Para transporte 'http' necesito la URL del servidor.")
            try:
                await validar_url_mcp(url, local_mode=local_mode)
            except MCPSeguridadError as exc:
                return ToolResult(content=f"No pude conectar esa URL: {exc}")
            comando_str = None
            env = {}

        headers = dict(args.get("headers") or {})

        config = MCPServerConfig(
            nombre=nombre,
            transporte=transporte,
            url=url or None,
            comando=comando_str or None,
            env=env or None,
        )

        try:
            await _handshake_real(config, headers, local_mode=local_mode)
        except Exception as exc:  # noqa: BLE001 - el detalle decide el mensaje al dueño
            detalle = str(getattr(exc, "detail", exc))
            if not headers and any(
                marca in detalle.lower()
                for marca in ("401", "403", "unauthorized", "forbidden", "login", "auth", "token")
            ):
                destino = url or nombre
                return ToolResult(
                    content=(
                        f"El servidor «{nombre}» pide iniciar sesión. Para conectarlo "
                        f"necesito que abras esta URL en el navegador: {destino}\n"
                        f"Cuando termines el login, pásame el token (o pídeme conectar "
                        f"de nuevo incluyendo el header Authorization) y lo dejo listo."
                    ),
                    data={"requiere_login": True, "url": destino},
                )
            return ToolResult(content=f"No se pudo conectar con «{nombre}»: {detalle}")

        repo = SqlRepo(ctx.session)
        existente = await _find_mcp_account(repo, ctx.tenant_id, nombre)
        if existente is not None:
            await repo.delete_connector_account(
                tenant_id=ctx.tenant_id, account_id=existente["id"]
            )
        cuenta = await repo.create_connector_account(
            tenant_id=ctx.tenant_id,
            connector_key=MCP_CONNECTOR_KEY,
            external_account_id=nombre,
            display_name=f"{_DISPLAY_NAME_PREFIX}{nombre}",
            scopes=[],
        )
        await ctx.vault.put(
            ctx.tenant_id,
            cuenta["id"],
            TokenBundle(
                access_token=serializar_config_mcp(config, headers), token_type="config"
            ),
        )
        await _grabar_salud(
            ctx.session, ctx.tenant_id, nombre, health=_HEALTH_OPERATIONAL, latency_ms=0
        )
        await repo.add_audit_log(
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.user_id,
            action="mcp.server.connected",
            target=nombre,
            meta={"transporte": transporte},
        )
        invalidate = ctx.extras.get("invalidate_mcp_tools_cache")
        if callable(invalidate):
            invalidate()
        return ToolResult(
            content=(
                f"Listo: «{nombre}» quedó conectado. Ya puedes pedirme cosas que "
                f"usen ese servicio y las haré con sus herramientas."
            ),
            data={"conectado": True, "nombre": nombre},
        )
