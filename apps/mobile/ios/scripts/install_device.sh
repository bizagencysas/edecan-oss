#!/usr/bin/env bash
set -euo pipefail

# Instalador local para el dispositivo configurado por el operador.
DEVICE_ID="${EDECAN_IOS_DEVICE_ID:-}"
TEAM_ID="${DEVELOPMENT_TEAM:-}"
BUNDLE_ID="${EDECAN_IOS_BUNDLE_ID:-cc.edecan.app}"
APP_GROUP="${EDECAN_IOS_APP_GROUP:-group.cc.edecan.app}"
BUILD_NUMBER="${CURRENT_PROJECT_VERSION:-54}"
DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode-beta.app/Contents/Developer}"

if [[ -z "$DEVICE_ID" || -z "$TEAM_ID" ]]; then
  echo "ERROR: configura EDECAN_IOS_DEVICE_ID y DEVELOPMENT_TEAM." >&2
  exit 2
fi

# Xcode puede intentar descargar o crear perfiles con
# `-allowProvisioningUpdates`, pero no debe hacerlo silenciosamente en una
# ruta que instala en el iPhone. El App Group tiene que existir en Apple
# Developer y aparecer firmado tanto en la app como en la extensión. El
# override explícito permite al operador autorizar la consulta/descarga cuando
# ya verificó ese registro fuera de este script.
if [[ "${EDECAN_ALLOW_PROFILE_FETCH:-0}" != "1" ]]; then
  profile_ok=0
  for profile_dir in \
    "${HOME}/Library/Developer/Xcode/UserData/Provisioning Profiles" \
    "${HOME}/Library/MobileDevice/Provisioning Profiles"
  do
    [[ -d "$profile_dir" ]] || continue
    for profile in "$profile_dir"/*.mobileprovision; do
      [[ -f "$profile" ]] || continue
      profile_plist="$(security cms -D -i "$profile" 2>/dev/null || true)"
      [[ -n "$profile_plist" ]] || continue
      profile_team="$(plutil -extract TeamIdentifier.0 raw -o - - 2>/dev/null <<<"$profile_plist" || true)"
      [[ "$profile_team" == "$TEAM_ID" ]] || continue
      if grep -q "$APP_GROUP" <<<"$profile_plist"; then
        profile_ok=1
        break 2
      fi
    done
  done
  if [[ "$profile_ok" != "1" ]]; then
    cat >&2 <<EOF
ERROR: no hay un provisioning profile local del Team configurado que declare
$APP_GROUP. Se aborta antes de compilar para proteger el pairing.
Registra el App Group en Apple Developer, asócialo a la app y a EdecanShare,
descarga perfiles nuevos y vuelve a ejecutar. Si ya verificaste todo y quieres
autorizar a Xcode a obtener perfiles, usa EDECAN_ALLOW_PROFILE_FETCH=1.
EOF
    exit 2
  fi
fi

build_dir="${EDECAN_BUILD_DIR:-/tmp/edecan-ios-device-build}"
mkdir -p "$build_dir"

DESTINATION="id=$DEVICE_ID"
if ! xcodebuild -showdestinations -project Edecan.xcodeproj -scheme EdecanApp 2>/dev/null | grep -q "$DEVICE_ID"; then
  DESTINATION="generic/platform=iOS"
fi

DEVELOPER_DIR="$DEVELOPER_DIR" xcodebuild \
  -project Edecan.xcodeproj \
  -scheme EdecanApp \
  -configuration Debug \
  -destination "$DESTINATION" \
  -derivedDataPath "$build_dir" \
  DEVELOPMENT_TEAM="$TEAM_ID" \
  EDECAN_IOS_BUNDLE_ID="$BUNDLE_ID" \
  CURRENT_PROJECT_VERSION="$BUILD_NUMBER" \
  CODE_SIGN_STYLE=Automatic \
  -allowProvisioningUpdates \
  build

app_path="$build_dir/Build/Products/Debug-iphoneos/Edecan.app"
test -d "$app_path"
share_path="$app_path/PlugIns/EdecanShare.appex"
test -d "$share_path"

for signed_path in "$app_path" "$share_path"; do
  entitlements="$(codesign -d --entitlements :- "$signed_path" 2>/dev/null || true)"
  if ! grep -q "$APP_GROUP" <<<"$entitlements"; then
    echo "ERROR: $signed_path no tiene el App Group $APP_GROUP; no se instala." >&2
    exit 2
  fi
done

echo "Verificando disponibilidad del dispositivo ${DEVICE_ID}..."
installed=0
for attempt in {1..15}; do
  if xcrun devicectl device install app --device "$DEVICE_ID" "$app_path" 2>/dev/null; then
    installed=1
    break
  fi
  echo "Dispositivo no listo (¿pantalla bloqueada?). Reintentando intento $attempt/15 en 2s…"
  sleep 2
done

if [[ "$installed" != "1" ]]; then
  echo "Intentando instalación final con salida detallada:"
  xcrun devicectl device install app --device "$DEVICE_ID" "$app_path"
fi

xcrun devicectl device process launch --device "$DEVICE_ID" --terminate-existing --activate "$BUNDLE_ID"
echo "Instalación segura completada: $BUNDLE_ID build $BUILD_NUMBER en $DEVICE_ID"
