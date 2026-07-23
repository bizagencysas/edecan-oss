#!/usr/bin/env bash
# Move one Android manifest on update-channels without replacing any desktop
# manifest. Every push is based on the exact remote head observed in that
# attempt; a concurrent publisher causes a non-fast-forward and a bounded
# retry against the new head.
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
CHANNEL_FILE="android-$CHANNEL.json"

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

publish_attempt() {
  local attempt=$1
  local remote_line base index blob tree commit

  remote_line=$(git ls-remote --heads "$REMOTE" "$REMOTE_REF") || return 1
  base=${remote_line%%[[:space:]]*}
  if [[ "$base" == "$remote_line" ]]; then
    # Empty output means this is the first channel publication.
    base=
  fi

  if [[ -n "$base" ]]; then
    git fetch --no-tags "$REMOTE" "$REMOTE_REF" || return 1
    git cat-file -e "$base^{commit}" || return 1
  fi

  index=$(mktemp "${TMPDIR:-/tmp}/edecan-update-index.XXXXXX") || return 1
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
    commit=$(printf 'release: move Android %s channel\n' "$CHANNEL" |
      git commit-tree "$tree" -p "$base") || {
        unset GIT_INDEX_FILE
        rm -f "$index"
        return 1
      }
  else
    commit=$(printf 'release: initialize Android %s channel\n' "$CHANNEL" |
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
  if publish_attempt "$attempt"; then
    exit 0
  fi
  if (( attempt < MAX_ATTEMPTS )); then
    echo "Concurrent update or transient push failure; retrying ($attempt/$MAX_ATTEMPTS)." >&2
    sleep $((attempt * RETRY_DELAY_SECONDS))
  fi
done

echo "Could not move $CHANNEL_FILE after $MAX_ATTEMPTS attempts; channel left unchanged." >&2
exit 1
