"""Adaptador Codex CLI — usa el binario `codex` de OpenAI ya instalado y
autenticado en la máquina del cliente (`DIRECCION_ACTUAL.md`, "Nuevo
requisito: conectar el LLM vía CLI local", WP-V3-03).

Análogo a `claude_cli.ClaudeCLIProvider`: mismo mecanismo de subproceso
(JAMÁS `shell=True`, prompt siempre por stdin, timeout con `asyncio.wait_for`
+ kill del proceso, tool-calling por prompt vía `prompted_tools`), pero:

- Comando: `codex exec --json` (en vez de `claude -p --output-format json`).
- La salida es JSONL (una línea = un evento), con un shape que no está
  pinneado en ningún lado — el parseo es tolerante: junta los eventos cuyo
  `type` contiene "message" o "agent" y usa el texto del ÚLTIMO como
  respuesta final (ver `_extract_last_agent_message`). Si ninguna línea
  matchea, usa el `stdout` crudo completo como texto.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from collections.abc import AsyncIterator
from tempfile import TemporaryDirectory

from .base import CompletionRequest, CompletionResponse, LLMProvider, StreamChunk, Usage
from .errors import CLINotAuthenticatedError, CLINotInstalledError, LLMError
from .multimodal import cli_image_context, materialize_request_images
from .output_safety import sanitize_visible_assistant_text
from .prompted_tools import parse_tool_call, render_prompt

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 300
INSTALL_URL = "https://github.com/openai/codex"
_AUTH_HINTS = ("login", "auth", "api key")
_ISOLATION_PROMPT = (
    "Estás actuando únicamente como el motor de decisión de Edecan. "
    "No inspecciones archivos, no ejecutes comandos, no uses herramientas internas "
    "de Codex y no intentes producir artefactos por tu cuenta. Si la solicitud "
    "necesita una herramienta de Edecan, devuelve solamente la llamada JSON que "
    "aparece en las instrucciones siguientes; Edecan la ejecutará y validará fuera "
    "de este proceso. Entrega únicamente el mensaje final destinado a la persona. "
    "No expongas análisis, razonamiento, planificación, borradores, notas internas ni "
    "autonarración como 'el usuario dijo...' o 'debo responder...'."
)

# `codex exec` es un agente de código completo, no un endpoint de inferencia
# desnudo. Sin estos límites puede obedecer una petición como "crea un PDF"
# escribiendo directamente en el repositorio del servidor, saltándose el
# ToolContext, las confirmaciones y el workspace aislado de Edecan.
_ISOLATION_ARGS = (
    "--ephemeral",
    "--ignore-user-config",
    "--sandbox",
    "read-only",
    "--skip-git-repo-check",
    "--disable",
    "shell_tool",
    "--disable",
    "unified_exec",
    "--disable",
    "apps",
    "--disable",
    "browser_use",
    "--disable",
    "computer_use",
    "--disable",
    "image_generation",
    "--disable",
    "multi_agent",
)


class CodexCLIProvider(LLMProvider):
    """Proveedor que ejecuta el binario `codex` como subproceso local."""

    name = "codex_cli"

    def __init__(
        self,
        binary_path: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        resolved = binary_path or shutil.which("codex")
        if not resolved:
            raise CLINotInstalledError(
                "Codex CLI no está instalado o no se encuentra en el PATH. "
                f"Instálalo (ver {INSTALL_URL}) y vuelve a intentar.",
                provider=self.name,
            )
        self._binary_path = resolved
        self._timeout_seconds = timeout_seconds

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        with TemporaryDirectory(prefix="edecan-codex-images-") as image_dir:
            image_paths = materialize_request_images(req, image_dir)
            prompt = _render_codex_prompt(req, image_paths)
            stdout, _stderr = await self._run(
                self._args(req, image_paths=image_paths), prompt
            )
        return _parse_response(stdout, req)

    async def stream(self, req: CompletionRequest) -> AsyncIterator[StreamChunk]:
        """`codex exec --json` ya emite JSONL de eventos para complete() y
        stream() por igual (Codex no tiene un modo "stream-json" aparte como
        Claude Code): acá se traduce cada mensaje de agente reconocido a su
        propio `StreamChunk` de texto en vez de colapsarlos a uno. Si ningún
        evento matchea el patrón tolerante, degrada a una única respuesta —
        mismo criterio que `ClaudeCLIProvider.stream`.
        """
        with TemporaryDirectory(prefix="edecan-codex-images-") as image_dir:
            image_paths = materialize_request_images(req, image_dir)
            prompt = _render_codex_prompt(req, image_paths)
            stdout, _stderr = await self._run(
                self._args(req, image_paths=image_paths), prompt
            )

        chunks = _parse_events(stdout, tools_requested=bool(req.tools))
        if chunks is None:
            logger.info(
                "Codex CLI: no se reconoció ningún evento 'message'/'agent' en "
                "la salida JSONL; degradando a una única respuesta (complete())."
            )
            response = _parse_response(stdout, req)
            chunks = _response_to_chunks(response)
        for chunk in chunks:
            yield chunk

    def _args(self, req: CompletionRequest, *, image_paths: list[str] | None = None) -> list[str]:
        args = [self._binary_path, "exec", "--json"]
        if req.model:
            args += ["--model", req.model]
        for image_path in image_paths or []:
            args += ["--image", image_path]
        return args

    async def _run(self, args: list[str], prompt: str) -> tuple[str, str]:
        # Un directorio vacío evita que Codex cargue AGENTS.md/código del repo
        # anfitrión. El sandbox read-only es una segunda barrera: aunque el
        # modelo ignore la instrucción, no puede escribir fuera del protocolo.
        with TemporaryDirectory(prefix="edecan-codex-") as isolated_workdir:
            isolated_args = [*args, *_ISOLATION_ARGS, "-C", isolated_workdir]
            isolated_prompt = f"{_ISOLATION_PROMPT}\n\n{prompt}"
            try:
                process = await asyncio.create_subprocess_exec(
                    *isolated_args,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError as exc:
                raise CLINotInstalledError(
                    f"Codex CLI no está instalado en {self._binary_path!r}. "
                    f"Instálalo (ver {INSTALL_URL}).",
                    provider=self.name,
                ) from exc

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(isolated_prompt.encode("utf-8")),
                    timeout=self._timeout_seconds,
                )
            except TimeoutError as exc:
                process.kill()
                await process.wait()
                raise LLMError(
                    f"Codex CLI no respondió en {self._timeout_seconds}s. Si tu "
                    "máquina o el modelo elegido son lentos, sube "
                    "LLM_CLI_TIMEOUT_SECONDS.",
                    provider=self.name,
                ) from exc

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        if process.returncode != 0:
            combined = f"{stderr}\n{stdout}".lower()
            if any(hint in combined for hint in _AUTH_HINTS):
                raise CLINotAuthenticatedError(
                    "Codex CLI no está autenticado: corre `codex login` en una "
                    "terminal y vuelve a intentar.",
                    provider=self.name,
                )
            raise LLMError(
                f"Codex CLI terminó con código {process.returncode}: "
                f"{stderr.strip() or stdout.strip()}",
                provider=self.name,
            )
        return stdout, stderr


def _render_codex_prompt(req: CompletionRequest, image_paths: list[str]) -> str:
    image_context = cli_image_context(image_paths)
    return "\n\n".join(piece for piece in (render_prompt(req), image_context) if piece)


def _response_to_chunks(response: CompletionResponse) -> list[StreamChunk]:
    chunks: list[StreamChunk] = []
    if response.tool_calls:
        chunks.extend(
            StreamChunk(type="tool_call", tool_call=call) for call in response.tool_calls
        )
    elif response.text:
        chunks.append(StreamChunk(type="text", text=response.text))
    chunks.append(StreamChunk(type="usage", usage=response.usage))
    chunks.append(StreamChunk(type="stop"))
    return chunks


def _parse_response(stdout: str, req: CompletionRequest) -> CompletionResponse:
    text, usage, recognized = _extract_last_agent_message(stdout)
    if not recognized:
        text = stdout.strip()
    text = sanitize_visible_assistant_text(text)

    if req.tools:
        tool_call = parse_tool_call(text)
        if tool_call is not None:
            return CompletionResponse(
                text="", tool_calls=[tool_call], usage=usage, stop_reason="tool_use"
            )
    return CompletionResponse(text=text, tool_calls=[], usage=usage, stop_reason="end")


def _parse_events(stdout: str, *, tools_requested: bool) -> list[StreamChunk] | None:
    text, usage, recognized = _extract_last_agent_message(stdout)
    if not recognized:
        return None
    text = sanitize_visible_assistant_text(text)

    tool_call = parse_tool_call(text) if tools_requested and text else None
    chunks: list[StreamChunk] = []
    if tool_call is not None:
        chunks.append(StreamChunk(type="tool_call", tool_call=tool_call))
    elif text:
        chunks.append(StreamChunk(type="text", text=text))
    chunks.append(StreamChunk(type="usage", usage=usage))
    chunks.append(StreamChunk(type="stop"))
    return chunks


def _extract_last_agent_message(stdout: str) -> tuple[str, Usage, bool]:
    """Recorre el JSONL de `codex exec --json` línea por línea: junta los
    eventos cuyo `type` contiene "message" o "agent" — o que, sin importar el
    `type` (p. ej. `"item.completed"`), traen un campo `message` no vacío —
    (parseo tolerante, el shape exacto no está documentado/pinned en ningún
    lado) y se queda con el texto del ÚLTIMO — Codex puede emitir mensajes
    intermedios de progreso que el mensaje final reemplaza, así que NO se
    concatenan. También junta el último `usage` que aparezca en cualquier
    evento, tenga o no ese tipo. `recognized=False` si ninguna línea matcheó
    el patrón — señal para que el llamador degrade a texto crudo/`complete()`.
    """
    text = ""
    usage = Usage()
    recognized = False

    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue

        usage_data = event.get("usage")
        if isinstance(usage_data, dict):
            usage = Usage(
                input_tokens=int(usage_data.get("input_tokens") or 0),
                output_tokens=int(usage_data.get("output_tokens") or 0),
            )

        event_type = str(event.get("type", "")).lower()
        item = event.get("item")
        item_type = str(item.get("type", "")).lower() if isinstance(item, dict) else ""
        type_hints_message = (
            "message" in event_type
            or "agent" in event_type
            or "message" in item_type
            or "agent" in item_type
        )
        has_message_field = bool(event.get("message"))
        if not type_hints_message and not has_message_field:
            continue
        extracted = _extract_text(event)
        if extracted:
            text = extracted
            recognized = True

    return text, usage, recognized


def _extract_text(event: dict) -> str | None:
    """Extrae texto de un evento con forma desconocida (parseo tolerante):
    prueba `text`/`content` en el nivel superior, y si `message` es un
    string o un dict con `text`/`content` (este último posiblemente una
    lista de bloques estilo Anthropic `[{"type": "text", "text": ...}]`).
    """
    for key in ("text", "content"):
        value = event.get(key)
        if isinstance(value, str) and value:
            return value

    message = event.get("message")
    if isinstance(message, str) and message:
        return message
    if isinstance(message, dict):
        for key in ("text", "content"):
            value = message.get(key)
            if isinstance(value, str) and value:
                return value
            if isinstance(value, list):
                joined = "".join(
                    b.get("text", "")
                    for b in value
                    if isinstance(b, dict) and b.get("type") == "text"
                )
                if joined:
                    return joined

    # Codex CLI 0.144+ envuelve los mensajes finales así:
    # {"type":"item.completed","item":{"type":"agent_message","text":"..."}}
    # Solo aceptamos el item si su propio tipo confirma que es un mensaje;
    # errores/progreso no deben terminar como texto visible del asistente.
    item = event.get("item")
    if isinstance(item, dict):
        item_type = str(item.get("type", "")).lower()
        if "message" in item_type or "agent" in item_type:
            for key in ("text", "content"):
                value = item.get(key)
                if isinstance(value, str) and value:
                    return value
    return None
