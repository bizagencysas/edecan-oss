"""Auditoría de seguridad local y adaptador autorizado para PentestGPT.

La auditoría estática es deliberadamente de solo lectura y nunca devuelve el
contenido que parezca un secreto. La ejecución activa de PentestGPT está
separada porque toca un objetivo real: requiere modo local, la confirmación
normal de herramientas peligrosas y una declaración de autorización cuyo
alcance debe coincidir exactamente con el objetivo.

PentestGPT no se descarga ni se actualiza automáticamente. El dueño instala y
fija la versión que desea; Edecán únicamente detecta el binario configurado y
lo ejecuta sin shell, con telemetría desactivada.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from edecan_core import Tool, ToolContext, ToolResult

_MAX_FILES = 20_000
_MAX_FILE_BYTES = 1_000_000
_MAX_FINDINGS = 250
_MAX_REPORT_CHARS = 250_000
_DEFAULT_TIMEOUT_SECONDS = 3_600
_MIN_TIMEOUT_SECONDS = 60
_MAX_TIMEOUT_SECONDS = 14_400
_TEXT_SUFFIXES = frozenset(
    {
        ".c",
        ".cc",
        ".conf",
        ".cpp",
        ".cs",
        ".css",
        ".env",
        ".go",
        ".h",
        ".html",
        ".ini",
        ".java",
        ".js",
        ".json",
        ".jsx",
        ".kt",
        ".kts",
        ".md",
        ".mjs",
        ".php",
        ".plist",
        ".properties",
        ".py",
        ".rb",
        ".rs",
        ".sh",
        ".sql",
        ".swift",
        ".toml",
        ".ts",
        ".tsx",
        ".xml",
        ".yaml",
        ".yml",
    }
)
_IGNORED_PARTS = frozenset(
    {
        ".git",
        ".gradle",
        ".idea",
        ".next",
        ".pytest_cache",
        ".venv",
        "build",
        "coverage",
        "dist",
        "node_modules",
        "target",
        "vendor",
    }
)


@dataclass(frozen=True)
class _Finding:
    severity: str
    rule: str
    path: str
    line: int | None
    message: str


_CONTENT_RULES: tuple[tuple[str, str, re.Pattern[str], str], ...] = (
    (
        "critical",
        "private-key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        "Parece contener una clave privada. Retírala del historial y rótala.",
    ),
    (
        "critical",
        "hardcoded-secret",
        re.compile(
            r"(?i)(?:api[_-]?key|access[_-]?token|client[_-]?secret|password|secret)"
            r"\s*[:=]\s*[\"'][A-Za-z0-9_./+\-=]{16,}[\"']"
        ),
        "Parece contener una credencial fija. Muévela al vault y rótala.",
    ),
    (
        "high",
        "tls-verification-disabled",
        re.compile(r"\bverify\s*=\s*False\b"),
        "La verificación TLS parece desactivada.",
    ),
    (
        "high",
        "shell-injection-surface",
        re.compile(r"\b(?:subprocess\.(?:run|Popen|call)|os\.system)\s*\([^\n]*shell\s*=\s*True"),
        "Se ejecuta un shell explícito. Prefiere argv exactos y validación estricta.",
    ),
    (
        "high",
        "unsafe-eval",
        re.compile(r"(?<![A-Za-z0-9_])eval\s*\("),
        "Uso de eval detectado. Sustitúyelo por un parser seguro.",
    ),
    (
        "high",
        "jwt-signature-disabled",
        re.compile(r"verify_signature[\"']?\s*[:=]\s*False"),
        "La verificación de firma JWT parece desactivada.",
    ),
    (
        "medium",
        "cors-wildcard",
        re.compile(r"allow_origins\s*=\s*\[\s*[\"']\*[\"']\s*\]"),
        "CORS permite cualquier origen. Restringe los orígenes en producción.",
    ),
)


def _local_root(ctx: ToolContext) -> Path | None:
    if not getattr(ctx.settings, "EDECAN_LOCAL_MODE", False):
        return None
    raw = getattr(ctx.settings, "EDECAN_LOCAL_REPO_PATH", None)
    if not raw:
        return None
    root = Path(str(raw)).expanduser().resolve()
    return root if root.is_dir() else None


def _path_inside(root: Path, relative: str) -> Path | None:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate if candidate.is_dir() else None


def _iter_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for candidate in root.rglob("*"):
        if len(files) >= _MAX_FILES:
            break
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            continue
        if any(part in _IGNORED_PARTS for part in relative.parts):
            continue
        if candidate.is_symlink() or not candidate.is_file():
            continue
        files.append(candidate)
    return files


def _looks_textual(path: Path) -> bool:
    return path.suffix.lower() in _TEXT_SUFFIXES or path.name.startswith(".env")


def _scan_project(root: Path) -> tuple[list[_Finding], dict[str, int | bool]]:
    findings: list[_Finding] = []
    files = _iter_files(root)
    truncated = len(files) >= _MAX_FILES

    for path in files:
        if len(findings) >= _MAX_FINDINGS:
            truncated = True
            break
        relative = path.relative_to(root).as_posix()
        lower_name = path.name.lower()
        if lower_name in {".env", ".env.local", ".env.production", "id_rsa", "id_ed25519"} or (
            path.suffix.lower() in {".key", ".pem", ".p12", ".pfx"}
        ):
            findings.append(
                _Finding(
                    severity="high",
                    rule="sensitive-file",
                    path=relative,
                    line=None,
                    message=(
                        "Archivo sensible dentro del proyecto. Verifica que no esté versionado."
                    ),
                )
            )
        if not _looks_textual(path):
            continue
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for severity, rule, pattern, message in _CONTENT_RULES:
            match = pattern.search(content)
            if match is None:
                continue
            findings.append(
                _Finding(
                    severity=severity,
                    rule=rule,
                    path=relative,
                    line=content.count("\n", 0, match.start()) + 1,
                    message=message,
                )
            )
            if len(findings) >= _MAX_FINDINGS:
                truncated = True
                break

    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    return findings, {
        "files_scanned": len(files),
        "findings": len(findings),
        "critical": counts["critical"],
        "high": counts["high"],
        "medium": counts["medium"],
        "low": counts["low"],
        "truncated": truncated,
    }


def _normalize_target(raw: str) -> str | None:
    value = raw.strip()
    if not value or any(character.isspace() for character in value):
        return None
    parsed = urlsplit(value if "://" in value else f"https://{value}")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password or parsed.fragment:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    host = parsed.hostname.lower().rstrip(".")
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc += f":{port}"
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, netloc, path, parsed.query, ""))


def _pentest_binary(settings: Any) -> tuple[str, str] | None:
    configured = str(getattr(settings, "PENTESTGPT_BINARY", "") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            resolved = str(path.resolve())
            style = "agent" if "pentestgpt-agent" in path.name else "classic"
            return resolved, style
        return None
    maintained = shutil.which("pentestgpt-agent")
    if maintained:
        return maintained, "agent"
    classic = shutil.which("pentestgpt")
    return (classic, "classic") if classic else None


def _redact_output(value: str) -> str:
    value = re.sub(
        r"(?i)(api[_-]?key|token|password|secret)(\s*[:=]\s*)([^\s,;]+)",
        r"\1\2[REDACTADO]",
        value,
    )
    value = re.sub(
        r"-----BEGIN [^-]+PRIVATE KEY-----.*?-----END [^-]+PRIVATE KEY-----",
        "[CLAVE PRIVADA REDACTADA]",
        value,
        flags=re.DOTALL,
    )
    return value[:_MAX_REPORT_CHARS]


async def _run_pentestgpt(
    binary: str,
    *,
    command_style: str,
    target: str,
    instruction: str | None,
    backend: str,
    model: str | None,
    cwd: Path,
    timeout: int,
) -> tuple[int, str, bool]:
    if command_style == "agent":
        goal = (
            "Assess this explicitly authorized target for vulnerabilities and produce a "
            "defensive remediation report. Do not target third parties or establish persistence."
        )
        if instruction:
            goal = f"{goal} Additional authorized scope context: {instruction}"
        argv = [
            binary,
            "--goal",
            goal,
            "--target",
            target,
            "--backend",
            backend,
        ]
        if model:
            argv.extend(["--model", model])
    else:
        argv = [binary, "--target", target, "--mode", "pentest", "--no-telemetry"]
        if instruction:
            argv.extend(["--instruction", instruction])
    env = dict(os.environ)
    env["LANGFUSE_ENABLED"] = "false"
    kwargs: dict[str, Any] = {}
    if os.name != "nt":
        kwargs["start_new_session"] = True
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        **kwargs,
    )
    timed_out = False
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        timed_out = True
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout=10)
        except TimeoutError:
            process.kill()
            output, _ = await process.communicate()
    return (
        process.returncode or 0,
        _redact_output(output.decode("utf-8", errors="replace")),
        timed_out,
    )


class AuditarSeguridadProyectoTool(Tool):
    name = "auditar_seguridad_proyecto"
    description = (
        "Audita de forma estática y de solo lectura un proyecto local autorizado. Detecta "
        "credenciales versionadas y patrones inseguros sin revelar secretos; devuelve hallazgos "
        "estructurados que Edecán puede usar para proponer y verificar correcciones."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "ruta": {
                "type": "string",
                "description": (
                    "Subdirectorio dentro del repo local configurado; usa '.' por defecto."
                ),
            }
        },
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        root = _local_root(ctx)
        if root is None:
            return ToolResult(
                content=(
                    "La auditoría local requiere EDECAN_LOCAL_MODE y un proyecto seleccionado "
                    "en EDECAN_LOCAL_REPO_PATH."
                )
            )
        project = _path_inside(root, str(args.get("ruta", ".") or "."))
        if project is None:
            return ToolResult(
                content="La ruta solicitada no es un directorio dentro del proyecto autorizado."
            )
        findings, summary = await asyncio.to_thread(_scan_project, project)
        data = {
            "project": project.name,
            "summary": summary,
            "findings": [asdict(finding) for finding in findings],
        }
        if findings:
            content = (
                f"Auditoría terminada: {summary['files_scanned']} archivos revisados y "
                f"{summary['findings']} hallazgos ({summary['critical']} críticos, "
                f"{summary['high']} altos, {summary['medium']} medios). No se incluyó el "
                "contenido de ninguna credencial."
            )
        else:
            content = (
                f"Auditoría terminada: {summary['files_scanned']} archivos revisados sin "
                "hallazgos de estas reglas heurísticas. Esto no sustituye una revisión completa."
            )
        return ToolResult(content=content, data=data)


class EjecutarPentestGPTAutorizadoTool(Tool):
    name = "ejecutar_pentestgpt_autorizado"
    description = (
        "Ejecuta PentestGPT en modo pentest únicamente contra un objetivo cuyo dueño declaró "
        "autorizado. Solo funciona en la instalación local, exige confirmación explícita, "
        "coincidencia exacta del alcance y usa --no-telemetry. Nunca instala PentestGPT solo."
    )
    dangerous = True
    input_schema = {
        "type": "object",
        "properties": {
            "objetivo": {
                "type": "string",
                "description": "URL o host exacto del sistema propio o autorizado.",
            },
            "alcance_autorizado": {
                "type": "string",
                "description": "Debe repetir exactamente el objetivo autorizado.",
            },
            "confirmo_que_tengo_autorizacion": {"type": "boolean"},
            "instruccion": {
                "type": "string",
                "description": "Contexto defensivo opcional, sin comandos de shell.",
                "maxLength": 1000,
            },
            "backend": {
                "type": "string",
                "enum": ["claude", "codex"],
                "description": "Backend autónomo de PentestGPT; usa Claude o Codex CLI.",
            },
            "modelo": {
                "type": "string",
                "description": "Modelo opcional compatible con el backend elegido.",
                "maxLength": 120,
            },
        },
        "required": [
            "objetivo",
            "alcance_autorizado",
            "confirmo_que_tengo_autorizacion",
        ],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        root = _local_root(ctx)
        if root is None:
            return ToolResult(
                content="PentestGPT solo se puede ejecutar desde una instalación local configurada."
            )
        target = _normalize_target(str(args.get("objetivo", "")))
        authorized_scope = _normalize_target(str(args.get("alcance_autorizado", "")))
        if not bool(args.get("confirmo_que_tengo_autorizacion")):
            return ToolResult(
                content="No se ejecutó: falta declarar que tienes autorización sobre el objetivo."
            )
        if target is None or authorized_scope is None or target != authorized_scope:
            return ToolResult(
                content=(
                    "No se ejecutó: el objetivo y el alcance autorizado deben ser válidos y "
                    "coincidir exactamente."
                )
            )
        binary_info = _pentest_binary(ctx.settings)
        if binary_info is None:
            return ToolResult(
                content=(
                    "PentestGPT no está instalado o PENTESTGPT_BINARY no apunta a un ejecutable. "
                    "Edecán no lo instala automáticamente para evitar código no fijado o "
                    "inesperado."
                )
            )
        binary, command_style = binary_info
        instruction = str(args.get("instruccion", "")).strip() or None
        if instruction and len(instruction) > 1000:
            return ToolResult(content="La instrucción supera el límite de 1000 caracteres.")
        backend = str(
            args.get("backend") or getattr(ctx.settings, "PENTESTGPT_BACKEND", "claude")
        ).strip()
        if backend not in {"claude", "codex"}:
            return ToolResult(content="PentestGPT autónomo solo admite backend claude o codex.")
        model = str(args.get("modelo") or "").strip() or None
        if model and (len(model) > 120 or not re.fullmatch(r"[A-Za-z0-9._:/+-]+", model)):
            return ToolResult(content="El identificador de modelo no tiene un formato válido.")
        configured_timeout = int(
            getattr(ctx.settings, "PENTESTGPT_TIMEOUT_SECONDS", _DEFAULT_TIMEOUT_SECONDS)
        )
        timeout = max(_MIN_TIMEOUT_SECONDS, min(configured_timeout, _MAX_TIMEOUT_SECONDS))
        return_code, output, timed_out = await _run_pentestgpt(
            binary,
            command_style=command_style,
            target=target,
            instruction=instruction,
            backend=backend,
            model=model,
            cwd=root,
            timeout=timeout,
        )
        data_dir = Path(str(getattr(ctx.settings, "DATA_DIR", "~/.edecan/data"))).expanduser()
        report_dir = data_dir / "security-reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_id = f"pentest-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
        report_path = report_dir / f"{report_id}.json"
        report_path.write_text(
            json.dumps(
                {
                    "id": report_id,
                    "target": target,
                    "created_at": datetime.now(UTC).isoformat(),
                    "return_code": return_code,
                    "timed_out": timed_out,
                    "output": output,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        state = "agotó el tiempo" if timed_out else ("terminó" if return_code == 0 else "falló")
        return ToolResult(
            content=(
                f"PentestGPT {state} con código {return_code}. El reporte local saneado quedó "
                f"guardado como {report_path.name}; revisa los hallazgos antes de aplicar cambios."
            ),
            data={
                "report_id": report_id,
                "report_path": str(report_path),
                "target": target,
                "return_code": return_code,
                "timed_out": timed_out,
            },
        )
