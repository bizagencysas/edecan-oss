from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "publish_update_channel.sh"
REAL_GIT = shutil.which("git")
assert REAL_GIT

PLATFORMS = (
    "darwin-aarch64-app",
    "linux-x86_64-appimage",
    "linux-x86_64-deb",
    "linux-x86_64-rpm",
    "windows-x86_64-nsis",
    "windows-x86_64-msi",
)


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


def desktop_manifest(version: str) -> str:
    return json.dumps(
        {
            "version": version,
            "notes": "",
            "pub_date": "2026-07-23T00:00:00Z",
            "platforms": {
                platform: {
                    "url": f"https://example.test/{platform}",
                    "signature": f"signature-{platform}",
                }
                for platform in PLATFORMS
            },
        }
    )


def test_desktop_channel_retries_race_and_preserves_mobile_manifests(
    tmp_path: Path,
) -> None:
    bare = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    competitor = tmp_path / "competitor"
    checkout = tmp_path / "result"
    git(tmp_path, "init", "--bare", str(bare))
    git(tmp_path, "init", "-b", "main", str(seed))
    init_identity(seed)
    (seed / "stable.json").write_text(desktop_manifest("0.7.3"), encoding="utf-8")
    (seed / "android-stable.json").write_text("android old\n", encoding="utf-8")
    (seed / "ios-stable.json").write_text("ios stable\n", encoding="utf-8")
    git(seed, "add", "stable.json", "android-stable.json", "ios-stable.json")
    git(seed, "commit", "-m", "seed channels")
    git(seed, "remote", "add", "origin", str(bare))
    git(seed, "push", "origin", "HEAD:refs/heads/update-channels")

    git(tmp_path, "clone", "--branch", "update-channels", str(bare), str(competitor))
    init_identity(competitor)
    (competitor / "android-stable.json").write_text(
        "android won the race\n",
        encoding="utf-8",
    )
    git(competitor, "add", "android-stable.json")
    git(competitor, "commit", "-m", "concurrent Android channel")

    candidate = seed / "latest.json"
    candidate.write_text(desktop_manifest("0.7.4"), encoding="utf-8")

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
        [str(SCRIPT), str(candidate), "stable", "origin"],
        cwd=seed,
        env=environment,
        check=True,
        text=True,
        capture_output=True,
    )

    git(tmp_path, "clone", "--branch", "update-channels", str(bare), str(checkout))
    published = json.loads((checkout / "stable.json").read_text(encoding="utf-8"))
    assert published["version"] == "0.7.4"
    assert (checkout / "android-stable.json").read_text(
        encoding="utf-8"
    ) == "android won the race\n"
    assert (checkout / "ios-stable.json").read_text(encoding="utf-8") == "ios stable\n"


def test_desktop_channel_rejects_regressive_version(tmp_path: Path) -> None:
    bare = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    checkout = tmp_path / "result"
    git(tmp_path, "init", "--bare", str(bare))
    git(tmp_path, "init", "-b", "main", str(seed))
    init_identity(seed)
    current = desktop_manifest("0.8.0")
    (seed / "stable.json").write_text(current, encoding="utf-8")
    git(seed, "add", "stable.json")
    git(seed, "commit", "-m", "current stable")
    git(seed, "remote", "add", "origin", str(bare))
    git(seed, "push", "origin", "HEAD:refs/heads/update-channels")

    candidate = seed / "latest.json"
    candidate.write_text(desktop_manifest("0.7.9"), encoding="utf-8")
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
    assert "regressive desktop channel" in result.stderr
    git(tmp_path, "clone", "--branch", "update-channels", str(bare), str(checkout))
    assert (checkout / "stable.json").read_text(encoding="utf-8") == current
