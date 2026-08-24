#!/usr/bin/env bash
# Upload GitHub Release assets without ever replacing published bytes.
#
# Re-running a release is safe:
# - an absent asset is uploaded;
# - an existing byte-identical asset is accepted;
# - an existing asset with the same name but different bytes fails closed.
# A failed upload is re-checked once to handle another workflow winning a race.
set -euo pipefail

if (( $# < 2 )); then
  echo "usage: $0 TAG FILE [FILE ...]" >&2
  exit 2
fi

TAG=$1
shift
RACE_LOOKUP_ATTEMPTS=${EDECAN_RELEASE_ASSET_RACE_LOOKUP_ATTEMPTS:-5}

command -v gh >/dev/null 2>&1 || {
  echo "error: gh is required." >&2
  exit 2
}
command -v python3 >/dev/null 2>&1 || {
  echo "error: python3 is required." >&2
  exit 2
}
command -v cmp >/dev/null 2>&1 || {
  echo "error: cmp is required." >&2
  exit 2
}
command -v sha256sum >/dev/null 2>&1 || {
  echo "error: sha256sum is required." >&2
  exit 2
}
if ! [[ "$RACE_LOOKUP_ATTEMPTS" =~ ^[1-9][0-9]*$ ]] ||
  (( RACE_LOOKUP_ATTEMPTS > 10 ))
then
  echo "error: EDECAN_RELEASE_ASSET_RACE_LOOKUP_ATTEMPTS must be between 1 and 10." >&2
  exit 2
fi

TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/edecan-release-assets.XXXXXX")
trap 'rm -rf "$TEMP_DIR"' EXIT

asset_api_url() {
  local asset_name=$1
  local assets_json
  assets_json=$(gh release view "$TAG" --json assets)
  ASSET_NAME="$asset_name" python3 -c '
import json
import os
import sys

assets = json.load(sys.stdin).get("assets", [])
matches = [
    asset.get("apiUrl")
    for asset in assets
    if asset.get("name") == os.environ["ASSET_NAME"]
]
if len(matches) > 1:
    raise SystemExit("release contains duplicate asset names")
if matches:
    api_url = matches[0]
    if not isinstance(api_url, str) or not api_url.startswith(
        "https://api.github.com/repos/"
    ):
        raise SystemExit("release asset has an invalid API URL")
    print(api_url)
' <<< "$assets_json"
}

remote_asset_matches() {
  local local_file=$1
  local remote_url=$2
  local remote_file="$TEMP_DIR/remote-asset"

  gh api \
    --method GET \
    -H "Accept: application/octet-stream" \
    "$remote_url" > "$remote_file"

  if cmp -s "$local_file" "$remote_file"; then
    return 0
  fi

  echo "::error::El asset $(basename "$local_file") ya existe con bytes distintos." >&2
  echo "Local SHA-256:  $(sha256sum "$local_file" | cut -d ' ' -f 1)" >&2
  echo "Remoto SHA-256: $(sha256sum "$remote_file" | cut -d ' ' -f 1)" >&2
  return 1
}

for local_file in "$@"; do
  if [[ ! -s "$local_file" ]]; then
    echo "error: release asset is missing or empty: $local_file" >&2
    exit 2
  fi
  asset_name=$(basename "$local_file")
  if [[ "$asset_name" == *$'\n'* || "$asset_name" == *$'\r'* ]]; then
    echo "error: release asset name contains a newline: $asset_name" >&2
    exit 2
  fi
  if [[ "$asset_name" == *"#"* ]]; then
    echo "error: '#' is not allowed in a GitHub Release asset name." >&2
    exit 2
  fi

  remote_url=$(asset_api_url "$asset_name")
  if [[ -n "$remote_url" ]]; then
    remote_asset_matches "$local_file" "$remote_url"
    echo "Asset ya publicado e idéntico: $asset_name"
    continue
  fi

  if gh release upload "$TAG" "$local_file"; then
    echo "Asset publicado: $asset_name"
    continue
  fi

  # A concurrent publisher can create the same asset between the lookup and
  # upload. Accept that race only if GitHub now serves the exact same bytes.
  remote_url=
  for ((attempt = 1; attempt <= RACE_LOOKUP_ATTEMPTS; attempt += 1)); do
    remote_url=$(asset_api_url "$asset_name")
    if [[ -n "$remote_url" ]]; then
      remote_asset_matches "$local_file" "$remote_url"
      echo "Asset publicado en paralelo e idéntico: $asset_name"
      break
    fi
    if (( attempt < RACE_LOOKUP_ATTEMPTS )); then
      sleep "$attempt"
    fi
  done
  if [[ -z "$remote_url" ]]; then
    echo "::error::No se pudo publicar $asset_name y GitHub no expone el asset." >&2
    exit 1
  fi
done
