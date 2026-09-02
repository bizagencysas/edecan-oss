"""Orquestador del runner local — comando `edecan` (`ARCHITECTURE.md`
§12f, dueño WP-V3-05): Postgres embebido (`edecan_local.pg`) + migraciones
(`edecan_local.migrate`) + object store S3-compatible (`edecan_local.
objectstore`) + `edecan_api` (uvicorn) + worker in-process
(`edecan_local.worker_loop`), todo en el MISMO proceso. El bind es loopback
por defecto; la app nativa activa acceso LAN solo para la API móvil.

## Orden de arranque (todo dentro de `run()`)

1. `data_dir` expandido y creado con permisos `0700`.
2. Manejadores de señales instalados YA (`SIGTERM`/`SIGINT` → `stop_event`) —
   antes de cualquier paso lento, para que un Ctrl+C durante el arranque
   deje el proceso en un estado limpio en vez de colgado.
3. `ensure_postgres(data_dir)` — Postgres embebido o `EDECAN_DATABASE_URL`
   (modo avanzado).
4. Secretos locales (`JWT_SECRET`/`LOCAL_MASTER_KEY`) generados/leídos de
   `data_dir` — ver `_ensure_local_secrets`.
5. **Variables de entorno fijadas ANTES de importar `edecan_api`** (§12g):
   `EDECAN_LOCAL_MODE`, `DATABASE_URL`, `REDIS_URL=memory://`,
   `QUEUE_PROVIDER=db`, `AWS_ENDPOINT_URL`/`AWS_ACCESS_KEY_ID`/
   `AWS_SECRET_ACCESS_KEY`/`S3_BUCKET` (object store local), `DATA_DIR`,
   `LOCAL_API_PORT`, `SERVE_WEB_DIR` (si aplica), `JWT_SECRET`,
   `LOCAL_MASTER_KEY` — y `SQS_QUEUE_URL` se BORRA explícitamente (nunca
   hereda un valor de un `.env`/shell de otro contexto). Esto es crítico:
   `edecan_api.main` construye `app = create_app()` a nivel de MÓDULO, que
   lee `get_settings()` (cacheado con `lru_cache`) la primera vez que algo
   importa ese módulo en el proceso — si el entorno no está listo ANTES de
   ese primer import, la app queda armada con la configuración por defecto
   equivocada para siempre (hasta reiniciar el proceso).
6. `run_migrations` (`edecan_local.migrate`, en `asyncio.to_thread`: es
   síncrona).
7. Se crean las apps ASGI (`edecan_api.main.app`, `edecan_local.
   objectstore.create_object_store_app`) y los `Deps` del worker
   (`edecan_local.worker_loop.build_local_deps`).
8. `edecan_local.ollama_supervisor.maybe_start_ollama(api_settings)` — Ollama
   embebido OPCIONAL (WP-V4-09, `asyncio.to_thread`: es síncrona igual que
   `pgserver.get_server`), de "mejor esfuerzo": `None` sin efecto alguno si
   `EDECAN_OLLAMA_AUTOSTART` está apagada, si no hay binario, o si nunca
   llega a responder — nunca bloquea el resto del arranque.
9. Tres tareas concurrentes: servidor uvicorn de la API (`127.0.0.1:port`),
   servidor uvicorn del object store (`127.0.0.1:port+2`), y el loop del
   worker (`edecan_local.worker_loop.run_forever`) -- las tres pasan por
   `_run_background` (ver su docstring): normaliza cualquier `SystemExit`/
   `KeyboardInterrupt` crudo (p. ej. `uvicorn.Server.startup()` sobre un
   puerto ocupado hace `sys.exit(...)`) a una excepción común ANTES de que
   `asyncio.Task` la vea, para que el apagado de más abajo pueda capturarla
   siempre de forma prolija en vez de que se escape de la máquina del event
   loop.
10. Poll de `GET /healthz` (hasta 30 intentos × 0.5s) — si alguna de las tres
    tareas ya terminó (crasheó) antes de que responda, se corta la espera de
    inmediato con el error real en vez de agotar el timeout completo.
11. Imprime la línea EXACTA `EDECAN_LOCAL_READY port=<puerto>` en stdout
    (`ARCHITECTURE.md` §12f: el proceso Tauri que lanza esto como subproceso
    hace polling de esa línea).
12. Espera a que `stop_event` se marque (señal recibida) y apaga todo:
    `should_exit = True` en ambos servidores uvicorn, se espera a que las
    tres tareas terminen, después `ollama_handle.stop()` (si se llegó a
    arrancar) y por último `pg_handle.cleanup()` (Postgres se apaga AL
    FINAL — fue lo primero que arrancó; orden de apagado inverso al de
    arranque).

## Por qué NO se deja que cada `uvicorn.Server` instale sus propios manejadores de señales

Este proceso corre DOS servidores uvicorn (API + object store) en el mismo
loop; si cada uno instalara su propio `signal.signal(SIGTERM/SIGINT, ...)`
(lo que hace `uvicorn.Server.serve()` por defecto), se pisarían entre sí de
forma no determinista. `_make_server` usa una subclase que anula
`capture_signals()` a un no-op — este módulo centraliza el manejo de
señales UNA sola vez (`_install_signal_handlers`, mismo patrón que
`edecan_worker.main._amain`) y apaga cada servidor marcando
`server.should_exit = True` explícitamente.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import importlib.metadata
import json
import logging
import os
import re
import secrets as secrets_module
import signal
import socket
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from edecan_core.platform_paths import DEFAULT_DATA_DIR

logger = logging.getLogger(__name__)

# `DEFAULT_PORT` sigue duplicado a propósito de `ARCHITECTURE.md` §12g
# (`LOCAL_API_PORT`): este módulo NO puede importar `edecan_api.config` antes
# de terminar de fijar el entorno (ver docstring), así que no puede leer ese
# default desde ahí -- es el mismo valor, declarado acá también para que
# `parse_args` tenga algo que mostrar sin ese import. `DEFAULT_DATA_DIR` NO
# tiene ese problema (viene de `edecan_core.platform_paths`, un módulo puro
# sin `lru_cache`/settings que fijar antes) y por eso ahora es una única
# fuente de verdad en vez de un literal repetido (docs/edecan-windows.md §2).
DEFAULT_PORT = 8765

OBJECTSTORE_PORT_OFFSET = 2
S3_BUCKET_NAME = "edecan-files"

HEALTHZ_MAX_ATTEMPTS = 30
HEALTHZ_INTERVAL_SECONDS = 0.5

_SECRETS_FILENAME = "secrets.json"
_DATA_DIR_MODE = 0o700
_PLATFORM_CONFIG_FILENAME = "platform-config.json"
_PLATFORM_CONFIG_KEYS = frozenset(
    {
        "CLOUDFLARE_ACCOUNT_ID",
        "CLOUDFLARE_API_TOKEN",
        "WORKERS_AI_CHAT_MODEL",
        "WORKERS_AI_TIMEOUT_SECONDS",
        # Modelo del ESCRITOR de posts (alias "profundo"). Tiene default en el
        # binario (GLM 5.2), pero se expone acá para poder cambiarlo sin recompilar.
        "WORKERS_AI_MODEL_PROFUNDO",
        # Switch de proveedor de inferencia (PHASE2/público): `workers_ai`
        # (default) | `azure_openai` | `openai_compat`. Cambiar esta clave en
        # platform-config.json mueve la inferencia sin recompilar; borrarla
        # restaura Workers AI. Las claves Azure/openai_compat de abajo son las
        # credenciales que consume `edecan_llm.router.build_provider_from_settings`.
        "LLM_PROVIDER",
        "AZURE_AI_FOUNDRY_ENDPOINT",
        "AZURE_AI_FOUNDRY_API_KEY",
        "AZURE_AI_FOUNDRY_TEXT_DEPLOYMENT",
        "AZURE_AI_FOUNDRY_TEXT_DEPLOYMENTS",
        "OPENAI_COMPAT_BASE_URL",
        "OPENAI_COMPAT_API_KEY",
        "CLAUDE_CLI_PATH",
        "CODEX_CLI_PATH",
        "OLLAMA_BASE_URL",
        "LLM_CLI_TIMEOUT_SECONDS",
        # Imágenes de posts con gpt-image-2 (director de arte, portado de REFERENCIA).
        # SIN estas claves en el allowlist, el `IMAGES_API_KEY` del platform-config
        # nunca llegaba al worker y el post salía SIN imagen (bug real: la card
        # llegaba solo con texto). `IMAGES_API_KEY` no tiene default -> es obligatoria.
        "IMAGES_PROVIDER",
        # Voz: el webhook post-call de ElevenLabs ConvAI fallaba 401 en cada
        # entrega porque este allowlist NUNCA pasaba el secret al sidecar
        # (secret vacío -> "Firma inválida" -> auto-disabled por fallos
        # repetidos). La API key mantiene el TTS (coach gym + voz del chat)
        # aunque cambie el modo del proceso.
        "ELEVENLABS_API_KEY",
        "ELEVENLABS_VOICE_ID",
        "IMAGES_BASE_URL",
        "IMAGES_API_KEY",
        "IMAGES_MODEL",
        # fydesign: selector de pantallas + visuales para publicaciones de una
        # organización configurada. `FYDESIGN_DIR`
        # lanza fydesign como subprocess on-demand (sin servidor, cero calor en
        # idle). Sin `FYDESIGN_DIR` cae a `FYDESIGN_URL` (servidor :3000).
        "FYDESIGN_DIR",
        "FYDESIGN_URL",
        "EDECAN_API_KEY",
        # Publicación multi-red: credenciales de Instagram/Facebook (Meta),
        # Threads y X. Se leen con `os.getenv` (mismo patrón que
        # `ORGANIZATION_LINKEDIN_ACCESS_TOKEN`). No existe fallback a archivos
        # privados de otra instalación.
        # Solo las que se configuren habilitan esa red; las que faltan se
        # saltan con error de configuración (no rompe el resto).
        "ORGANIZATION_META_ACCESS_TOKEN",
        "ORGANIZATION_META_PAGE_ID",
        "ORGANIZATION_META_IG_ID",
        "ORGANIZATION_THREADS_ACCESS_TOKEN",
        "ORGANIZATION_THREADS_USER_ID",
        "ORGANIZATION_X_OAUTH2_ACCESS_TOKEN",
        "ORGANIZATION_X_OAUTH2_REFRESH_TOKEN",
        "ORGANIZATION_X_CLIENT_ID",
        "ORGANIZATION_X_CLIENT_SECRET",
        "ORGANIZATION_X_CONSUMER_KEY",
        "ORGANIZATION_X_CONSUMER_SECRET",
        "ORGANIZATION_X_ACCESS_TOKEN",
        "ORGANIZATION_X_ACCESS_TOKEN_SECRET",
        "ORGANIZATION_LINKEDIN_ACCESS_TOKEN",
        "ORGANIZATION_LINKEDIN_ORG_URN",
        # URL pública base para generar URLs firmadas de archivos (IG/FB/Threads
        # necesitan URLs públicas para descargar imagen/video). Normalmente es
        # el túnel (ej: https://edecan.example.com). Si no se configura, se cae
        # a PUBLIC_BASE_URL (que en modo local es la IP de la LAN).
        "ORGANIZATION_PUBLIC_BASE_URL",
        # Frenos de telefonía. Ambos vienen ENCENDIDOS y solo un "no" explícito los apaga
        # (ver `edecan_voice.tools._requiere_confirmacion_de_llamada` y
        # `edecan_api.routers.phone._requiere_consentimiento_del_destinatario`): una
        # instalación nueva nunca marca un teléfono real sin frenos por accidente. Viven acá
        # y no en el binario para que el dueño pueda decidir sin recompilar nada.
        "EDECAN_PHONE_REQUIRE_CONFIRMATION",
        "EDECAN_PHONE_REQUIRE_RECIPIENT_CONSENT",
        # Huso (nombre IANA, "America/Bogota") en el que se siembran las horas
        # de las automatizaciones de LinkedIn (`edecan_local.
        # linkedin_automations_seed`). Sin esta clave, esas 5 filas nacen en
        # UTC y un "09:00" dispara a las 4:00 a.m. en Bogotá. Vive acá y no en
        # el binario porque la zona es de la INSTALACIÓN, no del producto: un
        # literal en el código convertiría el repo OSS en el asistente de una
        # sola persona.
        "EDECAN_TIMEZONE",
        # Base por la que TWILIO entra a este backend. Normalmente se detecta
        # sola leyendo la config de cloudflared (`_tunnel_base_url_from_
        # cloudflared`); esta clave es el override manual para cuando el túnel
        # lo administra otra herramienta o el hostname no se puede deducir.
        # Lo que se ponga acá GANA sobre lo autodetectado (ver `_apply_env`).
        "PHONE_WEBHOOK_BASE_URL",
    }
)


# ---------------------------------------------------------------------------
# CLI (ARCHITECTURE.md §12f: --port / --data-dir / --no-web)
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="edecan",
        description=(
            "Runner local de Edecán: api + worker + Postgres embebido + "
            "object store. La app nativa lo administra automáticamente."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Puerto de la API (default {DEFAULT_PORT}, override de LOCAL_API_PORT).",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default=DEFAULT_DATA_DIR,
        help=f"Carpeta de datos (default {DEFAULT_DATA_DIR}, override de DATA_DIR).",
    )
    parser.add_argument(
        "--no-web",
        action="store_true",
        help="No sirve el export estático de apps/web aunque esté disponible (dev).",
    )
    parser.add_argument(
        "--mobile-access",
        action="store_true",
        help=(
            "Permite que las apps móviles de la red local se conecten. "
            "La app nativa de Edecán lo activa automáticamente."
        ),
    )
    parser.add_argument(
        "--macos-permission-status",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--macos-capture-check",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--macos-capture-output",
        type=str,
        default=None,
        help=argparse.SUPPRESS,
    )
    return parser.parse_args(argv)


def _macos_permission_status() -> dict[str, bool]:
    """Consulta TCC desde el motor que realmente captura y controla la Mac."""

    if sys.platform != "darwin":
        return {"screen_recording": True, "accessibility": True}

    import ctypes

    core_graphics = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    core_graphics.CGPreflightScreenCaptureAccess.argtypes = []
    core_graphics.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool

    application_services = ctypes.CDLL(
        "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
    )
    application_services.AXIsProcessTrusted.argtypes = []
    application_services.AXIsProcessTrusted.restype = ctypes.c_bool

    return {
        "screen_recording": bool(core_graphics.CGPreflightScreenCaptureAccess()),
        "accessibility": bool(application_services.AXIsProcessTrusted()),
    }


def _macos_capture_check(output_path: str | None = None) -> dict[str, Any]:
    """Ejecuta una captura real y opcionalmente guarda evidencia local de QA."""

    from edecan_companion.actions import _screenshot
    from edecan_companion.config import CompanionConfig

    result = _screenshot(
        {"format": "webp", "quality": 55, "max_width": 640},
        CompanionConfig(sandbox_dir=Path.home()),
    )
    saved_path: str | None = None
    if output_path:
        destination = Path(output_path).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(base64.b64decode(str(result["image_b64"])))
        saved_path = str(destination)
    return {
        "ok": True,
        "width": int(result["width"]),
        "height": int(result["height"]),
        "mime": str(result["mime"]),
        **({"output": saved_path} if saved_path else {}),
    }


# ---------------------------------------------------------------------------
# Secretos locales: JWT_SECRET / LOCAL_MASTER_KEY
# ---------------------------------------------------------------------------


def _ensure_local_secrets(data_dir: Path) -> dict[str, str]:
    """Genera (la primera vez) y persiste `JWT_SECRET`/`LOCAL_MASTER_KEY` en
    `data_dir/secrets.json` (permisos `0600`); en arranques siguientes
    reutiliza el mismo valor.

    Ninguno de los dos aparece en la lista de env vars que fija
    `ARCHITECTURE.md` §12f/§12g de forma explícita — esa sección asume una
    plataforma hospedada, con un operador humano que ya puso un valor real
    en su `.env` (`docs/self-hosting.md` §2). La app de escritorio no tiene
    ningún operador que haga eso, y dejar el placeholder público de
    `edecan_api.config` (`"TU_LOCAL_MASTER_KEY_FERNET_AQUI"`) NO es solo
    "inseguro": `edecan_db.vault.LocalKeyProvider.__init__` construye un
    `cryptography.fernet.Fernet(LOCAL_MASTER_KEY)` DE FORMA EAGER en cada
    request que use el vault (`GET/PUT /v1/credentials`, `GET
    /v1/setup/status`, conectores...) — ese placeholder no decodifica a 32
    bytes válidos, así que CADA una de esas rutas devolvería 500 siempre,
    rompiendo por completo el flujo bring-your-own que es el corazón de v3
    (`DIRECCION_ACTUAL.md` "Modelo de credenciales"). Por eso este runner
    genera un valor real la primera vez.

    Persistir (en vez de generar uno nuevo en cada arranque) es OBLIGATORIO,
    no una comodidad: perder `LOCAL_MASTER_KEY` entre reinicios dejaría
    ilegibles para siempre las credenciales ya guardadas en el vault de ese
    tenant (cifrado envolvente, ARCHITECTURE.md §10.4) — y perder
    `JWT_SECRET` cerraría la sesión de todo el mundo en cada arranque.

    Respeta `JWT_SECRET`/`LOCAL_MASTER_KEY` si YA vienen fijados en el
    entorno (usuario avanzado, o la app Tauri que ya los trae generados) —
    ver `_apply_env`, que usa `setdefault` para estos dos.
    """
    secrets_path = data_dir / _SECRETS_FILENAME
    if secrets_path.is_file():
        try:
            data = json.loads(secrets_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("JWT_SECRET") and data.get("LOCAL_MASTER_KEY"):
                return {
                    "JWT_SECRET": data["JWT_SECRET"],
                    "LOCAL_MASTER_KEY": data["LOCAL_MASTER_KEY"],
                }
        except (OSError, ValueError):
            logger.warning(
                "No se pudo leer %s -- se generan secretos nuevos (las credenciales "
                "ya guardadas en el vault, si las hay, quedarán ilegibles).",
                secrets_path,
                exc_info=True,
            )

    from cryptography.fernet import Fernet

    generated = {
        "JWT_SECRET": secrets_module.token_urlsafe(32),
        "LOCAL_MASTER_KEY": Fernet.generate_key().decode("ascii"),
    }
    secrets_path.write_text(json.dumps(generated), encoding="utf-8")
    try:
        os.chmod(secrets_path, 0o600)
    except OSError:
        # En NTFS `chmod` es, en el mejor caso, un no-op parcial (solo el bit
        # de solo-lectura) y en el peor puede fallar si un antivirus/backup
        # tiene el archivo recién escrito abierto un instante (mismo motivo
        # que `docs/edecan-windows.md` documenta reintentos para archivos
        # bloqueados) -- nunca debe tirar abajo el arranque por esto: el
        # perfil de NTFS del usuario ya es la protección real en Windows.
        logger.warning("No se pudo aplicar permisos 0600 a %s.", secrets_path, exc_info=True)
    logger.info("Secretos locales generados por primera vez en %s.", secrets_path)
    return generated


def _load_local_platform_config(data_dir: Path) -> dict[str, str]:
    """Carga configuración privada del host sin interpretar sintaxis de shell.

    Una app abierta desde Finder/Explorer no hereda el ``.env`` del checkout
    desde el que fue compilada. Empaquetarlo filtraría secretos al artefacto,
    así que la instalación usa un archivo JSON local, fuera del bundle. Solo
    se aceptan las claves explícitas de la allowlist y nunca se registran sus
    valores.
    """

    config_path = data_dir / _PLATFORM_CONFIG_FILENAME
    if not config_path.is_file():
        return {}
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        logger.warning("Configuración privada inválida en %s; se ignora.", config_path)
        return {}
    if not isinstance(raw, dict):
        logger.warning("Configuración privada en %s no es un objeto JSON; se ignora.", config_path)
        return {}

    config: dict[str, str] = {}
    for key in _PLATFORM_CONFIG_KEYS:
        value = raw.get(key)
        if isinstance(value, (str, int, float)) and str(value).strip():
            config[key] = str(value).strip()
    return config


def _apply_local_platform_config(data_dir: Path) -> None:
    """Aplica la configuración privada sin pisar overrides del proceso."""

    for key, value in _load_local_platform_config(data_dir).items():
        os.environ.setdefault(key, value)


# ---------------------------------------------------------------------------
# SERVE_WEB_DIR
# ---------------------------------------------------------------------------


def _resolve_serve_web_dir(*, no_web: bool) -> str | None:
    """`None` si `--no-web` (útil en dev, cuando `apps/web` corre aparte con
    `npm run dev`, ARCHITECTURE.md §12f). Si no: `EDECAN_WEB_DIR` (env,
    escotilla de escape explícita) o `<bundle>/web` si el proceso corre
    congelado con PyInstaller (`sys._MEIPASS`, WP-V3-06 empaqueta el export
    estático de `apps/web` ahí). Sin ninguno de los dos, `None` — mismo
    default que `Settings.SERVE_WEB_DIR` (§12g), `edecan_api.main.
    create_app()` ya no monta nada en "/" en ese caso.
    """
    if no_web:
        return None
    env_dir = os.environ.get("EDECAN_WEB_DIR")
    if env_dir:
        return env_dir
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate = Path(meipass) / "web"
        if candidate.is_dir():
            return str(candidate)
    return None


# ---------------------------------------------------------------------------
# Entorno (ARCHITECTURE.md §12g) -- `_build_env` es PURA (no toca
# `os.environ`) para poder testear su contenido sin depender del estado
# global del proceso; `_apply_env` es el único punto que sí lo muta.
# ---------------------------------------------------------------------------


def _build_env(
    *,
    data_dir: Path,
    port: int,
    objectstore_port: int,
    database_url: str,
    serve_web_dir: str | None,
    local_secrets: dict[str, str],
    public_base_url: str | None = None,
    phone_webhook_base_url: str | None = None,
) -> dict[str, str]:
    env = {
        "EDECAN_LOCAL_MODE": "1",
        # Encargos largos de los bots: presupuesto de pasos del Orchestrator
        # generoso para que un trabajo multi-paso "no pare hasta terminar".
        "MISSIONS_MAX_STEPS": "30",
        "DATABASE_URL": database_url,
        "REDIS_URL": "memory://",
        "QUEUE_PROVIDER": "db",
        "AWS_ENDPOINT_URL": f"http://127.0.0.1:{objectstore_port}",
        "AWS_ACCESS_KEY_ID": "local",
        "AWS_SECRET_ACCESS_KEY": "local",
        "S3_BUCKET": S3_BUCKET_NAME,
        "DATA_DIR": str(data_dir),
        "LOCAL_API_PORT": str(port),
        "JWT_SECRET": local_secrets["JWT_SECRET"],
        "LOCAL_MASTER_KEY": local_secrets["LOCAL_MASTER_KEY"],
    }
    if serve_web_dir:
        env["SERVE_WEB_DIR"] = serve_web_dir
    # Repo local para `acceder_codigo_local` (el bot trabaja en el proyecto
    # como en el IDE/Cursor): lee/escribe archivos del repo, corre comandos
    # con cwd en la raíz y hace commits locales, con jaula de rutas. Se apunta
    # al clon del dueño si existe; si no, la tool queda desactivada (gate de
    # `_raiz`) sin romper nada — es una ruta de filesystem, no un secreto.
    repo_candidato = Path.home() / "Edecan-Nuevo" / "edecan"
    if repo_candidato.is_dir():
        env["EDECAN_LOCAL_REPO_PATH"] = str(repo_candidato)
    if public_base_url:
        env["PUBLIC_BASE_URL"] = public_base_url
    if phone_webhook_base_url:
        env["PHONE_WEBHOOK_BASE_URL"] = phone_webhook_base_url
    return env


_REMOTE_ACCESS_CONFIG_FILE = "remote-access.json"
_MAX_REMOTE_ACCESS_CONFIG_BYTES = 4_096


def _remote_access_url_from_file(data_dir: Path | None) -> str | None:
    """Lee la URL pública provisionada por desktop/Relay sin leer secretos.

    El token del túnel nunca vive aquí ni entra al QR. El archivo solo contiene
    la URL HTTPS que los teléfonos deben usar y puede persistir entre reinicios.
    """
    if data_dir is None:
        return None
    path = data_dir / _REMOTE_ACCESS_CONFIG_FILE
    try:
        if not path.is_file() or path.stat().st_size > _MAX_REMOTE_ACCESS_CONFIG_BYTES:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        logger.warning("Configuración de acceso remoto inválida en %s; se ignora.", path)
        return None
    value = payload.get("public_url") if isinstance(payload, dict) else None
    if not isinstance(value, str):
        return None
    value = value.strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
    except ValueError:
        logger.warning("public_url de acceso remoto no es una URL HTTPS segura; se ignora.")
        return None
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        logger.warning("public_url de acceso remoto no es una URL HTTPS segura; se ignora.")
        return None
    return value


# ---------------------------------------------------------------------------
# Base pública de los webhooks de Twilio (PHONE_WEBHOOK_BASE_URL)
# ---------------------------------------------------------------------------

_TUNNEL_CONFIG_FILENAMES = ("cloudflare-tunnel.yml", "cloudflare-tunnel.yaml")
_MAX_TUNNEL_CONFIG_BYTES = 64_000
# Hostname DNS normal: sin comodines (`*.ejemplo.com` no identifica un origen),
# sin esquema, sin ruta y con al menos un punto.
_TUNNEL_HOSTNAME_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+$"
)
_TUNNEL_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0.0.0.0"})


def _cloudflared_config_candidates(data_dir: Path | None) -> list[Path]:
    """Dónde puede estar la config del túnel, de lo más específico a lo genérico.

    La app de escritorio deja `cloudflare-tunnel.yml` junto a DATA_DIR (mismo
    sitio que `cloudflare-tunnel.log`); un túnel montado a mano por el dueño
    normalmente vive en `~/.cloudflared/config.yml`. Ninguno de los dos es
    obligatorio: si no hay ninguno se sigue sin autodetección.
    """
    candidates: list[Path] = []
    if data_dir is not None:
        for name in _TUNNEL_CONFIG_FILENAMES:
            candidates.append(data_dir / name)
            candidates.append(data_dir.parent / name)
    home = Path.home()
    candidates.append(home / ".cloudflared" / "config.yml")
    candidates.append(home / ".cloudflared" / "config.yaml")
    return candidates


def _servicio_apunta_a_este_backend(service: str, port: int) -> bool:
    """`True` si esa regla de ingress entrega el tráfico a ESTE proceso."""
    try:
        parsed = urlsplit(service.strip())
        return (
            parsed.scheme in {"http", "https"}
            and parsed.port == port
            and (parsed.hostname or "").lower() in _TUNNEL_LOOPBACK_HOSTS
        )
    except ValueError:
        return False


def _tunnel_hostname_for_port(path: Path, port: int) -> str | None:
    """Hostname de la regla de ingress que enruta hacia `port`, si la hay."""
    try:
        if not path.is_file() or path.stat().st_size > _MAX_TUNNEL_CONFIG_BYTES:
            return None
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    try:
        import yaml
    except ImportError:  # pragma: no cover - pyyaml viene con edecan-companion
        return None
    try:
        payload = yaml.safe_load(raw)
    except yaml.YAMLError:
        logger.warning("Config de túnel ilegible en %s; se ignora.", path)
        return None
    ingress = payload.get("ingress") if isinstance(payload, dict) else None
    if not isinstance(ingress, list):
        return None
    for rule in ingress:
        if not isinstance(rule, dict):
            continue
        hostname = rule.get("hostname")
        service = rule.get("service")
        if not isinstance(hostname, str) or not isinstance(service, str):
            continue
        if not _servicio_apunta_a_este_backend(service, port):
            continue
        hostname = hostname.strip().rstrip(".")
        if _TUNNEL_HOSTNAME_RE.match(hostname):
            return hostname
    return None


def _tunnel_base_url_from_cloudflared(data_dir: Path | None, port: int) -> str | None:
    """URL pública que de VERDAD entra a este backend, deducida del túnel.

    `PUBLIC_BASE_URL` puede apuntar legítimamente a una puerta "siempre viva"
    (un Worker que le contesta al teléfono del dueño aunque la computadora esté
    apagada). Esa puerta exige token de dispositivo, y Twilio no tiene ninguno:
    al pedir el TwiML recibe 404, se queda sin guion y reproduce su mensaje
    enlatado en inglés mientras la llamada muere en `queued`. Los webhooks
    tienen que entrar por el túnel, que es lo único que llega a ESTE proceso —
    el único que tiene el estado de la llamada.

    La regla de ingress cuyo `service` apunta a nuestro propio puerto es
    exactamente eso: "el hostname público por el que se llega a mí". Si nada
    cuadra se devuelve `None` y todo sigue como antes (cae en `PUBLIC_BASE_URL`).
    """
    for path in _cloudflared_config_candidates(data_dir):
        hostname = _tunnel_hostname_for_port(path, port)
        if hostname:
            return f"https://{hostname}"
    return None


def _mobile_public_url(port: int, data_dir: Path | None = None) -> str:
    """Origen que se incrusta en el QR móvil.

    Un override explícito cubre dominios/relays propios. En una instalación
    local se prefiere la IP privada que el sistema enruta hacia la LAN: Android
    no garantiza que los clientes HTTP normales resuelvan el hostname mDNS del
    Mac. El nombre `.local` queda como respaldo cuando no hay una interfaz IPv4.
    """
    configured = os.environ.get("EDECAN_MOBILE_PUBLIC_URL", "").strip().rstrip("/")
    if configured:
        return configured

    persisted = _remote_access_url_from_file(data_dir)
    if persisted:
        return persisted

    address = _primary_lan_ipv4()
    if address:
        return f"http://{address}:{port}"

    hostname = socket.gethostname().strip().rstrip(".")
    if hostname and hostname.lower() not in {"localhost", "localhost.localdomain"}:
        if "." not in hostname:
            hostname = f"{hostname}.local"
        try:
            hostname = hostname.encode("idna").decode("ascii")
        except UnicodeError:
            hostname = ""
    if hostname:
        return f"http://{hostname}:{port}"

    return f"http://127.0.0.1:{port}"


def _primary_lan_ipv4() -> str | None:
    """IPv4 elegida por la tabla de rutas sin enviar ningún paquete.

    El destino TEST-NET nunca recibe tráfico: `connect()` sobre UDP solo deja
    que el kernel seleccione la interfaz y permite leer la dirección local.
    Loopback no sirve para un teléfono y se descarta explícitamente.
    """
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_DGRAM)) as sock:
        try:
            sock.connect(("192.0.2.1", 9))
            address = sock.getsockname()[0]
        except OSError:
            return None
    if not address or address.startswith("127.") or address == "0.0.0.0":
        return None
    return address


_ENV_KEYS_QUE_RESPETAN_LO_YA_PUESTO = (
    "JWT_SECRET",
    "LOCAL_MASTER_KEY",
    # Autodetectada del túnel, pero el dueño manda: si la fijó en
    # `platform-config.json` (o en el shell) ese valor no se pisa. Es la
    # escotilla para un túnel que este runner no sabe leer.
    "PHONE_WEBHOOK_BASE_URL",
)


def _apply_env(env_updates: dict[str, str]) -> None:
    """Aplica `env_updates` a `os.environ`. Las claves de
    `_ENV_KEYS_QUE_RESPETAN_LO_YA_PUESTO` con `setdefault` (respetan un valor ya
    puesto desde afuera, ver docstring de `_ensure_local_secrets`); el resto
    SIEMPRE (son propios de este arranque). `SQS_QUEUE_URL` se borra
    explícitamente -- este runner nunca debe heredar un valor de un shell/`.env`
    de otro contexto (ARCHITECTURE.md §12f: "SQS_QUEUE_URL sin definir")."""
    for key, value in env_updates.items():
        if key in _ENV_KEYS_QUE_RESPETAN_LO_YA_PUESTO:
            os.environ.setdefault(key, value)
        else:
            os.environ[key] = value
    os.environ.pop("SQS_QUEUE_URL", None)


# ---------------------------------------------------------------------------
# Señales
# ---------------------------------------------------------------------------


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    """Mismo patrón que `edecan_worker.main._amain`: `SIGTERM`/`SIGINT`
    marcan `stop_event`, nunca un `kill -9` como único camino."""
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            # No disponible en algunas plataformas (p. ej. Windows) -- ver
            # docstring idéntico en `edecan_worker.main`.
            pass


# ---------------------------------------------------------------------------
# uvicorn (ver docstring del módulo: sin manejadores de señales propios)
# ---------------------------------------------------------------------------


def _make_server(app: Any, *, host: str, port: int) -> Any:
    import uvicorn

    class _NoSignalServer(uvicorn.Server):
        @contextlib.contextmanager
        def capture_signals(self):  # noqa: ANN202 - firma exacta de uvicorn.Server
            yield

    config = uvicorn.Config(app, host=host, port=port, log_level="info", log_config=None)
    return _NoSignalServer(config)


# ---------------------------------------------------------------------------
# Import perezoso de edecan_api -- SIEMPRE después de `_apply_env` (ver
# docstring del módulo, punto 5).
# ---------------------------------------------------------------------------


def _import_api_app() -> Any:
    import edecan_api.main as edecan_api_main

    return edecan_api_main.app


def _import_api_settings() -> Any:
    from edecan_api.config import get_settings

    return get_settings()


# ---------------------------------------------------------------------------
# Espera a /healthz -- corta apenas alguna tarea de servicio ya terminó sola
# (crash temprano, p. ej. puerto ocupado) en vez de agotar el timeout entero.
# También corta (sin lanzar) si `stop_event` se marca ANTES de que el
# arranque termine (p. ej. Ctrl+C durante el arranque): en ese caso, un
# `Deps`/task de servicio que responde a `stop_event` y termina LIMPIO no es
# un crash, es exactamente lo que tiene que pasar -- `run()` se entera
# revisando `stop_event.is_set()` después de este `return` y se salta el
# "EDECAN_LOCAL_READY" (nunca llegó a estar listo).
# ---------------------------------------------------------------------------


async def _run_background(coro: Any, *, label: str) -> None:
    """Ejecuta `coro` normalizando cualquier `BaseException` que NO sea
    `asyncio.CancelledError` ni una `Exception` "normal" a una `RuntimeError`
    -- ANTES de que la máquina de `asyncio.Task` la vea. Las tres tareas de
    fondo de `run()` (API, object store, worker) SIEMPRE se crean pasando su
    coroutine por acá primero (nunca `asyncio.create_task(api_server.
    serve())` directo) -- ver el punto 9 del docstring del módulo.

    Por qué esto no es paranoia: a diferencia de CUALQUIER otra excepción,
    `asyncio.Task` le da un trato especial a `SystemExit`/`KeyboardInterrupt`
    -- `asyncio.tasks.Task.__step_run_and_handle_result` hace
    `super().set_exception(exc); raise` para esas dos (guarda la excepción
    en la tarea como a cualquier otra, PERO además la vuelve a lanzar hacia
    afuera de la máquina del event loop -- algo que ninguna otra excepción
    hace). Ese `raise` atraviesa `asyncio.events.Handle._run` (que las deja
    pasar explícito, a propósito, para no silenciar un `sys.exit()`/Ctrl-C
    real) y puede escaparse por completo de `asyncio.gather(...,
    return_exceptions=True)` -- que solo protege contra excepciones que la
    tarea reporta "prolijamente" -- interrumpiendo lo que sea que esté
    bombeando el event loop en ese instante exacto. Bajo pytest-asyncio (un
    `asyncio.Runner`/event loop por test) eso puede ser la ejecución de OTRO
    test por completo: confirmado en la práctica (investigación de la fuga
    de tareas asyncio entre archivos de test, apps/local/tests/
    test_runtime.py + test_worker_loop.py) que una tarea así abandonada --
    nunca cancelada/esperada porque el `run_until_complete()` que la
    sostenía se interrumpió a mitad de camino -- sigue viva en memoria hasta
    que el GC cíclico la recolecta en un momento arbitrario posterior,
    dejando un "Task exception was never retrieved" (o turbulencia peor:
    teardown de fixtures de OTRO test interrumpido a mitad de camino, p. ej.
    un `monkeypatch.setattr`/`setitem` que nunca revierte) mientras corre un
    test de otro archivo por completo.

    `uvicorn.Server.startup()` hace EXACTAMENTE esto si el bind del puerto
    falla (`OSError`, típico bajo contención real de puertos/CPU -- en la
    suite de tests bajo carga del sistema, o un puerto ya ocupado en
    producción): `sys.exit(STARTUP_FAILURE)`. Es una llamada perfectamente
    razonable para un proceso standalone (`uvicorn` corriendo solo), pero
    letal acá porque `api_server.serve()`/`objectstore_server.serve()`
    SIEMPRE corren dentro de un `asyncio.create_task()`, nunca como el
    coroutine top-level de un proceso.

    `asyncio.CancelledError` se re-lanza SIN TOCAR: es la forma normal y
    esperada en que el `finally` de `run()` apaga estas tareas
    (`api_server.should_exit = True` / `stop_event.set()` seguido de
    `asyncio.gather(..., return_exceptions=True)`), no un error a
    normalizar. Cualquier `Exception` "normal" (p. ej. un bug real en
    `run_forever`) también se re-lanza sin tocar -- solo lo que ninguna de
    las dos categorías anteriores cubre (`SystemExit`, `KeyboardInterrupt`,
    y en teoría `GeneratorExit`) se envuelve.
    """
    try:
        await coro
    except (asyncio.CancelledError, Exception):
        raise
    except BaseException as exc:
        raise RuntimeError(f"{label} terminó con {type(exc).__name__}: {exc}") from exc


async def _wait_until_healthy(
    port: int,
    tasks: list[asyncio.Task] | None = None,
    stop_event: asyncio.Event | None = None,
) -> None:
    import httpx

    tasks = tasks or []
    url = f"http://127.0.0.1:{port}/healthz"
    async with httpx.AsyncClient(timeout=HEALTHZ_INTERVAL_SECONDS) as client:
        for _ in range(HEALTHZ_MAX_ATTEMPTS):
            if stop_event is not None and stop_event.is_set():
                return
            crashed = next((t for t in tasks if t.done()), None)
            if crashed is not None:
                exc = crashed.exception()
                raise (
                    exc
                    if exc is not None
                    else RuntimeError(f"La tarea {crashed.get_name()!r} terminó antes de tiempo.")
                )
            try:
                response = await client.get(url)
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(HEALTHZ_INTERVAL_SECONDS)
    raise RuntimeError(
        f"edecan_local: {url} no respondió 200 tras {HEALTHZ_MAX_ATTEMPTS} intentos."
    )


# ---------------------------------------------------------------------------
# Orquestación principal
# ---------------------------------------------------------------------------


async def run(
    *,
    port: int = DEFAULT_PORT,
    data_dir: str | Path = DEFAULT_DATA_DIR,
    no_web: bool = False,
    mobile_access: bool = False,
    stop_event: asyncio.Event | None = None,
) -> None:
    """`stop_event`, si se pasa, reemplaza al que este runner crea por
    defecto — mismo patrón que `edecan_local.worker_loop.run_forever`/
    `edecan_worker.scheduler.run_forever` (opcional, para que los tests
    puedan disparar el apagado de forma determinista en vez de depender de
    una señal real de SO)."""
    resolved_data_dir = Path(data_dir).expanduser()
    resolved_data_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(resolved_data_dir, _DATA_DIR_MODE)
    except OSError:
        logger.warning("No se pudo aplicar permisos 0700 a %s.", resolved_data_dir, exc_info=True)

    stop_event = stop_event or asyncio.Event()
    _install_signal_handlers(stop_event)

    # Debe ocurrir antes de importar `edecan_api`: `Settings()` y el
    # `LLMRouter` global quedan construidos y cacheados durante ese import.
    _apply_local_platform_config(resolved_data_dir)

    from edecan_local.pg import ensure_postgres

    database_url, pg_handle = await ensure_postgres(resolved_data_dir)
    try:
        # `OllamaHandle | None` -- se resuelve más abajo, recién cuando
        # `api_settings` está disponible (WP-V4-09, `edecan_local.
        # ollama_supervisor`). Se declara acá, ANTES de cualquier paso que
        # pueda lanzar, para que el `finally` de más abajo pueda apagarlo
        # SIEMPRE sin arriesgar un `NameError` si algo revienta antes de
        # llegar a arrancarlo.
        ollama_handle = None
        objectstore_port = port + OBJECTSTORE_PORT_OFFSET
        objects_root = resolved_data_dir / "objects"
        (objects_root / S3_BUCKET_NAME).mkdir(parents=True, exist_ok=True)

        local_secrets = _ensure_local_secrets(resolved_data_dir)
        serve_web_dir = _resolve_serve_web_dir(no_web=no_web)
        _apply_env(
            _build_env(
                data_dir=resolved_data_dir,
                port=port,
                objectstore_port=objectstore_port,
                database_url=database_url,
                serve_web_dir=serve_web_dir,
                local_secrets=local_secrets,
                public_base_url=(
                    _mobile_public_url(port, resolved_data_dir) if mobile_access else None
                ),
            )
        )
        logger.info(
            "Entorno local listo: EDECAN_LOCAL_MODE=1 DATA_DIR=%s LOCAL_API_PORT=%s "
            "AWS_ENDPOINT_URL=%s SERVE_WEB_DIR=%s",
            resolved_data_dir,
            port,
            os.environ.get("AWS_ENDPOINT_URL"),
            serve_web_dir,
        )

        from edecan_local.migrate import run_migrations

        await asyncio.to_thread(run_migrations, database_url)

        from edecan_local.objectstore import create_object_store_app

        objectstore_app = create_object_store_app(objects_root)

        api_app = _import_api_app()
        api_settings = _import_api_settings()

        # Continuidad híbrida opcional. La configuración vive únicamente en
        # DATA_DIR (o en variables del operador) y nunca en el repositorio.
        # Un error de red o de configuración no impide usar Edecán local.
        from edecan_local.edge_continuity import (
            EdgeContinuityConfigurationError,
            EdgeContinuityState,
            LocalChatTaskHandler,
            load_edge_continuity_config,
            run_edge_continuity,
        )

        edge_state = EdgeContinuityState()
        api_app.state.edge_continuity_state = edge_state
        edge_config = None
        try:
            edge_config = load_edge_continuity_config(resolved_data_dir)
        except EdgeContinuityConfigurationError:
            edge_state.configured = True
            edge_state.last_error = "configuration_invalid"
            logger.warning(
                "La continuidad privada está configurada pero no es válida; "
                "Edecán seguirá funcionando localmente.",
                exc_info=True,
            )
        edge_handler: LocalChatTaskHandler | None = None

        # La propia app instalada es el destino de control remoto. Se
        # registra cuando `/v1/auth/local` resuelve el tenant single-owner;
        # así el QR del teléfono basta y no existe un segundo emparejamiento
        # técnico que la persona deba entender.
        from edecan_local.companion_bridge import LocalCompanionBridge

        if hasattr(api_app.state, "companion_manager"):
            local_companion = LocalCompanionBridge(app=api_app, data_dir=resolved_data_dir)
            api_app.state.ensure_local_companion = local_companion.ensure_registered

        # Ollama embebido OPCIONAL (WP-V4-09, DIRECCION_ACTUAL.md "Confirmado:
        # agregar Ollama"): arranca DESPUÉS del Postgres embebido y de tener
        # `api_settings` (necesita `OLLAMA_BASE_URL`). Ciclo de vida completo
        # documentado en `edecan_local.ollama_supervisor` -- nunca lanza, es
        # de "mejor esfuerzo" (`None` si autostart está apagado, si no hay
        # binario, o si nunca llega a responder). Corre en `to_thread`: usa
        # `subprocess`/sondeo bloqueante, mismo criterio que `pgserver.
        # get_server` un poco más arriba.
        from edecan_local.ollama_supervisor import maybe_start_ollama

        ollama_handle = await asyncio.to_thread(maybe_start_ollama, api_settings)

        api_host = "0.0.0.0" if mobile_access else "127.0.0.1"
        api_server = _make_server(api_app, host=api_host, port=port)
        objectstore_server = _make_server(objectstore_app, host="127.0.0.1", port=objectstore_port)

        from edecan_local.worker_loop import build_local_deps, run_forever

        async with build_local_deps(api_settings) as deps:
            # Las tres corren envueltas en `_run_background` (ver su
            # docstring): un `SystemExit` crudo escapándose de una de estas
            # tareas (p. ej. `uvicorn.Server.startup()` sobre un puerto
            # ocupado) puede interrumpir por completo la ejecución de OTRO
            # test bajo pytest-asyncio -- `_run_background` lo normaliza a
            # una `RuntimeError` común ANTES de que `asyncio.Task` la vea,
            # así el `asyncio.gather(..., return_exceptions=True)` de más
            # abajo SIEMPRE puede capturarla de forma prolija.
            tasks = [
                asyncio.create_task(
                    _run_background(api_server.serve(), label="edecan-local-api"),
                    name="edecan-local-api",
                ),
                asyncio.create_task(
                    _run_background(objectstore_server.serve(), label="edecan-local-objectstore"),
                    name="edecan-local-objectstore",
                ),
                asyncio.create_task(
                    _run_background(
                        run_forever(deps, stop_event=stop_event), label="edecan-local-worker"
                    ),
                    name="edecan-local-worker",
                ),
            ]
            try:
                await _wait_until_healthy(port, tasks, stop_event)
                # No reclamar un trabajo antes de que /healthz responda: si el
                # túnel cae justo durante el arranque, marcar ese job como
                # fallido sería peor que dejarlo unos segundos más en SQS.
                if edge_config is not None and not stop_event.is_set():
                    desktop_capability = str(api_settings.LOCAL_DESKTOP_CAPABILITY or "")
                    if not desktop_capability:
                        edge_state.configured = True
                        edge_state.last_error = "local_desktop_capability_missing"
                        logger.warning(
                            "La continuidad privada no puede reclamar trabajos porque "
                            "este arranque no recibió LOCAL_DESKTOP_CAPABILITY."
                        )
                    else:
                        edge_handler = LocalChatTaskHandler(
                            api_base_url=f"http://127.0.0.1:{port}",
                            desktop_capability=desktop_capability,
                        )
                        try:
                            local_version = importlib.metadata.version("edecan-local")
                        except importlib.metadata.PackageNotFoundError:
                            local_version = "dev"
                        tasks.append(
                            asyncio.create_task(
                                _run_background(
                                    run_edge_continuity(
                                        edge_config,
                                        stop_event=stop_event,
                                        state=edge_state,
                                        version=local_version,
                                        capabilities=[
                                            "chat",
                                            "files",
                                            "tools",
                                            "voice",
                                            "calls",
                                            "desktop",
                                            "ide",
                                        ],
                                        task_handler=edge_handler,
                                    ),
                                    label="edecan-edge-continuity",
                                ),
                                name="edecan-edge-continuity",
                            )
                        )
                if not stop_event.is_set():
                    print(f"EDECAN_LOCAL_READY port={port}", flush=True)
                    logger.info("edecan_local listo (EDECAN_LOCAL_READY port=%s).", port)
                else:
                    logger.info("edecan_local: apagado pedido antes de terminar de arrancar.")
                await stop_event.wait()
            finally:
                stop_event.set()
                api_server.should_exit = True
                objectstore_server.should_exit = True
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for task, result in zip(tasks, results, strict=True):
                    if isinstance(result, BaseException) and not isinstance(
                        result, asyncio.CancelledError
                    ):
                        logger.warning(
                            "Tarea %r terminó con error durante el apagado: %r",
                            task.get_name(),
                            result,
                        )
                if edge_handler is not None:
                    await edge_handler.aclose()
    finally:
        # Orden inverso al de arranque (Ollama arrancó DESPUÉS de Postgres,
        # así que se apaga ANTES): mismo criterio que las tres tareas
        # asyncio de más arriba se apagan antes de llegar acá. `ollama_handle`
        # queda `None` (declarado arriba) en cualquier camino donde nunca se
        # llegó a arrancar Ollama -- ese caso es un no-op explícito, nunca un
        # `NameError`.
        if ollama_handle is not None:
            await asyncio.to_thread(ollama_handle.stop)
        await asyncio.to_thread(pg_handle.cleanup)
        logger.info("edecan_local detenido.")


# ---------------------------------------------------------------------------
# Entry point de `python -m edecan_local` (ver `__main__.py`)
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if args.macos_permission_status:
        print(json.dumps(_macos_permission_status(), separators=(",", ":")))
        return
    if args.macos_capture_check:
        print(
            json.dumps(
                _macos_capture_check(args.macos_capture_output),
                separators=(",", ":"),
            )
        )
        return
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(
        run(
            port=args.port,
            data_dir=args.data_dir,
            no_web=args.no_web,
            mobile_access=args.mobile_access,
        )
    )
