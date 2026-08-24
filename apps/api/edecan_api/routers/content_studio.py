"""Studio creativo completo para web, escritorio y clientes móviles.

La ruta social crea borradores privados y solo publica en una cuenta conectada
después de una confirmación explícita. La ruta de proyectos expone el mismo
motor versionado que usa el chat para que web, escritorio, iOS y Android puedan
mostrar lienzo, variantes, historial y exportaciones sin revelar herramientas
ni rutas locales.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import aioboto3
import httpx
from edecan_connectors.base import ConnectorError
from edecan_connectors.social.linkedin import create_post as create_linkedin_post
from edecan_connectors.social.organization_multi import publish_organization_all_networks
from edecan_core import ToolContext
from edecan_core.freshness import assess_freshness, grounding_queries, official_source_domains
from edecan_core.queue import enqueue
from edecan_creative.investigacion import titulares_de_varias_consultas
from edecan_creative.marcas import (
    DESTINATION_ID_PATTERN,
    PERSONAL_DESTINATION_ID,
    BrandDestination,
    default_personal_destination,
    visual_guardrail_line,
    voice_prompt_block,
)
from edecan_creative.marcas import (
    editorial_profile_key as _destination_editorial_profile_key,
)
from edecan_creative.social import (
    CrearContenidoSocialTool,
    build_context_bank_prompt_block,
    get_editorial_profile,
    save_editorial_profile,
)
from edecan_design_studio.studio_tools import (
    AdministrarProyectoCreativoTool,
    CrearEditarProyectoCreativoTool,
    UsarEstudioCreativoPremiumTool,
    VerProyectosCreativosTool,
)
from edecan_llm.base import ChatMessage, CompletionRequest
from edecan_llm.router import LLMRouter
from edecan_schemas import FLAG_CONNECTORS_SOCIAL, TokenBundle
from edecan_toolkit.research import get_tenant_search_provider
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi import Path as PathParam
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)
from sqlalchemy import text as sql_text  # alias: `text` ya es el campo del post
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_api.config import Settings, get_settings
from edecan_api.deps import (
    CurrentUser,
    get_current_user,
    get_llm_router,
    get_repo,
    get_tenant_session,
    get_vault,
    rate_limit,
)
from edecan_api.repo import Repo
from edecan_api.routers.files import make_public_file_url

router = APIRouter(
    prefix="/v1/content",
    tags=["content"],
    dependencies=[Depends(rate_limit)],
)
logger = logging.getLogger(__name__)

_LLM_ALIAS = "profundo"
_MAX_RESPONSE_TOKENS = 1800
_PLATFORM_LIMITS = {"linkedin": 3000, "x": 8_000}
_EDITORIAL_PROMPT_FIELD_LIMITS = {
    "purpose": 700,
    "audience": 700,
    "voice": 900,
    "visual_identity": 700,
    "image_rules": 900,
    "calls_to_action": 500,
    "avoid": 900,
    "notes": 900,
}
_EDITORIAL_PROMPT_LIST_LIMIT = 8
_EDITORIAL_PROMPT_TOTAL_LIMIT = 6_500

# Id de destino de publicación: abierto (no `Literal["personal", "organization"]`), mismo
# patrón que `edecan_creative.marcas.BrandDestination.id`. `"personal"` sigue siendo el
# único id reservado; cualquier otro valor es el que el propio tenant le puso a su
# destino de organización -- nunca un nombre de marca fijo en este repo.
#
# A propósito SIN `to_lower`/`strip_whitespace`: pydantic-core valida `pattern` sobre el
# valor CRUDO antes de aplicar esas transformaciones, así que combinarlas aquí solo
# rechazaría con un error confuso cualquier valor con mayúsculas en vez de normalizarlo.
# El patrón ya exige minúsculas explícitamente; `_resolve_destination` vuelve a normalizar
# (`.strip().lower()`) como defensa en profundidad antes de resolver contra un tenant.
TargetId = Annotated[str, StringConstraints(pattern=DESTINATION_ID_PATTERN)]

_SYSTEM_PROMPT = """Eres el editor social de un asistente personal. Crea contenido verdadero,
específico y listo para que la persona lo revise y comparta manualmente. No inventes datos,
resultados, clientes, experiencias ni citas. No digas que publicaste nada.

Reglas no negociables:
- La memoria del modelo es antigua por definición. Para temas de IA, modelos, precios,
  lanzamientos, benchmarks, capacidades, versiones o fechas recientes, solo puedes
  afirmar lo que venga en "Fuentes oficiales actuales". Si no hay fuentes, escribe una
  pieza evergreen/opinativa y evita nombres de versiones, fechas, precios y comparaciones.
- No mezcles destinos. Si el destino es el perfil personal, NO menciones las empresas ni
  marcas del usuario salvo que la idea lo pida literalmente. Si el destino es una página
  de empresa, sí puedes hablar de esa marca, pero solo con afirmaciones verificables y
  lenguaje de marca, y nunca de las otras marcas del usuario.
- La imagen debe parecer una pieza editorial premium, no una portada genérica de IA. Evita
  escenas espaciales, robots genéricos, texto gigante, logos, marcas, pantallas falsas y
  frases enormes dentro de la imagen. Si se usa texto visual, que sea mínimo, legible y
  subordinado a la composición.

Devuelve EXCLUSIVAMENTE un objeto JSON válido, sin markdown ni explicación, con estas claves:
{"texto":"copy final", "titular_visual":"titular breve",
"visual_prompt":"descripción visual original sin texto ni logos",
"alt_text":"descripción accesible", "hashtags":["Etiqueta"]}

Para LinkedIn usa párrafos breves, una idea útil y hasta 3000 caracteres incluyendo hashtags.
Para X prioriza un solo post de hasta 280 caracteres. Solo si la idea necesita más espacio,
escribe un texto que pueda dividirse naturalmente en un hilo breve. Usa pocos hashtags o ninguno.
El texto debe respetar el objetivo, el tono y el idioma de la idea recibida."""


class SocialContentCreateIn(BaseModel):
    platform: Literal["linkedin", "x"]
    target: TargetId = PERSONAL_DESTINATION_ID
    topic: str = Field(min_length=1, max_length=300)
    objective: str = Field(default="Enseñar algo útil", min_length=1, max_length=120)
    tone: str = Field(default="Claro y humano", min_length=1, max_length=80)
    with_image: bool = True


class SocialEditorialProfileIn(BaseModel):
    purpose: str = Field(default="", max_length=4000)
    audience: str = Field(default="", max_length=4000)
    voice: str = Field(default="", max_length=4000)
    content_pillars: list[str] = Field(default_factory=list, max_length=20)
    preferred_formats: list[str] = Field(default_factory=list, max_length=20)
    visual_identity: str = Field(default="", max_length=4000)
    image_rules: str = Field(default="", max_length=4000)
    calls_to_action: str = Field(default="", max_length=4000)
    avoid: str = Field(default="", max_length=4000)
    notes: str = Field(default="", max_length=4000)
    context_bank: str = Field(default="", max_length=16_000)
    # Genéricos y por-tenant (ver `edecan_creative.redaccion._con_cierre_garantizado` /
    # `_CAMPOS_PERFIL_EN_PROMPT`): nunca una marca ni una URL fija en este repo. Un destino
    # que no los llena no ve ningún cambio de comportamiento.
    closing_url: str = Field(default="", max_length=300)
    target_length: str = Field(default="", max_length=300)
    # `None` (no enviado) != `""` (borrar). Es la diferencia entre que un cliente viejo -- que
    # no conoce este campo -- deje el modo como está, y que se lo apague sin querer en cada
    # guardado del panel: `model_dump(exclude_none=True)` en el PUT descarta lo que nadie
    # mandó, y `save_editorial_profile` sólo pisa los campos que vienen en el patch. Para un
    # interruptor que afloja al auditor de hechos, apagarse solo sería tolerable; encenderse
    # o apagarse SIN QUE NADIE LO PIDA no lo es -- ver
    # `edecan_creative.social.perfil_autoriza_escenas_ilustrativas`.
    fact_check_mode: str | None = Field(default=None, max_length=60)
    # Misma semántica `None != ""` que `fact_check_mode`: un cliente viejo que no conoce
    # este campo deja la postura como está; uno que la manda vacía la apaga a propósito.
    # Ver `edecan_creative.social.perfil_autoriza_stance_polemica`.
    editorial_stance: str | None = Field(default=None, max_length=60)


class SocialEditorialProfileOut(SocialEditorialProfileIn):
    platform: Literal["linkedin", "x"] = "linkedin"
    fact_check_mode: str = ""
    editorial_stance: str = ""
    target: TargetId | None = None
    configured: bool = False
    version: int = 0


class SocialContentArtifactOut(BaseModel):
    file_id: str
    filename: str
    mime: str | None = None


class SocialContentSourceOut(BaseModel):
    title: str
    url: str
    snippet: str = ""


class SocialContentOut(BaseModel):
    status: Literal["ready"] = "ready"
    platform: Literal["linkedin", "x"]
    target: TargetId | None = None
    post_text: str = Field(serialization_alias="copy")
    parts: list[str]
    alt_text: str = ""
    offline_visual: bool = False
    visual_warning: str = ""
    sources: list[SocialContentSourceOut] = Field(default_factory=list)
    artifacts: list[SocialContentArtifactOut]
    requires_human_confirmation: bool = True


class SocialContentPublishIn(BaseModel):
    platform: Literal["linkedin"]
    target: TargetId = PERSONAL_DESTINATION_ID
    text: str = Field(min_length=1, max_length=3000)
    image_file_id: UUID | None = None
    video_file_id: UUID | None = None
    alt_text: str = Field(default="", max_length=4086)
    confirmed: bool = False


class SocialContentPublishOut(BaseModel):
    status: Literal["published"] = "published"
    platform: Literal["linkedin"]
    provider_id: str | None = None
    # `edecan_connectors.social.linkedin.create_post` relee el post recién creado antes
    # de devolver -- ver su docstring para el porqué (un 2xx de LinkedIn no prueba que el
    # post exista; es exactamente lo que pasó con el token de organización equivocado).
    # `"confirmed"`: se releyó y existe. `"unknown"`: no se pudo releer (típicamente sin
    # permiso de lectura), así que el post PUEDE estar publicado pero no se verificó --
    # nunca se debe mostrar como un ✅ llano. Si la relectura confirma que NO existe,
    # `create_post` levanta `ConnectorError` y este endpoint nunca llega a construir una
    # respuesta de éxito (ver el `except ConnectorError` de `publish_social_content`).
    verified: Literal["confirmed", "unknown"] = "confirmed"
    verification_note: str = ""


class SocialDraftPublishOut(SocialContentPublishOut):
    """Respuesta de publicar POR `draft_id` (el botón de la card).

    Hereda el contrato de `SocialContentPublishOut` -- `status`/`platform`/
    `provider_id` son exactamente los mismos campos que el cliente ya decodifica
    (`SocialContentPublishResult` en `EdecanKit/ContentStudioModels.swift`), así
    que los dos caminos de publicación se leen igual desde la app.

    `already_published` distingue "acabo de publicarlo" de "ya estaba publicado
    y te devuelvo lo de antes" sin cambiar `status` (para el mundo, el post está
    publicado en los dos casos). Sirve para no volver a narrar la publicación en
    el chat y, en la app, para no celebrar dos veces.
    """

    draft_id: str
    # `str` y no `TargetId`: es un ECO de lo que hay guardado en la fila, no una
    # entrada que este endpoint reciba. Validarlo aquí con el patrón convertiría
    # una fila con un destino raro en un 500 justo en el camino idempotente (el
    # del segundo toque), que es precisamente donde nada puede fallar.
    target: str | None = None
    already_published: bool = False


_MAX_PUBLISH_IMAGE_BYTES = 20 * 1024 * 1024
_MAX_PUBLISH_VIDEO_BYTES = 200 * 1024 * 1024  # LinkedIn acepta hasta 200 MB en el Videos API.


def _editorial_profile_key(platform: str, target: str | None) -> str:
    # Solo LinkedIn separa el perfil editorial por destino (ver
    # `edecan_creative.marcas.editorial_profile_key`); el resto de plataformas ignoran
    # `target`. Mismas claves exactas que ya usaba el `_LINKEDIN_TARGETS` fijo -- p. ej.
    # `_editorial_profile_key("linkedin", "personal") == "linkedin_personal"` -- pero ahora
    # con CUALQUIER id de destino de organización, no solo el literal "organization".
    if platform != "linkedin":
        return platform
    return _destination_editorial_profile_key(platform, target)


def _resolve_destination(target: str | None) -> BrandDestination:
    """Arma el `BrandDestination` de un id de destino suelto (`target`/`destino`).

    Esta instalación todavía no persiste una config de destinos por tenant (tono, tema
    fijo, cuentas asociadas -- ver `edecan_creative.marcas.BrandDestinationConfig`): hasta
    que exista ese almacenamiento, cualquier id que no sea `personal` se sintetiza como una
    organización genérica con ese mismo id como etiqueta. Un `target` con forma inválida no
    debería llegar aquí (`TargetId` ya lo rechaza en el borde de la API), así que un id no
    vacío distinto de `personal` siempre resuelve a una organización.
    """

    key = str(target or "").strip().lower()
    if not key or key == PERSONAL_DESTINATION_ID:
        return default_personal_destination()
    return BrandDestination(id=key, actor="organization")


async def _linkedin_editorial_profile(
    ctx: ToolContext,
    *,
    target: str,
) -> dict[str, Any]:
    """Carga perfil específico y cae al perfil legacy de LinkedIn si aplica.

    El importador privado anterior guardaba un único perfil `linkedin`. La app separa el
    perfil por destino sin romper ese dato histórico: si no existe el perfil específico, se
    usa el legacy como base.
    """

    profile = await get_editorial_profile(ctx, _editorial_profile_key("linkedin", target))
    if profile.get("configured"):
        return profile
    legacy = await get_editorial_profile(ctx, "linkedin")
    if legacy.get("configured"):
        legacy = dict(legacy)
        legacy["platform"] = _editorial_profile_key("linkedin", target)
        return legacy
    return profile


def _linkedin_target_context(target: str) -> str:
    """Bloque de voz específico del destino, construido desde `edecan_creative.marcas`
    (reemplaza el texto fijo que antes distinguía "personal" de "Acme" a mano)."""

    return "\n" + voice_prompt_block(_resolve_destination(target))


def _get_organization_linkedin_token_and_urn() -> tuple[str | None, str]:
    """Configuración explícita del operador, sin fallbacks a cuentas privadas."""
    token = os.getenv("ORGANIZATION_LINKEDIN_ACCESS_TOKEN")
    org_urn = os.getenv("ORGANIZATION_LINKEDIN_ORG_URN") or ""
    return token, org_urn


def _linkedin_account_matches_target(account: dict[str, Any], target: str) -> bool:
    destination = _resolve_destination(target)
    if destination.actor == "organization":
        ext = str(account.get("external_account_id") or "").lower()
        disp = str(account.get("display_name") or "").lower()
        if ext.startswith("urn:li:organization") or "organizac" in disp or "organization" in ext:
            return True
        return destination.matches_connected_account(account)
    return destination.matches_connected_account(account)


async def _require_connectors_social(
    current_user: CurrentUser = Depends(get_current_user),
) -> None:
    if not current_user.tenant.flags.get(FLAG_CONNECTORS_SOCIAL, False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Los conectores sociales no están habilitados en esta instalación.",
        )


_STUDIO_READ_ACTIONS = frozenset(VerProyectosCreativosTool.allowed_actions)
_STUDIO_WRITE_ACTIONS = frozenset(CrearEditarProyectoCreativoTool.allowed_actions)
_STUDIO_ADMIN_ACTIONS = frozenset(AdministrarProyectoCreativoTool.allowed_actions)
_STUDIO_ACTIONS = _STUDIO_READ_ACTIONS | _STUDIO_WRITE_ACTIONS | _STUDIO_ADMIN_ACTIONS
_STUDIO_PROJECT_ACTIONS = _STUDIO_ACTIONS - {
    "health",
    "list",
    "create",
    "template-list",
    "template-create",
    "design-system-list",
    "corpus-ingest",
    "corpus-search",
}


class StudioProjectActionIn(BaseModel):
    """Contrato estable para que el editor y las apps controlen Studio.

    Las rutas privadas y los nombres internos de herramientas nunca forman
    parte de la API. Los adjuntos siguen llegando como UUIDs opacos de Edecán.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    action: Literal[
        "health",
        "list",
        "create",
        "edit",
        "read",
        "render",
        "history",
        "variants",
        "duplicate",
        "brand-health",
        "tidy",
        "archive",
        "restore",
        "export",
        "template-list",
        "template-save",
        "template-create",
        "design-system-list",
        "design-system-generate",
        "corpus-ingest",
        "corpus-search",
        "share-package",
    ]
    project_id: str | None = Field(default=None, alias="projectId", max_length=80)
    revision_id: str | None = Field(default=None, alias="revisionId", max_length=80)
    template_id: str | None = Field(default=None, alias="templateId", max_length=80)
    prompt: str | None = Field(default=None, max_length=80_000)
    instruction: str | None = Field(default=None, max_length=80_000)
    project_name: str | None = Field(default=None, alias="projectName", max_length=160)
    brand_name: str | None = Field(default=None, alias="brandName", max_length=160)
    brand_tokens: str | None = Field(default=None, alias="brandTokens", max_length=80_000)
    mode: (
        Literal[
            "mockup",
            "carousel",
            "ad",
            "post",
            "landing",
            "email",
            "deck",
            "general",
        ]
        | None
    ) = None
    width: int | None = Field(default=None, ge=320, le=4096)
    height: int | None = Field(default=None, ge=320, le=4096)
    count: int | None = Field(default=None, ge=1, le=4)
    quality: Literal["fast", "balanced", "max"] | None = None
    files: list[str] = Field(default_factory=list, max_length=12)
    export_format: Literal["html", "png", "pdf"] | None = Field(default=None, alias="exportFormat")
    include_archived: bool | None = Field(default=None, alias="includeArchived")
    template_name: str | None = Field(default=None, alias="templateName", max_length=160)
    template_description: str | None = Field(
        default=None, alias="templateDescription", max_length=500
    )
    template_category: Literal["prototype", "deck", "landing", "marketing", "other"] | None = Field(
        default=None, alias="templateCategory"
    )
    repos: list[str] = Field(default_factory=list, max_length=25)
    corpus_limit: int | None = Field(default=None, alias="corpusLimit", ge=1, le=20)
    screen_briefs: list[dict[str, Any]] = Field(
        default_factory=list, alias="screenBriefs", max_length=8
    )
    languages: list[Literal["en", "es", "pt", "fr"]] = Field(default_factory=list, max_length=4)
    theme: dict[str, Any] | None = None
    tidy_actions: list[dict[str, Any]] = Field(
        default_factory=list, alias="tidyActions", max_length=100
    )
    confirmed: bool = False

    @model_validator(mode="after")
    def validate_action_requirements(self) -> StudioProjectActionIn:
        if self.action not in _STUDIO_ACTIONS:
            raise ValueError("Acción de Studio no admitida.")
        if self.action in _STUDIO_PROJECT_ACTIONS and not self.project_id:
            raise ValueError("Esta acción necesita projectId.")
        if self.action == "create" and not (self.prompt or "").strip():
            raise ValueError("Describe qué quieres crear en prompt.")
        if self.action == "edit" and not (self.instruction or "").strip():
            raise ValueError("Describe el cambio en instruction.")
        if self.action == "template-create" and not self.template_id:
            raise ValueError("template-create necesita templateId.")
        if self.action == "corpus-ingest" and not self.repos:
            raise ValueError("corpus-ingest necesita al menos un repositorio owner/repo.")
        if self.action in _STUDIO_ADMIN_ACTIONS and not self.confirmed:
            raise ValueError("Confirma explícitamente esta organización reversible.")
        return self

    def tool_arguments(self) -> dict[str, Any]:
        payload = self.model_dump(by_alias=True, exclude_none=True)
        payload.pop("action", None)
        payload.pop("confirmed", None)
        files = payload.pop("files", [])
        for key in ("repos", "screenBriefs", "languages", "tidyActions"):
            if payload.get(key) == []:
                payload.pop(key, None)
        if files:
            payload["archivos"] = files
        return {"accion": self.action, **payload}


class StudioProjectActionOut(BaseModel):
    status: Literal["ready"] = "ready"
    action: str
    message: str
    result: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    presentation: list[dict[str, Any]] = Field(default_factory=list)


def _json_object(text: str) -> dict[str, Any] | None:
    """Acepta JSON limpio o cercado sin confiar en texto fuera del objeto."""

    clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    candidates = [clean]
    start, end = clean.find("{"), clean.rfind("}")
    if start >= 0 and end > start:
        candidates.append(clean[start : end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(value, dict):
            return value
    return None


def _clean_list(value: Any, *, limit: int = 8) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value[:limit]:
        if isinstance(item, str) and (clean := item.strip()):
            result.append(clean[:80])
    return result


def _compact_editorial_profile_for_prompt(profile: dict[str, Any]) -> dict[str, Any]:
    """Return a prompt-safe editorial profile.

    User/imported editorial profiles can contain long playbooks. Persisting that
    full context is useful, but sending it verbatim to a fast LLM makes content
    creation brittle: providers can reject the request or time out and the user
    only sees a generic 502. The API keeps the original stored profile intact
    and injects a bounded, deterministic version into generation prompts.
    """

    compact: dict[str, Any] = {
        "platform": profile.get("platform"),
        "configured": bool(profile.get("configured")),
        "version": profile.get("version"),
    }
    for key, limit in _EDITORIAL_PROMPT_FIELD_LIMITS.items():
        raw = profile.get(key)
        if not isinstance(raw, str):
            continue
        value = " ".join(raw.split())
        if value:
            compact[key] = value[:limit].rstrip()
    for key in ("content_pillars", "preferred_formats"):
        raw = profile.get(key)
        if not isinstance(raw, list):
            continue
        values = [
            " ".join(str(item).split())[:160].rstrip()
            for item in raw[:_EDITORIAL_PROMPT_LIST_LIMIT]
            if str(item).strip()
        ]
        if values:
            compact[key] = values

    encoded = json.dumps(compact, ensure_ascii=False)
    if len(encoded) <= _EDITORIAL_PROMPT_TOTAL_LIMIT:
        return compact

    # Final deterministic safety net: keep the highest-impact fields and trim
    # the least critical free-form notes first until the JSON is guaranteed to
    # fit. This path only triggers for oversized manual imports.
    for key in ("content_pillars", "preferred_formats"):
        if key in compact and isinstance(compact[key], list):
            compact[key] = compact[key][:4]
    for limit in (480, 320, 220, 160):
        for key in (
            "notes",
            "visual_identity",
            "image_rules",
            "avoid",
            "voice",
            "audience",
            "purpose",
        ):
            if key in compact and isinstance(compact[key], str):
                compact[key] = compact[key][:limit].rstrip()
        encoded = json.dumps(compact, ensure_ascii=False)
        if len(encoded) <= _EDITORIAL_PROMPT_TOTAL_LIMIT:
            return compact
    return compact


def _generated_args(text: str, body: SocialContentCreateIn) -> dict[str, Any]:
    parsed = _json_object(text)
    if parsed is None:
        # Algunos proveedores locales ignoran el contrato JSON. Su texto aún
        # es un borrador útil y la herramienta aplica los límites finales.
        generated_copy = re.sub(r"^```(?:\w+)?\s*|\s*```$", "", text.strip()).strip()
        parsed = {"texto": generated_copy}

    copy = str(parsed.get("texto") or parsed.get("copy") or "").strip()
    if not copy:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="El modelo no devolvió un borrador utilizable. Inténtalo de nuevo.",
        )
    hashtags = _clean_list(parsed.get("hashtags"))
    limit = _PLATFORM_LIMITS[body.platform]
    if body.platform == "linkedin" and hashtags:
        # La herramienta agrega los hashtags al final; reservar su espacio
        # evita que un copy válido termine fallando por unos pocos caracteres.
        hashtag_suffix = "\n\n" + " ".join(f"#{tag.lstrip('#')}" for tag in hashtags)
        limit = max(1, limit - len(hashtag_suffix))
    copy = copy[:limit].rstrip()
    return {
        "plataforma": body.platform,
        "tema": body.topic.strip(),
        "texto": copy,
        "titular_visual": str(parsed.get("titular_visual") or body.topic).strip()[:180],
        "visual_prompt": str(parsed.get("visual_prompt") or body.topic).strip()[:4000],
        "alt_text": str(parsed.get("alt_text") or "").strip()[:1000],
        "hashtags": hashtags,
        "con_imagen": body.with_image,
    }


def _content_studio_fydesign_args(
    *,
    body: SocialContentCreateIn,
    generated: dict[str, Any],
    settings: Settings,
) -> dict[str, Any]:
    model = str(getattr(settings, "EDECAN_CONTENT_IMAGE_MODEL", "") or "gpt-image-2").strip()
    provider = str(getattr(settings, "EDECAN_CONTENT_IMAGE_PROVIDER", "") or "fydesign").strip()
    quality = str(getattr(settings, "EDECAN_CONTENT_IMAGE_QUALITY", "") or "standard").strip()
    style = str(getattr(settings, "EDECAN_CONTENT_IMAGE_STYLE", "") or "").strip()
    brand = str(getattr(settings, "EDECAN_CONTENT_IMAGE_BRAND", "") or "").strip()
    platform = "linkedin" if body.platform == "linkedin" else "landscape"

    # Construido desde `edecan_creative.marcas` (reemplaza el texto fijo que antes
    # nombraba "LinkedIn Acme" a mano para el destino de organización).
    target_rules = visual_guardrail_line(_resolve_destination(body.target))
    prompt_parts = [
        str(generated.get("visual_prompt") or body.topic).strip(),
        f"Post para {platform}.",
        f"Objetivo: {body.objective.strip()}.",
        f"Tono: {body.tone.strip()}.",
        target_rules,
        (
            "Debe funcionar como visual editorial profesional: limpio, premium, humano, "
            "coherente con el texto y sin elementos aleatorios."
        ),
        (
            "Evita logos, marcas registradas, pantallas con datos falsos, texto diminuto, "
            "texto gigante, portadas espaciales genéricas, robots genéricos y composiciones "
            "con una frase enorme al centro."
        ),
        (
            "Si necesitas texto dentro de la imagen, úsalo como detalle editorial corto, no "
            "como protagonista."
        ),
    ]
    headline = str(generated.get("titular_visual") or "").strip()
    if headline:
        prompt_parts.append(
            f"Referencia conceptual, no texto obligatorio dentro de la imagen: {headline}."
        )
    copy = str(generated.get("texto") or "").strip()
    if copy:
        prompt_parts.append(f"Texto del post para alinear la imagen:\n{copy[:1200]}")

    arguments: dict[str, Any] = {
        "prompt": "\n".join(part for part in prompt_parts if part),
        "platform": platform,
        "quality": quality,
        "count": 1,
        "engine": "gpt-image-2" if model == "gpt-image-2" else "auto",
    }
    if provider and provider != "fydesign":
        arguments["provider"] = provider
    if model and model not in {"gpt-image-2", "imagen-4", "imagen4"}:
        arguments["model"] = model
    if style:
        arguments["style"] = style
    if brand:
        arguments["brand"] = brand
    return arguments


async def _try_replace_visual_with_fydesign(
    *,
    ctx: ToolContext,
    body: SocialContentCreateIn,
    generated: dict[str, Any],
    artifacts: list[Any],
    data: dict[str, Any],
) -> tuple[list[Any], dict[str, Any]]:
    settings = ctx.settings
    provider = str(getattr(settings, "EDECAN_CONTENT_IMAGE_PROVIDER", "fydesign") or "").lower()
    if not body.with_image or provider in {"", "off", "disabled", "none", "stub"}:
        return artifacts, data
    if provider != "fydesign":
        return artifacts, data
    if not bool(getattr(settings, "EDECAN_LOCAL_MODE", False)):
        data["visual_warning"] = (
            data.get("visual_warning")
            or "FyDesign requiere la app local de Edecán; se usó el visual simple."
        )
        return artifacts, data

    tool = UsarEstudioCreativoPremiumTool()
    fydesign_args = _content_studio_fydesign_args(
        body=body,
        generated=generated,
        settings=settings,
    )
    try:
        result = await tool.run(
            ctx,
            {"capacidad": "fydesign_post", "argumentos": fydesign_args},
        )
    except Exception:
        logger.warning(
            "FyDesign no pudo renderizar el visual social; se conserva el fallback (tenant_id=%s).",
            getattr(ctx, "tenant_id", None),
            exc_info=True,
        )
        data["visual_warning"] = (
            data.get("visual_warning")
            or "FyDesign no pudo generar el visual final; se usó el visual simple."
        )
        return artifacts, data

    studio_data = result.data or {}
    studio_artifacts = studio_data.get("artifacts")
    if not isinstance(studio_artifacts, list):
        data["visual_warning"] = (
            data.get("visual_warning")
            or "FyDesign no devolvió un archivo de imagen; se usó el visual simple."
        )
        return artifacts, data
    image_artifacts = [
        artifact
        for artifact in studio_artifacts
        if isinstance(artifact, dict)
        and str(artifact.get("mime") or "").startswith("image/")
        and artifact.get("file_id")
    ]
    if not image_artifacts:
        data["visual_warning"] = (
            data.get("visual_warning")
            or "FyDesign no devolvió un PNG usable; se usó el visual simple."
        )
        return artifacts, data

    replaced = [
        artifact
        for artifact in artifacts
        if not (isinstance(artifact, dict) and str(artifact.get("mime") or "").startswith("image/"))
    ]
    replaced.append(image_artifacts[0])
    data["offline_visual"] = False
    data["visual_warning"] = ""
    data["fydesign"] = {
        "capability": "fydesign_post",
        "engine": fydesign_args.get("engine"),
        "model": fydesign_args.get("model")
        or getattr(settings, "EDECAN_CONTENT_IMAGE_MODEL", "gpt-image-2"),
    }
    return replaced, data


def _tool_context(
    *,
    current_user: CurrentUser,
    session: AsyncSession,
    settings: Settings,
    llm_router: LLMRouter,
    vault: Any,
) -> ToolContext:
    return ToolContext(
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        session=session,
        settings=settings,
        llm=llm_router,
        vault=vault,
        extras={"flags": current_user.tenant.flags},
    )


def _official_url(url: str, domains: tuple[str, ...]) -> bool:
    host = (urlsplit(url).hostname or "").casefold()
    return bool(host) and any(host == domain or host.endswith(f".{domain}") for domain in domains)


async def _research_social_topic(
    ctx: ToolContext,
    topic: str,
) -> list[dict[str, str]]:
    """Obtiene evidencia primaria para temas que pueden haber cambiado.

    Dos pasadas, en orden de confiabilidad para el auditor de hechos que corre después
    (`edecan_creative.auditoria.auditar_hechos`, cableado dentro de
    `CrearContenidoSocialTool.run`): primero titulares de noticias con `pubDate` verificado
    (`edecan_creative.investigacion`, sin API key), que si existen son evidencia más fuerte que
    un resultado de búsqueda web genérico sin fecha estructurada (ver el docstring de ese
    módulo); después la búsqueda web BYO del tenant como respaldo/complemento.
    """

    expected_domains = official_source_domains(topic)
    if not expected_domains and not assess_freshness(topic).required:
        return []

    selected: list[dict[str, str]] = []
    seen: set[str] = set()
    try:
        async with httpx.AsyncClient() as http:
            titulares = await titulares_de_varias_consultas(
                http, [topic], maximo_por_consulta=6, max_dias=3
            )
        for titular in titulares:
            if titular.url in seen:
                continue
            seen.add(titular.url)
            selected.append(
                {
                    "title": " ".join(titular.titulo.split())[:240],
                    "url": titular.url,
                    "snippet": " ".join(titular.snippet.split())[:600],
                }
            )
            if len(selected) >= 6:
                return selected
    except Exception:
        logger.warning(
            "No se pudieron obtener titulares frescos para el tema social (tenant_id=%s).",
            getattr(ctx, "tenant_id", None),
            exc_info=True,
        )

    provider = await get_tenant_search_provider(ctx)
    try:
        for query in grounding_queries(topic, language="es", date_iso=date.today().isoformat()):
            for hit in await provider.search(query, k=5):
                if expected_domains and not _official_url(hit.url, expected_domains):
                    continue
                if hit.url in seen:
                    continue
                seen.add(hit.url)
                selected.append(
                    {
                        "title": " ".join(hit.title.split())[:240],
                        "url": hit.url,
                        "snippet": " ".join(hit.snippet.split())[:600],
                    }
                )
                if len(selected) >= 6:
                    return selected
    except Exception:
        logger.warning(
            "No se pudo investigar el tema social actual (tenant_id=%s).",
            getattr(ctx, "tenant_id", None),
            exc_info=True,
        )
    return selected


@router.post("/social", response_model=SocialContentOut)
async def create_social_content(
    body: SocialContentCreateIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    session: AsyncSession = Depends(get_tenant_session),
    settings: Settings = Depends(get_settings),
    vault: Any = Depends(get_vault),
    llm_router: LLMRouter = Depends(get_llm_router),
) -> SocialContentOut:
    tool_context = _tool_context(
        current_user=current_user,
        session=session,
        settings=settings,
        llm_router=llm_router,
        vault=vault,
    )
    editorial_profile = (
        await _linkedin_editorial_profile(tool_context, target=body.target)
        if body.platform == "linkedin"
        else await get_editorial_profile(tool_context, body.platform)
    )
    editorial_context = (
        "\nPerfil editorial persistente de esta persona:\n"
        + json.dumps(_compact_editorial_profile_for_prompt(editorial_profile), ensure_ascii=False)
        if editorial_profile.get("configured")
        else (
            "\nEsta persona aún no configuró una estrategia editorial. Conserva un resultado "
            "útil y neutral, pero no inventes audiencia, experiencia ni identidad de marca."
        )
    )
    context_bank_block = build_context_bank_prompt_block(editorial_profile)
    sources = await _research_social_topic(tool_context, body.topic.strip())
    research_context = (
        "\nFuentes oficiales actuales para verificar afirmaciones:\n"
        + "\n".join(
            f"- {source['title']} | {source['url']} | {source['snippet']}" for source in sources
        )
        if sources
        else (
            "\nNo se encontraron fuentes oficiales actuales para este tema. "
            "No presentes como confirmado ningún nombre, versión, fecha o capacidad cambiante."
            if official_source_domains(body.topic) or assess_freshness(body.topic).required
            else ""
        )
    )
    platform_label = "LinkedIn" if body.platform == "linkedin" else "X"
    target_context = _linkedin_target_context(body.target) if body.platform == "linkedin" else ""
    request = CompletionRequest(
        model="",
        system=_SYSTEM_PROMPT,
        messages=[
            ChatMessage(
                role="user",
                content=(
                    f"Plataforma: {platform_label}\n"
                    f"Idea: {body.topic.strip()}\n"
                    f"Objetivo: {body.objective.strip()}\n"
                    f"Tono: {body.tone.strip()}\n"
                    f"Crear imagen: {'sí' if body.with_image else 'no'}"
                    f"{target_context}"
                    f"{editorial_context}"
                    f"{context_bank_block}"
                    f"{research_context}"
                ),
            )
        ],
        max_tokens=_MAX_RESPONSE_TOKENS,
        temperature=0.55,
    )
    response = await llm_router.complete(_LLM_ALIAS, current_user.tenant.flags, request)
    await repo.add_usage_event(
        tenant_id=current_user.tenant_id,
        kind="llm_tokens",
        quantity=float(response.usage.input_tokens + response.usage.output_tokens),
        meta={
            "alias": _LLM_ALIAS,
            "job": "content_studio_social",
            "platform": body.platform,
            "target": body.target if body.platform == "linkedin" else None,
        },
    )

    args = _generated_args(response.text, body)
    args["fuentes"] = sources
    if body.platform == "linkedin":
        # La tool no conoce "personal"/"organization": ese destino solo existe en
        # este endpoint (`body.target`). Pasarlo permite que la card de chat
        # equivalente (`ToolResult.presentation`, `SocialDraftBlock`) sepa
        # cuál de las dos variantes mostrar cuando el usuario pida lo mismo
        # directamente en el chat en vez de por este endpoint dedicado.
        args["destino"] = body.target
    tool = CrearContenidoSocialTool()
    try:
        result = await tool.run(
            tool_context,
            args,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Falló la creación del paquete social (tenant_id=%s user_id=%s platform=%s)",
            current_user.tenant_id,
            current_user.user_id,
            body.platform,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo terminar el paquete de contenido. Inténtalo de nuevo.",
        ) from exc

    data = dict(result.data or {})
    artifacts = data.get("artifacts")
    parts = data.get("parts")
    if not isinstance(artifacts, list) or not artifacts or not isinstance(parts, list) or not parts:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=result.content or "No se pudo crear el paquete de contenido.",
        )
    artifacts, data = await _try_replace_visual_with_fydesign(
        ctx=tool_context,
        body=body,
        generated=args,
        artifacts=artifacts,
        data=data,
    )
    data["artifacts"] = artifacts

    # El artefacto ya está persistido. El aviso es best-effort y transporta
    # únicamente UUIDs opacos, nunca el copy, el tema ni nombres de archivos.
    try:
        artifact_id = str(artifacts[0]["file_id"])
        await enqueue(
            settings,
            "notify_important_event",
            {
                "user_id": str(current_user.user_id),
                "kind": "content_created",
                "event_id": artifact_id,
                "artifact_id": artifact_id,
            },
            current_user.tenant_id,
        )
    except Exception:
        # Un proveedor push/cola sin configurar no convierte un contenido ya
        # creado en un error de cara a la persona.
        logger.warning(
            "No se pudo encolar la notificación del Content Studio (tenant_id=%s user_id=%s)",
            current_user.tenant_id,
            current_user.user_id,
            exc_info=True,
        )

    return SocialContentOut(
        platform=body.platform,
        target=body.target if body.platform == "linkedin" else None,
        post_text=str(data.get("copy") or args["texto"]),
        parts=[str(part) for part in parts],
        alt_text=str(data.get("alt_text") or args["alt_text"]),
        offline_visual=bool(data.get("offline_visual", False)),
        visual_warning=str(data.get("visual_warning") or ""),
        sources=[SocialContentSourceOut.model_validate(item) for item in sources],
        artifacts=[SocialContentArtifactOut.model_validate(item) for item in artifacts],
    )


@router.get("/social/profile", response_model=SocialEditorialProfileOut)
async def get_social_editorial_profile(
    platform: Literal["linkedin", "x"] = "linkedin",
    target: TargetId = PERSONAL_DESTINATION_ID,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
    settings: Settings = Depends(get_settings),
    vault: Any = Depends(get_vault),
    llm_router: LLMRouter = Depends(get_llm_router),
) -> SocialEditorialProfileOut:
    ctx = _tool_context(
        current_user=current_user,
        session=session,
        settings=settings,
        llm_router=llm_router,
        vault=vault,
    )
    profile_key = _editorial_profile_key(platform, target if platform == "linkedin" else None)
    profile = await get_editorial_profile(ctx, profile_key)
    if platform == "linkedin" and not profile.get("configured"):
        legacy = await get_editorial_profile(ctx, "linkedin")
        if legacy.get("configured"):
            profile = legacy
    public_profile = {**profile, "platform": platform}
    if platform == "linkedin":
        public_profile["target"] = target
    return SocialEditorialProfileOut.model_validate(public_profile)


@router.put("/social/profile", response_model=SocialEditorialProfileOut)
async def put_social_editorial_profile(
    body: SocialEditorialProfileIn,
    platform: Literal["linkedin", "x"] = "linkedin",
    target: TargetId = PERSONAL_DESTINATION_ID,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
    settings: Settings = Depends(get_settings),
    vault: Any = Depends(get_vault),
    llm_router: LLMRouter = Depends(get_llm_router),
) -> SocialEditorialProfileOut:
    ctx = _tool_context(
        current_user=current_user,
        session=session,
        settings=settings,
        llm_router=llm_router,
        vault=vault,
    )
    profile_key = _editorial_profile_key(platform, target if platform == "linkedin" else None)
    # `exclude_none=True`: ver el comentario de `fact_check_mode` en `SocialEditorialProfileIn`.
    # Un campo que el cliente no mandó no es un campo que el cliente quiso vaciar.
    saved = await save_editorial_profile(ctx, profile_key, body.model_dump(exclude_none=True))
    public_profile = {**saved, "platform": platform}
    if platform == "linkedin":
        public_profile["target"] = target
    return SocialEditorialProfileOut.model_validate(public_profile)


async def _load_private_publish_image(
    *,
    repo: Repo,
    settings: Settings,
    tenant_id: UUID,
    file_id: UUID,
) -> tuple[bytes, str]:
    row = await repo.get_file(tenant_id=tenant_id, file_id=file_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No encontré la imagen seleccionada.",
        )
    mime = str(row.get("mime") or "")
    if not mime.startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El archivo seleccionado no es una imagen.",
        )
    size_bytes = int(row.get("size_bytes") or 0)
    if size_bytes > _MAX_PUBLISH_IMAGE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="La imagen supera el límite de 20 MB.",
        )
    s3_key = str(row.get("s3_key") or "")
    if not s3_key:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La imagen no tiene contenido almacenado.",
        )

    content: bytes | None = None
    data_dir = getattr(settings, "DATA_DIR", None) or os.getenv("EDECAN_DATA_DIR")
    possible_local_paths: list[Path] = []
    if data_dir:
        possible_local_paths.append(Path(data_dir) / "objects" / "edecan-files" / s3_key)
        possible_local_paths.append(Path(data_dir) / s3_key)
    possible_local_paths.append(
        Path.home()
        / "Library"
        / "Application Support"
        / "cc.edecan.desktop"
        / "data"
        / "objects"
        / "edecan-files"
        / s3_key
    )
    possible_local_paths.append(
        Path.home() / ".edecan" / "data" / "objects" / "edecan-files" / s3_key
    )

    for p in possible_local_paths:
        if p.is_file():
            try:
                content = p.read_bytes()
                break
            except Exception:
                pass

    if content is None:
        session = aioboto3.Session()
        try:
            async with session.client(
                "s3",
                region_name=settings.AWS_REGION,
                endpoint_url=settings.AWS_ENDPOINT_URL,
            ) as s3:
                response = await s3.get_object(Bucket=settings.S3_BUCKET, Key=s3_key)
                content = await response["Body"].read(_MAX_PUBLISH_IMAGE_BYTES + 1)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 - clientes S3 tienen excepciones propias
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="No pude leer la imagen privada para publicarla.",
            ) from exc

    if content is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No pude leer la imagen privada para publicarla.",
        )
    if len(content) > _MAX_PUBLISH_IMAGE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="La imagen supera el límite de 20 MB.",
        )
    return content, mime


async def _load_private_publish_video(
    *,
    repo: Repo,
    settings: Settings,
    tenant_id: UUID,
    file_id: UUID,
) -> tuple[bytes, str]:
    """Lee el MP4 privado guardado por el autopost para publicarlo en LinkedIn.

    Espejo de ``_load_private_publish_image`` con límite de 200 MB y ``video/``."""
    row = await repo.get_file(tenant_id=tenant_id, file_id=file_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No encontré el video seleccionado.",
        )
    mime = str(row.get("mime") or "video/mp4")
    if not mime.startswith("video/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El archivo seleccionado no es un video.",
        )
    size_bytes = int(row.get("size_bytes") or 0)
    if size_bytes > _MAX_PUBLISH_VIDEO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="El video supera el límite de 200 MB.",
        )
    s3_key = str(row.get("s3_key") or "")
    if not s3_key:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El video no tiene contenido almacenado.",
        )

    content: bytes | None = None
    data_dir = getattr(settings, "DATA_DIR", None) or os.getenv("EDECAN_DATA_DIR")
    possible_local_paths: list[Path] = []
    if data_dir:
        possible_local_paths.append(Path(data_dir) / "objects" / "edecan-files" / s3_key)
        possible_local_paths.append(Path(data_dir) / s3_key)
    possible_local_paths.append(
        Path.home()
        / "Library"
        / "Application Support"
        / "cc.edecan.desktop"
        / "data"
        / "objects"
        / "edecan-files"
        / s3_key
    )
    possible_local_paths.append(
        Path.home() / ".edecan" / "data" / "objects" / "edecan-files" / s3_key
    )

    for p in possible_local_paths:
        if p.is_file():
            try:
                content = p.read_bytes()
                break
            except Exception:
                pass

    if content is None:
        session = aioboto3.Session()
        try:
            async with session.client(
                "s3",
                region_name=settings.AWS_REGION,
                endpoint_url=settings.AWS_ENDPOINT_URL,
            ) as s3:
                response = await s3.get_object(Bucket=settings.S3_BUCKET, Key=s3_key)
                content = await response["Body"].read(_MAX_PUBLISH_VIDEO_BYTES + 1)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 - clientes S3 tienen excepciones propias
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="No pude leer el video privado para publicarlo.",
            ) from exc

    if content is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No pude leer el video privado para publicarlo.",
        )
    if len(content) > _MAX_PUBLISH_VIDEO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="El video supera el límite de 200 MB.",
        )
    return content, mime


@router.post(
    "/social/publish",
    response_model=SocialContentPublishOut,
    dependencies=[Depends(_require_connectors_social)],
)
async def publish_social_content(
    body: SocialContentPublishIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    settings: Settings = Depends(get_settings),
    vault: Any = Depends(get_vault),
) -> SocialContentPublishOut:
    """Publicación puntual y confirmada mediante la API oficial de LinkedIn."""

    if not body.confirmed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Confirma esta publicación antes de enviarla a LinkedIn.",
        )
    accounts = await repo.list_connector_accounts(tenant_id=current_user.tenant_id)
    matching_accounts = [
        item
        for item in accounts
        if item.get("connector_key") == "linkedin"
        and item.get("status") == "active"
        and _linkedin_account_matches_target(item, body.target)
    ]
    account = next(reversed(matching_accounts), None) or next(
        (
            item
            for item in reversed(accounts)
            if item.get("connector_key") == "linkedin" and item.get("status") == "active"
        ),
        None,
    )
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Conecta tu cuenta de LinkedIn antes de publicar.",
        )
    bundle = await vault.get(current_user.tenant_id, account["id"])
    if bundle is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "La autorización de LinkedIn ya no está disponible. Vuelve a conectar la cuenta."
            ),
        )

    image: tuple[bytes, str] | None = None
    if body.image_file_id is not None:
        image = await _load_private_publish_image(
            repo=repo,
            settings=settings,
            tenant_id=current_user.tenant_id,
            file_id=body.image_file_id,
        )

    video: tuple[bytes, str] | None = None
    if body.video_file_id is not None:
        video = await _load_private_publish_video(
            repo=repo,
            settings=settings,
            tenant_id=current_user.tenant_id,
            file_id=body.video_file_id,
        )

    # Publicar en una PÁGINA de empresa (en vez del perfil personal) porta
    # `_linkedin_publish` de REFERENCIA (`features/organization_social.py`): si el destino
    # resuelto es una organización, se lee el URN de la página de `bundle.extra`
    # (`organization_urns`, capturado en el callback OAuth con
    # `edecan_connectors.social.linkedin.get_organization_urns` cuando el dueño
    # autorizó los scopes de organización) y se pasa como `org_urn` a `create_post`,
    # que entonces publica como esa página en vez de como la persona autorizada.
    # Si el dueño no autorizó org (o administra varias y ninguna coincide), sale
    # `None` y el post cae al perfil personal, igual que antes.
    org_urn: str | None = None
    if _resolve_destination(body.target).actor == "organization":
        org_urns = (bundle.extra.get("organization_urns") if bundle else None) or []
        org_urn = str((org_urns[0] if org_urns else "") or "").strip() or None
        # Fallback robusto: el vault NO persiste `bundle.extra` (ver
        # `_serialize_bundle`), así que una cuenta conectada por el importador
        # o antes de que `extra` se guardara devuelve `organization_urns` vacío.
        # El URN de la página ya vive en claro en la propia cuenta conectada
        # (`external_account_id`, p. ej. "urn:li:organization:123456789"), que
        # es como REFERENCIA lo tiene. Sin este fallback el post de la PÁGINA caía
        # silenciosamente al perfil personal.
        if not org_urn:
            ext = str(account.get("external_account_id") or "").strip()
            if ext.startswith("urn:li:organization"):
                org_urn = ext
    # Una instalación local puede configurar una organización explícitamente
    # por entorno; nunca se usa una cuenta fija del proyecto.
    if body.target in ("organization", "organization_linkedin") and bundle is None:
        organization_token, organization_org = _get_organization_linkedin_token_and_urn()
        if organization_token:
            bundle = TokenBundle(
                access_token=organization_token,
                scopes=[
                    "w_organization_social",
                    "r_organization_social",
                    "rw_organization_admin",
                    "w_member_social",
                ],
                extra={"organization_urns": [organization_org]},
            )
            org_urn = organization_org

    try:
        # El video nativo necesita más tiempo: initializeUpload → upload → finalize
        # → espera de procesamiento (~10-30s). La imagen sola sigue con 45s.
        client_timeout = 180.0 if video is not None else 45.0
        async with httpx.AsyncClient(timeout=client_timeout) as http:
            result = await create_linkedin_post(
                http,
                bundle,
                text=body.text,
                image=image[0] if image else None,
                image_content_type=image[1] if image else "image/png",
                video=video[0] if video else None,
                video_content_type=video[1] if video else "video/mp4",
                alt_text=body.alt_text,
                org_urn=org_urn,
            )
    except ConnectorError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error al publicar en LinkedIn: {exc}",
        ) from exc

    provider_id = result.get("id")
    # `create_linkedin_post` ya releyó el post antes de devolver (ver su docstring en
    # `edecan_connectors.social.linkedin.create_post`); si la relectura hubiera confirmado
    # que NO existe, habría levantado `ConnectorError` y ni siquiera se llega aquí. Lo que
    # queda por decidir es solo si se pudo CONFIRMAR (`"confirmed"`) o si quedó sin
    # verificar (`"unknown"`, típicamente sin permiso de lectura) -- eso viaja tal cual al
    # cliente y al audit log, nunca se lo trata como el mismo éxito llano de siempre.
    verified = str(result.get("verified") or "confirmed")
    verification_note = str(result.get("verification_note") or "")
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="content.linkedin_published",
        target=str(provider_id or account["id"]),
        # `author`: el 1c de hoy. Sin esto, un post salido con el autor equivocado
        # (el bug de hoy: token personal publicando "como" la página) no dejaba rastro
        # de a nombre de quién quedó realmente -- solo el `target` que PIDIÓ la persona,
        # nunca lo que LinkedIn aceptó de verdad.
        meta={
            "author": result.get("author"),
            "verified": verified,
            "verification_note": verification_note or None,
        },
    )
    try:
        # `provider_id` es el URN de LinkedIn ("urn:li:share:7489...", NO un
        # UUID) y así viajaba tal cual al job, que revienta con
        # `ValueError: notify_important_event requiere user_id y event_id UUID`
        # (`edecan_worker.handlers.notify_important_event`). Resultado: el push
        # de "Publicación terminada" no llegó NUNCA — el job se reintentaba
        # seis veces y terminaba en error. `uuid5` deriva un UUID estable a
        # partir del URN, así que se conserva la deduplicación por evento de
        # `record_notification_event` (dos publicaciones del mismo post siguen
        # dando un solo aviso), cosa que un `uuid4` de relleno perdería.
        event_id = (
            str(uuid5(NAMESPACE_URL, f"linkedin:{provider_id}")) if provider_id else str(uuid4())
        )
        await enqueue(
            settings,
            "notify_important_event",
            {
                "user_id": str(current_user.user_id),
                "kind": "content_published",
                "event_id": event_id,
            },
            current_user.tenant_id,
        )
    except Exception:
        logger.warning("No se pudo encolar la notificación de publicación.", exc_info=True)
    return SocialContentPublishOut(
        platform="linkedin",
        provider_id=str(provider_id) if provider_id else None,
        verified="confirmed" if verified == "confirmed" else "unknown",
        verification_note=verification_note,
    )


# ---------------------------------------------------------------------------
# Publicar POR `draft_id` — el cable que faltaba entre la card y LinkedIn
# ---------------------------------------------------------------------------
#
# SQL parametrizado directo contra `social_drafts` (mismo criterio que
# `edecan_api.routers.ads` con `ad_drafts`: el contrato pinnea tabla y columnas;
# este router no importa `edecan_db.models`).


async def _publish_organization_multi_network(
    *,
    repo: Repo,
    settings: Settings,
    tenant_id: UUID,
    draft: dict[str, Any],
) -> dict:
    """Despacha el borrador de Acme a las 5 redes de un golpe.

    Cuando el dueño toca "Aprobar y publicar" en un borrador de Acme,
    este funci\u00f3n publica el MISMO post + video en LinkedIn (p\u00e1gina),
    Instagram, Facebook, Threads y X. Porta el despachador de REFERENCIA
    (``organization_social.py:organization_redes_publicar``).

    Devuelve un dict con:
    - ``ok``: True si al menos una red public\u00f3
    - ``results``: dict por red con ``{ok, post_id/media_id/tweet_id, error}``
    - ``provider_id``: el id de LinkedIn (para persistir en social_drafts)
    - ``chat_text``: texto de confirmaci\u00f3n multi-red para el chat
    """
    text = str(draft.get("text") or "")
    image_file_id = draft.get("image_file_id")
    video_file_id = draft.get("video_file_id")

    # Cargar bytes de imagen y video (mismo mecanismo que publish_social_content)
    image: tuple[bytes, str] | None = None
    if image_file_id is not None:
        try:
            image = await _load_private_publish_image(
                repo=repo, settings=settings, tenant_id=tenant_id, file_id=UUID(str(image_file_id))
            )
        except HTTPException:
            image = None

    video: tuple[bytes, str] | None = None
    if video_file_id is not None:
        try:
            video = await _load_private_publish_video(
                repo=repo, settings=settings, tenant_id=tenant_id, file_id=UUID(str(video_file_id))
            )
        except HTTPException:
            video = None

    # Resolver credenciales de LinkedIn de Acme (mismo patr\u00f3n que publish_social_content)
    li_token, li_org = _get_organization_linkedin_token_and_urn()
    linkedin_bundle = None
    linkedin_org_urn = li_org
    if li_token:
        linkedin_bundle = TokenBundle(
            access_token=li_token,
            scopes=[
                "w_organization_social",
                "r_organization_social",
                "rw_organization_admin",
                "w_member_social",
            ],
            extra={"organization_urns": [li_org]},
        )

    # Funci\u00f3n para generar URLs p\u00fablicas firmadas (para IG/FB/Threads).
    # Prioridad: ORGANIZATION_PUBLIC_BASE_URL (override expl\u00edcito) > PHONE_WEBHOOK_BASE_URL
    # (el t\u00fanel, que es la URL p\u00fablica que de verdad llega a este backend) >
    # PUBLIC_BASE_URL (en modo local es la IP de la LAN, que NO sirve para APIs
    # externas como IG/FB/Threads).
    public_base_url = (
        os.getenv("ORGANIZATION_PUBLIC_BASE_URL", "")
        or os.getenv("PHONE_WEBHOOK_BASE_URL", "")
        or getattr(settings, "PUBLIC_BASE_URL", None)
        or ""
    ).rstrip("/")

    def _make_public_url(file_id_str: str) -> str:
        if not public_base_url:
            return ""
        return make_public_file_url(
            public_base_url=public_base_url,
            file_id=file_id_str,
            jwt_secret=settings.JWT_SECRET,
        )

    results = await publish_organization_all_networks(
        text=text,
        image_bytes=image[0] if image else None,
        image_mime=image[1] if image else "image/png",
        video_bytes=video[0] if video else None,
        video_mime=video[1] if video else "video/mp4",
        image_file_id=str(image_file_id) if image_file_id else None,
        video_file_id=str(video_file_id) if video_file_id else None,
        make_public_url=_make_public_url,
        linkedin_bundle=linkedin_bundle,
        linkedin_org_urn=linkedin_org_urn,
    )

    ok_redes = [k for k, v in results.items() if v.get("ok")]
    fail_redes = [k for k, v in results.items() if not v.get("ok")]
    any_ok = bool(ok_redes)

    # provider_id: el de LinkedIn (para compat con el campo published_provider_id)
    li_result = results.get("linkedin", {})
    provider_id = str(li_result.get("post_id") or "") if li_result.get("ok") else None

    # Texto de confirmaci\u00f3n para el chat
    nombres = {
        "linkedin": "LinkedIn",
        "instagram": "Instagram",
        "facebook": "Facebook",
        "threads": "Threads",
        "x": "X",
    }
    if any_ok:
        donde = ", ".join(nombres.get(r, r) for r in ok_redes)
        chat_text = f"Publicado en {donde}. \u2705"
        if fail_redes:
            errores = "; ".join(
                f"{nombres.get(r, r)}: {results[r].get('error', 'sin detalle')[:100]}"
                for r in fail_redes
            )
            chat_text += f"\n\nNo pude publicar en: {errores}"
    else:
        errores = "; ".join(
            f"{nombres.get(r, r)}: {results[r].get('error', 'sin detalle')[:100]}"
            for r in fail_redes
        )
        chat_text = f"No pude publicar en ninguna red. {errores}"

    return {
        "ok": any_ok,
        "results": results,
        "provider_id": provider_id,
        "verified": "confirmed"
        if li_result.get("ok") and li_result.get("verified") == "confirmed"
        else "unknown",
        "chat_text": chat_text,
    }


async def _lock_social_draft(
    session: AsyncSession, *, tenant_id: UUID, draft_id: str
) -> dict[str, Any] | None:
    """Lee el borrador del tenant y lo BLOQUEA hasta el final de la transacción.

    `FOR UPDATE` es el candado de verdad contra el doble post: dos toques
    del botón que se solapan (mala señal, la persona insiste) entran como dos
    requests; la segunda se queda esperando aquí hasta que la primera comitee y
    entonces ya lee `status='publicado'`, así que devuelve lo publicado en vez
    de mandarlo otra vez. Sin el `FOR UPDATE` ambas leerían `'borrador'` a la
    vez y el post saldría dos veces en LinkedIn, que se ve y da pena.

    El `tenant_id` va explícito en el `WHERE` aunque RLS ya filtre
    (`get_tenant_session` fija `app.tenant_id`): es la revalidación server-side
    que promete el docstring de `ApproveDraftAction`, y no depende de que la
    política siga en su sitio.
    """

    result = await session.execute(
        sql_text(
            "SELECT draft_id, user_id, platform, target, text, image_file_id, "
            "video_file_id, status, "
            "published_provider_id, verification FROM social_drafts "
            "WHERE tenant_id = :tenant_id ::uuid "
            "AND (draft_id = :draft_id OR id ::text = :draft_id) "
            "FOR UPDATE"
        ),
        {"tenant_id": str(tenant_id), "draft_id": draft_id},
    )
    row = result.mappings().first()
    return dict(row) if row is not None else None


async def _mark_social_draft_published(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    draft_id: str,
    provider_id: str | None,
    verification: str = "unknown",
) -> None:
    """`borrador -> publicado` + el id que devolvió la red social + SI SE VERIFICÓ.

    `WHERE status = 'borrador'` para que un reintento no pise el
    `published_provider_id` del primer envío (el de verdad).

    `verification` se persiste porque el matiz "se envió pero no se pudo
    comprobar" tiene que sobrevivir al request. Sin guardarlo, el SEGUNDO toque
    sobre esta misma fila (el camino idempotente) no tenía de dónde sacarlo y
    caía en el default del esquema, respondiendo `confirmed` sobre algo que
    nadie había comprobado nunca. Ver la migración `0030` y `app.md`.
    """

    await session.execute(
        sql_text(
            "UPDATE social_drafts SET status = 'publicado', "
            "published_provider_id = :provider_id, verification = :verification, "
            "updated_at = now() "
            "WHERE tenant_id = :tenant_id ::uuid "
            "AND (draft_id = :draft_id OR id ::text = :draft_id) "
            "AND status = 'borrador'"
        ),
        {
            "tenant_id": str(tenant_id),
            "draft_id": draft_id,
            "provider_id": provider_id,
            "verification": verification,
        },
    )


def _published_chat_text(
    target: str, *, verified: Literal["confirmed", "unknown"], verification_note: str = ""
) -> str:
    """La frase que queda escrita en el chat. Portada de REFERENCIA (*"Publicado en
    tu X, señor. ✅"*, `app.py:6060`) SIN el trato de "señor": la formalidad es
    configuración del tenant (`PersonaConfig.formalidad`), no texto fijo del
    producto.

    Con `verified == "unknown"` NUNCA se pone el ✅: `create_linkedin_post` no pudo
    releer el post para confirmar que existe de verdad (típicamente porque el token no
    tiene permiso de lectura, no porque haya fallado la publicación). Fingir certeza acá
    sería repetir el error de hoy -- decirle a la persona que algo se publicó sin haberlo
    comprobado. En su lugar se dice con todas sus letras que no se pudo confirmar y cómo
    comprobarlo ella misma."""

    destination = _resolve_destination(target)
    donde = (
        f"tu página de LinkedIn ({destination.label})"
        if destination.actor == "organization"
        else "tu LinkedIn"
    )
    if verified == "confirmed":
        return f"Publicado en {donde}. ✅"
    # SIN confirmar, la primera palabra NO puede ser "Publicado": el 02-ago-2026 el
    # mensaje decía "Publicado en tu página... Ojo: no pude releerlo" y el post NO
    # estaba en la página -- la persona leyó la primera palabra y dio por hecho el
    # resto ("Se volvió a dañar. No publicó nada."). El orden de las frases ES el
    # mensaje: primero la incertidumbre, después lo que sí se sabe.
    detail = f" ({verification_note})" if verification_note else ""
    return (
        f"Envié el post a {donde} y LinkedIn respondió que lo aceptó, pero NO puedo "
        f"confirmar que esté visible{detail}. Dos causas posibles: la app de LinkedIn "
        "no tiene aprobado el producto de lectura (Community Management API) y por eso "
        "no puedo releerlo, o LinkedIn aceptó y luego retiró el post (su filtro hace "
        "eso en silencio, p. ej. con URLs repetidas). Entra a la página como "
        "administrador y busca el post: si no está, dime y lo investigamos — NO lo des "
        "por publicado."
    )


@router.post(
    "/social/drafts/{draft_id}/publish",
    response_model=SocialDraftPublishOut,
    dependencies=[Depends(_require_connectors_social)],
)
async def publish_social_draft(
    draft_id: Annotated[str, PathParam(min_length=1, max_length=200)],
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    session: AsyncSession = Depends(get_tenant_session),
    settings: Settings = Depends(get_settings),
    vault: Any = Depends(get_vault),
) -> SocialDraftPublishOut:
    """Publica un borrador ya guardado, referenciado SOLO por su `draft_id`.

    Es el "un toque y publicado" del botón "Aprobar y publicar"
    (`ApproveDraftAction`): el teléfono manda el id y nada más -- ni el texto ni
    la imagen viajan de vuelta desde el cliente. Esa asimetría es a propósito:
    lo que se publica es EXACTAMENTE lo que el motor escribió y auditó, sin
    ventana para alterarlo entre que se generó y se aprobó. `POST
    /social/publish` sigue existiendo para el Studio, donde la app sí tiene el
    contenido en mano.

    ## No reescribe la publicación

    Llama a `publish_social_content`, la función que ya publica bien: perfil vs.
    página con `org_urn`, imagen privada leída de S3 y audit log. Acá solo se
    resuelve el `draft_id` y se cuida la idempotencia; toda la conversación con
    LinkedIn vive en un único sitio.

    ## Confirmación

    La confirmación puntual del humano ya ocurrió en el teléfono: el botón abre
    el modal nativo (`TarjetaConfirmacion`) antes de despachar. Por eso esta
    ruta no pide `confirmed` en el cuerpo -- de hecho no lleva cuerpo. Lo que sí
    se revalida server-side, y es lo que importa, es que el borrador sea de
    ESTE tenant.

    ## Idempotente

    Tocar dos veces no publica dos veces: el borrador se lee con `FOR UPDATE` y,
    si ya está `publicado`, se devuelve el `provider_id` de la vez anterior con
    `already_published=true`, sin hablar con LinkedIn ni volver a escribir en el
    chat. Un doble post es visible para todo el mundo y no se puede deshacer
    limpiamente; un segundo toque, en cambio, es lo más normal cuando la señal
    está mala.
    """

    draft = await _lock_social_draft(session, tenant_id=current_user.tenant_id, draft_id=draft_id)
    if draft is None:
        # 404 también cuando el borrador existe pero es de OTRO tenant: la
        # respuesta no debe permitir distinguir "no existe" de "no es tuyo".
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No encontré ese borrador. Pídele a Edecán que lo genere de nuevo.",
        )

    estado = str(draft.get("status") or "")
    target = str(draft.get("target") or PERSONAL_DESTINATION_ID)
    if estado == "publicado":
        provider_id = draft.get("published_provider_id")
        # El eco de un segundo toque devuelve la verificación GUARDADA de la vez que
        # sí se publicó (columna `verification`, migración `0030`), no un optimista
        # "confirmed" por defecto. Antes caía en ese default y afirmaba, en el segundo
        # toque, algo que nadie había comprobado nunca — exactamente la mentira que
        # toda esta cadena de arreglos existe para eliminar.
        # `or "unknown"`: una fila publicada ANTES de que la columna existiera no sabe
        # si se verificó, y el conservador es el correcto (ver `app.md`, 31-jul-2026).
        return SocialDraftPublishOut(
            platform="linkedin",
            provider_id=str(provider_id) if provider_id else None,
            draft_id=draft_id,
            target=target,
            already_published=True,
            verified="confirmed" if draft.get("verification") == "confirmed" else "unknown",
        )
    if estado != "borrador":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ese borrador está en estado '{estado}'; ya no se puede publicar.",
        )

    platform = str(draft.get("platform") or "")
    if platform != "linkedin":
        # `SocialContentPublishIn.platform` es `Literal["linkedin"]` a propósito:
        # el conector de X existe pero todavía no hay camino de publicación
        # probado. Mejor decirlo con todas sus letras que fallar con un 500.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Todavía no puedo publicar en {platform or 'esa red'} desde aquí.",
        )

    # ── Ruta multi-red para Acme ──────────────────────────────────────
    # Cuando el target es "organization" (la página de Acme), el mismo post +
    # video se publica en las 5 redes de un golpe (LinkedIn, Instagram,
    # Facebook, Threads y X), no solo en LinkedIn. Porta el despachador de
    # REFERENCIA (`organization_social.py:organization_redes_publicar`).
    if target in ("organization", "organization_linkedin"):
        multi = await _publish_organization_multi_network(
            repo=repo,
            settings=settings,
            tenant_id=current_user.tenant_id,
            draft=dict(draft),
        )
        if not multi["ok"]:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=multi["chat_text"],
            )
        await _mark_social_draft_published(
            session,
            tenant_id=current_user.tenant_id,
            draft_id=draft_id,
            provider_id=multi["provider_id"],
            verification=multi["verified"],
        )
        try:
            conversation = await repo.resolve_main_conversation(
                tenant_id=current_user.tenant_id, user_id=current_user.user_id
            )
            await repo.add_message(
                tenant_id=current_user.tenant_id,
                conversation_id=UUID(str(conversation["id"])),
                role="assistant",
                content={"text": multi["chat_text"]},
            )
        except Exception:
            logger.warning(
                "Se publicó el borrador multi-red pero no se pudo escribir la confirmación "
                "en el chat (tenant_id=%s draft_id=%s).",
                current_user.tenant_id,
                draft_id,
                exc_info=True,
            )
        return SocialDraftPublishOut(
            platform="linkedin",
            provider_id=multi["provider_id"],
            draft_id=draft_id,
            target=target,
            verified=multi["verified"],
        )

    # ── Ruta LinkedIn-only (perfil personal u otros targets) ─────────────
    try:
        publish_body = SocialContentPublishIn(
            platform="linkedin",
            target=target,
            text=str(draft.get("text") or ""),
            image_file_id=draft.get("image_file_id"),
            video_file_id=draft.get("video_file_id"),
            alt_text="",
            confirmed=True,
        )
    except ValidationError as exc:
        # Un borrador guardado que ya no cabe en el contrato de publicación
        # (texto por encima del límite de LinkedIn, destino con forma inválida).
        # Es un 409 explicativo, no un 500 mudo.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ese borrador no se puede publicar tal como está guardado.",
        ) from exc

    # La que ya publica de verdad (org_urn + imagen privada + audit log). Se
    # invoca como función normal: los `Depends(...)` de su firma solo los
    # resuelve FastAPI cuando entra por su propia ruta. Sus errores ya son
    # accionables tal cual -- "Conecta tu cuenta de LinkedIn antes de publicar"
    # sale con 400 desde ahí, y llega al teléfono como el mensaje del error.
    published = await publish_social_content(
        body=publish_body,
        current_user=current_user,
        repo=repo,
        settings=settings,
        vault=vault,
    )

    await _mark_social_draft_published(
        session,
        tenant_id=current_user.tenant_id,
        draft_id=draft_id,
        provider_id=published.provider_id,
        # Se persiste para que el segundo toque diga la verdad en vez de asumir.
        verification=published.verified,
    )

    # La confirmación se escribe en el chat, como hace REFERENCIA al colgar o al
    # publicar: publicar es irreversible de cara al público y merece quedar por
    # escrito en el hilo, no en un cartel que se va solo. Va en el chat
    # PRINCIPAL de quien tocó el botón -- que es donde el handler dejó la card
    # (`create_linkedin_post.py`, `resolve_main_conversation`) y donde la
    # persona está mirando.
    #
    # Best-effort a propósito: si narrar falla, el post YA salió y la fila ya
    # quedó marcada. Reventar aquí haría rollback de esa marca y el siguiente
    # toque publicaría por segunda vez -- el error que más caro sale de todo
    # este camino.
    try:
        conversation = await repo.resolve_main_conversation(
            tenant_id=current_user.tenant_id, user_id=current_user.user_id
        )
        await repo.add_message(
            tenant_id=current_user.tenant_id,
            conversation_id=UUID(str(conversation["id"])),
            role="assistant",
            content={
                "text": _published_chat_text(
                    target,
                    verified=published.verified,
                    verification_note=published.verification_note,
                )
            },
        )
    except Exception:
        logger.warning(
            "Se publicó el borrador pero no se pudo escribir la confirmación en el chat "
            "(tenant_id=%s draft_id=%s).",
            current_user.tenant_id,
            draft_id,
            exc_info=True,
        )

    return SocialDraftPublishOut(
        platform="linkedin",
        provider_id=published.provider_id,
        draft_id=draft_id,
        target=target,
        verified=published.verified,
        verification_note=published.verification_note,
    )


@router.post("/studio/actions", response_model=StudioProjectActionOut)
async def run_studio_project_action(
    body: StudioProjectActionIn,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
    settings: Settings = Depends(get_settings),
    vault: Any = Depends(get_vault),
    llm_router: LLMRouter = Depends(get_llm_router),
) -> StudioProjectActionOut:
    """Ejecuta una operación del Studio completo desde cualquier cliente.

    Esta ruta no acepta rutas del host ni secretos. Las imágenes llegan como
    ``file_id`` privados y las operaciones sensibles requieren confirmación
    explícita en el cuerpo.
    """

    if body.action in _STUDIO_READ_ACTIONS:
        tool = VerProyectosCreativosTool()
    elif body.action in _STUDIO_WRITE_ACTIONS:
        tool = CrearEditarProyectoCreativoTool()
    else:
        tool = AdministrarProyectoCreativoTool()
    try:
        result = await tool.run(
            _tool_context(
                current_user=current_user,
                session=session,
                settings=settings,
                llm_router=llm_router,
                vault=vault,
            ),
            body.tool_arguments(),
        )
    except Exception as exc:
        logger.exception(
            "studio_project_action_failed",
            extra={"action": body.action, "tenant_id": str(current_user.tenant_id)},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Studio no pudo completar esa operación. Inténtalo nuevamente.",
        ) from exc

    data = result.data or {}
    nested = data.get("result")
    if not isinstance(nested, dict):
        detail = result.content or "Studio no devolvió un resultado utilizable."
        status_code = (
            status.HTTP_409_CONFLICT
            if "app local" in detail.lower()
            else status.HTTP_502_BAD_GATEWAY
        )
        raise HTTPException(status_code=status_code, detail=detail)
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, list):
        artifacts = []
    presentation = result.presentation if isinstance(result.presentation, list) else []

    if body.action in {"create", "edit", "duplicate", "export", "share-package"}:
        artifact_id: str | None = None
        if artifacts and isinstance(artifacts[0], dict):
            candidate = str(artifacts[0].get("file_id") or "")
            try:
                artifact_id = str(UUID(candidate))
            except ValueError:
                artifact_id = None
        event_id = artifact_id or str(uuid4())
        notification_kind = (
            "design_export_ready" if body.action in {"export", "share-package"} else "design_ready"
        )
        notification_payload = {
            "user_id": str(current_user.user_id),
            "kind": notification_kind,
            "event_id": event_id,
        }
        if artifact_id is not None:
            notification_payload["artifact_id"] = artifact_id
        try:
            await enqueue(
                settings,
                "notify_important_event",
                notification_payload,
                current_user.tenant_id,
            )
        except Exception:
            logger.warning(
                "No se pudo encolar el aviso de Studio (tenant_id=%s user_id=%s)",
                current_user.tenant_id,
                current_user.user_id,
                exc_info=True,
            )

    return StudioProjectActionOut(
        action=body.action,
        message=result.content,
        result=nested,
        artifacts=[item for item in artifacts if isinstance(item, dict)],
        presentation=[item for item in presentation if isinstance(item, dict)],
    )


__all__ = ["router"]
