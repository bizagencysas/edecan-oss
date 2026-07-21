# apps/local — `edecan_local`

Runtime local que reúne API, worker, PostgreSQL embebido y almacenamiento de
archivos en un solo proceso. Por defecto escucha en `127.0.0.1`; la app nativa
activa acceso LAN para el móvil. Persiste bajo `~/.edecan/data` y se apaga
limpiamente con `SIGTERM`/`SIGINT`.

## Arranque desde un clon limpio

Desde la raíz del repositorio:

```bash
uv sync --all-packages --frozen
uv run --all-packages edecan --no-web
```

No hace falta instalar PostgreSQL ni conocer extras internos en las
plataformas donde `pgserver==0.1.4` publica wheel: macOS x64/arm64, Linux x64
y Windows x64. Cuando aparezca `EDECAN_LOCAL_READY port=8765`, la API está
disponible en `http://127.0.0.1:8765`; `Ctrl+C` detiene también la base
embebida.

En Linux ARM64 y Windows ARM64 el workspace se instala normalmente, pero no
hay wheel de `pgserver` para provisionar Postgres embebido. En esas
arquitecturas configura una base existente antes de arrancar:

```bash
export EDECAN_DATABASE_URL='postgresql+asyncpg://usuario:clave@host:5432/edecan'
uv run --all-packages edecan --no-web
```

`EDECAN_DATABASE_URL` también sirve en cualquier plataforma para optar por un
PostgreSQL propio; el runner no intentará importar ni administrar `pgserver`.

La aplicación Tauri usa este mismo entry point. Su quick start completo está
en [`../desktop/README.md`](../desktop/README.md).

## Opciones

- `--port`: puerto de API, `8765` por defecto.
- `--data-dir`: directorio persistente, `~/.edecan/data` por defecto.
- `--no-web`: no intenta servir un export estático de `apps/web`.
- `--mobile-access`: permite la conexión móvil desde la red local; la app de
  escritorio lo activa automáticamente.
- `EDECAN_WEB_DIR`: directorio de un export estático que se sirve en `/`.
