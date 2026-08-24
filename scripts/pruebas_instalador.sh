#!/usr/bin/env bash
# ============================================================================
# Edecán — pruebas del instalador de self-host.
#
# Harness bash puro (sin dependencias) que corre scripts/instalar-selfhost.sh
# dentro de SANDBOXES temporales (bajo $TMPDIR, nunca dentro de este repo) con
# un `docker` FALSO adelante en el PATH: nunca toca Docker real, nunca crea
# contenedores ni redes de verdad y nunca llama a internet. Ver
# docs/self-hosting.md para el contrato público del instalador.
#
# Uso:
#   scripts/pruebas_instalador.sh
#
# Sale 0 si las 6 pruebas pasan, 1 si alguna falla (imprime un resumen final
# con el detalle de qué falló). Limpia siempre sus sandboxes temporales,
# incluso si el propio harness falla a mitad de camino (trap EXIT).
#
# Si tienes `shellcheck` instalado, este harness también lo corre como
# chequeo extra (no cuenta para las "6 pruebas" — es un
# bonus). Si no lo tienes, no se instala ni se exige: se omite en silencio.
# ============================================================================

set -uo pipefail
# Nota: a propósito SIN "-e". Este harness hace asserts manuales que esperan
# comandos con salida != 0 en varios casos (p. ej. --help debe salir 0, pero
# el rechazo de Windows nativo debe salir != 0) — "set -e" abortaría el
# harness entero en el primer assert "negativo" en vez de dejarlo reportar el
# resultado y seguir con la siguiente prueba.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
INSTALADOR="${SCRIPT_DIR}/instalar-selfhost.sh"

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_GREEN=$'\033[32m'; C_RED=$'\033[31m'
else
  C_RESET=""; C_BOLD=""; C_GREEN=""; C_RED=""
fi

PASADAS=0
FALLADAS=0
SANDBOXES=()

cleanup() {
  local dir
  for dir in "${SANDBOXES[@]+"${SANDBOXES[@]}"}"; do
    rm -rf "${dir}"
  done
}
trap cleanup EXIT

ok() {
  PASADAS=$((PASADAS + 1))
  printf '  %s[PASS]%s %s\n' "${C_GREEN}" "${C_RESET}" "$1"
}

fail() {
  FALLADAS=$((FALLADAS + 1))
  printf '  %s[FAIL]%s %s\n' "${C_RED}" "${C_RESET}" "$1"
  [ -n "${2:-}" ] && printf '         %s\n' "$2"
}

# --- Sandbox: "repo" mínimo con lo justo para que el instalador no aborte
# por archivos faltantes, más un `docker` falso en PATH (bin/docker). --------
crear_sandbox() {
  local dir
  dir="$(mktemp -d "${TMPDIR:-/tmp}/edecan-pruebas-instalador.XXXXXX")"
  mkdir -p "${dir}/scripts" "${dir}/infra/docker" "${dir}/bin"
  cp "${INSTALADOR}" "${dir}/scripts/instalar-selfhost.sh"
  chmod +x "${dir}/scripts/instalar-selfhost.sh"

  cat > "${dir}/.env.example" <<'EOF'
# .env.example minimo del SANDBOX de pruebas -- no es el .env.example real
# del repo, solo trae las claves que instalar-selfhost.sh toca.
JWT_SECRET=TU_JWT_SECRET_AQUI
LOCAL_MASTER_KEY=TU_LOCAL_MASTER_KEY_FERNET_AQUI
DATABASE_URL=postgresql+asyncpg://edecan:edecan@localhost:5432/edecan
REDIS_URL=redis://localhost:6379/0
AWS_ENDPOINT_URL=
SQS_QUEUE_URL=
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
S3_BUCKET=
AWS_REGION=
EOF

  # El instalador solo verifica que este archivo EXISTA (nunca lo interpreta
  # -- "docker" es el stub de abajo), así que un placeholder alcanza.
  echo "# stub de compose.selfhost.yml para scripts/pruebas_instalador.sh" \
    > "${dir}/infra/docker/compose.selfhost.yml"

  cat > "${dir}/bin/docker" <<EOF
#!/usr/bin/env bash
# Stub de 'docker' para scripts/pruebas_instalador.sh -- NUNCA toca Docker
# real. Loggea cada invocación (para que las pruebas puedan verificar qué se
# llamó) y responde de forma plausible a los subcomandos que
# instalar-selfhost.sh necesita ('compose version[, --short]', 'info').
# Cualquier otra invocación ('compose ... up -d --build', etc.) es un éxito
# silencioso -- este stub no reimplementa Docker, solo simula que "funcionó".
echo "docker \$*" >> "${dir}/docker-stub.log"
case "\${1:-}" in
  compose)
    if [ "\${2:-}" = "version" ]; then
      if [ "\${3:-}" = "--short" ]; then
        echo "2.29.7"
      else
        echo "Docker Compose version v2.29.7"
      fi
    fi
    exit 0
    ;;
  info) exit 0 ;;
esac
exit 0
EOF
  chmod +x "${dir}/bin/docker"

  printf '%s' "${dir}"
}

# Variante de sandbox para la prueba 5: agrega un 'uname' falso que simula
# Windows nativo (Git Bash / MSYS2), sin tocar el resto del comportamiento.
crear_sandbox_windows() {
  local dir
  dir="$(crear_sandbox)"
  cat > "${dir}/bin/uname" <<'EOF'
#!/usr/bin/env bash
# Shim de 'uname' para scripts/pruebas_instalador.sh -- simula Windows
# nativo (Git Bash/MSYS2/Cygwin) sin importar en qué máquina real corra esto.
case "${1:-}" in
  -m) echo "x86_64" ;;
  *)  echo "MINGW64_NT-10.0-19045" ;;
esac
EOF
  chmod +x "${dir}/bin/uname"
  printf '%s' "${dir}"
}

# Corre el instalador DENTRO del sandbox, con bin/ del sandbox primero en
# PATH (docker-falso, y a veces uname-falso) -- el resto del PATH real sigue
# disponible (necesitamos python3/openssl/grep/awk/etc. de verdad; ninguno de
# ellos toca red ni nada externo por sí solo).
correr_instalador() {
  local dir="$1"; shift
  ( cd "${dir}" && PATH="${dir}/bin:${PATH}" "${dir}/scripts/instalar-selfhost.sh" "$@" )
}

echo "${C_BOLD}== scripts/pruebas_instalador.sh ==${C_RESET}"
echo

# ============================================================================
# [1/6] --help sale 0
# ============================================================================
echo "[1/6] --help sale con código 0"
dir1="$(crear_sandbox)"; SANDBOXES+=("${dir1}")
salida1="$(correr_instalador "${dir1}" --help 2>&1)"; codigo1=$?
if [ "${codigo1}" -eq 0 ] && printf '%s' "${salida1}" | grep -q -- "--dry-run"; then
  ok "--help sale 0 y documenta --dry-run"
else
  fail "--help debía salir 0 y mencionar --dry-run" "código=${codigo1}"
fi

# ============================================================================
# [2/6] --dry-run --no-interactive no deja rastro real
# ============================================================================
echo "[2/6] --dry-run --no-interactive no crea .env real ni llama a 'docker ... up'"
dir2="$(crear_sandbox)"; SANDBOXES+=("${dir2}")
salida2="$(correr_instalador "${dir2}" --dry-run --no-interactive 2>&1)"; codigo2=$?
problema=""
[ "${codigo2}" -eq 0 ] || problema="${problema}código=${codigo2}. "
[ -f "${dir2}/.env" ] && problema="${problema}se creó un .env real. "
[ -f "${dir2}/.edecan-install-state" ] && problema="${problema}se creó el state-file. "
[ -f "${dir2}/.edecan-install.log" ] && problema="${problema}se creó el log-file. "
if [ -f "${dir2}/docker-stub.log" ] && grep -Eq '(^| )up( |$)' "${dir2}/docker-stub.log"; then
  problema="${problema}se invocó 'docker ... up'. "
fi
if [ -z "${problema}" ]; then
  ok "--dry-run --no-interactive: sin .env, sin state-file, sin log, sin 'docker ... up'"
else
  fail "--dry-run --no-interactive dejó rastro real" "${problema}(salida: ${salida2})"
fi

# ============================================================================
# [3/6] Corrida completa --no-interactive contra el stub
# ============================================================================
echo "[3/6] Corrida --no-interactive completa crea .env con secretos generados"
dir34="$(crear_sandbox)"; SANDBOXES+=("${dir34}")
salida3="$(correr_instalador "${dir34}" --no-interactive 2>&1)"; codigo3=$?
problema=""
[ "${codigo3}" -eq 0 ] || problema="${problema}código=${codigo3}. "
if [ ! -f "${dir34}/.env" ]; then
  problema="${problema}no se creó .env. "
else
  jwt="$(grep -m1 '^JWT_SECRET=' "${dir34}/.env" | cut -d= -f2-)"
  mk="$(grep -m1 '^LOCAL_MASTER_KEY=' "${dir34}/.env" | cut -d= -f2-)"
  { [ -z "${jwt}" ] || [ "${jwt}" = "TU_JWT_SECRET_AQUI" ]; } && problema="${problema}JWT_SECRET no se generó (sigue vacío/placeholder). "
  { [ -z "${mk}" ] || [ "${mk}" = "TU_LOCAL_MASTER_KEY_FERNET_AQUI" ]; } && problema="${problema}LOCAL_MASTER_KEY no se generó (sigue vacío/placeholder). "
fi
[ -f "${dir34}/.edecan-install-state" ] || problema="${problema}no se creó el state-file. "
if [ -z "${problema}" ]; then
  ok "Corrida completa: .env con secretos reales (≠ placeholder) + state-file presente"
else
  fail "Corrida completa dejó algo incompleto" "${problema}(salida: ${salida3})"
fi

# ============================================================================
# [4/6] Segunda corrida: salta pasos y no regenera secretos
# ============================================================================
echo "[4/6] Segunda corrida salta pasos ya hechos y NO regenera secretos"
if [ -f "${dir34}/.env" ]; then
  cp "${dir34}/.env" "${dir34}/.env.despues-de-1a-corrida"
  salida4="$(correr_instalador "${dir34}" --no-interactive 2>&1)"; codigo4=$?
  problema=""
  [ "${codigo4}" -eq 0 ] || problema="${problema}código=${codigo4}. "
  if ! diff -q "${dir34}/.env" "${dir34}/.env.despues-de-1a-corrida" >/dev/null 2>&1; then
    problema="${problema}.env cambió entre la 1a y la 2a corrida. "
  fi
  if ! printf '%s' "${salida4}" | grep -q "ya hecho"; then
    problema="${problema}la 2a corrida no reportó ningún paso como 'ya hecho'. "
  fi
  if [ -z "${problema}" ]; then
    ok "Segunda corrida: .env sin cambios (mismos secretos) + pasos saltados"
  else
    fail "Segunda corrida no fue idempotente" "${problema}(salida: ${salida4})"
  fi
else
  fail "Segunda corrida: no se pudo probar porque la prueba 3 no dejó un .env"
fi

# ============================================================================
# [5/6] Rechazo temprano de Windows nativo
# ============================================================================
echo "[5/6] Simulación de Windows nativo (uname shim) sale != 0 y menciona WSL2"
dir5="$(crear_sandbox_windows)"; SANDBOXES+=("${dir5}")
salida5="$(correr_instalador "${dir5}" --no-interactive 2>&1)"; codigo5=$?
if [ "${codigo5}" -ne 0 ] && printf '%s' "${salida5}" | grep -qi "WSL2"; then
  ok "Windows nativo: código ${codigo5} (!= 0), menciona WSL2"
else
  fail "Se esperaba código != 0 y mención de WSL2" "código=${codigo5} salida=${salida5}"
fi
# No debió llegar a tocar nada -- ni .env ni el state-file.
if [ -f "${dir5}/.env" ] || [ -f "${dir5}/.edecan-install-state" ]; then
  fail "El rechazo de Windows nativo debía detenerse ANTES de escribir nada" ""
fi

# ============================================================================
# [6/6] bash -n sobre el instalador
# ============================================================================
echo "[6/6] bash -n limpio sobre instalar-selfhost.sh"
err_inst="$(bash -n "${INSTALADOR}" 2>&1)"; cod_inst=$?
if [ "${cod_inst}" -eq 0 ]; then
  ok "bash -n limpio en el instalador"
else
  fail "bash -n encontró errores de sintaxis" "instalar-selfhost.sh: ${err_inst}"
fi

# ============================================================================
# Bonus (no cuenta para las 6 pruebas): shellcheck, solo si ya está instalado.
# ============================================================================
echo
if command -v shellcheck >/dev/null 2>&1; then
  echo "${C_BOLD}[bonus] shellcheck${C_RESET}"
  shellcheck "${INSTALADOR}" "${SCRIPT_DIR}/pruebas_instalador.sh" || true
else
  echo "${C_BOLD}[bonus] shellcheck${C_RESET}: no está instalado, se omite (no se instala automáticamente)."
fi

# ============================================================================
# Resumen
# ============================================================================
echo
echo "${C_BOLD}== Resumen: ${PASADAS} pasadas, ${FALLADAS} falladas ==${C_RESET}"
if [ "${FALLADAS}" -eq 0 ]; then
  exit 0
else
  exit 1
fi
