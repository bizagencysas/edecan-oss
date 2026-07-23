#!/usr/bin/env bash
# Create a shared draft release, tolerating another workflow winning the race.
set -euo pipefail

if (( $# != 3 )); then
  echo "usage: $0 TAG VERSION (stable|preview)" >&2
  exit 2
fi

TAG=$1
VERSION=$2
CHANNEL=$3
MAX_ATTEMPTS=${EDECAN_RELEASE_LOOKUP_MAX_ATTEMPTS:-5}

if [[ "$TAG" != "v$VERSION" ]]; then
  echo "error: tag $TAG does not match version $VERSION." >&2
  exit 2
fi
if [[ "$CHANNEL" != stable && "$CHANNEL" != preview ]]; then
  echo "error: channel must be stable or preview." >&2
  exit 2
fi
if ! [[ "$MAX_ATTEMPTS" =~ ^[1-9][0-9]*$ ]] || (( MAX_ATTEMPTS > 10 )); then
  echo "error: EDECAN_RELEASE_LOOKUP_MAX_ATTEMPTS must be between 1 and 10." >&2
  exit 2
fi

release_exists() {
  gh release view "$TAG" >/dev/null 2>&1
}

if ! release_exists; then
  create_arguments=(
    "$TAG"
    --verify-tag
    --draft
    --title "Edecán $VERSION"
    --generate-notes
  )
  if [[ "$CHANNEL" == preview ]]; then
    create_arguments+=(--prerelease)
  fi
  if ! gh release create "${create_arguments[@]}"
  then
    # Desktop, Android and iOS can all observe an absent release and race to
    # create it. Wait for the winner instead of failing the losing workflow.
    for ((attempt = 1; attempt <= MAX_ATTEMPTS; attempt += 1)); do
      if release_exists; then
        break
      fi
      sleep "$attempt"
    done
  fi
fi

release_json=$(gh release view "$TAG" --json tagName,isPrerelease)
EXPECTED_PRERELEASE=false
if [[ "$CHANNEL" == preview ]]; then
  EXPECTED_PRERELEASE=true
fi
RELEASE_JSON="$release_json" \
  EXPECTED_TAG="$TAG" \
  EXPECTED_PRERELEASE="$EXPECTED_PRERELEASE" \
  python3 - <<'PY'
import json
import os

release = json.loads(os.environ["RELEASE_JSON"])
if release.get("tagName") != os.environ["EXPECTED_TAG"]:
    raise SystemExit("GitHub Release tag does not match the requested tag")
if bool(release.get("isPrerelease")) != (
    os.environ["EXPECTED_PRERELEASE"] == "true"
):
    raise SystemExit("GitHub Release prerelease state does not match the channel")
PY
