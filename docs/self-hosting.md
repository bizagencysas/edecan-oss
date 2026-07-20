# Self-hosting

Edecan puede ejecutarse como aplicación local o como servidor que tú operas.
Este documento cubre dos rutas públicas y verificables:

1. **Modo desarrollador:** las dependencias corren en Docker y las apps en tu
   máquina con recarga rápida.
2. **Stack containerizado:** API, worker, web, Postgres y Redis corren con el
   Compose de `infra/docker/compose.selfhost.yml`.

El proyecto está en fase pre-1.0. El Compose es una referencia de self-host,
no una plataforma administrada: tú eres responsable de TLS, firewall,
backups, actualizaciones, disponibilidad y costos de terceros.

## Requisitos

- Docker y Docker Compose v2 (`docker compose`).
- Para desarrollo: Python 3.12, [`uv`](https://docs.astral.sh/uv/) y Node 22.
- Al menos un proveedor LLM conectado desde la UI para usar el chat.

En Windows usa Docker Desktop con WSL2 y clona el repositorio dentro de la
distribución Linux. Los scripts Bash no soportan Git Bash/MSYS2/Cygwin.

## 1. Modo desarrollador

```bash
git clone https://github.com/isaccmanuel/edecan.git
cd edecan
cp .env.example .env
```

Genera `JWT_SECRET` y `LOCAL_MASTER_KEY` con los comandos documentados en
`.env.example`; no reutilices los placeholders públicos. Luego:

```bash
uv sync --all-packages --frozen
make deps
make db-migrate
```

Inicia los procesos en terminales separadas:

```bash
make api
make worker
make web
```

Abre `http://localhost:3000`. El `docker-compose.yml` raíz levanta solo
Postgres/pgvector, Redis y LocalStack para desarrollo; las apps permanecen en
tu máquina. Usa `make down` al terminar.

## 2. Stack containerizado

### Instalador guiado

```bash
scripts/instalar-selfhost.sh
```

El instalador:

- valida Docker, Compose, plataforma y espacio disponible;
- crea `.env` desde `.env.example` sin sobrescribir uno existente;
- genera los dos secretos obligatorios si siguen en su placeholder;
- cambia las URLs de Postgres/Redis a los nombres internos del Compose;
- permite elegir LocalStack para S3/SQS sin una cuenta AWS;
- muestra el comando y pide confirmación antes de levantar contenedores.

Es reanudable mediante `.edecan-install-state` y registra diagnóstico en
`.edecan-install.log` sin imprimir secretos. Opciones útiles:

```bash
scripts/instalar-selfhost.sh --dry-run --no-interactive
scripts/instalar-selfhost.sh --no-interactive --local-aws
scripts/instalar-selfhost.sh --force
```

Revisa `scripts/instalar-selfhost.sh --help` antes de automatizarlo.

### Ejecución manual

```bash
cp .env.example .env
```

En `.env`, genera secretos y cambia:

```dotenv
DATABASE_URL=postgresql+asyncpg://edecan:edecan@postgres:5432/edecan
REDIS_URL=redis://redis:6379/0
```

Para usar LocalStack dentro de Docker:

```dotenv
AWS_ENDPOINT_URL=http://localstack:4566
SQS_QUEUE_URL=http://localstack:4566/000000000000/edecan-jobs
AWS_ACCESS_KEY_ID=test
AWS_SECRET_ACCESS_KEY=test
```

Después levanta el stack:

```bash
docker compose -p edecan --profile local-aws \
  -f infra/docker/compose.selfhost.yml up -d --build
```

Sin `--profile local-aws`, configura tu propio bucket S3 y colas SQS con
credenciales IAM de mínimo privilegio. LocalStack es apropiado para desarrollo
y evaluaciones locales; no es un sustituto automático de una arquitectura de
producción.

El servicio `migrate` ejecuta `alembic upgrade head` y debe completar antes de
que API y worker arranquen. Consulta el estado con:

```bash
docker compose -p edecan -f infra/docker/compose.selfhost.yml ps
docker compose -p edecan -f infra/docker/compose.selfhost.yml logs migrate api worker
```

Para validar las imágenes en un entorno desechable antes de operar tu propia
instancia:

```bash
make selfhost-smoke
```

El smoke construye las cuatro imágenes, migra una base vacía, espera la
readiness de API y web, comprueba la CSP, importa el worker y confirma usuarios
no-root. Usa nombres, red y volúmenes aislados por proceso y los elimina al
terminar; no reutiliza los datos de una instalación existente.

## Dominios y TLS

Antes de exponer la instancia a internet:

- coloca un reverse proxy con TLS delante de los puertos 3000/8000;
- no publiques Postgres, Redis ni LocalStack;
- configura `PUBLIC_BASE_URL`, `WEB_BASE_URL` y `NEXT_PUBLIC_API_URL` con los
  dominios HTTPS reales;
- restringe CORS, grupos de red y acceso administrativo;
- usa secretos únicos y un gestor de secretos apropiado;
- reconstruye `web` si cambia `NEXT_PUBLIC_API_URL`, porque se incorpora al
  bundle durante el build.

El Compose no automatiza certificados ni DNS para evitar asumir un proveedor
o una topología que no controlamos.

## Actualizar

Haz backup primero y revisa las notas de versión:

```bash
git pull --ff-only
docker compose -p edecan -f infra/docker/compose.selfhost.yml pull
docker compose -p edecan -f infra/docker/compose.selfhost.yml up -d --build
```

Confirma que `migrate` terminó con exit 0 y prueba `/readyz` antes de devolver
tráfico. No actualices una instancia crítica directamente desde una rama sin
tag ni sin un plan de rollback.

## Backup y restauración

Los volúmenes sobreviven a reinicios, pero no son backups. Para PostgreSQL:

```bash
docker compose -p edecan -f infra/docker/compose.selfhost.yml exec -T postgres \
  pg_dump -U edecan -d edecan -Fc > "backup-$(date +%Y%m%d).dump"
```

Guarda backups cifrados fuera del mismo host, prueba restauraciones y conserva
también la clave que protege TokenVault; perder esa clave puede volver
irrecuperables las credenciales cifradas. Consulta
[`runbooks/restore-rds.md`](./runbooks/restore-rds.md) y
[`runbooks/rotacion-claves.md`](./runbooks/rotacion-claves.md).

## Credenciales

`JWT_SECRET` y `LOCAL_MASTER_KEY` son secretos del operador y viven solo en
`.env` o en tu gestor de secretos. Las credenciales de LLM, voz e integraciones
se conectan por tenant desde Configuración y se cifran en TokenVault. No las
incluyas en imágenes, archivos Compose, logs ni repositorios.

Más información:

- [`configuracion.md`](./configuracion.md)
- [`credenciales.md`](./credenciales.md)
- [`proveedores-llm.md`](./proveedores-llm.md)
- [`seguridad-modelo-amenazas.md`](./seguridad-modelo-amenazas.md)
- [`../SECURITY.md`](../SECURITY.md)
