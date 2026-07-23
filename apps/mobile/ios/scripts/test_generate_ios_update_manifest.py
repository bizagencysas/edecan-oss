from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name("generate_ios_update_manifest.py")
SPEC = importlib.util.spec_from_file_location("ios_update_manifest", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_manifest_binds_channel_version_build_notes_and_install_url() -> None:
    manifest = MODULE.build_manifest(
        tag="v0.8.0",
        channel="stable",
        version="0.8.0",
        build_number=25,
        install_url="https://testflight.apple.com/join/ABCDEF",
        release_notes="  Mejor chat.  ",
        published_at=datetime(2026, 7, 23, 12, tzinfo=UTC),
    )

    assert manifest == {
        "schema_version": 1,
        "channel": "stable",
        "version": "0.8.0",
        "build_number": 25,
        "published_at": "2026-07-23T12:00:00Z",
        "release_notes": "Mejor chat.",
        "install_url": "https://testflight.apple.com/join/ABCDEF",
    }


@pytest.mark.parametrize(
    ("url", "valid"),
    [
        ("https://apps.apple.com/app/id123", True),
        ("https://testflight.apple.com/join/ABC", True),
        ("https://updates.example.com/install", True),
        ("itms-apps://itunes.apple.com/app/id123", True),
        ("itms-beta://testflight.apple.com/join/ABC", True),
        ("altstore://source?url=https%3A%2F%2Fexample.com%2Fsource.json", True),
        ("sidestore://source?url=https%3A%2F%2Fexample.com%2Fsource.json", True),
        ("http://example.com/install", False),
        ("file:///tmp/app.ipa", False),
        ("javascript:alert(1)", False),
        ("https://user:secret@example.com/install", False),
        ("https://example.com/install#fragment", False),
        ("altstore:", False),
    ],
)
def test_install_url_allowlist(url: str, valid: bool) -> None:
    if valid:
        assert MODULE.validate_install_url(url) == url
    else:
        with pytest.raises(ValueError):
            MODULE.validate_install_url(url)


def test_generator_rejects_tag_channel_build_and_semver_mismatch() -> None:
    base = {
        "tag": "v0.8.0",
        "channel": "stable",
        "version": "0.8.0",
        "build_number": 25,
        "install_url": "https://apps.apple.com/app/id123",
        "release_notes": "",
    }
    for changes in [
        {"tag": "v0.8.1"},
        {"channel": "preview"},
        {"version": "0.08.0"},
        {"build_number": 0},
    ]:
        with pytest.raises(ValueError):
            MODULE.build_manifest(**(base | changes))

    preview = MODULE.build_manifest(
        **(
            base
            | {
                "tag": "v0.8.0-beta.2",
                "channel": "preview",
                "version": "0.8.0-beta.2",
            }
        )
    )
    assert preview["channel"] == "preview"


def test_transition_uses_semver_and_build_without_allowing_downgrade() -> None:
    current = MODULE.build_manifest(
        tag="v0.8.0-beta.10",
        channel="preview",
        version="0.8.0-beta.10",
        build_number=30,
        install_url="https://testflight.apple.com/join/ABC",
        release_notes="",
    )
    newer = MODULE.build_manifest(
        tag="v0.8.0-rc.1",
        channel="preview",
        version="0.8.0-rc.1",
        build_number=31,
        install_url="https://testflight.apple.com/join/ABC",
        release_notes="",
    )
    MODULE.validate_transition(current, newer, expected_channel="preview")

    with pytest.raises(ValueError, match="move backward"):
        MODULE.validate_transition(newer, current, expected_channel="preview")

    same_version_lower_build = dict(newer, build_number=29)
    with pytest.raises(ValueError, match="move build backward"):
        MODULE.validate_transition(
            newer,
            same_version_lower_build,
            expected_channel="preview",
        )


def test_release_workflow_is_noop_without_configured_install_url() -> None:
    workflow = (
        Path(__file__).resolve().parents[4]
        / ".github"
        / "workflows"
        / "release-ios.yml"
    ).read_text(encoding="utf-8")

    assert "vars.EDECAN_IOS_INSTALL_URL" in workflow
    assert "needs.gate.outputs.enabled == 'true'" in workflow
    assert "group: release-update-channels" in workflow
    assert "astral-sh/setup-uv@" in workflow
    assert "uv run --frozen pytest -q apps/mobile/ios/scripts" in workflow
    assert "publish_update_channel.sh" in workflow
    assert "ios-${{ steps.metadata.outputs.channel }}.json" in workflow
