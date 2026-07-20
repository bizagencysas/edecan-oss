# Backend local (`edecan_local`) — cómo corre por dentro

El backend que hoy está pensado para Docker Compose/ECS (Postgres+Redis+SQS+S3 separados, ver `ARCHITECTURE.md` §7/§8) se **empaqueta y corre entero en la máquina del cliente** cuando se usa la app de escritorio. `apps/local/edecan_local` es ese runner: un solo proceso Python que levanta todo lo que en dev/prod son servicios separados.

Este documento cubre la arquitectura interna. Para instalar/compilar la app de escritorio en sí (Tauri, empaquetado, dónde viven tus datos vistos desde la app, troubleshooting del splash) ver [`desktop.md`](./desktop.md) — ese documento es el que consume `apps/desktop` (fase v3), que lanza `edecan_local` como *sidecar* exactamente con el contrato descrito acá.

## 1. Arquitectura en un diagrama

```
                apps/desktop (Tauri, Rust)
                   lanza como sidecar:
            edecan-local --port P --data-dir D
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│ edecan_local.runtime.run():                            │
│  1. data_dir (0700) + señales SIGTERM/SIGINT           │
│  2. edecan_local.pg.ensure_postgres()                  │
│  3. secretos locales (JWT_SECRET / LOCAL_MASTER_KEY)   │
│  4. entorno fijado ANTES de importar edecan_api        │
│  5. edecan_local.migrate.run_migrations()              │
│  6. tres tareas asyncio concurrentes:                  │
│                                                        │
│ ┌──────────────────────┐  ┌──────────────────────────┐ │
│ │ uvicorn              │  │ uvicorn                  │ │
│ │ edecan_api.main:app  │  │ edecan_local.objectstore │ │
│ │ 127.0.0.1:P          │  │ 127.0.0.1:P+2 (mini S3)  │ │
│ │ (+ apps/web estático │  │ filesystem:              │ │
│ │  en "/" si aplica)   │  │  data_dir/objects/       │ │
│ └──────────────────────┘  └──────────────────────────┘ │
│                                                        │
│ ┌───────────────────────────────────────────────────┐  │
│ │ edecan_local.worker_loop                          │  │
│ │ (misma tarea asyncio, sin proceso de cola aparte) │  │
│ │ - SELECT jobs FOR UPDATE SKIP LOCKED, cada 2s     │  │
│ │ - scheduler local cada 30s: send_reminder_scan,   │  │
│ │   automation_scan                                 │  │
│ └───────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────┘
              │                             │
              ▼                             ▼
   fakeredis (en memoria,         pgserver: Postgres 16
    REDIS_URL=memory://)           + pgvector embebido
                                      data_dir/pg/
```

Todo bindea **solo en `127.0.0.1`** — nunca `0.0.0.0` (`ARCHITECTURE.md` §12.f): esto no es una plataforma multi-tenant expuesta, es un proceso local de un solo usuario en su propia máquina (ver §6 más abajo).

## 2. Por qué cada pieza se resolvió así

`ARCHITECTURE.md` §7/§8 describe el stack de referencia (RDS/ElastiCache/SQS/S3 en prod; Postgres+Redis+LocalStack en dev vía `docker-compose.yml`). Ninguna de esas piezas tiene sentido pedirle a un cliente que instale Docker/una cuenta AWS solo para abrir una app de escritorio — cada una se resolvió por separado:

| Pieza de referencia | Resuelta acá con | Por qué |
|---|---|---|
| PostgreSQL (RDS/`postgres` en compose) | `pgserver` (dependencia directa donde existe wheel) | Trae binarios reales de Postgres 16 + pgvector en macOS x64/arm64, Linux x64 y Windows x64. `edecan_local.pg.ensure_postgres` lo arranca en `data_dir/pg`; Linux/Windows ARM64 deben definir `EDECAN_DATABASE_URL`. |
| Redis (ElastiCache/`redis` en compose) | `fakeredis` (`REDIS_URL=memory://`) | Solo se usa para rate-limit, códigos de emparejamiento y confirmaciones pendientes (`ARCHITECTURE.md` §10.12) — todo de corta vida, de UN SOLO proceso. Levantar un Redis real para eso en la laptop de alguien es puro overhead. `edecan_api.deps` (fase v3) interpreta el esquema `memory://` — este WP solo lo fija como env var. |
| SQS + DLQ (`edecan-jobs`) | Tabla `jobs` de Postgres como cola (`QUEUE_PROVIDER=db`) | Evita depender de LocalStack/SQS real en la máquina del cliente. `edecan_core.queue.enqueue` gana una rama `INSERT INTO jobs` (vía `asyncpg`, conexión efímera) cuando `QUEUE_PROVIDER="db"` — el comportamiento SQS de siempre queda intacto para dev/prod (`QUEUE_PROVIDER` default `"sqs"`). `edecan_local.worker_loop` consume esa misma tabla con `SELECT ... FOR UPDATE SKIP LOCKED`, en vez de que un `edecan_worker.main` aparte haga long-polling a SQS. |
| S3 (`edecan-files` / LocalStack en dev) | `edecan_local.objectstore`, un mini servidor S3-compatible sobre filesystem | Ningún call site de `aioboto3` en el repo llama `create_bucket` — todos van directo a `put_object`/`get_object` (ver su docstring) con `Body=bytes`. Reimplementar SOLO ese subconjunto (PUT/GET/HEAD/DELETE de objeto, `ListObjectsV2` mínimo) alcanza para que `aiobotocore` hable con un `AWS_ENDPOINT_URL` local sin tocar ninguno de esos call sites. Firmas AWS (`Authorization`/`X-Amz-*`) se ignoran por completo — solo escucha loopback. |
| EventBridge Scheduler | Scheduler local dentro de `worker_loop.run_forever` | Mismo rol que `edecan_worker.scheduler` en dev: cada 30s encola `send_reminder_scan` y `automation_scan` (`tenant_id=None`, jobs de sistema). |
| Worker aparte (`edecan_worker.main`, proceso propio) | `edecan_local.worker_loop`, una tarea `asyncio` más del mismo proceso | Un usuario de escritorio no va a correr un segundo proceso de worker a mano — despachar a `edecan_worker.handlers.HANDLERS` (el registro REAL, sin duplicar handlers) desde una tarea concurrente alcanza y sobra para un solo usuario. |

## 3. Dónde viven los datos

`DATA_DIR` (default `~/.edecan/data`, override `--data-dir`; la app de escritorio SIEMPRE pasa uno explícito — ver [`desktop.md`](./desktop.md) §5):

```
<data_dir>/
├── pg/              # cluster de Postgres embebido (pgserver)
├── objects/
│   └── edecan-files/    # el único bucket que usa hoy el repo (S3_BUCKET)
│       └── tenants/<tenant_id>/files/<file_id>/<filename>
└── secrets.json     # JWT_SECRET + LOCAL_MASTER_KEY (permisos 0600) — ver §4
```

Permisos `0700` en `data_dir` completo — nadie más que el usuario dueño del proceso puede siquiera listar lo que hay adentro.

## 4. Secretos locales: `JWT_SECRET` / `LOCAL_MASTER_KEY`

Punto que **no** está en la lista de env vars pinned en `ARCHITECTURE.md` §12.f/§12.g (esa sección asume una plataforma hospedada con un operador humano que ya puso un valor real en su `.env`, ver [`self-hosting.md`](./self-hosting.md) §2) pero que este runner SÍ tiene que resolver: la app de escritorio no tiene ningún operador que edite un `.env` a mano.

Dejar el placeholder público de `edecan_api.config` (`"TU_LOCAL_MASTER_KEY_FERNET_AQUI"`) no es solo "inseguro" — directamente **rompe** cualquier ruta que use el `TokenVault` (`GET/PUT /v1/credentials`, `GET /v1/setup/status`, conectores...): `LocalKeyProvider.__init__` (`edecan_db.vault`) construye un `cryptography.fernet.Fernet(LOCAL_MASTER_KEY)` de forma *eager* en cada request, y ese placeholder no decodifica a 32 bytes válidos — cada una de esas rutas devolvería `500` siempre.

Por eso `edecan_local.runtime._ensure_local_secrets`:

- La primera vez que corre sobre un `data_dir` nuevo, genera `JWT_SECRET` (`secrets.token_urlsafe(32)`) y `LOCAL_MASTER_KEY` (`Fernet.generate_key()`) reales, y los persiste en `data_dir/secrets.json` (permisos `0600`).
- En arranques siguientes, los relee de ahí — **nunca** genera uno nuevo si ya hay uno válido: perder `LOCAL_MASTER_KEY` entre reinicios dejaría ilegibles para siempre las credenciales que el tenant ya guardó en el vault (cifrado envolvente, `ARCHITECTURE.md` §10.4), y perder `JWT_SECRET` cerraría la sesión de todo el mundo en cada arranque.
- Si `JWT_SECRET`/`LOCAL_MASTER_KEY` YA vienen fijados en el entorno del proceso (usuario avanzado, o una versión futura de la app Tauri que los traiga generados de otra forma), se respetan tal cual (`os.environ.setdefault`, nunca los pisa).

## 5. Cómo correrlo en desarrollo

```bash
# Desde la raíz del repo — arranca todo (Postgres embebido incluido) en :8765
uv run --all-packages python -m edecan_local

# Con la web de apps/web corriendo aparte (npm run dev en :3000), en vez de
# la exportación estática que empaqueta apps/desktop:
uv run --all-packages python -m edecan_local --no-web

# Puerto y carpeta de datos propios (útil para correr dos instancias a la vez):
uv run --all-packages python -m edecan_local --port 9001 --data-dir /tmp/edecan-dev-data
```

Al quedar sano (migraciones aplicadas, `GET /healthz` respondiendo de verdad — este proceso hace su propio poll antes de avisar, nunca asume) imprime en stdout la línea exacta:

```
EDECAN_LOCAL_READY port=8765
```

`apps/desktop/src-tauri/src/backend.rs` (fase v3) es quien lee esa línea para saber cuándo dejar de mostrar el splash y abrir la ventana principal — el formato (`"EDECAN_LOCAL_READY port="` + dígitos) es un contrato pinned, no cambiarlo sin coordinar con ese paquete. `Ctrl+C` (`SIGINT`) o `SIGTERM` apagan todo de forma ordenada: los dos servidores uvicorn, el worker (termina su ciclo en curso), y por último el Postgres embebido.

## 6. Modo avanzado: traer tu propio Postgres

Si ya tenés un Postgres corriendo (el tuyo propio, uno remoto, uno en Docker que preferís administrar vos) y no querés el embebido, definí `EDECAN_DATABASE_URL` antes de arrancar:

```bash
EDECAN_DATABASE_URL="postgresql+asyncpg://usuario:pass@localhost:5432/edecan" \
  uv run --all-packages python -m edecan_local
```

`edecan_local.pg.ensure_postgres` detecta esa variable y usa esa URL tal cual, sin tocar `pgserver` para nada (ni siquiera lo importa) — este proceso tampoco se hace cargo de apagarlo al salir, porque no es dueño de ese Postgres.

Este modo es obligatorio en Linux ARM64 y Windows ARM64: `pgserver==0.1.4`
no publica wheel para esas arquitecturas. El marker de dependencia permite
instalar el workspace y usar el runtime con una base administrada, sin fingir
que existe un Postgres embebido donde el proveedor no distribuye binarios.

## 7. Qué NO es este runner

- **No es multi-tenant expuesto a la red.** Aunque el esquema de datos por debajo sigue siendo el mismo multi-tenant con Row-Level Security de siempre (`ARCHITECTURE.md` §2) — en la práctica, cada instalación de la app de escritorio es un tenant único, en la máquina de un único usuario, bindeado solo a loopback. No hay ningún escenario soportado de "varios usuarios remotos pegándole a este mismo proceso".
- **El object store no habla el protocolo S3 completo.** Solo el subconjunto que el propio repo usa (§2, tabla) — no sirve como reemplazo genérico de LocalStack/MinIO para otra cosa.
- **No reemplaza `docker-compose.yml` para desarrollo del propio Edecán.** Seguís usando `make api`/`make worker`/`docker compose up` (`ARCHITECTURE.md` §8) si estás desarrollando el producto — `edecan_local` es el runtime del PRODUCTO EMPAQUETADO, para la máquina del cliente final.

## 8. Apagado grácil del sidecar en macOS/Linux (resuelto 2026-07-08)

`apps/desktop/src-tauri/src/backend.rs::kill_backend` mandaba `SIGKILL` (vía `CommandChild::kill()`) al PID del sidecar en **todas** las plataformas, sin darle a `edecan_local.runtime.run()` (§5) ninguna chance de correr su `finally` (que apaga `pgserver` limpio). Windows ya tenía una red de seguridad explícita (`taskkill /F /T`, tree-kill por PID) precisamente porque un kill duro al proceso de Python no se lleva con él al proceso hijo real de Postgres que lanza `pgserver` — pero macOS/Linux no tenían ningún equivalente, así que un `SIGKILL` directo podía dejar el Postgres embebido huérfano corriendo en segundo plano al cerrar la app.

**Fix aplicado**: en macOS/Linux, `kill_backend` ahora manda `SIGTERM` primero (vía el binario `kill`, sin agregar ninguna dependencia nueva de Cargo) y sondea hasta 3 segundos (cada 100ms, con `kill -0`) a que el proceso salga solo. `edecan_local.runtime.run()` ya maneja `SIGTERM`/`SIGINT` (§1/§5) y apaga `pgserver` en su propio `finally` — con este cambio, ese camino graceful se ejerce también cuando Tauri mata el sidecar, no solo corriendo el backend suelto desde una terminal. Si el proceso no sale dentro del margen (colgado), se escala al `SIGKILL` de siempre como red de seguridad final, para que cerrar la app nunca se quede esperando indefinidamente. Windows no cambia (sigue con `taskkill /F /T` tal cual).

**Nota de verificación**: este entorno no tiene `cargo`/`rustc` instalados (mismo límite que encontró fase v3 al construir `apps/desktop`), así que el fix se escribió y revisó cuidadosamente pero **no se pudo compilar ni probar empíricamente**. Antes de confiar en esto en producción, alguien con toolchain de Rust debería: `cargo build` en `apps/desktop/src-tauri`, lanzar `edecan-local` real desde la app, y confirmar con `ps`/`pstree` que el proceso de `pgserver` efectivamente desaparece al cerrar la app (no solo el proceso de Python) — exactamente la verificación empírica que este mismo documento ya recomendaba antes de escribir el fix.

**Actualización (fase v7, 2026-07-09) — la MITAD Python de este contrato ya tiene evidencia empírica real** (la mitad Rust de arriba en ese momento SEGUÍA sin poder verificarse, este entorno todavía no tenía `cargo`/`rustc`): se arrancó `edecan_local` de verdad (`uv run --all-packages python -m edecan_local`, Postgres embebido real vía `pgserver`), se le mandó `SIGTERM` directo al proceso (mismo patrón exacto que `send_sigterm_and_wait_for_exit` en `backend.rs`), y se confirmó con `ps`/`lsof` que en ~1s (dentro del margen de 3s que espera el lado Rust) el proceso terminó solo, `pgserver` se apagó limpio vía `pg_ctl stop` (log real: `"waiting for server to shut down.... done / server stopped"`), y no quedó ningún proceso `postgres`/`pgserver` huérfano ni puerto ocupado. Detalle completo, con logs y comandos reales, en [`cumplimiento/barrido-v7-desktop.md`](./cumplimiento/barrido-v7-desktop.md) §2 Paso 7.

**Actualización 2 (2026-07-09, mismo día) — la mitad Rust YA está verificada, este límite queda cerrado**: se instaló Rust (`cargo`/`rustc` 1.96.1, Homebrew) y se compiló/corrió `apps/desktop` de verdad (`cargo build` + `cargo run`, no solo `cargo check`). Confirmado pasando por `CommandChild`/`tauri-plugin-shell` real (no `kill -TERM` manual): la app completa, con la ruta real de `app_data_dir()` de macOS (`~/Library/Application Support/cc.edecan.desktop/data`, la que de verdad usa Tauri en producción), arrancó el sidecar, provisionó Postgres embebido, aplicó las 8 migraciones, montó los ~25 routers, y respondió `GET /healthz` → `200`. El camino de apagado documentado (cerrar ventana/Cmd+Q/tray "Salir" → `RunEvent::Exit` → `kill_backend`) se confirmó correcto leyendo el código ya compilado sin warnings. Aparecieron y se corrigieron 6 bugs reales en el camino (ninguno en `kill_backend` en sí — ese código ya estaba bien) — detalle completo en `docs/seguridad-modelo-amenazas.md`, sección "la app de escritorio Tauri nunca se había compilado". Único residual real que queda: un `kill -TERM` externo directo al proceso raíz de Tauri (simulando un `killall`/logout del sistema, no el cierre normal de la app) no dispara `RunEvent::Exit` — Tauri no instala manejadores de señales de SO por defecto — así que ese camino específico (no el normal) puede dejar Postgres en apagado no-limpio; instalar un manejador de señales de SO para el proceso Tauri en sí queda pendiente, riesgo acotado.

**Ampliación (fase v4, 2026-07-08) — Ollama embebido**: `backend.rs::build_command` ganó dos funciones nuevas (`with_ollama_env`/`resolve_ollama_sidecar`, ver §9 más abajo) para pasarle a este proceso `EDECAN_OLLAMA_BIN`/`EDECAN_OLLAMA_AUTOSTART` como env vars. Originalmente escrito sin poder compilar (mismo límite sin `cargo`/`rustc` de esta nota) — **actualizado 2026-07-09**: `cargo build`/`cargo run` reales confirman que compila sin warnings y que el camino "sin binario de Ollama empaquetado" (el default, `EDECAN_BUNDLE_OLLAMA` sin fijar — ver `docs/seguridad-modelo-amenazas.md` sobre por qué eso es lo correcto por defecto) funciona con gracia: `resolve_ollama_sidecar` devuelve `None`, `with_ollama_env` no agrega ninguna env var, y el resto de la app arranca normal. Queda pendiente (checklist sin correr todavía, no bloqueante) el camino CON Ollama bundleado de verdad:

1. `EDECAN_BUNDLE_OLLAMA=1 ./scripts/build-app.sh` en macOS, o la misma variable con `build-app.ps1` en Windows x64 — confirma que `resolve_ollama_sidecar` encuentra el binario y que `maybe_start_ollama` (Python, §9) lo arranca de verdad. El flujo Windows también empaqueta el árbol `lib/ollama` requerido por el CLI standalone; el build falla si ese runtime nativo está incompleto.
2. `ps aux | grep "ollama serve"` mientras la app está abierta — confirma el pid vivo.
3. Cerrar la app (ventana, bandeja, o Cmd+Q) y repetir el `ps` — el proceso `ollama serve` debe haber desaparecido, igual que `pgserver` en el punto 1 de esta nota.

## 9. Ollama embebido (opcional, fase v4)

Patrón de auto-provisioning adaptado de `open-jarvis/OpenJarvis` (Apache-2.0, ver `NOTICE`) — detalle de producto/UX en [`desktop.md`](./desktop.md) §10 ("Ollama embebido (opcional)"); acá va el detalle técnico de este runner.

**Env vars nuevas** (ninguna pinned en `ARCHITECTURE.md` §12.g — son propias de esta pieza opcional, mismo criterio que `EDECAN_LOCAL_DEV_CMD` en `apps/desktop/src-tauri/src/backend.rs`):

| Variable | Quién la fija | Qué hace |
|---|---|---|
| `EDECAN_OLLAMA_AUTOSTART` | El usuario (a mano hoy) o la app Tauri (UI de un clic futura) | `"true"`/`"1"`/`"yes"`/`"on"` activa el arranque automático. Sin fijar (default): `edecan_local.ollama_supervisor.maybe_start_ollama` devuelve `None` de inmediato, cero efecto — comportamiento idéntico a antes de este WP. |
| `EDECAN_OLLAMA_BIN` | `apps/desktop/src-tauri/src/backend.rs::build_command` (si empaquetó el sidecar de Ollama, ver `desktop.md` §10) | Ruta absoluta al binario. Sin fijar, `maybe_start_ollama` cae a `shutil.which("ollama")` (instalación del sistema) antes de rendirse. |

**`edecan_local.ollama_supervisor`** (`apps/local/edecan_local/ollama_supervisor.py`, con tests en `apps/local/tests/test_ollama_supervisor.py`): `maybe_start_ollama(settings) -> OllamaHandle | None`, síncrona (como `pgserver.get_server`), llamada desde `edecan_local.runtime.run()` dentro de `asyncio.to_thread(...)` justo después de que `api_settings` está disponible (después del Postgres embebido, ver el "Orden de arranque" del docstring de `runtime.py`). Cuatro caminos, todos de "mejor esfuerzo" (nunca lanzan, nunca bloquean el resto del arranque):

1. `EDECAN_OLLAMA_AUTOSTART` apagada → `None` inmediato.
2. Sin binario resoluble → `None` con log claro.
3. Ya hay un Ollama respondiendo en `settings.OLLAMA_BASE_URL` (`GET /api/tags`, timeout corto) → `None`: nunca se lanza un segundo proceso compitiendo por el mismo puerto — sea cual sea el origen de ese Ollama (el usuario lo tenía corriendo aparte, o quedó de un arranque anterior), la pantalla de Configuración ya lo va a ofrecer igual (`GET /v1/setup/detect`, ver `desktop.md` §10).
4. Arranque feliz: `subprocess.Popen([binario, "serve"], env=...)` con `OLLAMA_HOST` derivado de `OLLAMA_BASE_URL`, y sondeo con reintentos cortos hasta que responda (máx. 20s) → `OllamaHandle`. Si nunca responde, se lo detiene igual y se devuelve `None` — Ollama nunca es un requisito para que el resto del asistente arranque.

`OllamaHandle.stop()` (idempotente) sigue el MISMO criterio de apagado prolijo que `backend.rs::kill_backend` (§8 arriba): `terminate()` (SIGTERM) + espera corta (3s) y, si no alcanzó, `kill()` (SIGKILL) como red de seguridad. `edecan_local.runtime.run()` la llama SIEMPRE en su `finally` existente, ANTES de `pg_handle.cleanup()` (orden inverso al de arranque: Ollama arrancó después de Postgres, así que se apaga antes) — cubierto con tests que fuerzan una excepción a mitad del arranque para confirmar que el `finally` igual corre (`apps/local/tests/test_runtime.py`).

**Del lado de Rust** (`apps/desktop/src-tauri/src/backend.rs::with_ollama_env`/`resolve_ollama_sidecar`): ver la "Ampliación (fase v4...)" de la nota de verificación en §8 arriba — el camino sin Ollama bundleado ya está verificado empíricamente (2026-07-09); el checklist con Ollama bundleado de verdad sigue pendiente de correr.

## Ver también

- [`desktop.md`](./desktop.md) — la app de escritorio en sí: instalación, build, dónde viven los datos vistos desde la app, troubleshooting.
- `ARCHITECTURE.md` §12.f/§12.g — contrato técnico pinned de este runner (nombres/rutas exactos).
- [`self-hosting.md`](./self-hosting.md) — la alternativa "clonar el repo y correr con Docker Compose", para quien prefiere ese camino en vez de la app de escritorio.
