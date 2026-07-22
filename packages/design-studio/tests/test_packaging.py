from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_desktop_sidecar_collects_design_studio_and_entry_point_metadata() -> None:
    spec = (REPO_ROOT / "apps/desktop/packaging/edecan_local.spec").read_text(encoding="utf-8")
    assert '"edecan_design_studio"' in spec
    assert 'distribution_name = pkg.replace("_", "-")' in spec
    assert "datas.extend(copy_metadata(distribution_name))" in spec
