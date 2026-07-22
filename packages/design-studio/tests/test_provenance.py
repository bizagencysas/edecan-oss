from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "edecan_design_studio"


def test_manifest_is_allowlist_only_and_has_no_copied_assets_or_config() -> None:
    manifest = json.loads((ROOT / "PORTING_MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["porting_mode"].startswith("clean-room")
    assert len(manifest["allowlist"]) == 4
    assert manifest["copied_assets"] == []
    assert manifest["copied_configuration"] == []
    assert manifest["copied_data"] == []
    assert "**/.env*" in manifest["denylist"]


def test_runtime_has_no_provider_sdk_or_environment_secret_reads() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(PACKAGE.glob("*.py"))
    ).lower()
    forbidden = (
        "os.environ",
        "os.getenv",
        "api_key",
        "access_token",
        "secret_key",
        "process.env",
    )
    assert all(token not in source for token in forbidden)
