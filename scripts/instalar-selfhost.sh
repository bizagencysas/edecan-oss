#!/usr/bin/env bash
# ============================================================================
# Edecán — instalador guiado de self-host con Docker Compose, EN UN COMANDO.
#
# Qué hace (ver docs/self-hosting.md §2 para el detalle completo):
#   1. Verifica tu sistema (macOS/Linux/WSL2, arquitectura), que tengas
#      docker + el plugin `docker compose` (v2, con el daemon respondiendo),
#      y que haya espacio en disco razonable (aviso, no bloqueante).
#   2. Copia .env.example -> .env si todavía no existe (nunca pisa uno real).
#   3. Genera JWT_SECRET y LOCAL_MASTER_KEY si siguen en su placeholder de
#      .env.example — NUNCA toca un valor que ya hayas puesto tú.
#   4. Apunta DATABASE_URL/REDIS_URL a los nombres de servicio de
#      infra/docker/compose.selfhost.yml (postgres/redis, no localhost).
#   5. Te pregunta si quieres levantar LocalStack (profile "local-aws") para
#      que S3/SQS funcionen sin cuenta AWS — o usa --local-aws/--sin-local-aws
#      para no responder nada.
#   6. Imprime SIEMPRE un resumen de lo que va a hacer y pide confirmación
#      antes del ÚNICO paso con efecto real: `docker compose ... up -d --build`.
#
# Reanudable: cada paso de INSTALACIÓN (no las verificaciones del entorno —
# esas se revalidan siempre, en cada corrida) queda marcado en
# ".edecan-install-state" en la raíz del repo. Si el script se interrumpe
# (Ctrl+C, un error real), vuelve a correr el MISMO comando y retoma justo
# donde se quedó, sin repetir lo ya hecho ni volver a preguntarte lo mismo.
# Toda la salida además queda anexada en ".edecan-install.log" (útil para
# pegar el error si pides ayuda, o para que este script se cite a sí mismo
# al fallar — ver _on_install_error más abajo).
#
# Uso:
#   scripts/instalar-selfhost.sh [opciones]
#
# Opciones:
#   --local-aws        Usa el profile local-aws (LocalStack) sin preguntar.
#   --sin-local-aws    NO uses LocalStack — vas a traer tu propia cuenta AWS
#                      real (rellena AWS_* en .env tú mismo, ver el resumen).
#   --no-interactive   No hace NINGUNA pregunta ni pide confirmación final:
#                      usa el flag de arriba si lo diste, o por defecto activa
#                      local-aws (para que todo funcione sin cuenta AWS salvo
#                      que pidas explícitamente --sin-local-aws).
#   --force            Ignora ".edecan-install-state" y vuelve a correr TODOS
#                      los pasos desde cero. No debilita ninguna protección:
#                      un .env/secreto que ya tengas sigue sin tocarse — esa
#                      garantía vive en cada paso, no en el estado guardado.
#   --dry-run          Recorre TODOS los pasos e imprime exactamente qué
#                      haría (incluido el comando final de docker compose),
#                      SIN escribir .env/estado/log ni ejecutar nada con
#                      efecto real. Pensado para probar este script y para
#                      que audites qué va a hacer antes de correrlo de verdad.
#   -h, --help         Muestra esta ayuda y sale.
#
# Idempotente: puedes correrlo varias veces. Nunca pisa un .env ya creado,
# nunca regenera un secreto que ya tenga un valor propio, y "up -d --build"
# es seguro de repetir (Docker reconstruye solo lo que cambió).
#
# Este script se ESCRIBE y se revisa como cualquier otro código — igual que
# el resto de infra/, no lo ejecuta automáticamente ningún agente ni pipeline
# de este repositorio (ver infra/README.md). Lo corres TÚ, a mano, cuando
# quieras instalar de verdad.
#
# El sistema de pasos reanudables (state-file + trap de error con "cómo
# reanudar") y varias de las verificaciones de entorno de abajo (detección
# de OS/arquitectura, rechazo temprano de Windows nativo con guía a WSL2,
# mensajes de instalación de Docker por sistema operativo, chequeo de
# espacio en disco) son una adaptación propia del instalador de
# open-jarvis/OpenJarvis (Apache-2.0, scripts/install/install.sh) a este
# script de Docker Compose — ver NOTICE para la atribución completa. A
# diferencia de ese script, este NO manda telemetría/analítica de ningún
# tipo (Edecán no la tiene y no la va a tener) y no instala nada fuera de
# este repo (uv/venv/Ollama/symlinks de PATH no aplican aquí — self-host de
# Edecán corre en contenedores, no como un binario en $HOME).
# ============================================================================

set -Eeuo pipefail
# -E (errtrace): sin esto, un fallo DENTRO de una función (paso_secretos,
# verificar_docker, ...) no dispara el trap ERR de más abajo en bash — el
# script simplemente saldría en silencio por "set -e", sin el diagnóstico de
# _on_install_error. Verificado a mano contra la bash 3.2 que trae macOS por
# defecto (la misma preocupación que ya dejaban los comentarios de arrays de
# este script): con -E el trap SÍ se dispara para errores dentro de funciones.

# --- Rutas -------------------------------------------------------------------
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." &>/dev/null && pwd)"
cd "${REPO_ROOT}"

ENV_FILE="${REPO_ROOT}/.env"
ENV_EXAMPLE="${REPO_ROOT}/.env.example"
COMPOSE_FILE="infra/docker/compose.selfhost.yml"
STATE_FILE="${REPO_ROOT}/.edecan-install-state"
LOG_FILE="${REPO_ROOT}/.edecan-install.log"
# Nombre de proyecto explícito: sin esto, Docker Compose infiere el nombre
# del proyecto del directorio QUE CONTIENE el compose (infra/docker/ ->
# "docker"), y todo queda nombrado "docker-postgres-1", "docker_postgres_data"
# etc. — confuso en `docker ps`/Docker Desktop. Con -p, contenedores/volúmenes
# quedan como "edecan-postgres-1"/"edecan_postgres_data" sin importar desde
# dónde invoques el script.
COMPOSE_PROJECT="edecan"
# Constantes (no dependen de nada dinámico) -- declaradas aquí, no dentro de
# paso_urls(), para que el Resumen de más abajo pueda leerlas SIEMPRE, incluso
# en una corrida donde ese paso se saltó por estado (ver step()) y su función
# nunca llegó a ejecutarse.
DB_URL_COMPOSE="postgresql+asyncpg://edecan:edecan@postgres:5432/edecan"
REDIS_URL_COMPOSE="redis://redis:6379/0"

# --- Salida con color (se apaga si no hay TTY o si NO_COLOR está definida) ---
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'
  C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'; C_BLUE=$'\033[36m'
else
  C_RESET=""; C_BOLD=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_BLUE=""
fi

log_info() { printf '%s[info]%s %s\n' "${C_BLUE}" "${C_RESET}" "$*"; }
log_ok()   { printf '%s[ok]%s   %s\n' "${C_GREEN}" "${C_RESET}" "$*"; }
log_warn() { printf '%s[aviso]%s %s\n' "${C_YELLOW}" "${C_RESET}" "$*"; }
log_err()  { printf '%s[error]%s %s\n' "${C_RED}" "${C_RESET}" "$*" >&2; }

mostrar_ayuda() {
  cat <<'EOF'
Edecán — instalador guiado de self-host con Docker Compose, EN UN COMANDO.

Qué hace (ver docs/self-hosting.md §2 para el detalle completo):
  1. Verifica tu sistema (macOS/Linux/WSL2, arquitectura), que tengas
     docker + el plugin `docker compose` (v2, con el daemon respondiendo),
     y que haya espacio en disco razonable (aviso, no bloqueante).
  2. Copia .env.example -> .env si todavía no existe (nunca pisa uno real).
  3. Genera JWT_SECRET y LOCAL_MASTER_KEY si siguen en su placeholder de
     .env.example — NUNCA toca un valor que ya hayas puesto tú.
  4. Apunta DATABASE_URL/REDIS_URL a los nombres de servicio de
     infra/docker/compose.selfhost.yml (postgres/redis, no localhost).
  5. Te pregunta si quieres levantar LocalStack (profile "local-aws") para
     que S3/SQS funcionen sin cuenta AWS — o usa --local-aws/--sin-local-aws
     para no responder nada.
  6. Imprime SIEMPRE un resumen de lo que va a hacer y pide confirmación
     antes del ÚNICO paso con efecto real: `docker compose ... up -d --build`.

Reanudable: los pasos de instalación quedan marcados en
".edecan-install-state" — si el script se interrumpe, vuelve a correr el
MISMO comando y retoma donde se quedó. Toda la salida queda además en
".edecan-install.log".

Uso:
  scripts/instalar-selfhost.sh [opciones]

Opciones:
  --local-aws        Usa el profile local-aws (LocalStack) sin preguntar.
  --sin-local-aws    NO uses LocalStack — vas a traer tu propia cuenta AWS
                     real (rellena AWS_* en .env tú mismo, ver el resumen).
  --no-interactive   No hace NINGUNA pregunta ni pide confirmación final:
                     usa el flag de arriba si lo diste, o por defecto activa
                     local-aws (para que todo funcione sin cuenta AWS salvo
                     que pidas explícitamente --sin-local-aws).
  --force            Ignora ".edecan-install-state" y vuelve a correr TODOS
                     los pasos desde cero. Nunca pisa un .env/secreto que ya
                     tengas — esa garantía es independiente del estado.
  --dry-run          Recorre TODOS los pasos e imprime exactamente qué haría
                     (incluido el comando final de docker compose), SIN
                     escribir .env/estado/log ni ejecutar nada con efecto
                     real.
  -h, --help         Muestra esta ayuda y sale.

Idempotente: puedes correrlo varias veces. Nunca pisa un .env ya creado,
nunca regenera un secreto que ya tenga un valor propio, y "up -d --build"
es seguro de repetir (Docker reconstruye solo lo que cambió).

Este script se ESCRIBE y se revisa como cualquier otro código — igual que
el resto de infra/, no lo ejecuta automáticamente ningún agente ni pipeline
de este repositorio (ver infra/README.md). Lo corres TÚ, a mano, cuando
quieras instalar de verdad.
EOF
}

# --- Flags ---------------------------------------------------------------------
INTERACTIVE=1
LOCAL_AWS=""   # "" = decidir más abajo, "1" = sí, "0" = no
FORCE=0
DRY_RUN=0

for arg in "$@"; do
  case "${arg}" in
    --no-interactive) INTERACTIVE=0 ;;
    --local-aws) LOCAL_AWS=1 ;;
    --sin-local-aws) LOCAL_AWS=0 ;;
    --force) FORCE=1 ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help) mostrar_ayuda; exit 0 ;;
    *)
      log_err "Argumento desconocido: ${arg} (usa --help para ver las opciones)."
      exit 1
      ;;
  esac
done

# --- Rechazo temprano de Windows nativo ---------------------------------------
# Correr esto en Git Bash / MSYS2 / Cygwin (Windows nativo, NO WSL2) deja al
# usuario en un estado confuso: Docker Desktop para Windows normalmente se
# integra con el backend WSL2, no con estos shells, y el resto del script
# (cp/mktemp/tee/process substitution) asume un bash real tipo POSIX. La ruta
# soportada en Windows es WSL2. Salimos temprano con el siguiente paso claro,
# antes de que alguien descubra esto 3 minutos después con un error críptico.
# Adaptado de openjarvis/OpenJarvis (Apache-2.0) — ver NOTICE.
case "$(uname -s 2>/dev/null)" in
  MINGW*|MSYS*|CYGWIN*)
    cat >&2 <<'EOF'
instalar-selfhost.sh: Windows nativo (Git Bash / MSYS2 / Cygwin) no está
soportado.

Edecán con Docker Compose corre sobre Windows vía WSL2. Dos caminos:

  1. WSL2 (recomendado). Configuración única, en una PowerShell como
     administrador:

       wsl --install -d Ubuntu-24.04

     Reinicia si te lo pide, abre la terminal de Ubuntu que quedó instalada,
     clona el repo AHÍ DENTRO y corre este script normalmente:

       git clone <url-de-tu-fork-o-del-repo> edecan
       cd edecan
       scripts/instalar-selfhost.sh

  2. Docker Desktop para Windows + WSL2 — instala Docker Desktop
     (https://docs.docker.com/desktop/setup/install/windows-install/), activa
     "Use the WSL 2 based engine" y la integración con tu distro (Settings ->
     Resources -> WSL Integration). Sigue necesitando el camino 1 para correr
     este script en sí: hace falta un shell bash real (WSL2), no
     PowerShell/CMD ni Git Bash.

Más detalle: docs/self-hosting.md.
EOF
    exit 1
    ;;
esac

# --- Registro a archivo -------------------------------------------------------
# Todo lo que se imprime de aquí en adelante queda además anexado en
# LOG_FILE, para que _on_install_error pueda citar las últimas líneas si algo
# falla. En --dry-run NO se escribe (ni este archivo ni ningún otro) — el
# modo dry-run no deja ningún rastro en disco fuera de lo que ya existía.
if [ "${DRY_RUN}" -eq 1 ]; then
  echo "${C_YELLOW}${C_BOLD}Modo --dry-run: solo se muestra qué haría este script. No se escribe .env, ni ${STATE_FILE##*/}, ni ${LOG_FILE##*/}, y no se ejecuta ningún comando con efecto real (tampoco 'docker compose ... up').${C_RESET}"
else
  # shellcheck disable=SC2094
  exec > >(tee -a "${LOG_FILE}") 2>&1
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') — instalar-selfhost.sh $* =====" >&2
fi

CURRENT_STEP="inicio"

_on_install_error() {
  local exit_code=$?
  echo >&2
  log_err "Falló en: ${CURRENT_STEP}."
  if [ -f "${LOG_FILE}" ]; then
    echo "  Últimas líneas de ${LOG_FILE}:" >&2
    # Reintento acotado (máx. ~1s): "tee" (proceso aparte que alimenta
    # LOG_FILE, ver `exec > >(tee ...)` más arriba) puede tardar unos
    # milisegundos en volcar a disco lo último que se escribió justo antes de
    # este fallo -- sin el reintento, esta sección a veces sale vacía pese a
    # que el log SÍ tiene contenido (confirmado a mano). No es una
    # sincronización perfecta, pero cubre el caso real con margen de sobra.
    local _intento=0 _tail_out=""
    while [ "${_intento}" -lt 10 ]; do
      _tail_out="$(tail -n 20 "${LOG_FILE}" 2>/dev/null)"
      [ -n "${_tail_out}" ] && break
      sleep 0.1
      _intento=$((_intento + 1))
    done
    printf '%s\n' "${_tail_out}" | sed 's/^/    /' >&2
  fi
  echo >&2
  echo "  Cómo reanudar: corrige el problema de arriba y vuelve a correr" >&2
  echo "  exactamente el mismo comando. Los pasos que ya quedaron marcados" >&2
  echo "  como hechos en ${STATE_FILE} se saltan solos — retoma justo donde" >&2
  echo "  se quedó. (--force ignora ese estado y repite todo desde cero; las" >&2
  echo "  protecciones de .env/secretos existentes se mantienen igual.)" >&2
  exit "${exit_code}"
}
trap _on_install_error ERR

# Desarma/rearma el trap ERR alrededor de un intento que puede fallar A
# PROPÓSITO (p. ej. "probá la vía A, si falla probá la B") y que ya queda
# manejado con "|| true"/un chequeo de vacío justo después. SIN esto, con -E
# (errtrace) activo, un fallo DENTRO de la subshell de una sustitución de
# comando ("valor=\"\$(cmd)\" || true") dispara igual _on_install_error desde
# DENTRO de esa subshell, antes de que el "|| true" de afuera entre en
# juego -- confirmado a mano contra esta misma bash. No corrompe el valor
# capturado (_on_install_error solo escribe a stderr, nunca a stdout), pero
# sí imprime un bloque "Falló en: ..." confuso en medio de una corrida que
# en realidad va a terminar bien (p. ej. cuando falta el paquete Python
# `cryptography` y se cae al fallback de la librería estándar, algo rutinario
# y ya esperado). Usar SIEMPRE en pareja alrededor del intento riesgoso.
desarmar_trap_err() { trap - ERR; }
rearmar_trap_err() { trap _on_install_error ERR; }

# --- Helpers de lectura/escritura de .env ------------------------------------

# Lee el valor actual de una variable KEY=valor en un archivo .env. Vacío si
# la variable no existe todavía (o si el archivo ni siquiera existe aún, p.
# ej. en --dry-run antes de "crearlo").
read_env_value() {
  local key="$1" file="$2"
  local line=""
  if [ -f "${file}" ]; then
    # grep sin match (clave todavía no existe en el archivo) es rutina, no un
    # error -- desarmar_trap_err evita que dispare _on_install_error (ver su
    # comentario más arriba).
    desarmar_trap_err
    line="$(grep -m1 "^${key}=" "${file}" 2>/dev/null)" || true
    rearmar_trap_err
  fi
  printf '%s' "${line#*=}"
}

# Escribe/reemplaza KEY=valor en un archivo .env, preservando el resto de
# líneas (comentarios, orden, blancos) tal cual. Si la clave no existe, la
# agrega al final. Sin `sed -i` a propósito (BSD vs GNU sed difieren en esa
# bandera) — reescribe línea por línea en un temporal y hace `mv` al final.
#
# Guarda de --dry-run integrada aquí (no solo en cada llamador): esta es la
# ÚNICA función de todo el script que escribe en un .env, así que es el punto
# más seguro para garantizar "dry-run nunca escribe" incluso si algún paso
# futuro se olvida de chequear DRY_RUN antes de llamarla.
set_env_value() {
  if [ "${DRY_RUN:-0}" -eq 1 ]; then
    log_warn "[dry-run] (interno) se evitó escribir ${1}=... en ${3} — si ves este mensaje, es un bug del script, repórtalo."
    return 0
  fi
  local key="$1" value="$2" file="$3"
  local line found=0
  local -a lines=()
  if [ -f "${file}" ]; then
    while IFS= read -r line || [ -n "${line}" ]; do
      if [[ "${line}" == "${key}="* ]]; then
        lines+=("${key}=${value}")
        found=1
      else
        lines+=("${line}")
      fi
    done < "${file}"
  fi
  if [ "${found}" -eq 0 ]; then
    lines+=("${key}=${value}")
  fi
  printf '%s\n' "${lines[@]}" > "${file}"
}

es_placeholder_o_vacio() {
  local valor="$1" placeholder="$2"
  [ -z "${valor}" ] || [ "${valor}" = "${placeholder}" ]
}

# --- Generadores de secretos ---------------------------------------------------
# Nunca se llaman en --dry-run (ver paso_secretos): el fallback de abajo que
# usa `docker run` tiene efecto real (descarga una imagen) y no tiene sentido
# gastarlo solo para una vista previa.

# JWT_SECRET: cualquier cadena aleatoria larga sirve (firma HMAC de sesiones).
#
# Cada intento de abajo va envuelto en desarmar_trap_err/rearmar_trap_err:
# que un intento falle es RUTINA (probamos la vía A, si falla probamos la B),
# ya manejado con "|| true" -- sin desarmar el trap, _on_install_error se
# dispararía igual desde dentro de la subshell de cada intento (ver su
# comentario), imprimiendo un "Falló en: ..." confuso aunque el siguiente
# intento termine funcionando bien.
generar_jwt_secret() {
  local valor=""
  if command -v openssl >/dev/null 2>&1; then
    desarmar_trap_err
    valor="$(openssl rand -base64 48 2>/dev/null | tr -d '\n')" || true
    rearmar_trap_err
  fi
  if [ -z "${valor}" ] && command -v python3 >/dev/null 2>&1; then
    desarmar_trap_err
    valor="$(python3 -c "import secrets; print(secrets.token_urlsafe(48))" 2>/dev/null | tr -d '\n')" || true
    rearmar_trap_err
  fi
  if [ -z "${valor}" ] && command -v docker >/dev/null 2>&1; then
    desarmar_trap_err
    valor="$(docker run --rm python:3.12-slim python3 -c \
      "import secrets; print(secrets.token_urlsafe(48))" 2>/dev/null | tr -d '\n')" || true
    rearmar_trap_err
  fi
  [ -n "${valor}" ] || return 1
  printf '%s' "${valor}"
}

# LOCAL_MASTER_KEY: tiene que ser una clave Fernet válida (cifra el TokenVault
# en self-host/dev, ARCHITECTURE.md §2 y §10.4). `Fernet.generate_key()` es,
# byte a byte, `base64.urlsafe_b64encode(os.urandom(32))` — por eso el
# fallback sin la librería `cryptography` instalada sigue siendo 100%
# compatible, no es un atajo aproximado.
# Mismo motivo de desarmar_trap_err/rearmar_trap_err que generar_jwt_secret
# (arriba) -- aquí es todavía más importante: NO tener el paquete Python
# `cryptography` instalado (intento 1) es el caso RUTINARIO más común en un
# self-host recién instalado, no una rareza.
generar_master_key() {
  local valor=""
  if command -v python3 >/dev/null 2>&1; then
    # 1) La librería real, si ya está instalada en el Python local.
    desarmar_trap_err
    valor="$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null | tr -d '\n')" || true
    rearmar_trap_err
    if [ -z "${valor}" ]; then
      # 2) Sin `cryptography`: el mismo formato, solo con la librería estándar.
      desarmar_trap_err
      valor="$(python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())" 2>/dev/null | tr -d '\n')" || true
      rearmar_trap_err
    fi
  fi
  if [ -z "${valor}" ] && command -v docker >/dev/null 2>&1; then
    # 3) Sin Python local: contenedor efímero (--rm, se borra solo al salir)
    #    SOLO para este cálculo puntual — mismo one-liner de librería
    #    estándar, no instala nada ni deja nada corriendo.
    desarmar_trap_err
    valor="$(docker run --rm python:3.12-slim python3 -c \
      "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())" 2>/dev/null | tr -d '\n')" || true
    rearmar_trap_err
  fi
  [ -n "${valor}" ] || return 1
  printf '%s' "${valor}"
}

preguntar_si_no() {
  local prompt="$1" default="$2" respuesta=""
  local sufijo="[s/N]"
  [ "${default}" = "s" ] && sufijo="[S/n]"
  read -r -p "${prompt} ${sufijo} " respuesta || respuesta=""
  respuesta="${respuesta:-${default}}"
  case "${respuesta}" in
    [sSyY]*) return 0 ;;
    *) return 1 ;;
  esac
}

# --- Detección de sistema/arquitectura -----------------------------------------
# Adaptado de openjarvis/OpenJarvis (Apache-2.0, detect_os()/detect_arch() de
# scripts/install/install.sh) — ver NOTICE. Aquí es solo informativo (Docker
# funciona en más combinaciones de las que reconocemos por nombre); un
# resultado "desconocido" no bloquea, solo avisa.
detectar_os() {
  case "$(uname -s 2>/dev/null)" in
    Darwin) echo "macos" ;;
    Linux)
      if grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null; then
        echo "wsl2"
      else
        echo "linux"
      fi
      ;;
    *) echo "desconocido" ;;
  esac
}

detectar_arch() {
  case "$(uname -m 2>/dev/null)" in
    x86_64|amd64) echo "x86_64" ;;
    arm64|aarch64) echo "arm64" ;;
    *) echo "desconocida" ;;
  esac
}

# --- Docker: instalación por SO + versión + daemon -----------------------------
# Adaptado de openjarvis/OpenJarvis (Apache-2.0, need()/fail_missing_tool() de
# scripts/install/install.sh) — ver NOTICE. Ahí instalaban git/curl con el
# gestor de paquetes del sistema; aquí Docker no se puede "apt install" de
# forma confiable en todas las distros (Docker Desktop, docker-ce, distintos
# repos oficiales por distro) así que en vez de instalarlo por ti, este
# script te da el comando/enlace exacto para tu sistema y se detiene.
fail_falta_docker() {
  local motivo="$1"
  echo >&2
  log_err "${motivo}"
  case "$(uname -s 2>/dev/null)" in
    Darwin)
      cat >&2 <<'EOF'
  macOS:
    - Docker Desktop (recomendado, ya trae docker compose v2):
        https://docs.docker.com/desktop/setup/install/mac-install/
    - O con Homebrew:  brew install --cask docker
  Después de instalar, abre Docker Desktop una vez (icono de la ballena en
  la barra de menú) y vuelve a correr este script.
EOF
      ;;
    Linux)
      cat >&2 <<'EOF'
  Linux:
    - Debian/Ubuntu:  https://docs.docker.com/engine/install/ubuntu/
                      luego: sudo apt install docker-compose-plugin
    - Fedora/RHEL:    https://docs.docker.com/engine/install/fedora/
                      luego: sudo dnf install docker-compose-plugin
    - Arch:           sudo pacman -S docker docker-compose
    - O Docker Desktop para Linux:
        https://docs.docker.com/desktop/setup/install/linux/
  Después de instalar (si no usas Docker Desktop):
    sudo systemctl enable --now docker
    sudo usermod -aG docker "$USER"   # para no necesitar sudo cada vez
                                       # (cierra sesión y vuelve a entrar)
EOF
      ;;
    *)
      echo "  Instala Docker + el plugin 'docker compose' (v2) para tu sistema:" >&2
      echo "    https://docs.docker.com/get-docker/" >&2
      ;;
  esac
  echo "  Luego vuelve a correr este script." >&2
  exit 1
}

verificar_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    fail_falta_docker "No se encontró 'docker' en el PATH."
  fi
  if ! docker compose version >/dev/null 2>&1; then
    fail_falta_docker "Se encontró 'docker' pero no el plugin 'docker compose' (v2, sin guion)."
  fi

  local version_corta version_mayor
  version_corta="$(docker compose version --short 2>/dev/null || echo '')"
  version_mayor="${version_corta%%.*}"
  case "${version_mayor}" in
    ''|*[!0-9]*)
      log_warn "No se pudo leer la versión de 'docker compose' (salida: '${version_corta:-vacía}') — se continúa igual."
      ;;
    *)
      if [ "${version_mayor}" -lt 2 ]; then
        fail_falta_docker "docker compose v${version_corta} detectado — Edecán necesita v2 o superior (${COMPOSE_FILE} usa sintaxis de compose v2)."
      fi
      ;;
  esac
  log_ok "docker + docker compose disponibles (v${version_corta:-desconocida})."

  if ! docker info >/dev/null 2>&1; then
    log_err "docker está instalado pero el daemon no responde ('docker info' falló)."
    case "$(uname -s 2>/dev/null)" in
      Darwin) echo "  Abre Docker Desktop (o 'open -a Docker') y espera a que el ícono de la ballena en la barra de menú deje de animarse. Vuelve a correr el script." >&2 ;;
      Linux)  echo "  Arranca el daemon: 'sudo systemctl start docker' (o abre Docker Desktop si lo usas en Linux). Si acabas de instalar Docker, puede que necesites cerrar sesión y volver a entrar para que tu usuario tenga permiso sobre /var/run/docker.sock." >&2 ;;
      *) echo "  Arranca el daemon/servicio de Docker y vuelve a correr este script." >&2 ;;
    esac
    exit 1
  fi
  log_ok "El daemon de Docker responde."
}

# --- Espacio en disco (aviso, no bloqueante) -----------------------------------
verificar_espacio_disco() {
  local disponible_kb umbral_kb
  # df fallando (filesystem raro, permisos) es posible aunque infrecuente;
  # desarmar_trap_err evita que dispare _on_install_error por algo que ya
  # queda manejado abajo (case vacío -> aviso, no bloqueante).
  desarmar_trap_err
  disponible_kb="$(df -Pk "${REPO_ROOT}" 2>/dev/null | awk 'NR==2 {print $4}')" || true
  rearmar_trap_err
  case "${disponible_kb}" in
    ''|*[!0-9]*)
      log_warn "No se pudo determinar el espacio libre en disco — se continúa igual."
      return 0
      ;;
  esac
  umbral_kb=$((5 * 1024 * 1024))  # 5 GB en KB
  if [ "${disponible_kb}" -lt "${umbral_kb}" ]; then
    log_warn "Poco espacio libre en disco (~$((disponible_kb / 1024 / 1024))GB) — construir las imágenes de Docker puede necesitar varios GB. Continúa bajo tu propio riesgo, o libera espacio antes de seguir."
  else
    log_ok "Espacio en disco suficiente (~$((disponible_kb / 1024 / 1024))GB libres)."
  fi
}

# ============================================================================
# Sistema de pasos reanudable (STATE_FILE) — adaptado de openjarvis/OpenJarvis
# (Apache-2.0, funciones state_done()/mark_done()/step() de
# scripts/install/install.sh) — ver NOTICE. Formato propio, más simple que el
# JSON a mano del original: un nombre de paso por línea, texto plano. Nunca
# contiene secretos, solo nombres de pasos ("env", "secretos", ...).
# ============================================================================

state_done() {
  [ -f "${STATE_FILE}" ] && grep -qx "$1" "${STATE_FILE}" 2>/dev/null
}

mark_done() {
  [ "${DRY_RUN}" -eq 1 ] && return 0   # dry-run nunca escribe el state-file
  local key="$1"
  touch "${STATE_FILE}"
  state_done "${key}" || printf '%s\n' "${key}" >> "${STATE_FILE}"
}

# step <clave> <descripción humana> <función> [args...]
#
# Modo normal: si <clave> ya está en STATE_FILE y no diste --force, se salta
# (rápido, y evita repreguntar cosas como LocalStack). Con --dry-run SIEMPRE
# se ejecuta la función (para poder auditar el paso completo), pero cada
# función respeta DRY_RUN internamente y no escribe nada real.
step() {
  local key="$1" desc="$2"; shift 2
  CURRENT_STEP="${desc}"
  if [ "${DRY_RUN}" -ne 1 ] && [ "${FORCE}" -ne 1 ] && state_done "${key}"; then
    log_ok "${desc} (ya hecho — usa --force para repetirlo)"
    return 0
  fi
  "$@"
  mark_done "${key}"
}

# ============================================================================
# Implementación de cada paso
# ============================================================================

# No pasa por step()/STATE_FILE a propósito: son verificaciones del ENTORNO
# (¿tengo Docker? ¿responde el daemon? ¿hay espacio?), no "trabajo de
# instalación" — deben revalidarse en TODAS las corridas, incluso si ya
# quedaron marcadas como completadas en una corrida anterior (Docker se pudo
# haber detenido o desinstalado desde entonces).
paso_requisitos() {
  local os_det arch_det
  os_det="$(detectar_os)"
  arch_det="$(detectar_arch)"
  if [ "${os_det}" = "desconocido" ] || [ "${arch_det}" = "desconocida" ]; then
    log_warn "No se pudo identificar tu sistema/arquitectura con certeza (uname dio algo inesperado) — se continúa igual, Docker suele funcionar en más plataformas de las que este script reconoce por nombre."
  else
    log_ok "Sistema detectado: ${os_det} (${arch_det})."
  fi

  verificar_docker
  verificar_espacio_disco

  if [ ! -f "${ENV_EXAMPLE}" ]; then
    log_err "No se encontró ${ENV_EXAMPLE} — ¿estás corriendo esto desde un checkout completo del repo?"
    exit 1
  fi
  if [ ! -f "${REPO_ROOT}/${COMPOSE_FILE}" ]; then
    log_err "No se encontró ${REPO_ROOT}/${COMPOSE_FILE}."
    exit 1
  fi
  log_ok "Archivos del repo (.env.example, ${COMPOSE_FILE}) presentes."
}

paso_env() {
  if [ -f "${ENV_FILE}" ]; then
    ENV_ERA_NUEVO=0
    log_ok ".env ya existía — se reutiliza (nunca se pisa)."
  elif [ "${DRY_RUN}" -eq 1 ]; then
    log_info "[dry-run] Copiaría ${ENV_EXAMPLE} -> ${ENV_FILE}."
    ENV_ERA_NUEVO=1
  else
    cp "${ENV_EXAMPLE}" "${ENV_FILE}"
    ENV_ERA_NUEVO=1
    log_ok ".env creado a partir de .env.example."
  fi
}

paso_secretos() {
  JWT_ESTADO="ya tenía un valor propio — no se tocó"
  local jwt_actual
  jwt_actual="$(read_env_value "JWT_SECRET" "${ENV_FILE}")"
  if es_placeholder_o_vacio "${jwt_actual}" "TU_JWT_SECRET_AQUI"; then
    if [ "${DRY_RUN}" -eq 1 ]; then
      JWT_ESTADO="se generaría ahora (dry-run: no se ejecuta)"
    else
      local nuevo_jwt
      nuevo_jwt="$(generar_jwt_secret)" || {
        log_err "No se pudo generar JWT_SECRET automáticamente (no hay openssl, python3 ni docker)."
        echo "  Generalo a mano y pégalo en .env como JWT_SECRET=... :"
        echo '    openssl rand -base64 48'
        exit 1
      }
      set_env_value "JWT_SECRET" "${nuevo_jwt}" "${ENV_FILE}"
      JWT_ESTADO="generado ahora"
    fi
  fi
  log_ok "JWT_SECRET: ${JWT_ESTADO}."

  MASTER_KEY_ESTADO="ya tenía un valor propio — no se tocó"
  local master_key_actual
  master_key_actual="$(read_env_value "LOCAL_MASTER_KEY" "${ENV_FILE}")"
  if es_placeholder_o_vacio "${master_key_actual}" "TU_LOCAL_MASTER_KEY_FERNET_AQUI"; then
    if [ "${DRY_RUN}" -eq 1 ]; then
      MASTER_KEY_ESTADO="se generaría ahora (dry-run: no se ejecuta)"
    else
      local nuevo_master_key
      nuevo_master_key="$(generar_master_key)" || {
        log_err "No se pudo generar LOCAL_MASTER_KEY automáticamente (no hay python3 ni docker)."
        echo "  Generalo a mano y pégalo en .env como LOCAL_MASTER_KEY=... :"
        echo '    python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"'
        exit 1
      }
      set_env_value "LOCAL_MASTER_KEY" "${nuevo_master_key}" "${ENV_FILE}"
      MASTER_KEY_ESTADO="generado ahora"
    fi
  fi
  log_ok "LOCAL_MASTER_KEY: ${MASTER_KEY_ESTADO}."

  if [ "${JWT_ESTADO}" = "generado ahora" ] || [ "${MASTER_KEY_ESTADO}" = "generado ahora" ]; then
    log_warn "Los secretos nuevos quedaron guardados en .env — no se muestran en pantalla. No los pierdas ni los compartas: rotar LOCAL_MASTER_KEY después de tener datos reales los deja indescifrables (ver docs/runbooks/rotacion-claves.md)."
  fi
}

paso_urls() {
  # DB_URL_COMPOSE/REDIS_URL_COMPOSE son constantes -- ver su declaración
  # junto a COMPOSE_PROJECT más arriba, se usan tal cual aquí.
  if [ "${DRY_RUN}" -eq 1 ]; then
    log_info "[dry-run] Fijaría DATABASE_URL=${DB_URL_COMPOSE} y REDIS_URL=${REDIS_URL_COMPOSE} en .env."
  else
    set_env_value "DATABASE_URL" "${DB_URL_COMPOSE}" "${ENV_FILE}"
    set_env_value "REDIS_URL" "${REDIS_URL_COMPOSE}" "${ENV_FILE}"
  fi
  log_ok "DATABASE_URL/REDIS_URL apuntados a los servicios 'postgres'/'redis' de ${COMPOSE_FILE}."
}

paso_local_aws() {
  if [ -z "${LOCAL_AWS}" ]; then
    if [ "${INTERACTIVE}" -eq 1 ]; then
      echo
      echo "Sin una cuenta AWS real, la subida de archivos y los jobs en segundo"
      echo "plano (recordatorios, memoria, ingest_file...) necesitan algo que"
      echo "emule S3+SQS. Podemos levantar un LocalStack local por ti — cero"
      echo "cuenta AWS, todo dentro de tu propia máquina/servidor."
      if preguntar_si_no "¿Levantar LocalStack local (profile local-aws)?" "s"; then
        LOCAL_AWS=1
      else
        LOCAL_AWS=0
      fi
    else
      LOCAL_AWS=1
      log_info "Modo --no-interactive sin --sin-local-aws: se activa LocalStack por defecto."
    fi
  fi

  if [ "${LOCAL_AWS}" = "1" ]; then
    if [ "${DRY_RUN}" -eq 1 ]; then
      log_info "[dry-run] Fijaría AWS_ENDPOINT_URL/SQS_QUEUE_URL/AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY/S3_BUCKET/AWS_REGION en .env (LocalStack)."
    else
      set_env_value "AWS_ENDPOINT_URL" "http://localstack:4566" "${ENV_FILE}"
      set_env_value "SQS_QUEUE_URL" "http://localstack:4566/000000000000/edecan-jobs" "${ENV_FILE}"
      set_env_value "AWS_ACCESS_KEY_ID" "test" "${ENV_FILE}"
      set_env_value "AWS_SECRET_ACCESS_KEY" "test" "${ENV_FILE}"
      set_env_value "S3_BUCKET" "edecan-files" "${ENV_FILE}"
      set_env_value "AWS_REGION" "us-east-1" "${ENV_FILE}"
    fi
    log_ok "LocalStack activado: AWS_ENDPOINT_URL/SQS_QUEUE_URL apuntados al servicio 'localstack'."
  else
    log_warn "LocalStack NO se va a levantar. AWS_ENDPOINT_URL/S3_BUCKET/SQS_QUEUE_URL/AWS_* en .env deben apuntar a TU cuenta AWS real (bucket S3 + colas 'edecan-jobs'/'edecan-jobs-dlq' creadas por ti) — este script no los toca. Ver docs/self-hosting.md §2.2."
  fi
}

# ============================================================================
# Ejecución
# ============================================================================
echo "${C_BOLD}Edecán — instalador de self-host (Docker Compose)${C_RESET}"
echo "Repositorio: ${REPO_ROOT}"
echo

# Defaults para las variables que paso_env/paso_secretos normalmente rellenan
# — si ese paso se SALTA por estado (ya hecho, ver step() abajo), su función
# nunca corre, así que sin este default el Resumen de más abajo reventaría
# con "unbound variable" bajo `set -u`. Cuando el paso sí corre (primera
# corrida, --force, o --dry-run), estos valores se sobrescriben con el
# detalle real ("generado ahora" / "ya tenía un valor propio").
ENV_ERA_NUEVO=0
JWT_ESTADO="sin revisar en esta corrida (paso saltado — ya estaba hecho; usa --force para revisarlo de nuevo)"
MASTER_KEY_ESTADO="${JWT_ESTADO}"

CURRENT_STEP="verificar requisitos (sistema, Docker, espacio en disco, archivos del repo)"
paso_requisitos

step "env"       ".env"                                    paso_env
step "secretos"  "Secretos (JWT_SECRET / LOCAL_MASTER_KEY)" paso_secretos
step "urls"      "DATABASE_URL / REDIS_URL"                 paso_urls
step "local_aws" "S3/SQS (LocalStack o AWS real)"            paso_local_aws

# ============================================================================
# Resumen + confirmación (el ÚNICO paso con efecto real es el de más abajo)
# ============================================================================
PROFILE_ARGS=()
PROFILE_STR=""
if [ "${LOCAL_AWS}" = "1" ]; then
  PROFILE_ARGS=(--profile local-aws)
  PROFILE_STR="--profile local-aws "
fi
# "${arr[@]+"${arr[@]}"}" en vez de "${arr[@]}" a secas: con `set -u`, expandir
# un array VACÍO con "${arr[@]}" revienta como "unbound variable" en bash 3.2
# (el bash de macOS por defecto) — este patrón lo evita en cualquier versión.
COMPOSE_CMD=(docker compose -p "${COMPOSE_PROJECT}" "${PROFILE_ARGS[@]+"${PROFILE_ARGS[@]}"}" -f "${COMPOSE_FILE}" up -d --build)

PUERTOS="3000 (web), 8000 (api), 5432 (postgres), 6379 (redis)"
[ "${LOCAL_AWS}" = "1" ] && PUERTOS="${PUERTOS}, 4566 (localstack)"

echo
echo "${C_BOLD}Resumen${C_RESET}"
echo "  .env:                ${ENV_FILE} ($([ "${ENV_ERA_NUEVO}" -eq 1 ] && echo "nuevo" || echo "reutilizado"))"
echo "  JWT_SECRET:          ${JWT_ESTADO}"
echo "  LOCAL_MASTER_KEY:    ${MASTER_KEY_ESTADO}"
echo "  DATABASE_URL:        ${DB_URL_COMPOSE}"
echo "  REDIS_URL:           ${REDIS_URL_COMPOSE}"
if [ "${LOCAL_AWS}" = "1" ]; then
  echo "  S3/SQS:              LocalStack local (profile local-aws) — cero cuenta AWS"
else
  echo "  S3/SQS:              tu propia cuenta AWS real (AWS_* sin tocar en .env)"
fi
echo "  Puertos publicados:  ${PUERTOS}"
echo "  Comando a ejecutar:  ${COMPOSE_CMD[*]}"
echo
echo "  Nota: si vas a exponer esto en internet con un dominio propio (VPS/NAS,"
echo "  no solo localhost), edita además PUBLIC_BASE_URL/WEB_BASE_URL/"
echo "  NEXT_PUBLIC_API_URL en .env antes de continuar (ver docs/self-hosting.md)."
echo

if [ "${DRY_RUN}" -eq 1 ]; then
  log_info "[dry-run] Ejecutaría: ${COMPOSE_CMD[*]}"
  echo
  log_ok "Fin de --dry-run. No se escribió ${ENV_FILE} (salvo que ya existiera de antes), no se tocó ${STATE_FILE}, y no se llamó a Docker con ningún comando con efecto real."
  exit 0
fi

if [ "${INTERACTIVE}" -eq 1 ]; then
  if ! preguntar_si_no "¿Construir las imágenes y levantar el stack ahora?" "s"; then
    log_info "Cancelado por ti. Tu .env ya quedó preparado — vuelve a correr este script cuando quieras continuar."
    exit 0
  fi
else
  log_info "Modo --no-interactive: se continúa sin pedir confirmación."
fi

# ============================================================================
# Único paso con efecto real
# ============================================================================
CURRENT_STEP="levantar el stack (docker compose up -d --build)"
log_info "Ejecutando: ${COMPOSE_CMD[*]}"
"${COMPOSE_CMD[@]}"

# Ya tuvimos éxito — un fallo en los `echo` de abajo (virtualmente imposible)
# no debe reportarse como "instalación fallida, así reanuda".
trap - ERR

# ============================================================================
# Próximos pasos
# ============================================================================
echo
log_ok "Stack levantado."
echo
echo "${C_BOLD}Próximos pasos${C_RESET}"
echo "  1. Abre http://localhost:3000 (o la URL/dominio de tu servidor)."
echo "  2. Regístrate — crea tu tenant (correo + contraseña)."
echo "  3. Sigue el wizard de bienvenida (o ve directo a /app/configuracion):"
echo "     conecta tu proveedor de LLM con el flujo de pegar-y-validar — es"
echo "     lo ÚNICO obligatorio para poder chatear. Voz, conectores y"
echo "     mensajería son opcionales y se conectan cuando quieras, desde ahí"
echo "     mismo, nunca editando .env a mano (ver docs/credenciales.md)."
echo
echo "  Migraciones: se aplican SOLAS — el servicio 'migrate' del compose corre"
echo "  antes que api/worker (Dockerfile.migrate, infra/docker/compose.selfhost.yml)."
echo "  No hace falta ningún paso manual, ni la primera vez ni al actualizar."
echo "  Si alguna vez necesitas correrlas a mano (ver docs/self-hosting.md §2.3):"
echo "    docker compose -p ${COMPOSE_PROJECT} ${PROFILE_STR}-f ${COMPOSE_FILE} run --rm migrate"
echo
echo "  Para actualizar más adelante: git pull && $(printf '%s ' "${COMPOSE_CMD[@]}")"
echo "  Guía completa: docs/self-hosting.md"
