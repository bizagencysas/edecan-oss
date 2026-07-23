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
import re
from datetime import date
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import aioboto3
import httpx
from edecan_connectors.base import ConnectorError
from edecan_connectors.social.linkedin import create_post as create_linkedin_post
from edecan_core import ToolContext
from edecan_core.freshness import assess_freshness, grounding_queries, official_source_domains
from edecan_core.queue import enqueue
from edecan_creative.social import (
    CrearContenidoSocialTool,
    get_editorial_profile,
    save_editorial_profile,
)
from edecan_design_studio.studio_tools import (
    AdministrarProyectoCreativoTool,
    CrearEditarProyectoCreativoTool,
    VerProyectosCreativosTool,
)
from edecan_llm.base import ChatMessage, CompletionRequest
from edecan_llm.router import LLMRouter
from edecan_schemas import FLAG_CONNECTORS_SOCIAL
from edecan_toolkit.research import get_tenant_search_provider
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
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

router = APIRouter(
    prefix="/v1/content",
    tags=["content"],
    dependencies=[Depends(rate_limit)],
)
logger = logging.getLogger(__name__)

_LLM_ALIAS = "profundo"
_MAX_RESPONSE_TOKENS = 1800
_PLATFORM_LIMITS = {"linkedin": 3000, "x": 8_000}

_SYSTEM_PROMPT = """Eres el editor social de un asistente personal. Crea contenido verdadero,
específico y listo para que la persona lo revise y comparta manualmente. No inventes datos,
resultados, clientes, experiencias ni citas. No digas que publicaste nada.

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


class SocialEditorialProfileOut(SocialEditorialProfileIn):
    platform: Literal["linkedin", "x"] = "linkedin"
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
    text: str = Field(min_length=1, max_length=3000)
    image_file_id: UUID | None = None
    alt_text: str = Field(default="", max_length=4086)
    confirmed: bool = False


class SocialContentPublishOut(BaseModel):
    status: Literal["published"] = "published"
    platform: Literal["linkedin"]
    provider_id: str | None = None


_MAX_PUBLISH_IMAGE_BYTES = 20 * 1024 * 1024


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
    """Obtiene evidencia primaria para temas que pueden haber cambiado."""

    expected_domains = official_source_domains(topic)
    if not expected_domains and not assess_freshness(topic).required:
        return []

    provider = await get_tenant_search_provider(ctx)
    selected: list[dict[str, str]] = []
    seen: set[str] = set()
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
    editorial_profile = await get_editorial_profile(tool_context, body.platform)
    editorial_context = (
        "\nPerfil editorial persistente de esta persona:\n"
        + json.dumps(editorial_profile, ensure_ascii=False)
        if editorial_profile.get("configured")
        else (
            "\nEsta persona aún no configuró una estrategia editorial. Conserva un resultado "
            "útil y neutral, pero no inventes audiencia, experiencia ni identidad de marca."
        )
    )
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
                    f"{editorial_context}"
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
        meta={"alias": _LLM_ALIAS, "job": "content_studio_social", "platform": body.platform},
    )

    args = _generated_args(response.text, body)
    args["fuentes"] = sources
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

    data = result.data or {}
    artifacts = data.get("artifacts")
    parts = data.get("parts")
    if not isinstance(artifacts, list) or not artifacts or not isinstance(parts, list) or not parts:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=result.content or "No se pudo crear el paquete de contenido.",
        )

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
    return SocialEditorialProfileOut.model_validate(await get_editorial_profile(ctx, platform))


@router.put("/social/profile", response_model=SocialEditorialProfileOut)
async def put_social_editorial_profile(
    body: SocialEditorialProfileIn,
    platform: Literal["linkedin", "x"] = "linkedin",
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
    saved = await save_editorial_profile(ctx, platform, body.model_dump())
    return SocialEditorialProfileOut.model_validate(saved)


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
    if len(content) > _MAX_PUBLISH_IMAGE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="La imagen supera el límite de 20 MB.",
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
    account = next(
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
    try:
        async with httpx.AsyncClient(timeout=45.0) as http:
            result = await create_linkedin_post(
                http,
                bundle,
                text=body.text,
                image=image[0] if image else None,
                image_content_type=image[1] if image else "image/png",
                alt_text=body.alt_text,
            )
    except ConnectorError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    provider_id = result.get("id")
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="content.linkedin_published",
        target=str(provider_id or account["id"]),
    )
    try:
        event_id = str(provider_id or uuid4())
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
