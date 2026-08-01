#!/usr/bin/env python3
"""Generate the fail-closed public manifest for the Android OSS APK."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
CHANNELS = {"stable", "preview"}
DERIVED_TIMESTAMP_FIELD = "published_at"


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
        raise ValueError("version_name is too long.")
    match = SEMVER_RE.fullmatch(value)
    if not match:
        raise ValueError("version_name must be strict SemVer.")
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


def validate_manifest(manifest: dict[str, Any], *, expected_channel: str) -> None:
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported schema_version.")
    if expected_channel not in CHANNELS or manifest.get("channel") != expected_channel:
        raise ValueError("manifest channel does not match the target channel.")

    version_code = manifest.get("version_code")
    if not isinstance(version_code, int) or isinstance(version_code, bool) or version_code <= 0:
        raise ValueError("version_code must be a positive integer.")
    version_name = manifest.get("version_name")
    if not isinstance(version_name, str):
        raise ValueError("version_name is missing.")
    _, prerelease = parse_semver(version_name)
    if expected_channel == "stable" and prerelease:
        raise ValueError("stable cannot publish a prerelease.")
    if expected_channel == "preview" and not prerelease:
        raise ValueError("preview requires a SemVer prerelease.")
    published_at = manifest.get(DERIVED_TIMESTAMP_FIELD)
    if not isinstance(published_at, str):
        raise ValueError(f"manifest {DERIVED_TIMESTAMP_FIELD} is missing.")
    parse_published_at(published_at)

    apk = manifest.get("apk")
    if not isinstance(apk, dict):
        raise ValueError("apk metadata is missing.")
    raw_url = apk.get("url")
    if not isinstance(raw_url, str):
        raise ValueError("apk url is missing.")
    parts = urlsplit(raw_url)
    if (
        parts.scheme.lower() != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
    ):
        raise ValueError("apk url must be credential-free HTTPS.")
    sha256 = apk.get("sha256")
    if not isinstance(sha256, str) or not SHA256_RE.fullmatch(sha256):
        raise ValueError("apk sha256 is invalid.")
    size_bytes = apk.get("size_bytes")
    if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes <= 0:
        raise ValueError("apk size_bytes must be a positive integer.")


def validate_transition(
    current: dict[str, Any] | None,
    candidate: dict[str, Any],
    *,
    expected_channel: str,
) -> None:
    validate_manifest(candidate, expected_channel=expected_channel)
    if current is None:
        return
    validate_manifest(current, expected_channel=expected_channel)

    current_code = int(current["version_code"])
    candidate_code = int(candidate["version_code"])
    current_name = str(current["version_name"])
    candidate_name = str(candidate["version_name"])
    if candidate_code < current_code:
        raise ValueError(
            f"channel cannot move version_code backward from {current_code} to {candidate_code}."
        )
    precedence = compare_semver(candidate_name, current_name)
    if precedence < 0:
        raise ValueError(
            f"channel cannot move version_name backward from {current_name} to {candidate_name}."
        )
    if candidate_code == current_code and precedence != 0:
        raise ValueError("the same version_code cannot identify a different version_name.")
    if (
        candidate_code == current_code
        and precedence == 0
        and immutable_release_payload(candidate) != immutable_release_payload(current)
    ):
        raise ValueError(
            f"Android build {candidate_code} ({candidate_name}) is already published and immutable."
        )


def build_manifest(
    *,
    apk: Path,
    repository: str,
    tag: str,
    channel: str,
    version_code: int,
    version_name: str,
    release_notes: str,
    published_at: datetime | None = None,
) -> dict[str, object]:
    if not apk.is_file() or apk.stat().st_size <= 0:
        raise ValueError("The signed APK is missing or empty.")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("repository must be owner/name.")
    if channel not in {"stable", "preview"}:
        raise ValueError("channel must be stable or preview.")
    if version_code <= 0:
        raise ValueError("version_code must be positive.")
    _, prerelease = parse_semver(version_name)
    if channel == "stable" and prerelease:
        raise ValueError("stable cannot publish a prerelease.")
    if channel == "preview" and not prerelease:
        raise ValueError("preview requires a SemVer prerelease.")
    expected_tag = f"v{version_name}"
    if tag != expected_tag:
        raise ValueError(f"tag {tag!r} does not match {expected_tag!r}.")

    sha256 = hashlib.sha256(apk.read_bytes()).hexdigest()
    if not SHA256_RE.fullmatch(sha256):  # defensive; hashlib is deterministic.
        raise AssertionError("Unexpected SHA-256 output.")

    asset_name = quote(apk.name, safe="._-")
    manifest = {
        "schema_version": 1,
        "channel": channel,
        "version_code": version_code,
        "version_name": version_name,
        DERIVED_TIMESTAMP_FIELD: (published_at or datetime.now(UTC))
        .astimezone(UTC)
        .isoformat()
        .replace("+00:00", "Z"),
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
    validate_manifest(manifest, expected_channel=channel)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--channel", choices=("stable", "preview"), required=True)
    parser.add_argument("--version-code", type=int, required=True)
    parser.add_argument("--version-name", required=True)
    parser.add_argument("--published-at")
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
        published_at=(parse_published_at(args.published_at) if args.published_at else None),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
