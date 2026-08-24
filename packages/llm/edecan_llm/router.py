"""Provider-neutral LLM and task routing.

All non-IDE inference currently uses Cloudflare Workers AI. Callers only
know logical tasks and the generic ``LLMProvider`` contract, so replacing
Cloudflare with another provider later is isolated to a provider factory.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from inspect import isawaitable
from typing import Any, Literal, Protocol

from .anthropic import AnthropicProvider
from .base import CompletionRequest, CompletionResponse, LLMProvider, Usage
from .claude_cli import ClaudeCLIProvider
from .codex_cli import CodexCLIProvider
from .config import LLMProviderConfig
from .errors import LLMError
from .ollama import OllamaProvider
from .task_router import TaskDecision, TaskRouter, azure_activo, modelo_para_perfil
from .vertex import VertexAIProvider
from .workers_ai import WorkersAIProvider

Alias = Literal["principal", "rapido", "profundo", "ingenieria_software"]
logger = logging.getLogger(__name__)
_LOCAL_ONLY_KINDS = frozenset({"claude_cli", "codex_cli", "ollama"})
_DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"


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
    EDECAN_LOCAL_MODE: bool
    CLAUDE_CLI_PATH: str | None
    CODEX_CLI_PATH: str | None
    OLLAMA_BASE_URL: str | None
    LLM_CLI_TIMEOUT_SECONDS: int


OnUsage = Callable[[str, Usage], Awaitable[None]]
ProviderFactory = Callable[[SettingsLike], LLMProvider]


class _EarlyFailureFallbackProvider:
    """Reintenta una sola vez si el proveedor falla antes de emitir texto."""

    def __init__(self, primary: LLMProvider, fallback_model: str | None) -> None:
        self._primary = primary
        self._fallback_model = fallback_model or None
        self.name = str(getattr(primary, "name", "provider"))
        self.last_fallback_used = False
        self.last_model_used: str | None = None

    def stream(self, request: CompletionRequest) -> AsyncIterator[Any]:
        return self._stream(request)

    async def _stream(self, request: CompletionRequest) -> AsyncIterator[Any]:
        emitted = False
        self.last_fallback_used = False
        self.last_model_used = request.model
        try:
            async for chunk in self._primary.stream(request):
                emitted = True
                yield chunk
        except Exception:
            if emitted or not self._fallback_model or request.model == self._fallback_model:
                raise
            self.last_fallback_used = True
            logger.warning("LLM primary model failed before output; using configured fallback")
            fallback_request = request.model_copy(update={"model": self._fallback_model})
            self.last_model_used = fallback_request.model
            async for chunk in self._primary.stream(fallback_request):
                yield chunk

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.last_fallback_used = False
        self.last_model_used = request.model
        try:
            return await self._primary.complete(request)
        except Exception:
            if not self._fallback_model or request.model == self._fallback_model:
                raise
            self.last_fallback_used = True
            logger.warning("LLM primary completion failed; using configured fallback")
            fallback_request = request.model_copy(update={"model": self._fallback_model})
            self.last_model_used = fallback_request.model
            return await self._primary.complete(fallback_request)

    async def aclose(self) -> None:
        close = getattr(self._primary, "aclose", None)
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
    )


def build_provider_from_settings(settings: SettingsLike) -> LLMProvider:
    """Elige el proveedor según `LLM_PROVIDER` (default `workers_ai`).

    Es el "switch" de proveedor: hoy NO se pisa (Workers AI sigue siendo el
    default). Cambiar `LLM_PROVIDER=azure_openai` en `platform-config.json`
    mueve la inferencia a Azure sin tocar código; volver a `workers_ai` (o
    borrar la clave) restaura Cloudflare."""
    kind = str(getattr(settings, "LLM_PROVIDER", None) or "workers_ai").strip().lower()
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
        provider_config: LLMProviderConfig | None = None,
    ) -> None:
        self._settings = settings
        self._on_usage = on_usage
        self._provider_factory = provider_factory
        self._provider = provider
        self._provider_config = provider_config
        self._fallback_model = (
            None
            if provider_config is not None
            else (str(getattr(settings, "WORKERS_AI_FALLBACK_MODEL", None) or "").strip() or None)
        )
        if provider_config is not None:
            principal, rapido, profundo = self._config_models(provider_config)
            chat_m = rapido or principal
            deep_m = profundo or principal or chat_m
            self._task_router = task_router or TaskRouter(
                chat_model=chat_m,
                principal_model=principal,
                deep_model=deep_m,
                voice_model=rapido or principal,
                engineering_model=principal,
                allow_catalog_selection=provider_config.kind in {"workers_ai", "azure_openai"},
                allow_empty_models=True,
            )
        elif azure_activo():
            # Con Azure, el "modelo" es el nombre del deployment; ignora
            # WORKERS_AI_CHAT_MODEL (que sigue apuntando al catálogo de
            # Cloudflare) y usa el primer deployment ("Sol" por default).
            chat_m = modelo_para_perfil("chat_rapido")
            deep_m = getattr(settings, "WORKERS_AI_MODEL_PROFUNDO", None)
            self._task_router = task_router or TaskRouter(chat_model=chat_m, deep_model=deep_m)
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
        return self._get_provider(), decision.model

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
        return _EarlyFailureFallbackProvider(provider, self._fallback_model)

    def route(
        self,
        request: CompletionRequest,
        *,
        alias: Alias = "rapido",
    ) -> tuple[LLMProvider, TaskDecision]:
        """Classify a concrete request and return its provider decision."""

        decision = self._task_router.decide(request, alias=alias)
        return self._with_fallback(self._get_provider()), decision

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

    def _get_provider(self) -> LLMProvider:
        if self._provider is None:
            try:
                self._provider = (
                    self._build_provider_from_config(self._provider_config)
                    if self._provider_config is not None
                    else self._provider_factory(self._settings)
                )
            except LLMError:
                raise
            except Exception as exc:
                raise LLMError(f"No se pudo inicializar el proveedor de inferencia: {exc}") from exc
        return self._provider

    @staticmethod
    def _config_models(config: LLMProviderConfig) -> tuple[str, str, str]:
        principal = (config.model_principal or "").strip()
        rapido = (config.model_rapido or principal).strip()
        profundo = (config.model_profundo or principal or rapido).strip()
        return principal, rapido, profundo

    def _build_provider_from_config(self, config: LLMProviderConfig) -> LLMProvider:
        if config.kind == "workers_ai":
            account_id = str(config.extra.get("account_id") or "").strip()
            if not account_id or not config.api_key:
                raise LLMError(
                    "Workers AI requiere account_id y API token en Configuración.",
                    provider="workers_ai",
                )
            return WorkersAIProvider(
                account_id=account_id,
                api_token=config.api_key,
                timeout=float(getattr(self._settings, "WORKERS_AI_TIMEOUT_SECONDS", 120.0)),
            )
        if config.kind == "anthropic":
            if not config.api_key:
                raise LLMError("Proveedor 'anthropic' seleccionado sin api_key.")
            return AnthropicProvider(api_key=config.api_key)
        if config.kind == "openai_compat":
            if not config.base_url:
                raise LLMError("Proveedor 'openai_compat' seleccionado sin base_url.")
            from .openai_compat import OpenAICompatProvider

            return OpenAICompatProvider(base_url=config.base_url, api_key=config.api_key or "")
        if config.kind == "vertex":
            return VertexAIProvider(config)
        if config.kind in _LOCAL_ONLY_KINDS and not bool(
            getattr(self._settings, "EDECAN_LOCAL_MODE", False)
        ):
            raise LLMError(
                f"Proveedor {config.kind!r} requiere EDECAN_LOCAL_MODE=True en la app desktop."
            )
        if config.kind == "claude_cli":
            return ClaudeCLIProvider(
                **self._cli_provider_kwargs(config, path_setting="CLAUDE_CLI_PATH")
            )
        if config.kind == "codex_cli":
            kwargs = self._cli_provider_kwargs(config, path_setting="CODEX_CLI_PATH")
            if config.model_profundo and config.reasoning_effort_profundo:
                kwargs["reasoning_effort_by_model"] = {
                    config.model_profundo: config.reasoning_effort_profundo
                }
            return CodexCLIProvider(**kwargs)
        if config.kind == "ollama":
            return OllamaProvider(
                base_url=config.base_url
                or getattr(self._settings, "OLLAMA_BASE_URL", None)
                or _DEFAULT_OLLAMA_BASE_URL,
                model_principal=config.model_principal,
            )
        raise LLMError(f"kind de proveedor LLM desconocido: {config.kind!r}")

    def _cli_provider_kwargs(
        self, config: LLMProviderConfig, *, path_setting: str
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "binary_path": config.extra.get("binary_path")
            or getattr(self._settings, path_setting, None)
        }
        timeout_seconds = config.extra.get("timeout_seconds") or getattr(
            self._settings, "LLM_CLI_TIMEOUT_SECONDS", None
        )
        if timeout_seconds:
            kwargs["timeout_seconds"] = float(timeout_seconds)
        return kwargs

    async def aclose(self) -> None:
        """Release provider-owned network resources, if initialized."""

        provider, self._provider = self._provider, None
        if provider is None:
            return
        close = getattr(provider, "aclose", None)
        if close is None:
            return
        result = close()
        if isawaitable(result):
            await result
