#!/usr/bin/env bash
# Publica stable.json o preview.json sin reemplazar los canales móviles.
# Cada intento parte del HEAD remoto exacto. Si otro publicador gana la carrera,
# el push non-fast-forward falla y el siguiente intento conserva su árbol.
set -u -o pipefail

if [[ $# -lt 2 || $# -gt 3 ]]; then
  echo "usage: $0 MANIFEST (stable|preview) [REMOTE]" >&2
  exit 2
fi

MANIFEST=$1
CHANNEL=$2
REMOTE=${3:-origin}
MAX_ATTEMPTS=${EDECAN_UPDATE_CHANNEL_MAX_ATTEMPTS:-5}
RETRY_DELAY_SECONDS=${EDECAN_UPDATE_CHANNEL_RETRY_DELAY_SECONDS:-2}
REMOTE_REF=refs/heads/update-channels
CHANNEL_FILE="$CHANNEL.json"
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
GENERATOR="$SCRIPT_DIR/generate-update-manifest.py"

if [[ "$CHANNEL" != stable && "$CHANNEL" != preview ]]; then
  echo "channel must be stable or preview" >&2
  exit 2
fi
if [[ ! -s "$MANIFEST" ]]; then
  echo "manifest is missing or empty: $MANIFEST" >&2
  exit 2
fi
if ! [[ "$MAX_ATTEMPTS" =~ ^[1-9][0-9]*$ ]] || (( MAX_ATTEMPTS > 10 )); then
  echo "EDECAN_UPDATE_CHANNEL_MAX_ATTEMPTS must be between 1 and 10" >&2
  exit 2
fi
if ! [[ "$RETRY_DELAY_SECONDS" =~ ^[0-9]+$ ]] || (( RETRY_DELAY_SECONDS > 30 )); then
  echo "EDECAN_UPDATE_CHANNEL_RETRY_DELAY_SECONDS must be between 0 and 30" >&2
  exit 2
fi

export GIT_AUTHOR_NAME=${GIT_AUTHOR_NAME:-github-actions}
export GIT_AUTHOR_EMAIL=${GIT_AUTHOR_EMAIL:-41898282+github-actions[bot]@users.noreply.github.com}
export GIT_COMMITTER_NAME=${GIT_COMMITTER_NAME:-$GIT_AUTHOR_NAME}
export GIT_COMMITTER_EMAIL=${GIT_COMMITTER_EMAIL:-$GIT_AUTHOR_EMAIL}

validate_candidate() {
  local current_file=${1:-}
  python3 - "$GENERATOR" "$current_file" "$MANIFEST" "$CHANNEL" <<'PY'
import importlib.util
import json
import sys
from pathlib import Path

module_path, current_path, candidate_path, channel = sys.argv[1:]
spec = importlib.util.spec_from_file_location("desktop_update_manifest", module_path)
if spec is None or spec.loader is None:
    raise SystemExit("cannot load desktop manifest validator")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
current = None
if current_path:
    current = json.loads(Path(current_path).read_text(encoding="utf-8"))
candidate = json.loads(Path(candidate_path).read_text(encoding="utf-8"))
module.validate_transition(current, candidate, expected_channel=channel)
PY
}

publish_attempt() {
  local attempt=$1
  local remote_line base index blob tree commit current_file status

  remote_line=$(git ls-remote --heads "$REMOTE" "$REMOTE_REF") || return 1
  base=${remote_line%%[[:space:]]*}
  if [[ "$base" == "$remote_line" ]]; then
    base=
  fi

  current_file=
  if [[ -n "$base" ]]; then
    git fetch --no-tags "$REMOTE" "$REMOTE_REF" || return 1
    git cat-file -e "$base^{commit}" || return 1
    if git cat-file -e "$base:$CHANNEL_FILE" 2>/dev/null; then
      current_file=$(mktemp "${TMPDIR:-/tmp}/edecan-desktop-current.XXXXXX") || return 1
      git show "$base:$CHANNEL_FILE" > "$current_file" || {
        rm -f "$current_file"
        return 1
      }
    fi
  fi

  validate_candidate "$current_file"
  status=$?
  if [[ -n "$current_file" ]]; then
    rm -f "$current_file"
  fi
  if (( status != 0 )); then
    return 2
  fi

  index=$(mktemp "${TMPDIR:-/tmp}/edecan-desktop-update-index.XXXXXX") || return 1
  rm -f "$index"
  export GIT_INDEX_FILE=$index

  if [[ -n "$base" ]]; then
    git read-tree "$base" || {
      unset GIT_INDEX_FILE
      rm -f "$index"
      return 1
    }
  else
    git read-tree --empty || {
      unset GIT_INDEX_FILE
      rm -f "$index"
      return 1
    }
  fi

  blob=$(git hash-object -w -- "$MANIFEST") || {
    unset GIT_INDEX_FILE
    rm -f "$index"
    return 1
  }
  git update-index --add --cacheinfo 100644 "$blob" "$CHANNEL_FILE" || {
    unset GIT_INDEX_FILE
    rm -f "$index"
    return 1
  }
  tree=$(git write-tree) || {
    unset GIT_INDEX_FILE
    rm -f "$index"
    return 1
  }

  if [[ -n "$base" ]]; then
    commit=$(printf 'release: move desktop %s channel\n' "$CHANNEL" |
      git commit-tree "$tree" -p "$base") || {
        unset GIT_INDEX_FILE
        rm -f "$index"
        return 1
      }
  else
    commit=$(printf 'release: initialize desktop %s channel\n' "$CHANNEL" |
      git commit-tree "$tree") || {
        unset GIT_INDEX_FILE
        rm -f "$index"
        return 1
      }
  fi

  unset GIT_INDEX_FILE
  rm -f "$index"

  if git push "$REMOTE" "$commit:$REMOTE_REF"; then
    echo "Published $CHANNEL_FILE at $commit (attempt $attempt)."
    return 0
  fi
  return 1
}

for ((attempt = 1; attempt <= MAX_ATTEMPTS; attempt += 1)); do
  publish_attempt "$attempt"
  status=$?
  if (( status == 0 )); then
    exit 0
  fi
  if (( status == 2 )); then
    echo "Invalid or regressive desktop channel transition; channel left unchanged." >&2
    exit 1
  fi
  if (( attempt < MAX_ATTEMPTS )); then
    echo "Concurrent update or transient push failure; retrying ($attempt/$MAX_ATTEMPTS)." >&2
    sleep $((attempt * RETRY_DELAY_SECONDS))
  fi
done

echo "Could not move $CHANNEL_FILE after $MAX_ATTEMPTS attempts; channel left unchanged." >&2
exit 1
