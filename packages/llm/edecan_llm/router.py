"""Provider-neutral LLM and task routing.

All non-IDE inference currently uses Cloudflare Workers AI. Callers only
know logical tasks and the generic ``LLMProvider`` contract, so replacing
Cloudflare with another provider later is isolated to a provider factory.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from inspect import isawaitable
from typing import Any, Literal, Protocol

from .base import CompletionRequest, CompletionResponse, LLMProvider, Usage
from .errors import LLMError
from .task_router import TaskDecision, TaskRouter, modelo_para_perfil
from .workers_ai import WorkersAIProvider

Alias = Literal["principal", "rapido", "profundo", "ingenieria_software"]


class SettingsLike(Protocol):
    CLOUDFLARE_ACCOUNT_ID: str | None
    CLOUDFLARE_API_TOKEN: str | None
    WORKERS_AI_CHAT_MODEL: str | None
    WORKERS_AI_TIMEOUT_SECONDS: float


OnUsage = Callable[[str, Usage], Awaitable[None]]
ProviderFactory = Callable[[SettingsLike], LLMProvider]


def build_workers_ai_provider(settings: SettingsLike) -> LLMProvider:
    """Default provider factory; the only Cloudflare-specific composition."""

    return WorkersAIProvider(
        account_id=getattr(settings, "CLOUDFLARE_ACCOUNT_ID", None) or "",
        api_token=getattr(settings, "CLOUDFLARE_API_TOKEN", None) or "",
        timeout=float(getattr(settings, "WORKERS_AI_TIMEOUT_SECONDS", 120.0)),
    )


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
        provider_factory: ProviderFactory = build_workers_ai_provider,
        provider: LLMProvider | None = None,
        task_router: TaskRouter | None = None,
    ) -> None:
        self._settings = settings
        self._on_usage = on_usage
        self._provider_factory = provider_factory
        self._provider = provider
        chat_m = getattr(settings, "WORKERS_AI_CHAT_MODEL", None) or modelo_para_perfil(
            "chat_rapido"
        )
        self._task_router = task_router or TaskRouter(chat_model=chat_m)

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

    def route(
        self,
        request: CompletionRequest,
        *,
        alias: Alias = "rapido",
    ) -> tuple[LLMProvider, TaskDecision]:
        """Classify a concrete request and return its provider decision."""

        decision = self._task_router.decide(request, alias=alias)
        return self._get_provider(), decision

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
            await self._on_usage(decision.model, response.usage)
        return response

    def _get_provider(self) -> LLMProvider:
        if self._provider is None:
            try:
                self._provider = self._provider_factory(self._settings)
            except LLMError:
                raise
            except Exception as exc:
                raise LLMError(
                    f"No se pudo inicializar el proveedor de inferencia: {exc}"
                ) from exc
        return self._provider

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
