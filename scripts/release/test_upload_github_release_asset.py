from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).with_name("upload-github-release-asset.sh")

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
    assets = [
        {
            "name": name,
            "apiUrl": (
                "https://api.github.com/repos/example/edecan/"
                f"releases/assets/{metadata['id']}"
            ),
        }
        for name, metadata in state["assets"].items()
    ]
    print(json.dumps({"assets": assets}))
    raise SystemExit(0)

if arguments and arguments[0] == "api":
    asset_id = arguments[-1].rsplit("/", 1)[-1]
    for metadata in state["assets"].values():
        if metadata["id"] == asset_id:
            sys.stdout.buffer.write(Path(metadata["path"]).read_bytes())
            raise SystemExit(0)
    raise SystemExit(1)

if arguments[:2] == ["release", "upload"]:
    local_file = Path(arguments[-1])
    state["uploads"] += 1
    if state["mode"] in {"success", "race"}:
        destination = state_path.parent / f"remote-{local_file.name}"
        shutil.copyfile(local_file, destination)
        state["assets"][local_file.name] = {
            "id": str(len(state["assets"]) + 1),
            "path": str(destination),
        }
        save(state)
        raise SystemExit(1 if state["mode"] == "race" else 0)
    save(state)
    raise SystemExit(1)

raise SystemExit(f"unexpected gh invocation: {arguments!r}")
"""


def _environment(tmp_path: Path, state: dict[str, object]) -> tuple[dict[str, str], Path]:
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    fake_gh = bin_directory / "gh"
    fake_gh.write_text(FAKE_GH, encoding="utf-8")
    fake_gh.chmod(fake_gh.stat().st_mode | stat.S_IXUSR)
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    environment = os.environ.copy()
    environment["PATH"] = f"{bin_directory}{os.pathsep}{environment['PATH']}"
    environment["FAKE_GH_STATE"] = str(state_path)
    return environment, state_path


def _run(script_asset: Path, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), "v1.2.3", str(script_asset)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


def test_accepts_an_existing_byte_identical_asset(tmp_path: Path) -> None:
    local = tmp_path / "Edecan.dmg"
    remote = tmp_path / "published.dmg"
    local.write_bytes(b"same")
    remote.write_bytes(b"same")
    environment, state_path = _environment(
        tmp_path,
        {
            "assets": {local.name: {"id": "1", "path": str(remote)}},
            "mode": "success",
            "uploads": 0,
        },
    )

    result = _run(local, environment)

    assert result.returncode == 0, result.stderr
    assert json.loads(state_path.read_text(encoding="utf-8"))["uploads"] == 0


def test_rejects_an_existing_asset_with_different_bytes(tmp_path: Path) -> None:
    local = tmp_path / "Edecan.dmg"
    remote = tmp_path / "published.dmg"
    local.write_bytes(b"local")
    remote.write_bytes(b"remote")
    environment, state_path = _environment(
        tmp_path,
        {
            "assets": {local.name: {"id": "1", "path": str(remote)}},
            "mode": "success",
            "uploads": 0,
        },
    )

    result = _run(local, environment)

    assert result.returncode != 0
    assert "bytes distintos" in result.stderr
    assert json.loads(state_path.read_text(encoding="utf-8"))["uploads"] == 0


def test_uploads_an_absent_asset(tmp_path: Path) -> None:
    local = tmp_path / "Edecan.dmg"
    local.write_bytes(b"new")
    environment, state_path = _environment(
        tmp_path,
        {"assets": {}, "mode": "success", "uploads": 0},
    )

    result = _run(local, environment)

    assert result.returncode == 0, result.stderr
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["uploads"] == 1
    assert local.name in state["assets"]


def test_accepts_a_concurrent_identical_upload(tmp_path: Path) -> None:
    local = tmp_path / "Edecan.dmg"
    local.write_bytes(b"race")
    environment, state_path = _environment(
        tmp_path,
        {"assets": {}, "mode": "race", "uploads": 0},
    )

    result = _run(local, environment)

    assert result.returncode == 0, result.stderr
    assert json.loads(state_path.read_text(encoding="utf-8"))["uploads"] == 1
