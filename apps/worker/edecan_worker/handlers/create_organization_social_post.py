"""Handler del job `create_organization_linkedin_post`.

Variante PRODUCT-LED del post de LinkedIn para la página de Acme: en vez de
que el motor `edecan_creative.redaccion` busque una noticia y la comente, este
handler le pide a **fydesign** que elija una pantalla REAL de la app de Acme
(`POST /api/linkedin-screen/pick`), escribe un post de 100-150 palabras sobre ESA
pantalla con un modelo directo (el `postBrief.editorialRules` de fydesign como
system prompt, el `screen.summary` + `screen.texts` como contenido), le pide a
fydesign el visual de esa pantalla (`POST /api/linkedin-screen/visual`) y lo
entrega como CARD + PUSH en la conversación PRINCIPAL (is_main), igual que
`create_linkedin_post`.

Por qué NO usa `CrearPostLinkedInTool`: ese tool resuelve la voz y el banco de
contexto del PERFIL EDITORIAL del tenant (`social_editorial_profiles`, sembrado
por `apps/local/edecan_local/organization_linkedin_profile_seed.py`). Acá la línea
editorial la decide fydesign — el `postBrief` que devuelve `/pick` ya trae el
titular, el ángulo, el CTA y las reglas editoriales —, así que el modelo solo
tiene que escribir el cuerpo del post obedeciendo esas reglas. Pasar por el tool
sería mezclar DOS líneas editoriales (la del seed, que es "escena venezolana",
con la de fydesign, que es "pantalla de la app") y el post saldría híbrido. Por
eso este handler hace una llamada DIRECTA al modelo con el postBrief como system
prompt. El seed NO se toca (regla dura del encargo) y ni se importa acá.

LA INVARIANTE, idéntica a `create_linkedin_post`: **después de que el chat dijo
"me pongo a escribir", SIEMPRE tiene que llegar algo.** O el post, o una
explicación honesta de por qué no se pudo, escrita en la conversación. Nunca
silencio. Ver `_avisar_que_no_hubo_post`.

Las dos mitades del fallo, mismo reparto que el handler hermano:

  - falla fydesign/modelo -> se explica en el chat (`_avisar_que_no_hubo_post`)
  - falla la CARD          -> se entrega el post en texto plano (`_entregar_sin_card`)
  - no llegó NADA          -> se deja que la cola reintente (`_rendirse_o_reintentar`)
  - nadie lo pidió         -> el autopiloto no recibe disculpas en el chat, sólo el aviso

Contrato del payload (todo opcional salvo lo marcado):
  conversation_id: str | None  -> si falta, cae al chat principal (is_main). Su
                                   presencia es además la firma del pedido humano
  user_id:         str         -> OBLIGATORIO (JobEnvelope no lo trae)
  tema:            str | None  -> se IGNORA: el tema lo decide fydesign (la pantalla)
  destino:         str | None  -> se IGNORA: este handler SIEMPRE apunta a la página
                                   de Acme (target "organization"); el brandId de
                                   fydesign ya está fijado a Acme
  con_imagen:      bool        -> default True
  origen:          str | None  -> "automatizacion" cuando lo dispara el autopiloto
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import os
import shutil
import uuid
from typing import NamedTuple

import httpx
from edecan_core.notifications import ImportantNotificationEvent
from sqlalchemy import text as sql_text

from ..deps import Deps
from ..repo import SqlRepo
from ..universal_notifications import notify_important_event

logger = logging.getLogger(__name__)

# Configuración explícita de la organización. Vacío deshabilita el flujo hasta
# que el operador conecte su marca.
_BRAND_ID = os.getenv("EDECAN_SOCIAL_BRAND_ID", "").strip()
_COOLDOWN_DAYS = 4

# El cierre del post: el CTA del postBrief + la URL de Acme en línea aparte.
# Mismo mecanismo que `edecan_creative.redaccion._con_cierre_garantizado` (la
# `closing_url` del seed), pero acá cableado porque este handler NO usa el seed.
_CLOSING_URL = os.getenv("EDECAN_SOCIAL_CLOSING_URL", "").strip()

# Destino de LinkedIn: la página de Acme (no el perfil personal). El botón
# "Aprobar y publicar" de la card apunta a este target, y `social_drafts.target`
# lo guarda para que el endpoint de publicar sepa a dónde mandarlo.
_DESTINO = os.getenv("EDECAN_SOCIAL_DESTINATION", "organization").strip() or "organization"

# Piso mínimo de entrega: si el modelo devuelve menos que esto, algo salió mal
# (vacío, error, un eco del prompt). 50 palabras ~= 300 caracteres en español;
# el piso del motor hermano es en caracteres, acá usamos palabras porque el
# brief manda un conteo de palabras (100-150), no de caracteres.
_MIN_PALABRAS = 50

# El `aspecto` que esta card manda a iOS para su imagen. fydesign devuelve un
# visual 1080x1350 (4:5 vertical), así que "libre" evita el recorte que sufriría
# dentro de un marco cuadrado o panorámico con `contentMode: .fill`.
_IMAGEN_ASPECTO_CARD = "libre"

# Alias del modelo que escribe el post: "profundo" (GLM 5.2 en esta instalación)
# — un post en background tolera la latencia y conviene el modelo más capaz.
_LLM_ALIAS = "profundo"
_LLM_MAX_TOKENS = 900
_LLM_TEMPERATURE = 0.7

# Timeout de las llamadas a fydesign: pick hace análisis de repo (puede tardar)
# y visual genera/renderiza una imagen. Amplio a propósito.
_FYDESIGN_TIMEOUT_PICK = 120.0
_FYDESIGN_TIMEOUT_VISUAL = 180.0

# Cuántos reintentos de la cola se dejan correr antes de rendirse (igual que el
# handler hermano: dos, ~90s de backoff, suficiente para un corte momentáneo).
_REINTENTOS_DE_COLA_ANTES_DE_RENDIRSE = 2

# `payload["origen"]` cuando el trabajo lo disparó una automatización.
_ORIGEN_AUTOMATIZACION = "automatizacion"

_FALLO_FYDESIGN_PICK = "fydesign_pick"
_FALLO_FYDESIGN_VISUAL = "fydesign_visual"
_FALLO_MODELO = "modelo"
_FALLO_ERROR_INTERNO = "error_interno"


class _VisualDescargado(NamedTuple):
    """Bytes del PNG y del MP4 que devuelve fydesign /visual. Cualquier mitad
    puede estar vacía (degradación graceful); si hay video se prefiere para
    reproducir en el chat, la imagen queda como fallback de publicación."""

    img_data: bytes = b""
    img_mime: str = ""
    video_data: bytes = b""
    video_mime: str = ""


async def handle(env, deps: Deps) -> None:  # noqa: ANN001 (JobEnvelope; ver handlers/__init__)
    if env.tenant_id is None:
        logger.warning("create_organization_linkedin_post sin tenant_id; se descarta.")
        return
    tenant_id = env.tenant_id
    payload = env.payload or {}
    user_id_raw = str(payload.get("user_id") or "").strip()
    if not user_id_raw:
        logger.warning("create_organization_linkedin_post sin user_id en el payload; se descarta.")
        return
    user_id = uuid.UUID(user_id_raw)
    con_imagen = payload.get("con_imagen", True) is not False

    settings = deps.settings

    copy_text = ""
    file_id: uuid.UUID | None = None
    filename = ""
    image_mime = "image/png"
    video_file_id: uuid.UUID | None = None
    video_filename = ""
    video_mime = "video/mp4"
    fallo = ""

    # El `try` envuelve TODO lo que produce el post: llamar a fydesign, escribir el
    # texto con el modelo, descargar el visual y subirlo. Cualquier excepción que
    # se escape es silencio — el mismo bug que el handler hermano existe para
    # cerrar — así que se atrapa, se explica, y sólo si NI ESO se pudo entregar se
    # deja que la cola reintente (ver el final de este handler).
    try:
        from edecan_core.tools.base import ToolContext
        from edecan_creative._files import subir_archivo

        # 1) fydesign autopost: un solo subprocess que elige la pantalla, autores
        #    el video con Opus (bucle de crítica), renderiza MP4 + poster PNG, y
        #    escribe el copy del post con Opus (bucle de crítica). El texto y el
        #    video vienen de Opus — no del LLM del worker ni de Neon.
        autopost = await _llamar_fydesign_autopost(settings)
        if autopost is None:
            fallo = _FALLO_FYDESIGN_PICK
            raise RuntimeError("fydesign autopost no devolvió resultado.")

        # 2) Copy del post: lo escribe Opus (ya con CTA + organization.org).
        text_obj = autopost.get("text") or {}
        cuerpo = str(text_obj.get("body") or "").strip()
        if not cuerpo or len(cuerpo.split()) < _MIN_PALABRAS:
            fallo = _FALLO_MODELO
            raise RuntimeError(
                f"Opus devolvió un post demasiado corto ({len(cuerpo.split())} palabras)."
            )
        copy_text = cuerpo  # ya incluye CTA + organization.org (post-text.ts)

        # 3) Visual de fydesign: lee el PNG (poster) y el MP4 (video) que autopost
        #    dejó en disco. Reusa _leer_bytes_visual (mismas keys localImagePath/
        #    localVideoPath). Degradación graceful si falla — nunca silencio.
        if con_imagen:
            llm_router = await deps.llm_router_for(tenant_id)
            async with deps.session_factory(None) as session:
                ctx = ToolContext(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    session=session,
                    settings=settings,
                    llm=llm_router,
                    vault=deps.vault,
                    extras={"flags": {}},
                )
                img_data, img_mime = await _leer_bytes_visual(
                    autopost, "localImagePath", "imageUrl", mime_default="image/png"
                )
                video_data, video_mime = await _leer_bytes_visual(
                    autopost, "localVideoPath", "videoUrl", mime_default="video/mp4"
                )
                if img_data:
                    image_mime = img_mime
                    ext = "png" if "png" in img_mime else "jpg"
                    fname = f"organization-linkedin-{uuid.uuid4().hex[:8]}.{ext}"
                    file_id, filename = await subir_archivo(
                        ctx, data=img_data, filename=fname, mime=img_mime
                    )
                # El MP4 se sube aparte (mismo uploader, misma fila `files`): el
                # video se reproduce en el chat pero NO se publica en LinkedIn en
                # este alcance (la subida de video a LinkedIn es un paso posterior).
                if video_data:
                    vext = "mp4" if "mp4" in video_mime else "mp4"
                    vfname = f"organization-linkedin-{uuid.uuid4().hex[:8]}.{vext}"
                    try:
                        video_file_id, video_filename = await subir_archivo(
                            ctx, data=video_data, filename=vfname, mime=video_mime
                        )
                    except Exception:
                        logger.warning(
                            "create_organization_linkedin_post: no se pudo subir el MP4; "
                            "la card cae a imagen/fallback.",
                            exc_info=True,
                        )
                        video_file_id = None
    except Exception:
        logger.exception("create_organization_linkedin_post: el trabajo se cayó antes de entregar.")
        fallo = fallo or _FALLO_ERROR_INTERNO

    if not copy_text or len(copy_text.split()) < _MIN_PALABRAS:
        logger.warning(
            "create_organization_linkedin_post: no se produjo un post publicable "
            "(%d palabras, fallo=%s).",
            len(copy_text.split()),
            fallo or "sin código",
        )
        llego_algo = await _avisar_que_no_hubo_post(
            deps,
            tenant_id=tenant_id,
            user_id=user_id,
            job_id=env.job_id,
            payload=payload,
            fallo=fallo,
        )
        _rendirse_o_reintentar(env, llego_algo, "no hubo post y tampoco se pudo avisar")
        return

    # --- Borrador persistido -> card -> chat PRINCIPAL (is_main) + push ---
    # El borrador se guarda ANTES de armar la card, y con el MISMO id: es lo
    # único que le da sentido al botón "Aprobar y publicar". Ver el docstring de
    # `create_linkedin_post._persistir_borrador` para el porqué.
    card_id = _card_id(file_id)
    draft_id = await _persistir_borrador(
        deps,
        tenant_id=tenant_id,
        user_id=user_id,
        card_id=card_id,
        copy_text=copy_text,
        file_id=file_id,
        video_file_id=video_file_id,
    )

    # ENTREGAR TAMBIÉN PUEDE FALLAR (ver el handler hermano): el mismo
    # `_resolver_conversacion`, la misma base de datos, y la red tiene que estar
    # de los dos lados. Si la card no valida o la base se cayó un segundo, el
    # texto plano sí pasa.
    conversation_id: uuid.UUID | None = None
    entregado = False
    try:
        async with deps.session_factory(None) as session:
            repo = SqlRepo(session)
            conversation_id = await _resolver_conversacion(
                repo, tenant_id=tenant_id, user_id=user_id, payload=payload
            )

            card_json = _armar_card(
                copy_text,
                file_id,
                filename,
                card_id=draft_id or card_id,
                draft_id=draft_id,
                mime=image_mime,
                video_file_id=video_file_id,
                video_filename=video_filename,
                video_mime=video_mime,
            )
            tool_call_id = f"organization-linkedin-{uuid.uuid4().hex[:12]}"
            evento_tool_end = {
                "type": "tool_end",
                "tool_call_id": tool_call_id,
                "name": "crear_post_organization_linkedin",
                "result_preview": copy_text[:200],
                "blocks_version": 1,
                "blocks": [card_json],
            }
            await repo.add_message(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                role="assistant",
                content={
                    "text": _intro_del_post(
                        imagen_perdida=(
                            con_imagen
                            and file_id is None
                            and bool(getattr(settings, "FYDESIGN_URL", None))
                        ),
                    ),
                    "presentation": [card_json],
                },
                tool_calls=[evento_tool_end],
            )
        entregado = True
    except Exception:
        logger.exception(
            "create_organization_linkedin_post: el post estaba escrito y falló la ENTREGA."
        )
        entregado, conversation_id = await _entregar_sin_card(
            deps, tenant_id=tenant_id, user_id=user_id, copy_text=copy_text
        )

    # --- Push (durable + deduped) ----------------------------------------
    await notify_important_event(
        deps,
        ImportantNotificationEvent(
            tenant_id=tenant_id,
            user_id=user_id,
            kind="content_created" if entregado else "work_failed",
            event_id=env.job_id,
            chat_id=conversation_id,
        ),
    )
    _rendirse_o_reintentar(env, entregado, "el post se escribió pero no se pudo entregar")


# ---------------------------------------------------------------------------
# fydesign
# ---------------------------------------------------------------------------


def _fydesign_headers(settings) -> dict[str, str]:
    """Headers comunes a toda llamada a fydesign (`x-edecan-key`)."""
    return {"x-edecan-key": getattr(settings, "EDECAN_API_KEY", None) or ""}


def _fydesign_url(settings) -> str:
    """Base URL de fydesign sin barra final (de `FYDESIGN_URL` en la config)."""
    return str(getattr(settings, "FYDESIGN_URL", None) or "").rstrip("/")


def _fydesign_dir(settings) -> str:
    """Ruta al repo de fydesign en esta Mac (de `FYDESIGN_DIR` en la config).
    Si está seteada, Edecan lanza fydesign como subprocess de un solo uso
    (sin servidor, cero calor en idle). Si no, cae al HTTP clásico."""
    return str(getattr(settings, "FYDESIGN_DIR", None) or "").strip()


async def _run_fydesign_cli(settings, args: list[str], *, timeout: float = 240.0) -> dict | None:
    """Lanza `npx tsx scripts/edecan-generate.ts <args>` en el repo de fydesign,
    captura el JSON de stdout y lo devuelve. None si falló."""
    d = _fydesign_dir(settings)
    if not d or not os.path.isdir(d):
        return None
    script = os.path.join(d, "scripts", "edecan-generate.ts")
    if not os.path.isfile(script):
        logger.warning("create_organization_linkedin_post: no existe %s", script)
        return None
    cmd = ["npx", "tsx", "scripts/edecan-generate.ts", *args]
    # El sidecar (binario congelado de PyInstaller) hereda el PATH de launchd,
    # que en macOS no incluye Homebrew. `npx` vive en /opt/homebrew/opt/node@22/bin.
    # Si no está en PATH, buscamos rutas conocidas y las prependemos.
    env = dict(os.environ)
    if not shutil.which("npx"):
        for candidate in ("/opt/homebrew/opt/node@22/bin", "/opt/homebrew/bin", "/usr/local/bin"):
            if os.path.isfile(os.path.join(candidate, "npx")):
                env["PATH"] = candidate + ":" + env.get("PATH", "")
                break
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=d,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as e:
        logger.warning("create_organization_linkedin_post: no pude lanzar npx/tsx: %s", e)
        return None
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        logger.warning("create_organization_linkedin_post: fydesign CLI timeout (240s).")
        return None
    stdout = stdout_b.decode("utf-8", "replace").strip()
    stderr = stderr_b.decode("utf-8", "replace").strip()
    if proc.returncode != 0 or not stdout:
        logger.warning(
            "create_organization_linkedin_post: fydesign CLI exit %s — %s",
            proc.returncode,
            (stderr or stdout)[:300],
        )
        return None
    try:
        data = _json.loads(stdout)
    except _json.JSONDecodeError:
        logger.warning(
            "create_organization_linkedin_post: fydesign CLI stdout no es JSON: %s", stdout[:200]
        )
        return None
    if not data.get("ok", True):
        logger.warning(
            "create_organization_linkedin_post: fydesign CLI ok=false: %s", data.get("error", "")
        )
        return None
    return data


async def _llamar_fydesign_autopost(settings) -> dict | None:
    """Pipeline autónomo completo de fydesign: un solo subprocess que elige la
    pantalla, autores la escena HTML con Opus (bucle de crítica), renderiza el
    MP4, captura un poster PNG y escribe el copy del post con Opus (bucle de
    crítica). Devuelve {ok, route, screenName, localImagePath, localVideoPath,
    text:{headline, body, hashtags}, ...}. None si falló.

    Reemplaza al flujo pick → _generar_post_texto → visual: el texto y el video
    vienen de Opus (vía CLI de Claude, gratis por suscripción) en una sola llamada."""
    if _fydesign_dir(settings):
        data = await _run_fydesign_cli(settings, ["autopost", "--brand", _BRAND_ID], timeout=900.0)
        if data is None:
            return None
        logger.info(
            "create_organization_linkedin_post: fydesign autopost eligió %s (route=%s, videoScore=%s).",
            data.get("screenName"),
            data.get("route"),
            data.get("videoScore"),
        )
        return data
    return None


async def _llamar_fydesign_pick(settings) -> dict | None:
    """Pide una pantalla a fydesign. Preferencia: subprocess (FYDESIGN_DIR, sin
    servidor). Retro-compat: HTTP (FYDESIGN_URL). Devuelve el `screen` o None."""
    # ── Camino A: subprocess on-demand (sin servidor, sin calor en idle) ──
    if _fydesign_dir(settings):
        data = await _run_fydesign_cli(
            settings, ["pick", "--brand", _BRAND_ID, "--cooldown", str(_COOLDOWN_DAYS)]
        )
        if data is None:
            return None
        screen = data.get("screen")
        if isinstance(screen, dict):
            logger.info(
                "create_organization_linkedin_post: fydesign CLI eligió %s (route=%s, captura=%s).",
                screen.get("screenName"),
                screen.get("route"),
                screen.get("hasScreenshot"),
            )
            return screen
        logger.warning("create_organization_linkedin_post: fydesign CLI no devolvió un screen.")
        return None
    # ── Camino B (retro-compat): HTTP al servidor fydesign ──
    base = _fydesign_url(settings)
    if not base:
        logger.warning(
            "create_organization_linkedin_post: no hay FYDESIGN_DIR ni FYDESIGN_URL; "
            "no se puede pedir pantalla."
        )
        return None
    async with httpx.AsyncClient(timeout=_FYDESIGN_TIMEOUT_PICK) as client:
        r = await client.post(
            f"{base}/api/linkedin-screen/pick",
            headers=_fydesign_headers(settings),
            json={"brandId": _BRAND_ID, "cooldownDays": _COOLDOWN_DAYS},
        )
        r.raise_for_status()
        data = r.json()
    if not data.get("ok"):
        logger.warning("create_organization_linkedin_post: fydesign /pick respondió ok=false.")
        return None
    screen = data.get("screen")
    if not isinstance(screen, dict):
        logger.warning("create_organization_linkedin_post: fydesign /pick no devolvió un screen.")
        return None
    logger.info(
        "create_organization_linkedin_post: fydesign eligió %s (route=%s).",
        screen.get("screenName"),
        screen.get("route"),
    )
    return screen


async def _llamar_fydesign_visual(settings, route: str, headline: str) -> dict | None:
    """Pide el visual (PNG + HTML + MP4 + crítico) a fydesign. Preferencia:
    subprocess (FYDESIGN_DIR). Retro-compat: HTTP. Devuelve el JSON o None."""
    if _fydesign_dir(settings):
        data = await _run_fydesign_cli(
            settings,
            [
                "visual",
                "--brand",
                _BRAND_ID,
                "--route",
                route,
                "--headline",
                headline,
            ],
        )
        return data
    base = _fydesign_url(settings)
    if not base:
        return None
    async with httpx.AsyncClient(timeout=_FYDESIGN_TIMEOUT_VISUAL) as client:
        r = await client.post(
            f"{base}/api/linkedin-screen/visual",
            headers=_fydesign_headers(settings),
            json={"brandId": _BRAND_ID, "route": route, "headline": headline},
        )
        r.raise_for_status()
        data = r.json()
    if not data.get("ok"):
        logger.warning("create_organization_linkedin_post: fydesign /visual respondió ok=false.")
        return None
    return data


async def _descargar_visual(settings, screen: dict, headline: str) -> _VisualDescargado:
    """Pide el visual a fydesign y lee los bytes del PNG y (si lo hay) del MP4.

    fydesign guarda el visual en el SSD portable ("Creaciones FyDesign") y
    devuelve `localImagePath`/`localVideoPath` (rutas absolutas en esta Mac); se
    leen directo del disco — sin GCS, sin red. Si alguna de las dos trae una
    URL http en su lugar (`imageUrl`/`videoUrl`), se baja por red (retro-compat).

    Si falla en CUALQUIER paso, devuelve el visual con lo que tenga (vacío en
    el caso que falle) — el post se entrega sin imagen y/o sin video
    (degradación graceful, nunca fallo silencioso): si hay video se prefiere
    para reproducir en el chat; la imagen queda como fallback de publicación.
    """
    try:
        route = str(screen.get("route") or "")
        visual = await _llamar_fydesign_visual(settings, route, headline)
        if visual is None:
            return _VisualDescargado()
        img_data, img_mime = await _leer_bytes_visual(
            visual, "localImagePath", "imageUrl", mime_default="image/png"
        )
        video_data, video_mime = await _leer_bytes_visual(
            visual, "localVideoPath", "videoUrl", mime_default="video/mp4"
        )
        if not img_data and not video_data:
            logger.warning(
                "create_organization_linkedin_post: fydesign /visual sin imagen ni video."
            )
        logger.info(
            "create_organization_linkedin_post: visual leído (img=%d bytes %s, video=%d bytes %s).",
            len(img_data),
            img_mime,
            len(video_data),
            video_mime,
        )
        return _VisualDescargado(
            img_data=img_data, img_mime=img_mime, video_data=video_data, video_mime=video_mime
        )
    except Exception:
        logger.warning(
            "create_organization_linkedin_post: falló el visual; el post sale sin imagen/video.",
            exc_info=True,
        )
        return _VisualDescargado()


async def _leer_bytes_visual(
    visual: dict, local_key: str, url_key: str, *, mime_default: str
) -> tuple[bytes, str]:
    """Lee los bytes de un asset del JSON de fydesign: primero la ruta local en
    disco (`local_key`), y si no viene, la URL http (`url_key`). Devuelve
    `(b"", "")` si el asset no está o falla — nunca propaga la excepción: un
    asset que falta degrada, no tumba el visual entero."""
    try:
        local_path = str(visual.get(local_key) or "")
        if local_path:
            from pathlib import Path

            p = Path(local_path)
            if not p.is_file():
                logger.warning(
                    "create_organization_linkedin_post: %s no existe: %s", local_key, local_path
                )
                return b"", ""
            data = p.read_bytes()
            if not data:
                logger.warning("create_organization_linkedin_post: %s leyó 0 bytes.", local_key)
                return b"", ""
            return data, mime_default
        url = str(visual.get(url_key) or "")
        if not url:
            return b"", ""
        async with httpx.AsyncClient(timeout=_FYDESIGN_TIMEOUT_VISUAL) as client:
            r = await client.get(url)
            r.raise_for_status()
            mime = (r.headers.get("content-type") or mime_default).split(";")[0].strip()
        return r.content, mime
    except Exception:
        logger.warning(
            "create_organization_linkedin_post: no se pudo leer %s; se omite este asset.",
            local_key,
            exc_info=True,
        )
        return b"", ""


# ---------------------------------------------------------------------------
# Texto del post
# ---------------------------------------------------------------------------


async def _generar_post_texto(deps: Deps, tenant_id: uuid.UUID, screen: dict) -> str:
    """Llama DIRECTA al modelo con el `postBrief.editorialRules` como system
    prompt y el `screen.summary` + `screen.texts` como contenido del usuario.

    No pasa por `CrearPostLinkedInTool`: la línea editorial la decide fydesign
    (el postBrief), no el seed del perfil del tenant. El router resuelve el
    modelo del alias "profundo" (GLM 5.2 en esta instalación)."""
    from edecan_llm.base import ChatMessage, CompletionRequest

    post_brief = screen.get("postBrief") or {}
    editorial_rules = post_brief.get("editorialRules") or []
    system_prompt = _system_prompt_editorial(editorial_rules)
    user_content = _user_prompt_pantalla(screen)

    llm_router = await deps.llm_router_for(tenant_id)
    request = CompletionRequest(
        model="",  # `llm_router.complete` lo reemplaza por el modelo resuelto del alias.
        system=system_prompt,
        messages=[ChatMessage(role="user", content=user_content)],
        max_tokens=_LLM_MAX_TOKENS,
        temperature=_LLM_TEMPERATURE,
    )
    response = await llm_router.complete(_LLM_ALIAS, {}, request)
    return response.text.strip()


def _system_prompt_editorial(editorial_rules: list) -> str:
    """El system prompt que OBedece las `editorialRules` del postBrief de
    fydesign. Es la línea editorial completa: el modelo no inventa, solo
    escribe obedeciendo estas reglas."""
    reglas = (
        "\n".join(f"- {r}" for r in editorial_rules) if editorial_rules else "- (sin reglas extra)"
    )
    return (
        "Eres el redactor de la página de LinkedIn de Acme. Escribe un post "
        "product-led sobre una pantalla concreta de la app de Acme, en español "
        'de Venezuela con tuteo (nunca voseo: nada de "vos", "querés", "tenés", '
        '"podés").\n\n'
        "Reglas editoriales del brief (obligatorias):\n"
        f"{reglas}\n\n"
        "Instrucciones:\n"
        "- El post trata sobre la pantalla concreta de la app que se te indica, con "
        "los textos reales que aparecen en ella.\n"
        "- La app de Acme es SIEMPRE la protagonista: si el post funciona igual "
        "sin nombrar la app, está mal escrito.\n"
        "- No inventes funciones, productos ni cifras que no estén en el contenido "
        "provisto.\n"
        "- Escribe entre 100 y 150 palabras.\n"
        "- No incluyas el cierre ni el llamado a la acción final: eso se agrega aparte.\n"
        "- Devuelve SOLO el texto del post, sin prefijos, sin saludos, sin "
        "explicaciones, sin markdown."
    )


def _user_prompt_pantalla(screen: dict) -> str:
    """El mensaje del usuario: el resumen de la pantalla + los textos reales +
    el titular y ángulo del brief."""
    texts = screen.get("texts") or []
    textos = "\n".join(f"- {t}" for t in texts) if texts else "- (sin textos disponibles)"
    post_brief = screen.get("postBrief") or {}
    headline = str(post_brief.get("headline") or "").strip()
    angle = str(post_brief.get("angle") or "").strip()
    partes = [
        f"Pantalla: {screen.get('screenName', '?')} ({screen.get('featureName', '?')})",
        f"Ruta: {screen.get('route', '?')}",
        f"Resumen: {screen.get('summary', '?')}",
        "",
        f"Textos reales de la pantalla:\n{textos}",
    ]
    if headline:
        partes.append(f"\nTitular sugerido: {headline}")
    if angle:
        partes.append(f"Ángulo editorial: {angle}")
    return "\n".join(partes)


def _con_cierre(cuerpo: str, cta: str) -> str:
    """Apendiza el cierre garantizado: el CTA del brief + la URL de Acme en
    línea aparte, una sola vez, al final del post. Igual que el
    `closing_url` del seed, pero cableado acá (este handler no usa el seed)."""
    bloque = f"{cta}\n{_CLOSING_URL}" if cta else _CLOSING_URL
    return f"{cuerpo}\n\n{bloque}"


# ---------------------------------------------------------------------------
# Entrega (card, borrador, error nets) — espejos de create_linkedin_post
# ---------------------------------------------------------------------------


def _intro_del_post(*, imagen_perdida: bool = False) -> str:
    """La frase corta que acompaña a la card. El cuerpo COMPLETO del post ya va
    DENTRO de la card (nodo `cuerpo`); si este texto lo repitiera, iOS lo pinta
    dos veces (ver `create_linkedin_post._intro_del_post`)."""
    intro = "Tu borrador de LinkedIn para la página está listo 👇"
    if imagen_perdida:
        intro = (
            f"{intro}\n\n🖼️ La imagen no salió en este intento (fydesign falló); el "
            "texto quedó listo. Pídeme el post de nuevo si la quieres con foto."
        )
    return intro


async def _entregar_sin_card(
    deps: Deps,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    copy_text: str,
) -> tuple[bool, uuid.UUID | None]:
    """Último intento de entregar un post que YA está escrito, sin tarjeta y en
    el hilo principal. Espejo de `create_linkedin_post._entregar_sin_card`."""
    encabezado = (
        "Te escribí el post, pero se me trabó la tarjeta. Te lo dejo tal cual, para que lo copies:"
    )
    cuerpo = f"{encabezado}\n\n{copy_text}"
    try:
        async with deps.session_factory(None) as session:
            repo = SqlRepo(session)
            conv = await repo.resolve_main_conversation(tenant_id=tenant_id, user_id=user_id)
            conversation_id = uuid.UUID(str(conv["id"]))
            await repo.add_message(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                role="assistant",
                content={"text": cuerpo},
            )
            return True, conversation_id
    except Exception:
        logger.warning(
            "create_organization_linkedin_post: tampoco se pudo entregar el post en texto plano.",
            exc_info=True,
        )
        return False, None


def _rendirse_o_reintentar(env, llego_algo: bool, motivo: str) -> None:  # noqa: ANN001 (JobEnvelope)
    """Si NADA le llegó a la persona, deja que la cola reintente. Espejo de
    `create_linkedin_post._rendirse_o_reintentar`."""
    if llego_algo:
        return
    if getattr(env, "attempt", 0) < _REINTENTOS_DE_COLA_ANTES_DE_RENDIRSE:
        raise RuntimeError(
            f"create_organization_linkedin_post: {motivo}; nada le llegó a la persona. "
            "Se deja que la cola reintente."
        )
    logger.error(
        "create_organization_linkedin_post: %s y se agotaron los reintentos (attempt=%s). "
        "El trabajo termina sin entrega.",
        motivo,
        getattr(env, "attempt", 0),
    )


async def _resolver_conversacion(
    repo,  # noqa: ANN001 (Repo; el mismo protocolo que usa el resto del worker)
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: dict,
) -> uuid.UUID:
    """La conversación donde se entrega el resultado: la del pedido, o la
    principal. Espejo de `create_linkedin_post._resolver_conversacion`."""
    conv_id_raw = str(payload.get("conversation_id") or "").strip()
    conv = None
    if conv_id_raw:
        conv = await repo.get_conversation(
            tenant_id=tenant_id, conversation_id=uuid.UUID(conv_id_raw)
        )
    if conv is None:
        conv = await repo.resolve_main_conversation(tenant_id=tenant_id, user_id=user_id)
    return uuid.UUID(str(conv["id"]))


def _lo_pidio_una_persona(payload: dict) -> bool:
    """¿Hay alguien esperando una respuesta en un chat, o esto lo disparó el
    autopiloto? Espejo de `create_linkedin_post._lo_pidio_una_persona`."""
    if str(payload.get("origen") or "").strip() == _ORIGEN_AUTOMATIZACION:
        return False
    return bool(str(payload.get("conversation_id") or "").strip())


# Qué se le dice a la PERSONA cuando no hubo post, según el motivo.
_DETALLE_FYDESIGN_PICK = (
    "No pude conseguir la pantalla de la app que iba a comentar (el servicio que la "
    "elige falló). Vuelve a pedírmelo en un rato; si sigue sin funcionar, lo reviso yo."
)
_DETALLE_FYDESIGN_VISUAL = (
    "Tenía la pantalla pero no conseguí el visual que la acompaña, y prefiero "
    "entregártelo completo. Pídemelo otra vez y lo intento de una."
)
_DETALLE_MODELO = (
    "No pude escribir el post del largo adecuado para esa pantalla. Dame un "
    "momento y vuelve a pedírmelo; si sigue sin funcionar, lo reviso yo."
)
_DETALLE_ERROR_INTERNO = (
    "Se me cayó el proceso a mitad de camino y no quedó nada guardado. Pídemelo "
    "otra vez y lo vuelvo a intentar."
)
_MENSAJE_SIN_POST_GENERICO = (
    "No me salió nada que valiera la pena entregarte, y prefiero decírtelo a "
    "dejarte esperando. Pídemelo otra vez."
)


def _mensaje_sin_post(fallo: str) -> str:
    """El aviso honesto que se escribe en el chat cuando no hubo post."""
    detalle = {
        _FALLO_FYDESIGN_PICK: _DETALLE_FYDESIGN_PICK,
        _FALLO_FYDESIGN_VISUAL: _DETALLE_FYDESIGN_VISUAL,
        _FALLO_MODELO: _DETALLE_MODELO,
        _FALLO_ERROR_INTERNO: _DETALLE_ERROR_INTERNO,
    }.get(fallo, _MENSAJE_SIN_POST_GENERICO)
    return f"No pude escribirte el post de LinkedIn para la página de Acme. {detalle}"


async def _avisar_que_no_hubo_post(
    deps: Deps,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    job_id: uuid.UUID,
    payload: dict,
    fallo: str,
) -> bool:
    """LA INVARIANTE: después de "me pongo a escribir", SIEMPRE llega algo. O el
    post, o esta explicación. Espejo de `create_linkedin_post._avisar_que_no_hubo_post`."""
    conversation_id: uuid.UUID | None = None
    mensaje_escrito = False
    # ANTES: si lo disparó la automatización, no se escribía en el chat.
    # AHORA: siempre se escribe — el dueño necesita saber por qué no llegó el post,
    # aunque lo haya disparado el autopiloto. El push solo no basta (no se ve).
    try:
        async with deps.session_factory(None) as session:
            repo = SqlRepo(session)
            conversation_id = await _resolver_conversacion(
                repo, tenant_id=tenant_id, user_id=user_id, payload=payload
            )
            await repo.add_message(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                role="assistant",
                content={"text": _mensaje_sin_post(fallo)},
            )
            mensaje_escrito = True
    except Exception:
        logger.warning(
            "create_organization_linkedin_post: no se pudo escribir el aviso en el chat; "
            "queda el push.",
            exc_info=True,
        )

    durable = False
    try:
        resultado = await notify_important_event(
            deps,
            ImportantNotificationEvent(
                tenant_id=tenant_id,
                user_id=user_id,
                kind="work_failed",
                event_id=job_id,
                chat_id=conversation_id,
            ),
        )
        durable = bool(getattr(resultado, "durable", False))
    except Exception:
        logger.warning(
            "create_organization_linkedin_post: no se pudo avisar el fallo.", exc_info=True
        )

    return mensaje_escrito or durable


# ---------------------------------------------------------------------------
# Borrador + card
# ---------------------------------------------------------------------------


def _card_id(file_id: uuid.UUID | None) -> str:
    """El id que comparten la CARD y su fila de `social_drafts`. Espejo de
    `create_linkedin_post._card_id`."""
    return f"organization-linkedin-{file_id.hex[:8] if file_id else uuid.uuid4().hex[:8]}"


async def _persistir_borrador(
    deps: Deps,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    card_id: str,
    copy_text: str,
    file_id: uuid.UUID | None,
    video_file_id: uuid.UUID | None = None,
) -> str | None:
    """Guarda el borrador en `social_drafts` y devuelve el `draft_id` guardado.
    Espejo de `create_linkedin_post._persistir_borrador` (sin `destino` porque
    este handler SIEMPRE apunta a la página de Acme, `_DESTINO`).

    `video_file_id` guarda el MP4 para reproducirlo en el chat; NO se publica
    en LinkedIn en este alcance (la publicación sigue mandando texto+imagen con
    `image_file_id`), así que queda `NULL` sin afectar la publicación."""
    params = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "platform": "linkedin",
        "target": _DESTINO,
        "text": copy_text,
        "image_file_id": file_id,
        "video_file_id": video_file_id,
    }
    insert = sql_text(
        "INSERT INTO social_drafts ("
        "  tenant_id, user_id, draft_id, platform, target, text, "
        "  image_file_id, video_file_id"
        ") VALUES ("
        "  :tenant_id, :user_id, :draft_id, :platform, :target, :text, "
        "  :image_file_id, :video_file_id"
        ") ON CONFLICT (tenant_id, draft_id) DO NOTHING "
        "RETURNING draft_id"
    )
    try:
        async with deps.session_factory(None) as session:
            for intento, candidato in enumerate(
                (card_id, f"organization-linkedin-{uuid.uuid4().hex[:12]}")
            ):
                fila = (
                    (await session.execute(insert, {**params, "draft_id": candidato}))
                    .mappings()
                    .first()
                )
                if fila is not None:
                    return str(fila["draft_id"])
                logger.warning(
                    "create_organization_linkedin_post: el draft_id %s ya existe para este "
                    "tenant (intento %d); no piso el borrador anterior.",
                    candidato,
                    intento + 1,
                )
    except Exception:
        logger.warning(
            "create_organization_linkedin_post: no se pudo guardar el borrador en "
            "social_drafts (tenant_id=%s). La card sale sin el botón de publicar.",
            tenant_id,
            exc_info=True,
        )
    return None


def _armar_card(
    copy_text: str,
    file_id,
    filename: str,
    *,
    card_id: str,
    draft_id: str | None,
    mime: str = "image/png",
    video_file_id=None,
    video_filename: str = "",
    video_mime: str = "video/mp4",
) -> dict:
    """Arma la card del post de Acme. Espejo de
    `create_linkedin_post._armar_card`, simplificado: siempre es la página de
    Acme (nunca el perfil personal), sin `sin_auditar` (la línea editorial
    la decide fydesign, no el auditor de hechos del motor).

    `draft_id=None` significa "el borrador no quedó guardado": entonces la card
    cae a Copiar texto en vez de ofrecer "Aprobar y publicar". Regla de la casa:
    no se pinta un botón que no puede cumplir.

    Si hay `video_file_id` se emite un ``VideoNode`` (reproducible en el chat
    con AVPlayer) EN LUGAR de la imagen: el video se prefiere para el chat; si
    no, se mantiene la imagen como fallback. La publicación a LinkedIn sigue
    usando `image_file_id` (la subida de video a LinkedIn es un paso posterior
    fuera de este alcance)."""
    from edecan_core.cards import BotonCard, construir_card_generica
    from edecan_schemas import (
        ApproveDraftAction,
        ArtifactRef,
        CopyTextAction,
        SaveArtifactAction,
    )

    imagen = None
    if file_id is not None:
        imagen = ArtifactRef(file_id=file_id, filename=filename, mime=mime)
    video = None
    if video_file_id is not None:
        video = ArtifactRef(file_id=video_file_id, filename=video_filename, mime=video_mime)
    cuerpo = tuple(p for p in copy_text.split("\n\n") if p.strip())[:6]

    botones: list[BotonCard] = []
    if draft_id is None:
        botones.append(
            BotonCard(
                accion=CopyTextAction(id="copiar-texto", label="Copiar texto", text=copy_text)
            )
        )
        if file_id is not None:
            botones.append(
                BotonCard(
                    estilo="secundario",
                    accion=SaveArtifactAction(
                        id="guardar-imagen", label="Guardar imagen", file_id=file_id
                    ),
                )
            )
    else:
        botones.append(
            BotonCard(
                accion=ApproveDraftAction(
                    id="aprobar-post", label="Aprobar y publicar", draft_id=draft_id
                )
            )
        )

    return construir_card_generica(
        card_id=card_id,
        fallback_text=copy_text[:280] or "Borrador de LinkedIn listo",
        kicker="Acme · LinkedIn",
        titulo="Borrador de LinkedIn",
        imagen=imagen,
        imagen_aspecto=_IMAGEN_ASPECTO_CARD,
        video=video,
        video_aspecto=_IMAGEN_ASPECTO_CARD,
        cuerpo=cuerpo,
        botones=botones,
    ).model_dump(mode="json")
