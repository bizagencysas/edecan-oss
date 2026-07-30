#!/usr/bin/env python3
"""Crea el manifiesto estático que consume tauri-plugin-updater.

Solo acepta los tres artefactos canónicos que Edecán publica. Cada URL queda
unida al contenido de su `.sig`; si falta una plataforma o una firma el
release falla cerrado y no mueve el canal.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
CHANNELS = {"stable", "preview"}
DERIVED_TIMESTAMP_FIELD = "pub_date"
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


def parse_published_at(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{DERIVED_TIMESTAMP_FIELD} must be an RFC 3339 UTC timestamp.")
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError as error:
        raise ValueError(f"{DERIVED_TIMESTAMP_FIELD} must be an RFC 3339 UTC timestamp.") from error
    if parsed.utcoffset() != UTC.utcoffset(None):
        raise ValueError(f"{DERIVED_TIMESTAMP_FIELD} must use UTC.")
    return parsed


def immutable_release_payload(manifest: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in manifest.items() if key != DERIVED_TIMESTAMP_FIELD}


def parse_semver(value: str) -> tuple[tuple[int, int, int], tuple[str, ...]]:
    if len(value) > 80:
        raise ValueError("version is too long.")
    match = SEMVER_RE.fullmatch(value)
    if not match:
        raise ValueError("version must be strict SemVer.")
    prerelease = tuple((match.group(4) or "").split(".")) if match.group(4) else ()
    for identifier in prerelease:
        if identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0"):
            raise ValueError("numeric prerelease identifiers cannot have leading zeroes.")
    return (
        (int(match.group(1)), int(match.group(2)), int(match.group(3))),
        prerelease,
    )


def compare_semver(left: str, right: str) -> int:
    left_core, left_pre = parse_semver(left)
    right_core, right_pre = parse_semver(right)
    if left_core != right_core:
        return -1 if left_core < right_core else 1
    if left_pre == right_pre:
        return 0
    if not left_pre:
        return 1
    if not right_pre:
        return -1

    for left_identifier, right_identifier in zip(left_pre, right_pre, strict=False):
        if left_identifier == right_identifier:
            continue
        left_numeric = left_identifier.isdigit()
        right_numeric = right_identifier.isdigit()
        if left_numeric and right_numeric:
            return -1 if int(left_identifier) < int(right_identifier) else 1
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return -1 if left_identifier < right_identifier else 1
    return -1 if len(left_pre) < len(right_pre) else 1


def validate_channel_manifest(
    manifest: dict[str, Any],
    *,
    expected_channel: str,
) -> None:
    if expected_channel not in CHANNELS:
        raise ValueError("channel must be stable or preview.")
    version = manifest.get("version")
    if not isinstance(version, str):
        raise ValueError("manifest version is missing.")
    _, prerelease = parse_semver(version)
    pub_date = manifest.get(DERIVED_TIMESTAMP_FIELD)
    if not isinstance(pub_date, str):
        raise ValueError(f"manifest {DERIVED_TIMESTAMP_FIELD} is missing.")
    parse_published_at(pub_date)
    if expected_channel == "stable" and prerelease:
        raise ValueError("stable cannot publish a prerelease.")
    if expected_channel == "preview" and not prerelease:
        raise ValueError("preview requires a SemVer prerelease.")
    platforms = manifest.get("platforms")
    if not isinstance(platforms, dict) or set(platforms) != set(PLATFORM_PATTERNS):
        raise ValueError("manifest does not contain every canonical platform.")


def validate_transition(
    current: dict[str, Any] | None,
    candidate: dict[str, Any],
    *,
    expected_channel: str,
) -> None:
    validate_channel_manifest(candidate, expected_channel=expected_channel)
    if current is None:
        return
    validate_channel_manifest(current, expected_channel=expected_channel)
    precedence = compare_semver(
        str(candidate["version"]),
        str(current["version"]),
    )
    if precedence < 0:
        raise ValueError(
            f"channel cannot move backward from {current['version']} to {candidate['version']}."
        )
    if precedence == 0 and immutable_release_payload(candidate) != immutable_release_payload(
        current
    ):
        raise ValueError(f"version {candidate['version']} is already published and immutable.")


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
    published_at: datetime | None = None,
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
        DERIVED_TIMESTAMP_FIELD: (published_at or datetime.now(UTC))
        .astimezone(UTC)
        .isoformat()
        .replace("+00:00", "Z"),
        "platforms": platforms,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--published-at")
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
        published_at=(parse_published_at(args.published_at) if args.published_at else None),
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
