#!/usr/bin/env bash
# apps/desktop/scripts/download-opencode.sh (macOS/Linux; Windows nativo: ver
# download-opencode.ps1)
#
# Descarga el binario oficial de opencode (https://opencode.ai, MIT) para un
# target triple y lo deja en src-tauri/binaries/ con la convención de sidecar
# de Tauri (`opencode-<target-triple>[.exe]`, ver tauri.conf.json ->
# bundle.externalBin) -- el mismo lugar donde build-backend.sh instala el
# sidecar de `edecan-local`.
#
# A DIFERENCIA de download-ollama.sh, este script NO es opcional: opencode es
# el MOTOR del IDE de Edecán (decisión del 30-jul-2026, ver
# docs/opencode-motor.md). Sin este binario dentro del paquete, la app
# instalada no puede ejecutar ningún agente: `ide_opencode_binario.py` no
# encontraría nada que lanzar y el IDE quedaría muerto en cualquier máquina
# que no sea la de desarrollo. Por eso build-backend.sh lo llama siempre, sin
# variable de entorno que lo active.
#
# Estructura adaptada de download-ollama.sh (a su vez adaptado de
# open-reference implementation/OpenJarvis, Apache-2.0 -- ver NOTICE). Diferencias: la tabla de
# versión/SHA-256 es la de opencode, y no hace falta preservar DLLs
# adicionales porque cada asset es un único ejecutable autocontenido.
#
# Uso:
#   ./download-opencode.sh                      # autodetecta esta máquina
#   ./download-opencode.sh aarch64-apple-darwin
#   ./download-opencode.sh x86_64-pc-windows-msvc
set -euo pipefail

# Versión fijada a propósito: el adaptador (`ide_opencode.py`) está escrito
# contra la superficie `/api/*` de esta versión y comprueba el mínimo en
# arranque. Subirla es una decisión consciente, no un `latest` que cambie el
# motor del IDE sin que nadie lo note.
VERSION="1.17.18"
BASE_URL="https://github.com/anomalyco/opencode/releases/download/v${VERSION}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BINARIES_DIR="$(cd "$SCRIPT_DIR/../src-tauri/binaries" 2>/dev/null && pwd || echo "$SCRIPT_DIR/../src-tauri/binaries")"
mkdir -p "$BINARIES_DIR"

# --- 1) Target triple -------------------------------------------------------
if [[ "${1:-}" != "" ]]; then
  TARGET="$1"
else
  ARCH="$(uname -m)"
  OS="$(uname -s)"
  case "$OS" in
    Darwin)
      case "$ARCH" in
        arm64) TARGET="aarch64-apple-darwin" ;;
        x86_64) TARGET="x86_64-apple-darwin" ;;
        *) echo "error: arquitectura de macOS no soportada: $ARCH" >&2; exit 1 ;;
      esac
      ;;
    Linux)
      case "$ARCH" in
        aarch64|arm64) TARGET="aarch64-unknown-linux-gnu" ;;
        x86_64) TARGET="x86_64-unknown-linux-gnu" ;;
        *) echo "error: arquitectura de Linux no soportada: $ARCH" >&2; exit 1 ;;
      esac
      ;;
    MINGW*|MSYS*|CYGWIN*|Windows_NT)
      TARGET="x86_64-pc-windows-msvc"
      ;;
    *)
      echo "error: sistema no soportado: $OS" >&2
      exit 1
      ;;
  esac
fi

# --- 2) Asset y SHA-256 por target -----------------------------------------
# Los SHA vienen de la API de GitHub para el release v1.17.18. Se verifican
# ANTES de mover nada a binaries/: un binario que se va a firmar, notarizar y
# distribuir no se acepta por su nombre de archivo.
case "$TARGET" in
  aarch64-apple-darwin)
    ASSET="opencode-darwin-arm64.zip"; INTERNO="opencode"; SALIDA="opencode-${TARGET}"
    SHA="24327f89c103526c0518fc9b797767f318ab85ef3cee8636e722d6138f33aa3d" ;;
  x86_64-apple-darwin)
    ASSET="opencode-darwin-x64.zip"; INTERNO="opencode"; SALIDA="opencode-${TARGET}"
    SHA="cebf209aad2c0bd998fbac3f8dd1b45eef35da1af18cd698e78b111b73c5fbb0" ;;
  x86_64-pc-windows-msvc)
    ASSET="opencode-windows-x64.zip"; INTERNO="opencode.exe"; SALIDA="opencode-${TARGET}.exe"
    SHA="7d489fd9b314e25bccf9c5dd2f17ef2774902c7b7db9aa34f46b0aab4715c70c" ;;
  aarch64-pc-windows-msvc)
    ASSET="opencode-windows-arm64.zip"; INTERNO="opencode.exe"; SALIDA="opencode-${TARGET}.exe"
    SHA="fcfbd7f82242f47ec7e98bc8819eeebe716654e9bce1fb1bd7f364e887cb95ab" ;;
  x86_64-unknown-linux-gnu)
    ASSET="opencode-linux-x64.tar.gz"; INTERNO="opencode"; SALIDA="opencode-${TARGET}"
    SHA="e149d32ee5667c0cd5fb84d0bf8393b312e93782eeb4d74d29bbb0392de7133c" ;;
  aarch64-unknown-linux-gnu)
    ASSET="opencode-linux-arm64.tar.gz"; INTERNO="opencode"; SALIDA="opencode-${TARGET}"
    SHA="db9b53eae485da969a0a855bca465f9901dd84676384f724f320e3ccc5a9b107" ;;
  *)
    echo "error: target no soportado: $TARGET" >&2
    exit 1
    ;;
esac

DESTINO="$BINARIES_DIR/$SALIDA"

# --- 3) Salida temprana si ya está y es el correcto -------------------------
# Se compara el SHA del binario YA EXTRAÍDO contra el que reporta una
# descarga previa marcada. Sin esta marca, `build-backend.sh` volvería a
# bajar 40 MB en cada compilación.
MARCA="$BINARIES_DIR/.opencode-${TARGET}.sha256"
if [[ -x "$DESTINO" && -f "$MARCA" ]]; then
  if [[ "$(cat "$MARCA")" == "$SHA" ]]; then
    echo "==> opencode ${VERSION} para ${TARGET} ya está en binaries/ (SHA verificado)."
    exit 0
  fi
  echo "==> opencode en binaries/ es de otra versión; se reemplaza."
fi

for bin in curl shasum; do
  command -v "$bin" >/dev/null 2>&1 || { echo "error: falta '$bin' en el PATH." >&2; exit 1; }
done

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "==> Descargando opencode ${VERSION} (${ASSET}) para ${TARGET}..."
curl -fsSL --retry 3 --retry-delay 2 "${BASE_URL}/${ASSET}" -o "$TMP/$ASSET"

echo "==> Verificando SHA-256..."
REAL="$(shasum -a 256 "$TMP/$ASSET" | awk '{print $1}')"
if [[ "$REAL" != "$SHA" ]]; then
  echo "error: SHA-256 no coincide para $ASSET" >&2
  echo "  esperado: $SHA" >&2
  echo "  obtenido: $REAL" >&2
  echo "  No se instala nada. Si opencode publicó un release nuevo con el mismo" >&2
  echo "  número, actualiza la tabla de este script a conciencia -- no borres" >&2
  echo "  la comprobación." >&2
  exit 1
fi

echo "==> Extrayendo..."
case "$ASSET" in
  *.zip)
    command -v unzip >/dev/null 2>&1 || { echo "error: falta 'unzip'." >&2; exit 1; }
    unzip -q -o "$TMP/$ASSET" -d "$TMP/extraido"
    ;;
  *.tar.gz)
    mkdir -p "$TMP/extraido"
    tar xzf "$TMP/$ASSET" -C "$TMP/extraido"
    ;;
esac

ORIGEN="$TMP/extraido/$INTERNO"
if [[ ! -f "$ORIGEN" ]]; then
  # El asset se verificó por SHA, así que si el ejecutable no está donde se
  # esperaba es que el formato del release cambió: se dice, no se adivina.
  echo "error: el archivo descargado no trae '$INTERNO' en la raíz." >&2
  echo "  contenido: $(ls -A "$TMP/extraido" | tr '\n' ' ')" >&2
  exit 1
fi

install -m 0755 "$ORIGEN" "$DESTINO"
printf '%s' "$SHA" > "$MARCA"

echo "==> Listo: $DESTINO"
"$DESTINO" --version >/dev/null 2>&1 && echo "==> Comprobado: responde a --version." || \
  echo "aviso: el binario no respondió a --version (normal si el target no es el de esta máquina)."
