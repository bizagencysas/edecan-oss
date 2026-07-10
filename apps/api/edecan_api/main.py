"""`edecan_api.main` — construcción de la app FastAPI (ARCHITECTURE.md §10.12).

`create_app()`:

- Primero llama `Settings.assert_safe_for_prod()`: en `ENV=prod` rechaza
  arrancar si `JWT_SECRET`/`LOCAL_MASTER_KEY` siguen en su placeholder público
  de `.env.example` (mismo patrón fail-fast que `edecan_worker.main._amain()`
  usa para `SQS_QUEUE_URL`). En dev/test no hace nada.
- Configura CORS para `WEB_BASE_URL` y un middleware que agrega `X-Request-ID`
  y registra una línea de log estructurada por request (método, ruta, status,
  duración — nunca cuerpo ni headers, para no filtrar PII).
- Monta todos los routers de `edecan_api.routers`.
- En el `lifespan` construye el `ToolRegistry` de `edecan_core` y carga las
  herramientas registradas vía entry points (`edecan.tools`).
- Si `edecan_premium` está instalado (`importlib.util.find_spec`), monta
  `edecan_premium.twilio_router.router` (§10.10) y `edecan_api.routers.
  consents.router` (`POST /v1/consents`, único punto de entrada auditado hacia
  `edecan_premium.compliance.grant_consent`) — el núcleo funciona completo sin
  ese paquete. `twilio_router` espera un contrato en `app.state` (documentado
  en su propio módulo): `get_session` (mismo contrato que
  `edecan_db.session.get_session`), `make_vault(session) -> TokenVault` y
  `settings` (la instancia real de `Settings` del proceso -- barrido v7,
  WP-V7-12: sin esto, `TWILIO_MEDIA_STREAMS_ENABLED` nunca se leía como
  `True` en un despliegue real, ver `docs/voz-telefonia.md` "Interrupciones
  naturales (beta)"); las tres se conectan aquí. `app.state.phone_agent`
  (turno del agente sin streaming para el webhook `/gather`) se deja sin
  configurar a propósito:
  `edecan_premium.twilio_router` ya degrada con una disculpa genérica si no
  está presente, y resolver identidad/persona para una llamada entrante (sin
  usuario autenticado) es una decisión de producto que no está pinned en
  ARCHITECTURE.md — queda para un paquete de trabajo dedicado a telefonía.
  `edecan_api.routers.consents`, a diferencia de `twilio_router`, sí usa
  `edecan_api.deps` (`get_current_user`/`get_tenant_session`/`rate_limit`)
  porque autentica al usuario del tenant vía JWT normal, no vía firma Twilio —
  por eso vive en `apps/api` y no en `premium/` (ver el docstring de ese
  router para el razonamiento completo de capas).
- Monta los routers v2 (ROADMAP_V2.md §7.6, dueño WP-V2-01: `missions`,
  `automations`, `hooks`, `ide`, `remote`, `commerce`, `negocios`, `perfil`)
  de forma DEFENSIVA: cada uno se importa con `importlib.import_module`
  dentro de su propio `try/except ImportError`, con `logger.warning` (nunca
  `raise`) si falta. Es defensivo A PROPÓSITO, no un descuido: los 8 work
  packages v2 dueños de esos routers aterrizan en paralelo, sin orden
  garantizado entre sí ni respecto a este archivo — v1 ya dejó la lección
  (ROADMAP_V2.md §2.3/§2.5) de que un import perezoso "roto" resultó ser,
  en más de una auditoría, simplemente un paquete hermano que su propio WP
  todavía no había aterrizado, no un bug real. Con este patrón, `create_app()`
  sigue arrancando completa (con el resto de rutas v1 + v2 ya aterrizadas)
  sin importar cuántos de esos 8 módulos existan todavía — cada WP dueño
  aparece automáticamente en cuanto crea su `edecan_api/routers/<name>.py`
  exportando `router`, sin tener que tocar este archivo ni coordinarse con
  los demás.
- Monta los routers v3 (`ARCHITECTURE.md` §12, dueño WP-V3-01: `credentials`,
  `setup`, `skills`, `smarthome`) con EL MISMO patrón defensivo que v2 —
  mismo motivo (los 4 WPs dueños aterrizan en paralelo, sin orden
  garantizado).
- Monta los routers v4 (`ARCHITECTURE.md` §13, dueño WP-V4-01: `devices`,
  `erp`, `ads`, `vehiculos`, `mensajes`) con EL MISMO patrón defensivo que
  v2/v3 — mismo motivo. `devices` lo construye este mismo WP (WP-V4-01,
  linchpin de v4); los otros 4 los construyen WPs paralelos y pueden
  aterrizar después.
- Monta los routers v5 (`ARCHITECTURE.md` §14, dueño WP-V5-01: `rrhh`,
  `viajes`, `voz_avanzada`) con EL MISMO patrón defensivo que v2/v3/v4 —
  mismo motivo. A diferencia de v4 (donde este WP construyó `devices` de
  verdad), este WP NO construye ningún router v5 real — los 3 quedan para
  WPs paralelos, así que al aterrizar este archivo los 3 arrancan con el
  aviso de "módulo no disponible todavía" hasta que cada WP dueño cree su
  propio `edecan_api/routers/{rrhh,viajes,voz_avanzada}.py`.
- Monta los routers v6 (`ARCHITECTURE.md` §15, dueño WP-V6-01: `reuniones`,
  `analista`, `mcp`) con EL MISMO patrón defensivo que v2/v3/v4/v5 — mismo
  motivo. Igual que v5, este WP NO construye ningún router v6 real — los 3
  quedan para WPs paralelos (`reuniones`/`analista` con WP-V6-05, `mcp` con
  WP-V6-07). Los endpoints de podcasts (`POST/GET /v1/voz/podcasts*`) NO son
  un router nuevo: WP-V6-04 los agrega DENTRO del router `voz_avanzada` ya
  montado por v5, así que no aparecen en `V6_ROUTER_NAMES`.
- Al final, si `settings.SERVE_WEB_DIR` apunta a una carpeta que existe,
  monta ahí el export estático de `apps/web` en la raíz ("/") — así el
  runner local de la app de escritorio (`apps/local`, WP-V3-05) sirve la UI
  completa desde el mismo puerto que la API. Va DESPUÉS de todos los routers
  (v1, v2, v3, `edecan_premium`) a propósito: un `Mount("/")` de Starlette
  solo actúa como fallback para lo que ninguna ruta explícita ya haya
  reclamado, así que montarlo antes taparía `/v1/*` y `/healthz`. Sin
  `SERVE_WEB_DIR` (default `None`) o si la carpeta no existe todavía, no se
  monta nada en "/" — solo un `logger.warning` en el segundo caso.
"""

from __future__ import annotations

import importlib.util
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from edecan_api import __version__
from edecan_api.companion_manager import ConnectionManager
from edecan_api.config import Settings, get_settings
from edecan_api.routers import (
    admin,
    auth,
    billing,
    companion,
    connectors,
    contacts,
    conversations,
    files,
    finance,
    me,
    memory,
    persona,
    reminders,
    usage,
    voice,
)

logger = logging.getLogger("edecan_api")

# v2 (ROADMAP_V2.md §7.6, dueño WP-V2-01): nombres EXACTOS de los routers que
# se montan de forma defensiva más abajo — módulo constante (en vez de un
# literal dentro de `create_app()`) para que `test_v2_mounting.py` pueda
# importarlo y verificar que sigue coincidiendo con §7.6 sin duplicar la lista.
V2_ROUTER_NAMES: tuple[str, ...] = (
    "missions",
    "automations",
    "hooks",
    "ide",
    "remote",
    "commerce",
    "negocios",
    "perfil",
)

# v3 (ARCHITECTURE.md §12, dueño WP-V3-01): mismo criterio que V2_ROUTER_NAMES
# — constante de módulo para que `test_v3_mounting.py` la importe en vez de
# duplicar la lista.
V3_ROUTER_NAMES: tuple[str, ...] = (
    "credentials",
    "setup",
    "skills",
    "smarthome",
)

# v4 (ARCHITECTURE.md §13, dueño WP-V4-01): mismo criterio que
# V2_ROUTER_NAMES/V3_ROUTER_NAMES — constante de módulo para que
# `test_v4_mounting.py` la importe en vez de duplicar la lista.
V4_ROUTER_NAMES: tuple[str, ...] = (
    "devices",
    "erp",
    "ads",
    "vehiculos",
    "mensajes",
)

# v5 (ARCHITECTURE.md §14, dueño WP-V5-01): mismo criterio que
# V2_ROUTER_NAMES/V3_ROUTER_NAMES/V4_ROUTER_NAMES — constante de módulo para
# que `test_v5_mounting.py` la importe en vez de duplicar la lista.
V5_ROUTER_NAMES: tuple[str, ...] = (
    "rrhh",
    "viajes",
    "voz_avanzada",
)

# v6 (ARCHITECTURE.md §15, dueño WP-V6-01): mismo criterio que
# V2_ROUTER_NAMES/V3_ROUTER_NAMES/V4_ROUTER_NAMES/V5_ROUTER_NAMES — constante
# de módulo para que `test_v6_mounting.py` la importe en vez de duplicar la
# lista. Los endpoints de podcasts NO están acá: viven dentro del router
# `voz_avanzada` ya montado por v5 (dueño real WP-V6-04).
V6_ROUTER_NAMES: tuple[str, ...] = (
    "reuniones",
    "analista",
    "mcp",
)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Agrega `X-Request-ID` (reusa el del cliente si ya trae uno) y registra
    una línea de log estructurada por request — sin cuerpo, headers ni query
    string, para no filtrar PII (ARCHITECTURE.md §10.12: "logging estructurado
    sin PII")."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.exception(
                "request_id=%s method=%s path=%s status=500 duration_ms=%.1f",
                request_id,
                request.method,
                request.url.path,
                duration_ms,
            )
            raise
        duration_ms = (time.perf_counter() - start) * 1000
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request_id=%s method=%s path=%s status=%d duration_ms=%.1f",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response


def _configure_logging(settings: Settings) -> None:
    level = getattr(logging, (settings.LOG_LEVEL or "INFO").upper(), logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    from edecan_core.tools import ToolRegistry

    registry = ToolRegistry()
    registry.load_entry_points(group="edecan.tools")
    app.state.tool_registry = registry
    logger.info("edecan_api listo: ToolRegistry construido desde entry points 'edecan.tools'.")
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    settings.assert_safe_for_prod()
    _configure_logging(settings)

    app = FastAPI(title="Edecán API", version=__version__, lifespan=_lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.WEB_BASE_URL],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestContextMiddleware)

    app.state.companion_manager = ConnectionManager()

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(auth.router)
    app.include_router(me.router)
    app.include_router(persona.router)
    app.include_router(conversations.router)
    app.include_router(memory.router)
    app.include_router(connectors.router)
    app.include_router(files.router)
    app.include_router(reminders.router)
    app.include_router(contacts.router)
    app.include_router(finance.router)
    app.include_router(voice.router)
    app.include_router(companion.router)
    app.include_router(usage.router)
    app.include_router(admin.router)
    app.include_router(billing.router)

    if importlib.util.find_spec("edecan_premium") is not None:
        from edecan_db.session import get_session
        from edecan_db.vault import TokenVault
        from edecan_premium.twilio_router import router as twilio_router

        from edecan_api.deps import build_key_provider
        from edecan_api.routers import consents

        app.state.get_session = get_session
        app.state.make_vault = lambda session: TokenVault(session, build_key_provider(settings))
        # `app.state.settings` (barrido v7, WP-V7-12): sin esta línea,
        # `edecan_premium.media_streams._media_streams_enabled()` nunca podía
        # leer `TWILIO_MEDIA_STREAMS_ENABLED` como `True` en un despliegue real
        # -- el flag y el código que lo consume ya existían y estaban probados,
        # pero el "cableado" final entre `Settings` y `app.state` faltaba (ver
        # `ARCHITECTURE.md` §15.h, `docs/voz-telefonia.md` "Interrupciones
        # naturales (beta)"). `app.state.phone_agent` sigue sin configurar a
        # propósito (ver el docstring del módulo, arriba): resolver identidad
        # para una llamada entrante sin usuario autenticado es una decisión de
        # producto que no está pinned todavía.
        app.state.settings = settings

        app.include_router(twilio_router)
        # `edecan_api.routers.consents` importa `edecan_premium.compliance` a
        # nivel de módulo (único invocador de `grant_consent`, ver su
        # docstring) — por eso se importa/monta aquí, detrás del mismo guard
        # que `twilio_router`, y no en la lista incondicional de arriba.
        app.include_router(consents.router)
        logger.info(
            "edecan_premium detectado: rutas de telefonía Twilio y consentimiento montadas."
        )

    # v2 (ROADMAP_V2.md §7.6) — montaje defensivo, ver docstring del módulo
    # para el porqué. Prefix/tags de cada router los define su propio WP
    # dueño (§7.6); este archivo no los declara.
    for name in V2_ROUTER_NAMES:
        try:
            mod = importlib.import_module(f"edecan_api.routers.{name}")
        except ImportError:
            logger.warning(
                "router v2 'edecan_api.routers.%s' no disponible todavía "
                "(WP no aterrizado) — se omite, el resto de la API sigue arrancando.",
                name,
            )
            continue
        app.include_router(mod.router)
        logger.info("router v2 'edecan_api.routers.%s' montado.", name)

    # v3 (ARCHITECTURE.md §12) — mismo montaje defensivo que v2, mismo motivo.
    for name in V3_ROUTER_NAMES:
        try:
            mod = importlib.import_module(f"edecan_api.routers.{name}")
        except ImportError:
            logger.warning(
                "router v3 'edecan_api.routers.%s' no disponible todavía "
                "(WP no aterrizado) — se omite, el resto de la API sigue arrancando.",
                name,
            )
            continue
        app.include_router(mod.router)
        logger.info("router v3 'edecan_api.routers.%s' montado.", name)

    # v4 (ARCHITECTURE.md §13) — mismo montaje defensivo que v2/v3, mismo motivo.
    for name in V4_ROUTER_NAMES:
        try:
            mod = importlib.import_module(f"edecan_api.routers.{name}")
        except ImportError:
            logger.warning(
                "router v4 'edecan_api.routers.%s' no disponible todavía "
                "(WP no aterrizado) — se omite, el resto de la API sigue arrancando.",
                name,
            )
            continue
        app.include_router(mod.router)
        logger.info("router v4 'edecan_api.routers.%s' montado.", name)

    # v5 (ARCHITECTURE.md §14) — mismo montaje defensivo que v2/v3/v4, mismo motivo.
    for name in V5_ROUTER_NAMES:
        try:
            mod = importlib.import_module(f"edecan_api.routers.{name}")
        except ImportError:
            logger.warning(
                "router v5 'edecan_api.routers.%s' no disponible todavía "
                "(WP no aterrizado) — se omite, el resto de la API sigue arrancando.",
                name,
            )
            continue
        app.include_router(mod.router)
        logger.info("router v5 'edecan_api.routers.%s' montado.", name)

    # v6 (ARCHITECTURE.md §15) — mismo montaje defensivo que v2/v3/v4/v5, mismo motivo.
    for name in V6_ROUTER_NAMES:
        try:
            mod = importlib.import_module(f"edecan_api.routers.{name}")
        except ImportError:
            logger.warning(
                "router v6 'edecan_api.routers.%s' no disponible todavía "
                "(WP no aterrizado) — se omite, el resto de la API sigue arrancando.",
                name,
            )
            continue
        app.include_router(mod.router)
        logger.info("router v6 'edecan_api.routers.%s' montado.", name)

    # v3 (ARCHITECTURE.md §12, runner local WP-V3-05): si hay una carpeta de
    # export estático de apps/web configurada, la sirve en "/" — SIEMPRE al
    # final, después de todos los routers de arriba, para no tapar /v1/* ni
    # /healthz (Starlette resuelve rutas en orden de registro; un `Mount("/")`
    # solo captura lo que ningún path operation explícito ya reclamó).
    if settings.SERVE_WEB_DIR:
        web_dir = Path(settings.SERVE_WEB_DIR).expanduser()
        if web_dir.is_dir():
            app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")
            logger.info("SERVE_WEB_DIR detectado: sirviendo %s en '/'.", web_dir)
        else:
            logger.warning(
                "SERVE_WEB_DIR=%s no existe o no es una carpeta — no se sirve nada en '/'.",
                settings.SERVE_WEB_DIR,
            )

    return app


app = create_app()
