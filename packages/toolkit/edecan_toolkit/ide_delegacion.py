"""Delegar un encargo al IDE de Edecán (motor opencode empaquetado de la app).

El bot NO debe trabajar el código a ciegas por la terminal: para trabajo de
ingeniería real delega en el IDE de Edecán, que corre un agente sobre un
workspace con sus propios modos de permiso, y después verifica el resultado
contra el disco. Esta tool habla con la API local de la app instalada
(`http://127.0.0.1:8765`) usando la capability efímera del proceso de
escritorio — el mismo protocolo verificado el 1-sep-2026 (healthz → auth/local
→ /v1/ide/*).
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx
from edecan_core import Tool, ToolContext, ToolResult
from edecan_core.safety import redact

_PUERTO_DEFAULT = 8765
_INTERVALO_POLL = 5.0
_MAX_ESPERA_SEGUNDOS = 1800
_MAX_CHARS_TEXTO = 8000
_ESTADOS_OCUPADO = frozenset({"starting", "running", "plan_pending"})
_ESTADOS_TERMINAL = frozenset({"completed", "failed", "cancelled"})
_MAX_DETALLE_ERROR = 200


def _redactar(exc: BaseException | str) -> str:
    texto = str(exc)
    token = os.environ.get("LOCAL_DESKTOP_CAPABILITY", "").strip()
    if token and token in texto:
        texto = texto.replace(token, "***")
    return texto[:_MAX_DETALLE_ERROR]


def _base(ctx: ToolContext) -> str:
    puerto = int(getattr(ctx.settings, "LOCAL_API_PORT", None) or _PUERTO_DEFAULT)
    return f"http://127.0.0.1:{puerto}"


def _capability() -> str:
    return os.environ.get("LOCAL_DESKTOP_CAPABILITY", "").strip()


def _token_headers(token: str, capability: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Edecan-Desktop-Capability": capability,
    }


async def _fijar_modo_restringido(
    client: httpx.AsyncClient,
    base: str,
    headers: dict[str, str],
    session_id: str,
    modo: str,
) -> ToolResult | None:
    """Fija un modo CON freno (manual/aceptar_ediciones/plan) y valida que quedó
    aplicado del lado del servidor. Devuelve `None` solo si el modo pedido quedó
    fijado y releído; si hay que abortar, devuelve el `ToolResult` de error.

    C2 (auditoría): antes, un fallo del `PUT /v1/ide/agents/{id}/modo` solo se
    logueaba y la delegación seguía adelante — el agente del IDE quedaba con
    autonomía total sin los frenos pedidos. Ahora NUNCA se degrada a `auto` en
    silencio: si el PUT falla, o si el estado real no coincide con lo pedido, se
    aborta la delegación con un error claro.

    El PUT es idempotente-confirmatorio: un 2xx no es prueba de que el freno
    quedó aplicado en el motor (AGENTS.md §5, "un 200 no es una prueba"), así
    que tras el PUT se relee `GET /v1/ide/agents/{id}/modo` y se compara el modo
    real contra el pedido.
    """
    respuesta_modo = await client.put(
        f"{base}/v1/ide/agents/{session_id}/modo",
        headers=headers,
        json={"modo": modo},
    )
    if respuesta_modo.status_code >= 400:
        return ToolResult(
            content=(
                f"No pude fijar el modo restringido {modo!r} del agente del IDE "
                f"(PUT modo → HTTP {respuesta_modo.status_code}). Aborté la delegación "
                "para no dejar al agente trabajando sin frenos. Revisa la app Edecán "
                "y reintenta."
            )
        )
    lectura_modo = await client.get(
        f"{base}/v1/ide/agents/{session_id}/modo", headers=headers
    )
    if lectura_modo.status_code != 200:
        return ToolResult(
            content=(
                f"El PUT de modo {modo!r} respondió, pero no pude releer el modo real "
                f"del agente del IDE (GET modo → HTTP {lectura_modo.status_code}). "
                "Aborté la delegación para no dejarlo trabajando sin frenos."
            )
        )
    modo_real = str((lectura_modo.json() or {}).get("modo") or "")
    if modo_real != modo:
        return ToolResult(
            content=(
                f"El IDE no fijó el modo pedido: pedí {modo!r} y quedó {modo_real!r}. "
                "Aborté la delegación para no dejar al agente trabajando sin frenos."
            )
        )
    return None


class DelegarAlIDETool(Tool):
    name = "delegar_al_ide"
    description = (
        "Delega ingeniería al IDE de Edecán (opencode en la app): editar repos, "
        "arreglar bugs como login roto, correr tests. Úsala para encargos grandes de "
        "código en la Mac con la app abierta; para lecturas rápidas o git status usa "
        "`acceder_codigo_local`. Verifica siempre contra disco/git, no solo el "
        "estado que devuelve el IDE."
    )
    category = "code"
    risk_level = "high"
    dangerous = True
    # El poll del agente del IDE puede esperar hasta _MAX_ESPERA_SEGUNDOS;
    # el deadline DURO del executor (E-CORE-2) respeta este valor, así que
    # debe cubrir el máximo real + margen.
    timeout_seconds = float(_MAX_ESPERA_SEGUNDOS) + 120.0
    input_schema = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Encargo completo y autosuficiente para el agente del IDE: "
                "qué hacer, en qué archivos, y qué define 'terminado'.",
            },
            "workspace_id": {
                "type": "string",
                "description": "Workspace del IDE donde trabajar (opcional; si falta, se usa "
                "el primer workspace disponible).",
            },
            "titulo": {
                "type": "string",
                "description": "Título corto de la sesión (opcional).",
            },
            "modo": {
                "type": "string",
                "enum": ["manual", "aceptar_ediciones", "plan", "auto"],
                "description": "Modo de permiso del agente del IDE. Default 'auto' (trabaja "
                "sin frenos). 'manual' pausa en cada edición y el encargo NO terminará sin "
                "que alguien apruebe desde la app.",
            },
            "max_espera_segundos": {
                "type": "integer",
                "description": "Máximo de espera del resultado (default 1200; tope 1800).",
            },
        },
        "required": ["prompt"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            return ToolResult(content="El encargo no puede ir vacío.")
        capability = _capability()
        if not capability:
            # SIN capability de escritorio (VPS): la delegación va por el
            # COMPANION de la Mac, que ejecuta el IDE (opencode) — el mismo
            # camino del iPhone. No hace falta abrir la app de escritorio.
            return await self._run_via_companion(ctx, args, prompt)
        base = _base(ctx)
        return await self._run_local(ctx, args, prompt, base, capability)

    async def _run_via_companion(
        self, ctx: ToolContext, args: dict[str, Any], prompt: str
    ) -> ToolResult:
        manager = ctx.extras.get("companion_manager")
        if manager is None:
            return ToolResult(
                content=(
                    "No hay puente con la Mac (companion). Haz el trabajo TÚ con "
                    "tus tools del box: `acceder_codigo_local` para archivos del "
                    "repo y `run_command` para comandos/pruebas."
                )
            )
        max_espera = min(
            int(args.get("max_espera_segundos") or _MAX_ESPERA_SEGUNDOS),
            _MAX_ESPERA_SEGUNDOS,
        )
        try:
            workspaces = await manager.send_command(
                ctx.tenant_id, "ide_workspace_list", {}, timeout=20, machine="Mac"
            )
        except Exception as exc:
            return ToolResult(
                content=(
                    f"La Mac no respondió al listar los workspaces del IDE "
                    f"({redact(str(exc))}). Verifica que el companion esté vivo "
                    "y reintenta, o trabaja con tus tools del box."
                )
            )
        lista = workspaces.get("workspaces") or workspaces.get("result") or []
        if not lista:
            return ToolResult(
                content=(
                    "El IDE de la Mac no tiene workspaces. Crea uno en la app "
                    "(o pásame workspace_id de uno existente)."
                )
            )
        elegido = None
        if args.get("workspace_id"):
            for item in lista:
                if str(item.get("id") or "") == str(args["workspace_id"]):
                    elegido = str(item["id"])
                    break
            if elegido is None:
                return ToolResult(
                    content=(
                        f"El workspace {args['workspace_id']!r} no existe en el IDE. "
                        f"Disponibles: {[str(w.get('name') or w.get('id')) for w in lista][:8]}"
                    )
                )
        else:
            elegido = str(lista[0].get("id") or "")

        try:
            arranque = await manager.send_command(
                ctx.tenant_id,
                "ide_agent_start",
                {
                    "workspace_id": elegido,
                    "prompt": prompt,
                    "title": args.get("titulo") or "Encargo de bot",
                },
                timeout=30,
                machine="Mac",
            )
        except Exception as exc:
            return ToolResult(
                content=f"La Mac no pudo arrancar el agente del IDE ({redact(str(exc))})."
            )
        session_id = str(
            arranque.get("session_id")
            or arranque.get("id")
            or (arranque.get("session") or {}).get("id")
            or ""
        )
        if not session_id:
            return ToolResult(
                content=(
                    f"El IDE arrancó pero no devolvió sesión: "
                    f"{redact(str(arranque)[:400])}"
                )
            )

        inicio = time.monotonic()
        cursor = 0
        texto = ""
        while time.monotonic() - inicio < max_espera:
            try:
                lectura = await manager.send_command(
                    ctx.tenant_id,
                    "ide_agent_read",
                    {"session_id": session_id, "cursor": cursor},
                    timeout=20,
                    machine="Mac",
                )
            except Exception as exc:
                return ToolResult(
                    content=f"Se perdió la lectura del agente del IDE ({redact(str(exc))})."
                )
            eventos = lectura.get("events") or []
            for evento in eventos:
                texto_evento = evento.get("text") if isinstance(evento, dict) else None
                if texto_evento:
                    texto += str(texto_evento)
            cursor = int(lectura.get("next_cursor") or cursor)
            estado = str((lectura.get("session") or {}).get("status") or "running")
            if estado in _ESTADOS_TERMINAL:
                if estado == "failed":
                    return ToolResult(
                        content=(
                            f"El agente del IDE falló (estado={estado}). "
                            f"Texto parcial: {texto[:_MAX_CHARS_TEXTO]}"
                        )
                    )
                return ToolResult(
                    content=texto[:_MAX_CHARS_TEXTO] or "(el agente terminó sin texto)"
                )
            await asyncio.sleep(_INTERVALO_POLL)
        return ToolResult(
            content=(
                f"El agente del IDE sigue trabajando tras {max_espera}s. "
                f"Texto parcial: {texto[:_MAX_CHARS_TEXTO]}"
            )
        )

    async def _run_local(
        self, ctx: ToolContext, args: dict[str, Any], prompt: str, base: str, capability: str
    ) -> ToolResult:

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                salud = await client.get(f"{base}/healthz")
            except httpx.TransportError:
                return ToolResult(
                    content=(
                        "La app Edecán no está corriendo en la Mac "
                        "(127.0.0.1:8765 no responde). Ábrela y reintenta."
                    )
                )
            if salud.status_code != 200:
                return ToolResult(
                    content=f"La app Edecán respondió raro en /healthz ({salud.status_code})."
                )

            auth = await client.post(
                f"{base}/v1/auth/local",
                headers={"X-Edecan-Desktop-Capability": capability},
            )
            if auth.status_code != 200:
                return ToolResult(
                    content=(
                        "No pude abrir la sesión local del IDE "
                        f"(/v1/auth/local {auth.status_code}). Reabre la app Edecán y reintenta."
                    )
                )
            token = str((auth.json() or {}).get("access_token") or "")
            if not token:
                return ToolResult(content="La sesión local no devolvió token.")
            headers = _token_headers(token, capability)

            workspaces = await client.get(f"{base}/v1/ide/workspaces", headers=headers)
            if workspaces.status_code != 200:
                return ToolResult(
                    content=f"No pude leer los workspaces del IDE ({workspaces.status_code})."
                )
            lista = (workspaces.json() or {}).get("workspaces") or []
            elegido: str | None = None
            if args.get("workspace_id"):
                for item in lista:
                    if str(item.get("id") or "") == str(args["workspace_id"]):
                        elegido = str(item["id"])
                        break
                if elegido is None:
                    return ToolResult(
                        content=(
                            f"El workspace {args['workspace_id']!r} no existe en el IDE. "
                            f"Disponibles: {[str(w.get('name') or w.get('id')) for w in lista][:8]}"
                        )
                    )
            else:
                if not lista:
                    return ToolResult(
                        content=(
                            "El IDE no tiene ningún workspace todavía. Crea uno en la app "
                            "(o pásame workspace_id de uno existente)."
                        )
                    )
                elegido = str(lista[0].get("id") or "")

            inicio = await client.post(
                f"{base}/v1/ide/agents",
                headers=headers,
                json={
                    "workspace_id": elegido,
                    "prompt": prompt,
                    **({"title": str(args["titulo"])} if args.get("titulo") else {}),
                },
            )
            if inicio.status_code != 200:
                return ToolResult(
                    content=f"El IDE rechazó el encargo ({inicio.status_code}): "
                    f"{str((inicio.json() or {}).get('detail') or '')[:200]}"
                )
            sesion = inicio.json() or {}
            session_id = str(sesion.get("id") or "")
            if not session_id:
                return ToolResult(content="El IDE no devolvió el id de la sesión.")

            modo = str(args.get("modo") or "auto")
            if modo != "auto":
                error_modo = await _fijar_modo_restringido(
                    client, base, headers, session_id, modo
                )
                if error_modo is not None:
                    return error_modo

            max_espera = int(args.get("max_espera_segundos") or 1200)
            max_espera = max(30, min(max_espera, _MAX_ESPERA_SEGUNDOS))
            deadline = time.monotonic() + max_espera
            estado = "starting"
            ultimo_texto = ""
            while True:
                await asyncio.sleep(_INTERVALO_POLL)
                lectura = await client.get(
                    f"{base}/v1/ide/agents/{session_id}", headers=headers
                )
                if lectura.status_code != 200:
                    return ToolResult(
                        content=(
                            f"Perdí la pista de la sesión del IDE "
                            f"(GET agent {lectura.status_code}). session_id={session_id}"
                        )
                    )
                payload = lectura.json() or {}
                meta = payload.get("session") or {}
                estado = str(meta.get("status") or estado)
                eventos = payload.get("events") or []
                if isinstance(eventos, list):
                    for evento in eventos:
                        if isinstance(evento, dict) and evento.get("type") in {
                            "text",
                            "result",
                            "status",
                            "error",
                        }:
                            trozo = str(evento.get("text") or "")
                            if trozo.strip():
                                ultimo_texto = trozo
                if estado in _ESTADOS_TERMINAL:
                    break
                if estado == "plan_pending":
                    return ToolResult(
                        content=(
                            "El agente del IDE propuso un plan y quedó esperando aprobación "
                            "(modo plan). Apruébalo en la app Edecán o vuelve a delegar en "
                            "modo 'auto'."
                        ),
                        data={"session_id": session_id, "status": "plan_pending"},
                    )
                if time.monotonic() > deadline:
                    return ToolResult(
                        content=(
                            f"El agente del IDE sigue trabajando tras {max_espera}s "
                            f"(estado: {estado}). Sigue en la app; session_id={session_id}"
                        ),
                        data={"session_id": session_id, "status": estado},
                    )

        texto_final = (ultimo_texto or "").strip()[-_MAX_CHARS_TEXTO:]
        if estado == "completed":
            if texto_final:
                contenido = f"[IDE completado] {texto_final}"
            else:
                contenido = (
                    "El agente del IDE terminó. Verifica el resultado contra el disco "
                    "(git status/diff) antes de darlo por bueno."
                )
        elif estado == "failed":
            contenido = (
                f"[IDE falló] {texto_final or 'sin texto de error; revisa la sesión en la app.'}"
            )
        else:
            contenido = f"[IDE {estado}] {texto_final or 'sin texto final.'}"
        return ToolResult(
            content=contenido,
            data={"session_id": session_id, "status": estado, "workspace_id": elegido},
        )