#!/usr/bin/env bash
# Instala un nodo personal de Edecán con HTTPS. No usa cuentas ni servidores
# del proyecto: cada persona conserva su dominio, sus datos y sus claves.

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." &>/dev/null && pwd)"
cd "${REPO_ROOT}"

ENV_FILE="${REPO_ROOT}/.env.online"
BASE_COMPOSE="infra/docker/compose.selfhost.yml"
ONLINE_COMPOSE="infra/docker/compose.online.yml"
ENV_REFERENCE="${REPO_ROOT}/.env.example"

usage() {
  cat <<'EOF'
Uso:
  scripts/instalar-online.sh --dominio edecan.tudominio.com [--email tu@email.com]

Requisitos:
  - Un servidor Linux con Docker Compose.
  - Los puertos 80 y 443 abiertos.
  - Un registro DNS A/AAAA del dominio apuntando a ese servidor.

El instalador crea .env.online con permisos 600, genera secretos únicos,
levanta Postgres, Redis, almacenamiento, API, worker, web y HTTPS, y valida
el nodo. Nunca envía claves a los servidores del proyecto.
EOF
}

DOMAIN=""
EMAIL=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dominio)
      DOMAIN="${2:-}"
      shift 2
      ;;
    --email)
      EMAIL="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Opción desconocida: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [ -z "${DOMAIN}" ]; then
  printf 'Falta --dominio.\n\n' >&2
  usage >&2
  exit 2
fi
if ! printf '%s' "${DOMAIN}" | grep -Eq '^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$'; then
  printf 'El dominio no tiene un formato válido: %s\n' "${DOMAIN}" >&2
  exit 2
fi
if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  printf 'Docker Compose v2 no está disponible.\n' >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  printf 'Docker está instalado, pero el daemon no responde.\n' >&2
  exit 1
fi
if [ ! -f "${ENV_REFERENCE}" ]; then
  printf 'No se encontró %s\n' "${ENV_REFERENCE}" >&2
  exit 1
fi

if [ ! -f "${ENV_FILE}" ]; then
  cp "${ENV_REFERENCE}" "${ENV_FILE}"
  chmod 600 "${ENV_FILE}"
else
  chmod 600 "${ENV_FILE}"
fi

upsert_env() {
  key="$1"
  value="$2"
  tmp_file="${ENV_FILE}.tmp"
  awk -v key="${key}" -v value="${value}" '
    BEGIN { replaced = 0 }
    $0 ~ ("^" key "=") {
      if (!replaced) print key "=" value
      replaced = 1
      next
    }
    { print }
    END { if (!replaced) print key "=" value }
  ' "${ENV_FILE}" > "${tmp_file}"
  mv "${tmp_file}" "${ENV_FILE}"
  chmod 600 "${ENV_FILE}"
}

read_env() {
  key="$1"
  awk -F= -v key="${key}" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' "${ENV_FILE}"
}

generate_urlsafe() {
  python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
}

generate_fernet() {
  python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())' \
    2>/dev/null || python3 -c \
    'import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())'
}

upsert_env EDECAN_DOMAIN "${DOMAIN}"
upsert_env ACME_EMAIL "${EMAIL}"
upsert_env EDECAN_ENV_FILE "../../.env.online"
upsert_env ENV "prod"
upsert_env PUBLIC_BASE_URL "https://${DOMAIN}"
upsert_env WEB_BASE_URL "https://${DOMAIN}"
upsert_env NEXT_PUBLIC_API_URL "https://${DOMAIN}"
upsert_env DATABASE_URL "postgresql+asyncpg://edecan:$(read_env POSTGRES_PASSWORD)@postgres:5432/edecan"
upsert_env REDIS_URL "redis://redis:6379/0"
upsert_env AWS_ENDPOINT_URL "http://localstack:4566"
upsert_env AWS_REGION "us-east-1"
upsert_env AWS_ACCESS_KEY_ID "test"
upsert_env AWS_SECRET_ACCESS_KEY "test"
upsert_env S3_BUCKET "edecan-files"
upsert_env SQS_QUEUE_URL "http://localstack:4566/000000000000/edecan-jobs"

current_jwt="$(read_env JWT_SECRET)"
if [ -z "${current_jwt}" ] || [ "${current_jwt}" = "TU_JWT_SECRET_AQUI" ]; then
  upsert_env JWT_SECRET "$(generate_urlsafe)"
fi
current_master="$(read_env LOCAL_MASTER_KEY)"
if [ -z "${current_master}" ] || [ "${current_master}" = "TU_LOCAL_MASTER_KEY_FERNET_AQUI" ]; then
  upsert_env LOCAL_MASTER_KEY "$(generate_fernet)"
fi
current_postgres="$(read_env POSTGRES_PASSWORD)"
if [ -z "${current_postgres}" ] || [ "${current_postgres}" = "edecan" ]; then
  current_postgres="$(generate_urlsafe)"
  upsert_env POSTGRES_PASSWORD "${current_postgres}"
  upsert_env DATABASE_URL "postgresql+asyncpg://edecan:${current_postgres}@postgres:5432/edecan"
fi

compose() {
  docker compose \
    --env-file "${ENV_FILE}" \
    -p edecan-online \
    --profile local-aws \
    -f "${BASE_COMPOSE}" \
    -f "${ONLINE_COMPOSE}" \
    "$@"
}

printf 'Levantando Edecán en https://%s ...\n' "${DOMAIN}"
compose up -d --build

printf 'Esperando la verificación HTTPS'
attempt=0
until curl --fail --silent --show-error "https://${DOMAIN}/healthz" >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "${attempt}" -ge 60 ]; then
    printf '\nEl nodo no respondió tras 5 minutos. Revisa DNS, puertos y logs:\n' >&2
    printf '  scripts/instalar-online.sh --dominio %s --email %s\n' "${DOMAIN}" "${EMAIL}" >&2
    printf '  docker compose --env-file .env.online -p edecan-online --profile local-aws -f %s -f %s logs\n' "${BASE_COMPOSE}" "${ONLINE_COMPOSE}" >&2
    exit 1
  fi
  printf '.'
  sleep 5
done

printf '\nEdecán está online: https://%s\n' "${DOMAIN}"
printf 'Conecta el teléfono usando el QR de ese Edecán. Chat, llamadas, memoria,\n'
printf 'recordatorios y notificaciones seguirán activos aunque la computadora se apague.\n'
