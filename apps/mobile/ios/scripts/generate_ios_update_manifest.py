#!/usr/bin/env python3
"""Generate and validate Edecán's public iOS update-channel manifest.

The manifest never carries an executable or a credential. It only tells the
installed app that a newer signed distribution exists and which official
installation mechanism iOS should open.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
CHANNELS = {"stable", "preview"}
INSTALL_SCHEMES = {"https", "itms-apps", "itms-beta", "altstore", "sidestore"}
MAX_NOTES_LENGTH = 20_000
MAX_URL_LENGTH = 2_048


def parse_semver(value: str) -> tuple[tuple[int, int, int], tuple[str, ...]]:
    if len(value) > 80:
        raise ValueError("version is too long.")
    match = SEMVER_RE.fullmatch(value)
    if not match:
        raise ValueError("version must be strict SemVer.")
    prerelease = tuple((match.group(4) or "").split(".")) if match.group(4) else ()
    for identifier in prerelease:
        if identifier.isascii() and identifier.isdigit():
            if len(identifier) > 1 and identifier.startswith("0"):
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


def validate_install_url(value: str) -> str:
    if not value or len(value) > MAX_URL_LENGTH:
        raise ValueError("install_url is missing or too long.")
    if any(character.isspace() or ord(character) < 0x20 for character in value):
        raise ValueError("install_url contains whitespace or control characters.")

    parts = urlsplit(value)
    scheme = parts.scheme.lower()
    if scheme not in INSTALL_SCHEMES:
        raise ValueError(
            "install_url must use App Store, TestFlight, AltStore, "
            "SideStore or HTTPS."
        )
    if parts.username is not None or parts.password is not None or parts.fragment:
        raise ValueError("install_url cannot contain credentials or a fragment.")
    try:
        _ = parts.port
    except ValueError as error:
        raise ValueError("install_url has an invalid port.") from error

    if scheme in {"https", "itms-apps", "itms-beta"} and not parts.hostname:
        raise ValueError(f"{scheme} install_url requires a host.")
    if scheme in {"altstore", "sidestore"} and not (
        parts.netloc or parts.path or parts.query
    ):
        raise ValueError(f"{scheme} install_url is incomplete.")
    return value


def validate_manifest(manifest: dict[str, Any], *, expected_channel: str) -> None:
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported schema_version.")
    if expected_channel not in CHANNELS or manifest.get("channel") != expected_channel:
        raise ValueError("manifest channel does not match the target channel.")
    version = manifest.get("version")
    if not isinstance(version, str):
        raise ValueError("manifest version is missing.")
    _, prerelease = parse_semver(version)
    if expected_channel == "stable" and prerelease:
        raise ValueError("stable cannot publish a prerelease.")
    build_number = manifest.get("build_number")
    if not isinstance(build_number, int) or isinstance(build_number, bool) or build_number <= 0:
        raise ValueError("build_number must be a positive integer.")
    install_url = manifest.get("install_url")
    if not isinstance(install_url, str):
        raise ValueError("install_url is missing.")
    validate_install_url(install_url)


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
    precedence = compare_semver(
        str(candidate["version"]),
        str(current["version"]),
    )
    if precedence < 0:
        raise ValueError(
            f"channel cannot move backward from {current['version']} "
            f"to {candidate['version']}."
        )
    if (
        precedence == 0
        and int(candidate["build_number"]) < int(current["build_number"])
    ):
        raise ValueError(
            f"channel cannot move build backward from {current['build_number']} "
            f"to {candidate['build_number']}."
        )


def build_manifest(
    *,
    tag: str,
    channel: str,
    version: str,
    build_number: int,
    install_url: str,
    release_notes: str,
    published_at: datetime | None = None,
) -> dict[str, object]:
    if channel not in CHANNELS:
        raise ValueError("channel must be stable or preview.")
    if tag != f"v{version}":
        raise ValueError(f"tag {tag!r} does not match version {version!r}.")

    _, prerelease = parse_semver(version)
    if channel == "stable" and prerelease:
        raise ValueError("stable cannot publish a prerelease.")
    if channel == "preview" and not prerelease:
        raise ValueError("preview releases require a SemVer prerelease suffix.")
    if build_number <= 0:
        raise ValueError("build_number must be positive.")

    manifest: dict[str, object] = {
        "schema_version": 1,
        "channel": channel,
        "version": version,
        "build_number": build_number,
        "published_at": (published_at or datetime.now(UTC))
        .astimezone(UTC)
        .isoformat()
        .replace("+00:00", "Z"),
        "release_notes": release_notes.strip()[:MAX_NOTES_LENGTH],
        "install_url": validate_install_url(install_url),
    }
    validate_manifest(manifest, expected_channel=channel)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--channel", choices=sorted(CHANNELS), required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--build-number", type=int, required=True)
    parser.add_argument("--install-url", required=True)
    parser.add_argument("--notes-file", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    notes = ""
    if args.notes_file and args.notes_file.is_file():
        notes = args.notes_file.read_text(encoding="utf-8")
    manifest = build_manifest(
        tag=args.tag,
        channel=args.channel,
        version=args.version,
        build_number=args.build_number,
        install_url=args.install_url,
        release_notes=notes,
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
