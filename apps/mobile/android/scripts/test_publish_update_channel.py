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


def android_manifest(channel: str, version_code: int, version_name: str) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "channel": channel,
            "version_code": version_code,
            "version_name": version_name,
            "published_at": "2026-07-23T00:00:00Z",
            "release_notes": "",
            "apk": {
                "url": f"https://example.test/Edecan-{version_name}.apk",
                "sha256": "a" * 64,
                "size_bytes": 123,
            },
        }
    )


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

    android_manifest_path = seed / "new-android-stable.json"
    stable_candidate = android_manifest("stable", 9, "0.7.4")
    android_manifest_path.write_text(stable_candidate, encoding="utf-8")

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
        [str(SCRIPT), str(android_manifest_path), "stable", "origin"],
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
    ) == stable_candidate
    assert not (checkout / "android-preview.json").exists()

    preview_manifest = seed / "new-android-preview.json"
    preview_candidate = android_manifest("preview", 10, "0.8.0-beta.1")
    preview_manifest.write_text(preview_candidate, encoding="utf-8")
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
    ) == stable_candidate
    assert (checkout / "android-preview.json").read_text(
        encoding="utf-8"
    ) == preview_candidate
    assert (checkout / "stable.json").read_text(encoding="utf-8") == "desktop stable\n"


def test_channel_rejects_regressive_manifest_and_keeps_remote(
    tmp_path: Path,
) -> None:
    bare = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    checkout = tmp_path / "result"
    git(tmp_path, "init", "--bare", str(bare))
    git(tmp_path, "init", "-b", "main", str(seed))
    init_identity(seed)
    current = android_manifest("stable", 12, "0.9.0")
    (seed / "android-stable.json").write_text(current, encoding="utf-8")
    git(seed, "add", "android-stable.json")
    git(seed, "commit", "-m", "current Android stable")
    git(seed, "remote", "add", "origin", str(bare))
    git(seed, "push", "origin", "HEAD:refs/heads/update-channels")

    candidate = seed / "candidate.json"
    candidate.write_text(
        android_manifest("stable", 11, "0.8.0"),
        encoding="utf-8",
    )
    result = subprocess.run(
        [str(SCRIPT), str(candidate), "stable", "origin"],
        cwd=seed,
        env={
            **os.environ,
            "EDECAN_UPDATE_CHANNEL_RETRY_DELAY_SECONDS": "0",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "regressive Android channel" in result.stderr
    git(tmp_path, "clone", "--branch", "update-channels", str(bare), str(checkout))
    assert (checkout / "android-stable.json").read_text(encoding="utf-8") == current
