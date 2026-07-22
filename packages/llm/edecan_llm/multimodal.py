"""Validación y traducción común de bloques multimodales de Edecán."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

_IMAGE_MIMES = {
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
_MAX_IMAGES = 10
_MAX_IMAGE_BYTES = 10 * 1024 * 1024


def image_source(block: dict[str, Any]) -> tuple[str, str] | None:
    """Devuelve ``(mime, base64)`` para un bloque de imagen válido."""

    if block.get("type") != "image":
        return None
    source = block.get("source")
    if not isinstance(source, dict) or source.get("type") != "base64":
        return None
    mime = str(source.get("media_type") or "").split(";", 1)[0].strip().lower()
    data = source.get("data")
    if mime not in _IMAGE_MIMES or not isinstance(data, str) or not data:
        return None
    return mime, data


def text_blocks(content: str | list[dict[str, Any]]) -> str:
    if isinstance(content, str):
        return content
    return "".join(
        str(block.get("text") or "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


def image_sources(content: str | list[dict[str, Any]]) -> list[tuple[str, str]]:
    if isinstance(content, str):
        return []
    sources: list[tuple[str, str]] = []
    for block in content:
        if isinstance(block, dict) and (source := image_source(block)) is not None:
            sources.append(source)
    return sources


def materialize_request_images(req: Any, directory: str | Path) -> list[str]:
    """Materializa imágenes base64 en rutas temporales generadas y acotadas."""

    target = Path(directory)
    paths: list[str] = []
    for message in getattr(req, "messages", []):
        for mime, encoded in image_sources(getattr(message, "content", "")):
            if len(paths) >= _MAX_IMAGES:
                return paths
            try:
                raw = base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError):
                continue
            if not raw or len(raw) > _MAX_IMAGE_BYTES:
                continue
            path = target / f"adjunto-{len(paths) + 1:02d}{_IMAGE_MIMES[mime]}"
            path.write_bytes(raw)
            paths.append(str(path))
    return paths


def cli_image_context(paths: list[str]) -> str:
    if not paths:
        return ""
    return "\n".join(
        [
            "Imágenes privadas adjuntas a este turno. Analízalas antes de responder; "
            "no inventes su contenido:",
            *(f"- {path}" for path in paths),
        ]
    )
