"""Conector oficial de LinkedIn para identidad, imágenes y publicaciones.

Cada instalación registra su propia app en LinkedIn Developer Portal y usa
OAuth 2.0. Edecán nunca incluye un client_id, client_secret ni token
compartido. El acceso self-service necesita los productos ``Sign in with
LinkedIn using OpenID Connect`` y ``Share on LinkedIn``.

La publicación usa las APIs REST actuales:

* ``/v2/userinfo`` para identificar a la persona autorizada.
* ``/rest/organizationAcls`` (Community Management API) para descubrir las páginas de
  empresa que esa persona administra, si autorizó los scopes de organización.
* ``/rest/images?action=initializeUpload`` para subir una imagen.
* ``/rest/posts`` para crear el post.

``Linkedin-Version`` usa un default FIJO (``_DEFAULT_LINKEDIN_VERSION``, ver más abajo)
salvo que la instalación fije ``LINKEDIN_API_VERSION=YYYYMM``. ANTES este valor se derivaba
de ``datetime.now()`` (el mes en curso): eso es una bomba de tiempo -- el día 1 de cada mes
la instalación empieza a pedir sola, sin que nadie toque nada, una versión que LinkedIn quizás
no activó todavía, y publicar se rompe en silencio. Portado de REFERENCIA
(``features/organization_social.py:1917``, que la fija en ``"202606"`` por la misma razón).

Después de publicar, ``create_post`` relee el post (``GET /rest/posts/{id}``) antes de
reportar éxito -- ver su docstring para el porqué (un 2xx de LinkedIn no es prueba de que
el post exista).

Publicar en una PÁGINA de empresa (en vez del perfil personal) porta
``_linkedin_publish``/``_conectar_linkedin_organization`` de REFERENCIA
(``features/organization_social.py``): ``create_post`` acepta un ``org_urn`` opcional que,
si viene, se usa como ``author`` (y como ``owner`` al subir la imagen) en lugar del urn
de la persona autorizada, y ``get_organization_urns`` hace la misma consulta a
``organizationAcls`` que REFERENCIA para descubrir esas páginas. A diferencia de REFERENCIA
(que persiste el urn elegido en su propio ``.env``), este módulo no guarda nada: quien
llame a ``get_organization_urns`` durante el callback OAuth (``apps/api/edecan_api/
routers/connectors.py``) es responsable de persistir el resultado como
``organization_urn`` en los metadatos de la cuenta conectada -- hoy
``edecan_db.models.ConnectorAccount`` no tiene esa columna, así que esa persistencia
queda pendiente de un work package aparte (fuera del alcance de este archivo).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any, Literal
from urllib.parse import quote, urlparse

import httpx
from edecan_schemas import TokenBundle

from ..base import Connector, ConnectorError, OAuthSpec, build_authorize_url
from ._util import expires_at_from_seconds

logger = logging.getLogger(__name__)

AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
IMAGES_URL = "https://api.linkedin.com/rest/images"
POSTS_URL = "https://api.linkedin.com/rest/posts"
VIDEOS_URL = "https://api.linkedin.com/rest/videos"
ORGANIZATION_ACLS_URL = "https://api.linkedin.com/rest/organizationAcls"

# ``w_organization_social``/``rw_organization_admin`` habilitan publicar en PÁGINAS de
# empresa (Community Management API) además del perfil personal (``w_member_social``):
# el primero autoriza a publicar en nombre de una organización que la persona administra,
# el segundo a listar esas organizaciones vía ``get_organization_urns``/``organizationAcls``
# más abajo. LinkedIn exige ``rw_organization_admin`` (lectura-escritura) para consultar
# ``organizationAcls``; el ``r_organization_admin`` de solo lectura no basta. Ambos scopes
# son opcionales para el flujo de perfil personal existente -- una instalación que no los
# necesite simplemente nunca llama ``get_organization_urns`` ni pasa ``org_urn`` a
# ``create_post``.
SCOPES = [
    "openid",
    "profile",
    "email",
    "w_member_social",
    "w_organization_social",
    "rw_organization_admin",
]

LINKEDIN_OAUTH = OAuthSpec(
    auth_url=AUTH_URL,
    token_url=TOKEN_URL,
    scopes=SCOPES,
    pkce=False,
)

_VERSION_RE = re.compile(r"^\d{6}$")
_UPLOAD_HOSTS = frozenset({"api.linkedin.com", "www.linkedin.com", "media.licdn.com"})

# Default FIJO y conocido -- NO se deriva de `datetime.now()` (ver el docstring del
# módulo: eso era una bomba de tiempo que rompía la publicación solo, cada 1° de mes).
# Mismo valor que REFERENCIA (`features/organization_social.py:1917`) porque es la versión que
# ya se verificó en producción. Revisar y subir este valor a mano cuando LinkedIn saque
# una versión posterior que la instalación quiera adoptar (no hay automatismo a propósito);
# mientras tanto, una instalación puede fijar otra con `LINKEDIN_API_VERSION=YYYYMM`.
_DEFAULT_LINKEDIN_VERSION = "202606"


def linkedin_version() -> str:
    configured = os.getenv("LINKEDIN_API_VERSION", "").strip()
    if configured:
        if not _VERSION_RE.fullmatch(configured):
            raise ConnectorError("LINKEDIN_API_VERSION debe tener formato YYYYMM.")
        return configured
    return _DEFAULT_LINKEDIN_VERSION


def _headers(bundle: TokenBundle, *, json: bool = True) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {bundle.access_token}",
        "Linkedin-Version": linkedin_version(),
        "X-Restli-Protocol-Version": "2.0.0",
    }
    if json:
        headers["Content-Type"] = "application/json"
    return headers


def _provider_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:800] or f"HTTP {response.status_code}"
    if isinstance(payload, dict):
        return str(
            payload.get("message")
            or payload.get("error_description")
            or payload.get("error")
            or payload
        )[:800]
    return str(payload)[:800]


def _raise_for_linkedin_error(response: httpx.Response, operation: str) -> None:
    if response.status_code >= 400:
        raise ConnectorError(
            f"LinkedIn rechazó {operation} ({response.status_code}): {_provider_detail(response)}"
        )


def _parse_bundle(payload: dict[str, Any]) -> TokenBundle:
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise ConnectorError("La respuesta OAuth de LinkedIn no incluyó access_token.")
    scope_value = payload.get("scope")
    return TokenBundle(
        access_token=access_token,
        refresh_token=payload.get("refresh_token"),
        expires_at=expires_at_from_seconds(payload.get("expires_in")),
        scopes=scope_value.split() if isinstance(scope_value, str) else SCOPES,
        token_type=str(payload.get("token_type") or "bearer"),
    )


class LinkedInConnector(Connector):
    key = "linkedin"
    display_name = "LinkedIn"
    oauth = LINKEDIN_OAUTH

    def auth_url(self, redirect_uri: str, state: str, client_id: str) -> str:
        return build_authorize_url(self.oauth, client_id, redirect_uri, state)

    async def exchange_code(
        self,
        code: str,
        redirect_uri: str,
        http: httpx.AsyncClient,
        client_id: str,
        client_secret: str | None,
        code_verifier: str | None = None,
    ) -> TokenBundle:
        del code_verifier
        if not client_secret:
            raise ConnectorError("LinkedIn requiere el Client secret de la app.")
        response = await http.post(
            self.oauth.token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={"Accept": "application/json"},
        )
        _raise_for_linkedin_error(response, "la autorización")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ConnectorError("LinkedIn devolvió una respuesta OAuth ilegible.") from exc
        if not isinstance(payload, dict):
            raise ConnectorError("LinkedIn devolvió una respuesta OAuth inesperada.")
        return _parse_bundle(payload)

    async def refresh(
        self,
        bundle: TokenBundle,
        http: httpx.AsyncClient,
        client_id: str,
        client_secret: str | None,
    ) -> TokenBundle:
        if not bundle.refresh_token:
            raise ConnectorError(
                "Esta cuenta de LinkedIn debe autorizarse de nuevo cuando venza el token. "
                "LinkedIn solo entrega refresh tokens a programas aprobados."
            )
        if not client_secret:
            raise ConnectorError("LinkedIn requiere el Client secret de la app.")
        response = await http.post(
            self.oauth.token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": bundle.refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={"Accept": "application/json"},
        )
        _raise_for_linkedin_error(response, "la renovación")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ConnectorError("LinkedIn devolvió una renovación ilegible.") from exc
        if not isinstance(payload, dict):
            raise ConnectorError("LinkedIn devolvió una renovación inesperada.")
        refreshed = _parse_bundle(payload)
        if not refreshed.refresh_token:
            refreshed = refreshed.model_copy(update={"refresh_token": bundle.refresh_token})
        return refreshed


async def get_me(http: httpx.AsyncClient, bundle: TokenBundle) -> dict[str, Any]:
    response = await http.get(USERINFO_URL, headers=_headers(bundle, json=False))
    _raise_for_linkedin_error(response, "la lectura del perfil")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ConnectorError("LinkedIn devolvió un perfil ilegible.") from exc
    if not isinstance(payload, dict) or not payload.get("sub"):
        raise ConnectorError("LinkedIn no devolvió el identificador del perfil.")
    return payload


def person_urn(profile: dict[str, Any]) -> str:
    identifier = str(profile.get("sub") or "").strip()
    if not identifier:
        raise ConnectorError("No se pudo identificar a la persona autorizada en LinkedIn.")
    return f"urn:li:person:{identifier}"


async def get_organization_urns(http: httpx.AsyncClient, bundle: TokenBundle) -> list[str]:
    """URNs (``urn:li:organization:...``) de las páginas de empresa donde la persona
    autorizada es ADMINISTRATOR aprobado -- Community Management API, ``GET
    /rest/organizationAcls?q=roleAssignee&role=ADMINISTRATOR&state=APPROVED``, la misma
    llamada que usa el propio LinkedIn para saber a qué páginas puede publicar una cuenta.

    Requiere que ``bundle`` traiga el scope ``r_organization_admin`` (ver ``SCOPES``); si
    la instalación no lo autorizó, LinkedIn devuelve una lista vacía o un 403 (propagado
    como ``ConnectorError`` por ``_raise_for_linkedin_error``).

    Portado de REFERENCIA (``features/organization_social.py::_conectar_linkedin_organization``): no
    persiste nada -- quien llame esto (p. ej. el callback OAuth de
    ``apps/api/edecan_api/routers/connectors.py``) decide cómo guardar el resultado como
    ``organization_urn`` en los metadatos de la cuenta conectada.

    LinkedIn no es consistente entre finders: unos elementos devuelven
    ``organizationTarget``, otros ``organization`` -- se aceptan ambos.
    """
    response = await http.get(
        ORGANIZATION_ACLS_URL,
        headers=_headers(bundle, json=False),
        params={"q": "roleAssignee", "role": "ADMINISTRATOR", "state": "APPROVED"},
    )
    _raise_for_linkedin_error(response, "la consulta de páginas administradas")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ConnectorError("LinkedIn devolvió páginas administradas ilegibles.") from exc
    elements = payload.get("elements") if isinstance(payload, dict) else None
    urns: list[str] = []
    for element in elements or []:
        if not isinstance(element, dict):
            continue
        urn = str(element.get("organizationTarget") or element.get("organization") or "").strip()
        if urn and urn not in urns:
            urns.append(urn)
    return urns


def _validate_upload_url(url: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in _UPLOAD_HOSTS:
        raise ConnectorError("LinkedIn devolvió una URL de carga no confiable.")
    if parsed.username or parsed.password:
        raise ConnectorError("LinkedIn devolvió una URL de carga inválida.")


async def upload_image(
    http: httpx.AsyncClient,
    bundle: TokenBundle,
    *,
    owner: str,
    content: bytes,
    content_type: str = "image/png",
) -> str:
    response = await http.post(
        f"{IMAGES_URL}?action=initializeUpload",
        headers=_headers(bundle),
        json={"initializeUploadRequest": {"owner": owner}},
    )
    _raise_for_linkedin_error(response, "la preparación de la imagen")
    try:
        value = response.json()["value"]
        upload_url = str(value["uploadUrl"])
        image_urn = str(value["image"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ConnectorError("LinkedIn no devolvió los datos para cargar la imagen.") from exc
    _validate_upload_url(upload_url)
    uploaded = await http.put(
        upload_url,
        content=content,
        headers={
            "Authorization": f"Bearer {bundle.access_token}",
            "Content-Type": content_type,
        },
    )
    _raise_for_linkedin_error(uploaded, "la carga de la imagen")
    await _esperar_imagen_lista(http, bundle, image_urn)
    return image_urn


# La imagen tarda ~2s en pasar de WAITING_UPLOAD a AVAILABLE (medido en vivo el 06-ago
# con una imagen de la ORGANIZACIÓN: t=0.3s WAITING_UPLOAD, t=1.7s AVAILABLE — y el
# estado SÍ es consultable para imágenes de página, sin 403). Adjuntarla antes produce
# el fantasma documentado el 31-jul: `POST /rest/posts` responde 201 + id válido y el
# post NUNCA aparece. Una espera anterior (tope 30s) se retiró el 03-ago porque colgaba
# el botón — y tenía el bug de SALTARSE la protección si el estado no se podía leer.
# Esta versión: tope corto, y si el estado no es legible, pausa fija en vez de saltar.
_IMAGEN_LISTA = "AVAILABLE"
_IMAGEN_SONDEO_SEGUNDOS = 0.5
_IMAGEN_TOPE_SEGUNDOS = 15.0
_IMAGEN_PAUSA_CIEGA_SEGUNDOS = 5.0


async def _esperar_imagen_lista(
    http: httpx.AsyncClient, bundle: TokenBundle, image_urn: str
) -> None:
    url = f"{IMAGES_URL}/{quote(image_urn, safe='')}"
    espera = 0.0
    while espera < _IMAGEN_TOPE_SEGUNDOS:
        try:
            respuesta = await http.get(url, headers=_headers(bundle, json=False))
        except httpx.HTTPError:
            respuesta = None
        estado = ""
        if respuesta is not None and respuesta.status_code < 400:
            try:
                cuerpo = respuesta.json()
            except ValueError:
                cuerpo = {}
            estado = str(
                cuerpo.get("status") or (cuerpo.get("value") or {}).get("status") or ""
            ).upper()
        if estado == _IMAGEN_LISTA:
            return
        if respuesta is None or respuesta.status_code >= 400 or not estado:
            # Estado ilegible: pausa fija y se sigue. NUNCA saltar sin esperar — ese
            # era el bug de la versión del 31-jul (publicaba en plena carrera).
            await asyncio.sleep(_IMAGEN_PAUSA_CIEGA_SEGUNDOS)
            return
        await asyncio.sleep(_IMAGEN_SONDEO_SEGUNDOS)
        espera += _IMAGEN_SONDEO_SEGUNDOS
    logger.warning(
        "linkedin: la imagen %s no llegó a %s en %.0fs; se adjunta igual.",
        image_urn,
        _IMAGEN_LISTA,
        _IMAGEN_TOPE_SEGUNDOS,
    )


# ── Video (Videos API: initializeUpload → upload → finalizeUpload → poll) ──────
# LinkedIn publica video nativo (no un .mp4 adjunto como documento): hay que subirlo
# a /rest/videos, esperar que procese, y referenciar el URN en content.media.id del
# post — misma forma que la imagen, solo cambia el urn:li:video: por urn:li:image:.
# La subida es single-part para archivos pequeños (nuestro MP4 ~2-5 MB) o multipart
# si LinkedIn responde uploadInstructions (archivos grandes).
_VIDEO_LISTO = "AVAILABLE"
_VIDEO_SONDEO_SEGUNDOS = 2.0
_VIDEO_TOPE_SEGUNDOS = 120.0
_VIDEO_PAUSA_CIEGA_SEGUNDOS = 15.0


def _validate_video_upload_url(url: str) -> None:
    """Valida la URL de carga de video: https y host controlado por LinkedIn.

    Más permisivo que ``_validate_upload_url`` (que pega a ``media.licdn.com``):
    los videos suben a media-store de LinkedIn (``*.licdn.com`` / ``*.linkedin.com``),
    hosts que no están en ``_UPLOAD_HOSTS`` pero son de LinkedIn. No se aceptan
    otros dominios porque PUTear el MP4 a un host arbitrario filtraría el video.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (
        host.endswith(".licdn.com") or host.endswith(".linkedin.com")
    ):
        raise ConnectorError("LinkedIn devolvió una URL de carga de video no confiable.")
    if parsed.username or parsed.password:
        raise ConnectorError("LinkedIn devolvió una URL de carga de video inválida.")


async def upload_video(
    http: httpx.AsyncClient,
    bundle: TokenBundle,
    *,
    owner: str,
    content: bytes,
    content_type: str = "video/mp4",
) -> str:
    """Sube un MP4 a LinkedIn (Videos API) y devuelve el ``urn:li:video:`` listo
    para adjuntar a un post. Levanta ``ConnectorError`` en cualquier paso real."""
    if not content_type.startswith("video/"):
        raise ConnectorError("El contenido a subir no es un video.")
    size = len(content)
    response = await http.post(
        f"{VIDEOS_URL}?action=initializeUpload",
        headers=_headers(bundle),
        json={
            "initializeUploadRequest": {
                "owner": owner,
                "fileSizeBytes": size,
            }
        },
    )
    _raise_for_linkedin_error(response, "la preparación del video")
    try:
        value = response.json()["value"]
        video_urn = str(value["video"])
        upload_token = str(value.get("uploadToken") or "")
    except (KeyError, TypeError, ValueError) as exc:
        raise ConnectorError("LinkedIn no devolvió los datos para cargar el video.") from exc
    if not upload_token:
        # Algunos respuestos omiten uploadToken cuando no hace falta finalize;
        # lo dejamos vacío solo si LinkedIn lo dejó así, no si fallamos parseando.
        logger.warning("linkedin: initializeUpload del video no trajo uploadToken.")

    upload_headers = {
        "Authorization": f"Bearer {bundle.access_token}",
        "Content-Type": "application/octet-stream",
    }
    # Cada parte subida devuelve en el header ``etag`` el ID firmado del chunk
    # (``/ambry-video/signedId/...``); LinkedIn exige esos IDs en
    # ``finalizeUpload.uploadedPartIds``. Sin ellos responde 400
    # "All chunks IDs must be signed". Medido en vivo con la version 202606.
    part_ids: list[str] = []
    if "uploadUrl" in value:
        upload_url = str(value["uploadUrl"])
        _validate_video_upload_url(upload_url)
        uploaded = await http.put(upload_url, content=content, headers=upload_headers)
        _raise_for_linkedin_error(uploaded, "la carga del video")
        etag = uploaded.headers.get("etag")
        if etag:
            part_ids.append(etag)
    else:
        instructions = value.get("uploadInstructions") or []
        if not instructions:
            raise ConnectorError("LinkedIn no devolvió instrucciones de carga de video.")
        # LinkedIn devuelve ``firstByte``/``lastByte`` planos (no anidados en
        # ``byteRange``), medido en vivo con la version 202606.
        for instr in sorted(instructions, key=lambda x: x["firstByte"]):
            part_url = str(instr["uploadUrl"])
            _validate_video_upload_url(part_url)
            chunk = content[instr["firstByte"] : instr["lastByte"] + 1]
            uploaded = await http.put(part_url, content=chunk, headers=upload_headers)
            _raise_for_linkedin_error(uploaded, "la carga del video (multiparte)")
            etag = uploaded.headers.get("etag")
            if etag:
                part_ids.append(etag)

    if not part_ids:
        raise ConnectorError("La subida del video no devolvió identificadores de chunk.")

    finalize = await http.post(
        f"{VIDEOS_URL}?action=finalizeUpload",
        headers=_headers(bundle),
        json={
            "finalizeUploadRequest": {
                "video": video_urn,
                "uploadToken": upload_token,
                "uploadedPartIds": part_ids,
            }
        },
    )
    _raise_for_linkedin_error(finalize, "la finalización del video")
    await _esperar_video_listo(http, bundle, video_urn)
    return video_urn


async def _esperar_video_listo(
    http: httpx.AsyncClient, bundle: TokenBundle, video_urn: str
) -> None:
    """Sondea ``GET /rest/videos/{urn}`` hasta que ``available`` sea true (tope 120s).

    Para videos de PÁGINA el estado SÍ es legible (medido con imágenes análogas); si
    no se puede leer, pausa fija en vez de saltar — misma regla que la imagen: adjuntar
    un video antes de procesarse publica un post que aparece sin video."""
    url = f"{VIDEOS_URL}/{quote(video_urn, safe='')}"
    espera = 0.0
    while espera < _VIDEO_TOPE_SEGUNDOS:
        try:
            respuesta = await http.get(url, headers=_headers(bundle, json=False))
        except httpx.HTTPError:
            respuesta = None
        estado = ""
        disponible = None
        if respuesta is not None and respuesta.status_code < 400:
            try:
                cuerpo = respuesta.json()
            except ValueError:
                cuerpo = {}
            estado = str(cuerpo.get("status") or "").upper()
            disponible = cuerpo.get("available")
        if disponible is True or estado == _VIDEO_LISTO:
            return
        if respuesta is None or respuesta.status_code >= 400 or (not estado and disponible is None):
            await asyncio.sleep(_VIDEO_PAUSA_CIEGA_SEGUNDOS)
            return
        await asyncio.sleep(_VIDEO_SONDEO_SEGUNDOS)
        espera += _VIDEO_SONDEO_SEGUNDOS
    logger.warning(
        "linkedin: el video %s no llegó a %s en %.0fs; se adjunta igual.",
        video_urn,
        _VIDEO_LISTO,
        _VIDEO_TOPE_SEGUNDOS,
    )


# El caso real que motivó esto (ver docstring del módulo): con el token de organización
# equivocado (scope de persona, no de página), LinkedIn respondía HTTP 201 + un
# `x-restli-id` con forma válida al publicar como PÁGINA, y no creaba nada -- un 2xx
# indistinguible de un éxito. `_verify_post` relee el post recién creado para separar
# un éxito real de esa mentira silenciosa. Devuelve tres estados, no dos, porque la
# relectura tiene su propio modo de fallo independiente de si el post existe:
#
# * "confirmed": el ``GET`` devolvió el post. Éxito real, verificado.
# * "not_found": el ``GET`` respondió 404. El ``POST`` mintió -- no se publicó nada.
# * "unknown": el ``GET`` falló por otra razón (permiso, red, lo que sea). NO se sabe
#   si el post existe o no -- me pasó en vivo: 403 ACCESS_DENIED con un token que SÍ
#   había publicado, solo que sin scope de LECTURA. Este estado nunca debe reportarse
#   como éxito llano; quien llame decide cómo comunicarlo (nunca un "✅" pelado).
_PostVerification = Literal["confirmed", "not_found", "unknown"]


# NO existe (ya no) un "oráculo de estadísticas". El 02-ago se cableó
# `organizationalEntityShareStatistics` como detector de existencia, calibrado con un
# post VIEJO (200 + elements) contra uno fresco (400). El 06-ago se midió el caso que
# tumba esa calibración: un post de página VISIBLE en el feed, con 18+ horas de edad,
# seguía devolviendo 400 "Unable to get activityIds" -- las estadísticas no discriminan
# existencia, discriminan (como mucho) antigüedad/actividad acumulada. El "oráculo"
# convertía publicaciones REALES en errores 502 de "no se publicó nada", dejaba el
# borrador re-aprobable y produjo posts duplicados en la página. Para páginas, sin el
# permiso partner de lectura, el único estado honesto alcanzable es "unknown".


async def verify_post(
    http: httpx.AsyncClient,
    bundle: TokenBundle,
    post_id: str,
    org_urn: str | None = None,
) -> tuple[_PostVerification, str]:
    """Relee ``GET /rest/posts/{id}`` para confirmar que el post recién creado existe.

    Para PÁGINAS (``org_urn``) la relectura directa está cerrada por permisos (403 de
    nivel partner) y NO hay camino alternativo confiable (ver el comentario de arriba
    sobre el oráculo de estadísticas retirado): el resultado será "unknown" y quien
    llame debe comunicarlo como "enviado, no confirmable" -- jamás como fracaso.

    ``org_urn`` se conserva en la firma por compatibilidad con los llamadores aunque ya
    no cambia el comportamiento.

    El segundo elemento de la tupla es un detalle legible para mostrarle a la persona
    cuando el estado no es ``"confirmed"`` -- vacío en ese caso porque no hace falta
    explicar nada."""

    del org_urn
    encoded_id = quote(post_id, safe="")
    url = f"{POSTS_URL}/{encoded_id}"
    try:
        response = await http.get(url, headers=_headers(bundle, json=False))
    except httpx.HTTPError as exc:
        return "unknown", f"No se pudo contactar a LinkedIn para confirmar el post ({exc})."
    if response.status_code == 200:
        return "confirmed", ""
    if response.status_code == 404:
        return "not_found", "LinkedIn respondió que el post no existe."
    if response.status_code in (401, 403):
        return "unknown", (
            f"LinkedIn no dio permiso para releer el post ({response.status_code}); "
            "puede que sí esté publicado y solo falte el scope de lectura."
        )
    return "unknown", (f"LinkedIn respondió {response.status_code} al intentar confirmar el post.")


async def create_post(
    http: httpx.AsyncClient,
    bundle: TokenBundle,
    *,
    text: str,
    image: bytes | None = None,
    image_content_type: str = "image/png",
    video: bytes | None = None,
    video_content_type: str = "video/mp4",
    video_title: str = "Acme",
    alt_text: str = "",
    org_urn: str | None = None,
) -> dict[str, Any]:
    """Crea el post y lo relee antes de darlo por publicado (ver ``verify_post``).

    Con ``org_urn`` (``urn:li:organization:...``, ver ``get_organization_urns``) publica
    como esa PÁGINA de empresa en vez del perfil personal: ``author`` pasa a ser
    ``org_urn`` (y también el ``owner`` que recibe ``upload_image`` para la imagen, si la
    hay) y se salta la llamada a ``/v2/userinfo`` -- no hace falta identificar a la
    persona para publicar como organización, igual que ``_linkedin_publish`` de REFERENCIA
    (``features/organization_social.py``).

    El resultado trae ``verified`` (``"confirmed"`` o ``"unknown"``, ver ``verify_post``)
    y ``verification_note`` (detalle legible, vacío si ``verified == "confirmed"``).
    Si LinkedIn no devuelve el ``x-restli-id`` del post, o si la relectura confirma que
    el post NO existe (``"not_found"``), esta función levanta ``ConnectorError`` -- eso
    es un fallo real de publicación, no algo para reportarle a la persona como éxito
    incierto. El caso de hoy que motivó todo esto (token con scope de persona publicando
    "como" una página: LinkedIn respondía 201 + id válido sin crear nada) cae exactamente
    en esta rama."""
    clean_text = text.strip()
    clean_text = re.sub(
        r"Descarga el nuevo buró de crédito alternativo moderno para Venezuela"
        r'\s+en:\s*"?www\.organization\.org"?',
        "example.org",
        clean_text,
        flags=re.IGNORECASE,
    )
    clean_text = clean_text.replace('"example.org"', "example.org")
    if not clean_text:
        raise ConnectorError("El post de LinkedIn no puede estar vacío.")
    clean_org_urn = (org_urn or "").strip()
    if clean_org_urn:
        author = clean_org_urn
    else:
        profile = await get_me(http, bundle)
        author = person_urn(profile)
    content: dict[str, Any] | None = None
    # El video nativo de LinkedIn tiene prioridad sobre la imagen (que queda como
    # poster de fallback si solo llega la imagen). Ambos usan content.media.id;
    # lo único que cambia es el urn (urn:li:video: vs urn:li:image:).
    if video is not None:
        video_urn = await upload_video(
            http,
            bundle,
            owner=author,
            content=video,
            content_type=video_content_type,
        )
        content = {"media": {"id": video_urn, "title": video_title[:300]}}
    elif image is not None:
        image_urn = await upload_image(
            http,
            bundle,
            owner=author,
            content=image,
            content_type=image_content_type,
        )
        media: dict[str, str] = {"id": image_urn, "title": "Acme"}
        clean_alt = alt_text.strip()
        if clean_alt:
            media["altText"] = clean_alt[:4086]
        content = {"media": media}

    payload: dict[str, Any] = {
        "author": author,
        "commentary": clean_text[:3000],
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    if content is not None:
        payload["content"] = content

    response = await http.post(POSTS_URL, headers=_headers(bundle), json=payload)
    _raise_for_linkedin_error(response, "la publicación")
    post_id = response.headers.get("x-restli-id")
    result: dict[str, Any] = {"id": post_id, "author": author}
    if response.content:
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            result["response"] = body

    if not post_id:
        # LinkedIn respondió 2xx sin el header que su propia documentación promete.
        # Sin URN no hay nada que releer -- tratar esto como éxito sería exactamente
        # el fallo silencioso que esta función existe para evitar.
        raise ConnectorError(
            "LinkedIn no devolvió el identificador del post publicado; no se puede "
            "confirmar que se haya creado."
        )

    verified, verification_note = await verify_post(
        http, bundle, post_id, org_urn=clean_org_urn or None
    )
    # "unknown" viaja tal cual: para páginas es el estado normal (sin permiso de
    # lectura) y quien llame lo comunica como "enviado, no confirmable" -- NUNCA se
    # asciende a "confirmed" acá (eso fue el parche del 06-ago que decía "Publicado ✅"
    # sin verificar, la misma mentira en la otra dirección). El raise queda SOLO para
    # el 404 directo, la única señal real de no-existencia que esta app puede ver.
    if verified == "not_found":
        raise ConnectorError(
            f"LinkedIn aceptó la publicación pero al releerla respondió 404 ({post_id}): "
            f"no se publicó nada de verdad; no hay post publicado como {author}. "
            "El borrador sigue intacto: revisa la "
            "conexión de LinkedIn y vuelve a aprobar."
        )
    result["verified"] = verified
    result["verification_note"] = verification_note
    return result
