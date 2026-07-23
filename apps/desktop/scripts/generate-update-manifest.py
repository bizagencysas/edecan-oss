#!/usr/bin/env python3
"""Crea el manifiesto estático que consume tauri-plugin-updater.

Solo acepta los tres artefactos canónicos que Edecán publica. Cada URL queda
unida al contenido de su `.sig`; si falta una plataforma o una firma el
release falla cerrado y no mueve el canal.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

PLATFORM_PATTERNS = {
    # tauri-plugin-updater busca primero `{os}-{arch}-{installer}`. Publicar
    # una única entrada genérica haría que una instalación MSI intentara
    # consumir el NSIS, o que Debian intentara instalar una AppImage.
    "darwin-aarch64-app": lambda path: path.name.endswith(".app.tar.gz"),
    "linux-x86_64-appimage": lambda path: path.name.endswith(".AppImage"),
    "linux-x86_64-deb": lambda path: path.name.endswith(".deb"),
    "linux-x86_64-rpm": lambda path: path.name.endswith(".rpm"),
    "windows-x86_64-nsis": lambda path: path.name.endswith("-setup.exe"),
    "windows-x86_64-msi": lambda path: path.name.endswith(".msi"),
}


def _artifact_for(root: Path, platform: str) -> Path:
    matches = sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and not path.name.endswith(".sig") and PLATFORM_PATTERNS[platform](path)
    )
    if len(matches) != 1:
        names = ", ".join(str(path.relative_to(root)) for path in matches) or "ninguno"
        raise ValueError(
            f"{platform}: se esperaba exactamente un artefacto de updater, encontrados: {names}"
        )
    signature = Path(f"{matches[0]}.sig")
    if not signature.is_file() or not signature.read_text(encoding="utf-8").strip():
        raise ValueError(f"{platform}: falta la firma {signature.name}")
    return matches[0]


def build_manifest(
    *,
    artifacts: Path,
    repository: str,
    tag: str,
    version: str,
    notes: str,
) -> dict[str, object]:
    platforms: dict[str, dict[str, str]] = {}
    base_url = f"https://github.com/{repository}/releases/download/{quote(tag, safe='')}"
    for platform in PLATFORM_PATTERNS:
        artifact = _artifact_for(artifacts, platform)
        signature = Path(f"{artifact}.sig").read_text(encoding="utf-8").strip()
        platforms[platform] = {
            "url": f"{base_url}/{quote(artifact.name, safe='')}",
            "signature": signature,
        }
    return {
        "version": version.removeprefix("v"),
        "notes": notes.strip(),
        "pub_date": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "platforms": platforms,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--notes-file", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    notes = (
        args.notes_file.read_text(encoding="utf-8")
        if args.notes_file and args.notes_file.is_file()
        else f"Actualización {args.version} de Edecán."
    )
    manifest = build_manifest(
        artifacts=args.artifacts,
        repository=args.repository,
        tag=args.tag,
        version=args.version,
        notes=notes,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(f"{args.output.suffix}.tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(args.output)


if __name__ == "__main__":
    main()
