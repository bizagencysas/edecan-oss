# Self-hosting

Edecán se puede correr fuera de la app de escritorio, con el núcleo `Apache-2.0` (todo el repo salvo `premium/`): chat con herramientas, memoria/grafo, conectores de Google y Microsoft, sociales (Meta/X/YouTube) con tus propias apps OAuth, voz web con tus propias claves STT/TTS y el companion de escritorio (agente local emparejado para IDE embebido/control remoto, `ARCHITECTURE.md` §10.12). No necesitas la capa hospedada ni el paquete `edecan_premium` para tener un asistente funcional — y, en todos los caminos de abajo, el mismo principio bring-your-own (`DIRECCION_ACTUAL.md`, "Modelo de credenciales"): tú traes tus propias credenciales de LLM/voz/conectores, Edecán nunca opera ni paga una cuenta de terceros por ti.

## Los tres caminos

| # | Camino | Para quién | Infraestructura que mantienes tú |
|---|---|---|---|
| 1 | **App de escritorio** | Casi todo el mundo — instala y usa, sin servidor. | Ninguna (corre en tu máquina). |
| 2 | **Self-host con Docker Compose** (este documento) | Quien quiere un servidor propio 24/7, sencillo — VPS, NAS, mini-PC en casa. | La de tu VPS/NAS (unos pocos USD/mes típico). |
| 3 | **Tu propia AWS con Terraform** | Quien necesita la topología de producción completa (alta disponibilidad, escala) o contrató "Tu propia nube". | Infraestructura AWS real — ver tabla de costos en `infra/terraform/README.md`. |

1. **App de escritorio** (recomendada para la mayoría) — descarga, instala, abre, ve a "Configuración" y pega tus credenciales. Cero servidor, cero Docker, cero AWS. Ver [`desktop.md`](./desktop.md).
2. **Self-host con Docker Compose** — este documento cubre este camino en detalle (§2 más abajo): instalador guiado de un comando, modo desarrollador para quien va a tocar código, actualización y backups.
3. **Tu propia AWS con Terraform** — la topología de producción completa (`infra/terraform/`) desplegada en TU cuenta de AWS. Detalle completo en [`../infra/README.md`](../infra/README.md) y [`../infra/terraform/README.md`](../infra/terraform/README.md); atajo guiado en §3 más abajo.

Este documento cubre en profundidad los caminos 2 y 3 (el 1 tiene su propio documento).

## 1. Requisitos

- **Camino 2 (self-host Docker Compose)**: **Docker** y **Docker Compose v2** (`docker compose`, sin guion) — es lo único que exige `scripts/instalar-selfhost.sh`. Si además vas a modificar código (modo desarrollador, §2.4): **Python 3.12** con [`uv`](https://docs.astral.sh/uv/) y **Node.js 20+**.
- **Camino 3 (tu propia AWS)**: Terraform >= 1.7, AWS CLI v2, una cuenta AWS propia con permisos amplios. Ver [`../infra/terraform/README.md`](../infra/terraform/README.md) para el detalle completo y la tabla de costos aproximados.
- **Todos los caminos**: al menos un proveedor de LLM conectado para poder chatear — Anthropic, cualquier endpoint OpenAI-compatible, Vertex AI, o (en modo local) Claude CLI/Codex CLI/Ollama ya instalados (ver [`proveedores-llm.md`](./proveedores-llm.md)). Todo lo demás (voz, búsqueda web, conectores) tiene un modo `stub`/offline o queda deshabilitado si no lo configuras — nunca bloquea el primer uso (`DIRECCION_ACTUAL.md`, "configuración de pocos clicks").
- **Windows**: `scripts/instalar-selfhost.sh` y `scripts/desplegar-mi-aws.sh` corren sobre **WSL2** (`wsl --install -d Ubuntu-24.04` desde una PowerShell como administrador, luego clona el repo y corre el script normal DENTRO de esa terminal de Ubuntu). Windows nativo (Git Bash/MSYS2/Cygwin) no está soportado — ambos scripts lo detectan y te lo dicen explícitamente en vez de fallar a medio camino con un error confuso.

## 2. Self-host con Docker Compose

### 2.1 Instalador guiado (recomendado)

Un solo comando levanta todo el stack (`api`, `worker`, `web` + Postgres+pgvector + Redis) en contenedores, sobre tu propio servidor:

```bash
git clone <url-de-tu-fork-o-del-repo> edecan
cd edecan
scripts/instalar-selfhost.sh
```

Qué hace por ti (detalle completo en el encabezado del propio script y en `infra/docker/compose.selfhost.yml`):

1. Verifica tu sistema (macOS/Linux/WSL2, arquitectura), que tengas `docker` + el plugin `docker compose` (v2) con el daemon respondiendo, y que haya espacio en disco razonable (aviso, no bloqueante) — se revalida en **todas** las corridas, incluso si ya instalaste antes.
2. Copia `.env.example` → `.env` si todavía no existe — nunca pisa uno que ya tengas.
3. **Genera `JWT_SECRET` y `LOCAL_MASTER_KEY`** si siguen en su placeholder público de `.env.example` (nunca toca un valor que ya hayas puesto tú). `LOCAL_MASTER_KEY` cifra el `TokenVault` (`ARCHITECTURE.md` §10.4) — perderlo tras tener datos reales los deja indescifrables, ver [`runbooks/rotacion-claves.md`](./runbooks/rotacion-claves.md).
4. Apunta `DATABASE_URL`/`REDIS_URL` a los nombres de servicio del propio compose (`postgres`/`redis`, no `localhost`).
5. Te pregunta si quieres levantar **LocalStack** (profile `local-aws`) para que S3/SQS funcionen sin cuenta AWS — ver §2.2 más abajo.
6. Imprime un resumen completo (qué cambió en tu `.env`, qué comando va a correr, qué puertos quedan publicados) y pide tu confirmación antes del único paso con efecto real: `docker compose -p edecan [...] up -d --build`.

Los pasos 2-5 quedan marcados como hechos en un archivo de estado (`.edecan-install-state`, en la raíz del repo) — si interrumpes el script o falla algo, **corre exactamente el mismo comando de nuevo** y retoma donde se quedó, sin repetir lo ya hecho ni volver a preguntarte lo mismo (ver §2.1bis más abajo, "Reanudable, `--force` y `--dry-run`").

Al terminar: abre `http://localhost:3000`, regístrate, y sigue el wizard de bienvenida (o ve directo a `/app/configuracion`) para conectar tu LLM — ver §4 más abajo y [`primeros-pasos.md`](./primeros-pasos.md).

**Las migraciones se aplican solas.** `compose.selfhost.yml` incluye un servicio `migrate` (`infra/docker/Dockerfile.migrate`) que corre `alembic upgrade head` una sola vez y sale; `api`/`worker` esperan a que termine bien (`condition: service_completed_successfully`) antes de arrancar — no hace falta ningún paso manual, ni la primera vez ni al actualizar (§2.3). Si alguna vez necesitas correrlas a mano (por ejemplo para revisar el log de una migración puntual):

```bash
docker compose -p edecan -f infra/docker/compose.selfhost.yml run --rm migrate
```

**¿Servidor con dominio propio, no solo `localhost`?** Edita además `PUBLIC_BASE_URL`/`WEB_BASE_URL`/`NEXT_PUBLIC_API_URL` en `.env` con tu dominio real antes de levantar el stack (`NEXT_PUBLIC_API_URL` se hornea en el build del frontend — si lo cambias después, reconstruye con `up -d --build` de nuevo).

Modo sin preguntas (para automatizar, o si ya sabes qué quieres): `scripts/instalar-selfhost.sh --no-interactive [--local-aws|--sin-local-aws]`. Ver `--help` para el resto de flags, o §2.1bis para `--force`/`--dry-run`.

### 2.1bis Reanudable, `--force` y `--dry-run`

- **Reanudable de verdad**: si el script se interrumpe (Ctrl+C, un error real — falta de espacio en disco, `docker compose up` que falla por un `Dockerfile` roto, etc.), el error impreso dice exactamente en qué paso se detuvo, muestra las últimas líneas de `.edecan-install.log` (el registro completo de la corrida, en la raíz del repo) y te dice que vuelvas a correr el mismo comando. Los pasos ya completados (marcados en `.edecan-install-state`) se saltan solos — nunca vuelve a preguntarte por LocalStack ni regenera un secreto que ya quedó listo.
- **`--force`**: ignora `.edecan-install-state` y vuelve a correr todos los pasos desde cero (útil si sospechas que el estado quedó inconsistente, o simplemente quieres ver el resumen completo de nuevo). No debilita ninguna protección: la garantía de "nunca pisa un `.env`/secreto existente" vive en cada paso del script, no en el archivo de estado — con o sin `--force`, un `JWT_SECRET`/`LOCAL_MASTER_KEY` que ya tengas un valor propio nunca se regenera.
- **`--dry-run`**: recorre **todos** los pasos e imprime exactamente qué haría — incluido el comando final `docker compose ... up -d --build` — sin escribir `.env`, sin tocar `.edecan-install-state`/`.edecan-install.log`, y sin ejecutar ningún comando con efecto real. Útil para auditar qué va a hacer el script antes de correrlo de verdad en un servidor real, o para probarlo en CI. Combínalo con `--no-interactive` para una vista previa 100% no interactiva.
- Ni `.edecan-install-state` ni `.edecan-install.log` contienen secretos — el primero solo guarda nombres de pasos (`env`, `secretos`, `urls`, `local_aws`), el segundo es la transcripción de lo que ya viste en pantalla (los valores de `JWT_SECRET`/`LOCAL_MASTER_KEY` en sí nunca se imprimen, ver punto 3 de la lista de arriba). Puedes borrar cualquiera de los dos en cualquier momento — el próximo `scripts/instalar-selfhost.sh` los vuelve a crear.

**Troubleshooting** — qué hacer ante cada mensaje:

| Mensaje | Qué significa | Qué hacer |
|---|---|---|
| `[error] Falló en: <paso>.` + "Cómo reanudar" | Un paso falló de verdad (no una validación ya manejada aparte, esas dan su propio mensaje específico sin este bloque). | Lee las últimas líneas del log que el propio mensaje te muestra, arregla lo que indiquen, y vuelve a correr **el mismo comando** — el script retoma justo ahí. |
| `docker está instalado pero el daemon no responde` | `docker info` falló — Docker Desktop no está corriendo, o el servicio `docker` está detenido en Linux. | macOS: abre Docker Desktop y espera a que el ícono de la ballena deje de animarse. Linux: `sudo systemctl start docker` (y `sudo usermod -aG docker "$USER"` si necesitas evitar `sudo` cada vez). |
| `No se encontró 'docker' en el PATH` / `no el plugin 'docker compose'` | Falta Docker o el plugin compose v2. | El mensaje ya trae el enlace/comando exacto para tu sistema operativo (Docker Desktop, Homebrew, `apt`/`dnf`/`pacman`). |
| `Windows nativo ... no está soportado` | Estás en Git Bash/MSYS2/Cygwin, no en WSL2. | Sigue las dos rutas que imprime el propio mensaje (instalar WSL2 con `wsl --install`, o Docker Desktop + WSL2) — no hay forma de correr esto en Windows nativo. |
| `Poco espacio libre en disco` | Aviso, no bloqueante — construir las imágenes puede necesitar varios GB. | Libera espacio si puedes, o ignóralo bajo tu propio riesgo (el script sigue). |
| `No se pudo generar JWT_SECRET/LOCAL_MASTER_KEY automáticamente` | No hay `openssl` ni `python3` ni `docker` disponibles para generarlo por ti. | Genera el valor a mano con el comando exacto que imprime el propio mensaje y pégalo en `.env`. |

### 2.2 S3 y SQS sin cuenta AWS — el profile `local-aws`

`compose.selfhost.yml` no incluye LocalStack por defecto porque no toda instalación self-host lo necesita (si vas a usar tu propia cuenta AWS real para archivos/colas, no tiene sentido levantar además un emulador). Sin **alguna** de las dos rutas de abajo, la subida de archivos (`/v1/files`) y los jobs asíncronos del worker (recordatorios, consolidación de memoria, `ingest_file`...) fallan:

- **Ruta A — tu propia cuenta AWS real.** Recomendada si vas a dejar esto corriendo de forma permanente en internet. Crea a mano un bucket S3 y dos colas SQS (`edecan-jobs`, `edecan-jobs-dlq`), pon tus credenciales IAM reales en `.env` (`AWS_ENDPOINT_URL` vacío, `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`S3_BUCKET`/`SQS_QUEUE_URL`/`AWS_REGION` reales).
- **Ruta B — LocalStack local, cero cuenta AWS.** El instalador te pregunta esto directamente (o pásale `--local-aws` para no responder nada); con el profile activo, `docker compose --profile local-aws -f infra/docker/compose.selfhost.yml up -d --build` levanta TODO en un solo comando: `postgres`, `redis`, `api`, `worker`, `web` + `localstack` + un contenedor `init-aws` que crea el bucket y las colas una sola vez dentro de LocalStack (mismo patrón que `docker-compose.yml` de dev, `ARCHITECTURE.md` §8 — S3/SQS quedan emulados, nunca tocan AWS real).

El instalador reescribe `.env` automáticamente según la ruta que elijas — nunca tienes que editar `AWS_*` a mano si eliges la ruta B.

### 2.3 Actualizar y respaldar

**Actualizar** a una versión nueva:

```bash
git pull
docker compose -p edecan -f infra/docker/compose.selfhost.yml pull          # trae versiones nuevas de postgres/redis
docker compose -p edecan -f infra/docker/compose.selfhost.yml up -d --build # reconstruye api/worker/web desde tu código actualizado
```

Si la actualización incluye una migración nueva, el servicio `migrate` la aplica solo la próxima vez que `api`/`worker` arranquen (mismo mecanismo de §2.1) — el `--build` de arriba reconstruye también la imagen `migrate`, así que Compose la vuelve a correr antes de recrear `api`/`worker`. No hace falta ningún paso manual; si quieres confirmarlo o forzarlo tú mismo, corre `docker compose -p edecan -f infra/docker/compose.selfhost.yml run --rm migrate` (§2.1).

**Backups**: no hay ninguno automático por defecto — el volumen `edecan_postgres_data` (nombrado así gracias al `-p edecan` que ya usa el instalador) sobrevive a reinicios de contenedor, pero no protege contra borrado accidental ni pérdida del disco. Configura un `pg_dump` programado (cron) desde el primer día si vas a operar con datos reales:

```bash
docker compose -p edecan -f infra/docker/compose.selfhost.yml exec -T postgres \
  pg_dump -U edecan -d edecan -Fc > "backup-$(date +%Y%m%d).dump"
```

Restaurar (y qué hacer con el rol `app_user`, verificación post-restore, etc.): [`runbooks/restore-rds.md`](./runbooks/restore-rds.md) sección B.

### 2.4 ¿Vas a modificar código? — Modo desarrollador

Si en vez de solo *usar* Edecán vas a *modificar* código, usa este modo en vez del instalador: las apps corren directo en tu máquina (con `--reload`), no dentro de contenedores.

```bash
git clone <url-de-tu-fork-o-del-repo> edecan
cd edecan
cp .env.example .env
# Edita .env: como mínimo JWT_SECRET/LOCAL_MASTER_KEY propios (no dejes los
# placeholders TU_X_AQUI en un .env que vayas a usar de verdad, aunque sea
# local). ANTHROPIC_API_KEY/OPENAI_COMPAT_* acá SOLO alimentan los jobs de
# sistema de `apps/worker` (sin tenant) — NO el chat: para eso, después de
# levantar `make web`, entra a Configuración y conecta tu propio LLM
# (`PUT /v1/credentials/llm`) igual que cualquier otro tenant (ver
# credenciales.md).

make deps         # postgres+pgvector, redis, LocalStack (S3/SQS) — ARCHITECTURE.md §8
make db-migrate
make api          # FastAPI :8000 (terminal aparte)
make worker       # worker (terminal aparte)
make web          # Next.js :3000 (terminal aparte)
```

`make deps` levanta `docker-compose.yml` de la raíz — **no** `compose.selfhost.yml` (ese es para producción ligera; este es solo dependencias). Ya incluye LocalStack con el bucket/colas creados automáticamente, sin preguntar nada — pensado para desarrollo activo, no para dejar corriendo 24/7. Apágalas con `make down`. Comandos de calidad: `make test` (pytest offline, sin red real) y `make lint` (ruff).

## 3. Tu propia AWS con Terraform

Para quien necesita la topología de producción completa (VPC de 3 AZ, ECS Fargate, RDS PostgreSQL Multi-AZ, ElastiCache, CloudFront+WAF, EventBridge Scheduler...) en su propia cuenta de AWS, o contrató el servicio cotizado "Tu propia nube" (`PLAN.md`). Es más pesado y más caro que los caminos 1 y 2 — solo vale la pena si necesitas de verdad lo que ofrece (alta disponibilidad, escala, un equipo operándolo).

```bash
export AWS_PROFILE=tu-perfil   # o AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY/AWS_SESSION_TOKEN
scripts/desplegar-mi-aws.sh --entorno=dev
```

Verifica Terraform/AWS CLI/tus credenciales, te muestra qué cuenta AWS detectó (para que confirmes que es la correcta), corre `terraform init` + `terraform plan` y se **detiene** mostrando el plan — nunca aplica nada por defecto. Aplicar sigue siendo tu decisión manual (`--aplicar-de-verdad` te lo ofrece con una segunda confirmación escrita a mano, y Terraform todavía te pide su propio `yes`).

El detalle completo — backend remoto de state, primer apply, construir/publicar imágenes a ECR, rellenar secretos reales en Secrets Manager, dominio propio y HTTPS, destruir, y la tabla de costos aproximados mensuales — vive en [`../infra/README.md`](../infra/README.md) y [`../infra/terraform/README.md`](../infra/terraform/README.md); este documento no lo repite.

## 4. Trae tus propias credenciales

Edecán no incluye ninguna credencial de terceros. Dos categorías, no las confundas:

- **`JWT_SECRET` / `LOCAL_MASTER_KEY`** — secretos de PLATAFORMA (firman las sesiones y cifran el `TokenVault`), viven **solo** en `.env`. `scripts/instalar-selfhost.sh` los genera por ti (§2.1); en modo desarrollador los pones tú a mano.
- **LLM, voz (Deepgram/ElevenLabs), conectores (Google/Microsoft/Meta/X/YouTube/Slack), mensajería (Telegram/Discord)** — credenciales POR TENANT. Se conectan desde la pantalla **Configuración** (`/app/configuracion`) con el flujo de pegar-y-validar — nunca editando `.env` a mano (ver [`credenciales.md`](./credenciales.md), [`conectores.md`](./conectores.md)). A diferencia de versiones anteriores de este documento: poner `ANTHROPIC_API_KEY`/`DEEPGRAM_API_KEY`/`ELEVENLABS_API_KEY` en tu `.env` YA NO alcanza — incluso en self-host de un solo tenant, tienes que conectar tu LLM en **Configuración** antes de poder chatear (`POST /v1/conversations/.../messages` corta con `400` si no lo hiciste; la voz web cae a un stub silencioso, no a esas variables). Ver [`credenciales.md`](./credenciales.md) "Orden de resolución".
- Puedes activar `premium/` (telefonía Twilio) instalando el paquete `edecan_premium` por separado, pero las credenciales de Twilio **nunca** van en variables de entorno: cada tenant las conecta desde el panel y quedan cifradas en el `TokenVault`. El núcleo funciona completo y de forma autónoma sin ese paquete.

No hay ninguna credencial compartida de la plataforma oculta en ningún punto del flujo: lo que no configures explícitamente queda deshabilitado o cae a un stub offline, nunca a una llave que no sea tuya.

## 5. Siguientes pasos

- [`primeros-pasos.md`](./primeros-pasos.md) — el wizard de bienvenida y la pantalla de Configuración, paso a paso.
- [`configuracion.md`](./configuracion.md) — referencia completa de cada variable de entorno.
- [`credenciales.md`](./credenciales.md) / [`proveedores-llm.md`](./proveedores-llm.md) — bring-your-own de LLM y voz en detalle.
- [`conectores.md`](./conectores.md) — cómo registrar cada app OAuth.
- [`personalizacion-nivel-dios.md`](./personalizacion-nivel-dios.md) — cómo configurar a fondo tu asistente.
- [`runbooks/`](./runbooks/) — qué hacer ante incidentes operativos (fuga entre tenants, rotación de claves, restaurar la base de datos, cola atascada).
