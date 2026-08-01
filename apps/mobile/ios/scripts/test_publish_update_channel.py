from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).with_name("publish_update_channel.sh")
REAL_GIT = shutil.which("git")
assert REAL_GIT


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        [REAL_GIT, *args],
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def init_identity(repo: Path) -> None:
    git(repo, "config", "user.name", "Update Test")
    git(repo, "config", "user.email", "updates@example.test")


def manifest(version: str, build: int, channel: str = "stable") -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "channel": channel,
            "version": version,
            "build_number": build,
            "published_at": "2026-07-23T12:00:00Z",
            "release_notes": "",
            "install_url": "https://apps.apple.com/app/id123",
        }
    )


def test_push_retries_race_and_preserves_every_other_manifest(tmp_path: Path) -> None:
    bare = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    competitor = tmp_path / "competitor"
    checkout = tmp_path / "result"
    git(tmp_path, "init", "--bare", str(bare))
    git(tmp_path, "init", "-b", "main", str(seed))
    init_identity(seed)
    existing = {
        "stable.json": "desktop stable\n",
        "preview.json": "desktop preview\n",
        "android-stable.json": "android stable\n",
        "android-preview.json": "android preview\n",
    }
    for name, content in existing.items():
        (seed / name).write_text(content, encoding="utf-8")
    git(seed, "add", *existing)
    git(seed, "commit", "-m", "existing channels")
    git(seed, "remote", "add", "origin", str(bare))
    git(seed, "push", "origin", "HEAD:refs/heads/update-channels")

    git(tmp_path, "clone", "--branch", "update-channels", str(bare), str(competitor))
    init_identity(competitor)
    (competitor / "android-preview.json").write_text(
        "android preview won race\n",
        encoding="utf-8",
    )
    git(competitor, "add", "android-preview.json")
    git(competitor, "commit", "-m", "concurrent Android preview")

    ios_manifest = seed / "new-ios-stable.json"
    ios_manifest.write_text(manifest("0.8.0", 25) + "\n", encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "first-push-seen"
    fake_git = fake_bin / "git"
    fake_git.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "push" && ! -e "$FIRST_PUSH_MARKER" ]]; then
  : > "$FIRST_PUSH_MARKER"
  "$REAL_GIT" -C "$COMPETING_REPO" push origin HEAD:refs/heads/update-channels
  exit 1
fi
exec "$REAL_GIT" "$@"
""",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)

    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "REAL_GIT": REAL_GIT,
            "COMPETING_REPO": str(competitor),
            "FIRST_PUSH_MARKER": str(marker),
            "EDECAN_UPDATE_CHANNEL_MAX_ATTEMPTS": "3",
            "EDECAN_UPDATE_CHANNEL_RETRY_DELAY_SECONDS": "0",
        }
    )
    subprocess.run(
        [str(SCRIPT), str(ios_manifest), "stable", "origin"],
        cwd=seed,
        env=environment,
        check=True,
        text=True,
        capture_output=True,
    )

    git(tmp_path, "clone", "--branch", "update-channels", str(bare), str(checkout))
    for name, content in existing.items():
        expected = (
            "android preview won race\n"
            if name == "android-preview.json"
            else content
        )
        assert (checkout / name).read_text(encoding="utf-8") == expected
    assert json.loads((checkout / "ios-stable.json").read_text())["version"] == "0.8.0"


def test_push_rejects_regressive_version_and_leaves_channel_unchanged(
    tmp_path: Path,
) -> None:
    bare = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    checkout = tmp_path / "result"
    git(tmp_path, "init", "--bare", str(bare))
    git(tmp_path, "init", "-b", "main", str(seed))
    init_identity(seed)
    (seed / "ios-stable.json").write_text(
        manifest("2.0.0", 50) + "\n",
        encoding="utf-8",
    )
    git(seed, "add", "ios-stable.json")
    git(seed, "commit", "-m", "current iOS")
    git(seed, "remote", "add", "origin", str(bare))
    git(seed, "push", "origin", "HEAD:refs/heads/update-channels")

    older = seed / "older.json"
    older.write_text(manifest("1.9.9", 60) + "\n", encoding="utf-8")
    environment = os.environ.copy()
    environment["EDECAN_UPDATE_CHANNEL_MAX_ATTEMPTS"] = "1"
    result = subprocess.run(
        [str(SCRIPT), str(older), "stable", "origin"],
        cwd=seed,
        env=environment,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    git(tmp_path, "clone", "--branch", "update-channels", str(bare), str(checkout))
    assert json.loads((checkout / "ios-stable.json").read_text())["version"] == "2.0.0"
