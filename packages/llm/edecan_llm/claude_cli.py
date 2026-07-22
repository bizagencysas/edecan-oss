"""Adaptador Claude CLI — usa el binario `claude` ya instalado y autenticado
en la máquina del cliente (`DIRECCION_ACTUAL.md`, "Nuevo requisito: conectar
el LLM vía CLI local", WP-V3-03).

Sin API key: reutiliza la sesión/suscripción de Claude Code que el cliente
ya tiene paga (filosofía bring-your-own de `ARCHITECTURE.md` §0). Solo tiene
sentido cuando el backend corre LOCAL en la máquina del cliente (el modelo
de la app de escritorio Tauri, ver `DIRECCION_ACTUAL.md`) — no aplica a un
hosted multi-tenant compartido.

Ejecución: `asyncio.create_subprocess_exec` — JAMÁS `shell=True` — con el
prompt SIEMPRE por stdin (nunca como argumento de la línea de comandos, para
evitar inyección de argv y sus límites de longitud). El modo `-p`/print del
CLI es de un solo turno: todo el historial se aplana a un único prompt de
texto vía `edecan_llm.prompted_tools.render_prompt`.

Tool-calling: el CLI no acepta tool-schemas nativos, así que usa el
protocolo por prompt de `prompted_tools` (mejor esfuerzo — ver ese módulo).
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
from .output_safety import VISIBLE_OUTPUT_CONTRACT_ES, sanitize_visible_assistant_text
from .prompted_tools import parse_tool_call, render_prompt

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 300
INSTALL_URL = "https://claude.com/claude-code"
_AUTH_HINTS = ("login", "auth", "api key")


class ClaudeCLIProvider(LLMProvider):
    """Proveedor que ejecuta el binario `claude` como subproceso local."""

    name = "claude_cli"

    def __init__(
        self,
        binary_path: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        resolved = binary_path or shutil.which("claude")
        if not resolved:
            raise CLINotInstalledError(
                "Claude CLI no está instalado o no se encuentra en el PATH. "
                f"Instálalo desde {INSTALL_URL} y vuelve a intentar.",
                provider=self.name,
            )
        self._binary_path = resolved
        self._timeout_seconds = timeout_seconds

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        with TemporaryDirectory(prefix="edecan-claude-images-") as image_dir:
            image_paths = materialize_request_images(req, image_dir)
            args = self._base_args("json", image_dir=image_dir if image_paths else None)
            if req.model:
                args += ["--model", req.model]
            stdout, _stderr = await self._run(
                args, _render_cli_prompt(req, image_paths=image_paths)
            )
        return _parse_response(stdout, req)

    async def stream(self, req: CompletionRequest) -> AsyncIterator[StreamChunk]:
        """Intenta `--output-format stream-json`; si no se reconoce el
        formato de salida (versión distinta del CLI, u otro motivo), degrada
        a una sola respuesta completa (`complete()`) emitida como un único
        chunk `text` + `usage` + `stop` — documentado en el punto 3 del
        paquete WP-V3-03.
        """
        with TemporaryDirectory(prefix="edecan-claude-images-") as image_dir:
            image_paths = materialize_request_images(req, image_dir)
            args = self._base_args(
                "stream-json", verbose=True, image_dir=image_dir if image_paths else None
            )
            if req.model:
                args += ["--model", req.model]
            stdout, _stderr = await self._run(
                args, _render_cli_prompt(req, image_paths=image_paths)
            )

        chunks = _parse_stream_json(stdout, tools_requested=bool(req.tools))
        if chunks is None:
            logger.info(
                "Claude CLI: --output-format stream-json no reconocido en esta "
                "versión del binario; degradando a una única respuesta (complete())."
            )
            response = _parse_response(stdout, req)
            chunks = _response_to_chunks(response)
        for chunk in chunks:
            yield chunk

    def _base_args(
        self, output_format: str, *, verbose: bool = False, image_dir: str | None = None
    ) -> list[str]:
        args = [self._binary_path, "-p", "--output-format", output_format]
        if verbose:
            args.append("--verbose")
        if image_dir:
            # Solo Read y solo sobre el directorio temporal de este turno.
            # Claude ve la imagen sin recibir acceso general al computador.
            args += [
                "--tools",
                "Read",
                "--allowedTools",
                "Read",
                "--permission-mode",
                "dontAsk",
                "--add-dir",
                image_dir,
            ]
        return args

    async def _run(self, args: list[str], prompt: str) -> tuple[str, str]:
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise CLINotInstalledError(
                f"Claude CLI no está instalado en {self._binary_path!r}. "
                f"Instálalo desde {INSTALL_URL}.",
                provider=self.name,
            ) from exc

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(prompt.encode("utf-8")), timeout=self._timeout_seconds
            )
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise LLMError(
                f"Claude CLI no respondió en {self._timeout_seconds}s. Si tu "
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
                    "Claude CLI no está autenticado: corre `claude login` en una "
                    "terminal y vuelve a intentar.",
                    provider=self.name,
                )
            raise LLMError(
                f"Claude CLI terminó con código {process.returncode}: "
                f"{stderr.strip() or stdout.strip()}",
                provider=self.name,
            )
        return stdout, stderr


def _response_to_chunks(response: CompletionResponse) -> list[StreamChunk]:
    chunks: list[StreamChunk] = []
    if response.tool_calls:
        chunks.extend(StreamChunk(type="tool_call", tool_call=call) for call in response.tool_calls)
    elif response.text:
        chunks.append(StreamChunk(type="text", text=response.text))
    chunks.append(StreamChunk(type="usage", usage=response.usage))
    chunks.append(StreamChunk(type="stop"))
    return chunks


def _render_cli_prompt(req: CompletionRequest, *, image_paths: list[str] | None = None) -> str:
    image_context = cli_image_context(image_paths or [])
    pieces = [VISIBLE_OUTPUT_CONTRACT_ES, render_prompt(req), image_context]
    return "\n\n".join(piece for piece in pieces if piece)


def _parse_response(stdout: str, req: CompletionRequest) -> CompletionResponse:
    """Parsea la salida de `--output-format json`: el campo `result` trae el
    texto final (shape documentado del CLI). Si el `stdout` no es JSON (o no
    trae `result`), usa el `stdout` crudo como texto — fallback tolerante a
    versiones distintas del binario.
    """
    data = _safe_json_object(stdout)
    if data is not None and "result" in data:
        text = str(data.get("result") or "")
        usage_data = data.get("usage") or {}
        usage = Usage(
            input_tokens=int(usage_data.get("input_tokens") or 0),
            output_tokens=int(usage_data.get("output_tokens") or 0),
        )
    else:
        text = stdout.strip()
        usage = Usage()

    text = sanitize_visible_assistant_text(text)

    if req.tools:
        tool_call = parse_tool_call(text)
        if tool_call is not None:
            return CompletionResponse(
                text="", tool_calls=[tool_call], usage=usage, stop_reason="tool_use"
            )
    return CompletionResponse(text=text, tool_calls=[], usage=usage, stop_reason="end")


def _safe_json_object(raw: str) -> dict | None:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _parse_stream_json(stdout: str, *, tools_requested: bool) -> list[StreamChunk] | None:
    """Traduce las líneas JSON de `--output-format stream-json --verbose` a
    `StreamChunk`. Devuelve `None` (nunca una lista vacía) si ninguna línea
    se pudo interpretar con una forma reconocida — es la señal para que
    `stream()` degrade a `complete()` en vez de devolver una respuesta vacía
    en silencio.
    """
    text_chunks: list[StreamChunk] = []
    usage: Usage | None = None
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

        event_type = event.get("type")
        if event_type == "assistant":
            message = event.get("message") or {}
            for block in message.get("content") or []:
                if block.get("type") == "text" and block.get("text"):
                    text_chunks.append(StreamChunk(type="text", text=block["text"]))
                    recognized = True
        elif event_type == "result":
            recognized = True
            usage_data = event.get("usage") or {}
            if usage_data:
                usage = Usage(
                    input_tokens=int(usage_data.get("input_tokens") or 0),
                    output_tokens=int(usage_data.get("output_tokens") or 0),
                )
            if not text_chunks and event.get("result"):
                text_chunks.append(StreamChunk(type="text", text=str(event["result"])))

    if not recognized:
        return None

    full_text = "".join(c.text or "" for c in text_chunks)
    visible_text = sanitize_visible_assistant_text(full_text)
    if visible_text != full_text:
        text_chunks = [StreamChunk(type="text", text=visible_text)]
        full_text = visible_text
    tool_call = parse_tool_call(full_text) if tools_requested and full_text else None
    if tool_call is not None:
        chunks: list[StreamChunk] = [StreamChunk(type="tool_call", tool_call=tool_call)]
    else:
        chunks = text_chunks
    chunks.append(StreamChunk(type="usage", usage=usage or Usage()))
    chunks.append(StreamChunk(type="stop"))
    return chunks
