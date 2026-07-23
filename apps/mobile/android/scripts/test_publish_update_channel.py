from __future__ import annotations

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


def test_channel_push_retries_race_and_preserves_desktop_manifests(
    tmp_path: Path,
) -> None:
    bare = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    competitor = tmp_path / "competitor"
    checkout = tmp_path / "result"
    git(tmp_path, "init", "--bare", str(bare))
    git(tmp_path, "init", "-b", "main", str(seed))
    init_identity(seed)
    (seed / "stable.json").write_text("desktop stable\n", encoding="utf-8")
    (seed / "preview.json").write_text("desktop preview\n", encoding="utf-8")
    git(seed, "add", "stable.json", "preview.json")
    git(seed, "commit", "-m", "desktop channels")
    git(seed, "remote", "add", "origin", str(bare))
    git(seed, "push", "origin", "HEAD:refs/heads/update-channels")

    git(tmp_path, "clone", "--branch", "update-channels", str(bare), str(competitor))
    init_identity(competitor)
    (competitor / "preview.json").write_text(
        "desktop preview won the race\n",
        encoding="utf-8",
    )
    git(competitor, "add", "preview.json")
    git(competitor, "commit", "-m", "concurrent desktop preview")

    android_manifest = seed / "new-android-stable.json"
    android_manifest.write_text('{"channel":"stable"}\n', encoding="utf-8")

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
        [str(SCRIPT), str(android_manifest), "stable", "origin"],
        cwd=seed,
        env=environment,
        check=True,
        text=True,
        capture_output=True,
    )

    git(tmp_path, "clone", "--branch", "update-channels", str(bare), str(checkout))
    assert (checkout / "stable.json").read_text(encoding="utf-8") == "desktop stable\n"
    assert (checkout / "preview.json").read_text(
        encoding="utf-8"
    ) == "desktop preview won the race\n"
    assert (checkout / "android-stable.json").read_text(
        encoding="utf-8"
    ) == '{"channel":"stable"}\n'
    assert not (checkout / "android-preview.json").exists()

    preview_manifest = seed / "new-android-preview.json"
    preview_manifest.write_text('{"channel":"preview"}\n', encoding="utf-8")
    subprocess.run(
        [str(SCRIPT), str(preview_manifest), "preview", "origin"],
        cwd=seed,
        env=environment,
        check=True,
        text=True,
        capture_output=True,
    )
    git(checkout, "pull", "--ff-only")
    assert (checkout / "android-stable.json").read_text(
        encoding="utf-8"
    ) == '{"channel":"stable"}\n'
    assert (checkout / "android-preview.json").read_text(
        encoding="utf-8"
    ) == '{"channel":"preview"}\n'
    assert (checkout / "stable.json").read_text(encoding="utf-8") == "desktop stable\n"
