"""Frontera local, cerrada y comprobable para el motor creativo de Studio.

El catálogo se descubre por MCP, pero las ejecuciones llaman directamente al
generador fijado del paquete. Así no hay ``shell``, ``npx``, descargas en frío
ni lectura implícita de ``.env.local``. El proceso hijo recibe solamente el
entorno mínimo y credenciales explícitamente permitidas desde TokenVault.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .process_boundary import (
    ProcessExecutionTimeoutError,
    ProcessOutputLimitError,
    communicate_bounded,
    isolated_process_kwargs,
)

REMOTE_FYDESIGN_CAPABILITIES = frozenset(
    {
        "fydesign_ad_engine",
        "fydesign_ambassador",
        "fydesign_analyze_video",
        "fydesign_angles",
        "fydesign_animate",
        "fydesign_autoroute",
        "fydesign_batch",
        "fydesign_brands",
        "fydesign_campaign",
        "fydesign_clipper",
        "fydesign_edit",
        "fydesign_generate",
        "fydesign_image",
        "fydesign_influencer",
        "fydesign_instadump",
        "fydesign_instant",
        "fydesign_marketplace_card",
        "fydesign_moodboard",
        "fydesign_photo_dump",
        "fydesign_photodump",
        "fydesign_post",
        "fydesign_product_ad",
        "fydesign_product_photoshoot",
        "fydesign_product_shots",
        "fydesign_refine",
        "fydesign_register_brand",
        "fydesign_studio",
        "fydesign_storyboard",
        "fydesign_strategy",
        "fydesign_svg",
        "fydesign_talking_head",
        "fydesign_train_face",
        "fydesign_upscale",
        "fydesign_video",
        "fydesign_video_ad",
        "fydesign_virality",
    }
)
FYDESIGN_CAPABILITIES = tuple(sorted(REMOTE_FYDESIGN_CAPABILITIES | {"fydesign_health"}))

# Solo valores que el motor distribuido consume. Quedan fuera deliberadamente
# AWS_*, DATABASE_URL, claves maestras de Edecan, OAuth web y cookies del navegador.
FYDESIGN_SECRET_ENV_ALLOWLIST = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "CLAUDE_USE_MAX",
        "CLAUDE_CLI_PATH",
        "CLAUDE_CLI_MODEL",
        "CLAUDE_MODEL",
        "CLAUDE_VISION_MODEL",
        "FAL_KEY",
        "FAL_IMAGE_MODEL",
        "GEMINI_API_KEY",
        "GOOGLE_GENAI_API_KEY",
        "GOOGLE_CREDENTIALS_JSON",
        "GOOGLE_PREMIUM_IMAGE_MODEL",
        "GITHUB_TOKEN",
        "OPENAI_API_KEY",
        "VERTEX_AI_PROJECT_ID",
        "VERTEX_AI_LOCATION",
        "VERTEX_IMAGEN_LOCATION",
        "VERTEX_GOOGLE_IMAGE_LOCATION",
        "GCS_ASSETS_BUCKET",
        "MUAPI_API_KEY",
        "MUAPI_API_KEY2",
        "MUAPI_SANDBOX",
        "FY_IMAGE_ENGINE",
        "FYDESIGN_LLM_BRIDGE_TOKEN",
        "FYDESIGN_LLM_BRIDGE_URL",
        "CHROMIUM_PATH",
        "PUPPETEER_EXECUTABLE_PATH",
        "FFMPEG_PATH",
        "FFPROBE_PATH",
        "YTDLP_PATH",
        "ANIMATE_MODEL",
        "RECAST_MODEL",
        "REFERENCE_VIDEO_MODEL",
        "START_END_FRAME_MODEL",
        "MUAPI_ANGLES_MODEL",
        "MUAPI_BG_REMOVER_MODEL",
        "MUAPI_EXPAND_IMAGE_MODEL",
        "MUAPI_FACE_SWAP_MODEL",
        "MUAPI_HEADSHOT_MODEL",
        "MUAPI_IMAGE_MODEL",
        "MUAPI_IMAGE_UPSCALE",
        "MUAPI_INPAINT_MODEL",
        "MUAPI_LIPSYNC_MODEL",
        "MUAPI_LORA_TRAINER_MODEL",
        "MUAPI_LORA_ZIP_URL",
        "MUAPI_MUSIC_MODEL",
        "MUAPI_OBJECT_ERASE_MODEL",
        "MUAPI_OUTFIT_SWAP_MODEL",
        "MUAPI_PLACE_OBJECT_MODEL",
        "MUAPI_PRODUCT_PHOTO_MODEL",
        "MUAPI_RELIGHT_MODEL",
        "MUAPI_SKIN_ENHANCE_MODEL",
        "MUAPI_STYLE_EDIT_MODEL",
        "MUAPI_STYLE_TRANSFER_MODEL",
        "MUAPI_TTS_MODEL",
        "MUAPI_TTS_VOICE",
        "MUAPI_TTS_VOICE_ES",
        "MUAPI_VIDEO_MODEL",
        "MUAPI_VIDEO_UPSCALE",
    }
)

_BASE_ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
_INTERNAL_ARGUMENTS = frozenset(
    {
        "media",
        "list",
        "godMode",
        "outDir",
        "regColors",
        "regLogo",
        "regAssets",
        "regFonts",
        "regFacts",
        "regBlurb",
        "regRepo",
        "regId",
        "videoUrl",
        "videoFile",
        "analyzeFrames",
        "clipCount",
        "clipLength",
        "adNiches",
        "adGenerate",
        "personaAction",
        "voiceText",
        "editOp",
        "upscaleTarget",
        "upscaleScale",
        "animateOp",
        "refineAnswers",
    }
)

_MEDIA_BY_CAPABILITY = {
    "fydesign_register_brand": "register",
    "fydesign_image": "image",
    "fydesign_post": "post",
    "fydesign_edit": "edit",
    "fydesign_campaign": "campaign",
    "fydesign_strategy": "campaign",
    "fydesign_svg": "svg",
    "fydesign_video": "video",
    "fydesign_video_ad": "video-ad",
    "fydesign_analyze_video": "analyze",
    "fydesign_clipper": "clip",
    "fydesign_ad_engine": "ad-engine",
    "fydesign_product_ad": "product-ad",
    "fydesign_influencer": "persona",
    "fydesign_talking_head": "talking-head",
    "fydesign_photo_dump": "photo-dump",
    "fydesign_batch": "batch",
    "fydesign_studio": "edit-pro",
    "fydesign_photodump": "photodump",
    "fydesign_instadump": "instadump",
    "fydesign_ambassador": "ambassador",
    "fydesign_train_face": "train-face",
    "fydesign_storyboard": "storyboard",
    "fydesign_upscale": "upscale",
    "fydesign_animate": "animate",
    "fydesign_refine": "refine",
    "fydesign_moodboard": "moodboard",
    "fydesign_autoroute": "autoroute",
    "fydesign_virality": "virality",
    "fydesign_angles": "angles",
    "fydesign_product_shots": "product-shots",
    "fydesign_product_photoshoot": "product-photoshoot",
    "fydesign_marketplace_card": "marketplace-card",
    "fydesign_instant": "instant",
}

_RENAMES_BY_CAPABILITY: dict[str, dict[str, str]] = {
    "fydesign_register_brand": {
        "colors": "regColors",
        "logo": "regLogo",
        "assets": "regAssets",
        "fonts": "regFonts",
        "facts": "regFacts",
        "blurb": "regBlurb",
        "repo": "regRepo",
        "id": "regId",
    },
    "fydesign_strategy": {"brief": "prompt", "pieces": "count"},
    "fydesign_analyze_video": {
        "url": "videoUrl",
        "file": "videoFile",
        "frames": "analyzeFrames",
    },
    "fydesign_clipper": {
        "url": "videoUrl",
        "file": "videoFile",
        "count": "clipCount",
        "clipLength": "clipLength",
    },
    "fydesign_ad_engine": {"niches": "adNiches", "generate": "adGenerate"},
    "fydesign_influencer": {"action": "personaAction"},
    "fydesign_talking_head": {"script": "voiceText"},
    "fydesign_studio": {"op": "editOp"},
    "fydesign_upscale": {"target": "upscaleTarget", "scale": "upscaleScale"},
    "fydesign_animate": {"op": "animateOp"},
    "fydesign_refine": {"answers": "refineAnswers"},
}


class StudioEngineError(RuntimeError):
    """Fallo seguro y accionable de instalación, protocolo o ejecución."""


@dataclass(frozen=True)
class StudioEngineConfig:
    root: Path
    node_binary: Path | str = "node"
    timeout_seconds: float = 1_200.0
    max_output_bytes: int = 16 * 1024 * 1024
    store_path: Path | None = None
    output_dir: Path | None = None
    runtime_env: dict[str, str] = field(default_factory=dict)


def _clean_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_clean_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _clean_json_value(item) for key, item in value.items()}
    raise StudioEngineError(
        "El argumento de Studio contiene un valor no serializable: "
        f"{type(value).__name__}."
    )


def _generator_input(capability: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if capability == "fydesign_brands":
        return {"list": True}
    result = {
        key: _clean_json_value(value)
        for key, value in arguments.items()
        if key not in _INTERNAL_ARGUMENTS
    }
    for source, target in _RENAMES_BY_CAPABILITY.get(capability, {}).items():
        if source in arguments:
            result[target] = _clean_json_value(arguments[source])
        if source != target:
            result.pop(source, None)
    media = _MEDIA_BY_CAPABILITY.get(capability)
    if media:
        result["media"] = media
    if capability == "fydesign_strategy":
        result["godMode"] = True
    return result


class StudioEngineClient:
    """Cliente por trabajo; fail-closed ante drift y sin estado global."""

    def __init__(self, config: StudioEngineConfig) -> None:
        self.config = config

    def _root(self) -> Path:
        root = Path(self.config.root).expanduser().resolve()
        if not root.is_dir():
            raise StudioEngineError(f"El root de FyDesign no existe: {root}")
        return root

    def _error_detail(self, stderr: bytes) -> str:
        detail = stderr.decode("utf-8", errors="replace")
        for candidate in (self.config.root, self.config.output_dir, self.config.store_path):
            if candidate is not None:
                resolved = os.fspath(Path(candidate).expanduser().resolve())
                detail = detail.replace(resolved, "<studio>")
        detail = re.sub(
            r"(?i)(api[_-]?key|token|authorization|bearer)\s*[:=]\s*[^\s,;]+",
            r"\1=<redacted>",
            detail,
        )
        lines = [line.strip() for line in detail.splitlines() if line.strip()]
        return (lines[-1] if lines else "sin detalle adicional")[:600]

    def _node(self) -> str:
        raw = os.fspath(self.config.node_binary)
        if os.path.isabs(raw) or os.sep in raw:
            candidate = Path(raw).expanduser().resolve()
            if not candidate.is_file():
                raise StudioEngineError(f"No encontré el runtime Node configurado: {candidate}")
            return os.fspath(candidate)
        resolved = shutil.which(raw)
        if not resolved:
            raise StudioEngineError("No encontré Node. Instala el runtime de Studio empaquetado.")
        return resolved

    def _mcp_script(self) -> Path:
        script = self._root() / "mcp" / "fydesign-mcp.mjs"
        if not script.is_file():
            raise StudioEngineError(f"Falta el script MCP de FyDesign: {script}")
        return script

    def _generator_command(self) -> list[str]:
        root = self._root()
        generator = root / "scripts" / "fydesign-gen.ts"
        if not generator.is_file():
            raise StudioEngineError(f"Falta el generador de FyDesign: {generator}")
        # El bundle no debe depender del shebang ``/usr/bin/env node`` de
        # node_modules/.bin/tsx: Tauri entrega el runtime con el nombre de
        # sidecar ``fydesign-node``. Invocar el CLI JavaScript con el Node
        # configurado hace que el DMG/AppImage/NSIS funcione sin Node global.
        tsx_cli = root / "node_modules" / "tsx" / "dist" / "cli.mjs"
        if tsx_cli.is_file():
            return [self._node(), os.fspath(tsx_cli), "scripts/fydesign-gen.ts"]
        tsx = root / "node_modules" / ".bin" / ("tsx.cmd" if os.name == "nt" else "tsx")
        if not tsx.is_file():
            raise StudioEngineError(
                "FyDesign no está instalado: falta el CLI local de tsx. "
                "Ejecuta npm ci en el motor."
            )
        return [os.fspath(tsx), "scripts/fydesign-gen.ts"]

    def _safe_env(self, credentials: dict[str, str] | None = None) -> dict[str, str]:
        env = {key: os.environ[key] for key in _BASE_ENV_ALLOWLIST if os.environ.get(key)}
        for key, value in (credentials or {}).items():
            if key in FYDESIGN_SECRET_ENV_ALLOWLIST and isinstance(value, str) and value:
                env[key] = value
        # Configuración no secreta controlada por Edecán, no por el modelo.
        runtime_keys = (
            "CHROMIUM_PATH",
            "PUPPETEER_EXECUTABLE_PATH",
            "PLAYWRIGHT_BROWSERS_PATH",
            "FFMPEG_PATH",
            "FFPROBE_PATH",
            "YTDLP_PATH",
        )
        for key in runtime_keys:
            value = self.config.runtime_env.get(key)
            if value:
                env[key] = value
        bundled_browsers = self._root() / "playwright-browsers"
        if bundled_browsers.is_dir():
            env["PLAYWRIGHT_BROWSERS_PATH"] = os.fspath(bundled_browsers)
        if self.config.store_path is not None:
            store_path = self.config.store_path.expanduser().resolve()
            env["FYDESIGN_STORE_PATH"] = os.fspath(store_path)
            env["FYDESIGN_STATE_ROOT"] = os.fspath(store_path.parent)
        return env

    async def _communicate(
        self,
        command: list[str],
        *,
        stdin: bytes | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[bytes, bytes]:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=self._root(),
            env=env or self._safe_env(),
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **isolated_process_kwargs(),
        )
        try:
            stdout, stderr = await communicate_bounded(
                process,
                stdin,
                timeout_seconds=self.config.timeout_seconds,
                max_output_bytes=self.config.max_output_bytes,
            )
        except ProcessExecutionTimeoutError as exc:
            raise StudioEngineError(
                f"El motor de Studio tardó más de {self.config.timeout_seconds:g}s y se canceló."
            ) from exc
        except ProcessOutputLimitError as exc:
            raise StudioEngineError(
                "La salida del motor de Studio superó el límite permitido y se canceló."
            ) from exc
        if process.returncode != 0:
            raise StudioEngineError(
                f"El motor de Studio terminó con código {process.returncode}. "
                f"Detalle seguro: {self._error_detail(stderr)}"
            )
        return stdout, stderr

    async def discover(self) -> list[dict[str, Any]]:
        payload = b"\n".join(
            (
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
                    }
                ).encode(),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).encode(),
            )
        ) + b"\n"
        env = self._safe_env()
        env["FYDESIGN_DIR"] = os.fspath(self._root())
        stdout, _ = await self._communicate(
            [self._node(), os.fspath(self._mcp_script())], stdin=payload, env=env
        )
        responses: dict[int, dict[str, Any]] = {}
        for line in stdout.decode("utf-8", errors="strict").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            if isinstance(item, dict) and isinstance(item.get("id"), int):
                responses[item["id"]] = item
        tools = responses.get(2, {}).get("result", {}).get("tools")
        if not isinstance(tools, list):
            raise StudioEngineError("FyDesign no devolvió un catálogo MCP válido.")
        names = {tool.get("name") for tool in tools if isinstance(tool, dict)}
        if names != REMOTE_FYDESIGN_CAPABILITIES:
            missing = len(REMOTE_FYDESIGN_CAPABILITIES - names)
            extra = len(names - REMOTE_FYDESIGN_CAPABILITIES)
            raise StudioEngineError(
                "La superficie de FyDesign no coincide con la allowlist "
                f"(faltan {missing}, sobran {extra})."
            )
        return [
            *tools,
            {
                "name": "fydesign_health",
                "description": (
                    "Comprueba el motor local y sus 37 capacidades sin generar artefactos "
                    "ni modificar estado."
                ),
                "inputSchema": {"type": "object", "properties": {}},
            },
        ]

    async def execute(
        self,
        capability: str,
        arguments: dict[str, Any],
        *,
        credentials: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if capability not in FYDESIGN_CAPABILITIES:
            raise StudioEngineError(f"La capacidad {capability!r} no está permitida por Studio.")
        if capability == "fydesign_health":
            discovered = await self.discover()
            return {
                "ok": True,
                "engine": "fydesign",
                "capabilities": len(discovered),
                "root": os.fspath(self._root()),
            }
        if not isinstance(arguments, dict):
            raise StudioEngineError("Los argumentos de Studio deben ser un objeto JSON.")
        command = self._generator_command()
        payload = _generator_input(capability, arguments)
        env = self._safe_env(credentials)
        if self.config.output_dir is not None:
            output_dir = self.config.output_dir.expanduser().resolve()
            output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            env["FYDESIGN_OUTPUT_ROOT"] = os.fspath(output_dir)
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        stdout, _ = await self._communicate(command, stdin=encoded, env=env)
        text = stdout.decode("utf-8", errors="strict").strip()
        try:
            result = json.loads(text)
        except json.JSONDecodeError as exc:
            raise StudioEngineError("FyDesign devolvió una respuesta JSON inválida.") from exc
        if not isinstance(result, dict):
            raise StudioEngineError("FyDesign devolvió un resultado inesperado.")
        return result


__all__ = [
    "FYDESIGN_CAPABILITIES",
    "FYDESIGN_SECRET_ENV_ALLOWLIST",
    "REMOTE_FYDESIGN_CAPABILITIES",
    "StudioEngineClient",
    "StudioEngineConfig",
    "StudioEngineError",
]
