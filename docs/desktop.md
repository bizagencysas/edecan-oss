# App de escritorio (Tauri)

**El vehículo principal de venta del producto** (`DIRECCION_ACTUAL.md`, "Qué se vende"): un instalable de macOS/Windows que se descarga, se instala, se abre, se conecta con tus propias credenciales en Configuración y queda funcionando — sin servidor propio, sin Docker, sin editar ningún archivo a mano. Este documento cubre instalación, requisitos y pasos de build por plataforma, dónde viven tus datos, cómo desinstalar y troubleshooting. Para el recorrido completo del wizard de bienvenida y la pantalla de Configuración (que es el mismo dentro de la app de escritorio que en cualquier otro modo), ver [`primeros-pasos.md`](./primeros-pasos.md). Para cómo corre el backend empaquetado por dentro (Postgres embebido, migraciones, colas), ver [`desktop-local.md`](./desktop-local.md) *(WP-V3-05)*.

Código fuente de este paquete: [`apps/desktop`](../apps/desktop) (referencia técnica rápida en su propio `README.md`).

## 1. Arquitectura en 60 segundos

```
apps/desktop (Tauri, Rust) — el shell nativo:
  1. arranca → elige puerto libre (preferencia 8765)
  2. muestra la ventana de splash (HTML embebido, "Arrancando tu asistente…")
  3. lanza edecan_local como sidecar:
       edecan-local --port <P> --data-dir <carpeta de datos de la app>
  4. espera la línea "EDECAN_LOCAL_READY port=<P>" en su stdout (máx. 60s)
  5. abre la ventana principal → http://127.0.0.1:<P>/  y cierra el splash
  6. al salir (ventana, bandeja o botón "Salir"): mata el sidecar SIEMPRE

        │
        ▼ el sidecar sirve, en el mismo origen (http://127.0.0.1:<P>/):

  apps/local (Python, WP-V3-05)        apps/web exportado estático
  API + worker + Postgres embebido,    (Next.js, NEXT_OUTPUT=export,
  todo local                            lo sirve el propio backend en "/")
```

Ni la interfaz ni el backend se reescriben para la versión de escritorio: `apps/desktop` es pura orquestación nativa alrededor de piezas que ya existen. Contrato completo del backend local: `ARCHITECTURE.md` §12.f.

## 2. Instalación (para quien solo usa la app)

1. Descargá el instalador de tu plataforma y abrilo.
   - **macOS**: `Edecán.dmg` → arrastrá `Edecán.app` a Aplicaciones. Como el `.dmg` de este repo sale **sin firmar** por defecto (ver §7), macOS va a bloquear la primera apertura ("no se puede abrir porque no se puede verificar el desarrollador"): hacé **clic derecho (o Control-clic) sobre la app → Abrir → Abrir** de nuevo en el diálogo de confirmación. Solo hace falta esa vez.
   - **Windows**: `Edecán-Setup.exe` (NSIS) → siguiente, siguiente. SmartScreen puede avisar "Windows protegió tu PC" la primera vez (mismo motivo: sin firmar) — **Más información → Ejecutar de todas formas**.
2. Al abrir por primera vez ves la ventana de splash ("Arrancando tu asistente…") mientras el backend local termina de prepararse (crea su base de datos embebida, corre migraciones) — tarda unos segundos, no minutos.
3. La app abre directo en el wizard de bienvenida (2–3 pasos: conectar un proveedor de LLM y listo) — recorrido completo en [`primeros-pasos.md`](./primeros-pasos.md).

Nada de esto pide un `.env`, una terminal ni una base de datos propia — ver §9.

## 3. Requisitos para compilar

Solo hacen falta si vos mismo vas a **generar** el instalador (no para usarlo ya generado):

| Herramienta | Para qué | Notas |
|---|---|---|
| **Rust** estable (`rustup`) + `cargo-tauri` | El shell nativo | `cargo install tauri-cli --version '^2.0'` |
| **Node.js 20+** y npm | Build estático de `apps/web` | Ya usado por el resto del repo |
| **Python 3.12** + [`uv`](https://docs.astral.sh/uv/) | `edecan_local` (backend) y su empaquetado | El workspace uv completo del repo, no un venv aislado |
| **PyInstaller** | Congela `edecan_local` en un binario | **No hace falta instalarlo a mano** — `scripts/build-backend.sh`/`.ps1` lo agregan al vuelo con `uv run --with pyinstaller`, sin tocar ningún `pyproject.toml` del repo |
| macOS: Xcode Command Line Tools | `sips`/`iconutil` (`scripts/make-icons.sh`) | Ya presentes en cualquier Mac con Xcode CLT instalado |
| Windows: Visual Studio Build Tools (C++) | Requisito estándar de compilar con MSVC | El instalador de Rust para Windows ya te lo pide si falta |

## 4. Build paso a paso

### 4.1 macOS

```bash
cd apps/desktop
./scripts/build-app.sh
```

Internamente: construye `apps/web` en modo export estático → congela `edecan_local` con PyInstaller (onedir) → copia ambos donde Tauri los espera → `cargo tauri build`. Salida en `src-tauri/target/release/bundle/`:

- `dmg/Edecán_<version>_<arch>.dmg` — el instalador para repartir.
- `macos/Edecán.app` — la app suelta (útil para probar sin montar el dmg).

Sin firmar por defecto (§7). Requiere macOS 11+ para correr (`tauri.conf.json` → `bundle.macOS.minimumSystemVersion`).

### 4.2 Windows

```powershell
cd apps\desktop
.\scripts\build-backend.ps1
cargo tauri build
```

(No hay `build-app.ps1` — son las dos líneas de arriba, a mano; `build-backend.ps1` hace la parte de web+PyInstaller, igual que `build-backend.sh` en macOS/Linux.) Salida en `src-tauri\target\release\bundle\`:

- `nsis\Edecán_<version>_<arch>-setup.exe` — instalador NSIS (el recomendado; instala por-usuario, sin pedir admin — `tauri.conf.json` → `bundle.windows.nsis.installMode: "currentUser"`).
- `msi\Edecán_<version>_<arch>.msi` — alternativa MSI, útil si tu organización despliega por GPO/Intune.

### 4.3 Modo desarrollo

```bash
cd apps/desktop
./scripts/dev.sh          # shell nativo con recarga en caliente; backend desde código fuente
make web                  # (aparte, opcional) UI real de apps/web en :3000 con hot reload
```

`dev.sh` **no** corre PyInstaller ni exporta `apps/web` — arranca `cargo tauri dev`, y el sidecar corre directo `uv run python -m edecan_local` desde el código fuente (variable `EDECAN_LOCAL_DEV_CMD`, con default a eso mismo). Sirve para iterar rápido en Rust o en el backend; para probar el flujo exacto que ve un cliente (shell + web empaquetada + backend congelado) usá `build-app.sh`.

## 5. Dónde viven tus datos

Dos ubicaciones posibles — cuál aplica depende de cómo corriste el backend:

- **Corriendo por la app de escritorio (el caso normal, siempre)**: la app SIEMPRE le pasa `--data-dir` explícito al backend, apuntando a la carpeta de datos de la propia app que resuelve Tauri (`app_data_dir()` + `/data`):
  - macOS: `~/Library/Application Support/cc.edecan.desktop/data/`
  - Windows: `%APPDATA%\cc.edecan.desktop\data\` (`C:\Users\<vos>\AppData\Roaming\cc.edecan.desktop\data\`)

  Ahí vive la base de datos embebida (conversaciones, memoria, credenciales cifradas, todo) y los archivos que subas.
- **Corriendo `edecan_local` suelto** (`python -m edecan_local` o el binario `edecan-local`, sin pasar por Tauri — típico si estás depurando el backend a mano): usa su propio default, `DATA_DIR=~/.edecan/data` (`ARCHITECTURE.md` §12.g), salvo que también le pases `--data-dir` vos mismo.

El menú de bandeja tiene un atajo directo: **"Ver carpeta de datos"** abre la carpeta correcta (la primera opción de arriba) en Finder/Explorador, sin tener que recordar la ruta.

Nota aparte: la ventana principal carga `http://127.0.0.1:<puerto>/` como contenido **externo** (no un asset empaquetado de Tauri) — el motor de webview del sistema (WKWebView en macOS, WebView2 en Windows) guarda su propio caché/cookies para ese origen en su ubicación estándar del SO, separada de lo de arriba. Es contenido regenerable (no hay nada ahí que no puedas volver a cargar), así que no hace falta trackearlo para backups.

## 6. Cómo desinstalar

- **macOS**: arrastrá `Edecán.app` (en Aplicaciones) a la Papelera. No queda un ícono de desinstalador separado — así funciona cualquier `.app` de macOS.
- **Windows**: **Configuración → Aplicaciones → Aplicaciones instaladas → Edecán → Desinstalar** (el instalador NSIS registra un desinstalador estándar de Windows).

En ambos casos, **tus datos NO se borran** — desinstalar la app deja intacta la carpeta de §5 por defecto, para que reinstalar más adelante no pierda nada. Si además querés borrar tus datos por completo, borrá a mano esa carpeta (usá "Ver carpeta de datos" en la bandeja mientras la app todavía esté instalada para encontrarla rápido, o andá directo a la ruta de §5).

## 7. Firma de código

Este repo genera instaladores **sin firmar** por defecto — es el camino que funciona out-of-the-box para cualquiera que clone el repo, sin depender de un certificado de pago. Bring-your-own, igual criterio que el resto del producto (`ARCHITECTURE.md` §0):

- **macOS**: si tenés tu propio Apple Developer ID, `packaging/edecan_local.spec` ya lee `EDECAN_MACOS_CODESIGN_IDENTITY` (env var, tu `"Developer ID Application: Tu Nombre (TEAMID)"`) para firmar el binario del sidecar; para firmar el `.app`/`.dmg` final, `cargo tauri build` respeta las variables estándar de Tauri (`APPLE_SIGNING_IDENTITY`, y para notarizar además `APPLE_ID`/`APPLE_PASSWORD` o `APPLE_API_KEY` — ver la [guía oficial de firma de Tauri](https://v2.tauri.app/distribute/sign/macos/)). Sin notarizar, Gatekeeper sigue pidiendo el clic derecho→Abrir de §2 aunque esté firmado; con notarización completa, desaparece.
- **Windows**: análogo con un certificado de firma de código propio (Authenticode) — variables `TAURI_SIGNING_PRIVATE_KEY`/o el flujo de `signtool` que documenta Tauri para Windows. Sin firmar, SmartScreen avisa la primera vez (§2) pero el instalador funciona igual.

Ninguna identidad ni certificado real vive en este repo — solo el enganche para que vos pongas el tuyo.

## 8. Troubleshooting

**"El backend local tardó más de 60 segundos" / la app se queda en el splash.**
Abrí "Ver detalle técnico" en la propia ventana de splash — muestra el stdout/stderr del sidecar en vivo. Si termina en error, el panel rojo que aparece ya trae las últimas 50 líneas de log y un botón "Reintentar" (repite el arranque desde cero, sin cerrar la app).

**Puerto ocupado.**
No debería pasar nunca en la práctica: la app prueba primero `8765` y, si está ocupado, le pide uno libre al sistema operativo automáticamente (nunca falla por esto). Para saber qué puerto terminó usando, mirá "Ver detalle técnico" en el splash (imprime `EDECAN_LOCAL_READY port=<p>`) o abrí el menú de bandeja → "Abrir en el navegador" (siempre apunta al puerto correcto vigente).

**La ventana principal abre pero el login/chat no responde (mientras el splash sí llegó a mostrarla).**
**Resuelto (verificado 2026-07-09, WP-V7-11)** — este punto describía un bug real de `apps/web/src/lib/api.ts` (resolvía `NEXT_PUBLIC_API_URL` con `||` en vez de `??`, así que un vacío explícito —el que usa el build de escritorio para same-origin— podía recaer en el default hardcodeado `http://localhost:8000` en vez de quedarse relativo), pero **ya no es cierto contra el código actual**: `apps/web/src/lib/api.ts` línea 39 usa `??` (`process.env.NEXT_PUBLIC_API_URL?.replace(/\/+$/, "") ?? "http://localhost:8000"`), igual que `api-configuracion.ts`/`api-mcp.ts` (que definen su propio `API_BASE_URL`), y el resto de `api-*.ts` importan ese mismo `API_BASE_URL` ya corregido desde `api.ts` — confirmado con `grep -rn "NEXT_PUBLIC_API_URL" apps/web/src/lib/*.ts`, cero ocurrencias de `||` en ese patrón en todo el árbol. No se pudo determinar en qué work package se corrigió (no hay commits que consultar, este repo no tiene `.git`), pero el código real ya no tiene el bug — si alguna vez volvés a ver este síntoma, es una regresión nueva, no este caso ya conocido.

**Antivirus/Defender/Gatekeeper marcan el instalador o lo ponen en cuarentena.**
Esperable en un binario sin firmar (§7) — es el motivo por el que `packaging/edecan_local.spec` deja la compresión UPX apagada a propósito (`upx=False`; UPX es una causa frecuente de falsos positivos en binarios de PyInstaller, incluso más que el resto). Si tu antivirus corporativo bloquea la instalación por completo: agregá una excepción para la carpeta de instalación, o firmá vos mismo el build (§7) — la firma de código es, con diferencia, lo que más baja estos falsos positivos.

**`cargo tauri` no es un comando reconocido.**
Falta el subcomando (no viene con `cargo` ni con `rustup`): `cargo install tauri-cli --version '^2.0'`.

**PyInstaller no encuentra `edecan_local`/tira `ModuleNotFoundError` para algún paquete `edecan_*`.**
`scripts/build-backend.sh`/`.ps1` corren `uv run --with pyinstaller pyinstaller packaging/edecan_local.spec` desde `apps/desktop/` — necesitan resolver el entorno **compartido del workspace uv completo** (todos los `packages/*`/`apps/*`, no solo `apps/local`), porque las 12 herramientas del agente se descubren en runtime vía entry points (`edecan.tools`, `ARCHITECTURE.md` §10.7) y no son dependencias directas de `apps/local/pyproject.toml`. Si corriste `uv sync --package <algo>` en vez de un `uv sync` normal en algún momento, puede que tu entorno esté acotado — un `uv sync` sin `--package` desde la raíz del repo lo resuelve.

## 9. Filosofía: cero `.env` a mano

A diferencia de self-hosting (`self-hosting.md`, pensado para quien ya está cómodo con Docker/`.env`), la app de escritorio asume que quien la instala **no** quiere tocar archivos de configuración. Por eso:

- Ninguna credencial viaja en el instalador ni en el repo — todo se conecta desde la pantalla de **Configuración** dentro de la propia app (misma pantalla web de siempre, servida ahora por el backend local).
- El único paso obligatorio para poder chatear es conectar un proveedor de LLM (wizard de 2-3 pasos); todo lo demás (voz, telefonía, conectores) queda como tarjetas opcionales, nunca bloqueando el primer uso — detalle completo en [`primeros-pasos.md`](./primeros-pasos.md).
- La app detecta automáticamente `claude`/`codex`/Ollama ya instalados en tu máquina y los ofrece con un clic, sin pedir ninguna API key — tiene más sentido todavía en la app de escritorio que en cualquier otro modo, porque acá SIEMPRE hay "tu máquina" de la cual autodetectar (`DIRECCION_ACTUAL.md`, "conectar el LLM vía CLI local").

## 10. Ollama embebido (opcional)

Patrón de auto-provisioning adaptado de [`open-jarvis/OpenJarvis`](https://github.com/open-jarvis/OpenJarvis) (Apache-2.0, ver `NOTICE`): en vez de depender de que el cliente instale Ollama aparte, quien empaqueta la app puede **incluir el binario de Ollama directo en el instalador** — "IA local gratis, cero fricción" (prioridad del roadmap del producto). Es 100% opcional en los dos sentidos: opcional para quien empaqueta (no hace falta para que la app funcione) y opcional para quien la usa (sigue pudiendo conectar Anthropic/OpenAI/Vertex/Claude CLI/Codex CLI/su propio Ollama externo desde Configuración igual que siempre, ver [`proveedores-llm.md`](./proveedores-llm.md)).

**Para quien empaqueta un release:**

```bash
cd apps/desktop
./scripts/download-ollama.sh          # antes de build-app.sh / build-backend.sh
./scripts/build-app.sh
```

`scripts/download-ollama.sh` (`.ps1` en Windows) descarga el binario oficial de [ollama.com](https://ollama.com) para el target triple de esta máquina (o el que le pases como argumento) y lo deja en `src-tauri/binaries/ollama-<target-triple>` — mismo lugar y misma convención de sidecar (`tauri.conf.json` → `bundle.externalBin`) que ya usa `edecan-local`. Alternativa de un solo paso: `EDECAN_BUNDLE_OLLAMA=1 ./scripts/build-backend.sh` corre ese script automáticamente antes de armar el resto del backend. Sin correr ninguno de los dos, el `.dmg`/instalador sale exactamente igual que hoy, solo que sin el binario de Ollama adentro.

**Cómo lo activa el usuario final:** hoy, fijando `EDECAN_OLLAMA_AUTOSTART=true` en el entorno antes de abrir la app (uso avanzado/dev). La pieza de "un solo clic" ya existe del lado de detección: `GET /v1/setup/detect` (`apps/api/edecan_api/routers/setup.py`, WP-V3-03/`edecan_llm.detect.detect_local_providers`) ya reporta si Ollama está corriendo en `OLLAMA_BASE_URL`, y la pantalla de Configuración ya ofrece "usar Ollama" con un clic apenas lo detecta corriendo (`DIRECCION_ACTUAL.md`, "configuración de pocos clicks") — no importa si ese Ollama lo arrancó el usuario a mano, ya estaba corriendo de antes, o lo arrancó `edecan_local.ollama_supervisor` (ver abajo) por él: para la pantalla de Configuración es indistinguible, simplemente "ya está corriendo, un clic y listo".

**Qué pasa por dentro:** cuando `EDECAN_OLLAMA_AUTOSTART` está activada, `edecan_local.ollama_supervisor.maybe_start_ollama` (dentro del backend local, `edecan_local.runtime.run()`) resuelve el binario (`EDECAN_OLLAMA_BIN`, la ruta al sidecar que el paso de arriba empaquetó — la fija automáticamente `apps/desktop/src-tauri/src/backend.rs` al lanzar el sidecar si lo encuentra — o si no, `ollama` en el `PATH`), evita lanzar un segundo proceso si ya hay uno corriendo, y lo apaga limpio al cerrar la app (mismo criterio de apagado prolijo que el resto del backend local — ver [`desktop-local.md`](./desktop-local.md) §8). Es de "mejor esfuerzo" en todo momento: cualquier problema (binario roto, puerto ocupado, nunca responde) se resuelve en silencio con un log claro, nunca bloquea el arranque del resto del asistente. Detalle técnico completo en [`desktop-local.md`](./desktop-local.md).

## Ver también

- [`primeros-pasos.md`](./primeros-pasos.md) — wizard de bienvenida y pantalla de Configuración, paso a paso.
- [`desktop-local.md`](./desktop-local.md) — cómo corre el backend por dentro (Postgres embebido, migraciones, colas).
- [`self-hosting.md`](./self-hosting.md) — alternativa para quien prefiere correr Edecán desde el código fuente en vez de instalar el binario.
- [`proveedores-llm.md`](./proveedores-llm.md) — qué proveedor de LLM elegir.
- `ARCHITECTURE.md` §12.f — contrato técnico pinned del runner local que consume esta app.
