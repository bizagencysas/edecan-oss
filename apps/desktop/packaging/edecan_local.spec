# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — congela `python -m edecan_local` (WP-V3-05,
`apps/local/edecan_local`, contrato pinned en `ARCHITECTURE.md` §12.f) en un
binario onefile `edecan-local` que el sidecar de Tauri lanza (ver
`../src-tauri/src/backend.rs`, `../../../docs/desktop.md`).

Se invoca SIEMPRE vía `uv run pyinstaller packaging/edecan_local.spec` desde
`apps/desktop/` (`scripts/build-backend.sh`/`.ps1`) — nunca con `pyinstaller`
suelto ni con un venv de un solo paquete, porque necesita el entorno
COMPARTIDO del workspace uv completo: los paquetes de `edecan.tools`
listados en `EDECAN_TOOL_PACKAGES` abajo (16 a la fecha de v7 — ver esa
lista para el detalle exacto) NO son dependencias directas de
`apps/local/pyproject.toml` (se resuelven en runtime vía entry points,
`ARCHITECTURE.md` §10.7/§12.a) y solo están instalados si `uv run` usa el
`.venv` de la raíz del workspace (`uv sync` sin `--package`, mismo criterio
documentado en `apps/api/pyproject.toml` sobre `edecan-business`).

Modo onefile, corregido 2026-07-09 (ver `HOTFIXES_PENDIENTES.md`) — ANTES
era onedir (arranca más rápido, más fácil de depurar), pero eso resultó ser
incompatible con el mecanismo de sidecar de Tauri: `tauri.conf.json` ->
`bundle.externalBin` (y el `build.rs` de `tauri-build` que copia el sidecar
resuelto a `target/<profile>/` en cada `cargo build`/`cargo run`, no solo en
`cargo tauri build`) asume un binario de UN SOLO ARCHIVO por sidecar — copia
únicamente el ejecutable con el nombre exacto `edecan-local-<target-triple>`,
nunca sus archivos hermanos. Verificado empíricamente construyendo el sidecar
onedir de verdad, corriendo `cargo build`, y ejecutando el binario resultante
en `target/debug/`: revienta con `Failed to load Python shared library
'.../target/debug/libpython3.12.dylib'` porque ese `.dylib` (y el resto de
los ~90 archivos hermanos: paquetes `edecan_*`, `alembic/`, `web/`, etc.)
nunca se copiaron ahí — solo el ejecutable. Con onedir, esto rompía TANTO el
flujo de desarrollo (`cargo run`) COMO, muy probablemente, el instalador
real (`cargo tauri build` empaqueta el sidecar en `Contents/MacOS/` del
`.app` con el mismo mecanismo de un solo archivo). Onefile resuelve esto de
raíz: PyInstaller empaqueta TODO (intérprete, libs, datas) dentro de un
único ejecutable que se autoextrae a un directorio temporal en cada arranque
(`sys._MEIPASS`) — exactamente el modelo de sidecar de un solo archivo que
Tauri espera. Costo: arranque más lento (autoextracción) y un ejecutable más
pesado — aceptado a cambio de que el sidecar funcione de verdad.
`scripts/build-backend.sh`/`.ps1` copian ese único archivo (ya no una
carpeta) al lugar donde Tauri espera el sidecar.
"""

from __future__ import annotations

import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules, get_package_paths

# ---------------------------------------------------------------------------
# Rutas. PyInstaller inyecta `SPECPATH` en el namespace de este archivo ANTES
# de ejecutarlo — es la carpeta que contiene el .spec (`os.path.split(SPEC)`,
# ver docs de PyInstaller "Using Spec Files"), potencialmente RELATIVA a la
# carpeta desde la que se invocó `pyinstaller`. Se resuelve con
# `os.path.abspath()` de entrada para que el resto del archivo trabaje con
# rutas absolutas sin importar el CWD exacto de quien lo invoque (siempre
# `apps/desktop/` vía los scripts de build, pero anclar a SPECPATH en vez de
# asumir el CWD es más robusto).
# ---------------------------------------------------------------------------
PACKAGING_DIR = os.path.abspath(SPECPATH)  # apps/desktop/packaging
DESKTOP_DIR = os.path.dirname(PACKAGING_DIR)  # apps/desktop
APPS_DIR = os.path.dirname(DESKTOP_DIR)  # apps
REPO_ROOT = os.path.dirname(APPS_DIR)  # raíz del repo

ENTRY_SCRIPT = os.path.join(PACKAGING_DIR, "edecan_local_entry.py")
ALEMBIC_SRC_DIR = os.path.join(REPO_ROOT, "packages", "db", "alembic")
# `scripts/build-backend.sh`/`.ps1` construyen `apps/web` (NEXT_OUTPUT=export)
# y copian `out/` acá ANTES de correr este spec.
WEB_SRC_DIR = os.path.join(PACKAGING_DIR, "web")

if not os.path.isfile(ENTRY_SCRIPT):
    raise SystemExit(f"[edecan_local.spec] falta {ENTRY_SCRIPT} (no debería pasar, está en el repo).")

# ---------------------------------------------------------------------------
# Paquetes `edecan_*` del workspace (`ARCHITECTURE.md` §12.h + §10.1).
#
# Se recolectan TODOS con `collect_all()`, no solo los que `edecan_local`
# importa directo (`edecan_api`/`edecan_worker`/`edecan_db`/`edecan_core`/
# `edecan_schemas`, ver `apps/local/pyproject.toml`) — porque `collect_all(
# pkg)` hace tres cosas a la vez, y las tres hacen falta para alguno de estos
# paquetes:
#
#   1. `copy_metadata(pkg)`: copia el `.dist-info` del paquete (incluye
#      `entry_points.txt`). SIN esto, `importlib.metadata.entry_points(
#      group="edecan.tools")` (`edecan_core.tools.registry.ToolRegistry`,
#      ARCHITECTURE.md §10.7) devuelve una lista VACÍA dentro del binario
#      congelado aunque el código de cada herramienta esté perfectamente
#      empaquetado ahí adentro — ninguna herramienta de
#      toolkit/docanalysis/browser/creative/mensajería/... se registraría,
#      en silencio, sin ningún error visible en el arranque.
#   2. `collect_data_files(pkg)`: cualquier archivo no-`.py` que algún
#      paquete necesite en runtime (plantillas, prompts empaquetados, etc).
#   3. `collect_submodules(pkg)`: suma TODOS los submódulos del paquete como
#      hidden imports — necesario en particular para `edecan_api.routers.*`
#      y `edecan_worker.handlers.*`, que se cargan con
#      `importlib.import_module(f"...{nombre}")` (string dinámico,
#      `ARCHITECTURE.md` §12.a) en vez de un `import` estático que el
#      análisis de PyInstaller pueda seguir solo.
#
# Cada paquete se recolecta en su propio `try/except`: uno que todavía sea
# el esqueleto mínimo que deja WP-V3-01 (sin código real aterrizado todavía,
# `ARCHITECTURE.md` §12.h) sigue siendo perfectamente importable (tiene su
# `__init__.py` real), así que esto no debería disparar nunca en la
# práctica — pero si algún paquete faltara del workspace por el motivo que
# sea, un build de escritorio no se rompe entero por eso (mismo espíritu
# tolerante-a-aterrizajes-parciales que el montaje defensivo de routers en
# `edecan_api.main`, `ARCHITECTURE.md` §12.a).
# ---------------------------------------------------------------------------
EDECAN_CORE_PACKAGES = [
    "edecan_local",  # el propio runner (contiene edecan_local_entry.py)
    "edecan_schemas",
    "edecan_db",
    "edecan_core",
    "edecan_api",
    "edecan_worker",
    "edecan_companion",
    # Paquetes intermedios de los que dependen edecan_api/edecan_worker
    # (ARCHITECTURE.md §10.1) — import estático, PyInstaller los seguiría
    # solo, pero se recolectan igual por si alguno hace despacho dinámico
    # interno (p. ej. `edecan_connectors`/`edecan_voice` eligen proveedor
    # OAuth/STT-TTS por nombre de string bring-your-own, §12.b/§12.c).
    "edecan_llm",
    "edecan_connectors",
    "edecan_voice",
]

# Los paquetes de herramientas que el agente descubre vía entry points
# `edecan.tools` (`edecan_core.tools.registry.ToolRegistry.load_entry_points`,
# ARCHITECTURE.md §10.7) — enumerados explícitamente por nombre tal como
# pide este work package. Nunca los importa nadie de forma estática, solo el
# entry point resuelto en runtime.
#
# `edecan_travel` (v5, WP-V5-09, `ARCHITECTURE.md` §14) se suma con el mismo
# criterio que `edecan_ads` (v4): esqueleto reservado por WP-V5-01, tools
# reales de WP-V5-09.
#
# `edecan_voice` (v5, WP-V5-10, `ARCHITECTURE.md` §14) aparece DOS veces a
# propósito en este archivo, no por descuido: ya está en
# `EDECAN_CORE_PACKAGES` de arriba (import estático de
# `edecan_api`/`edecan_local` para el registro STT/TTS de voz web, §10.9) y
# AHORA también acá, porque desde WP-V5-10 gana su PROPIO entry point
# `edecan.tools` (`listar_voces`/`sintetizar_voz`) — un motivo de
# `collect_all()` completamente distinto al de arriba (import estático vs.
# metadata de entry points, ver el docstring largo más arriba para las 3
# razones de `collect_all`). Listarlo en ambos lugares documenta las dos
# razones por separado; `_collect()` corriendo dos veces sobre el mismo
# paquete es inofensivo (PyInstaller tolera entradas repetidas en
# `datas`/`binaries`/`hiddenimports`), así que no hace falta "elegir" un solo
# lugar ni deduplicar a mano.
#
# NO incluye `edecan_vehicles` (también expone `[project.entry-points.
# "edecan.tools"]`, ARCHITECTURE.md §13.h): exclusión a propósito, no
# olvido — "Vehículos (Smartcar) eliminado del alcance" en
# `DIRECCION_ACTUAL.md` deja pinned que ese paquete queda huérfano/inerte,
# "no conectarlo a la API/worker/apps móviles". Si algún día se revierte esa
# decisión de producto, agregarlo aquí también.
#
# `edecan_meetings` (v6, WP-V6-05, `ARCHITECTURE.md` §15.f) se suma con el
# mismo criterio que `edecan_travel` arriba: expone `resumir_reunion` SOLO
# vía el entry point `edecan.tools`. Gap ya documentado como pendiente en
# `packages/meetings/README.md`, sección "Nota sobre el workspace uv".
EDECAN_TOOL_PACKAGES = [
    "edecan_toolkit",
    "edecan_docanalysis",
    "edecan_browser",
    "edecan_creative",
    "edecan_messaging",
    "edecan_agents",
    "edecan_automations",
    "edecan_commerce",
    "edecan_advisory",
    "edecan_business",
    "edecan_skills",
    "edecan_smarthome",
    "edecan_ads",
    "edecan_travel",
    "edecan_voice",
    "edecan_meetings",
]

datas: list[tuple[str, str]] = []
binaries: list[tuple[str, str]] = []
hiddenimports: list[str] = []


def _collect(pkg: str) -> None:
    """`collect_all(pkg)` defensivo — ver docstring de arriba: un paquete
    ausente/no importable en la máquina de build no debe tirar abajo todo
    el `.spec`, solo perderse él (se loguea para que quede visible en la
    salida de `pyinstaller`, no en silencio)."""
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    except Exception as exc:  # noqa: BLE001 — defensivo a propósito
        print(f"[edecan_local.spec] aviso: no se pudo recolectar '{pkg}' ({exc}); se omite.")
        return
    datas.extend(pkg_datas)
    binaries.extend(pkg_binaries)
    hiddenimports.extend(pkg_hidden)


for _pkg in EDECAN_CORE_PACKAGES + EDECAN_TOOL_PACKAGES:
    _collect(_pkg)

# Explícito además de lo que ya trae `collect_all("edecan_api"/"edecan_worker")`
# arriba — redundante a propósito (defensa en profundidad): si algún día esos
# dos `collect_all()` dejan de traer todos los submódulos por el motivo que
# sea, esto solo sigue garantizando que el montaje dinámico de routers (§12.a)
# y el despacho de handlers del worker no se rompan en silencio.
hiddenimports += collect_submodules("edecan_api.routers")
hiddenimports += collect_submodules("edecan_worker.handlers")

# ---------------------------------------------------------------------------
# Dependencias de terceros con descubrimiento/carga dinámica propia — no
# alcanza con que `edecan_local`/`edecan_api` las importen estático:
#
#   - `pgserver`: Postgres+pgvector embebido (`DIRECCION_ACTUAL.md`,
#     "Postgres embebido"; dependencia directa con marker de plataforma en
#     `apps/local/pyproject.toml`) — trae binarios reales de Postgres
#     (`collect_dynamic_libs`) además de datos y submódulos, por eso
#     `collect_all` y no solo `collect_submodules`.
#   - `fakeredis`: cola/caché en memoria para `QUEUE_PROVIDER=db` +
#     `REDIS_URL=memory://` en modo local (`ARCHITECTURE.md` §12.g).
#   - `alembic`: `edecan_local.migrate` (contrato §12.f: "migraciones
#     aplicadas" antes de imprimir `EDECAN_LOCAL_READY`) invoca alembic vía
#     su API de Python contra `packages/db/alembic.ini`/`env.py` — alembic
#     descubre sus propios templates/plugins de forma dinámica.
#   - `uvicorn`: sirve `edecan_api.main:create_app()` dentro del proceso
#     congelado — sus loops/protocolos (`asyncio` puro / `uvloop`+
#     `httptools` cuando están disponibles) se eligen en runtime
#     (`uvicorn.loops.auto`, `uvicorn.protocols.http.auto`, etc.), invisible
#     para el análisis estático de PyInstaller.
#   - `boto3`/`botocore`/`aiobotocore`/`aioboto3`: S3 (`edecan_local.
#     objectstore`, subida de archivos) y SQS (modo `QUEUE_PROVIDER=sqs`)
#     resuelven su cliente de cada servicio con `importlib.import_module()`
#     en runtime, a partir de un modelo de datos JSON empaquetado (no un
#     import estático que PyInstaller pueda ver) — corregido 2026-07-09
#     (ver `HOTFIXES_PENDIENTES.md`): el binario congelado sin este bloque
#     arranca, provisiona Postgres, aplica migraciones y monta todos los
#     routers, pero revienta recién al crear el cliente S3 con
#     `ModuleNotFoundError: No module named 'aioboto3.s3'` — solo se notó
#     corriendo el binario de verdad, no con `cargo check`/una lectura de
#     código.
# ---------------------------------------------------------------------------
for _pkg in ("pgserver", "fakeredis", "alembic", "uvicorn", "boto3", "botocore", "aiobotocore", "aioboto3"):
    _collect(_pkg)

# En Linux, los `.so` bajo `pgserver/pginstall/lib/postgresql` NO son
# extensiones de Python ni bibliotecas enlazadas directamente por el
# ejecutable congelado. Son módulos que Postgres carga en runtime mediante
# `$libdir/<nombre>` (por ejemplo `dict_snowball.so` durante `initdb` y
# `vector.so` al aplicar nuestras migraciones). Si quedan en `a.binaries`,
# PyInstaller intenta tratarlos como dependencias ELF del proceso Python y
# puede omitirlos/reubicarlos; el onefile entonces arranca `initdb`, pero
# Postgres falla con `could not access file "$libdir/dict_snowball"`.
#
# Reubicarlos de `binaries` a `datas` conserva bytes y ruta exactos dentro de
# `_MEIPASS`, que es precisamente el prefijo relocatable que el Postgres del
# wheel ya calcula desde `pginstall/bin/postgres`. Las bibliotecas enlazadas
# normales de `pginstall/lib` permanecen en `binaries` y siguen recibiendo el
# análisis ELF de PyInstaller.
if sys.platform.startswith("linux"):
    _postgres_module_dest = "pgserver/pginstall/lib/postgresql"
    _, _pgserver_package_dir = get_package_paths("pgserver")
    _postgres_module_dir = os.path.join(
        _pgserver_package_dir, "pginstall", "lib", "postgresql"
    )
    _postgres_loadable_modules = [
        (entry.path, _postgres_module_dest)
        for entry in os.scandir(_postgres_module_dir)
        if entry.is_file() and entry.name.endswith(".so")
    ]

    # `collect_all()` sí encuentra alguna biblioteca aislada de esta carpeta
    # (hoy `libpqwalreceiver.so`) y la agrega a `binaries`. Se quitan esas
    # entradas antes de agregar el conjunto completo como `datas` para no
    # dejar dos TOC con el mismo destino dentro del onefile.
    binaries[:] = [
        entry
        for entry in binaries
        if entry[1].replace("\\", "/") != _postgres_module_dest
    ]
    datas.extend(_postgres_loadable_modules)

    _module_names = {os.path.basename(entry[0]) for entry in _postgres_loadable_modules}
    _required_modules = {"dict_snowball.so", "vector.so"}
    _missing_modules = _required_modules - _module_names
    if _missing_modules:
        raise SystemExit(
            "[edecan_local.spec] el wheel Linux de pgserver no contiene módulos "
            f"PostgreSQL requeridos: {', '.join(sorted(_missing_modules))}."
        )

# Gotchas conocidos de congelar uvicorn/SQLAlchemy+asyncpg con PyInstaller
# que `collect_all("uvicorn")` NO cubre (solo mira dentro del propio paquete
# uvicorn): uvicorn resuelve su loop/protocolo HTTP con
# `importlib.import_module()` sobre estos nombres exactos en runtime, y el
# dialecto `postgresql+asyncpg` de SQLAlchemy se registra vía entry points
# de setuptools (`sqlalchemy.dialects`), igual de invisible para el análisis
# estático. Si el binario final revienta con `ModuleNotFoundError` en un
# import dinámico que no esté ni aquí ni arriba, agregalo a esta lista.
hiddenimports += [
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "sqlalchemy.dialects.postgresql.asyncpg",
]
_collect("sqlalchemy")

# ---------------------------------------------------------------------------
# Datos no-Python:
#   - Migraciones de alembic — `edecan_local.migrate` (contrato §12.f) las
#     resuelve en runtime con `EDECAN_ALEMBIC_DIR` (override) o, si no está
#     seteada y el proceso está congelado, `Path(sys._MEIPASS) / "alembic"`
#     — por eso el destino dentro del bundle se llama literalmente
#     `"alembic"` acá abajo, ese es el contrato con WP-V3-05.
#   - Web estática exportada de `apps/web` (`NEXT_OUTPUT=export`) — la copia
#     a `packaging/web/` `scripts/build-backend.sh`/`.ps1` ANTES de este
#     paso (ver esos scripts). `edecan_api.main.create_app()` la sirve en
#     `"/"` cuando `SERVE_WEB_DIR` apunta a una carpeta que existe
#     (`ARCHITECTURE.md` §12.a/§12.g) — incluida acá como `"web"` para que
#     ese mismo contrato encuentre algo bajo `sys._MEIPASS/web`.
# ---------------------------------------------------------------------------
if not os.path.isdir(ALEMBIC_SRC_DIR):
    raise SystemExit(
        f"[edecan_local.spec] no existe {ALEMBIC_SRC_DIR} — ¿corriste esto desde "
        "un checkout completo del repo? (packages/db/alembic debe existir; sin "
        "las migraciones el binario no podría preparar su base de datos)."
    )
datas.append((ALEMBIC_SRC_DIR, "alembic"))

if os.path.isdir(WEB_SRC_DIR) and os.listdir(WEB_SRC_DIR):
    datas.append((WEB_SRC_DIR, "web"))
else:
    # No es fatal: `--no-web` (contrato §12.f) es un modo soportado — típico
    # en desarrollo, cuando `apps/web` corre aparte con `npm run dev`. Pero
    # para un build de PRODUCCIÓN real (`scripts/build-app.sh`) esto es señal
    # de que algo salió mal antes (ver ese script, siempre corre el build de
    # `apps/web` primero) — por eso el aviso explícito en vez de solo omitir.
    print(
        f"[edecan_local.spec] aviso: {WEB_SRC_DIR} no existe o está vacío — este "
        "binario va a arrancar sin web estática empaquetada (nada que servir en "
        "SERVE_WEB_DIR salvo que apuntes a otra carpeta a mano). Normal si "
        "corriste este .spec suelto sin pasar por scripts/build-backend.sh/.ps1 "
        "(que construye apps/web antes de llegar acá)."
    )

# ---------------------------------------------------------------------------
# Analysis / PYZ / EXE — onefile (ver nota de arriba sobre por qué, corregido
# 2026-07-09). `console=True` a propósito: `edecan-local` es un proceso
# servidor/CLI, no una app con ventana — el sidecar de Tauri
# (`src-tauri/src/backend.rs`) depende de poder leer su stdout línea por
# línea para encontrar `EDECAN_LOCAL_READY`; un build "windowed"
# (`console=False`) puede dejar `sys.stdout` sin un pipe utilizable en
# Windows, exactamente lo que NO puede pasar acá.
# `upx=False`: la compresión UPX es una causa frecuente de falsos positivos
# de antivirus en binarios de PyInstaller (ver docs/desktop.md,
# troubleshooting) — se prioriza "no dispara el antivirus del cliente" sobre
# unos MB menos de tamaño de instalador.
# Sin `COLLECT`/`contents_directory`/`exclude_binaries`: esos tres son
# específicos del modo onedir (una carpeta de distribución con el ejecutable
# y sus archivos hermanos sueltos). En onefile, `a.binaries`/`a.datas` se
# pasan DIRECTO al `EXE()` de abajo — quedan empaquetados DENTRO del único
# ejecutable resultante, no hay una carpeta de distribución que armar
# aparte. `sys._MEIPASS` en runtime resuelve al directorio temporal donde
# PyInstaller se autoextrae en cada arranque (no "la carpeta del
# ejecutable", como en onedir) — `edecan_local_entry.py` y el resto del
# código de `apps/local` no asumen ninguna de las dos formas explícitamente
# (usan `sys._MEIPASS`/rutas relativas al propio paquete, nunca la ruta del
# ejecutable en sí), así que este cambio no les afecta.
# ---------------------------------------------------------------------------
a = Analysis(
    [ENTRY_SCRIPT],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="edecan-local",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    # Firma de código del binario del sidecar en sí (además de la firma del
    # instalador final que hace `cargo tauri build`, ver docs/desktop.md
    # "macOS: firma con tu propio Developer ID"): bring-your-own, igual
    # criterio que el resto del repo — nunca una identidad hardcodeada acá.
    # Dejalo en `None` (build sin firmar, camino por defecto) o pasá tu
    # propio `Developer ID Application: ...` vía la env var que lea tu fork
    # de este script si algún día lo necesitás automatizado.
    codesign_identity=os.environ.get("EDECAN_MACOS_CODESIGN_IDENTITY") or None,
    entitlements_file=None,
)
