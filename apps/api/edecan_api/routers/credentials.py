"""Credenciales de capacidades conectables por tenant.

La inferencia LLM ya no es una credencial del usuario: Edecán la administra
con Workers AI y el ``TaskRouter`` decide automáticamente. Los endpoints LLM
de escritura se conservan solo para devolver un error explícito a clientes
antiguos; nunca guardan ni cambian proveedor o modelo.

Voz, imágenes y búsqueda continúan siendo capacidades BYO cifradas en
``TokenVault``. ``GET /v1/credentials`` jamás expone secretos completos.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import httpx
from edecan_db.vault import TokenVault
from edecan_schemas import TokenBundle
from edecan_toolkit.research import BraveSearch, TavilySearch
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from edecan_api.config import Settings, get_settings
from edecan_api.deps import (
    VOICE_STT_CONNECTOR_KEY,
    VOICE_TTS_CONNECTOR_KEY,
    CurrentUser,
    get_current_user,
    get_repo,
    get_vault,
    rate_limit,
)
from edecan_api.repo import Repo

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/credentials", tags=["credentials"], dependencies=[Depends(rate_limit)]
)

# ---------------------------------------------------------------------------
# Providers soportados por capacidades BYO
# ---------------------------------------------------------------------------

_TTS_PROVIDERS = frozenset({"elevenlabs", "polly"})
_POLLY_DEFAULT_VOICE = "Lupe"

_SEARCH_PROVIDERS = frozenset({"brave", "tavily"})

_VOICE_STT_DISPLAY_NAME = "Voz — transcripción (STT)"
_VOICE_TTS_DISPLAY_NAME = "Voz — síntesis (TTS)"
_IMAGES_DISPLAY_NAME = "Generación de imágenes"
_SEARCH_DISPLAY_NAME = "Búsqueda web"

# `connector_key` del `TokenVault` — mismo string literal que
# `edecan_creative.providers.IMAGES_CONNECTOR_KEY`/`edecan_toolkit.research.
# SEARCH_CONNECTOR_KEY` (duplicado a propósito, ver el comentario en esos
# módulos: `edecan_api` sí depende de ambos paquetes, pero `LLM_CONNECTOR_KEY`
# ya sienta el precedente de definir el connector_key donde se USA en vez de
# importarlo — este router es el único lugar de `edecan_api` que necesita
# estos dos, a diferencia de `LLM_CONNECTOR_KEY` que también lee `deps.py`).
IMAGES_CONNECTOR_KEY = "images"
SEARCH_CONNECTOR_KEY = "search"
# Solo permite limpiar una credencial LLM guardada por versiones antiguas.
# No participa en la inferencia actual.
LEGACY_LLM_CONNECTOR_KEY = "llm"

_VALIDATE_TIMEOUT_SECONDS = 15.0


# ---------------------------------------------------------------------------
# Bodies de entrada — `validate_` con alias "validate" (no se llama `validate`
# a secas: `pydantic.BaseModel` ya trae un método de clase deprecado con ese
# nombre y definir un campo igual dispara un `UserWarning` de shadowing en
# cada import de este módulo).
# ---------------------------------------------------------------------------


class LLMCredentialsIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    kind: str
    api_key: str | None = None
    base_url: str | None = None
    model_principal: str | None = None
    model_rapido: str | None = None
    model_profundo: str | None = None
    reasoning_effort_profundo: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
    validate_: bool = Field(default=True, alias="validate")


class LLMModelsIn(BaseModel):
    """Cambio de modelo sin volver a pedir ni reemplazar la credencial."""

    model_config = ConfigDict(populate_by_name=True)

    model_principal: str = Field(min_length=1, max_length=240)
    model_rapido: str | None = Field(default=None, max_length=240)
    model_profundo: str | None = Field(default=None, max_length=240)
    reasoning_effort_profundo: str | None = Field(default="xhigh", max_length=24)


class VoiceSTTCredentialsIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    provider: str
    api_key: str
    validate_: bool = Field(default=True, alias="validate")


class VoiceTTSCredentialsIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    provider: str
    api_key: str | None = None
    voice_id: str | None = None
    validate_: bool = Field(default=True, alias="validate")


class ImagesCredentialsIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    base_url: str
    api_key: str
    model: str
    validate_: bool = Field(default=True, alias="validate")


class SearchCredentialsIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    provider: str
    api_key: str
    validate_: bool = Field(default=True, alias="validate")


# ---------------------------------------------------------------------------
# Helpers de `connector_accounts` (singleton por tenant+key, ver docstring)
# ---------------------------------------------------------------------------


async def _find_account(
    repo: Repo, tenant_id: uuid.UUID, connector_key: str
) -> dict[str, Any] | None:
    accounts = await repo.list_connector_accounts(tenant_id=tenant_id)
    matches = [a for a in accounts if a["connector_key"] == connector_key]
    if not matches:
        return None
    # Por si alguna vez hay más de una (no debería, ver `_find_or_create_account`):
    # la más antigua es la que `_find_or_create_account` reutilizaría, así que
    # es la fuente de verdad consistente para lectura también.
    return min(matches, key=lambda a: a["created_at"])


async def _find_or_create_account(
    repo: Repo, tenant_id: uuid.UUID, connector_key: str, display_name: str
) -> dict[str, Any]:
    """Encuentra la `connector_account` del tenant para `connector_key` o crea
    una nueva — ver docstring del módulo ("singleton por tenant"). A
    diferencia de `connect_twilio` (que sí tiene un `external_account_id`
    natural, el número E.164), estas tres claves no lo tienen: se usa el
    propio `connector_key` como `external_account_id` fijo y estable.
    """
    existing = await _find_account(repo, tenant_id, connector_key)
    if existing is not None:
        return existing
    return await repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key=connector_key,
        external_account_id=connector_key,
        display_name=display_name,
        scopes=[],
    )


async def _read_config(
    repo: Repo, vault: TokenVault, tenant_id: uuid.UUID, connector_key: str
) -> dict[str, Any] | None:
    """Config guardada (ya descifrada + parseada) para `connector_key`, o
    `None` si el tenant no conectó nada ahí todavía, o si lo guardado está
    corrupto/ilegible (se registra con `logger.warning`, nunca revienta
    `GET /v1/credentials`)."""
    account = await _find_account(repo, tenant_id, connector_key)
    if account is None:
        return None
    bundle = await vault.get(tenant_id, account["id"])
    if bundle is None:
        return None
    try:
        data = json.loads(bundle.access_token)
    except (TypeError, ValueError):
        logger.warning(
            "Config ilegible en el vault (connector_key=%s, tenant_id=%s).",
            connector_key,
            tenant_id,
        )
        return None
    return data if isinstance(data, dict) else None


def _masked(secret: str | None) -> str | None:
    """`"…" + últimos 4 caracteres` — JAMÁS la credencial completa (ver
    docstring del módulo)."""
    if not secret:
        return None
    return "…" + secret[-4:]


# ---------------------------------------------------------------------------
# Pings de validación — un GET liviano por proveedor (o un subproceso para los
# CLI locales). Todas devuelven `None` si la credencial sirve; lanzan
# `HTTPException(400, ...)` con el detalle EXACTO del proveedor si no.
# ---------------------------------------------------------------------------


async def _get_with_error_handling(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
    proveedor: str,
) -> httpx.Response:
    try:
        async with httpx.AsyncClient(timeout=_VALIDATE_TIMEOUT_SECONDS) as client:
            response = await client.get(url, headers=headers, params=params)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No pudimos conectar con {proveedor}: {exc}",
        ) from exc
    if not (200 <= response.status_code < 300):
        snippet = response.text[:300]
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{proveedor} rechazó la credencial (status {response.status_code}): {snippet}",
        )
    return response


async def _ping_openai_compat(base_url: str, api_key: str | None) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
    url = f"{base_url.rstrip('/')}/models"
    response = await _get_with_error_handling(
        url, headers=headers, proveedor="el endpoint OpenAI-compatible"
    )
    return _json_object(response, "el endpoint OpenAI-compatible")


def _json_object(response: httpx.Response, proveedor: str) -> dict[str, Any]:
    """Lee un catálogo de modelos validado sin aceptar HTML/JSON escalar.

    Un ``200`` con una página de login no demuestra compatibilidad con el
    contrato de modelos. Fallar aquí evita guardar una conexión que rompería
    el primer turno; el cuerpo nunca se incluye para no filtrar datos del
    proveedor.
    """

    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{proveedor} respondió, pero su catálogo de modelos no es JSON válido.",
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{proveedor} respondió con un catálogo de modelos inválido.",
        )
    return payload


async def _ping_deepgram(api_key: str) -> None:
    await _get_with_error_handling(
        "https://api.deepgram.com/v1/projects",
        headers={"Authorization": f"Token {api_key}"},
        proveedor="Deepgram",
    )


async def _ping_elevenlabs(api_key: str) -> None:
    await _get_with_error_handling(
        "https://api.elevenlabs.io/v1/user",
        headers={"xi-api-key": api_key},
        proveedor="ElevenLabs",
    )


async def _ping_brave(api_key: str) -> None:
    """Brave Search no documenta un endpoint dedicado de solo-validación/cuenta
    (a diferencia de `/v1/models` de Anthropic o `/v1/projects` de Deepgram) —
    en su lugar reutiliza `edecan_toolkit.research.BraveSearch` con `k=1`, la
    MISMA clase que ya usa `buscar_web`/`comparar_precios` en producción, así
    el contrato de la petición ya está comprobado por sus propios tests
    (`packages/toolkit/tests/test_research.py::test_brave_search_parsea_resultados`)
    en vez de duplicar la URL/headers a mano acá."""
    try:
        await BraveSearch(api_key).search("prueba de credencial", k=1)
    except httpx.HTTPStatusError as exc:
        codigo, snippet = exc.response.status_code, exc.response.text[:300]
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Brave Search rechazó la credencial (status {codigo}): {snippet}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No pudimos conectar con Brave Search: {exc}",
        ) from exc


async def _ping_tavily(api_key: str) -> None:
    """Mismo criterio que `_ping_brave` (ver su docstring): reutiliza
    `edecan_toolkit.research.TavilySearch` con `k=1` en vez de un endpoint de
    cuenta aparte."""
    try:
        await TavilySearch(api_key).search("prueba de credencial", k=1)
    except httpx.HTTPStatusError as exc:
        snippet = exc.response.text[:300]
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Tavily rechazó la credencial (status {exc.response.status_code}): {snippet}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No pudimos conectar con Tavily: {exc}",
        ) from exc


# ---------------------------------------------------------------------------
# GET /v1/credentials
# ---------------------------------------------------------------------------


def _voice_stt_out(cfg: dict[str, Any] | None) -> dict[str, Any] | None:
    if cfg is None:
        return None
    return {"provider": cfg.get("provider"), "masked": _masked(cfg.get("api_key"))}


def _voice_tts_out(cfg: dict[str, Any] | None) -> dict[str, Any] | None:
    if cfg is None:
        return None
    return {
        "provider": cfg.get("provider"),
        "voice_id": cfg.get("voice_id") or cfg.get("voice"),
        "masked": _masked(cfg.get("api_key")),
    }


def _images_out(cfg: dict[str, Any] | None) -> dict[str, Any] | None:
    if cfg is None:
        return None
    return {
        "base_url": cfg.get("base_url"),
        "model": cfg.get("model"),
        "masked": _masked(cfg.get("api_key")),
    }


def _search_out(cfg: dict[str, Any] | None) -> dict[str, Any] | None:
    if cfg is None:
        return None
    return {"provider": cfg.get("provider"), "masked": _masked(cfg.get("api_key"))}


@router.get("")
async def get_credentials(
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    stt_cfg = await _read_config(repo, vault, current_user.tenant_id, VOICE_STT_CONNECTOR_KEY)
    tts_cfg = await _read_config(repo, vault, current_user.tenant_id, VOICE_TTS_CONNECTOR_KEY)
    images_cfg = await _read_config(repo, vault, current_user.tenant_id, IMAGES_CONNECTOR_KEY)
    search_cfg = await _read_config(repo, vault, current_user.tenant_id, SEARCH_CONNECTOR_KEY)
    workers_ai_ready = bool(
        settings.CLOUDFLARE_ACCOUNT_ID and settings.CLOUDFLARE_API_TOKEN
    )
    return {
        "llm": (
            {
                "kind": "workers_ai",
                "model_principal": settings.WORKERS_AI_CHAT_MODEL,
                "model_rapido": settings.WORKERS_AI_CHAT_MODEL,
                "model_profundo": settings.WORKERS_AI_CHAT_MODEL,
                "reasoning_effort_profundo": None,
                "base_url": None,
                "masked": "Administrado por Edecan",
            }
            if workers_ai_ready
            else None
        ),
        "voice_stt": _voice_stt_out(stt_cfg),
        "voice_tts": _voice_tts_out(tts_cfg),
        "images": _images_out(images_cfg),
        "search": _search_out(search_cfg),
    }


# ---------------------------------------------------------------------------
# PUT/DELETE /v1/credentials/llm
# ---------------------------------------------------------------------------


@router.get("/llm/models")
async def get_llm_models(
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Estado de inferencia administrada; no expone selección al usuario."""

    model = settings.WORKERS_AI_CHAT_MODEL
    return {
        "kind": "workers_ai",
        "model_principal": model,
        "model_rapido": model,
        "model_profundo": model,
        "reasoning_effort_profundo": None,
        "models": [model],
        "manual_allowed": False,
        "capabilities_managed_by_edecan": True,
        "discovery_error": None,
    }


@router.patch("/llm/models", status_code=status.HTTP_204_NO_CONTENT)
async def update_llm_models(
    payload: LLMModelsIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
) -> None:
    """La selección manual desapareció; el Task Router es la única autoridad."""

    del payload, current_user, repo, vault
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Edecan elige el modelo automáticamente según la tarea.",
    )


@router.put("/llm", status_code=status.HTTP_204_NO_CONTENT)
async def put_llm_credentials(
    payload: LLMCredentialsIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
    settings: Settings = Depends(get_settings),
) -> None:
    del payload, current_user, repo, vault, settings
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            "La inferencia está administrada por Edecan con Workers AI. "
            "No se conecta ni se selecciona un proveedor desde esta cuenta."
        ),
    )


@router.delete("/llm", status_code=status.HTTP_204_NO_CONTENT)
async def delete_llm_credentials(
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> None:
    account = await _find_account(repo, current_user.tenant_id, LEGACY_LLM_CONNECTOR_KEY)
    if account is None:
        return  # idempotente: nada que borrar ya es un estado válido de "desconectado".
    await repo.delete_connector_account(tenant_id=current_user.tenant_id, account_id=account["id"])
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="credentials.llm.disconnected",
        target=LEGACY_LLM_CONNECTOR_KEY,
    )


# ---------------------------------------------------------------------------
# PUT/DELETE /v1/credentials/voice/{stt,tts}
# ---------------------------------------------------------------------------


@router.put("/voice/stt", status_code=status.HTTP_204_NO_CONTENT)
async def put_voice_stt_credentials(
    payload: VoiceSTTCredentialsIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
) -> None:
    provider = payload.provider.strip().lower()
    if provider != "deepgram":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"provider desconocido: {payload.provider!r}. Solo 'deepgram' por ahora.",
        )
    api_key = payload.api_key.strip()
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="api_key no puede estar vacío."
        )

    if payload.validate_:
        await _ping_deepgram(api_key)

    config_dict = {"provider": "deepgram", "api_key": api_key}
    account = await _find_or_create_account(
        repo, current_user.tenant_id, VOICE_STT_CONNECTOR_KEY, _VOICE_STT_DISPLAY_NAME
    )
    await vault.put(
        current_user.tenant_id,
        account["id"],
        TokenBundle(access_token=json.dumps(config_dict), token_type="config", scopes=["deepgram"]),
    )
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="credentials.voice_stt.connected",
        target="deepgram",
    )


@router.put("/voice/tts", status_code=status.HTTP_204_NO_CONTENT)
async def put_voice_tts_credentials(
    payload: VoiceTTSCredentialsIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
    settings: Settings = Depends(get_settings),
) -> None:
    provider = payload.provider.strip().lower()
    if provider not in _TTS_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"provider desconocido: {payload.provider!r}. Debe ser 'elevenlabs' o 'polly'.",
        )

    # Ver docstring del módulo ("EDECAN_LOCAL_MODE... por el mismo motivo"):
    # Polly no tiene credencial propia del tenant, usa la identidad AWS del
    # PROCESO — fuera de modo local esa identidad se compartiría entre
    # tenants, así que se rechaza aquí antes de guardar nada (mismo criterio
    # que los proveedores locales heredados en `PUT /v1/credentials/llm`).
    if provider == "polly" and not getattr(settings, "EDECAN_LOCAL_MODE", False):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "'polly' requiere la app de escritorio (modo local): usa la cadena de "
                "credenciales AWS del PROCESO que corre el backend, no una api_key propia "
                "del tenant, así que en un servidor hospedado/compartido terminaría "
                "compartiendo una sola identidad AWS entre tenants. Usa 'elevenlabs' (con "
                "tu propia api_key), o instala la app de escritorio de Edecán."
            ),
        )

    voice_id = (payload.voice_id or "").strip() or None
    api_key = (payload.api_key or "").strip() or None

    if provider == "elevenlabs":
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="'elevenlabs' requiere api_key."
            )
        if payload.validate_:
            await _ping_elevenlabs(api_key)
        config_dict: dict[str, Any] = {
            "provider": "elevenlabs",
            "api_key": api_key,
            "voice_id": voice_id,
        }
        scopes = ["elevenlabs"]
    else:
        # Polly no valida la key: usa la cadena de credenciales AWS estándar
        # del propio cliente, no una "key" única (ver edecan_voice.polly y
        # docs/voz-telefonia.md) — `validate_=true` no dispara ningún ping de
        # red para este proveedor.
        config_dict = {"provider": "polly", "voice": voice_id or _POLLY_DEFAULT_VOICE}
        scopes = ["polly"]

    account = await _find_or_create_account(
        repo, current_user.tenant_id, VOICE_TTS_CONNECTOR_KEY, _VOICE_TTS_DISPLAY_NAME
    )
    await vault.put(
        current_user.tenant_id,
        account["id"],
        TokenBundle(access_token=json.dumps(config_dict), token_type="config", scopes=scopes),
    )
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="credentials.voice_tts.connected",
        target=provider,
    )


_VOICE_CANAL_TO_CONNECTOR_KEY = {"stt": VOICE_STT_CONNECTOR_KEY, "tts": VOICE_TTS_CONNECTOR_KEY}


@router.delete("/voice/{canal}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_voice_credentials(
    canal: str,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> None:
    connector_key = _VOICE_CANAL_TO_CONNECTOR_KEY.get(canal)
    if connector_key is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"canal desconocido: {canal!r}. Debe ser 'stt' o 'tts'.",
        )
    account = await _find_account(repo, current_user.tenant_id, connector_key)
    if account is None:
        return  # idempotente, ver delete_llm_credentials.
    await repo.delete_connector_account(tenant_id=current_user.tenant_id, account_id=account["id"])
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action=f"credentials.voice_{canal}.disconnected",
        target=connector_key,
    )


# ---------------------------------------------------------------------------
# PUT/DELETE /v1/credentials/images — bring-your-own de generación de
# imágenes (auditoría "riesgo-legal-tos", ver docstring del módulo: antes de
# esto, `edecan_creative.providers.get_image_provider` solo leía
# `IMAGES_API_KEY` de plataforma, sin ningún mecanismo bring-your-own).
# ---------------------------------------------------------------------------


@router.put("/images", status_code=status.HTTP_204_NO_CONTENT)
async def put_images_credentials(
    payload: ImagesCredentialsIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
) -> None:
    base_url = payload.base_url.strip()
    api_key = payload.api_key.strip()
    model = payload.model.strip()
    if not base_url or not api_key or not model:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="base_url, api_key y model son obligatorios.",
        )

    if payload.validate_:
        # Mismo ping que ya usa `kind: "openai_compat"` de LLM (`_ping_openai_compat`,
        # `GET {base_url}/models`): el proveedor de imágenes de hoy
        # (`OpenAICompatImagesProvider`) habla el mismo contrato OpenAI-compatible.
        await _ping_openai_compat(base_url, api_key)

    config_dict = {"base_url": base_url, "api_key": api_key, "model": model}
    account = await _find_or_create_account(
        repo, current_user.tenant_id, IMAGES_CONNECTOR_KEY, _IMAGES_DISPLAY_NAME
    )
    await vault.put(
        current_user.tenant_id,
        account["id"],
        TokenBundle(
            access_token=json.dumps(config_dict), token_type="config", scopes=["openai_compat"]
        ),
    )
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="credentials.images.connected",
        target="openai_compat",
    )


@router.delete("/images", status_code=status.HTTP_204_NO_CONTENT)
async def delete_images_credentials(
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> None:
    account = await _find_account(repo, current_user.tenant_id, IMAGES_CONNECTOR_KEY)
    if account is None:
        return  # idempotente, ver delete_llm_credentials.
    await repo.delete_connector_account(tenant_id=current_user.tenant_id, account_id=account["id"])
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="credentials.images.disconnected",
        target=IMAGES_CONNECTOR_KEY,
    )


# ---------------------------------------------------------------------------
# PUT/DELETE /v1/credentials/search — bring-your-own de búsqueda web (misma
# auditoría que arriba: `edecan_toolkit.research.get_search_provider` solo
# leía `BRAVE_API_KEY`/`TAVILY_API_KEY` de plataforma).
# ---------------------------------------------------------------------------


@router.put("/search", status_code=status.HTTP_204_NO_CONTENT)
async def put_search_credentials(
    payload: SearchCredentialsIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
) -> None:
    provider = payload.provider.strip().lower()
    if provider not in _SEARCH_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"provider desconocido: {payload.provider!r}. Debe ser 'brave' o 'tavily'.",
        )
    api_key = payload.api_key.strip()
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="api_key no puede estar vacío."
        )

    if payload.validate_:
        if provider == "brave":
            await _ping_brave(api_key)
        else:
            await _ping_tavily(api_key)

    config_dict = {"provider": provider, "api_key": api_key}
    account = await _find_or_create_account(
        repo, current_user.tenant_id, SEARCH_CONNECTOR_KEY, _SEARCH_DISPLAY_NAME
    )
    await vault.put(
        current_user.tenant_id,
        account["id"],
        TokenBundle(access_token=json.dumps(config_dict), token_type="config", scopes=[provider]),
    )
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="credentials.search.connected",
        target=provider,
    )


@router.delete("/search", status_code=status.HTTP_204_NO_CONTENT)
async def delete_search_credentials(
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> None:
    account = await _find_account(repo, current_user.tenant_id, SEARCH_CONNECTOR_KEY)
    if account is None:
        return  # idempotente, ver delete_llm_credentials.
    await repo.delete_connector_account(tenant_id=current_user.tenant_id, account_id=account["id"])
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="credentials.search.disconnected",
        target=SEARCH_CONNECTOR_KEY,
    )
