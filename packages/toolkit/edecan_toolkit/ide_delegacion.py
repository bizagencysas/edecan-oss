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
import logging
import os
import time
from typing import Any

import httpx
from edecan_core import Tool, ToolContext, ToolResult

logger = logging.getLogger(__name__)

_PUERTO_DEFAULT = 8765
_INTERVALO_POLL = 5.0
_MAX_ESPERA_SEGUNDOS = 1800
_MAX_CHARS_TEXTO = 8000
_ESTADOS_OCUPADO = frozenset({"starting", "running", "plan_pending"})
_ESTADOS_TERMINAL = frozenset({"completed", "failed", "cancelled"})


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


class DelegarAlIDETool(Tool):
    name = "delegar_al_ide"
    description = (
        "Delega un encargo de código/ingeniería al IDE de Edecán (su motor opencode "
        "empaquetado, dentro de la app): corre un agente sobre un workspace, con sus "
        "modos de permiso, y devuelve el estado final y el texto producido. Úsala para "
        "trabajo real sobre repos: editar archivos, correr comandos, arreglar bugs. "
        "Después de delegar, VERIFICA el resultado contra el disco (o `git status`), "
        "nunca confíes solo en el estado que devuelve. Solo funciona en la Mac del dueño "
        "con la app Edecán abierta."
    )
    category = "code"
    risk_level = "high"
    dangerous = True
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
        base = _base(ctx)
        capability = _capability()
        if not capability:
            return ToolResult(
                content=(
                    "No encontré la capability del escritorio: esta tool solo funciona "
                    "dentro de la app Edecán instalada en la Mac (no en dev ni en el "
                    "servidor). Si estás en la Mac, abre la app Edecán y reintenta."
                )
            )

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
                respuesta_modo = await client.put(
                    f"{base}/v1/ide/agents/{session_id}/modo",
                    headers=headers,
                    json={"modo": modo},
                )
                if respuesta_modo.status_code >= 400:
                    logger.warning(
                        "delegar_al_ide: no pude fijar modo=%s (HTTP %s); sigo igual",
                        modo,
                        respuesta_modo.status_code,
                    )

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