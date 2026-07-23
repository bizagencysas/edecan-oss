#!/usr/bin/env bash
# Publish a shared draft only after every configured platform uploaded its
# immutable assets. The updater channels move only after GitHub exposes the
# release publicly.
set -euo pipefail

if (( $# < 4 || $# > 5 )); then
  echo "usage: $0 TAG VERSION (stable|preview) IOS_REQUIRED [REMOTE]" >&2
  exit 2
fi

TAG=$1
VERSION=$2
CHANNEL=$3
IOS_REQUIRED=$4
REMOTE=${5:-origin}

if [[ "$TAG" != "v$VERSION" ]]; then
  echo "error: tag $TAG does not match version $VERSION." >&2
  exit 2
fi
if [[ "$CHANNEL" != stable && "$CHANNEL" != preview ]]; then
  echo "error: channel must be stable or preview." >&2
  exit 2
fi
if [[ "$IOS_REQUIRED" != true && "$IOS_REQUIRED" != false ]]; then
  echo "error: IOS_REQUIRED must be true or false." >&2
  exit 2
fi

TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/edecan-release-finalize.XXXXXX")
trap 'rm -rf "$TEMP_DIR"' EXIT

release_json=$(gh release view "$TAG" --json assets,isDraft,publishedAt,tagName)
printf '%s' "$release_json" > "$TEMP_DIR/release.json"

required_manifests=("latest.json" "android-$CHANNEL.json")
if [[ "$IOS_REQUIRED" == true ]]; then
  required_manifests+=("ios-$CHANNEL.json")
fi

missing_manifests=$(
  RELEASE_JSON="$release_json" \
    python3 - "${required_manifests[@]}" <<'PY'
import json
import os
import sys

assets = {
    asset.get("name")
    for asset in json.loads(os.environ["RELEASE_JSON"]).get("assets", [])
}
print("\n".join(name for name in sys.argv[1:] if name not in assets))
PY
)
if [[ -n "$missing_manifests" ]]; then
  if [[ $(python3 -c 'import json,sys; print(str(json.load(sys.stdin)["isDraft"]).lower())' \
    <<< "$release_json") != true ]]
  then
    echo "::error::El release público está incompleto: $missing_manifests" >&2
    exit 1
  fi
  echo "Release borrador: esperando manifiestos obligatorios:"
  printf '  - %s\n' $missing_manifests
  exit 0
fi

for manifest in "${required_manifests[@]}"; do
  gh release download "$TAG" \
    --dir "$TEMP_DIR" \
    --pattern "$manifest"
done

set +e
python3 - "$TEMP_DIR" "$VERSION" "$CHANNEL" "$IOS_REQUIRED" <<'PY'
import json
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

root = Path(sys.argv[1])
version = sys.argv[2]
channel = sys.argv[3]
ios_required = sys.argv[4] == "true"
release = json.loads((root / "release.json").read_text(encoding="utf-8"))
asset_names = {
    asset.get("name")
    for asset in release.get("assets", [])
    if isinstance(asset.get("name"), str)
}


def asset_name(url: object) -> str:
    if not isinstance(url, str):
        raise ValueError("manifest asset URL is missing")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "github.com":
        raise ValueError("manifest asset URL is not an official GitHub HTTPS URL")
    return unquote(PurePosixPath(parsed.path).name)


desktop = json.loads((root / "latest.json").read_text(encoding="utf-8"))
if desktop.get("version") != version:
    raise ValueError("desktop manifest version does not match the release")
expected_platforms = {
    "darwin-aarch64-app",
    "linux-x86_64-appimage",
    "linux-x86_64-deb",
    "linux-x86_64-rpm",
    "windows-x86_64-nsis",
    "windows-x86_64-msi",
}
platforms = desktop.get("platforms")
if not isinstance(platforms, dict) or set(platforms) != expected_platforms:
    raise ValueError("desktop manifest does not contain every required platform")

required_assets = {"latest.json"}
for metadata in platforms.values():
    if not isinstance(metadata, dict) or not metadata.get("signature"):
        raise ValueError("desktop updater signature is missing")
    name = asset_name(metadata.get("url"))
    required_assets.update({name, f"{name}.sig"})

dmgs = {name for name in asset_names if name.lower().endswith(".dmg")}
if len(dmgs) > 1:
    raise ValueError("release contains more than one macOS DMG")
required_assets.update(dmgs)
if not dmgs:
    required_assets.add("<one macOS DMG>")

android_name = f"android-{channel}.json"
android = json.loads((root / android_name).read_text(encoding="utf-8"))
if (
    android.get("channel") != channel
    or android.get("version_name") != version
):
    raise ValueError("Android manifest identity does not match the release")
apk = android.get("apk")
if not isinstance(apk, dict) or not apk.get("sha256"):
    raise ValueError("Android manifest APK metadata is missing")
apk_name = asset_name(apk.get("url"))
required_assets.update({android_name, apk_name, f"{apk_name}.sha256"})

if ios_required:
    ios_name = f"ios-{channel}.json"
    ios = json.loads((root / ios_name).read_text(encoding="utf-8"))
    if (
        ios.get("channel") != channel
        or ios.get("version") != version
        or not ios.get("install_url")
    ):
        raise ValueError("iOS manifest identity does not match the release")
    required_assets.add(ios_name)

missing = sorted(required_assets - asset_names)
if missing:
    print("\n".join(missing))
    raise SystemExit(3)
PY
validation_status=$?
set -e

if (( validation_status == 3 )); then
  if [[ $(python3 -c 'import json,sys; print(str(json.load(sys.stdin)["isDraft"]).lower())' \
    <<< "$release_json") != true ]]
  then
    echo "::error::El release público está incompleto." >&2
    exit 1
  fi
  echo "Release borrador: esperando assets obligatorios."
  exit 0
fi
if (( validation_status != 0 )); then
  echo "::error::Los assets del release no superaron la validación." >&2
  exit "$validation_status"
fi

is_draft=$(
  python3 -c 'import json,sys; print(str(json.load(sys.stdin)["isDraft"]).lower())' \
    <<< "$release_json"
)
if [[ "$is_draft" == true ]]; then
  gh release edit "$TAG" --draft=false
fi

public_json=$(gh release view "$TAG" --json isDraft,publishedAt)
PUBLIC_JSON="$public_json" python3 - <<'PY'
import json
import os

release = json.loads(os.environ["PUBLIC_JSON"])
if release.get("isDraft") or not release.get("publishedAt"):
    raise SystemExit("GitHub Release is not public after finalization")
PY

gh auth setup-git
apps/desktop/scripts/publish_update_channel.sh \
  "$TEMP_DIR/latest.json" \
  "$CHANNEL" \
  "$REMOTE"
apps/mobile/android/scripts/publish_update_channel.sh \
  "$TEMP_DIR/android-$CHANNEL.json" \
  "$CHANNEL" \
  "$REMOTE"
if [[ "$IOS_REQUIRED" == true ]]; then
  apps/mobile/ios/scripts/publish_update_channel.sh \
    "$TEMP_DIR/ios-$CHANNEL.json" \
    "$CHANNEL" \
    "$REMOTE"
fi

echo "Release $TAG público y canales configurados movidos."
