from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate-update-manifest.py"
SPEC = importlib.util.spec_from_file_location("generate_update_manifest", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _write_artifacts(root: Path) -> None:
    names = (
        "Edecán.app.tar.gz",
        "Edecán_0.8.0_amd64.AppImage",
        "Edecán_0.8.0_amd64.deb",
        "Edecán-0.8.0-1.x86_64.rpm",
        "Edecán_0.8.0_x64-setup.exe",
        "Edecán_0.8.0_x64_en-US.msi",
    )
    for index, name in enumerate(names):
        artifact = root / name
        artifact.write_bytes(b"release")
        Path(f"{artifact}.sig").write_text(f"signature-{index}", encoding="utf-8")


def test_manifest_requires_signed_artifact_for_every_desktop_platform(
    tmp_path: Path,
) -> None:
    _write_artifacts(tmp_path)

    manifest = MODULE.build_manifest(
        artifacts=tmp_path,
        repository="bizagencysas/edecan-oss",
        tag="v0.8.0",
        version="v0.8.0",
        notes="Cambios importantes.",
    )

    assert manifest["version"] == "0.8.0"
    assert set(manifest["platforms"]) == {
        "darwin-aarch64-app",
        "linux-x86_64-appimage",
        "linux-x86_64-deb",
        "linux-x86_64-rpm",
        "windows-x86_64-nsis",
        "windows-x86_64-msi",
    }
    assert manifest["platforms"]["darwin-aarch64-app"]["url"].endswith("Edec%C3%A1n.app.tar.gz")
    assert manifest["platforms"]["windows-x86_64-nsis"]["signature"] == "signature-4"
    assert manifest["platforms"]["windows-x86_64-msi"]["signature"] == "signature-5"


def test_manifest_fails_closed_when_signature_is_missing(tmp_path: Path) -> None:
    _write_artifacts(tmp_path)
    Path(f"{tmp_path / 'Edecán_0.8.0_amd64.deb'}.sig").unlink()

    with pytest.raises(ValueError, match="falta la firma"):
        MODULE.build_manifest(
            artifacts=tmp_path,
            repository="bizagencysas/edecan-oss",
            tag="v0.8.0",
            version="0.8.0",
            notes="",
        )


def test_manifest_rejects_ambiguous_platform_artifacts(tmp_path: Path) -> None:
    _write_artifacts(tmp_path)
    duplicate = tmp_path / "otra.app.tar.gz"
    duplicate.write_bytes(b"duplicate")
    Path(f"{duplicate}.sig").write_text("signature", encoding="utf-8")

    with pytest.raises(ValueError, match="exactamente un artefacto"):
        MODULE.build_manifest(
            artifacts=tmp_path,
            repository="bizagencysas/edecan-oss",
            tag="v0.8.0",
            version="0.8.0",
            notes="",
        )


def test_channel_transition_rejects_downgrade_and_wrong_release_kind(
    tmp_path: Path,
) -> None:
    _write_artifacts(tmp_path)
    current = MODULE.build_manifest(
        artifacts=tmp_path,
        repository="bizagencysas/edecan-oss",
        tag="v0.8.0",
        version="0.8.0",
        notes="",
    )
    candidate = dict(current)
    candidate["version"] = "0.7.9"

    with pytest.raises(ValueError, match="cannot move backward"):
        MODULE.validate_transition(current, candidate, expected_channel="stable")

    preview = dict(current)
    preview["version"] = "0.9.0-beta.1"
    with pytest.raises(ValueError, match="stable cannot publish"):
        MODULE.validate_transition(current, preview, expected_channel="stable")

    with pytest.raises(ValueError, match="preview requires"):
        MODULE.validate_transition(None, current, expected_channel="preview")


def test_channel_transition_allows_monotonic_or_idempotent_publication(
    tmp_path: Path,
) -> None:
    _write_artifacts(tmp_path)
    current = MODULE.build_manifest(
        artifacts=tmp_path,
        repository="bizagencysas/edecan-oss",
        tag="v0.8.0",
        version="0.8.0",
        notes="",
    )
    candidate = dict(current)
    candidate["version"] = "0.9.0"

    MODULE.validate_transition(current, candidate, expected_channel="stable")
    MODULE.validate_transition(current, current, expected_channel="stable")

    timestamp_only = dict(current)
    timestamp_only["pub_date"] = "2026-07-23T23:59:59Z"
    MODULE.validate_transition(current, timestamp_only, expected_channel="stable")

    mutated = dict(current)
    mutated["notes"] = "El mismo número no puede cambiar después de publicarse."
    with pytest.raises(ValueError, match="already published and immutable"):
        MODULE.validate_transition(current, mutated, expected_channel="stable")


def test_manifest_uses_a_repeatable_release_timestamp(tmp_path: Path) -> None:
    _write_artifacts(tmp_path)
    published_at = MODULE.parse_published_at("2026-07-23T12:34:56Z")

    first = MODULE.build_manifest(
        artifacts=tmp_path,
        repository="bizagencysas/edecan-oss",
        tag="v0.8.0",
        version="0.8.0",
        notes="Mismos bytes.",
        published_at=published_at,
    )
    second = MODULE.build_manifest(
        artifacts=tmp_path,
        repository="bizagencysas/edecan-oss",
        tag="v0.8.0",
        version="0.8.0",
        notes="Mismos bytes.",
        published_at=published_at,
    )

    assert first == second
    with pytest.raises(ValueError, match="RFC 3339 UTC"):
        MODULE.parse_published_at("2026-07-23T12:34:56")
