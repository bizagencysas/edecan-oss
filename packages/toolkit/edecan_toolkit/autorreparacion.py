"""Autorreparación local, aislada y explícitamente aprobada.

Este módulo NO convierte a Edecán en un proceso con acceso arbitrario al
sistema. Implementa la última etapa de una estrategia escalonada:

1. reutilizar/configurar una capacidad existente;
2. crear o actualizar una skill local (``edecan_skills``);
3. solo si el fallo pertenece al núcleo y la instalación conserva su clon
   Git, preparar una reparación de código en un *worktree* aislado.

``DiagnosticarAutorreparacionLocalTool`` es de solo lectura. La herramienta
``GestionarAutorreparacionLocalTool`` es ``dangerous=True``: cada transición
que crea un worktree, edita, ejecuta, instala, integra o revierte pasa por la
confirmación de tools existente. Los comandos nunca usan shell y deben
coincidir EXACTAMENTE con una entrada configurada por el dueño.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

from edecan_core import Tool, ToolContext, ToolResult
from edecan_core.safety import redact

_REPAIR_ID_RE = re.compile(r"^[a-f0-9]{12}$")
_MAX_EDITS = 24
_MAX_FILE_BYTES = 512 * 1024
_MAX_COMMAND_OUTPUT_CHARS = 12_000
_DEFAULT_COMMAND_TIMEOUT_SECONDS = 300

_DISABLED = (
    "La autorreparación de código está apagada. Solo el dueño puede activarla con "
    "EDECAN_SELF_REPAIR_ENABLED=true en una instancia local con un clon Git configurado."
)


def _repo_root(ctx: ToolContext) -> Path | None:
    if not getattr(ctx.settings, "EDECAN_LOCAL_MODE", False):
        return None
    raw = getattr(ctx.settings, "EDECAN_LOCAL_REPO_PATH", None)
    if not raw:
        return None
    root = Path(str(raw)).expanduser().resolve()
    return root if root.is_dir() and (root / ".git").exists() else None


def _enabled(ctx: ToolContext) -> bool:
    return bool(getattr(ctx.settings, "EDECAN_SELF_REPAIR_ENABLED", False))


def _state_root(ctx: ToolContext, repo: Path) -> Path:
    data_dir = Path(str(getattr(ctx.settings, "DATA_DIR", "~/.edecan/data"))).expanduser().resolve()
    root = data_dir / "self-repair"
    try:
        root.relative_to(repo)
    except ValueError:
        return root
    raise ValueError("DATA_DIR para autorreparación debe quedar fuera del repositorio.")


async def _run(argv: list[str], *, cwd: Path, timeout: int) -> tuple[int, str]:
    if not argv or not all(isinstance(part, str) and part for part in argv):
        return 2, "El comando debe ser una lista no vacía de argumentos de texto."
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        return 127, f"No se pudo iniciar {argv[0]!r}: {redact(str(exc))}"
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        return 124, f"Timeout después de {timeout}s; el proceso fue terminado."
    text = output.decode("utf-8", errors="replace")
    if len(text) > _MAX_COMMAND_OUTPUT_CHARS:
        text = text[:_MAX_COMMAND_OUTPUT_CHARS] + "\n... (salida truncada)"
    return process.returncode or 0, redact(text)


async def _git(repo: Path, *args: str, timeout: int = 60) -> tuple[int, str]:
    return await _run(["git", *args], cwd=repo, timeout=timeout)


async def _head(repo: Path) -> str | None:
    code, output = await _git(repo, "rev-parse", "HEAD")
    return output.strip() if code == 0 and output.strip() else None


async def _status(repo: Path) -> list[str]:
    code, output = await _git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    if code != 0:
        raise ValueError(f"No se pudo leer git status: {output}")
    return [line for line in output.splitlines() if line]


def _atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def _load_manifest(ctx: ToolContext, repo: Path, repair_id: str) -> tuple[Path, dict[str, Any]]:
    if not _REPAIR_ID_RE.fullmatch(repair_id):
        raise ValueError("repair_id inválido.")
    state_dir = _state_root(ctx, repo) / repair_id
    manifest_path = state_dir / "manifest.json"
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("No existe una reparación local con ese repair_id.") from exc
    if data.get("repair_id") != repair_id:
        raise ValueError("El manifiesto de reparación no coincide con repair_id.")
    return manifest_path, data


def _save_manifest(path: Path, data: dict[str, Any]) -> None:
    _atomic_json(path, data)


def _worktree(manifest: dict[str, Any]) -> Path:
    return Path(str(manifest["worktree"])).resolve()


def _resolve_worktree_path(worktree: Path, raw: str) -> Path:
    if not raw or raw.startswith(("/", "\\")):
        raise ValueError("Cada ruta debe ser relativa al repositorio.")
    candidate = (worktree / raw).resolve()
    try:
        relative = candidate.relative_to(worktree)
    except ValueError as exc:
        raise ValueError(f"Ruta fuera del worktree: {raw!r}.") from exc
    if ".git" in relative.parts:
        raise ValueError("No se puede editar metadata interna de Git.")
    return candidate


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_exact_commands(ctx: ToolContext, field: str) -> list[list[str]]:
    raw = getattr(ctx.settings, field, "[]")
    if isinstance(raw, list):
        parsed = raw
    else:
        try:
            parsed = json.loads(str(raw or "[]"))
        except ValueError:
            return []
    if not isinstance(parsed, list):
        return []
    return [
        list(item)
        for item in parsed
        if isinstance(item, list) and item and all(isinstance(part, str) and part for part in item)
    ]


def _command_from_args(args: dict[str, Any]) -> list[str] | None:
    raw = args.get("comando")
    if not isinstance(raw, list) or not raw or not all(isinstance(x, str) and x for x in raw):
        return None
    return list(raw)


async def _changed_paths(worktree: Path) -> list[str]:
    code, output = await _git(
        worktree,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    )
    if code != 0:
        raise ValueError(f"No se pudieron enumerar los cambios: {output}")
    paths: list[str] = []
    entries = output.split("\0")
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        status = entry[:2]
        path = entry[3:]
        if "R" in status or "C" in status:
            if index >= len(entries):
                raise ValueError("git status devolvió un rename incompleto.")
            path = entries[index]
            index += 1
        _resolve_worktree_path(worktree, path)
        paths.append(path)
    return sorted(set(paths))


async def _paths_between(repo: Path, baseline: str, head: str) -> list[str]:
    """Rutas cuyo árbol difiere entre dos commits, validadas dentro del repo."""
    code, output = await _git(repo, "diff", "--name-only", "-z", baseline, head, "--")
    if code != 0:
        raise ValueError(f"No se pudo calcular el delta completo a revertir: {output}")
    paths = sorted(set(path for path in output.split("\0") if path))
    for path in paths:
        _resolve_worktree_path(repo, path)
    return paths


async def _integrated_chain(manifest: dict[str, Any], repo: Path) -> tuple[str, list[str]]:
    """Checkpoint original y commits integrados, incluida compatibilidad con manifiestos v1."""
    raw_chain = manifest.get("integrated_commits")
    if isinstance(raw_chain, list):
        chain = [str(commit) for commit in raw_chain if commit]
    else:
        chain = [str(commit) for commit in manifest.get("previous_commits") or [] if commit]
        current = manifest.get("commit")
        if (
            manifest.get("status") in {"ready_to_retry", "completed"}
            and current
            and str(current) not in chain
        ):
            chain.append(str(current))
    if not chain:
        raise ValueError("El manifiesto no contiene commits integrados que revertir.")
    original = str(manifest.get("original_baseline") or "")
    if not original:
        code, output = await _git(repo, "rev-parse", f"{chain[0]}^")
        if code != 0 or not output.strip():
            raise ValueError("No se pudo reconstruir el checkpoint original.")
        original = output.strip()
    return original, chain


async def _branch_exists(repo: Path, branch: str) -> bool:
    if not branch:
        return False
    code, output = await _git(repo, "branch", "--list", branch)
    return code == 0 and any(line.strip().lstrip("* ") == branch for line in output.splitlines())


class DiagnosticarAutorreparacionLocalTool(Tool):
    """Inspección sin cambios; decide la vía menos invasiva antes de reparar."""

    name = "diagnosticar_autorreparacion_local"
    description = (
        "Diagnostica por qué una intención falló y elige la vía menos invasiva: primero "
        "configurar/reutilizar una capacidad, luego una skill local aislada y solo para un "
        "defecto del núcleo una reparación Git local. No modifica nada."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "intencion_original": {"type": "string"},
            "fallo_reportado": {"type": "string"},
            "categoria": {
                "type": "string",
                "enum": ["configuracion", "capacidad_faltante", "defecto_nucleo", "incierta"],
            },
            "repair_id": {
                "type": "string",
                "description": "Opcional: consulta el estado de una reparación existente.",
            },
        },
        "required": ["intencion_original", "fallo_reportado", "categoria"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        repo = _repo_root(ctx)
        repair_id = str(args.get("repair_id") or "").strip()
        if repair_id and repo is not None:
            try:
                _path, manifest = _load_manifest(ctx, repo, repair_id)
            except ValueError as exc:
                return ToolResult(content=str(exc))
            public = {
                key: manifest.get(key)
                for key in (
                    "repair_id",
                    "status",
                    "strategy",
                    "original_intent",
                    "failure",
                    "changed_paths",
                    "test_runs",
                    "commit",
                )
            }
            return ToolResult(content=json.dumps(public, ensure_ascii=False), data=public)

        category = str(args.get("categoria") or "incierta")
        route = {
            "configuracion": "configurar_capacidad_existente",
            "capacidad_faltante": "crear_o_actualizar_skill_local",
            "defecto_nucleo": "reparar_codigo_fuente",
            "incierta": "inspeccionar_capacidad_existente",
        }.get(category, "inspeccionar_capacidad_existente")

        status_lines: list[str] = []
        head = None
        if repo is not None:
            head = await _head(repo)
            try:
                status_lines = await _status(repo)
            except ValueError as exc:
                status_lines = [str(exc)]
        test_commands = _parse_exact_commands(ctx, "EDECAN_SELF_REPAIR_TEST_COMMANDS_JSON")
        install_commands = _parse_exact_commands(ctx, "EDECAN_SELF_REPAIR_INSTALL_COMMANDS_JSON")
        source_ready = bool(
            _enabled(ctx) and repo is not None and head and not status_lines and test_commands
        )
        data = {
            "route": route,
            "policy": [
                "configurar_o_reutilizar",
                "skill_local_aislada",
                "codigo_fuente_solo_si_es_defecto_del_nucleo",
            ],
            "source_repair_ready": source_ready,
            "repo_clean": repo is not None and not status_lines,
            "head": head,
            "blocking_status": status_lines[:20],
            "test_commands_configured": test_commands,
            "install_commands_configured": install_commands,
        }
        if route == "reparar_codigo_fuente" and not source_ready:
            summary = (
                "El fallo parece del núcleo, pero la reparación de código no está lista: "
                "se exige opt-in local, clon Git y estado limpio. No cambié nada."
            )
        elif route == "crear_o_actualizar_skill_local":
            summary = (
                "La vía recomendada es una skill local aislada y recargable. Solo escala al "
                "núcleo si una prueba demuestra que la skill no puede resolverlo."
            )
        else:
            summary = (
                "Primero reutiliza o configura la capacidad existente. No se justifica editar "
                "el núcleo todavía."
            )
        return ToolResult(content=summary, data=data)


class GestionarAutorreparacionLocalTool(Tool):
    """Máquina de estados de reparación Git en worktree aislado."""

    name = "gestionar_autorreparacion_local"
    description = (
        "Prepara, edita, prueba, instala, confirma o revierte una reparación del núcleo en "
        "un worktree Git aislado. Cada llamada requiere aprobación humana. No usa shell, no "
        "hace push y solo ejecuta comandos exactos configurados por el dueño."
    )
    dangerous = True
    input_schema = {
        "type": "object",
        "properties": {
            "accion": {
                "type": "string",
                "enum": [
                    "iniciar",
                    "aplicar_cambios",
                    "ejecutar_pruebas",
                    "instalar_dependencias",
                    "crear_commit",
                    "integrar",
                    "registrar_reintento",
                    "revertir",
                ],
            },
            "repair_id": {"type": "string"},
            "intencion_original": {"type": "string"},
            "fallo_reportado": {"type": "string"},
            "cambios": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "ruta": {"type": "string"},
                        "sha256_esperado": {
                            "type": "string",
                            "description": "SHA-256 actual o 'missing' para un archivo nuevo.",
                        },
                        "contenido": {"type": "string"},
                    },
                    "required": ["ruta", "sha256_esperado", "contenido"],
                },
            },
            "comando": {
                "type": "array",
                "items": {"type": "string"},
                "description": "argv exacto; nunca un string de shell.",
            },
            "directorio": {"type": "string", "default": "."},
            "mensaje": {"type": "string"},
            "rutas_esperadas": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Lista exacta de archivos que el commit debe contener. Si las pruebas "
                    "o una instalación cambiaron otro archivo, el commit se rechaza."
                ),
            },
            "reintento_exitoso": {"type": "boolean"},
            "evidencia": {
                "type": "string",
                "description": "Resumen sin secretos del resultado observado al reintentar.",
            },
        },
        "required": ["accion"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        if not _enabled(ctx):
            return ToolResult(content=_DISABLED)
        repo = _repo_root(ctx)
        if repo is None:
            return ToolResult(
                content="Se requiere EDECAN_LOCAL_MODE y EDECAN_LOCAL_REPO_PATH hacia un clon Git."
            )
        action = str(args.get("accion") or "").strip()
        try:
            if action == "iniciar":
                return await self._start(ctx, repo, args)
            repair_id = str(args.get("repair_id") or "").strip()
            manifest_path, manifest = _load_manifest(ctx, repo, repair_id)
            handlers = {
                "aplicar_cambios": self._apply,
                "ejecutar_pruebas": self._test,
                "instalar_dependencias": self._install,
                "crear_commit": self._commit,
                "integrar": self._integrate,
                "registrar_reintento": self._register_retry,
                "revertir": self._rollback,
            }
            handler = handlers.get(action)
            if handler is None:
                return ToolResult(content=f"Acción de reparación desconocida: {action!r}.")
            return await handler(ctx, repo, manifest_path, manifest, args)
        except ValueError as exc:
            return ToolResult(content=f"No se realizó la acción: {redact(str(exc))}")

    async def _start(self, ctx: ToolContext, repo: Path, args: dict[str, Any]) -> ToolResult:
        original_intent = str(args.get("intencion_original") or "").strip()
        failure = str(args.get("fallo_reportado") or "").strip()
        if not original_intent or not failure:
            raise ValueError("iniciar requiere la intención original y el fallo reportado.")
        dirty = await _status(repo)
        if dirty:
            raise ValueError(
                "El repositorio tiene cambios previos. Guárdalos o usa otro clon antes de "
                f"autorreparar; no tocaré: {dirty[:20]}"
            )
        baseline = await _head(repo)
        if baseline is None:
            raise ValueError("El repositorio todavía no tiene un commit base.")
        state_root = _state_root(ctx, repo)
        repair_id = uuid4().hex[:12]
        state_dir = state_root / repair_id
        worktree = state_dir / "worktree"
        branch = f"edecan/repair-{repair_id}"
        state_dir.mkdir(parents=True, exist_ok=False)
        code, output = await _git(
            repo,
            "worktree",
            "add",
            "--detach",
            str(worktree),
            baseline,
            timeout=120,
        )
        if code != 0:
            state_dir.rmdir()
            raise ValueError(f"No se pudo crear el worktree aislado: {output}")
        code, output = await _git(worktree, "switch", "--create", branch)
        if code != 0:
            await _git(repo, "worktree", "remove", "--force", str(worktree))
            raise ValueError(f"No se pudo crear la rama local de reparación: {output}")
        manifest = {
            "repair_id": repair_id,
            "status": "prepared",
            "strategy": "core_source",
            "original_intent": redact(original_intent)[:4000],
            "failure": redact(failure)[:4000],
            "repo": str(repo),
            "baseline": baseline,
            "original_baseline": baseline,
            "integrated_commits": [],
            "branch": branch,
            "worktree": str(worktree),
            "changed_paths": [],
            "test_runs": [],
            "install_runs": [],
            "commit": None,
        }
        manifest_path = state_dir / "manifest.json"
        _save_manifest(manifest_path, manifest)
        return ToolResult(
            content=(
                f"Preparé la reparación {repair_id} en un worktree aislado. El clon principal "
                "sigue intacto. Ahora hay que proponer cambios con hashes previos."
            ),
            data={"repair_id": repair_id, "status": "prepared", "branch": branch},
        )

    async def _apply(
        self,
        _ctx: ToolContext,
        _repo: Path,
        manifest_path: Path,
        manifest: dict[str, Any],
        args: dict[str, Any],
    ) -> ToolResult:
        if manifest.get("status") not in {"prepared", "modified", "tests_failed"}:
            raise ValueError(
                "Solo se puede editar una reparación preparada o con pruebas fallidas."
            )
        changes = args.get("cambios")
        if not isinstance(changes, list) or not 0 < len(changes) <= _MAX_EDITS:
            raise ValueError(f"cambios debe tener entre 1 y {_MAX_EDITS} archivos.")
        worktree = _worktree(manifest)
        prepared: list[tuple[Path, str, str]] = []
        for change in changes:
            if not isinstance(change, dict):
                raise ValueError("Cada cambio debe ser un objeto.")
            raw_path = str(change.get("ruta") or "")
            target = _resolve_worktree_path(worktree, raw_path)
            if target.exists() and (target.is_symlink() or not target.is_file()):
                raise ValueError(f"No se puede reemplazar {raw_path!r}: no es un archivo regular.")
            expected = str(change.get("sha256_esperado") or "")
            actual = _sha256(target) if target.exists() else "missing"
            if expected != actual:
                raise ValueError(
                    f"{raw_path!r} cambió desde el diagnóstico "
                    f"(esperado {expected}, actual {actual})."
                )
            content = change.get("contenido")
            if not isinstance(content, str):
                raise ValueError(f"{raw_path!r} no trae contenido de texto.")
            if len(content.encode("utf-8")) > _MAX_FILE_BYTES:
                raise ValueError(f"{raw_path!r} supera {_MAX_FILE_BYTES} bytes.")
            prepared.append((target, raw_path, content))
        for target, _raw_path, content in prepared:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
            tmp = Path(tmp_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(content)
                tmp.replace(target)
            finally:
                tmp.unlink(missing_ok=True)
        manifest["changed_paths"] = await _changed_paths(worktree)
        manifest["status"] = "modified"
        _save_manifest(manifest_path, manifest)
        return ToolResult(
            content=(
                f"Apliqué {len(prepared)} cambio(s) solo en el worktree. Aún no hay commit ni "
                "integración; ahora deben pasar las pruebas aprobadas."
            ),
            data={"repair_id": manifest["repair_id"], "changed_paths": manifest["changed_paths"]},
        )

    async def _execute_configured(
        self,
        ctx: ToolContext,
        manifest_path: Path,
        manifest: dict[str, Any],
        args: dict[str, Any],
        *,
        config_field: str,
        runs_field: str,
    ) -> tuple[int, str, list[str]]:
        command = _command_from_args(args)
        if command is None:
            raise ValueError("comando debe ser un argv no vacío, no texto de shell.")
        allowed = _parse_exact_commands(ctx, config_field)
        if command not in allowed:
            raise ValueError(
                f"El comando exacto no está autorizado en {config_field}; no se ejecutó nada."
            )
        worktree = _worktree(manifest)
        cwd = _resolve_worktree_path(worktree, str(args.get("directorio") or "."))
        if not cwd.is_dir():
            raise ValueError("directorio no existe dentro del worktree.")
        timeout = int(
            getattr(
                ctx.settings,
                "EDECAN_SELF_REPAIR_COMMAND_TIMEOUT_SECONDS",
                _DEFAULT_COMMAND_TIMEOUT_SECONDS,
            )
        )
        timeout = max(1, min(timeout, 1800))
        code, output = await _run(command, cwd=cwd, timeout=timeout)
        run = {"command": command, "directory": str(cwd.relative_to(worktree)), "code": code}
        manifest.setdefault(runs_field, []).append(run)
        manifest["changed_paths"] = await _changed_paths(worktree)
        _save_manifest(manifest_path, manifest)
        return code, output, command

    async def _test(
        self,
        ctx: ToolContext,
        _repo: Path,
        manifest_path: Path,
        manifest: dict[str, Any],
        args: dict[str, Any],
    ) -> ToolResult:
        if manifest.get("status") not in {"modified", "tests_failed"}:
            raise ValueError("Primero debe existir un cambio no integrado que probar.")
        code, output, command = await self._execute_configured(
            ctx,
            manifest_path,
            manifest,
            args,
            config_field="EDECAN_SELF_REPAIR_TEST_COMMANDS_JSON",
            runs_field="test_runs",
        )
        manifest["status"] = "tests_passed" if code == 0 else "tests_failed"
        _save_manifest(manifest_path, manifest)
        return ToolResult(
            content=(
                f"Pruebas {'aprobadas' if code == 0 else 'fallidas'} ({code}) con {command!r}.\n"
                f"{output}"
            ),
            data={"repair_id": manifest["repair_id"], "code": code, "status": manifest["status"]},
        )

    async def _install(
        self,
        ctx: ToolContext,
        _repo: Path,
        manifest_path: Path,
        manifest: dict[str, Any],
        args: dict[str, Any],
    ) -> ToolResult:
        if manifest.get("status") not in {"prepared", "modified", "tests_failed"}:
            raise ValueError("No se pueden instalar dependencias en este estado.")
        code, output, command = await self._execute_configured(
            ctx,
            manifest_path,
            manifest,
            args,
            config_field="EDECAN_SELF_REPAIR_INSTALL_COMMANDS_JSON",
            runs_field="install_runs",
        )
        manifest["status"] = "modified" if code == 0 else "tests_failed"
        _save_manifest(manifest_path, manifest)
        return ToolResult(
            content=(
                f"Instalación {'completada' if code == 0 else 'fallida'} ({code}) con {command!r}. "
                "Aunque haya funcionado, hay que ejecutar pruebas antes del commit.\n"
                f"{output}"
            ),
            data={"repair_id": manifest["repair_id"], "code": code, "status": manifest["status"]},
        )

    async def _commit(
        self,
        _ctx: ToolContext,
        _repo: Path,
        manifest_path: Path,
        manifest: dict[str, Any],
        args: dict[str, Any],
    ) -> ToolResult:
        if manifest.get("status") != "tests_passed":
            raise ValueError("No se puede crear el commit hasta que las pruebas pasen.")
        message = str(args.get("mensaje") or "").strip()
        if not message:
            raise ValueError("crear_commit requiere un mensaje.")
        worktree = _worktree(manifest)
        paths = await _changed_paths(worktree)
        if not paths:
            raise ValueError("No hay cambios para guardar.")
        expected_paths_raw = args.get("rutas_esperadas")
        if (
            not isinstance(expected_paths_raw, list)
            or not expected_paths_raw
            or not all(isinstance(path, str) and path for path in expected_paths_raw)
        ):
            raise ValueError("crear_commit requiere rutas_esperadas explícitas.")
        expected_paths = sorted(set(expected_paths_raw))
        for path in expected_paths:
            _resolve_worktree_path(worktree, path)
        if expected_paths != paths:
            raise ValueError(
                "Los cambios reales no coinciden con rutas_esperadas; revisa antes de aprobar. "
                f"Esperadas={expected_paths}, reales={paths}."
            )
        code, output = await _git(worktree, "add", "--", *paths)
        if code != 0:
            raise ValueError(f"No se pudieron preparar los archivos exactos: {output}")
        code, output = await _git(worktree, "commit", "--message", message, timeout=120)
        if code != 0:
            await _git(worktree, "reset", "--", *paths)
            raise ValueError(f"No se pudo crear el commit local: {output}")
        commit = await _head(worktree)
        manifest["commit"] = commit
        manifest["status"] = "committed"
        manifest["changed_paths"] = paths
        _save_manifest(manifest_path, manifest)
        return ToolResult(
            content=(
                f"Creé el commit local {commit} en {manifest['branch']}. No hice push ni toqué "
                "todavía la rama principal."
            ),
            data={"repair_id": manifest["repair_id"], "commit": commit, "status": "committed"},
        )

    async def _integrate(
        self,
        _ctx: ToolContext,
        repo: Path,
        manifest_path: Path,
        manifest: dict[str, Any],
        _args: dict[str, Any],
    ) -> ToolResult:
        if manifest.get("status") != "committed" or not manifest.get("commit"):
            raise ValueError("Solo se integra una reparación probada y con commit local.")
        if await _status(repo):
            raise ValueError("El clon principal ya no está limpio; no se mezcló nada.")
        if await _head(repo) != manifest.get("baseline"):
            raise ValueError("La rama principal avanzó desde el checkpoint; vuelve a diagnosticar.")
        code, output = await _git(repo, "merge", "--ff-only", str(manifest["commit"]), timeout=120)
        if code != 0:
            raise ValueError(f"No se pudo integrar por fast-forward: {output}")
        manifest.setdefault("integrated_commits", []).append(manifest["commit"])
        manifest["status"] = "ready_to_retry"
        _save_manifest(manifest_path, manifest)
        return ToolResult(
            content=(
                "La reparación quedó integrada localmente y sigue sin push. Reintenta ahora la "
                f"intención original: «{manifest['original_intent']}». Si vuelve a fallar, "
                "conserva "
                f"repair_id={manifest['repair_id']} para continuar o revertir."
            ),
            data={
                "repair_id": manifest["repair_id"],
                "status": "ready_to_retry",
                "retry_intent": manifest["original_intent"],
            },
        )

    async def _rollback(
        self,
        _ctx: ToolContext,
        repo: Path,
        manifest_path: Path,
        manifest: dict[str, Any],
        _args: dict[str, Any],
    ) -> ToolResult:
        status = manifest.get("status")
        worktree = _worktree(manifest)
        if status == "reverted":
            return ToolResult(content="La reparación ya estaba revertida.")
        has_integrated_commits = bool(
            manifest.get("integrated_commits") or manifest.get("previous_commits")
        )
        if status in {"ready_to_retry", "completed"} or has_integrated_commits:
            if await _status(repo):
                raise ValueError(
                    "El clon principal tiene cambios; no se puede revertir con seguridad."
                )
            head = await _head(repo)
            original, chain = await _integrated_chain(manifest, repo)
            if head != chain[-1]:
                raise ValueError(
                    "HEAD ya no es el último commit integrado de esta reparación; "
                    "no se revirtió nada."
                )
            code, output = await _git(repo, "rev-list", "--reverse", f"{original}..{head}")
            actual_chain = output.splitlines() if code == 0 else []
            if actual_chain != chain:
                raise ValueError(
                    "Hay commits ajenos o falta un commit en la cadena desde el checkpoint; "
                    "no se revirtió nada."
                )
            paths = await _paths_between(repo, original, str(head))
            if paths:
                code, output = await _git(
                    repo,
                    "restore",
                    "--source",
                    original,
                    "--staged",
                    "--worktree",
                    "--",
                    *paths,
                    timeout=120,
                )
                if code != 0:
                    raise ValueError(f"No se pudo restaurar el checkpoint original: {output}")
                code, output = await _git(
                    repo,
                    "commit",
                    "--message",
                    f"revert: self-repair {manifest['repair_id']}",
                    timeout=120,
                )
                if code != 0:
                    # HEAD aún es el último commit reparador. Deshace únicamente
                    # el restore que esta operación acaba de preparar.
                    await _git(
                        repo,
                        "restore",
                        "--source",
                        str(head),
                        "--staged",
                        "--worktree",
                        "--",
                        *paths,
                    )
                    raise ValueError(f"No se pudo guardar la reversión completa: {output}")
            manifest["revert_commit"] = await _head(repo)
            manifest["reverted_commits"] = chain
            branch = str(manifest.get("branch") or "")
            if worktree.exists():
                code, output = await _git(repo, "worktree", "remove", "--force", str(worktree))
                if code != 0:
                    manifest["status"] = "reverted_cleanup_pending"
                    _save_manifest(manifest_path, manifest)
                    return ToolResult(
                        content=(
                            "La cadena completa quedó revertida, pero no pude cerrar el "
                            f"worktree temporal: {output}"
                        ),
                        data={
                            "repair_id": manifest["repair_id"],
                            "status": "reverted_cleanup_pending",
                        },
                    )
            if await _branch_exists(repo, branch):
                code, output = await _git(repo, "branch", "--delete", branch)
                if code != 0:
                    manifest["status"] = "reverted_cleanup_pending"
                    _save_manifest(manifest_path, manifest)
                    return ToolResult(
                        content=(
                            "La cadena completa quedó revertida, pero no pude borrar la "
                            f"rama temporal: {output}"
                        ),
                        data={
                            "repair_id": manifest["repair_id"],
                            "status": "reverted_cleanup_pending",
                        },
                    )
        else:
            if worktree.exists():
                code, output = await _git(repo, "worktree", "remove", "--force", str(worktree))
                if code != 0:
                    raise ValueError(f"No se pudo descartar el worktree aislado: {output}")
            branch = str(manifest.get("branch") or "")
            if await _branch_exists(repo, branch):
                await _git(repo, "branch", "--delete", "--force", branch)
        manifest["status"] = "reverted"
        _save_manifest(manifest_path, manifest)
        return ToolResult(
            content="La reparación quedó revertida localmente; nunca se hizo push.",
            data={"repair_id": manifest["repair_id"], "status": "reverted"},
        )

    async def _register_retry(
        self,
        _ctx: ToolContext,
        repo: Path,
        manifest_path: Path,
        manifest: dict[str, Any],
        args: dict[str, Any],
    ) -> ToolResult:
        """Cierra el worktree si funcionó, o abre otro ciclo si volvió a fallar."""
        if manifest.get("status") != "ready_to_retry":
            raise ValueError("Primero integra una reparación y reintenta la intención original.")
        success = args.get("reintento_exitoso")
        if not isinstance(success, bool):
            raise ValueError("Indica reintento_exitoso=true/false según el resultado observado.")
        evidence = redact(str(args.get("evidencia") or ""))[:2000]
        manifest.setdefault("retry_runs", []).append({"success": success, "evidence": evidence})
        worktree = _worktree(manifest)
        if not success:
            if await _status(repo) or await _head(repo) != manifest.get("commit"):
                raise ValueError(
                    "El clon principal cambió después de integrar; no se puede continuar "
                    "el mismo ciclo de forma segura."
                )
            manifest.setdefault("previous_commits", []).append(manifest["commit"])
            manifest["baseline"] = manifest["commit"]
            manifest["commit"] = None
            manifest["changed_paths"] = []
            manifest["status"] = "prepared"
            _save_manifest(manifest_path, manifest)
            return ToolResult(
                content=(
                    "Registré que el reintento falló. El worktree aislado sigue disponible "
                    "para diagnosticar otro cambio; vuelve a aplicar, probar, commitear e integrar."
                ),
                data={"repair_id": manifest["repair_id"], "status": "prepared"},
            )
        if await _status(repo) or await _head(repo) != manifest.get("commit"):
            raise ValueError(
                "El clon principal cambió después de integrar; no cerraré el checkpoint."
            )
        if worktree.exists():
            code, output = await _git(repo, "worktree", "remove", str(worktree))
            if code != 0:
                raise ValueError(f"No se pudo cerrar el worktree limpio: {output}")
        branch = str(manifest.get("branch") or "")
        if await _branch_exists(repo, branch):
            code, output = await _git(repo, "branch", "--delete", branch)
            if code != 0:
                raise ValueError(f"No se pudo cerrar la rama local ya integrada: {output}")
        manifest["status"] = "completed"
        _save_manifest(manifest_path, manifest)
        return ToolResult(
            content=(
                "Reintento verificado: cerré el worktree y la rama temporal. El commit local "
                "integrado permanece; no se hizo push."
            ),
            data={"repair_id": manifest["repair_id"], "status": "completed"},
        )
