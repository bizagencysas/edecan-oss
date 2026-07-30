from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name("generate_android_update_manifest.py")
SPEC = importlib.util.spec_from_file_location("android_update_manifest", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_manifest_binds_hash_size_version_and_release_url(tmp_path: Path) -> None:
    apk = tmp_path / "Edecan-Android-0.7.4.apk"
    apk.write_bytes(b"signed-apk-placeholder")

    manifest = MODULE.build_manifest(
        apk=apk,
        repository="bizagencysas/edecan-oss",
        tag="v0.7.4",
        channel="stable",
        version_code=9,
        version_name="0.7.4",
        release_notes="Una mejora.",
    )

    assert manifest["version_code"] == 9
    assert manifest["apk"]["size_bytes"] == apk.stat().st_size
    assert len(manifest["apk"]["sha256"]) == 64
    assert manifest["apk"]["url"].endswith("/v0.7.4/Edecan-Android-0.7.4.apk")


def test_manifest_rejects_mismatched_tag_and_missing_apk(tmp_path: Path) -> None:
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"apk")

    with pytest.raises(ValueError, match="does not match"):
        MODULE.build_manifest(
            apk=apk,
            repository="bizagencysas/edecan-oss",
            tag="v0.7.5",
            channel="stable",
            version_code=9,
            version_name="0.7.4",
            release_notes="",
        )

    with pytest.raises(ValueError, match="missing or empty"):
        MODULE.build_manifest(
            apk=tmp_path / "missing.apk",
            repository="bizagencysas/edecan-oss",
            tag="v0.7.4",
            channel="stable",
            version_code=9,
            version_name="0.7.4",
            release_notes="",
        )


def test_transition_rejects_version_code_and_version_name_downgrades(
    tmp_path: Path,
) -> None:
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"apk")
    current = MODULE.build_manifest(
        apk=apk,
        repository="bizagencysas/edecan-oss",
        tag="v0.8.0",
        channel="stable",
        version_code=10,
        version_name="0.8.0",
        release_notes="",
    )

    lower_code = dict(current)
    lower_code["version_code"] = 9
    with pytest.raises(ValueError, match="version_code backward"):
        MODULE.validate_transition(current, lower_code, expected_channel="stable")

    lower_name = dict(current)
    lower_name["version_code"] = 11
    lower_name["version_name"] = "0.7.9"
    with pytest.raises(ValueError, match="version_name backward"):
        MODULE.validate_transition(current, lower_name, expected_channel="stable")

    conflicting_same_code = dict(current)
    conflicting_same_code["version_name"] = "0.8.1"
    with pytest.raises(ValueError, match="same version_code"):
        MODULE.validate_transition(
            current,
            conflicting_same_code,
            expected_channel="stable",
        )


def test_transition_allows_same_version_republish_and_monotonic_build(
    tmp_path: Path,
) -> None:
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"apk")
    current = MODULE.build_manifest(
        apk=apk,
        repository="bizagencysas/edecan-oss",
        tag="v0.8.0",
        channel="stable",
        version_code=10,
        version_name="0.8.0",
        release_notes="",
    )
    next_build = dict(current)
    next_build["version_code"] = 11

    MODULE.validate_transition(current, current, expected_channel="stable")
    MODULE.validate_transition(current, next_build, expected_channel="stable")

    timestamp_only = dict(current)
    timestamp_only["published_at"] = "2026-07-23T23:59:59Z"
    MODULE.validate_transition(current, timestamp_only, expected_channel="stable")

    mutated = dict(current)
    mutated["release_notes"] = "No puede mutarse después de publicar."
    with pytest.raises(ValueError, match="already published and immutable"):
        MODULE.validate_transition(current, mutated, expected_channel="stable")


def test_manifest_uses_a_repeatable_release_timestamp(tmp_path: Path) -> None:
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"apk")
    published_at = MODULE.parse_published_at("2026-07-23T12:34:56Z")
    arguments = {
        "apk": apk,
        "repository": "bizagencysas/edecan-oss",
        "tag": "v0.8.0",
        "channel": "stable",
        "version_code": 10,
        "version_name": "0.8.0",
        "release_notes": "Mismos bytes.",
        "published_at": published_at,
    }

    assert MODULE.build_manifest(**arguments) == MODULE.build_manifest(**arguments)
    with pytest.raises(ValueError, match="RFC 3339 UTC"):
        MODULE.parse_published_at("2026-07-23T12:34:56")


def test_release_workflow_uses_an_independent_race_safe_channel() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    workflow = (repo_root / ".github" / "workflows" / "release-android.yml").read_text(
        encoding="utf-8"
    )
    pinned_signer = (
        repo_root / "apps" / "mobile" / "android" / "release-signing-cert.sha256"
    ).read_text(encoding="utf-8")

    assert "group: release-android-updates" in workflow
    assert "group: release-update-channels" not in workflow
    assert "finalize-github-release.sh" in workflow
    assert "Signer #[0-9][0-9]* certificate SHA-256 digest" in workflow
    assert "release-signing-cert.sha256" in workflow
    assert "exactamente un firmante" in workflow
    assert "scripts/release/upload-github-release-asset.sh" in workflow
    assert "scripts/release/ensure-github-release.sh" in workflow
    assert '--published-at "$RELEASE_CREATED_AT"' in workflow
    assert "--clobber" not in workflow
    assert len(pinned_signer.strip()) == 64
    assert all(character in "0123456789abcdef" for character in pinned_signer.strip())
