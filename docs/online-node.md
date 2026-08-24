# Edecán online

Edecán puede funcionar en dos lugares con el mismo código:

- **En tu computadora:** añade IDE, terminal, archivos locales, control remoto
  y modelos CLI. La aplicación de escritorio permanece residente en la barra
  de menús aunque su ventana esté cerrada.
- **En tu nodo online:** mantiene disponibles chat, memoria, agentes de
  llamadas entrantes y salientes, resúmenes, recordatorios, automatizaciones,
  archivos y notificaciones cuando la computadora está apagada.

El nodo online es autohospedado. No usa una base de datos, una cuenta de Apple
ni una nube perteneciente a los mantenedores de Edecán. Cada instalación tiene
su dominio, sus secretos, sus datos y sus credenciales.

## Instalación

Necesitas:

1. Un servidor Linux con Docker Compose v2.
2. Los puertos 80 y 443 abiertos.
3. Un subdominio, por ejemplo `edecan.tudominio.com`, cuyo registro DNS A o
   AAAA apunte al servidor.

En el servidor:

```bash
git clone https://github.com/your-org/edecan-oss.git
cd edecan-oss
scripts/instalar-online.sh --dominio edecan.tudominio.com --email tu@email.com
```

El instalador:

- crea `.env.online` con permisos `600`;
- genera secretos independientes y nunca sobreescribe secretos existentes;
- levanta Postgres con pgvector, Redis, almacenamiento compatible con S3,
  colas, API, worker, web y Caddy;
- obtiene y renueva TLS automáticamente;
- ejecuta migraciones antes de habilitar la API;
- valida `https://<dominio>/healthz` antes de anunciar que terminó.

`.env.online` está excluido de Git y nunca debe compartirse. Las API keys y
credenciales de cada persona se guardan cifradas por el mismo vault de Edecán.

## Operación

Para actualizar el código y reconstruir el nodo:

```bash
git pull --ff-only
scripts/instalar-online.sh --dominio edecan.tudominio.com --email tu@email.com
```

Para revisar el estado:

```bash
docker compose --env-file .env.online -p edecan-online --profile local-aws \
  -f infra/docker/compose.selfhost.yml \
  -f infra/docker/compose.online.yml ps
```

Para revisar logs:

```bash
docker compose --env-file .env.online -p edecan-online --profile local-aws \
  -f infra/docker/compose.selfhost.yml \
  -f infra/docker/compose.online.yml logs -f --tail=200
```

## Alcance y continuidad

La aplicación móvil se conecta al URL HTTPS del nodo online. El trabajo
asíncrono continúa en el worker aunque iOS o Android se minimicen. Cuando la
computadora está conectada, puede aportar capacidades locales; cuando está
apagada, el nodo no inventa acceso a su disco, terminal o pantalla, pero el
núcleo online sigue atendiendo chat, telefonía y trabajo básico.

Twilio necesita que `PUBLIC_BASE_URL` sea público y HTTPS. El instalador ya lo
configura con el dominio del nodo. Desde Edecán se conectan el número y los
agentes de llamada; los webhooks entrantes y los resúmenes posteriores llegan
al mismo nodo.
