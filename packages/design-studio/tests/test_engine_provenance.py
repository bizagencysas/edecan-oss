from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from edecan_design_studio.engine import (
    FYDESIGN_CAPABILITIES,
    REMOTE_FYDESIGN_CAPABILITIES,
)
from edecan_design_studio.studio_tools import (
    PREMIUM_STUDIO_CAPABILITIES,
    SAFE_STUDIO_CAPABILITIES,
)

DESIGN_PACKAGE = Path(__file__).resolve().parents[1]
ENGINE_ROOT = DESIGN_PACKAGE.parent / "fydesign-engine"
MANIFEST_PATH = ENGINE_ROOT / "PORTING_MANIFEST.json"
CAPABILITIES_PATH = ENGINE_ROOT / "CAPABILITIES.md"


def _manifest() -> dict[str, object]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _capability_rows() -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    for line in CAPABILITIES_PATH.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| `fydesign_"):
            continue
        columns = [column.strip() for column in line.strip("|").split("|")]
        assert len(columns) == 6
        name = columns[0].strip("`")
        assert name not in rows
        rows[name] = columns
    return rows


def test_capability_matrix_matches_adapter_and_mcp_exactly() -> None:
    rows = _capability_rows()
    expected = set(FYDESIGN_CAPABILITIES)
    assert len(rows) == 37
    assert set(rows) == expected
    assert SAFE_STUDIO_CAPABILITIES | PREMIUM_STUDIO_CAPABILITIES == expected
    assert SAFE_STUDIO_CAPABILITIES.isdisjoint(PREMIUM_STUDIO_CAPABILITIES)

    mcp_source = (ENGINE_ROOT / "mcp" / "fydesign-mcp.mjs").read_text(
        encoding="utf-8"
    )
    mcp_names = set(re.findall(r"name:\s*['\"](fydesign_[a-z_]+)", mcp_source))
    assert mcp_names == set(REMOTE_FYDESIGN_CAPABILITIES)
    assert "fydesign_health" not in mcp_names

    for name, columns in rows.items():
        tier = columns[1]
        assert tier == ("safe" if name in SAFE_STUDIO_CAPABILITIES else "premium")
        assert columns[2]
        assert columns[3]
        assert columns[4]
        assert columns[5] == ("No" if tier == "safe" else "Sí")


def test_porting_manifest_is_an_exact_hashed_inventory() -> None:
    manifest = _manifest()
    contract = manifest["inventory_contract"]
    files = manifest["files"]
    assert isinstance(contract, dict)
    assert isinstance(files, dict)
    assert manifest["upstream"]["revision"] == (
        "4e5d6a8d21ef81a2daae921c70abf2d52c7a5cf7"
    )
    assert manifest["upstream"]["snapshot_kind"] == (
        "owner-supplied reviewed working tree"
    )
    working_tree = manifest["upstream"]["working_tree_inputs"]
    assert "src/lib/ai/fal-client.ts" in working_tree[
        "owner_supplied_untracked_and_reviewed"
    ]
    assert "scripts/port-open-design-templates.mjs" in working_tree["not_copied"]
    assert manifest["authorization"]["target_license"] == "Apache-2.0"

    extensions = set(contract["extensions"])
    actual = {
        path.relative_to(ENGINE_ROOT).as_posix()
        for path in ENGINE_ROOT.rglob("*")
        if path.is_file()
        and path.suffix in extensions
        and "node_modules" not in path.relative_to(ENGINE_ROOT).parts
    }
    assert len(files) == contract["file_count"]
    assert set(files) == actual

    resolved_root = ENGINE_ROOT.resolve()
    for relative, expected_digest in files.items():
        target = ENGINE_ROOT / relative
        assert target.resolve().is_relative_to(resolved_root)
        assert target.is_file()
        assert hashlib.sha256(target.read_bytes()).hexdigest() == expected_digest


def test_layer_b_inventory_accounts_for_every_upstream_module() -> None:
    manifest = _manifest()
    files = set(manifest["files"])
    layer = manifest["layer_b_inventory"]
    oss_only = set(layer["oss_only_modules"])
    excluded = layer["excluded_modules"]
    excluded_paths = {item["path"] for item in excluded}

    ported_same_path = {
        path for path in files if path.startswith("src/lib/") and path not in oss_only
    }
    assert len(ported_same_path) == layer["ported_same_path_module_count"] == 125
    assert len(excluded_paths) == len(excluded) == 6
    assert ported_same_path.isdisjoint(excluded_paths)
    assert oss_only <= files

    complete_upstream_inventory = ported_same_path | excluded_paths
    assert len(complete_upstream_inventory) == layer["upstream_module_count"] == 131
    inventory_bytes = "".join(
        f"{path}\n" for path in sorted(complete_upstream_inventory)
    ).encode()
    assert hashlib.sha256(inventory_bytes).hexdigest() == (
        layer["upstream_path_inventory_sha256"]
    )

    allowed_classifications = {
        "credential_plumbing",
        "private_brand_data",
        "saas_authentication",
        "saas_persistence",
        "ui_only",
        "unmediated_web_surface",
    }
    assert {item["classification"] for item in excluded} == allowed_classifications
    assert all(item["reason"].strip() for item in excluded)

    documented_exclusions = {
        match.group(1)
        for match in re.finditer(
            r"^\| `(src/lib/[^`]+)` \|",
            CAPABILITIES_PATH.read_text(encoding="utf-8"),
            flags=re.MULTILINE,
        )
    }
    assert documented_exclusions == excluded_paths


def test_manifest_excludes_private_state_builds_and_dependency_trees() -> None:
    manifest = _manifest()
    exclusions = manifest["exclusions"]
    patterns = set(exclusions["patterns"])
    assert {
        "**/.env*",
        "**/node_modules/**",
        "**/.git/**",
        "**/.next/**",
        "**/build/**",
        "**/test-anthropic.ts",
    } <= patterns
    assert exclusions["copied_assets"] == []
    assert exclusions["copied_configuration"] == []
    assert exclusions["copied_private_data"] == []
    assert exclusions["copied_history"] == []

    forbidden_parts = {".git", ".next", "build", "dist", "node_modules"}
    for relative in manifest["files"]:
        path = Path(relative)
        assert not forbidden_parts.intersection(path.parts)
        assert not path.name.startswith(".env")
        assert path.name != "test-anthropic.ts"
