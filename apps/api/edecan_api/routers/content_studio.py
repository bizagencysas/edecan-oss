"""Mini estudio de contenido social para los clientes móviles.

Este endpoint no publica ni conecta cuentas sociales. Convierte una idea breve
en un borrador editable y crea artefactos privados (Markdown, manifiesto e
imagen opcional) usando la misma herramienta que el chat. Tener una ruta
dedicada permite que iOS y Android muestren progreso y entreguen el resultado
sin obligar a la persona a entender herramientas, prompts o conversaciones.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal

from edecan_core import ToolContext
from edecan_core.queue import enqueue
from edecan_creative.social import CrearContenidoSocialTool
from edecan_llm.base import ChatMessage, CompletionRequest
from edecan_llm.router import LLMRouter
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
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

_LLM_ALIAS = "principal"
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


class SocialContentArtifactOut(BaseModel):
    file_id: str
    filename: str
    mime: str | None = None


class SocialContentOut(BaseModel):
    status: Literal["ready"] = "ready"
    platform: Literal["linkedin", "x"]
    post_text: str = Field(serialization_alias="copy")
    parts: list[str]
    alt_text: str = ""
    offline_visual: bool = False
    artifacts: list[SocialContentArtifactOut]
    requires_human_confirmation: bool = True


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
    tool = CrearContenidoSocialTool()
    try:
        result = await tool.run(
            ToolContext(
                tenant_id=current_user.tenant_id,
                user_id=current_user.user_id,
                session=session,
                settings=settings,
                llm=llm_router,
                vault=vault,
                extras={"flags": current_user.tenant.flags},
            ),
            args,
        )
    except HTTPException:
        raise
    except Exception as exc:
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
            "No se pudo encolar la notificación del Content Studio "
            "(tenant_id=%s user_id=%s)",
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
        artifacts=[SocialContentArtifactOut.model_validate(item) for item in artifacts],
    )


__all__ = ["router"]
