#!/usr/bin/env python3
"""Generate the fail-closed public manifest for the Android OSS APK."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def build_manifest(
    *,
    apk: Path,
    repository: str,
    tag: str,
    channel: str,
    version_code: int,
    version_name: str,
    release_notes: str,
) -> dict[str, object]:
    if not apk.is_file() or apk.stat().st_size <= 0:
        raise ValueError("The signed APK is missing or empty.")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("repository must be owner/name.")
    if channel not in {"stable", "preview"}:
        raise ValueError("channel must be stable or preview.")
    if version_code <= 0:
        raise ValueError("version_code must be positive.")
    if not version_name or any(character.isspace() for character in version_name):
        raise ValueError("version_name is invalid.")
    expected_tag = f"v{version_name}"
    if tag != expected_tag:
        raise ValueError(f"tag {tag!r} does not match {expected_tag!r}.")

    sha256 = hashlib.sha256(apk.read_bytes()).hexdigest()
    if not SHA256_RE.fullmatch(sha256):  # defensive; hashlib is deterministic.
        raise AssertionError("Unexpected SHA-256 output.")

    asset_name = quote(apk.name, safe="._-")
    return {
        "schema_version": 1,
        "channel": channel,
        "version_code": version_code,
        "version_name": version_name,
        "published_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "release_notes": release_notes.strip()[:20_000],
        "apk": {
            "url": (
                f"https://github.com/{repository}/releases/download/"
                f"{quote(tag, safe='._-')}/{asset_name}"
            ),
            "sha256": sha256,
            "size_bytes": apk.stat().st_size,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--channel", choices=("stable", "preview"), required=True)
    parser.add_argument("--version-code", type=int, required=True)
    parser.add_argument("--version-name", required=True)
    parser.add_argument("--notes-file", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    notes = ""
    if args.notes_file:
        notes = args.notes_file.read_text(encoding="utf-8")
    manifest = build_manifest(
        apk=args.apk,
        repository=args.repository,
        tag=args.tag,
        channel=args.channel,
        version_code=args.version_code,
        version_name=args.version_name,
        release_notes=notes,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
