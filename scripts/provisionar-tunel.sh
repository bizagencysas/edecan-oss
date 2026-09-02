#!/usr/bin/env bash
# Aprovisiona un túnel de Cloudflare para la computadora de UN tercero, del
# lado del DUEÑO (donde viven las credenciales de Cloudflare ~/.cloudflared).
#
# Resultado: una carpeta lista para entregar al tercero. El/la dueño/a de esa
# computadora solo:
#   1. instala la app de escritorio de Edecán,
#   2. copia esta carpeta en su data dir (Application Support/cc.edecan.desktop/),
#   3. abre la app, crea su cuenta y toca "Conectar teléfono" -> el QR usa el
#      hostname público -> el iPhone lo escanea desde cualquier red.
#
# Uso (en la Mac del dueño, desde la raíz del repo):
#   bash scripts/provisionar-tunel.sh  nombre-para-la-maquina
#   # crea  edecan-<nombre>.example.com  y deja  ./tuneles-<nombre>/  lista
#
# Requiere: cloudflared instalado y cert.pem de la cuenta (~/.cloudflared).
set -euo pipefail

DOMINIO="${DOMINIO:-example.com}"
NOMBRE="${1:?uso: bash scripts/provisionar-tunel.sh NOMBRE}"
HOST="edecan-${NOMBRE}.${DOMINIO}"
DEST="tuneles-${NOMBRE}"
PUERTO="${EDECAN_TUNNEL_PORT:-8765}"

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "ERROR: cloudflared no está (brew install cloudflared)"
  exit 1
fi
if [ ! -f ~/.cloudflared/cert.pem ]; then
  echo "ERROR: falta ~/.cloudflared/cert.pem (loguéate: cloudflared tunnel login)"
  exit 1
fi

echo "==> Creando túnel para $HOST (puerto local $PUERTO)..."
TUNNEL_ID=$(cloudflared tunnel create "$NOMBRE" 2>/dev/null | grep -oE '[0-9a-f-]{36}' | head -1 || true)
if [ -z "$TUNNEL_ID" ]; then
  # Puede que ya exista (idempotente)
  TUNNEL_ID=$(cloudflared tunnel list 2>/dev/null | grep -w "$NOMBRE" | awk '{print $1}' | head -1)
fi
if [ -z "$TUNNEL_ID" ]; then
  echo "ERROR: no pude crear el túnel. Revisa el login de cloudflared."
  exit 1
fi
echo "   túnel=$TUNNEL_ID"

cloudflared tunnel route dns "$TUNNEL_ID" "$HOST" >/dev/null 2>&1 || echo "   (route dns: ya estaba o se creó igual)"

mkdir -p "$DEST"
cat > "$DEST/cloudflare-tunnel.yml" <<EOF
tunnel: $TUNNEL_ID
credentials-file: $DEST/$TUNNEL_ID.json

protocol: http2
retries: 5
grace-period: 30s

ingress:
  - hostname: $HOST
    service: http://127.0.0.1:$PUERTO
  - service: http_status:404
EOF
cloudflared tunnel token "$TUNNEL_ID" > "$DEST/token.txt" 2>/dev/null || true

# Credenciales del túnel (que cloudflared guardó tras `tunnel create`)
CRED=$(cloudflared tunnel list 2>/dev/null | grep -w "$NOMBRE" | grep -oE '/Users/[^ ]+\.json' | head -1 || true)
if [ -n "$CRED" ] && [ -f "$CRED" ]; then
  cp "$CRED" "$DEST/$TUNNEL_ID.json"
fi
cp ~/.cloudflared/cert.pem "$DEST/cert.pem" 2>/dev/null || true

chmod 600 "$DEST"/*.json "$DEST"/*.txt 2>/dev/null || true
echo
echo "==> LISTO. Entrega la carpeta '$DEST' al dueño/a de esa computadora."
echo "   Hostname público: https://$HOST"
echo "   Instrucciones para el tercero:"
echo "     1. Instala la app de escritorio de Edecán."
echo "     2. Copia el CONTENIDO de '$DEST' en:"
echo "        Mac:   ~/Library/Application Support/cc.edecan.desktop/"
echo "        Win:   %APPDATA%/cc.edecan.desktop/"
echo "        Linux: ~/.config/cc.edecan.desktop/"
echo "     3. Abre la app, crea su cuenta y toca 'Conectar teléfono'."
echo "     4. El QR usa https://$HOST — escanéalo desde su iPhone en cualquier red."
echo
echo "   (Opcional, si la app no levanta el túnel sola: cloudflared tunnel run --config cloudflare-tunnel.yml)"