"""Provider-neutral LLM and task routing.

All non-IDE inference currently uses Cloudflare Workers AI. Callers only
know logical tasks and the generic ``LLMProvider`` contract, so replacing
Cloudflare with another provider later is isolated to a provider factory.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from inspect import isawaitable
from typing import Any, Literal, Protocol

from .base import CompletionRequest, CompletionResponse, LLMProvider, Usage
from .errors import LLMError, ProviderDownError, RateLimitedError
from .task_router import (
    TaskDecision,
    TaskRouter,
    _provider_activo,
    azure_activo,
    modelo_para_perfil,
)
from .workers_ai import WorkersAIProvider

Alias = Literal["principal", "rapido", "profundo", "ingenieria_software", "orquestador", "worker", "worker_vision"]
logger = logging.getLogger(__name__)


class SettingsLike(Protocol):
    CLOUDFLARE_ACCOUNT_ID: str | None
    CLOUDFLARE_API_TOKEN: str | None
    WORKERS_AI_CHAT_MODEL: str | None
    WORKERS_AI_MODEL_PROFUNDO: str | None
    WORKERS_AI_TIMEOUT_SECONDS: float
    LLM_PROVIDER: str | None
    AZURE_AI_FOUNDRY_ENDPOINT: str | None
    AZURE_AI_FOUNDRY_API_KEY: str | None
    OPENAI_COMPAT_BASE_URL: str | None
    OPENAI_COMPAT_API_KEY: str | None


OnUsage = Callable[[str, Usage], Awaitable[None]]
ProviderFactory = Callable[[SettingsLike], LLMProvider]


def _es_fallo_transitorio(exc: BaseException) -> bool:
    """True SOLO para transport/5xx/429/timeout: reintentable y sin side effects.

    `RateLimitedError` (429 y 5xx agotados) y `ProviderDownError` (timeout y
    conexión caída) son los únicos fallos que el fallback puede arreglar. Los
    errores 4xx/auth — `PeticionInvalidaError`, `CredencialInvalidaError` y el
    `LLMError` genérico que `openai_compat` levanta para `status_code >= 400` —
    son permanentes: reintentar un 400/401 no lo convierte en 200 y antes
    gastaba un presupuesto nuevo en cada intento (C9b).
    """
    return isinstance(exc, (RateLimitedError, ProviderDownError))


def _deadline_s_del_request(request: CompletionRequest, default: float = 300.0) -> float:
    """Lee `metadata["deadline_s"]` (el mismo contrato que `WorkersAIProvider`),
    con default compartido — un valor ausente/corrupto nunca revienta el
    enrutado."""
    try:
        valor = request.metadata.get("deadline_s")
        return max(0.0, float(valor)) if valor is not None else default
    except (TypeError, ValueError):
        return default


class _EarlyFailureFallbackProvider:
    """Reintenta una sola vez si el proveedor falla antes de emitir texto.

    `fallback_provider` es el proveedor del MODELO de fallback: si el modelo
    es `@cf/` (Workers AI) y el primario es Azure, el fallback DEBE ir al
    proveedor workers — antes se reusaba el primario y el @cf/ moría 400.

    Dos reglas de seguridad de reintento (C9b):
    - Solo reintenta fallos transitorios (`_es_fallo_transitorio`); un 4xx/auth
      sube directo.
    - Primario y fallback comparten UN deadline único: el reintento recibe solo
      el tiempo restante, no un presupuesto nuevo completo.
    """

    def __init__(
        self,
        primary: LLMProvider,
        fallback_model: str | None,
        fallback_provider: LLMProvider | None = None,
    ) -> None:
        self._primary = primary
        self._fallback_model = fallback_model or None
        self._fallback_provider = fallback_provider
        self.name = str(getattr(primary, "name", "provider"))
        self.last_fallback_used = False
        self.last_model_used: str | None = None

    def _objetivo_del_fallback(self) -> LLMProvider:
        return self._fallback_provider or self._primary

    def _fallback_request(self, request: CompletionRequest, deadline: float) -> CompletionRequest:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LLMError(
                "El presupuesto compartido de la llamada se agotó antes de poder "
                "reintentar con el modelo de fallback.",
                provider=self.name,
            )
        return request.model_copy(
            update={
                "model": self._fallback_model,
                # metadata nuevo (no se muta el del request original) con el
                # tiempo restante para que Workers AI comparta el deadline.
                "metadata": {**request.metadata, "deadline_s": remaining},
            }
        )

    def stream(self, request: CompletionRequest) -> AsyncIterator[Any]:
        return self._stream(request)

    async def _stream(self, request: CompletionRequest) -> AsyncIterator[Any]:
        emitted = False
        deadline = time.monotonic() + _deadline_s_del_request(request)
        self.last_fallback_used = False
        self.last_model_used = request.model
        try:
            async for chunk in self._primary.stream(request):
                emitted = True
                yield chunk
        except Exception as exc:
            if not _es_fallo_transitorio(exc):
                raise
            if emitted or not self._fallback_model or request.model == self._fallback_model:
                raise
            self.last_fallback_used = True
            logger.warning("LLM primary model failed before output; using configured fallback")
            fallback_request = self._fallback_request(request, deadline)
            self.last_model_used = fallback_request.model
            async for chunk in self._objetivo_del_fallback().stream(fallback_request):
                yield chunk

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        deadline = time.monotonic() + _deadline_s_del_request(request)
        self.last_fallback_used = False
        self.last_model_used = request.model
        try:
            return await self._primary.complete(request)
        except Exception as exc:
            if not _es_fallo_transitorio(exc):
                raise
            if not self._fallback_model or request.model == self._fallback_model:
                raise
            self.last_fallback_used = True
            logger.warning("LLM primary completion failed; using configured fallback")
            fallback_request = self._fallback_request(request, deadline)
            self.last_model_used = fallback_request.model
            return await self._objetivo_del_fallback().complete(fallback_request)

    async def aclose(self) -> None:
        cerrados: set[int] = set()
        for objetivo in (self._primary, self._fallback_provider):
            if objetivo is None or id(objetivo) in cerrados:
                continue
            cerrados.add(id(objetivo))
            close = getattr(objetivo, "aclose", None)
            if close is not None:
                result = close()
                if isawaitable(result):
                    await result


def build_workers_ai_provider(settings: SettingsLike) -> LLMProvider:
    """Default provider factory; the only Cloudflare-specific composition."""

    return WorkersAIProvider(
        account_id=getattr(settings, "CLOUDFLARE_ACCOUNT_ID", None) or "",
        api_token=getattr(settings, "CLOUDFLARE_API_TOKEN", None) or "",
        timeout=float(getattr(settings, "WORKERS_AI_TIMEOUT_SECONDS", 120.0)),
    )


def build_azure_openai_provider(settings: SettingsLike) -> LLMProvider:
    """Proveedor Azure AI Foundry (protocolo `openai-v1`, auth `api-key`).

    Lee del entorno (via `platform-config.json`, ver `apps/local/edecan_local/
    runtime.py::_PLATFORM_CONFIG_KEYS`):
    - `AZURE_AI_FOUNDRY_ENDPOINT`: base, p. ej.
      `https://<recurso>.services.ai.azure.com/openai/v1` (o
      `https://<recurso>.openai.azure.com/openai/v1`).
    - `AZURE_AI_FOUNDRY_API_KEY`: la clave (header `api-key`).
    - `AZURE_AI_FOUNDRY_TEXT_DEPLOYMENT`: el nombre del deployment ("modelo").

    No es el formato legacy `/openai/deployments/<d>/chat/completions?api-version`
    — ese es otro protocolo. Aquí se usa el `openai-v1` (mismo que usa FyDesign
    2.0 con `AZURE_AI_FOUNDRY_USE_LEGACY=false`)."""

    from .openai_compat import OpenAICompatProvider

    endpoint = str(getattr(settings, "AZURE_AI_FOUNDRY_ENDPOINT", None) or "").strip()
    api_key = str(getattr(settings, "AZURE_AI_FOUNDRY_API_KEY", None) or "").strip()
    if not endpoint or not api_key:
        raise LLMError(
            "Azure AI Foundry requiere AZURE_AI_FOUNDRY_ENDPOINT y "
            "AZURE_AI_FOUNDRY_API_KEY en platform-config.json.",
            provider="azure_openai",
        )
    # Asegura el sufijo `/openai/v1` (mismo criterio que FyDesign
    # `ensureGenericV1`): el path de chat del adaptador es `/chat/completions`.
    base = endpoint.rstrip("/")
    if not (base.endswith("/openai/v1") or base.endswith("/v1")):
        base = f"{base}/openai/v1"
    return OpenAICompatProvider(
        base_url=base,
        api_key=api_key,
        key_auth_mode="api-key",
        use_max_completion_tokens=True,
    )


def build_provider_from_settings(settings: SettingsLike) -> LLMProvider:
    """Elige el proveedor según `LLM_PROVIDER` (default `workers_ai`).

    Es el "switch" de proveedor: hoy NO se pisa (Workers AI sigue siendo el
    default). Cambiar `LLM_PROVIDER=azure_openai` en `platform-config.json`
    mueve la inferencia a Azure sin tocar código; volver a `workers_ai` (o
    borrar la clave) restaura Cloudflare."""
    kind = _provider_activo(settings) or "workers_ai"
    if kind == "azure_openai":
        return build_azure_openai_provider(settings)
    if kind == "openai_compat":
        from .openai_compat import OpenAICompatProvider

        base_url = str(getattr(settings, "OPENAI_COMPAT_BASE_URL", None) or "").strip()
        api_key = str(getattr(settings, "OPENAI_COMPAT_API_KEY", None) or "").strip()
        if not base_url or not api_key:
            raise LLMError(
                "openai_compat requiere OPENAI_COMPAT_BASE_URL y OPENAI_COMPAT_API_KEY.",
                provider="openai_compat",
            )
        return OpenAICompatProvider(base_url=base_url, api_key=api_key)
    return build_workers_ai_provider(settings)


class LLMRouter:
    """Routes Edecán tasks without exposing provider or model selection.

    ``provider_factory`` is an architectural seam, not a user setting. A
    future OpenAI, Anthropic, Google, or self-hosted adapter can be swapped in
    at composition time without changing agents, calls, workers, or chat.
    """

    def __init__(
        self,
        settings: SettingsLike,
        on_usage: OnUsage | None = None,
        *,
        provider_factory: ProviderFactory = build_provider_from_settings,
        provider: LLMProvider | None = None,
        task_router: TaskRouter | None = None,
        workers_provider_factory: ProviderFactory = build_workers_ai_provider,
    ) -> None:
        self._settings = settings
        self._on_usage = on_usage
        self._provider_factory = provider_factory
        self._provider = provider
        self._workers_provider_factory = workers_provider_factory
        # Workers AI como SEGUNDO proveedor (los trabajadores de los bots):
        # los modelos `@cf/...` se rutean acá aunque el principal sea Azure.
        # GPT (Azure) queda para el chat y Astra (el jefe).
        self._workers_provider: LLMProvider | None = None
        self._fallback_model = (
            str(getattr(settings, "WORKERS_AI_FALLBACK_MODEL", None) or "").strip() or None
        )
        if azure_activo(self._settings):
            # Con Azure, el "modelo" es el nombre del deployment; ignora
            # WORKERS_AI_CHAT_MODEL (que sigue apuntando al catálogo de
            # Cloudflare) y usa el primer deployment ("Sol" por default). El
            # ESCRITOR de posts ("profundo") también: mandar el id de GLM del
            # catálogo de Workers AI sobre el proveedor Azure fallaría.
            # `azure_activo(self._settings)` lee la MISMA fuente que
            # `build_provider_from_settings` (C9b: una sola fuente de verdad).
            chat_m = modelo_para_perfil("chat_rapido")
            deep_m = modelo_para_perfil("profundo")
        else:
            chat_m = getattr(settings, "WORKERS_AI_CHAT_MODEL", None) or modelo_para_perfil(
                "chat_rapido"
            )
            deep_m = getattr(settings, "WORKERS_AI_MODEL_PROFUNDO", None)
        self._task_router = task_router or TaskRouter(chat_model=chat_m, deep_model=deep_m)

    def resolve(
        self,
        alias: Alias,
        tenant_flags: dict[str, Any],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[LLMProvider, str]:
        """Resuelve proveedor + modelo para un turno.

        `metadata` es el canal por el que el selector del chat hace llegar su
        elección (`modelo_elegido`, ver `task_router.METADATA_MODELO_ELEGIDO`).
        Es kwarg con default a propósito: los llamadores que no eligen nada
        —la mayoría— siguen resolviendo exactamente igual que antes, y la
        decisión final la sigue tomando `TaskRouter`, que ignora un id fuera
        de catálogo.
        """

        del tenant_flags
        decision = self._task_router.decide(alias=alias, metadata=metadata)
        model = decision.model
        if str(model or "").startswith("@cf/"):
            # Los trabajadores de los bots corren en Workers AI (gratis/barato)
            # aunque el proveedor principal sea Azure. El chat y Astra (jefes)
            # siguen con el proveedor principal.
            return self._get_workers_provider(), model
        return self._get_provider(), model

    def resolve_with_attribution(
        self,
        alias: Alias,
        tenant_flags: dict[str, Any],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[LLMProvider, str, dict[str, str]]:
        """Resuelve y conserva la decisión auditable del `TaskRouter`."""
        del tenant_flags
        decision = self._task_router.decide(alias=alias, metadata=metadata)
        model = decision.model
        if str(model or "").startswith("@cf/"):
            # Misma regla que `resolve()`: los trabajadores de los bots corren
            # en Workers AI aunque el principal sea Azure. Antes esta rama NO
            # existía aquí y cada paso de misión mandaba un modelo `@cf/` al
            # endpoint de Azure → 400 unknown model (bug E-LLM-1). El wrapper
            # de fallback SÍ aplica: si Workers AI falla antes del primer
            # chunk y hay WORKERS_AI_FALLBACK_MODEL, reintenta con él (el
            # wrapper enruta un fallback @cf/ al provider workers).
            provider = self._with_fallback(self._get_workers_provider())
        else:
            provider = self._with_fallback(self._get_provider())
        attribution = {
            "router": "task_router",
            "router_alias": str(alias),
            "task_kind": decision.kind.value,
            "routing_reason": decision.reason[:200],
        }
        if self._fallback_model and self._fallback_model != decision.model:
            attribution["fallback_model"] = self._fallback_model
        return provider, decision.model, attribution

    def _with_fallback(self, provider: LLMProvider) -> LLMProvider:
        """Añade fallback solo cuando está configurado, sin cambiar defaults."""
        if not self._fallback_model:
            return provider
        fallback_provider: LLMProvider | None = None
        if str(self._fallback_model or "").startswith("@cf/"):
            # El modelo de fallback es de Workers AI: el reintento va al
            # proveedor workers, no al primario (Azure rechaza ids @cf/).
            fallback_provider = self._get_workers_provider()
        return _EarlyFailureFallbackProvider(provider, self._fallback_model, fallback_provider)

    def route(
        self,
        request: CompletionRequest,
        *,
        alias: Alias = "rapido",
    ) -> tuple[LLMProvider, TaskDecision]:
        """Classify a concrete request and return its provider decision.

        Igual que `resolve()`/`resolve_with_attribution()`: un modelo `@cf/`
        (trabajadores de los bots) va SIEMPRE al proveedor de Workers AI
        aunque el primario sea Azure (C9b — antes `route()`/`complete()`
        mandaban `@cf/` al primario y moría 400 en Azure).
        """

        decision = self._task_router.decide(request, alias=alias)
        base = (
            self._get_workers_provider()
            if str(decision.model or "").startswith("@cf/")
            else self._get_provider()
        )
        return self._with_fallback(base), decision

    async def complete(
        self,
        alias: Alias,
        tenant_flags: dict[str, Any],
        req: CompletionRequest,
    ) -> CompletionResponse:
        del tenant_flags
        provider, decision = self.route(req, alias=alias)
        resolved_req = (
            req if req.model == decision.model else req.model_copy(update={"model": decision.model})
        )
        response = await provider.complete(resolved_req)
        if self._on_usage is not None:
            await self._on_usage(
                str(getattr(provider, "last_model_used", None) or decision.model), response.usage
            )
        return response

    def _get_workers_provider(self) -> LLMProvider:
        if self._workers_provider is None:
            self._workers_provider = self._workers_provider_factory(self._settings)
        return self._workers_provider

    def _get_provider(self) -> LLMProvider:
        if self._provider is None:
            try:
                self._provider = self._provider_factory(self._settings)
            except LLMError:
                raise
            except Exception as exc:
                raise LLMError(f"No se pudo inicializar el proveedor de inferencia: {exc}") from exc
        return self._provider

    async def aclose(self) -> None:
        """Release provider-owned network resources, if initialized."""

        for current in (
            ("primario", self._provider),
            ("workers", self._workers_provider),
        ):
            _, provider = current
            if provider is None:
                continue
            close = getattr(provider, "aclose", None)
            if close is None:
                continue
            result = close()
            if isawaitable(result):
                await result
        self._provider = None
        self._workers_provider = None
