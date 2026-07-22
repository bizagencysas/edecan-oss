#!/usr/bin/env bash
# Empaqueta packages/fydesign-engine como recurso autosuficiente de Tauri.
#
# El resultado contiene el codigo del motor, dependencias exactas de
# package-lock.json, Chromium de Playwright y un runtime Node 22 oficial como
# externalBin (`node-runtime`). No usa npx ni el Node/Chrome del equipo destino.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$DESKTOP_DIR/../.." && pwd)"
ENGINE_SOURCE="$REPO_ROOT/packages/fydesign-engine"
ENGINE_RESOURCE="$DESKTOP_DIR/packaging/studio-engine"
BINARIES_DIR="$DESKTOP_DIR/src-tauri/binaries"
NODE_VERSION="22.17.0"
NODE_DIST_BASE="https://nodejs.org/dist/v$NODE_VERSION"
YTDLP_VERSION="2026.06.09"
YTDLP_DIST_BASE="https://github.com/yt-dlp/yt-dlp/releases/download/$YTDLP_VERSION"
FFMPEG_VERSION="8.1.2"
FFMPEG_DIST_BASE="https://ffmpeg.martin-riedl.de/download"
FFMPEG_GPL_URL="https://raw.githubusercontent.com/FFmpeg/FFmpeg/n$FFMPEG_VERSION/COPYING.GPLv3"
FFMPEG_GPL_SHA256="8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903"
YTDLP_LICENSE_URL="https://raw.githubusercontent.com/yt-dlp/yt-dlp/$YTDLP_VERSION/LICENSE"
YTDLP_LICENSE_SHA256="7e12e5df4bae12cb21581ba157ced20e1986a0508dd10d0e8a4ab9a4cf94e85c"
YTDLP_THIRD_PARTY_URL="https://raw.githubusercontent.com/yt-dlp/yt-dlp/$YTDLP_VERSION/THIRD_PARTY_LICENSES.txt"
YTDLP_THIRD_PARTY_SHA256="b085c65586a953cdb4b13c6390d63ec984d66912e4b6a19e66ba3582f2ed104b"

for bin in curl rustc tar unzip; do
  if ! command -v "$bin" >/dev/null 2>&1; then
    echo "error: falta '$bin' para empaquetar FyDesign Studio." >&2
    exit 1
  fi
done
if [[ ! -f "$ENGINE_SOURCE/package-lock.json" ]]; then
  echo "error: falta packages/fydesign-engine/package-lock.json; el bundle debe ser reproducible." >&2
  exit 1
fi

TARGET_TRIPLE="$(rustc -Vv | awk '/^host:/ { print $2 }')"
case "$TARGET_TRIPLE" in
  aarch64-apple-darwin)
    NODE_ASSET="node-v$NODE_VERSION-darwin-arm64.tar.gz"
    NODE_SHA256="615dda58b5fb41fad2be43940b6398ca56554cbe05800953afadc724729cb09e"
    YTDLP_ASSET="yt-dlp_macos"
    YTDLP_SHA256="b82c3626952e6c14eaf654cc565866775ffd0b9ffb7021628ac59b42c2f4f244"
    FFMPEG_ASSET_PATH="macos/arm64/1783011502_8.1.2"
    FFMPEG_SHA256="ef1aa60006c7b77ce170c1608c08d8e4ba1c30c5746f2ac986ded932d0ac2c3c"
    FFPROBE_SHA256="c39787f4af7a3932502d2d48db6f6feaaa836b48a73ef78c32cc3285df61dfaf"
    ;;
  x86_64-apple-darwin)
    NODE_ASSET="node-v$NODE_VERSION-darwin-x64.tar.gz"
    NODE_SHA256="c39c8ec3cdadedfcc75de0cb3305df95ae2aecebc5db8d68a9b67bd74616d2ad"
    YTDLP_ASSET="yt-dlp_macos"
    YTDLP_SHA256="b82c3626952e6c14eaf654cc565866775ffd0b9ffb7021628ac59b42c2f4f244"
    FFMPEG_ASSET_PATH="macos/amd64/1783018342_8.1.2"
    FFMPEG_SHA256="a52ef43883f44c219766d4b3bdde4e635b35465d0b704c01c3a0566b59775df9"
    FFPROBE_SHA256="5408ca588c8c72b0dde3afe676d0a7acf25ef97e55ae6eba5c7bede1cda42695"
    ;;
  aarch64-unknown-linux-gnu)
    NODE_ASSET="node-v$NODE_VERSION-linux-arm64.tar.gz"
    NODE_SHA256="3e99df8b01b27dc8b334a2a30d1cd500442b3b0877d217b308fd61a9ccfc33d4"
    YTDLP_ASSET="yt-dlp_linux_aarch64"
    YTDLP_SHA256="cabd246445bdfde0eda0dfe68bbe90354be83f3fdbbf077df11a2ea55f41cdbd"
    FFMPEG_ASSET_PATH="linux/arm64/1783010599_8.1.2"
    FFMPEG_SHA256="ab9e16864b6bf4ae7e13bbdbdc29621be11a5c547c57af8d4250e9fa2f5e6461"
    FFPROBE_SHA256="fb78317b81cdeb614533be59e489019b754afd199670666af28f0e9574be395b"
    ;;
  x86_64-unknown-linux-gnu)
    NODE_ASSET="node-v$NODE_VERSION-linux-x64.tar.gz"
    NODE_SHA256="0fa01328a0f3d10800623f7107fbcd654a60ec178fab1ef5b9779e94e0419e1a"
    YTDLP_ASSET="yt-dlp_linux"
    YTDLP_SHA256="bf8aac79b72287a6d2043074415132558b43743a8f9461a22b0141e90f16ce66"
    FFMPEG_ASSET_PATH="linux/amd64/1783011670_8.1.2"
    FFMPEG_SHA256="56452c0bfc4ee0325cd615d62f46ba8264f62eed34f727c2224c6c84fa7b8719"
    FFPROBE_SHA256="c6f2d36e98f9a4445fad0b0be539f4c4faf13fd502116bf131becd53f56cd390"
    ;;
  *)
    echo "error: FyDesign Studio no tiene Node 22 fijado para $TARGET_TRIPLE." >&2
    exit 1
    ;;
esac

BUILD_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/edecan-studio.XXXXXX")"
cleanup() {
  rm -rf "$BUILD_ROOT"
}
trap cleanup EXIT

NODE_ARCHIVE="$BUILD_ROOT/$NODE_ASSET"
echo "==> [Studio 1/5] Descargando Node v$NODE_VERSION oficial para $TARGET_TRIPLE..."
curl --fail --location --silent --show-error \
  "$NODE_DIST_BASE/$NODE_ASSET" --output "$NODE_ARCHIVE"
if command -v shasum >/dev/null 2>&1; then
  ACTUAL_SHA256="$(shasum -a 256 "$NODE_ARCHIVE" | awk '{ print $1 }')"
elif command -v sha256sum >/dev/null 2>&1; then
  ACTUAL_SHA256="$(sha256sum "$NODE_ARCHIVE" | awk '{ print $1 }')"
else
  echo "error: falta shasum/sha256sum para verificar Node 22." >&2
  exit 1
fi
if [[ "$ACTUAL_SHA256" != "$NODE_SHA256" ]]; then
  echo "error: checksum invalido para $NODE_ASSET." >&2
  exit 1
fi
tar -xzf "$NODE_ARCHIVE" -C "$BUILD_ROOT"
NODE_HOME="$BUILD_ROOT/${NODE_ASSET%.tar.gz}"
NODE_RUNTIME="$NODE_HOME/bin/node"
NPM_CLI="$NODE_HOME/lib/node_modules/npm/bin/npm-cli.js"
if [[ "$($NODE_RUNTIME --version)" != "v$NODE_VERSION" || ! -f "$NPM_CLI" ]]; then
  echo "error: el archivo oficial de Node no contiene el runtime/npm esperados." >&2
  exit 1
fi

STAGED_ENGINE="$BUILD_ROOT/fydesign-engine"
mkdir -p "$STAGED_ENGINE"
cp "$ENGINE_SOURCE/package.json" "$ENGINE_SOURCE/package-lock.json" \
  "$ENGINE_SOURCE/tsconfig.json" "$ENGINE_SOURCE/LICENSE" \
  "$ENGINE_SOURCE/NOTICE" "$ENGINE_SOURCE/README.md" \
  "$ENGINE_SOURCE/CAPABILITIES.md" "$ENGINE_SOURCE/PORTING_MANIFEST.json" \
  "$STAGED_ENGINE/"
cp -R "$ENGINE_SOURCE/mcp" "$ENGINE_SOURCE/scripts" "$ENGINE_SOURCE/src" "$STAGED_ENGINE/"

echo "==> [Studio 2/5] Instalando packages/fydesign-engine con npm ci..."
(
  cd "$STAGED_ENGINE"
  "$NODE_RUNTIME" "$NPM_CLI" ci --ignore-scripts
)

echo "==> [Studio 3/5] Instalando ffmpeg/ffprobe redistribuibles y yt-dlp fijados..."
TOOLS_DIR="$STAGED_ENGINE/tools"
LICENSES_DIR="$TOOLS_DIR/licenses"
mkdir -p "$TOOLS_DIR" "$LICENSES_DIR"
FFMPEG_ARCHIVE="$BUILD_ROOT/ffmpeg.zip"
FFPROBE_ARCHIVE="$BUILD_ROOT/ffprobe.zip"
curl --fail --location --silent --show-error \
  "$FFMPEG_DIST_BASE/$FFMPEG_ASSET_PATH/ffmpeg.zip" --output "$FFMPEG_ARCHIVE"
curl --fail --location --silent --show-error \
  "$FFMPEG_DIST_BASE/$FFMPEG_ASSET_PATH/ffprobe.zip" --output "$FFPROBE_ARCHIVE"
if command -v shasum >/dev/null 2>&1; then
  FFMPEG_ACTUAL_SHA256="$(shasum -a 256 "$FFMPEG_ARCHIVE" | awk '{ print $1 }')"
  FFPROBE_ACTUAL_SHA256="$(shasum -a 256 "$FFPROBE_ARCHIVE" | awk '{ print $1 }')"
else
  FFMPEG_ACTUAL_SHA256="$(sha256sum "$FFMPEG_ARCHIVE" | awk '{ print $1 }')"
  FFPROBE_ACTUAL_SHA256="$(sha256sum "$FFPROBE_ARCHIVE" | awk '{ print $1 }')"
fi
if [[ "$FFMPEG_ACTUAL_SHA256" != "$FFMPEG_SHA256" || "$FFPROBE_ACTUAL_SHA256" != "$FFPROBE_SHA256" ]]; then
  echo "error: checksum invalido para ffmpeg/ffprobe $FFMPEG_VERSION ($TARGET_TRIPLE)." >&2
  exit 1
fi
mkdir -p "$BUILD_ROOT/ffmpeg" "$BUILD_ROOT/ffprobe"
unzip -q "$FFMPEG_ARCHIVE" -d "$BUILD_ROOT/ffmpeg"
unzip -q "$FFPROBE_ARCHIVE" -d "$BUILD_ROOT/ffprobe"
FFMPEG_SOURCE="$(find "$BUILD_ROOT/ffmpeg" -type f -name ffmpeg -print -quit)"
FFPROBE_SOURCE="$(find "$BUILD_ROOT/ffprobe" -type f -name ffprobe -print -quit)"
if [[ -z "$FFMPEG_SOURCE" || -z "$FFPROBE_SOURCE" ]]; then
  echo "error: los archivos fijados no contienen ffmpeg y ffprobe." >&2
  exit 1
fi
cp "$FFMPEG_SOURCE" "$TOOLS_DIR/ffmpeg"
cp "$FFPROBE_SOURCE" "$TOOLS_DIR/ffprobe"
curl --fail --location --silent --show-error "$YTDLP_DIST_BASE/$YTDLP_ASSET" --output "$TOOLS_DIR/yt-dlp"
if command -v shasum >/dev/null 2>&1; then
  YTDLP_ACTUAL_SHA256="$(shasum -a 256 "$TOOLS_DIR/yt-dlp" | awk '{ print $1 }')"
else
  YTDLP_ACTUAL_SHA256="$(sha256sum "$TOOLS_DIR/yt-dlp" | awk '{ print $1 }')"
fi
if [[ "$YTDLP_ACTUAL_SHA256" != "$YTDLP_SHA256" ]]; then
  echo "error: checksum invalido para $YTDLP_ASSET." >&2
  exit 1
fi
chmod +x "$TOOLS_DIR/ffmpeg" "$TOOLS_DIR/ffprobe" "$TOOLS_DIR/yt-dlp"
FFMPEG_LICENSE_OUTPUT="$("$TOOLS_DIR/ffmpeg" -L 2>&1)"
if grep -Eqi '(enable-nonfree|not legally redistributable|unredistributable)' <<<"$FFMPEG_LICENSE_OUTPUT"; then
  echo "error: la build de ffmpeg contiene componentes no redistribuibles." >&2
  exit 1
fi
if ! grep -qi 'GNU General Public License' <<<"$FFMPEG_LICENSE_OUTPUT"; then
  echo "error: la build de ffmpeg no declaró su licencia GPL esperada." >&2
  exit 1
fi
curl --fail --location --silent --show-error "$FFMPEG_GPL_URL" --output "$LICENSES_DIR/GPL-3.0.txt"
curl --fail --location --silent --show-error "$YTDLP_LICENSE_URL" --output "$LICENSES_DIR/YT-DLP-UNLICENSE.txt"
curl --fail --location --silent --show-error "$YTDLP_THIRD_PARTY_URL" --output "$LICENSES_DIR/YT-DLP-THIRD-PARTY-LICENSES.txt"
if command -v shasum >/dev/null 2>&1; then
  GPL_ACTUAL_SHA256="$(shasum -a 256 "$LICENSES_DIR/GPL-3.0.txt" | awk '{ print $1 }')"
  YTDLP_LICENSE_ACTUAL_SHA256="$(shasum -a 256 "$LICENSES_DIR/YT-DLP-UNLICENSE.txt" | awk '{ print $1 }')"
  YTDLP_THIRD_PARTY_ACTUAL_SHA256="$(shasum -a 256 "$LICENSES_DIR/YT-DLP-THIRD-PARTY-LICENSES.txt" | awk '{ print $1 }')"
else
  GPL_ACTUAL_SHA256="$(sha256sum "$LICENSES_DIR/GPL-3.0.txt" | awk '{ print $1 }')"
  YTDLP_LICENSE_ACTUAL_SHA256="$(sha256sum "$LICENSES_DIR/YT-DLP-UNLICENSE.txt" | awk '{ print $1 }')"
  YTDLP_THIRD_PARTY_ACTUAL_SHA256="$(sha256sum "$LICENSES_DIR/YT-DLP-THIRD-PARTY-LICENSES.txt" | awk '{ print $1 }')"
fi
if [[ "$GPL_ACTUAL_SHA256" != "$FFMPEG_GPL_SHA256" || "$YTDLP_LICENSE_ACTUAL_SHA256" != "$YTDLP_LICENSE_SHA256" || "$YTDLP_THIRD_PARTY_ACTUAL_SHA256" != "$YTDLP_THIRD_PARTY_SHA256" ]]; then
  echo "error: checksum invalido para las licencias de herramientas multimedia." >&2
  exit 1
fi
"$TOOLS_DIR/ffmpeg" -buildconf >"$LICENSES_DIR/FFMPEG-BUILD-CONFIGURATION.txt" 2>&1
printf '%s\n' \
  "FFmpeg/ffprobe $FFMPEG_VERSION — separate GPL-3.0-or-later executables." \
  "Binary source: $FFMPEG_DIST_BASE/$FFMPEG_ASSET_PATH/" \
  "Corresponding source: https://github.com/FFmpeg/FFmpeg/tree/n$FFMPEG_VERSION" \
  "Build scripts: https://git.martin-riedl.de/ffmpeg/build-script" \
  >"$LICENSES_DIR/FFMPEG-SOURCE.txt"
printf '%s\n' \
  "yt-dlp $YTDLP_VERSION — separate executable; the PyInstaller build is GPLv3+ because of bundled components." \
  "Source: https://github.com/yt-dlp/yt-dlp/tree/$YTDLP_VERSION" \
  >"$LICENSES_DIR/YT-DLP-SOURCE.txt"
"$TOOLS_DIR/ffprobe" -version >/dev/null
"$TOOLS_DIR/yt-dlp" --version >/dev/null

echo "==> [Studio 4/5] Descargando Chromium fijado por Playwright..."
PLAYWRIGHT_BROWSERS_PATH="$STAGED_ENGINE/playwright-browsers" \
  "$NODE_RUNTIME" "$STAGED_ENGINE/node_modules/playwright/cli.js" install --only-shell chromium
(
  cd "$STAGED_ENGINE"
  PLAYWRIGHT_BROWSERS_PATH="$STAGED_ENGINE/playwright-browsers" \
    "$NODE_RUNTIME" --input-type=module -e \
    "import { chromium } from 'playwright'; const browser = await chromium.launch({ headless: true }); await browser.close();"
  "$NODE_RUNTIME" "$NPM_CLI" prune --omit=dev --ignore-scripts
)

# Algunos SDKs publican paquetes `@types/*` como dependencias de producción aunque
# solo contienen declaraciones. tsx transpila sin type-check en runtime, así que no
# forman parte del recurso ejecutable. El lock reproducible permanece en el source.
rm -rf "$STAGED_ENGINE/node_modules/@types"
if [[ -d "$STAGED_ENGINE/node_modules/typescript" || -d "$STAGED_ENGINE/node_modules/@sparticuz" ]]; then
  echo "error: Studio conservó una dependencia de desarrollo o Chromium legado." >&2
  exit 1
fi
"$NODE_RUNTIME" -e \
  'const fs=require("node:fs");const p=process.argv[1];const value=JSON.parse(fs.readFileSync(p,"utf8"));delete value.devDependencies;fs.writeFileSync(p,JSON.stringify(value,null,2)+"\n");' \
  "$STAGED_ENGINE/package.json"
rm -f "$STAGED_ENGINE/package-lock.json" \
  "$STAGED_ENGINE/node_modules/.package-lock.json"
"$NODE_RUNTIME" -e \
  'const cp=require("node:child_process"),fs=require("node:fs"),path=require("node:path");const root=path.resolve(process.argv[1]),npm=process.argv[2];for(let round=0;round<8;round++){let out="";try{out=cp.execFileSync(process.execPath,[npm,"ls","--json","--omit=dev","--depth=0"],{cwd:root,encoding:"utf8",stdio:["ignore","pipe","pipe"]});}catch(error){out=String(error.stdout||"");}const tree=JSON.parse(out);const extras=Object.entries(tree.dependencies||{}).filter(([,meta])=>meta&&meta.extraneous).map(([name])=>name);if(!extras.length)process.exit(0);for(const name of extras){const target=path.resolve(root,"node_modules",name),modules=path.resolve(root,"node_modules")+path.sep;if(!target.startsWith(modules))throw new Error("unsafe dependency path");fs.rmSync(target,{recursive:true,force:true});}}process.exit(1);' \
  "$STAGED_ENGINE" "$NPM_CLI"
(
  cd "$STAGED_ENGINE"
  "$NODE_RUNTIME" "$NPM_CLI" ls --omit=dev --depth=0 >/dev/null
)

echo "==> [Studio 5/5] Instalando recurso y externalBin de Tauri..."
rm -rf "$ENGINE_RESOURCE"
mkdir -p "$(dirname "$ENGINE_RESOURCE")" "$BINARIES_DIR"
mv "$STAGED_ENGINE" "$ENGINE_RESOURCE"

find "$BINARIES_DIR" -maxdepth 1 -name 'fydesign-node-*' -exec rm -f {} +
NODE_SIDECAR="$BINARIES_DIR/fydesign-node-$TARGET_TRIPLE"
cp "$NODE_RUNTIME" "$NODE_SIDECAR"
chmod +x "$NODE_SIDECAR"
if [[ "$($NODE_SIDECAR --version)" != "v$NODE_VERSION" ]]; then
  echo "error: el runtime Node copiado para Studio no arranca." >&2
  exit 1
fi

echo "==> Studio listo: recurso $ENGINE_RESOURCE + Node runtime $NODE_SIDECAR"
