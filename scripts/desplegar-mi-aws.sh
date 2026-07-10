#!/usr/bin/env bash
# ============================================================================
# Edecán — "Tu propia nube": wrapper HONESTO de infra/terraform/ (PLAN.md,
# "«Tu propia nube» (opcional)").
#
# Qué es esto: un atajo para desplegar la topología AWS de producción
# completa (ARCHITECTURE.md §7 — VPC, ECS Fargate, RDS, ElastiCache, SQS, S3,
# CloudFront, ALB, KMS, Secrets Manager...) en TU PROPIA cuenta de AWS, más
# pesado que infra/docker/compose.selfhost.yml a propósito: es la ruta para
# quien quiere un servidor 24/7 de nivel producción, no para probar Edecán.
#
# Qué NO es esto: este proyecto JAMÁS ejecuta Terraform por ti (regla dura,
# ARCHITECTURE.md §0.4 / infra/README.md). Por defecto este script SOLO hace
# `terraform init` + `terraform plan` (ambos de solo lectura frente a tu
# cuenta) y se DETIENE mostrándote el plan — aplicar es SIEMPRE una decisión
# y una acción manual tuya. Con --aplicar-de-verdad puedes pedirle a este
# MISMO script que encadene el apply, pero incluso así te vuelve a preguntar
# escribiendo una frase de confirmación Y Terraform te pide su propio "yes"
# nativo — nunca hay un solo paso silencioso entre tú y gastar dinero real.
#
# Uso:
#   scripts/desplegar-mi-aws.sh [--entorno=dev|prod] [--aplicar-de-verdad]
#
# Opciones:
#   --entorno=dev|prod   Qué envs/<entorno>.tfvars usar (default: dev).
#   --aplicar-de-verdad  Tras mostrar el plan, ofrece aplicarlo YA MISMO —
#                        SIEMPRE con una segunda confirmación escrita a mano
#                        ("SI QUIERO GASTAR DINERO") y el propio prompt "yes"
#                        de `terraform apply`. Sin este flag, el script nunca
#                        siquiera pregunta por aplicar: solo planea y para.
#   -h, --help           Muestra esta ayuda y sale.
#
# Variables de entorno que necesitas exportar ANTES de correr esto (son tuyas,
# de TU cuenta AWS — este script/repo nunca trae ni pide una credencial de
# plataforma, ver DIRECCION_ACTUAL.md "Modelo de credenciales"):
#   AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY   (o AWS_PROFILE apuntando a un
#                                                perfil ya creado con
#                                                `aws configure`)
#   AWS_SESSION_TOKEN                           (solo si usas credenciales
#                                                temporales / SSO)
#   AWS_REGION                                  (opcional — la región donde
#                                                se despliega la controla el
#                                                .tfvars vía `aws_region`,
#                                                esta variable solo afecta a
#                                                qué región usa el AWS CLI
#                                                para el chequeo inicial)
#
# Prerequisitos: Terraform >= 1.7 (infra/terraform/versions.tf), AWS CLI v2,
# una cuenta AWS con permisos amplios (administrador, o un rol suficiente
# para VPC/ECS/RDS/ElastiCache/SQS/S3/KMS/Secrets Manager/CloudFront/WAF/
# EventBridge/CloudWatch/Budgets — ver infra/terraform/README.md), y
# opcionalmente un dominio propio (se puede desplegar sin uno, ver
# infra/README.md §5).
#
# Este script se ESCRIBE y se revisa como cualquier otro código — no lo
# ejecuta automáticamente ningún agente ni pipeline de este repositorio (ver
# infra/README.md). Lo corres TÚ, a mano, cuando decidas de verdad desplegar
# en tu propia cuenta.
# ============================================================================

set -Eeuo pipefail
# -E (errtrace): para que el trap ERR de más abajo se dispare también si
# algún día una falla queda dentro de una función (hoy los pasos con efecto
# real de este script -- terraform/aws -- corren al nivel superior, pero
# _on_deploy_error debe ser confiable igual si eso cambia).

# --- Rutas -------------------------------------------------------------------
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." &>/dev/null && pwd)"
cd "${REPO_ROOT}"

TF_DIR="infra/terraform"

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

# Trap con diagnóstico (qué línea, qué comando) en vez del mensaje genérico
# anterior -- mismo espíritu que _on_install_error de instalar-selfhost.sh,
# adaptado: este script no tiene pasos reanudables ni log a archivo (no hace
# falta -- por defecto solo hace init+plan, de solo lectura contra tu cuenta).
_on_deploy_error() {
  local exit_code=$?
  local linea="$1" comando="$2"
  log_err "Se detuvo en la línea ${linea} (comando: ${comando})."
  log_err "No se aplicó ningún cambio de infraestructura más allá de lo que ya viste arriba."
  exit "${exit_code}"
}
trap '_on_deploy_error "$LINENO" "$BASH_COMMAND"' ERR

# Desarma/rearma el trap ERR alrededor de un intento que puede fallar A
# PROPÓSITO y que ya queda manejado justo después con su propio "if"/mensaje
# específico (ver el chequeo de credenciales AWS más abajo). SIN esto, con -E
# (errtrace) activo, un fallo DENTRO de la subshell de una sustitución de
# comando dispara igual _on_deploy_error desde DENTRO de esa subshell, ANTES
# de que el manejo específico de afuera entre en juego -- confirmado a mano.
# No corrompe el valor capturado (_on_deploy_error solo escribe a stderr),
# pero sí imprime un bloque "Se detuvo en la línea..." confuso encima del
# mensaje específico, incluso en el caso más rutinario de todos (credenciales
# AWS todavía no configuradas la primera vez que alguien corre este script).
desarmar_trap_err() { trap - ERR; }
rearmar_trap_err() { trap '_on_deploy_error "$LINENO" "$BASH_COMMAND"' ERR; }

RAYA='!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!'

mostrar_ayuda() {
  cat <<'EOF'
Edecán — "Tu propia nube": wrapper honesto de infra/terraform/.

Despliega la topología AWS de producción completa (ARCHITECTURE.md §7) en TU
PROPIA cuenta de AWS. Por defecto SOLO hace `terraform init` + `terraform
plan` (solo lectura) y se detiene mostrando el plan — aplicar es siempre tu
decisión manual. Ver infra/terraform/README.md para el detalle completo y la
tabla de costos aproximados.

Uso:
  scripts/desplegar-mi-aws.sh [--entorno=dev|prod] [--aplicar-de-verdad]

Opciones:
  --entorno=dev|prod   Qué envs/<entorno>.tfvars usar (default: dev).
  --aplicar-de-verdad  Tras el plan, ofrece aplicarlo YA — con una segunda
                       confirmación escrita a mano y el "yes" nativo de
                       `terraform apply`. Sin este flag, el script nunca
                       pregunta por aplicar: solo planea y para.
  -h, --help           Muestra esta ayuda y sale.

Variables de entorno que exportas TÚ antes de correr esto (tu propia cuenta):
  AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY   (o AWS_PROFILE)
  AWS_SESSION_TOKEN                           (opcional, credenciales temporales)
  AWS_REGION                                  (opcional)

Más detalle: infra/README.md, infra/terraform/README.md.
EOF
}

mostrar_advertencia() {
  echo "${C_RED}${RAYA}${C_RESET}"
  echo "${C_RED}${C_BOLD}  ATENCIÓN — esto despliega TU PROPIA NUBE, no una demo.${C_RESET}"
  echo "${C_RED}${RAYA}${C_RESET}"
  echo
  echo "  Este script crea infraestructura AWS REAL (VPC, ECS Fargate, RDS,"
  echo "  ElastiCache, SQS, S3, CloudFront, ALB, KMS, Secrets Manager...) EN TU"
  echo "  PROPIA cuenta de AWS. AWS te factura A TI, directo, con costo real y"
  echo "  continuo mientras quede corriendo — revisa infra/terraform/README.md"
  echo "  (tabla de costos aproximados) ANTES de seguir."
  echo
  echo "  Este proyecto NUNCA ejecuta 'terraform apply' por ti. Este script,"
  echo "  por defecto, SOLO hace 'init' + 'plan' (de solo lectura contra tu"
  echo "  cuenta) y se DETIENE. Aplicar es siempre una decisión y una acción"
  echo "  manual tuya — incluso con --aplicar-de-verdad, te vamos a volver a"
  echo "  preguntar por escrito antes de tocar nada."
  echo
  echo "${C_RED}${RAYA}${C_RESET}"
  echo
}

# --- Flags ---------------------------------------------------------------------
ENTORNO="dev"
APLICAR_DE_VERDAD=0

for arg in "$@"; do
  case "${arg}" in
    --entorno=*) ENTORNO="${arg#*=}" ;;
    --aplicar-de-verdad) APLICAR_DE_VERDAD=1 ;;
    -h|--help) mostrar_ayuda; exit 0 ;;
    *)
      log_err "Argumento desconocido: ${arg} (usa --help para ver las opciones)."
      exit 1
      ;;
  esac
done

case "${ENTORNO}" in
  dev|prod) ;;
  *)
    log_err "--entorno debe ser 'dev' o 'prod' (recibido: '${ENTORNO}')."
    exit 1
    ;;
esac

# --- Rechazo temprano de Windows nativo ---------------------------------------
# Mismo rechazo que scripts/instalar-selfhost.sh (adaptado de
# openjarvis/OpenJarvis, Apache-2.0 -- ver NOTICE): Git Bash/MSYS2/Cygwin no
# son un bash real tipo POSIX y dejan a Terraform/AWS CLI en un estado
# confuso (rutas de Windows donde el resto del tooling espera rutas Unix).
# La ruta soportada en Windows es WSL2.
case "$(uname -s 2>/dev/null)" in
  MINGW*|MSYS*|CYGWIN*)
    cat >&2 <<'EOF'
desplegar-mi-aws.sh: Windows nativo (Git Bash / MSYS2 / Cygwin) no está
soportado.

Este script (Terraform + AWS CLI) corre sobre Windows vía WSL2.
Configuración única, en una PowerShell como administrador:

    wsl --install -d Ubuntu-24.04

Reinicia si te lo pide, abre la terminal de Ubuntu que quedó instalada,
instala ahí Terraform (https://developer.hashicorp.com/terraform/install) y
AWS CLI v2
(https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html),
clona el repo DENTRO de WSL2 y vuelve a correr:

    scripts/desplegar-mi-aws.sh

Más detalle: docs/self-hosting.md, infra/terraform/README.md.
EOF
    exit 1
    ;;
esac

TFVARS_FILE="envs/${ENTORNO}.tfvars"

mostrar_advertencia

# ============================================================================
# 1. Requisitos: Terraform + AWS CLI + credenciales reales
# ============================================================================
if ! command -v terraform >/dev/null 2>&1; then
  log_err "No se encontró 'terraform' en el PATH."
  echo "  Instálalo desde https://developer.hashicorp.com/terraform/install (>= 1.7, ver infra/terraform/versions.tf)."
  exit 1
fi
log_ok "terraform disponible ($(terraform version -json 2>/dev/null | grep -o '"terraform_version":"[^"]*"' | head -1 || terraform version | head -1))."

if ! command -v aws >/dev/null 2>&1; then
  log_err "No se encontró 'aws' (AWS CLI v2) en el PATH."
  echo "  Instálalo desde https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
  exit 1
fi
log_ok "aws cli disponible ($(aws --version 2>&1))."

if [ ! -d "${REPO_ROOT}/${TF_DIR}" ]; then
  log_err "No se encontró ${REPO_ROOT}/${TF_DIR} — ¿estás corriendo esto desde un checkout completo del repo?"
  exit 1
fi
if [ ! -f "${REPO_ROOT}/${TF_DIR}/${TFVARS_FILE}" ]; then
  log_err "No se encontró ${REPO_ROOT}/${TF_DIR}/${TFVARS_FILE}."
  exit 1
fi

log_info "Verificando credenciales AWS con 'aws sts get-caller-identity' (solo lectura)..."
IDENTIDAD_TXT=""
# No tener credenciales configuradas TODAVÍA es el caso más común de todos
# (primera vez que alguien corre este script) -- desarmar_trap_err evita el
# "Se detuvo en la línea..." espurio encima del mensaje específico de abajo.
# Se rearma incondicionalmente después del "fi" (cubre tanto el camino de
# éxito, que sigue corriendo, como el de error, que ya sale con exit 1 antes
# de llegar ahí -- rearmar ahí también no hace daño).
desarmar_trap_err
if ! IDENTIDAD_TXT="$(aws sts get-caller-identity --output text --query '[Account,Arn]' 2>&1)"; then
  rearmar_trap_err
  log_err "No se pudo verificar tu identidad AWS. Salida de 'aws sts get-caller-identity':"
  echo "${IDENTIDAD_TXT}" >&2
  echo
  echo "  Configura credenciales antes de continuar, por ejemplo:"
  echo "    aws configure                       # perfil por defecto"
  echo "    export AWS_PROFILE=tu-perfil         # o un perfil con nombre"
  echo "    export AWS_ACCESS_KEY_ID=...         # o exportarlas directo"
  echo "    export AWS_SECRET_ACCESS_KEY=..."
  exit 1
fi
rearmar_trap_err

# --output text con --query '[Account,Arn]' devuelve una sola línea con los
# dos campos separados por TAB — más robusto que parsear el JSON a mano con
# grep (que aborta el script bajo `pipefail` si algún campo no calza con el
# patrón esperado).
CUENTA_ID="$(printf '%s' "${IDENTIDAD_TXT}" | cut -f1)"
CUENTA_ARN="$(printf '%s' "${IDENTIDAD_TXT}" | cut -f2)"
log_ok "Credenciales AWS válidas."

# ============================================================================
# 2. Resumen antes de tocar nada
# ============================================================================
echo
echo "${C_BOLD}Resumen${C_RESET}"
echo "  Cuenta AWS detectada:  ${CUENTA_ID:-desconocida}"
echo "  Identidad (ARN):       ${CUENTA_ARN:-desconocida}"
echo "  Entorno:                ${ENTORNO}"
echo "  Archivo de variables:  ${TF_DIR}/${TFVARS_FILE}"
echo "  Directorio Terraform:  ${TF_DIR}"
if [ "${APLICAR_DE_VERDAD}" -eq 1 ]; then
  echo "  Modo:                    init + plan, y LUEGO te pregunta si aplicar de verdad (--aplicar-de-verdad)"
else
  echo "  Modo:                    init + plan ÚNICAMENTE — nunca se pregunta por aplicar"
fi
echo
log_warn "¿Es esta la cuenta AWS correcta? Si no, Ctrl+C ahora y cambia tus credenciales (AWS_PROFILE / aws configure)."
echo

# ============================================================================
# 3. terraform init (local backend por defecto — no toca AWS, ver backend.tf)
# ============================================================================
log_info "Ejecutando: terraform -chdir=${TF_DIR} init"
terraform -chdir="${TF_DIR}" init
log_ok "terraform init completo."

# ============================================================================
# 4. terraform plan (de solo lectura frente a tu cuenta — no crea nada)
# ============================================================================
echo
log_info "Ejecutando: terraform -chdir=${TF_DIR} plan -var-file=${TFVARS_FILE}"
terraform -chdir="${TF_DIR}" plan -var-file="${TFVARS_FILE}"

echo
log_ok "Plan generado arriba. TODAVÍA NO SE APLICÓ NADA."

if [ "${APLICAR_DE_VERDAD}" -ne 1 ]; then
  echo
  echo "${C_BOLD}Siguiente paso (manual, decisión tuya)${C_RESET}"
  echo "  Revisa el plan de arriba con calma — cada recurso que dice crear tiene"
  echo "  costo real (ver infra/terraform/README.md). Cuando estés listo:"
  echo
  echo "    cd ${TF_DIR}"
  echo "    terraform apply -var-file=${TFVARS_FILE}"
  echo
  echo "  (o vuelve a correr este script con --aplicar-de-verdad para hacerlo"
  echo "  desde aquí mismo, con la misma confirmación de doble check.)"
  exit 0
fi

# ============================================================================
# 5. --aplicar-de-verdad: SEGUNDA confirmación escrita a mano, sin atajos.
#    Terraform apply (sin -auto-approve) todavía va a pedir su propio "yes"
#    después de esto — nunca hay un solo paso entre tú y gastar dinero real.
# ============================================================================
echo
echo "${C_RED}${RAYA}${C_RESET}"
echo "${C_RED}${C_BOLD}  Pediste --aplicar-de-verdad. Esto va a GASTAR DINERO REAL en la cuenta"
echo "  AWS de arriba (${CUENTA_ID:-desconocida}).${C_RESET}"
echo "${C_RED}${RAYA}${C_RESET}"
echo
RESPUESTA=""
read -r -p "Para continuar, escribe exactamente: SI QUIERO GASTAR DINERO
> " RESPUESTA || RESPUESTA=""

if [ "${RESPUESTA}" != "SI QUIERO GASTAR DINERO" ]; then
  log_info "No se escribió la frase exacta — se cancela. No se aplicó nada."
  exit 0
fi

log_info "Confirmado. Ejecutando: terraform -chdir=${TF_DIR} apply -var-file=${TFVARS_FILE}"
log_info "Terraform va a pedirte SU PROPIO 'yes' antes de crear nada — esta NO es la última confirmación."
terraform -chdir="${TF_DIR}" apply -var-file="${TFVARS_FILE}"

echo
log_ok "Apply completo. Guarda la salida de arriba (outputs: alb_dns, bucket, queue_url, ecr_repository_urls...)."
echo "  Próximo paso manual: construir y publicar las imágenes (ver infra/README.md"
echo "  paso 3) y rellenar los secretos reales en Secrets Manager (paso 4)."
