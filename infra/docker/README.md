# Self-hosting con Docker

Esta carpeta contiene imágenes multi-stage para la API, el worker, las
migraciones y la aplicación web, además de un Compose de referencia que
levanta el núcleo OSS completo con PostgreSQL, Redis y, opcionalmente,
LocalStack para S3/SQS.

## Inicio rápido

Desde la raíz del repositorio:

```bash
cp .env.example .env
```

Genera valores únicos para `JWT_SECRET` y `LOCAL_MASTER_KEY` siguiendo las
instrucciones de `.env.example`. Para que los contenedores se comuniquen,
cambia también estas variables:

```dotenv
DATABASE_URL=postgresql+asyncpg://edecan:edecan@postgres:5432/edecan
REDIS_URL=redis://redis:6379/0
```

En una instalación expuesta a internet, define además `POSTGRES_PASSWORD` y
usa el mismo valor dentro de `DATABASE_URL`; el valor por defecto `edecan` es
solo para evaluación local.

Valida la configuración antes de construir:

```bash
docker compose -p edecan -f infra/docker/compose.selfhost.yml config
```

### Opción A: AWS real

Crea un bucket S3 y las colas `edecan-jobs` y `edecan-jobs-dlq`, configura en
`.env` credenciales IAM de mínimo privilegio, `S3_BUCKET`, `SQS_QUEUE_URL`,
`AWS_REGION` y deja `AWS_ENDPOINT_URL` vacío. Luego:

```bash
docker compose -p edecan -f infra/docker/compose.selfhost.yml up -d --build
```

### Opción B: infraestructura AWS local

Para una evaluación completamente local, usa estos valores en `.env`:

```dotenv
AWS_ENDPOINT_URL=http://localstack:4566
SQS_QUEUE_URL=http://localstack:4566/000000000000/edecan-jobs
AWS_ACCESS_KEY_ID=test
AWS_SECRET_ACCESS_KEY=test
S3_BUCKET=edecan-files
AWS_REGION=us-east-1
```

Y activa el perfil `local-aws`:

```bash
docker compose -p edecan --profile local-aws -f infra/docker/compose.selfhost.yml up -d --build
```

El servicio efímero `migrate` ejecuta Alembic una sola vez. La API y el worker
solo arrancan después de que PostgreSQL esté sano y la migración haya
terminado correctamente.

## Operación segura

- Los puertos web, API y LocalStack escuchan en `127.0.0.1` por defecto.
  Publícalos mediante un reverse proxy con TLS; cambia `WEB_BIND_ADDRESS` o
  `API_BIND_ADDRESS` únicamente si entiendes el alcance de red.
- Mantén `.env` fuera de Git, rota claves comprometidas y usa credenciales IAM
  con acceso exclusivo al bucket y las colas de Edecán.
- Automatiza copias de seguridad de `postgres_data`. Redis usa AOF y conserva
  su estado en `redis_data`, pero no sustituye a PostgreSQL como fuente de
  verdad.
- Revisa logs y estado con `docker compose -p edecan -f
  infra/docker/compose.selfhost.yml ps` y `... logs -f`.
- `docker compose ... down` conserva datos. No añadas `-v` salvo que quieras
  eliminar de forma irreversible los volúmenes de PostgreSQL, Redis y
  LocalStack.

## Builds individuales

Las imágenes Python necesitan la raíz del monorepo como contexto porque uv
resuelve dependencias locales de `packages/*`:

```bash
docker build -f infra/docker/Dockerfile.api -t edecan-api:dev .
docker build -f infra/docker/Dockerfile.worker -t edecan-worker:dev .
docker build -f infra/docker/Dockerfile.migrate -t edecan-migrate:dev .
```

La web es un proyecto Node independiente:

```bash
docker build -f infra/docker/Dockerfile.web -t edecan-web:dev apps/web
```

Todas las imágenes de ejecución usan usuarios no-root. Los Dockerfiles copian
solo los archivos de aplicación necesarios, por lo que `.env` y otros secretos
locales no terminan dentro de sus capas.
