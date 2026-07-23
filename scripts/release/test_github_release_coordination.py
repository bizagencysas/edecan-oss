from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

ENSURE = Path(__file__).with_name("ensure-github-release.sh")
FINALIZE = Path(__file__).with_name("finalize-github-release.sh")

FAKE_GH = r"""#!/usr/bin/env python3
import json
import os
import shutil
import sys
from pathlib import Path

state_path = Path(os.environ["FAKE_GH_STATE"])


def load():
    return json.loads(state_path.read_text(encoding="utf-8"))


def save(state):
    state_path.write_text(json.dumps(state), encoding="utf-8")


arguments = sys.argv[1:]
state = load()
if arguments[:2] == ["release", "view"]:
    if state.get("release") is None:
        raise SystemExit(1)
    print(json.dumps(state["release"]))
    raise SystemExit(0)

if arguments[:2] == ["release", "create"]:
    tag = arguments[2]
    state["release"] = {
        "assets": [],
        "createdAt": "2026-07-23T12:34:56Z",
        "isDraft": True,
        "isPrerelease": "--prerelease" in arguments,
        "publishedAt": None,
        "tagName": tag,
    }
    save(state)
    raise SystemExit(1 if state.get("create_race") else 0)

if arguments[:2] == ["release", "download"]:
    destination = Path(arguments[arguments.index("--dir") + 1])
    name = arguments[arguments.index("--pattern") + 1]
    metadata = next(
        asset for asset in state["release"]["assets"] if asset["name"] == name
    )
    shutil.copyfile(metadata["path"], destination / name)
    raise SystemExit(0)

if arguments[:2] == ["release", "edit"]:
    state["release"]["isDraft"] = False
    state["release"]["publishedAt"] = "2026-07-23T13:00:00Z"
    save(state)
    raise SystemExit(0)

if arguments[:2] == ["auth", "setup-git"]:
    raise SystemExit(0)

raise SystemExit(f"unexpected gh invocation: {arguments!r}")
"""


def _environment(
    tmp_path: Path,
    *,
    release: dict[str, object] | None,
    create_race: bool = False,
) -> tuple[dict[str, str], Path]:
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    fake_gh = bin_directory / "gh"
    fake_gh.write_text(FAKE_GH, encoding="utf-8")
    fake_gh.chmod(fake_gh.stat().st_mode | stat.S_IXUSR)
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps({"release": release, "create_race": create_race}),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PATH"] = f"{bin_directory}{os.pathsep}{environment['PATH']}"
    environment["FAKE_GH_STATE"] = str(state_path)
    return environment, state_path


def _draft(assets: list[dict[str, str]]) -> dict[str, object]:
    return {
        "assets": assets,
        "createdAt": "2026-07-23T12:34:56Z",
        "isDraft": True,
        "isPrerelease": False,
        "publishedAt": None,
        "tagName": "v1.2.3",
    }


def test_release_creation_accepts_another_workflow_winning_the_race(
    tmp_path: Path,
) -> None:
    environment, state_path = _environment(
        tmp_path,
        release=None,
        create_race=True,
    )

    result = subprocess.run(
        [str(ENSURE), "v1.2.3", "1.2.3", "stable"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    release = json.loads(state_path.read_text(encoding="utf-8"))["release"]
    assert release["isDraft"] is True
    assert release["tagName"] == "v1.2.3"


def test_incomplete_draft_stays_private(tmp_path: Path) -> None:
    environment, state_path = _environment(tmp_path, release=_draft([]))

    result = subprocess.run(
        [str(FINALIZE), "v1.2.3", "1.2.3", "stable", "false"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    release = json.loads(state_path.read_text(encoding="utf-8"))["release"]
    assert release["isDraft"] is True


def test_complete_draft_is_published_before_channels_move(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    publisher_log = tmp_path / "publishers.log"
    for relative in (
        "apps/desktop/scripts/publish_update_channel.sh",
        "apps/mobile/android/scripts/publish_update_channel.sh",
        "apps/mobile/ios/scripts/publish_update_channel.sh",
    ):
        publisher = project / relative
        publisher.parent.mkdir(parents=True, exist_ok=True)
        publisher.write_text(
            f"#!/usr/bin/env bash\nprintf '%s\\n' '{relative}' >> '{publisher_log}'\n",
            encoding="utf-8",
        )
        publisher.chmod(publisher.stat().st_mode | stat.S_IXUSR)

    platform_names = {
        "darwin-aarch64-app": "Edecan.app.tar.gz",
        "linux-x86_64-appimage": "Edecan.AppImage",
        "linux-x86_64-deb": "Edecan.deb",
        "linux-x86_64-rpm": "Edecan.rpm",
        "windows-x86_64-nsis": "Edecan-setup.exe",
        "windows-x86_64-msi": "Edecan.msi",
    }
    latest = {
        "version": "1.2.3",
        "pub_date": "2026-07-23T12:34:56Z",
        "platforms": {
            platform: {
                "url": (f"https://github.com/example/edecan/releases/download/v1.2.3/{name}"),
                "signature": "signature",
            }
            for platform, name in platform_names.items()
        },
    }
    android = {
        "channel": "stable",
        "version_name": "1.2.3",
        "apk": {
            "url": (
                "https://github.com/example/edecan/releases/download/"
                "v1.2.3/Edecan-Android-1.2.3.apk"
            ),
            "sha256": "0" * 64,
        },
    }
    remote_files: dict[str, Path] = {}
    for name, contents in (
        ("latest.json", json.dumps(latest).encode()),
        ("android-stable.json", json.dumps(android).encode()),
    ):
        path = tmp_path / name
        path.write_bytes(contents)
        remote_files[name] = path
    asset_names = {
        "latest.json",
        "android-stable.json",
        "Edecan.dmg",
        "Edecan-Android-1.2.3.apk",
        "Edecan-Android-1.2.3.apk.sha256",
    }
    for name in platform_names.values():
        asset_names.update({name, f"{name}.sig"})
    assets = []
    for name in sorted(asset_names):
        path = remote_files.get(name, tmp_path / f"asset-{len(assets)}")
        if not path.exists():
            path.write_bytes(b"asset")
        assets.append({"name": name, "path": str(path)})

    environment, state_path = _environment(tmp_path, release=_draft(assets))
    result = subprocess.run(
        [str(FINALIZE), "v1.2.3", "1.2.3", "stable", "false"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        cwd=project,
    )

    assert result.returncode == 0, result.stderr
    release = json.loads(state_path.read_text(encoding="utf-8"))["release"]
    assert release["isDraft"] is False
    assert release["publishedAt"]
    assert publisher_log.read_text(encoding="utf-8").splitlines() == [
        "apps/desktop/scripts/publish_update_channel.sh",
        "apps/mobile/android/scripts/publish_update_channel.sh",
    ]
