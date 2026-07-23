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
