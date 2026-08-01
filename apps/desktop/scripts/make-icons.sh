#!/usr/bin/env bash
# apps/desktop/scripts/make-icons.sh
#
# Genera el set de iconos que pide `tauri.conf.json` (`bundle.icon`) a partir
# de UNA sola imagen fuente: apps/desktop/assets/icon-source.png (PNG
# cuadrado, ideal 1024x1024, fondo transparente u opaco). El repo ya trae un
# placeholder monocromo simple ahí — este script NO lo dibuja, solo lo
# convierte a los formatos/tamaños que necesita cada plataforma.
#
# Para reemplazar el placeholder por el logo real de Edecán: pisa
# apps/desktop/assets/icon-source.png con tu propio PNG cuadrado y vuelve a
# correr este script.
#
# Requisitos (solo herramientas de macOS + python3 de la stdlib, nada de
# ImageMagick/PIL obligatorio):
#   - sips     (macOS, siempre presente) — redimensiona PNGs.
#   - iconutil (macOS, siempre presente) — arma icon.icns desde un .iconset.
#   - python3  (stdlib únicamente: struct) — empaqueta icon.ico (formato
#     "PNG-in-ICO", soportado desde Windows Vista, evita depender de Pillow).
#
# Si `sips`/`iconutil` no existen (no estás en macOS), el script avisa y no
# rompe nada: podés generar los mismos archivos con cualquier herramienta
# equivalente (ImageMagick `convert`, Pillow, icoutils, etc.) y dejarlos en
# apps/desktop/src-tauri/icons/ con los mismos nombres de archivo.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC_PNG="$DESKTOP_DIR/assets/icon-source.png"
ICONS_DIR="$DESKTOP_DIR/src-tauri/icons"

if [[ ! -f "$SRC_PNG" ]]; then
  echo "error: no existe $SRC_PNG (imagen fuente). Poné ahí un PNG cuadrado y volvé a correr este script." >&2
  exit 1
fi

if ! command -v sips >/dev/null 2>&1 || ! command -v iconutil >/dev/null 2>&1; then
  echo "aviso: este script necesita 'sips' e 'iconutil' (herramientas de macOS)." >&2
  echo "       No estás en macOS o no están en el PATH — no se generó nada." >&2
  echo "       Generá manualmente los archivos de $ICONS_DIR con una herramienta equivalente." >&2
  exit 1
fi

mkdir -p "$ICONS_DIR"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

echo "==> Generando PNGs sueltos que usa tauri.conf.json (bundle.icon)…"
sips -z 32 32 "$SRC_PNG" --out "$ICONS_DIR/32x32.png" >/dev/null
sips -z 128 128 "$SRC_PNG" --out "$ICONS_DIR/128x128.png" >/dev/null
sips -z 256 256 "$SRC_PNG" --out "$ICONS_DIR/128x128@2x.png" >/dev/null

echo "==> Armando .iconset para icon.icns…"
ICONSET="$WORK_DIR/icon.iconset"
mkdir -p "$ICONSET"
declare -a SIZES=(16 32 64 128 256 512)
for sz in "${SIZES[@]}"; do
  sips -z "$sz" "$sz" "$SRC_PNG" --out "$ICONSET/icon_${sz}x${sz}.png" >/dev/null
  sz2x=$((sz * 2))
  sips -z "$sz2x" "$sz2x" "$SRC_PNG" --out "$ICONSET/icon_${sz}x${sz}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$ICONS_DIR/icon.icns"

echo "==> Empaquetando icon.ico (PNG-in-ICO, 16/32/48/64/128/256)…"
declare -a ICO_SIZES=(16 32 48 64 128 256)
ICO_PNGS=()
for sz in "${ICO_SIZES[@]}"; do
  out="$WORK_DIR/ico_${sz}.png"
  sips -z "$sz" "$sz" "$SRC_PNG" --out "$out" >/dev/null
  ICO_PNGS+=("$out")
done

python3 "$SCRIPT_DIR/_pack_ico.py" "$ICONS_DIR/icon.ico" "${ICO_PNGS[@]}"

echo "==> Listo. Archivos en $ICONS_DIR:"
ls -la "$ICONS_DIR"
