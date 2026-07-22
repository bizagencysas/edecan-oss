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
- Monta siempre los routers OSS `phone` (`/v1/phone/*`) y `consents`
  (`POST /v1/consents`): llamadas entrantes/salientes, confirmación humana,
  webhooks firmados y consentimiento no dependen de paquetes privados.
- Si `edecan_premium` está instalado (`importlib.util.find_spec`), monta además
  su `twilio_router` legado para SMS/campañas y Media Streams. Ese router espera
  un contrato en `app.state` (documentado
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
  El router OSS `consents` usa `edecan_api.deps` porque autentica al usuario
  del tenant vía JWT normal, no mediante firma Twilio.
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
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import redis.asyncio as redis_asyncio
from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Scope

from edecan_api import __version__
from edecan_api.companion_manager import ConnectionManager
from edecan_api.config import Settings, get_settings
from edecan_api.deps import get_platform_session, get_redis
from edecan_api.routers import (
    admin,
    auth,
    billing,
    companion,
    connectors,
    consents,
    contacts,
    content_studio,
    conversations,
    files,
    finance,
    me,
    memory,
    persona,
    phone,
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


# El export de Next no puede emitir headers HTTP por sí mismo. En desktop lo
# sirve este proceso desde el mismo origen que la API, por lo que `connect-src
# 'self'` alcanza para las llamadas normales; Tauri IPC conserva únicamente
# sus dos transportes internos. `unsafe-inline` se limita a script/style por
# el bootstrap estático de Next y THEME_INIT_SCRIPT; producción nunca habilita
# `unsafe-eval` ni comodines de red.
DESKTOP_WEB_SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": "; ".join(
        (
            "default-src 'self'",
            "base-uri 'self'",
            "object-src 'none'",
            # Studio y los previews de archivos usan exclusivamente Blob URLs
            # creadas tras una descarga autenticada. Cada iframe se monta sin
            # permisos y recibe además una CSP interna sin scripts ni red.
            "frame-src blob:",
            "frame-ancestors 'none'",
            "form-action 'self'",
            "script-src 'self' 'unsafe-inline'",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: blob:",
            "font-src 'self' data:",
            "media-src 'self' data: blob:",
            "worker-src 'self' blob:",
            "connect-src 'self' ipc: http://ipc.localhost",
            "manifest-src 'self'",
        )
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": (
        "camera=(), geolocation=(), microphone=(self), payment=(), usb=()"
    ),
}

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

# El desktop local puede publicarse detrás de Cloudflare Tunnel. Un header
# Authorization cualquiera no debe convertir `/register` o `/login` en rutas
# públicas: esos endpoints ignoran el bearer porque reciben credenciales en el
# body. Se bloquean antes de aplicar la regla general de sesión autenticada.
_TUNNEL_LOCAL_ONLY_PATHS = frozenset(
    {
        "/v1/auth/local",
        "/v1/auth/login",
        "/v1/auth/register",
    }
)
_TUNNEL_UNAUTHENTICATED_PATHS = frozenset(
    {
        "/healthz",
        "/readyz",
        "/v1/auth/logout",
        "/v1/auth/refresh",
        "/v1/devices/pairing/claim",
        "/v1/devices/pairing/refresh",
    }
)


class LocalTunnelGuardMiddleware(BaseHTTPMiddleware):
    """Frontera del backend personal cuando entra desde Cloudflare.

    En LAN conserva la experiencia completa. Desde Internet permite el canje
    de un QR aleatorio, la renovación mediante secretos durables y las rutas
    `/v1/*` que ya llevan Bearer. La UI, registro, login y setup manual nunca
    se publican. Solo se activa en `EDECAN_LOCAL_MODE`; una instalación hosted
    multiusuario detrás de Cloudflare mantiene su política normal.
    """

    def __init__(self, app: ASGIApp, *, enabled: bool) -> None:
        super().__init__(app)
        self.enabled = enabled

    @staticmethod
    def _comes_from_cloudflare(request: Request) -> bool:
        return bool(request.headers.get("CF-Ray") or request.headers.get("CF-Connecting-IP"))

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not self.enabled or not self._comes_from_cloudflare(request):
            return await call_next(request)

        path = request.url.path.rstrip("/") or "/"
        if request.method == "OPTIONS" or path in _TUNNEL_UNAUTHENTICATED_PATHS:
            return await call_next(request)

        if (
            path in _TUNNEL_LOCAL_ONLY_PATHS
            or path.startswith("/v1/setup")
            or not path.startswith("/v1/")
        ):
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "Esta función solo está disponible desde tu computador."},
            )

        authorization = request.headers.get("Authorization", "")
        if not authorization.startswith("Bearer ") or not authorization[7:].strip():
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Este dispositivo no está conectado con Edecán."},
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)


class SecureStaticFiles(StaticFiles):
    """Sirve el export de escritorio con la frontera HTTP que Next pierde al
    compilar con ``output: export``.

    Los headers se aplican también a JS/CSS/imágenes: ``nosniff`` es útil en
    esos recursos y mantener una política uniforme evita respuestas estáticas
    desprotegidas por extensiones o fallbacks de ``StaticFiles``.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        for name, value in DESKTOP_WEB_SECURITY_HEADERS.items():
            response.headers[name] = value
        return response


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Agrega `X-Request-ID` (reusa el del cliente si ya trae uno) y registra
    una línea de log estructurada por request — sin cuerpo, headers ni query
    string, para no filtrar PII (ARCHITECTURE.md §10.12: "logging estructurado
    sin PII")."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        supplied_request_id = request.headers.get("X-Request-ID", "")
        request_id = (
            supplied_request_id
            if _REQUEST_ID_RE.fullmatch(supplied_request_id)
            else str(uuid.uuid4())
        )
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
    app.add_middleware(LocalTunnelGuardMiddleware, enabled=settings.EDECAN_LOCAL_MODE)
    app.add_middleware(RequestContextMiddleware)

    app.state.companion_manager = ConnectionManager()
    # Telefonía OSS: los webhooks no tienen JWT, por eso resuelven tenant por
    # número/Call SID y abren su propia sesión después de validar la firma.
    from edecan_db.session import get_session
    from edecan_db.vault import TokenVault

    from edecan_api.deps import build_key_provider

    app.state.get_session = get_session
    app.state.make_vault = lambda session: TokenVault(session, build_key_provider(settings))
    app.state.settings = settings

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz(
        session: AsyncSession = Depends(get_platform_session),
        redis_client: redis_asyncio.Redis = Depends(get_redis),
    ) -> Response:
        """Readiness real: la API solo recibe tráfico si DB y Redis responden."""
        try:
            await session.execute(text("SELECT 1"))
            await redis_client.ping()
        except Exception:
            logger.exception("readiness_check_failed")
            return Response(
                content='{"status":"unavailable"}',
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                media_type="application/json",
            )
        return Response(content='{"status":"ok"}', media_type="application/json")

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
    app.include_router(consents.router)
    app.include_router(content_studio.router)
    app.include_router(phone.router)
    app.include_router(companion.router)
    app.include_router(usage.router)
    app.include_router(admin.router)
    app.include_router(billing.router)

    if importlib.util.find_spec("edecan_premium") is not None:
        from edecan_premium.twilio_router import router as twilio_router
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
        app.include_router(twilio_router)
        logger.info(
            "edecan_premium detectado: rutas heredadas de telefonía Twilio montadas."
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
        # `devices.pairing_router` contiene claim/refresh móviles que aún no
        # tienen JWT y por eso no pueden heredar el rate-limit autenticado del
        # CRUD `/v1/devices`. Se monta junto a su router dueño, manteniendo el
        # patrón defensivo de v4 y sin sumar un módulo artificial al catálogo.
        extra_router = getattr(mod, "pairing_router", None)
        if extra_router is not None:
            app.include_router(extra_router)
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
            app.mount("/", SecureStaticFiles(directory=str(web_dir), html=True), name="web")
            logger.info("SERVE_WEB_DIR detectado: sirviendo %s en '/'.", web_dir)
        else:
            logger.warning(
                "SERVE_WEB_DIR=%s no existe o no es una carpeta — no se sirve nada en '/'.",
                settings.SERVE_WEB_DIR,
            )

    return app


app = create_app()
