#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../../.." &>/dev/null && pwd)"
cd "${REPO_ROOT}"

PROFILE="${AWS_PROFILE:-default}"
REGION="${AWS_REGION:-us-east-1}"
STACK_NAME="${EDECAN_EDGE_STACK:-edecan-edge}"
INSTALLATION_NAME="${EDECAN_INSTALLATION_NAME:-edecan-personal}"
MODEL_ID="${EDECAN_OFFLINE_MODEL_ID:-amazon.nova-2-lite-v1:0}"
ALLOWED_ORIGIN="${EDECAN_ALLOWED_ORIGIN:-}"

if [[ -z "${ALLOWED_ORIGIN}" ]]; then
  echo "Falta EDECAN_ALLOWED_ORIGIN. Usa el origen HTTPS exacto de tu puerta privada." >&2
  exit 64
fi
if [[ ! "${ALLOWED_ORIGIN}" =~ ^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?$ ]]; then
  echo "EDECAN_ALLOWED_ORIGIN debe ser un origen HTTPS sin ruta, query ni fragmento." >&2
  exit 64
fi
if [[ ! "${INSTALLATION_NAME}" =~ ^[a-z0-9-]+$ ]]; then
  echo "EDECAN_INSTALLATION_NAME solo admite minúsculas, números y guiones." >&2
  exit 64
fi

ACCOUNT_ID="$(aws sts get-caller-identity --profile "${PROFILE}" --query Account --output text)"
DEPLOYMENT_BUCKET="${EDECAN_DEPLOYMENT_BUCKET:-edecan-edge-deploy-${ACCOUNT_ID}-${REGION}}"
BUILD_DIR="$(mktemp -d "${TMPDIR:-/tmp}/edecan-edge.XXXXXX")"
trap 'rm -rf "${BUILD_DIR}"' EXIT

if ! aws s3api head-bucket --profile "${PROFILE}" --bucket "${DEPLOYMENT_BUCKET}" 2>/dev/null; then
  if [ "${REGION}" = "us-east-1" ]; then
    aws s3api create-bucket --profile "${PROFILE}" --region "${REGION}" \
      --bucket "${DEPLOYMENT_BUCKET}" >/dev/null
  else
    aws s3api create-bucket --profile "${PROFILE}" --region "${REGION}" \
      --bucket "${DEPLOYMENT_BUCKET}" \
      --create-bucket-configuration "LocationConstraint=${REGION}" >/dev/null
  fi
  aws s3api put-public-access-block --profile "${PROFILE}" \
    --bucket "${DEPLOYMENT_BUCKET}" \
    --public-access-block-configuration \
      BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
  aws s3api put-bucket-encryption --profile "${PROFILE}" \
    --bucket "${DEPLOYMENT_BUCKET}" \
    --server-side-encryption-configuration \
      '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
fi

cp "${SCRIPT_DIR}/src/handler.py" "${BUILD_DIR}/handler.py"
(
  cd "${BUILD_DIR}"
  zip -q edge.zip handler.py
)
ARTIFACT_HASH="$(shasum -a 256 "${BUILD_DIR}/edge.zip" | awk '{print $1}')"
DEPLOYMENT_KEY="edge/${ARTIFACT_HASH}.zip"
aws s3 cp --profile "${PROFILE}" --region "${REGION}" \
  "${BUILD_DIR}/edge.zip" "s3://${DEPLOYMENT_BUCKET}/${DEPLOYMENT_KEY}" \
  --only-show-errors

aws cloudformation deploy \
  --profile "${PROFILE}" \
  --region "${REGION}" \
  --stack-name "${STACK_NAME}" \
  --template-file "${SCRIPT_DIR}/template.yml" \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    "DeploymentBucket=${DEPLOYMENT_BUCKET}" \
    "DeploymentKey=${DEPLOYMENT_KEY}" \
    "InstallationName=${INSTALLATION_NAME}" \
    "OfflineModelId=${MODEL_ID}" \
    "AllowedOrigin=${ALLOWED_ORIGIN}" \
  --tags app=edecan layer=edge managed-by=cloudformation

aws cloudformation describe-stacks \
  --profile "${PROFILE}" \
  --region "${REGION}" \
  --stack-name "${STACK_NAME}" \
  --query 'Stacks[0].Outputs' \
  --output table
