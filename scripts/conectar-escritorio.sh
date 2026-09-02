#!/usr/bin/env bash
# Conecta ESTA computadora (Mac / Windows-Git-Bash / Linux) como companion del
# Edecán-NUBE y la deja controlable desde TU iPhone por el túnel público.
#
# Cada persona usa SU cuenta (correo/clave) -> su token -> SU computadora
# queda asociada a SU tenant. El iPhone entra con la misma cuenta y, desde la
# pestaña Remoto, ve y controla ESTA máquina aunque esté en otra red.
#
# IMPORTANTE: correlo en una TERMINAL y déjala abierta. Las acciones de
# control (mouse/teclado) piden tu aprobación LOCAL aquí mismo (por diseño,
# nunca auto-aprueban input remoto).
#
# Uso:
#   bash scripts/conectar-escritorio.sh
#   bash scripts/conectar-escritorio.sh https://edecan.example.com CORREO CLAVE
#   SERVER=... CORREO=... CLAVE=... bash scripts/conectar-escritorio.sh
set -euo pipefail

SERVER="${SERVER:-${1:-https://edecan.example.com}}"
CORREO="${CORREO:-${2:-}}"
CLAVE="${CLAVE:-${3:-}}"

if [ -z "$CORREO" ]; then
  read -rp "Correo de tu cuenta Edecán: " CORREO
fi
if [ -z "$CLAVE" ]; then
  read -rsp "Clave: " CLAVE
  echo
fi

if [ ! -f pyproject.toml ]; then
  echo "ERROR: corre esto desde la raíz del repo Edecán."
  exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: falta uv (curl -LsSf https://astral.sh/uv/install.sh | sh)"
  exit 1
fi

echo "Conectando $SERVER con $CORREO ..."
CODE=$(uv run --all-packages python - "$SERVER" "$CORREO" "$CLAVE" <<'PY'
import sys, httpx
server, correo, clave = sys.argv[1], sys.argv[2], sys.argv[3]
r = httpx.post(server + "/v1/auth/login", json={"email": correo, "password": clave}, timeout=30)
r.raise_for_status()
tok = r.json()["access_token"]
r = httpx.post(server + "/v1/companion/pair-code", json={}, headers={"Authorization": f"Bearer {tok}"}, timeout=30)
r.raise_for_status()
print(r.json().get("code") or r.json().get("pair_code") or "")
PY
)

if [ -z "$CODE" ]; then
  echo "ERROR: no obtuve el pair-code. Revisa correo/clave y que el servidor responda."
  exit 1
fi

echo "Tu computadora queda controlable desde tu iPhone (pestaña Remoto)."
echo "Deja esta ventana abierta. Ctrl+C para desconectar."
uv run --all-packages python -m edecan_companion --server "$SERVER" --code "$CODE"