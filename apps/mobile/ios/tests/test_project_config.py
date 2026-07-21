from pathlib import Path

PROJECT_YML = Path(__file__).resolve().parents[1] / "project.yml"


def test_visible_name_keeps_accent_but_executable_name_is_codesign_safe() -> None:
    project = PROJECT_YML.read_text(encoding="utf-8")

    assert "CFBundleDisplayName: Edecán" in project
    assert "PRODUCT_NAME: Edecan" in project
    assert "PRODUCT_NAME: Edecán" not in project
