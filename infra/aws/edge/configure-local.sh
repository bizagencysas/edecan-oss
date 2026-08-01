#!/usr/bin/env bash
set -Eeuo pipefail

# Conecta ESTA computadora con un stack Edecán Edge ya desplegado. No imprime
# el secreto y no modifica el repositorio.

PROFILE="${AWS_PROFILE:-default}"
REGION="${AWS_REGION:-us-east-1}"
STACK_NAME="${EDECAN_EDGE_STACK:-edecan-edge}"
if [[ -n "${EDECAN_DATA_DIR:-}" ]]; then
  DATA_DIR="${EDECAN_DATA_DIR}"
else
  case "$(uname -s)" in
    Darwin)
      DATA_DIR="${HOME}/Library/Application Support/cc.edecan.desktop/data"
      ;;
    Linux)
      DATA_DIR="${XDG_DATA_HOME:-${HOME}/.local/share}/cc.edecan.desktop/data"
      ;;
    MINGW*|MSYS*|CYGWIN*)
      DATA_DIR="${APPDATA:?APPDATA no está definido}/cc.edecan.desktop/data"
      ;;
    *)
      DATA_DIR="${HOME}/.edecan/data"
      ;;
  esac
fi

STACK_JSON="$(aws cloudformation describe-stacks \
  --profile "${PROFILE}" \
  --region "${REGION}" \
  --stack-name "${STACK_NAME}" \
  --output json)"

API_URL="$(python3 -c '
import json, sys
stack = json.load(sys.stdin)["Stacks"][0]
print(next(item["OutputValue"] for item in stack["Outputs"] if item["OutputKey"] == "ApiUrl"))
' <<<"${STACK_JSON}")"

SECRET_ARN="$(python3 -c '
import json, sys
stack = json.load(sys.stdin)["Stacks"][0]
print(next(item["OutputValue"] for item in stack["Outputs"] if item["OutputKey"] == "SharedSecretArn"))
' <<<"${STACK_JSON}")"

INSTALLATION_NAME="$(python3 -c '
import json, sys
stack = json.load(sys.stdin)["Stacks"][0]
print(next(item["ParameterValue"] for item in stack["Parameters"] if item["ParameterKey"] == "InstallationName"))
' <<<"${STACK_JSON}")"

mkdir -p "${DATA_DIR}"
chmod 700 "${DATA_DIR}" 2>/dev/null || true
CONFIG_TMP="$(mktemp "${DATA_DIR}/edge-continuity.json.XXXXXX")"
SECRET_TMP="$(mktemp "${DATA_DIR}/edge-continuity.secret.XXXXXX")"
cleanup() {
  rm -f "${CONFIG_TMP}" "${SECRET_TMP}"
}
trap cleanup EXIT
chmod 600 "${CONFIG_TMP}" "${SECRET_TMP}" 2>/dev/null || true

python3 - "${CONFIG_TMP}" "${API_URL}" "${INSTALLATION_NAME}" <<'PY'
import json
import os
import sys

path, base_url, installation_id = sys.argv[1:]
with open(path, "w", encoding="utf-8") as handle:
    json.dump(
        {
            "enabled": True,
            "base_url": base_url.rstrip("/"),
            "installation_id": installation_id,
            "heartbeat_seconds": 30,
            "claim_seconds": 2,
        },
        handle,
        ensure_ascii=False,
        indent=2,
    )
    handle.write("\n")
os.chmod(path, 0o600)
PY

aws secretsmanager get-secret-value \
  --profile "${PROFILE}" \
  --region "${REGION}" \
  --secret-id "${SECRET_ARN}" \
  --query SecretString \
  --output text >"${SECRET_TMP}"
printf '\n' >>"${SECRET_TMP}"
chmod 600 "${SECRET_TMP}" 2>/dev/null || true

mv -f "${CONFIG_TMP}" "${DATA_DIR}/edge-continuity.json"
mv -f "${SECRET_TMP}" "${DATA_DIR}/edge-continuity.secret"
trap - EXIT

echo "Continuidad privada configurada en ${DATA_DIR}."
echo "Reinicia Edecán y consulta GET /v1/continuity/status desde una sesión autenticada."
