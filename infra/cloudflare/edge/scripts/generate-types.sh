#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." &>/dev/null && pwd)"
DEV_VARS="${PROJECT_DIR}/.dev.vars"

cleanup() {
  rm -f -- "${DEV_VARS}"
}
trap cleanup EXIT

cp -- "${PROJECT_DIR}/.dev.vars.example" "${DEV_VARS}"
cd -- "${PROJECT_DIR}"
npx wrangler types
