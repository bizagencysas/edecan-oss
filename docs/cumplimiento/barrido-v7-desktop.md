# Barrido v7 — Escritorio: E2E real de `apps/local` + revisión de `apps/desktop` (WP-V7-11)

Este documento registra la verificación **end-to-end real** (no solo revisión de código) del
flujo "abrir la app → conectar LLM en pocos clics → chatear" contra el estado ACTUAL del
backend (tras todo lo que v4/v5/v6 agregaron), más la revisión de `apps/desktop` (sin compilar
Rust — este entorno sigue sin `cargo`/`rustc`, mismo límite de siempre, ver
[`desktop-local.md`](../desktop-local.md) §8). Referencias leídas completas antes de escribir
una línea: `DIRECCION_ACTUAL.md`, `ARCHITECTURE.md` §12 (contratos v3, runner local),
`HOTFIXES_PENDIENTES.md` (secciones `kill_backend`/apagado grácil, fuga de tareas asyncio en
`runtime.py`, riesgo residual de `uv run` suelto en `dev.sh`), y los cuatro docs que este mismo
paquete puede tocar (`desktop-local.md`, `desktop.md`, `primeros-pasos.md`, y este archivo).

**Resultado ejecutivo:**

1. **Se encontró y corrigió un bug real que rompía el arranque en frío de `apps/local` contra
   el `pgserver` instalado de verdad** — `edecan_local.pg.ensure_postgres` leía `server.uri`
   (atributo), pero `pgserver` 0.1.4 (la única versión que satisface
   `apps/local/pyproject.toml`, `embedded = ["pgserver>=0.1.4"]`) solo expone la conexión vía el
   MÉTODO `server.get_uri()`. Invisible para la suite normal porque el fake de
   `apps/local/tests/test_pg.py` asumía un `.uri` que el paquete real nunca tuvo — exactamente
   el patrón "esquema asumido vs. esquema real" que ya causó el bug crítico de `reuniones.py` en
   v6 (`HOTFIXES_PENDIENTES.md`). Corregido + 3 tests actualizados/nuevos (detalle en §1).
2. **El flujo completo funciona de punta a punta contra el backend real**, verificado con
   comandos reales (no simulados): arranque del runtime → registro de tenant real → wizard
   `/v1/setup/*` (con autodetección REAL de `claude`/`codex` CLI ya instalados en esta máquina)
   → conectar un proveedor LLM `openai_compat` sin ninguna credencial real (pegar-y-validar
   contra un servidor HTTP fake) → crear conversación → turno de chat completo por SSE con la
   respuesta canned llegando byte-por-byte idéntica al otro lado → apagado limpio con `SIGTERM`
   → cero procesos huérfanos. Detalle completo en §2.
3. **Revisión de `apps/desktop` sin compilar Rust**: sin bugs funcionales nuevos. El fix de
   `dev.sh` que este paquete tenía como tarea ("agregar `--all-packages` al default de
   `EDECAN_LOCAL_DEV_CMD`") **ya estaba aplicado** por trabajo previo — se verificó, se reforzó
   el mismo default también del lado de `backend.rs` (cubre el caso de correr `cargo tauri dev`
   directo, sin pasar por `dev.sh`), y se corrigieron 3 comentarios desactualizados ("12"/"13
   paquetes" cuando ya son 16). `EDECAN_TOOL_PACKAGES` del `.spec` de PyInstaller se comparó
   campo por campo contra los entry points `edecan.tools` reales de `packages/*/pyproject.toml`
   — completo, sin huecos. Detalle en §3.
4. **`uv run --all-packages pytest -q apps/local/tests -m "not integration"` → 134 passed, 2
   deselected**; corridos también los 2 `@pytest.mark.integration` (ahora que `pgserver` está
   instalado) → **2 passed**. `ruff check apps/local/ apps/desktop/` → limpio. `ps` de control
   final → sin ningún proceso huérfano.
5. **Ningún bug de código nuevo encontrado fuera de mi ruta** (`apps/api`, `apps/web`,
   `packages/*`) — todas las rutas HTTP ejercitadas en el E2E (`/v1/auth/register`,
   `/v1/setup/status`, `/v1/setup/detect`, `/v1/credentials`, `/v1/conversations`) se
   comportaron exactamente como documentan `ARCHITECTURE.md`/`docs/primeros-pasos.md`/
   `docs/credenciales.md`. Sí se encontró (y corrigió, dentro de MI ruta: solo los docs, cero
   cambios en `apps/web`) un **desajuste doc-vs-código**: `docs/desktop.md`/`docs/
   primeros-pasos.md` seguían advirtiendo sobre un bug de `apps/web/src/lib/api.ts`
   (`NEXT_PUBLIC_API_URL` resuelto con `||` en vez de `??`) que el código real YA NO TIENE —
   `api.ts` usa `??` desde antes de este paquete (no se pudo determinar en qué WP, este repo no
   tiene `.git`), pero nadie había actualizado los dos docs que lo mencionaban. Corregidos
   ambos (§6 tiene el detalle) — nada que
   reportar en esta ronda.

---

## 0. Entorno de esta verificación

```text
$ uv --version
uv 0.11.19 (Homebrew 2026-06-03 aarch64-apple-darwin)
$ uv run --all-packages python --version
Python 3.12.13
```

`pgserver` (extra opcional `edecan-local[embedded]`) y los otros 3 extras opcionales del
workspace (`playwright` de `packages/browser`, `vertex`/`google-auth` de `packages/llm`,
`remote-input`/`pyobjc` de `apps/companion`) **no estaban instalados** al empezar — un `uv sync
--all-packages` normal (el que usan todos los targets de `Makefile`) no instala extras
opcionales por defecto. Se corrió una vez, al principio:

```text
$ uv sync --all-packages --all-extras
Resolved 125 packages in 19ms
Downloading pyobjc-core (6.1MiB)
Downloading playwright (40.2MiB)
Downloading pgserver (9.4MiB)
Installed 12 packages in 45ms
 + fasteners==0.20
 + google-auth==2.55.2
 + pgserver==0.1.4
 + platformdirs==4.10.0
 + playwright==1.61.0
 + psutil==7.2.2
 + pyasn1==0.6.3
 + pyasn1-modules==0.4.2
 + pyee==13.0.1
 + pyobjc-core==12.2.1
 + pyobjc-framework-cocoa==12.2.1
 + pyobjc-framework-quartz==12.2.1
```

No tocó `pyproject.toml` ni `uv.lock` (ambos ya declaraban estos extras; solo faltaba
instalarlos en el `.venv` compartido) — confirmado por timestamp: ambos archivos siguen con
fecha de modificación anterior al inicio de esta sesión. Rust: se confirmó de nuevo que este
entorno sigue sin `cargo`/`rustc` (mismo límite documentado desde WP-V3-06) — la revisión de
`apps/desktop` en §3 es de código, no de compilación.

---

## 1. Bug real encontrado y corregido: `pgserver.PostgresServer` no tiene `.uri`

### 1.1 Cómo apareció

Primer arranque real de `python -m edecan_local` contra el `pgserver` recién instalado:

```text
Traceback (most recent call last):
  ...
  File ".../apps/local/edecan_local/pg.py", line 140, in ensure_postgres
    database_url = _to_asyncpg_url(server.uri)
                                   ^^^^^^^^^^
AttributeError: 'PostgresServer' object has no attribute 'uri'
```

Postgres embebido SÍ arrancó bien (`initdb` + `pg_ctl start` corrieron limpio, visible en el log
completo), pero `edecan_local.pg.ensure_postgres` no podía leer la URI de conexión — el runner
completo caía antes de llegar a aplicar migraciones. Este es exactamente el tipo de bug que la
suite normal (`pytest -m "not integration"`) no puede atrapar por construcción: el único test
que usa el paquete `pgserver` real (`test_ensure_postgres_embebido_real_con_pgserver`) está
marcado `@pytest.mark.integration` y se salta con `pytest.importorskip("pgserver")` si el
paquete no está instalado — que es el estado por defecto de cualquier `uv sync --all-packages`
sin `--all-extras`.

### 1.2 Causa raíz confirmada contra el código fuente real de `pgserver` 0.1.4

```text
$ python3 -c "import pgserver, inspect; print([m for m in dir(pgserver.postgres_server.PostgresServer) if not m.startswith('__')])"
['_cleanup', '_instances', '_lock', 'cleanup', 'ensure_pgdata_inited',
 'ensure_postgres_running', 'fasteners', 'get_pid', 'get_postmaster_info',
 'get_uri', 'lock_path', 'platformdirs', 'psql', 'runtime_path']
```

`PostgresServer` (0.1.4) expone `get_uri(self, user="postgres", database=None) -> str` como
MÉTODO — nunca hubo un atributo público `.uri`. `cleanup()` (usado por
`edecan_local.pg._EmbeddedHandle.cleanup`) sí existe tal cual y hace lo esperado (respeta
`cleanup_mode="stop"`, el default de `get_server()`, así que solo detiene el servidor, nunca
borra `pgdata`) — ese método estaba bien, el único punto roto era `server.uri`.

### 1.3 Fix aplicado

`apps/local/edecan_local/pg.py::ensure_postgres`:

```python
# antes
database_url = _to_asyncpg_url(server.uri)

# después
database_url = _to_asyncpg_url(server.get_uri())
```

`server.get_uri()` sin argumentos usa los defaults (`user="postgres"`, `database=None` →
`database=user`), que apunta a la base `"postgres"` que `initdb` siempre crea — suficiente para
este runner de un solo tenant embebido, donde el NOMBRE de la base no importa para aislamiento
(todo el cluster es privado de esta instalación).

### 1.4 Tests actualizados/nuevos (`apps/local/tests/test_pg.py`)

- Los dos fakes existentes (`test_ensure_postgres_modo_embebido_arranca_pgserver_y_convierte_uri`,
  `test_ensure_postgres_data_dir_con_tilde_se_expande`) cambiaron su `_FakeServer` de un
  atributo `.uri` a un método `get_uri()` — para que el fake deje de mentir sobre la forma real
  del paquete.
- **Test nuevo dedicado**,
  `test_ensure_postgres_modo_embebido_nunca_lee_un_atributo_uri`: un fake que a propósito **no
  define `.uri` en absoluto** (ni como atributo ni como método) — cualquier regresión futura a
  `server.uri` vuelve a fallar con `AttributeError` de inmediato, así que este test SOLO puede
  pasar si el código de producción usa `get_uri()`.

Verificación (unit + integration, esta última ahora corre de verdad porque `pgserver` está
instalado):

```text
$ uv run --all-packages pytest -q apps/local/tests/test_pg.py -m "not integration"
13 passed, 1 deselected in 0.04s

$ uv run --all-packages pytest -q apps/local/tests/test_pg.py -m "integration"
1 passed, 13 deselected in 1.23s
```

`ps` después de la corrida del test de integración: ningún proceso `pgserver`/`postgres`
huérfano (solo el Postgres del sistema, preexistente, no relacionado).

---

## 2. E2E real, paso a paso (comandos + resultados reales)

Todo corrido con datos de prueba en el scratchpad (`--data-dir` propio, nunca
`~/.edecan/data`), puerto `8765` (el default real que usa la app de escritorio).

### Paso 1 — Arrancar el runtime real

```text
$ uv run --all-packages python -m edecan_local --port 8765 --data-dir <scratch>/edecan-local-data
...
2026-07-09 07:43:05,969 INFO edecan_local.migrate Migraciones aplicadas (upgrade head).
2026-07-09 07:43:06,554 INFO edecan_api edecan_premium detectado: rutas de telefonía Twilio y consentimiento montadas.
2026-07-09 07:43:06,562..07,302 INFO edecan_api router v2/v3/v4/v5/v6 '...' montado.   (23 routers, cero fallas de montaje)
2026-07-09 07:43:07,544..593  INFO edecan_core.tools.registry Cargadas N herramienta(s) desde el entry point '<paquete>' (edecan.tools)  (18 entry points, cero fallas)
2026-07-09 07:43:07,595 INFO uvicorn.error Uvicorn running on http://127.0.0.1:8765 ...
2026-07-09 07:43:07,595 INFO uvicorn.error Uvicorn running on http://127.0.0.1:8767 ...
2026-07-09 07:43:07,595 INFO edecan_local.worker_loop escuchando la tabla 'jobs' (poll=2.0s, scheduler=30.0s).
EDECAN_LOCAL_READY port=8765
```

Arrancó en ~2.5s desde la línea de comando hasta `EDECAN_LOCAL_READY` (incluyendo `initdb` del
cluster embebido, migraciones Alembic completas, y carga de los 18 entry points `edecan.tools`
de todo el workspace: `toolkit`(17), `docanalysis`(8), `advisory`(8), `business`(7),
`creative`(6), `travel`(5), `skills`(5), `commerce`(4), `browser`(3), `smarthome`(3),
`premium`(3), `messaging`(2), `voice`(2), `vehicles`(2), `ads`(2), `agents`(1),
`automations`(1), `meetings`(1)). Nota esperada, no un bug: `edecan_premium detectado` aparece
porque el entorno de desarrollo usa `--all-packages` (ver `DIRECCION_ACTUAL.md`, discusión
análoga sobre `edecan_vehicles`) — no cambia el paquete real que se distribuye (§3.2).

```text
$ curl -s -w "\nHTTP_STATUS:%{http_code}\n" http://127.0.0.1:8765/healthz
{"status":"ok"}
HTTP_STATUS:200
```

### Paso 2 — Registrar un tenant real (credenciales de prueba, placeholder)

```text
$ curl -s -X POST http://127.0.0.1:8765/v1/auth/register -H "Content-Type: application/json" \
    -d '{"email":"wp-v7-11-test2@example.com","password":"PlaceholderPass123","tenant_name":"WP-V7-11 Smoke Test 2"}'
{"access_token":"eyJ...","refresh_token":"eyJ...","token_type":"bearer"}
HTTP_STATUS:201
```

### Paso 3 — Wizard real de primer arranque (`/v1/setup/*`)

```text
$ curl -s http://127.0.0.1:8765/v1/setup/status -H "Authorization: Bearer $TOKEN"
{"local_mode":true,"llm_configured":false,"version":"0.1.0"}

$ curl -s http://127.0.0.1:8765/v1/setup/detect -H "Authorization: Bearer $TOKEN"
{"local_mode":true,
 "claude_cli":{"installed":true,"path":"/Users/hennsolutionsllc/.local/bin/claude","version":"2.1.202 (Claude Code)"},
 "codex_cli":{"installed":true,"path":"/Users/hennsolutionsllc/.local/bin/codex","version":"codex-cli 0.142.5"},
 "ollama":{"running":false,"base_url":"http://localhost:11434","models":[]}}

$ curl -s http://127.0.0.1:8765/v1/credentials -H "Authorization: Bearer $TOKEN"
{"llm":null,"voice_stt":null,"voice_tts":null,"images":null,"search":null}
```

Esto confirma en vivo, contra binarios reales de esta máquina, la promesa central de
"configuración de pocos clicks" (`DIRECCION_ACTUAL.md`): `edecan_llm.detect.
detect_local_providers` detectó de verdad `claude`/`codex` ya instalados y autenticados, sin
ninguna llamada de red — exactamente lo que la pantalla de Configuración usaría para ofrecer
"usar mi Claude CLI ya instalado" en un clic.

### Paso 4 — Mini servidor OpenAI-compatible fake (sin ninguna credencial real)

Script nuevo, vive SOLO en el scratchpad (`fake_openai_compat_server.py`, FastAPI + uvicorn, ya
dependencias del workspace) — implementa el subconjunto mínimo que
`edecan_llm.openai_compat.OpenAICompatProvider` necesita:

- `GET /models` → 200 (lo que pega el ping de "pegar-y-validar" de `PUT /v1/credentials/llm`).
- `POST /chat/completions` con `stream=true` → SSE con el mismo formato que
  `_iter_openai_sse` sabe parsear (`data: {...}` repetido + `data: [DONE]`), respuesta canned
  partida en 11 chunks (para probar streaming real, no un solo bloque).

```text
$ uv run --all-packages python fake_openai_compat_server.py --port 8899 &
$ curl -s http://127.0.0.1:8899/models
{"data":[{"id":"wp-v7-11-fake-model","object":"model"}]}
```

### Paso 5 — Conectar el LLM vía `PUT /v1/credentials/llm` (pegar-y-validar real)

```text
$ curl -s -w "\nHTTP_STATUS:%{http_code}\n" -X PUT http://127.0.0.1:8765/v1/credentials/llm \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"kind":"openai_compat","base_url":"http://127.0.0.1:8899",
         "api_key":"TU_OPENAI_COMPAT_API_KEY_AQUI",
         "model_principal":"wp-v7-11-fake-model","model_rapido":"wp-v7-11-fake-model",
         "validate":true}'
HTTP_STATUS:204
```

El log del servidor fake confirma que el ping de validación llegó de verdad:
`"GET /models HTTP/1.1" 200 OK`. Después de esto:

```text
$ curl -s http://127.0.0.1:8765/v1/setup/status -H "Authorization: Bearer $TOKEN"
{"local_mode":true,"llm_configured":true,"version":"0.1.0"}

$ curl -s http://127.0.0.1:8765/v1/credentials -H "Authorization: Bearer $TOKEN"
{"llm":{"kind":"openai_compat","model_principal":"wp-v7-11-fake-model","model_rapido":"wp-v7-11-fake-model",
        "base_url":"http://127.0.0.1:8899","masked":"…AQUI"}, "voice_stt":null,...}
```

`masked` confirma que nunca se expone la key completa (`"…AQUI"`, últimos 4 caracteres del
placeholder) — contrato de `docs/credenciales.md` respetado.

### Paso 6 — Chatear: conversación + turno completo por SSE

```text
$ curl -s -X POST http://127.0.0.1:8765/v1/conversations -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" -d '{"title":"WP-V7-11 smoke test","channel":"web"}'
{"id":"e7114163-...","title":"WP-V7-11 smoke test","channel":"web",...}
HTTP_STATUS:201

$ curl -s -N -X POST http://127.0.0.1:8765/v1/conversations/e7114163-.../messages \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"text":"Hola, esto es una prueba end-to-end de WP-V7-11. Respondeme algo corto."}'
event: message.delta
data: {"type": "text_delta", "text": "Hola, soy una respuesta "}
event: message.delta
data: {"type": "text_delta", "text": "canned del servidor OpenAI-compatible "}
... (11 chunks en total) ...
event: message.done
data: {"type": "done", "usage": {"input_tokens": 12, "output_tokens": 11}}
```

Reconstruyendo los 11 `text_delta` con un script Python de verificación, el texto **coincide
byte a byte** con el canned response del servidor fake (`MATCH: True`) — confirma el camino
completo: `runtime` → `LLMRouter` (con la config bring-your-own del tenant, NUNCA una credencial
de plataforma) → `OpenAICompatProvider.stream` → `Agent.run_turn` → SSE de
`POST /v1/conversations/{id}/messages`, de punta a punta, sin ninguna credencial real de
ningún proveedor.

`GET /v1/conversations/{id}` después del turno confirma que ambos mensajes (usuario +
asistente, con `tokens_in=12`/`tokens_out=11` tomados del `usage` del SSE) quedaron persistidos
en Postgres real.

### Paso 7 — Apagado limpio (`SIGTERM`) y verificación de huérfanos

`SIGTERM` mandado DIRECTO al proceso Python de `edecan_local.runtime` (el mismo patrón que
`apps/desktop/src-tauri/src/backend.rs::kill_backend` aplica al PID del sidecar):

```text
$ kill -TERM <pid-edecan-local>
$ # proceso terminó solo, confirmado con kill -0, en 1s
```

Log del propio proceso, apagado en el orden documentado por `runtime.py` (uvicorn API + object
store → worker loop → Ollama si corriera → Postgres embebido AL FINAL):

```text
INFO uvicorn.error Shutting down
INFO uvicorn.error Application shutdown complete.  (x2, API + object store)
INFO pgserver Running commandline: [... pg_ctl ... stop]
INFO pgserver Successful postgres command [...] stdout: waiting for server to shut down.... done / server stopped
INFO edecan_local.pg Postgres embebido detenido.
INFO edecan_local.runtime edecan_local detenido.
```

Verificación con `ps`/`lsof` inmediatamente después:

```text
$ ps -ef | grep -i edecan_local        # (vacío)
$ ps -ef | grep "<data-dir de la prueba>"   # (vacío -- ni el proceso postgres ni sus 8 workers hijos)
$ lsof -nP -iTCP:8765 -sTCP:LISTEN     # (vacío)
$ lsof -nP -iTCP:8767 -sTCP:LISTEN     # (vacío)
```

**Cero procesos huérfanos, cero puertos ocupados.** Esto ejercita empíricamente, por primera
vez, la MITAD Python del contrato de apagado grácil que `HOTFIXES_PENDIENTES.md`/
`desktop-local.md` §8 documentan: `edecan_local.runtime.run()` responde a `SIGTERM`, corre su
`finally` completo, y apaga `pgserver` limpio — exactamente lo que el fix de
`backend.rs::kill_backend` (mandar `SIGTERM` antes de escalar a `SIGKILL`) necesita del lado
del backend para funcionar. La mitad Rust (`CommandChild`/`tauri-plugin-shell` mandando la
señal de verdad al PID del sidecar empaquetado) sigue sin poder verificarse en este entorno sin
`cargo`/`rustc` — pero el eslabón que antes era 100% teórico del lado de Python ahora tiene
evidencia real.

El servidor fake (`fake_openai_compat_server.py`) también se detuvo con `SIGTERM` y se
confirmó sin huérfanos (`lsof -iTCP:8899` vacío).

---

## 3. Revisión de código `apps/desktop` (sin compilar Rust)

Mismo límite de siempre (`docs/desktop-local.md` §8, `README.md` de `apps/desktop`): sin
`cargo`/`rustc` en este entorno, esta sección es revisión de código + `bash -n` +
`py_compile`, no una compilación real.

### 3.1 `backend.rs` / `commands.rs` / `lib.rs` / `tray.rs` / `util.rs` contra el contrato real de `apps/local`

| Punto del contrato (`ARCHITECTURE.md` §12.f / `docs/desktop-local.md`) | Verificado contra el código real hoy |
|---|---|
| Bind solo `127.0.0.1`, puerto preferido `8765` | `backend.rs::pick_port`/`PREFERRED_PORT` — coincide con el default real de `runtime.py::DEFAULT_PORT` (verificado en §2, arrancó en `8765` sin pedir nada). |
| `--port`/`--data-dir` pasados al sidecar | `build_command` arma `backend_args = ["--port", ..., "--data-dir", ...]` — mismos nombres de flag que `runtime.parse_args` (`ARCHITECTURE.md` §12.f). |
| Espera `EDECAN_LOCAL_READY port=<p>` en stdout, máx. 60s | `backend.rs::READY_MARKER`/`READY_TIMEOUT` — el string exacto coincide con lo que de verdad imprimió el proceso real en el Paso 1 de §2. |
| Apagado: `SIGTERM` + hasta 3s de espera, escalando a `SIGKILL` (macOS/Linux) | `send_sigterm_and_wait_for_exit` (`kill -TERM` + poll de `kill -0` cada 100ms, máx. 3s) — **la mitad Python de este contrato quedó verificada empíricamente en el Paso 7 de §2** (el runtime real respondió a `SIGTERM` y completó su `finally` en ~1s, dentro del margen de 3s). La nota "pendiente verificación con cargo" en `docs/desktop-local.md` §8 se deja intacta (no se borra): sigue siendo cierto que la mitad Rust (`CommandChild`, el binario `kill` invocado desde Rust) no se pudo compilar/ejecutar en este entorno. |
| Un solo punto de salida garantizado (`RunEvent::Exit`) mata el sidecar siempre | `lib.rs::run()` — `on_window_event`→`exit(0)` y el `.run(|app, event| ...)` final cubren ventana/bandeja/panel de error, todos convergen en `RunEvent::Exit`→`kill_backend`. Sin cambios necesarios. |
| Comandos invocables desde el splash (`retry_backend`/`quit_app`) y eventos (`edecan://backend-status`/`-log`/`-error`) | `commands.rs`/`backend.rs` coinciden 1:1 con los nombres que usa `src-tauri/splash/index.html` (`invoke("retry_backend"/"quit_app")`, `listen("edecan://backend-log"/...)`) — verificado con `grep` cruzado, sin desajustes. |
| Menú de bandeja: abrir navegador / ver carpeta de datos / salir | `tray.rs` — cada acción reusa `backend::current_port`/`backend::data_dir`/`util::open_in_*`, sin lógica propia. Sin cambios. |
| Ollama embebido (env vars `EDECAN_OLLAMA_BIN`/`EDECAN_OLLAMA_AUTOSTART`) | `with_ollama_env`/`resolve_ollama_sidecar` — mismos nombres que `edecan_local.ollama_supervisor` (`apps/local`, SÍ tiene tests, corridos limpios como parte de la suite de §5). Sigue pendiente de verificación empírica del lado Rust, sin cambios de este paquete. |

**Fix aplicado (hardening, no un bug de comportamiento distinto en la práctica)**: el default
Rust de `EDECAN_LOCAL_DEV_CMD` (usado SOLO si la env var no está fijada en absoluto) seguía
siendo `"uv run python -m edecan_local"`, sin `--all-packages` — el mismo riesgo residual que
`HOTFIXES_PENDIENTES.md` documentó para `dev.sh`. `scripts/dev.sh` YA exporta esa variable
explícita con `--all-packages` antes de invocar `cargo tauri dev` (§3.3, ya resuelto por
trabajo previo a este paquete), así que en la práctica el camino documentado (`./scripts/
dev.sh`) ya estaba cerrado — pero alguien que corriera `cargo tauri dev` DIRECTO, sin pasar por
`dev.sh`, seguía golpeando el default sin el flag. Cambiado a `"uv run --all-packages python -m
edecan_local"` + comentario explicando el porqué y la relación con `dev.sh`. Cambio de un solo
string literal, riesgo de compilación esencialmente nulo — pero sigue sin poder confirmarse con
`cargo build` real en este entorno.

### 3.2 `packaging/edecan_local.spec` — `EDECAN_TOOL_PACKAGES` contra los entry points reales

Comparación campo por campo entre `EDECAN_TOOL_PACKAGES` del `.spec` y
`grep -rn 'edecan.tools' packages/*/pyproject.toml premium/pyproject.toml`:

| Paquete con entry point `edecan.tools` real | ¿En `EDECAN_TOOL_PACKAGES`? |
|---|---|
| `edecan_toolkit`, `edecan_docanalysis`, `edecan_browser`, `edecan_creative`, `edecan_messaging`, `edecan_agents`, `edecan_automations`, `edecan_commerce`, `edecan_advisory`, `edecan_business`, `edecan_skills`, `edecan_smarthome`, `edecan_ads`, `edecan_travel`, `edecan_voice`, `edecan_meetings` | ✅ Los 16, sin faltantes (confirmado también en vivo en el Paso 1 de §2: los 16 aparecen en el log real "Cargadas N herramienta(s)..." — más `agents`/`vehicles` que se explican abajo). |
| `edecan_vehicles` | ❌ Deliberado — `DIRECCION_ACTUAL.md` "Vehículos eliminado del alcance" sigue vigente, exclusión NO negociable, **no se tocó**. |
| `edecan_premium` (`premium/pyproject.toml`) | ❌ Correcto, no es un gap — `docs/self-hosting.md` documenta explícitamente que el núcleo funciona completo sin él ("puedes activar `premium/`... instalando el paquete `edecan_premium` por separado"); ninguno de los dos Dockerfiles de producción ni `apps/api/pyproject.toml`/`apps/worker/pyproject.toml` lo declaran como dependencia tampoco — es comercial/opcional en los TRES caminos de distribución real, no solo en el de escritorio. Que aparezca "detectado" en el log de §2 es un artefacto de correr en modo dev con `--all-packages` (mismo mecanismo ya documentado para `edecan_vehicles` en `DIRECCION_ACTUAL.md`), no del build empaquetado real. |
| `edecan_mcp` (`packages/mcp`) | ❌ Correcto, no es un gap — a diferencia de los 16 de arriba, MCP **no** expone tools vía el entry point estático `edecan.tools` (`ARCHITECTURE.md` §15.g: las tools `mcp_{slug}_{tool}` son dinámicas POR TENANT, inyectadas en cada turno vía `extra_tools`, nunca vía `ToolRegistry.load_entry_points`) — así que no pertenece a esta lista por diseño. Se captura igual en el binario congelado por una vía distinta: `edecan-mcp` es dependencia DECLARADA de `apps/api`/`apps/worker` (`grep` confirma `"edecan-mcp"` en ambos `pyproject.toml`) y `edecan_api.routers.mcp`/`edecan_api.deps` lo importan con `import` estático (no dinámico) — el análisis de módulos de PyInstaller lo sigue solo, sin necesitar `collect_all()` (que en este paquete solo hace falta para metadata de entry points/datos/submódulos dinámicos, ninguno de los cuales aplica acá: `packages/mcp/edecan_mcp/*.py` no tiene imports dinámicos ni entry points propios, confirmado leyendo los 6 archivos). |

**Conclusión: `EDECAN_TOOL_PACKAGES` está completo, sin faltantes reales — no hizo falta
agregar nada.** Se corrigieron 3 comentarios desactualizados que decían "12"/"13 paquetes"
(quedaron así de cuando la lista era más corta, en v3/v4) en `packaging/edecan_local.spec`,
`scripts/build-backend.sh` y `scripts/build-backend.ps1` — puramente cosmético (texto de
comentario, cero efecto en el `.spec` en sí, que ya itera la lista real
programáticamente), pero corregido para que quien lea el comentario no subestime cuánto hay
que verificar.

### 3.3 Scripts (`build-backend.sh`/`.ps1`, `build-app.sh`, `download-ollama.sh`/`.ps1`, `dev.sh`)

```text
$ bash -n scripts/build-app.sh scripts/build-backend.sh scripts/dev.sh \
          scripts/download-ollama.sh scripts/make-icons.sh
OK (los 5)
$ python -m py_compile packaging/edecan_local_entry.py packaging/edecan_local.spec
OK (ambos)
```

`dev.sh` línea 45 (antes reportada como línea 43 en `HOTFIXES_PENDIENTES.md`, se corrió por los
comentarios agregados al aplicar el fix):

```bash
export EDECAN_LOCAL_DEV_CMD="${EDECAN_LOCAL_DEV_CMD:-uv run --all-packages python -m edecan_local}"
```

**Ya tenía `--all-packages` aplicado** — verificado, no hizo falta ningún cambio en este
archivo. El hardening del lado `backend.rs` (§3.1) cierra el único hueco que quedaba (correr
`cargo tauri dev` sin pasar por este script).

### 3.4 Empaquetado de la web (item (e), sin tocar `apps/web`)

`scripts/build-backend.sh`/`.ps1` siguen construyendo `apps/web` con
`NEXT_OUTPUT=export NEXT_PUBLIC_API_URL='' npm run build` y copiando `apps/web/out/` →
`packaging/web/` (que el `.spec` empaqueta como datos `"web"`, servidos por
`edecan_api.main.create_app()` cuando `SERVE_WEB_DIR` apunta ahí). Confirmado que
`apps/web/next.config.mjs` **sigue** soportando `NEXT_OUTPUT=export` (`isExport = process.env.
NEXT_OUTPUT === "export"` → `output: "export"`) — el pointer sigue apuntando al build real de
`apps/web`, sin desincronización. No se tocó ningún archivo de `apps/web` (dueño WP-V7-09).

---

## 4. Barridos pedidos

**asyncio** — `_run_background` (única función de `runtime.py` que envuelve las 3 tareas de
fondo) sigue intacta, sin ningún `asyncio.create_task(...)` nuevo que la salte; nada tocado en
este paquete crea tareas de fondo nuevas.

**Bring-your-own** — el E2E completo de §2 nunca usó ninguna credencial real de ningún
proveedor: LLM se conectó contra un servidor HTTP propio en el scratchpad (`kind=
"openai_compat"`, `api_key` placeholder `TU_OPENAI_COMPAT_API_KEY_AQUI`), confirmando en vivo
que `apps/local`/`apps/api` nunca degradan a una credencial de plataforma cuando el tenant trae
la suya (`get_llm_router` construyó el `LLMRouter` con la config del tenant, `LLMProviderConfig
(kind="openai_compat", ...)`, nunca con `ANTHROPIC_API_KEY`/`.env`).

**Pocos clicks** — confirmado extremo a extremo: `GET /v1/setup/status` antes de conectar
cualquier cosa reportó `llm_configured:false` sin bloquear ningún otro endpoint (el registro,
`/v1/setup/detect`, `GET /v1/credentials` funcionaron igual sin LLM conectado); un solo `PUT
/v1/credentials/llm` con pegar-y-validar bastó para poder chatear de inmediato, sin ningún paso
intermedio. `GET /v1/setup/detect` demostró la autodetección de CLIs (`claude`/`codex`) contra
binarios reales de esta máquina.

---

## 5. Verificación final

```text
$ uv run --all-packages pytest -q apps/local/tests -m "not integration"
134 passed, 2 deselected in 2.78s

$ uv run --all-packages pytest -q apps/local/tests -m "integration"
2 passed, 134 deselected in 2.04s

$ uv run --all-packages ruff check apps/local/
All checks passed!

$ uv run --all-packages ruff check apps/desktop/
All checks passed!
```

`ps`/`lsof` de control final (después de TODO lo anterior — arranque real, tests de
integración, apagado del E2E):

```text
$ ps -ef | grep -Ei "edecan_local|fake_openai_compat" | grep -v grep
(vacío)
$ ps -ef | grep -i "pgserver/pginstall"  | grep -v grep
(vacío)
$ lsof -nP -iTCP:8765,8767,8899 -sTCP:LISTEN
(vacío)
```

Sin Docker en este paquete (no hizo falta ninguno — el runner local no depende de contenedores,
`ARCHITECTURE.md` §12.f), así que no aplica el `docker ps -a` de control.

---

## 6. Fuera de mi ruta (para quien siga)

Ningún bug de código nuevo en `apps/api`, `apps/web` o `packages/*` durante este barrido — las
rutas HTTP ejercitadas (`/v1/auth/register`, `/v1/setup/status`, `/v1/setup/detect`,
`/v1/credentials`, `/v1/conversations`, `/v1/conversations/{id}/messages`) se comportaron
exactamente como documentan `ARCHITECTURE.md`/`docs/primeros-pasos.md`/`docs/credenciales.md`,
sin ningún desajuste de esquema, contrato o comportamiento.

**Sí se encontró un desajuste doc-vs-código** (documental, no de código — corregido dentro de
mi propia ruta, cero cambios en `apps/web`): tanto `docs/desktop.md` §8 (troubleshooting) como
`docs/primeros-pasos.md` §4 seguían advirtiendo que `apps/web/src/lib/api.ts` resolvía
`NEXT_PUBLIC_API_URL` con `||` en vez de `??` — un bug real en su momento (un `||` convierte de
vuelta a `http://localhost:8000` el `NEXT_PUBLIC_API_URL=''` intencional que usa el build de
escritorio para same-origin, rompiendo el login/chat si el backend terminó en un puerto
distinto a `8000`, típicamente `8765`). Verificado leyendo el código real: **ya no es cierto**
— `apps/web/src/lib/api.ts:39` usa `??`, igual que `api-configuracion.ts`/`api-mcp.ts` (que
definen su propio `API_BASE_URL`), y los otros 16 archivos `api-*.ts` importan ese mismo
`API_BASE_URL` ya corregido desde `api.ts` en vez de redefinirlo (`grep -rn
"NEXT_PUBLIC_API_URL" apps/web/src/lib/*.ts` → cero ocurrencias de `||` para este patrón en
todo el árbol). No se pudo determinar en qué work package se corrigió `api.ts` (este repo no
tiene `.git`, y el propio archivo no trae ninguna nota de changelog) — probablemente
concurrente con WP-V7-09 (dueño de `apps/web`) en esta misma ola v7, o incluso antes. Ambos
docs corregidos para reflejar el estado real (§0/troubleshooting de `desktop.md`, §4 de
`primeros-pasos.md`) — este es exactamente el tipo de "trabajo que ya estaba hecho pero el
reporte/doc quedó desactualizado" que `DIRECCION_ACTUAL.md` pide verificar contra el archivo
real antes de asumir que falta.

Sigue pendiente, sin cambios respecto de antes de este paquete (fuera de lo que este entorno
puede resolver): verificación empírica con `cargo build`/`cargo tauri build` real de TODO
`apps/desktop/src-tauri` — este paquete no tenía `cargo`/`rustc` disponibles, igual que los
anteriores. Lo que sí cambió: la mitad Python del contrato de apagado grácil (`SIGTERM` →
`runtime.py` → `pgserver.cleanup()`) que antes era 100% teórica ahora tiene evidencia empírica
real (§2 Paso 7) — reduce (no elimina) el riesgo de lo que queda pendiente del lado Rust.

## Ver también

- [`desktop-local.md`](../desktop-local.md) — arquitectura interna del runner local.
- [`desktop.md`](../desktop.md) — la app de escritorio en sí (instalación, build, troubleshooting).
- [`primeros-pasos.md`](../primeros-pasos.md) — el wizard de bienvenida desde la perspectiva de quien lo usa.
- `ARCHITECTURE.md` §12.f/§12.g — contrato técnico pinned del runner local.
- `HOTFIXES_PENDIENTES.md` — historial completo de hallazgos previos, incluida la nota de
  apagado grácil que este paquete amplía con evidencia empírica nueva (no reemplaza).
