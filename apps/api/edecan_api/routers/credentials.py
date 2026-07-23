"""`/v1/credentials/*` — bring-your-own de LLM y voz (STT/TTS) por tenant
(ARCHITECTURE.md §10.4, §10.6, §10.9; `DIRECCION_ACTUAL.md` "Modelo de
credenciales: TODO lo trae el cliente, siempre"; `docs/credenciales.md`).

Este router NO se monta a sí mismo: `edecan_api.main` (WP-V3-01) lo monta de
forma defensiva, igual que el resto de routers v2/v3 (`importlib.util.find_spec`
o un `try/except ImportError` alrededor del `include_router`) — este módulo
solo declara `router`.

## Qué resuelve

Antes de este WP, `apps/api/edecan_api/deps.py::get_llm_router` y
`apps/api/edecan_api/routers/voice.py` construían el proveedor LLM/voz con
`ANTHROPIC_API_KEY`/`DEEPGRAM_API_KEY`/`ELEVENLABS_API_KEY` de `Settings` — un
único `.env` de PLATAFORMA compartido por todos los tenants. Igual que Twilio
(`PUT /v1/connectors/twilio/credentials`, ya bring-your-own desde v1), este
router deja que CADA tenant conecte su propia credencial, cifrada en el
`TokenVault` (ARCHITECTURE.md §10.4) bajo una `connector_account` propia:

| Recurso     | `connector_key`  | Guardado por                    |
|-------------|-------------------|----------------------------------|
| LLM         | `"llm"`           | `PUT /v1/credentials/llm`        |
| Voz (STT)   | `"voice_stt"`     | `PUT /v1/credentials/voice/stt`  |
| Voz (TTS)   | `"voice_tts"`     | `PUT /v1/credentials/voice/tts`  |
| Imágenes    | `"images"`        | `PUT /v1/credentials/images`     |
| Búsqueda web| `"search"`        | `PUT /v1/credentials/search`     |

`"images"`/`"search"` (auditoría "riesgo-legal-tos": antes de esto,
`edecan_creative.providers.get_image_provider`/`edecan_toolkit.research.
get_search_provider` SOLO leían `IMAGES_API_KEY`/`BRAVE_API_KEY`/
`TAVILY_API_KEY` de la config de PLATAFORMA — sin excepción ni mecanismo
alguno para que un tenant trajera la propia, a diferencia de LLM/voz/Twilio/
conectores OAuth) siguen el mismo criterio "tenant → capacidad propia, SIN paso de
plataforma" que ya sigue voz (`apps/api/edecan_api/routers/voice.py::
_stt_para_tenant`/`_tts_para_tenant`): `edecan_creative.providers.
get_tenant_image_provider(ctx)` y `edecan_toolkit.research.
get_tenant_search_provider(ctx)` leen el `TokenVault` directo desde la propia
`Tool` (`ctx.session`/`ctx.vault`/`ctx.tenant_id`, ya presentes en
`ToolContext`) en vez de por un `load_tenant_*_config` centralizado acá — no
hay una "resolución por request" equivalente a `get_llm_router` para tools
individuales, así que se resuelve perezosamente en el momento en que la tool
corre; si el tenant no conectó nada (o falla cualquier paso), esas dos
funciones caen DIRECTO a `StubImageProvider`/`DuckDuckGoSearch`, nunca a
`IMAGES_API_KEY`/`BRAVE_API_KEY`/`TAVILY_API_KEY` de plataforma — ver
`docs/credenciales.md`. Imágenes conservan un generador local de demostración;
búsqueda web sí usa internet real sin API key. Body de cada uno:

Cada una de estas tres claves es SINGLETON por tenant (a diferencia de un
conector OAuth, donde un tenant puede tener varias cuentas del mismo
proveedor): `_find_or_create_account` busca la `connector_account` existente
para `(tenant_id, connector_key)` y la reutiliza si ya existe, en vez de crear
una nueva cada vez que el tenant actualiza su credencial (mismo espíritu que
`connect_twilio` en `routers/connectors.py`, adaptado: estas cinco claves no
tienen un `external_account_id` natural como el número E.164 de Twilio, así
que se usa el propio `connector_key` como `external_account_id` fijo).
`create_connector_account` (`edecan_api.repo.Repo`, compartido con el resto
de conectores) siempre guarda `status="active"` — no se introduce un valor de
`status` nuevo solo para estas cinco filas, por consistencia con Twilio/Google/
Telegram/Discord/Slack, que usan la misma columna con el mismo valor.

`TokenBundle.access_token` guarda el JSON de la config (`token_type="config"`,
NO es un token OAuth real) — `edecan_db.vault.TokenVault` lo cifra igual que
cualquier otro secreto de tenant, sin cambios en ese paquete:

- LLM: `{"kind", "api_key", "base_url", "model_principal", "model_rapido", "extra"}`
  — mismos campos que `edecan_llm.config.LLMProviderConfig` (WP-V3-03, ver
  `edecan_api.deps.load_tenant_llm_config`).
- Voz STT: `{"provider": "deepgram", "api_key"}`.
- Voz TTS: `{"provider": "elevenlabs", "api_key", "voice_id"}` o
  `{"provider": "polly", "voice"}` (Polly no guarda API key: usa la cadena de
  credenciales AWS estándar del propio cliente, ver `edecan_voice.polly` —
  por eso, a diferencia de los demás proveedores de esta lista, SOLO se
  acepta con `EDECAN_LOCAL_MODE=True`, ver más abajo).
- Imágenes: `{"base_url", "api_key", "model"}` — mismos campos que
  `edecan_creative.providers.OpenAICompatImagesProvider` (único proveedor
  real hoy, ver `IMAGES_PROVIDER=openai_compat` en `configuracion.md`).
- Búsqueda web: `{"provider": "brave"|"tavily", "api_key"}`.

## "Pegar y validar" (`DIRECCION_ACTUAL.md` "Principio de UX no negociable")

Cada `PUT` acepta `validate: bool = true`: si `true` (default), antes de
guardar nada se hace UNA llamada liviana real al proveedor (`_ping_*`,
`GET` de bajo costo, o `claude --version`/`codex --version` como subproceso
para los CLIs) para confirmar que la credencial sirve — igual que pedirle a
alguien "prueba tu llave antes de guardarla". Si el proveedor rechaza (o no
responde), se devuelve `400` con el detalle EXACTO que dio el proveedor
(status + fragmento del cuerpo) para que la UI lo muestre tal cual — nunca se
guarda nada si la validación falla. `validate: false` es la escotilla de
escape para guardar sin pegarle a la red (tests, migraciones, kinds que el
propio dueño del proyecto sabe que están bien).

`claude_cli`/`codex_cli`/`ollama` SOLO se aceptan si
`getattr(settings, "EDECAN_LOCAL_MODE", False)` es verdadero: los tres asumen
que el backend corre LOCAL en la máquina del cliente (`DIRECCION_ACTUAL.md`
"Nuevo requisito: conectar el LLM vía CLI local") — apuntar un servidor
hospedado a `claude`/`codex`/`http://localhost:11434` no tiene sentido (esos
binarios/puertos son de la máquina del SERVIDOR, no la del cliente). En
hosted, devuelven `400` con un detalle claro de que requieren la app de
escritorio. `getattr` con default `False` (nunca `settings.EDECAN_LOCAL_MODE`
directo): `Settings` puede no declarar ese campo todavía si WP-V3-01 no ha
aterrizado esa pieza — no debe romper el import de este router.

`PUT /v1/credentials/voice/tts` con `provider="polly"` exige el MISMO
`EDECAN_LOCAL_MODE=True`, por el mismo motivo: Polly no tiene un campo de
credencial propia del tenant (a diferencia de `elevenlabs`) — se autentica
SIEMPRE con la cadena de credenciales AWS del PROCESO que corre el backend
(`edecan_voice.polly.PollyTTS`, `aioboto3.Session()` por defecto). Esa
identidad de proceso solo es de verdad "la del tenant" cuando el backend
corre `EDECAN_LOCAL_MODE=True` (single-user, la máquina ES la del cliente);
en cualquier despliegue que sirva a más de un tenant desde el mismo proceso
(hosted compartido, o un self-host que dé acceso a varios clientes/equipos),
dos tenants que elijan `polly` compartirían la MISMA identidad AWS — el
mismo patrón "llave compartida de plataforma" que este documento prohíbe
para los demás proveedores. Fuera de `EDECAN_LOCAL_MODE`, se rechaza con
`400` (mismo mensaje "instala la app de escritorio"); una fila `polly` ya
guardada de ANTES de este gate (o migrada desde una instalación local a un
servidor hospedado) se vuelve a comprobar en `_tts_para_tenant`
(`apps/api/edecan_api/routers/voice.py`) y `resolver_tts_del_tenant`
(`packages/voice/edecan_voice/tenant.py`) — misma doble capa de defensa que
`_build_provider_from_config` en `packages/llm/edecan_llm/router.py`.

`GET /v1/credentials` JAMÁS devuelve la credencial completa: `masked` es
siempre `"…" + últimos 4 caracteres` (o `None` si no hay credencial guardada
o el proveedor no usa API key, como Polly).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any

import httpx
from edecan_db.vault import TokenVault
from edecan_llm import choose_discovered_models, discovered_model_ids
from edecan_schemas import TokenBundle
from edecan_toolkit.research import BraveSearch, TavilySearch
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from edecan_api.config import Settings, get_settings
from edecan_api.deps import (
    LLM_CONNECTOR_KEY,
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
# Kinds/providers soportados
# ---------------------------------------------------------------------------

_LLM_KINDS = frozenset(
    {"anthropic", "openai_compat", "vertex", "claude_cli", "codex_cli", "ollama"}
)
# Ver docstring del módulo: estos tres solo tienen sentido con el backend
# corriendo en la máquina del propio cliente.
_LOCAL_ONLY_LLM_KINDS = frozenset({"claude_cli", "codex_cli", "ollama"})
_LLM_KINDS_REQUIEREN_API_KEY = frozenset({"anthropic", "vertex"})
_OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434"
_CLI_BINARIES = {"claude_cli": "claude", "codex_cli": "codex"}

_TTS_PROVIDERS = frozenset({"elevenlabs", "polly"})
_POLLY_DEFAULT_VOICE = "Lupe"

_SEARCH_PROVIDERS = frozenset({"brave", "tavily"})

_LLM_DISPLAY_NAME = "Proveedor LLM"
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

_VALIDATE_TIMEOUT_SECONDS = 15.0
_CLI_VERSION_TIMEOUT_SECONDS = 10.0


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


async def _ping_anthropic(api_key: str) -> dict[str, Any]:
    response = await _get_with_error_handling(
        "https://api.anthropic.com/v1/models",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        proveedor="Anthropic",
    )
    return _json_object(response, "Anthropic")


async def _ping_openai_compat(base_url: str, api_key: str | None) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
    url = f"{base_url.rstrip('/')}/models"
    response = await _get_with_error_handling(
        url, headers=headers, proveedor="el endpoint OpenAI-compatible"
    )
    return _json_object(response, "el endpoint OpenAI-compatible")


async def _ping_vertex_api_key(api_key: str) -> dict[str, Any]:
    response = await _get_with_error_handling(
        "https://generativelanguage.googleapis.com/v1beta/models",
        params={"key": api_key},
        proveedor="Google AI (Gemini/Vertex)",
    )
    return _json_object(response, "Google AI (Gemini/Vertex)")


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


async def _ping_vertex_service_account(service_account_json: str) -> None:
    """Valida la FORMA de la clave de cuenta de servicio de GCP — JSON
    parseable + las claves mínimas que exige un service account
    (`client_email`, `private_key`). A diferencia del resto de `_ping_*`,
    NO hace una llamada de red real a Google: autenticar de verdad requiere
    `google-auth`, extra OPCIONAL de `edecan-llm`
    (`packages/llm/pyproject.toml`, `[project.optional-dependencies] vertex`,
    ver también `docs/proveedores-llm.md`) que `apps/api` no instala por
    defecto. Exigirlo aquí bloquearía guardar la credencial en cualquier
    despliegue sin ese extra — el mismo bug ("imposible de guardar") que este
    ping existe para evitar. `VertexAIProvider`
    (`packages/llm/edecan_llm/vertex.py`) hace la validación real (obtiene un
    token OAuth2 de verdad) la primera vez que el tenant manda un mensaje,
    con el mismo `LLMError` claro si la clave no sirve.
    """
    try:
        info = json.loads(service_account_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"El JSON de la cuenta de servicio no es válido: {exc}",
        ) from exc
    if not isinstance(info, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El JSON de la cuenta de servicio debe ser un objeto.",
        )
    faltantes = [campo for campo in ("client_email", "private_key") if not info.get(campo)]
    if faltantes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "El JSON de la cuenta de servicio no tiene los campos esperados "
                f"({', '.join(faltantes)}): ¿es la clave completa que descargaste de GCP?"
            ),
        )


async def _ping_ollama(base_url: str) -> None:
    url = f"{base_url.rstrip('/')}/api/tags"
    await _get_with_error_handling(url, proveedor="Ollama")


def _modelos_codex_cache() -> list[str]:
    """Lee el catálogo local que mantiene Codex CLI sin ejecutar ni autenticar nada.

    Codex CLI no expone hoy un comando estable ``models list``. Su cache sí
    contiene el catálogo que la propia instalación ya obtuvo, incluyendo la
    visibilidad de cada modelo. Si el formato cambia o el archivo todavía no
    existe, Ajustes conserva el flujo manual en lugar de fallar.
    """

    codex_root = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")).expanduser()
    cache_path = codex_root / "models_cache.json"
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return []

    raw_models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(raw_models, list):
        return []

    models: list[str] = []
    for item in raw_models:
        if not isinstance(item, dict) or item.get("visibility") == "hide":
            continue
        slug = str(item.get("slug") or "").strip()
        if slug and slug not in models:
            models.append(slug)
    return models


async def _modelos_disponibles(cfg: dict[str, Any]) -> list[str]:
    """Catálogo actual del proveedor conectado, sin exponer su credencial."""

    kind = str(cfg.get("kind") or "")
    payload: dict[str, Any] | None = None
    if kind == "anthropic" and cfg.get("api_key"):
        payload = await _ping_anthropic(str(cfg["api_key"]))
    elif kind == "openai_compat" and cfg.get("base_url"):
        payload = await _ping_openai_compat(str(cfg["base_url"]), cfg.get("api_key"))
    elif kind == "vertex" and cfg.get("api_key"):
        payload = await _ping_vertex_api_key(str(cfg["api_key"]))
    elif kind == "ollama":
        base_url = str(cfg.get("base_url") or _OLLAMA_DEFAULT_BASE_URL).rstrip("/")
        response = await _get_with_error_handling(f"{base_url}/api/tags", proveedor="Ollama")
        data = _json_object(response, "Ollama")
        raw_models = data.get("models") or []
        return [
            str(item.get("name")).strip()
            for item in raw_models
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        ]
    elif kind == "codex_cli":
        return _modelos_codex_cache()
    if payload is None or kind not in {"anthropic", "openai_compat", "vertex"}:
        return []
    return discovered_model_ids(kind, payload)


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


async def _ping_cli_binary(binary: str) -> None:
    """Verifica que `binary` (`"claude"`/`"codex"`) esté instalado y responda,
    corriendo `binary --version` como subproceso con timeout de
    `_CLI_VERSION_TIMEOUT_SECONDS` — nunca cuelga la request esperando un
    binario que no existe o no responde."""
    try:
        process = await asyncio.create_subprocess_exec(
            binary,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"No encontramos el binario '{binary}' instalado en esta máquina. "
                "Instálalo y vuelve a intentar (la app de escritorio lo detecta "
                "automáticamente si ya está instalado)."
            ),
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No pudimos ejecutar '{binary} --version': {exc}",
        ) from exc

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=_CLI_VERSION_TIMEOUT_SECONDS
        )
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"'{binary} --version' no respondió en {_CLI_VERSION_TIMEOUT_SECONDS:.0f}s.",
        ) from exc

    if process.returncode != 0:
        detail = (stderr or stdout or b"").decode("utf-8", errors="replace").strip()[:300]
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"'{binary} --version' falló (código {process.returncode}): {detail}",
        )


# ---------------------------------------------------------------------------
# GET /v1/credentials
# ---------------------------------------------------------------------------


def _llm_out(cfg: dict[str, Any] | None) -> dict[str, Any] | None:
    if cfg is None:
        return None
    return {
        "kind": cfg.get("kind"),
        "model_principal": cfg.get("model_principal"),
        "model_rapido": cfg.get("model_rapido"),
        "model_profundo": cfg.get("model_profundo"),
        "reasoning_effort_profundo": cfg.get("reasoning_effort_profundo"),
        "base_url": cfg.get("base_url"),
        "masked": _masked(cfg.get("api_key")),
    }


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
) -> dict[str, Any]:
    llm_cfg = await _read_config(repo, vault, current_user.tenant_id, LLM_CONNECTOR_KEY)
    stt_cfg = await _read_config(repo, vault, current_user.tenant_id, VOICE_STT_CONNECTOR_KEY)
    tts_cfg = await _read_config(repo, vault, current_user.tenant_id, VOICE_TTS_CONNECTOR_KEY)
    images_cfg = await _read_config(repo, vault, current_user.tenant_id, IMAGES_CONNECTOR_KEY)
    search_cfg = await _read_config(repo, vault, current_user.tenant_id, SEARCH_CONNECTOR_KEY)
    return {
        "llm": _llm_out(llm_cfg),
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
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
) -> dict[str, Any]:
    """Modelos detectados y selección actual, sin devolver la API key.

    Los CLI que no publican un catálogo estable siguen admitiendo un ID
    manual. Si el proveedor está temporalmente caído, Ajustes continúa siendo
    utilizable y devuelve la selección guardada junto con ``discovery_error``.
    """

    cfg = await _read_config(repo, vault, current_user.tenant_id, LLM_CONNECTOR_KEY)
    if cfg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conecta primero un proveedor de inteligencia.",
        )
    error: str | None = None
    try:
        discovered = await _modelos_disponibles(cfg)
    except HTTPException as exc:
        discovered = []
        error = str(exc.detail)

    actuales = [
        cfg.get("model_principal"),
        cfg.get("model_rapido"),
        cfg.get("model_profundo"),
    ]
    modelos: list[str] = []
    for modelo in [*actuales, *discovered]:
        limpio = str(modelo or "").strip()
        if limpio and limpio not in modelos:
            modelos.append(limpio)
    return {
        "kind": cfg.get("kind"),
        "model_principal": cfg.get("model_principal"),
        "model_rapido": cfg.get("model_rapido"),
        "model_profundo": cfg.get("model_profundo"),
        "reasoning_effort_profundo": cfg.get("reasoning_effort_profundo") or "xhigh",
        "models": modelos,
        "manual_allowed": True,
        "capabilities_managed_by_edecan": True,
        "discovery_error": error,
    }


@router.patch("/llm/models", status_code=status.HTTP_204_NO_CONTENT)
async def update_llm_models(
    payload: LLMModelsIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
) -> None:
    """Cambia el modelo activo sin pedir de nuevo la credencial guardada."""

    account = await _find_account(repo, current_user.tenant_id, LLM_CONNECTOR_KEY)
    cfg = await _read_config(repo, vault, current_user.tenant_id, LLM_CONNECTOR_KEY)
    if account is None or cfg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conecta primero un proveedor de inteligencia.",
        )

    principal = payload.model_principal.strip()
    rapido = (payload.model_rapido or "").strip() or principal
    profundo = (payload.model_profundo or "").strip() or principal
    effort = (payload.reasoning_effort_profundo or "xhigh").strip().lower()
    if effort not in {"minimal", "low", "medium", "high", "xhigh", "max"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El esfuerzo de razonamiento no es válido.",
        )
    if not principal or any(ord(char) < 32 for char in principal + rapido + profundo):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El nombre del modelo no es válido.",
        )

    cfg["model_principal"] = principal
    cfg["model_rapido"] = rapido
    cfg["model_profundo"] = profundo
    cfg["reasoning_effort_profundo"] = effort
    kind = str(cfg.get("kind") or "unknown")
    await vault.put(
        current_user.tenant_id,
        account["id"],
        TokenBundle(access_token=json.dumps(cfg), token_type="config", scopes=[kind]),
    )
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="credentials.llm.models_updated",
        target=f"{kind}:{principal}",
    )


@router.put("/llm", status_code=status.HTTP_204_NO_CONTENT)
async def put_llm_credentials(
    payload: LLMCredentialsIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
    settings: Settings = Depends(get_settings),
) -> None:
    kind = payload.kind.strip().lower()
    if kind not in _LLM_KINDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"kind desconocido: {payload.kind!r}. Debe ser uno de {sorted(_LLM_KINDS)}.",
        )

    if kind in _LOCAL_ONLY_LLM_KINDS and not getattr(settings, "EDECAN_LOCAL_MODE", False):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"'{kind}' requiere la app de escritorio (modo local): un servidor "
                "hospedado no puede usar un binario/puerto de TU máquina. Usa una "
                "API key normal, o instala la app de escritorio de Edecán."
            ),
        )

    api_key = (payload.api_key or "").strip() or None
    base_url = (payload.base_url or "").strip() or None
    model_principal = (payload.model_principal or "").strip() or None
    model_rapido = (payload.model_rapido or "").strip() or None
    model_profundo = (payload.model_profundo or "").strip() or None
    reasoning_effort_profundo = (payload.reasoning_effort_profundo or "xhigh").strip().lower()
    if reasoning_effort_profundo not in {"minimal", "low", "medium", "high", "xhigh", "max"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El esfuerzo de razonamiento no es válido.",
        )
    # `vertex` tiene dos modos (ver `VertexAIProvider`/docs/proveedores-llm.md):
    # "api_key" (default, requiere `api_key`) y "service_account" (avanzado,
    # bring-your-own proyecto GCP — requiere `extra.project_id` +
    # `extra.service_account_json` en su lugar, NUNCA `api_key`).
    vertex_service_account = kind == "vertex" and payload.extra.get("mode") == "service_account"

    if kind in _LLM_KINDS_REQUIEREN_API_KEY and not vertex_service_account and not api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"'{kind}' requiere api_key."
        )
    if vertex_service_account:
        project_id = (payload.extra.get("project_id") or "").strip()
        service_account_json = (payload.extra.get("service_account_json") or "").strip()
        if not project_id or not service_account_json:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "'vertex' en modo 'service_account' requiere extra.project_id y "
                    "extra.service_account_json (el JSON completo de la clave de la "
                    "cuenta de servicio)."
                ),
            )
    if kind == "openai_compat" and not base_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="'openai_compat' requiere base_url."
        )
    if kind == "ollama":
        base_url = base_url or _OLLAMA_DEFAULT_BASE_URL
        if not model_principal:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "'ollama' requiere model_principal: el nombre de un modelo ya "
                    "descargado en tu Ollama local (p. ej. 'llama3.1')."
                ),
            )

    discovered_payload: dict[str, Any] | None = None
    if payload.validate_:
        if kind == "anthropic":
            discovered_payload = await _ping_anthropic(api_key)
        elif vertex_service_account:
            await _ping_vertex_service_account(payload.extra.get("service_account_json") or "")
        elif kind == "vertex":
            discovered_payload = await _ping_vertex_api_key(api_key)
        elif kind == "openai_compat":
            discovered_payload = await _ping_openai_compat(base_url, api_key)
        elif kind == "ollama":
            await _ping_ollama(base_url)
        elif kind in _CLI_BINARIES:
            await _ping_cli_binary(_CLI_BINARIES[kind])

    # Si la persona no eligió IDs técnicos, fijamos una pareja exacta a partir
    # de los modelos que SU credencial puede usar. Esto ocurre solo al conectar:
    # no se introduce una llamada de red, ni un alias mutable, en cada turno.
    if discovered_payload is not None and kind in {"anthropic", "openai_compat", "vertex"}:
        choice = choose_discovered_models(kind, discovered_payload)
        if choice is not None:
            model_principal = model_principal or choice.principal
            model_rapido = model_rapido or choice.rapido

    if kind == "openai_compat" and not model_principal:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "El endpoint no anunció un modelo de conversación utilizable. "
                "Indica el nombre exacto en model_principal (y, opcionalmente, "
                "model_rapido)."
            ),
        )
    model_rapido = model_rapido or model_principal
    model_profundo = model_profundo or model_principal

    config_dict = {
        "kind": kind,
        "api_key": api_key,
        "base_url": base_url,
        "model_principal": model_principal,
        "model_rapido": model_rapido,
        "model_profundo": model_profundo,
        "reasoning_effort_profundo": reasoning_effort_profundo,
        "extra": payload.extra or {},
    }
    account = await _find_or_create_account(
        repo, current_user.tenant_id, LLM_CONNECTOR_KEY, _LLM_DISPLAY_NAME
    )
    await vault.put(
        current_user.tenant_id,
        account["id"],
        TokenBundle(access_token=json.dumps(config_dict), token_type="config", scopes=[kind]),
    )
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="credentials.llm.connected",
        target=kind,
    )


@router.delete("/llm", status_code=status.HTTP_204_NO_CONTENT)
async def delete_llm_credentials(
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> None:
    account = await _find_account(repo, current_user.tenant_id, LLM_CONNECTOR_KEY)
    if account is None:
        return  # idempotente: nada que borrar ya es un estado válido de "desconectado".
    await repo.delete_connector_account(tenant_id=current_user.tenant_id, account_id=account["id"])
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="credentials.llm.disconnected",
        target=LLM_CONNECTOR_KEY,
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
    # que `claude_cli`/`codex_cli`/`ollama` en `PUT /v1/credentials/llm`).
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
