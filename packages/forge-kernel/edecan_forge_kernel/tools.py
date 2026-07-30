"""Definición y ejecutor del ABI de Tools de Edecán Forge (Fase D).

Suministra las herramientas nativas que consumen los modelos LLM (GLM 5.2 / Kimi 2.7 Code):
1. `read_file_window`: Lectura paginada de archivos con soporte para desplazamiento.
2. `apply_text_edit`: Edición de texto anclada resistente a desplazamientos.
3. `run_command_exec_window`: Ejecución de comandos en disco real aislados vía `ExecWindow` VFS.
4. `write_file`: Creación y sobrescritura de archivos a través del VFS.
5. `list_dir`: Listado jerárquico de directorios del VFS.

Garantiza la inyección automática de esquemas JSON Schema para la llamadas a funciones y
la validación estricta de parámetros mediante Pydantic.
"""

from __future__ import annotations

import shlex
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from edecan_forge_kernel.vfs import ExecWindow, TextEdit, Vfs

# --------------------------------------------------------------------------- #
# Modelos de Invocación y Resultado de Herramientas
# --------------------------------------------------------------------------- #


class ToolCall(BaseModel, frozen=True):
    call_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel, frozen=True):
    call_id: str
    status: Literal["ok", "error"]
    output: str
    error_code: str | None = None
    duration_ms: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Esquemas Pydantic para los Parámetros de cada Tool
# --------------------------------------------------------------------------- #


class ReadFileWindowArgs(BaseModel):
    path: str = Field(description="Ruta relativa del archivo en el workspace VFS.")
    start_line: int = Field(default=1, description="Línea inicial (1-indexed).")
    max_lines: int = Field(default=500, description="Número máximo de líneas a leer.")


class ApplyTextEditArgs(BaseModel):
    path: str = Field(description="Ruta relativa del archivo a modificar.")
    target_content: str = Field(description="Texto exacto o bloque a reemplazar.")
    replacement_content: str = Field(description="Texto de reemplazo.")
    start_line_hint: int | None = Field(
        default=None, description="Línea aproximada donde se ubica el cambio."
    )


class WriteFileArgs(BaseModel):
    path: str = Field(description="Ruta relativa del archivo en el workspace VFS.")
    content: str = Field(description="Contenido completo en texto a escribir.")
    encoding: str = Field(default="utf-8", description="Codificación del texto.")


class ListDirArgs(BaseModel):
    path: str = Field(default="", description="Ruta del directorio a listar (vacío para la raíz).")


class RunCommandArgs(BaseModel):
    command: str = Field(description="Comando de consola a ejecutar.")
    timeout_seconds: int = Field(default=30, description="Tiempo límite de ejecución en segundos.")


# --------------------------------------------------------------------------- #
# Registro y Dispatcher de Herramientas (Tool Registry)
# --------------------------------------------------------------------------- #


class ToolRegistry:
    """Registro centralizado de herramientas expuestas a la ventana de razonamiento de Forge."""

    def __init__(self, vfs: Vfs):
        self.vfs = vfs
        self._handlers: dict[str, Callable[[dict[str, Any]], ToolResult]] = {}
        self._schemas: dict[str, dict[str, Any]] = {}

        # Registrar herramientas por defecto
        self._register_defaults()

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """Devuelve los JSON Schemas de las herramientas registradas
        compatibles con OpenAI/GLM/Kimi.
        """
        return list(self._schemas.values())

    def dispatch(self, call: ToolCall) -> ToolResult:
        """Ejecuta una llamada a herramienta capturando errores y midiendo latencia."""
        handler = self._handlers.get(call.tool_name)
        if handler is None:
            return ToolResult(
                call_id=call.call_id,
                status="error",
                output=f"Herramienta desconocida: {call.tool_name!r}",
                error_code="TOOL_NOT_FOUND",
            )

        t0 = time.perf_counter()
        try:
            res = handler(call.arguments)
            duration_ms = (time.perf_counter() - t0) * 1000
            return res.model_copy(update={"duration_ms": duration_ms, "call_id": call.call_id})
        except Exception as err:
            duration_ms = (time.perf_counter() - t0) * 1000
            return ToolResult(
                call_id=call.call_id,
                status="error",
                output=f"Error ejecutando {call.tool_name}: {err}",
                error_code=type(err).__name__,
                duration_ms=duration_ms,
            )

    def register_custom_tool(
        self, name: str, description: str, handler: Callable[[dict[str, Any]], ToolResult]
    ) -> None:
        """Registra una herramienta personalizada dinámicamente."""
        self._schemas[name] = {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {"type": "object", "properties": {}},
            },
        }
        self._handlers[name] = handler

    def get_mcp_tools_schema(self) -> list[dict[str, Any]]:
        """Devuelve los esquemas de herramientas formateados para MCP."""
        mcp_tools = []
        for s in self._schemas.values():
            fn = s.get("function", {})
            mcp_tools.append(
                {
                    "name": fn.get("name"),
                    "description": fn.get("description"),
                    "inputSchema": fn.get("parameters"),
                }
            )
        return mcp_tools

    def _register_defaults(self) -> None:
        # 1. read_file_window
        self._schemas["read_file_window"] = {
            "type": "function",
            "function": {
                "name": "read_file_window",
                "description": "Lee una ventana de líneas de un archivo de texto en el VFS.",
                "parameters": ReadFileWindowArgs.model_json_schema(),
            },
        }
        self._handlers["read_file_window"] = self._handle_read_file_window

        # 2. apply_text_edit
        self._schemas["apply_text_edit"] = {
            "type": "function",
            "function": {
                "name": "apply_text_edit",
                "description": "Aplica una edición de texto anclada resistente a desplazamientos.",
                "parameters": ApplyTextEditArgs.model_json_schema(),
            },
        }
        self._handlers["apply_text_edit"] = self._handle_apply_text_edit

        # 3. write_file
        self._schemas["write_file"] = {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Crea o reemplaza completamente un archivo de texto en el VFS.",
                "parameters": WriteFileArgs.model_json_schema(),
            },
        }
        self._handlers["write_file"] = self._handle_write_file

        # 4. list_dir
        self._schemas["list_dir"] = {
            "type": "function",
            "function": {
                "name": "list_dir",
                "description": "Lista el contenido de un directorio del VFS.",
                "parameters": ListDirArgs.model_json_schema(),
            },
        }
        self._handlers["list_dir"] = self._handle_list_dir

        # 5. run_command
        self._schemas["run_command"] = {
            "type": "function",
            "function": {
                "name": "run_command",
                "description": "Ejecuta un comando en un directorio aislado mediante ExecWindow.",
                "parameters": RunCommandArgs.model_json_schema(),
            },
        }
        self._handlers["run_command"] = self._handle_run_command

    # ----------------------------------------------------------------------- #
    # Handlers Internos
    # ----------------------------------------------------------------------- #

    def _handle_read_file_window(self, raw_args: dict[str, Any]) -> ToolResult:
        args = ReadFileWindowArgs.model_validate(raw_args)
        full_text = self.vfs.read_text(args.path)
        lines = full_text.splitlines()
        start_idx = max(0, args.start_line - 1)
        end_idx = start_idx + args.max_lines
        selected_lines = lines[start_idx:end_idx]

        output = "\n".join(
            f"{start_idx + i + 1:4d} | {line}" for i, line in enumerate(selected_lines)
        )
        return ToolResult(
            call_id="",
            status="ok",
            output=output,
            metadata={"total_lines": len(lines), "shown_lines": len(selected_lines)},
        )

    def _handle_apply_text_edit(self, raw_args: dict[str, Any]) -> ToolResult:
        args = ApplyTextEditArgs.model_validate(raw_args)
        txn = self.vfs.begin_txn()
        edit = TextEdit(
            target_content=args.target_content,
            replacement_content=args.replacement_content,
            start_line_hint=args.start_line_hint,
        )
        txn.apply_text_edit(args.path, edit)
        txn.commit()
        return ToolResult(
            call_id="",
            status="ok",
            output=f"Edición de texto aplicada con éxito a {args.path!r}.",
        )

    def _handle_write_file(self, raw_args: dict[str, Any]) -> ToolResult:
        args = WriteFileArgs.model_validate(raw_args)
        txn = self.vfs.begin_txn()
        txn.write_text(args.path, args.content, encoding=args.encoding)
        txn.commit()
        return ToolResult(
            call_id="",
            status="ok",
            output=f"Archivo {args.path!r} escrito correctamente ({len(args.content)} caracteres).",
        )

    def _handle_list_dir(self, raw_args: dict[str, Any]) -> ToolResult:
        args = ListDirArgs.model_validate(raw_args)
        all_files = self.vfs.list_files()
        prefix = args.path.strip("/")

        matches: list[str] = []
        for path, entry in all_files:
            if not prefix or path.startswith(prefix + "/") or path == prefix:
                matches.append(
                    f"{path} ({entry.size_bytes} bytes, kind={entry.classification.kind})"
                )

        output = (
            "\n".join(matches)
            if matches
            else f"Directorio o prefijo {args.path!r} vacío o no encontrado."
        )
        return ToolResult(
            call_id="",
            status="ok",
            output=output,
            metadata={"match_count": len(matches)},
        )

    def _handle_run_command(self, raw_args: dict[str, Any]) -> ToolResult:
        args = RunCommandArgs.model_validate(raw_args)
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_exec_dir:
            target_dir = Path(tmp_exec_dir)
            window = ExecWindow(vfs=self.vfs, target_dir=target_dir)
            window.sync_vfs_to_disk()

            try:
                proc = subprocess.run(
                    shlex.split(args.command),
                    cwd=target_dir,
                    capture_output=True,
                    text=True,
                    timeout=args.timeout_seconds,
                )
                output = (
                    f"EXIT CODE: {proc.returncode}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
                )

                # Reconciliar cambios externos producidos por el comando
                window.reconcile_from_disk()

                status = "ok" if proc.returncode == 0 else "error"
                return ToolResult(
                    call_id="",
                    status=status,
                    output=output,
                    metadata={"exit_code": proc.returncode},
                )
            except subprocess.TimeoutExpired:
                return ToolResult(
                    call_id="",
                    status="error",
                    output=f"El comando excedió el tiempo límite de {args.timeout_seconds}s.",
                    error_code="TIMEOUT",
                )
