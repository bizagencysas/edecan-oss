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
