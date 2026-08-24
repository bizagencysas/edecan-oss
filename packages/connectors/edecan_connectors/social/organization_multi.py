"""Publicador multi-red para una organización configurada por el operador.

Cuando el usuario toca "Aprobar y publicar", este módulo publica el mismo
post y recurso en las redes configuradas:
LinkedIn (página), Instagram (business), Facebook (página), Threads y X.

Cada red tiene su propia forma de API (single-shot, 2 pasos, 3 pasos). La
función despachadora `publish_organization_all_networks` las llama en paralelo
y devuelve un dict de resultados por red.

Las credenciales se leen de configuración explícita. Ningún valor secreto ni
ruta de otra instalación vive en este archivo.

URLs públicas: Instagram, Facebook y Threads exigen URLs PÚBLICAS de
imagen/video. Este módulo recibe una función `make_public_url(file_id)` que
genera URLs firmadas y temporalmente públicas servidas por el backend a
través del túnel. LinkedIn y X suben bytes directamente (no necesitan URL
pública).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import os
import secrets
import time
from urllib.parse import quote as _urllib_quote

import httpx

logger = logging.getLogger(__name__)

_BRAND = "Acme"
_ALL_NETWORKS = ("linkedin", "instagram", "facebook", "threads", "x")
_X_IMAGE_MAX = 5 * 1024 * 1024  # 5 MB, límite del endpoint simple de X


def _dcfg(name: str, default: str = "") -> str:
    """Lee una variable de la organización desde el entorno del operador."""
    return str(os.getenv(name, default) or "").strip()


def _ok_cfg(name: str) -> bool:
    return bool(_dcfg(name))


def _persist_x_tokens(*, access_token: str, refresh_token: str) -> None:
    """Informa la rotación; la persistencia pertenece al gestor de secretos."""
    logger.warning(
        "X rotó sus tokens; persístelos en el gestor de secretos (access=%s, refresh=%s).",
        bool(access_token),
        bool(refresh_token),
    )


def _http_error(payload) -> str:
    if isinstance(payload, dict):
        err = payload.get("error") or payload.get("message") or payload.get("errors")
        if isinstance(err, dict):
            return str(err.get("message") or err.get("detail") or err)[:180]
        if isinstance(err, list) and err:
            return str(err[0].get("detail") if isinstance(err[0], dict) else err[0])[:180]
        if err:
            return str(err)[:180]
        detail = payload.get("detail") or payload.get("title")
        if detail:
            return str(detail)[:220]
    return "error sin detalle"


# ---------------------------------------------------------------------------
# Meta (Instagram + Facebook) — comparten token de página
# ---------------------------------------------------------------------------


async def _meta_assets() -> tuple[dict | None, str | None]:
    """Resuelve el page_token y el ig_id de Acme desde la Graph API.

    Porte de `_meta_assets` de REFERENCIA (`organization_social.py:1609`). Llama a
    `me/accounts` con el token de usuario, encuentra la página por su PAGE_ID
    y de ahí saca el page_token y el IG_ID conectado a esa página.
    """
    token = _dcfg("ORGANIZATION_META_ACCESS_TOKEN")
    page_id = _dcfg("ORGANIZATION_META_PAGE_ID")
    ig_id = _dcfg("ORGANIZATION_META_IG_ID")
    if not token:
        return None, "falta ORGANIZATION_META_ACCESS_TOKEN"
    if not page_id:
        return None, "falta ORGANIZATION_META_PAGE_ID"
    page_token = token
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            resp = await c.get(
                "https://graph.facebook.com/v21.0/me/accounts",
                headers={"Authorization": f"Bearer {token}"},
                params={"fields": "id,name,access_token,instagram_business_account"},
            )
            data = resp.json()
            for p in data.get("data", []) or []:
                if str(p.get("id")) == str(page_id):
                    page_token = p.get("access_token") or token
                    if not ig_id:
                        ig_id = str((p.get("instagram_business_account") or {}).get("id") or "")
                    break
            if page_id and not ig_id:
                resp2 = await c.get(
                    f"https://graph.facebook.com/v21.0/{page_id}",
                    headers={"Authorization": f"Bearer {page_token}"},
                    params={"fields": "instagram_business_account"},
                )
                ig_id = str((resp2.json().get("instagram_business_account") or {}).get("id") or "")
    except Exception as e:
        return None, f"error resolviendo assets de Meta: {str(e)[:120]}"
    return {"page_id": page_id, "ig_id": ig_id, "page_token": page_token}, None


async def _ig_wait(
    http: httpx.AsyncClient, cid: str, token: str, tries: int = 30, delay: int = 4
) -> str | None:
    """Poll a {creation_id}?fields=status_code hasta FINISHED o ERROR."""
    for _ in range(tries):
        r = await http.get(
            f"https://graph.facebook.com/v21.0/{cid}",
            headers={"Authorization": f"Bearer {token}"},
            params={"fields": "status_code"},
        )
        code = (r.json() or {}).get("status_code")
        if code == "FINISHED":
            return None
        if code == "ERROR":
            return "Instagram reportó ERROR procesando el media"
        await asyncio.sleep(delay)
    return "Instagram tardó demasiado en procesar el media"


async def _publish_instagram(
    http: httpx.AsyncClient,
    asset: dict,
    video_url: str,
    image_url: str,
    caption: str,
) -> dict:
    """Publica en Instagram Business (REELS si hay video, foto si no).

    Porte de `_publish_instagram` de REFERENCIA. 2 pasos + espera: crear
    contenedor → poll → publicar.
    """
    ig_id = asset.get("ig_id")
    token = asset.get("page_token")
    if not ig_id:
        return {"ok": False, "error": "Acme no tiene ORGANIZATION_META_IG_ID conectado"}
    url = video_url or image_url
    if not url:
        return {"ok": False, "error": "Instagram necesita una URL pública"}
    try:
        if video_url:
            cont = await http.post(
                f"https://graph.facebook.com/v21.0/{ig_id}/media",
                headers={"Authorization": f"Bearer {token}"},
                data={"media_type": "REELS", "video_url": url, "caption": caption[:2200]},
            )
            tries, delay = 30, 4
        else:
            cont = await http.post(
                f"https://graph.facebook.com/v21.0/{ig_id}/media",
                headers={"Authorization": f"Bearer {token}"},
                data={"image_url": url, "caption": caption[:2200]},
            )
            tries, delay = 18, 2
        cont_j = cont.json()
        cid = cont_j.get("id")
        if not cid:
            return {"ok": False, "error": _http_error(cont_j)}
        err = await _ig_wait(http, cid, token, tries=tries, delay=delay)
        if err:
            return {"ok": False, "error": err}
        pub = await http.post(
            f"https://graph.facebook.com/v21.0/{ig_id}/media_publish",
            headers={"Authorization": f"Bearer {token}"},
            data={"creation_id": cid},
        )
        pub_j = pub.json()
        pid = pub_j.get("id")
        return {"ok": bool(pid), "media_id": pid, "error": None if pid else _http_error(pub_j)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:180]}


async def _publish_facebook(
    http: httpx.AsyncClient,
    asset: dict,
    video_url: str,
    image_url: str,
    caption: str,
) -> dict:
    """Publica en la página de Facebook (video si hay, foto si no, texto si nada).

    Porte de `_publish_facebook` de REFERENCIA. 1 paso.
    """
    page_id = asset.get("page_id")
    token = asset.get("page_token")
    if not page_id:
        return {"ok": False, "error": "Acme no tiene ORGANIZATION_META_PAGE_ID"}
    try:
        if video_url:
            res = await http.post(
                f"https://graph.facebook.com/v21.0/{page_id}/videos",
                headers={"Authorization": f"Bearer {token}"},
                data={"file_url": video_url, "description": caption[:5000]},
            )
        elif image_url:
            res = await http.post(
                f"https://graph.facebook.com/v21.0/{page_id}/photos",
                headers={"Authorization": f"Bearer {token}"},
                data={"url": image_url, "caption": caption[:5000]},
            )
        else:
            res = await http.post(
                f"https://graph.facebook.com/v21.0/{page_id}/feed",
                headers={"Authorization": f"Bearer {token}"},
                data={"message": caption[:5000]},
            )
        res_j = res.json()
        pid = res_j.get("id") or res_j.get("post_id")
        return {"ok": bool(pid), "post_id": pid, "error": None if pid else _http_error(res_j)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:180]}


# ---------------------------------------------------------------------------
# Threads — API propia (graph.threads.net)
# ---------------------------------------------------------------------------


async def _threads_wait(
    http: httpx.AsyncClient, cid: str, token: str, tries: int = 20, delay: int = 2
) -> str | None:
    for _ in range(tries):
        r = await http.get(
            f"https://graph.threads.net/v1.0/{cid}",
            params={"fields": "status", "access_token": token},
        )
        code = (r.json() or {}).get("status")
        if code == "FINISHED":
            return None
        if code == "ERROR":
            return "Threads reportó ERROR procesando el media"
        await asyncio.sleep(delay)
    return "Threads tardó demasiado en procesar el media"


async def _publish_threads(
    http: httpx.AsyncClient,
    image_url: str,
    caption: str,
) -> dict:
    """Publica en Threads (@organization.app). 2 pasos + espera.

    Threads NO soporta video (solo IMAGE). Si hay video, cae al poster PNG.
    Porte de `_threads_publish` de REFERENCIA.
    """
    token = _dcfg("ORGANIZATION_THREADS_ACCESS_TOKEN")
    user_id = _dcfg("ORGANIZATION_THREADS_USER_ID")
    if not token or not user_id:
        return {
            "ok": False,
            "no_configurado": True,
            "error": "falta ORGANIZATION_THREADS_ACCESS_TOKEN o ORGANIZATION_THREADS_USER_ID",
        }
    if not image_url:
        return {"ok": False, "error": "Threads necesita una URL pública de imagen"}
    text = (caption or "").strip()
    if len(text) > 500:
        text = text[:497].rstrip() + "..."
    try:
        cont = await http.post(
            f"https://graph.threads.net/v1.0/{user_id}/threads",
            params={
                "media_type": "IMAGE",
                "image_url": image_url,
                "text": text,
                "access_token": token,
            },
        )
        cont_j = cont.json()
        cid = cont_j.get("id")
        if not cid:
            return {"ok": False, "error": _http_error(cont_j)}
        err = await _threads_wait(http, cid, token)
        if err:
            return {"ok": False, "error": err}
        pub = await http.post(
            f"https://graph.threads.net/v1.0/{user_id}/threads_publish",
            params={"creation_id": cid, "access_token": token},
        )
        pub_j = pub.json()
        pid = pub_j.get("id")
        return {"ok": bool(pid), "post_id": pid, "error": None if pid else _http_error(pub_j)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:180]}


# ---------------------------------------------------------------------------
# X (Twitter) — OAuth2 para texto + OAuth 1.0a para media
# ---------------------------------------------------------------------------


async def _x_refresh_token() -> str:
    """Refresca el token OAuth2 de X y persiste el nuevo refresh_token.

    X usa refresh tokens rotativos: cada refresh invalida el anterior y
    devuelve uno nuevo. Si no se persiste, el SIGUIENTE publish falla
    porque el refresh token viejo ya no sirve. Porte del patrón de
    REFERENCIA (``organization_social.py:1987`` y ``x_personal.py:614``).
    """
    refresh = _dcfg("ORGANIZATION_X_OAUTH2_REFRESH_TOKEN")
    client_id = _dcfg("ORGANIZATION_X_CLIENT_ID")
    client_secret = _dcfg("ORGANIZATION_X_CLIENT_SECRET")
    if not (refresh and client_id):
        return ""
    headers: dict[str, str] = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {"grant_type": "refresh_token", "refresh_token": refresh, "client_id": client_id}
    if client_secret:
        raw = f"{client_id}:{client_secret}".encode()
        headers["Authorization"] = "Basic " + base64.b64encode(raw).decode("ascii")
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post("https://api.x.com/2/oauth2/token", headers=headers, data=data)
            j = r.json()
        access = j.get("access_token")
        new_refresh = j.get("refresh_token")
        if access:
            _persist_x_tokens(access_token=access, refresh_token=new_refresh or "")
            logger.info("Token OAuth2 de X refrescado y persistido correctamente.")
            return access
    except Exception:
        pass
    return ""


def _x_oauth1_header(method: str, url: str) -> str:
    """Firma OAuth 1.0a (HMAC-SHA1) para el upload de medios de X.

    El Bearer OAuth2 da 403 en el endpoint de media. Porte exacto de
    `_x_oauth1_header` de REFERENCIA.
    """
    ck = _dcfg("ORGANIZATION_X_CONSUMER_KEY")
    cs = _dcfg("ORGANIZATION_X_CONSUMER_SECRET")
    at = _dcfg("ORGANIZATION_X_ACCESS_TOKEN")
    ats = _dcfg("ORGANIZATION_X_ACCESS_TOKEN_SECRET")
    params = {
        "oauth_consumer_key": ck,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": at,
        "oauth_version": "1.0",
    }

    def enc(s):
        return _urllib_quote(str(s), safe="")

    param_str = "&".join(f"{enc(k)}={enc(v)}" for k, v in sorted(params.items()))
    base_str = f"{method.upper()}&{enc(url)}&{enc(param_str)}"
    signing_key = f"{enc(cs)}&{enc(ats)}"
    sig = base64.b64encode(
        hmac.new(signing_key.encode(), base_str.encode(), hashlib.sha1).digest()
    ).decode()
    params["oauth_signature"] = sig
    return "OAuth " + ", ".join(f'{enc(k)}="{enc(v)}"' for k, v in sorted(params.items()))


async def _x_upload(
    http: httpx.AsyncClient, image_bytes: bytes, image_mime: str
) -> tuple[str, str | None]:
    """Sube una imagen a X vía OAuth 1.0a. Devuelve (media_id, error)."""
    if not image_bytes:
        return "", "no hay imagen para subir a X"
    if len(image_bytes) > _X_IMAGE_MAX:
        return "", "la imagen supera el límite simple de X (5 MB)"
    if not _ok_cfg("ORGANIZATION_X_CONSUMER_KEY") or not _ok_cfg(
        "ORGANIZATION_X_ACCESS_TOKEN_SECRET"
    ):
        return "", "faltan credenciales OAuth 1.0a de X"
    upload_url = "https://upload.twitter.com/1.1/media/upload.json"
    header = _x_oauth1_header("POST", upload_url)
    ext = "png" if "png" in image_mime else ("gif" if "gif" in image_mime else "jpg")
    try:
        r = await http.post(
            upload_url,
            headers={"Authorization": header},
            files={"media": (f"media.{ext}", image_bytes, image_mime)},
        )
        j = r.json()
    except Exception as e:
        return "", str(e)[:160]
    mid = str(j.get("media_id_string") or j.get("media_id") or "")
    return mid, None if mid else _http_error(j)


async def _publish_x(
    http: httpx.AsyncClient,
    image_bytes: bytes,
    image_mime: str,
    caption: str,
) -> dict:
    """Publica un tweet en X con imagen. Porte de `_x_publish` de REFERENCIA."""
    token = _dcfg("ORGANIZATION_X_OAUTH2_ACCESS_TOKEN")
    if not token:
        return {
            "ok": False,
            "no_configurado": True,
            "error": "falta ORGANIZATION_X_OAUTH2_ACCESS_TOKEN",
        }

    async def attempt(tok: str) -> tuple[int, dict]:
        media_id, media_err = (
            await _x_upload(http, image_bytes, image_mime) if image_bytes else ("", None)
        )
        if media_err:
            return 400, {"error": media_err}
        text = (caption or "").strip()
        if len(text) > 275:
            text = text[:272].rstrip() + "..."
        body: dict = {"text": text or "Acme", "made_with_ai": bool(media_id)}
        if media_id:
            body["media"] = {"media_ids": [media_id]}
        r = await http.post(
            "https://api.x.com/2/tweets",
            headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
            json=body,
        )
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, {"text": r.text[:200]}

    status_code, payload = await attempt(token)
    if status_code == 401:
        new_token = await _x_refresh_token()
        if new_token:
            token = new_token
            status_code, payload = await attempt(token)
    tid = str((payload.get("data") or {}).get("id") or "")
    return {"ok": bool(tid), "tweet_id": tid, "error": None if tid else _http_error(payload)}


# ---------------------------------------------------------------------------
# Despachador principal
# ---------------------------------------------------------------------------


async def publish_organization_all_networks(
    *,
    text: str,
    image_bytes: bytes | None,
    image_mime: str,
    video_bytes: bytes | None,
    video_mime: str,
    image_file_id: str | None,
    video_file_id: str | None,
    make_public_url,
    linkedin_bundle,
    linkedin_org_urn: str | None,
) -> dict:
    """Despacha el post + video a las 5 redes de Acme en paralelo.

    ``make_public_url``: callable ``(file_id: str) -> str`` que genera una URL
    pública firmada y temporal (servida por el backend vía túnel). Se usa para
    IG, FB y Threads, que exigen URLs públicas. LinkedIn y X suben bytes
    directamente.

    ``linkedin_bundle``: un `TokenBundle` ya resuelto para LinkedIn. Si es
    ``None``, LinkedIn se salta con error de configuración.

    Devuelve un dict por red: ``{linkedin: {ok, post_id, ...}, instagram: {...}, ...}``.
    """

    from edecan_connectors.social.linkedin import create_post as _create_linkedin_post

    caption = (text or "").strip()
    image_mime = image_mime or "image/png"
    video_mime = video_mime or "video/mp4"

    # URLs públicas para IG, FB y Threads
    video_public_url = ""
    image_public_url = ""
    if video_file_id:
        video_public_url = make_public_url(video_file_id)
    if image_file_id:
        image_public_url = make_public_url(image_file_id)

    # IG/FB: video si hay, si no imagen. Threads: solo imagen.
    ig_video_url = video_public_url
    ig_image_url = image_public_url if not ig_video_url else ""
    fb_video_url = video_public_url
    fb_image_url = image_public_url if not fb_video_url else ""
    threads_image_url = image_public_url

    # X: solo imagen (poster PNG), no soporta video en el endpoint simple
    x_image_bytes = image_bytes or b""
    x_image_mime = image_mime

    async with httpx.AsyncClient(timeout=180.0) as http:
        # LinkedIn: reusa el conector existente (sube bytes, no necesita URL pública)
        async def _do_linkedin() -> dict:
            if linkedin_bundle is None:
                return {
                    "ok": False,
                    "no_configurado": True,
                    "error": "falta token de LinkedIn de Acme",
                }
            try:
                result = await _create_linkedin_post(
                    http,
                    linkedin_bundle,
                    text=caption,
                    image=image_bytes if image_bytes and not video_bytes else None,
                    image_content_type=image_mime,
                    video=video_bytes,
                    video_content_type=video_mime,
                    alt_text="",
                    org_urn=linkedin_org_urn,
                )
                return {
                    "ok": bool(result.get("id")),
                    "post_id": result.get("id"),
                    "verified": result.get("verified"),
                    "error": None if result.get("id") else "LinkedIn no devolvió id",
                }
            except Exception as e:
                return {"ok": False, "error": str(e)[:180]}

        # Meta assets: resolver UNA vez para IG y FB
        meta_asset: dict | None = None
        meta_err: str | None = None
        if _ok_cfg("ORGANIZATION_META_ACCESS_TOKEN"):
            meta_asset, meta_err = await _meta_assets()

        async def _do_instagram() -> dict:
            if meta_err:
                return {"ok": False, "error": meta_err}
            if not meta_asset:
                return {"ok": False, "no_configurado": True, "error": "falta configuración de Meta"}
            return await _publish_instagram(http, meta_asset, ig_video_url, ig_image_url, caption)

        async def _do_facebook() -> dict:
            if meta_err:
                return {"ok": False, "error": meta_err}
            if not meta_asset:
                return {"ok": False, "no_configurado": True, "error": "falta configuración de Meta"}
            return await _publish_facebook(http, meta_asset, fb_video_url, fb_image_url, caption)

        async def _do_threads() -> dict:
            return await _publish_threads(http, threads_image_url, caption)

        async def _do_x() -> dict:
            return await _publish_x(http, x_image_bytes, x_image_mime, caption)

        results = await asyncio.gather(
            _do_linkedin(),
            _do_instagram(),
            _do_facebook(),
            _do_threads(),
            _do_x(),
            return_exceptions=True,
        )

    redes = _ALL_NETWORKS
    out: dict[str, dict] = {}
    # `results` nace del gather fijo de las cinco redes; `strict` convierte una
    # deriva accidental entre ambas listas en un fallo explícito.
    for red, res in zip(redes, results, strict=True):
        if isinstance(res, Exception):
            out[red] = {"ok": False, "error": str(res)[:180]}
        else:
            out[red] = res
    return out


def organization_networks_status() -> dict:
    """Estado de configuración de cada red sin exponer secretos."""
    return {
        "linkedin": {
            "configurado": _ok_cfg("ORGANIZATION_LINKEDIN_ACCESS_TOKEN")
            and _ok_cfg("ORGANIZATION_LINKEDIN_ORG_URN")
        },
        "instagram": {
            "configurado": _ok_cfg("ORGANIZATION_META_ACCESS_TOKEN")
            and _ok_cfg("ORGANIZATION_META_PAGE_ID")
            and _ok_cfg("ORGANIZATION_META_IG_ID")
        },
        "facebook": {
            "configurado": _ok_cfg("ORGANIZATION_META_ACCESS_TOKEN")
            and _ok_cfg("ORGANIZATION_META_PAGE_ID")
        },
        "threads": {
            "configurado": _ok_cfg("ORGANIZATION_THREADS_ACCESS_TOKEN")
            and _ok_cfg("ORGANIZATION_THREADS_USER_ID")
        },
        "x": {"configurado": _ok_cfg("ORGANIZATION_X_OAUTH2_ACCESS_TOKEN")},
    }
