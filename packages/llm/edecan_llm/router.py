"""Router de proveedor/modelo LLM (`ARCHITECTURE.md` §3, §10.6, §12)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Literal, Protocol

from .anthropic import AnthropicProvider
from .base import CompletionRequest, CompletionResponse, LLMProvider, Usage
from .claude_cli import ClaudeCLIProvider
from .codex_cli import CodexCLIProvider
from .config import LLMProviderConfig
from .errors import LLMError
from .ollama import OllamaProvider
from .openai_compat import OpenAICompatProvider
from .vertex import VertexAIProvider

logger = logging.getLogger(__name__)

Alias = Literal["principal", "rapido"]

_DEFAULT_MODEL_PRINCIPAL = "claude-sonnet-4-5"
_DEFAULT_MODEL_RAPIDO = "claude-haiku-4-5"
# Fallbacks estables para service accounts sin acceso al endpoint de catálogo.
# Las conexiones por API key guardan IDs descubiertos exactos al conectarse;
# estos valores solo cubren el camino offline/legacy.
_DEFAULT_VERTEX_MODEL_PRINCIPAL = "gemini-3.5-flash"
_DEFAULT_VERTEX_MODEL_RAPIDO = "gemini-3.1-flash-lite"
_DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"

# `kind`s que solo tienen sentido con el backend corriendo LOCAL en la
# máquina del propio cliente (app de escritorio `apps/local`, ver
# `ARCHITECTURE.md` §12.c) — MISMO conjunto que
# `edecan_api.routers.credentials._LOCAL_ONLY_LLM_KINDS` (duplicado a
# propósito: este paquete no puede importar `apps/api`, ARCHITECTURE.md
# §10.1). Ese endpoint es la única puerta de ESCRITURA para uno de estos
# `kind` (rechaza guardarlo si `EDECAN_LOCAL_MODE` no está activo), pero una
# fila ya guardada puede sobrevivir a un cambio de configuración (`EDECAN_
# LOCAL_MODE` apagado después) o llegar por otra vía (p. ej. la base de
# datos de una instalación local copiada a un servidor hospedado
# compartido) — `_build_provider_from_config` vuelve a comprobarlo aquí,
# en el único punto donde CUALQUIER caller (`apps/api`, `apps/worker`)
# convierte esta config en un proceso/socket real, como segunda capa de
# defensa (mismo criterio de redundancia que la verificación de número
# Twilio, ARCHITECTURE.md §10.10).
_LOCAL_ONLY_KINDS = frozenset({"claude_cli", "codex_cli", "ollama"})


class SettingsLike(Protocol):
    """Atributos que `LLMRouter` necesita de un objeto de configuración.

    Cualquier `pydantic-settings.BaseSettings` con estos campos (ver
    `ARCHITECTURE.md` §10.2) sirve — no se importa una clase `Settings`
    concreta para no acoplar `edecan_llm` a `apps/*`. Se leen con `getattr`
    (con default) así que un doble de prueba puede omitir campos que no use.
    Con `provider_config` (§12, WP-V3-03) también se leen, siempre con
    `getattr`, `VERTEX_MODEL_PRINCIPAL`/`VERTEX_MODEL_RAPIDO` — no están
    declarados acá porque son opcionales incluso para quien SÍ implementa
    este protocolo (ver `_config_models`).
    """

    ANTHROPIC_API_KEY: str | None
    ANTHROPIC_MODEL_PRINCIPAL: str
    ANTHROPIC_MODEL_RAPIDO: str
    OPENAI_COMPAT_BASE_URL: str | None
    OPENAI_COMPAT_API_KEY: str | None


OnUsage = Callable[[str, Usage], Awaitable[None]]


class LLMRouter:
    """Resuelve alias lógicos (`"principal"`, `"rapido"`) a `(LLMProvider, modelo)`.

    Dos modos, según se pase o no `provider_config` (§12, WP-V3-03):

    - **Sin `provider_config`** (default, `None`): comportamiento legacy
      intacto — resuelve desde variables de entorno de plataforma
      (`settings.ANTHROPIC_*`/`OPENAI_COMPAT_*`), documentado abajo.
    - **Con `provider_config`**: el tenant eligió explícitamente un
      proveedor (Anthropic, OpenAI-compat, Vertex AI, Claude CLI, Codex CLI
      u Ollama) desde la pantalla de Configuración — `_build_provider()`
      construye ESE proveedor y `_resolve_model()` prefiere
      `provider_config.model_principal`/`model_rapido` sobre los de
      `settings`, con un default sano por proveedor si faltan (ver
      `_config_models`). `kind` ∈ `_LOCAL_ONLY_KINDS` (`claude_cli`/
      `codex_cli`/`ollama`) además exige `getattr(settings,
      "EDECAN_LOCAL_MODE", False)` — si no, `_build_provider_from_config`
      lanza `LLMError` en vez de construir el proveedor (segunda capa de
      aislamiento multi-tenant, ver el comentario de `_LOCAL_ONLY_KINDS`).

    Comportamiento legacy (sin `provider_config`):
    - `"principal"` → `settings.ANTHROPIC_MODEL_PRINCIPAL`, salvo que el plan
      del tenant no tenga el flag `models.premium` (entonces degrada al
      modelo `"rapido"`).
    - `"rapido"` → `settings.ANTHROPIC_MODEL_RAPIDO` siempre.
    - Proveedor: `AnthropicProvider` si hay `ANTHROPIC_API_KEY`; si no, y hay
      `OPENAI_COMPAT_BASE_URL`, usa `OpenAICompatProvider`.

    En ambos modos, el proveedor se construye una sola vez (perezoso) y se
    reutiliza entre llamadas para no abrir conexiones nuevas en cada turno.
    """

    def __init__(
        self,
        settings: SettingsLike,
        on_usage: OnUsage | None = None,
        *,
        provider_config: LLMProviderConfig | None = None,
    ) -> None:
        # `on_usage` es un hook OPCIONAL: por defecto es `None` y en ese caso
        # `complete()` no registra `usage_events` ni estima costo por sí solo
        # (ver docstring de `complete()`). Ningún call site de primera parte
        # pasa hoy un callback real — `edecan_api.deps.get_llm_router` pasa
        # `on_usage=None` explícitamente y `edecan_worker.deps._build_llm_router`
        # ni siquiera lo pasa — así que no asumas que este mecanismo ya está
        # conectado a `usage_events`/costo solo porque el parámetro existe.
        self._settings = settings
        self._on_usage = on_usage
        self._provider_config = provider_config
        self._provider: LLMProvider | None = None

    def resolve(self, alias: Alias, tenant_flags: dict[str, Any]) -> tuple[LLMProvider, str]:
        """Devuelve `(proveedor, modelo)` para el alias lógico dado."""
        model = self._resolve_model(alias, tenant_flags)
        return self._get_provider(), model

    async def complete(
        self, alias: Alias, tenant_flags: dict[str, Any], req: CompletionRequest
    ) -> CompletionResponse:
        """Resuelve `alias`, ejecuta la completion y reporta uso vía `on_usage`
        (si se configuró uno — ver más abajo).

        `on_usage(model, usage)` se llama con el **modelo ya resuelto** (no el
        alias), para que el callback pueda registrar `usage_events`/estimar
        costo con `edecan_llm.costs.estimate(model, usage)` directamente.

        Nota: si no se pasó `on_usage` al construir el router (su default es
        `None`), este método NO registra `usage_events` ni calcula costo por
        su cuenta — simplemente no llama a nada. Quien necesite ese registro
        debe construir su propio callback (que sí invoque
        `edecan_llm.costs.estimate`) y pasarlo en `LLMRouter(..., on_usage=...)`.
        """
        provider, model = self.resolve(alias, tenant_flags)
        resolved_req = req if req.model == model else req.model_copy(update={"model": model})
        response = await provider.complete(resolved_req)
        if self._on_usage is not None:
            await self._on_usage(model, response.usage)
        return response

    def _resolve_model(self, alias: Alias, tenant_flags: dict[str, Any]) -> str:
        if self._provider_config is not None:
            principal, rapido = self._config_models(self._provider_config)
        else:
            principal = (
                getattr(self._settings, "ANTHROPIC_MODEL_PRINCIPAL", None)
                or _DEFAULT_MODEL_PRINCIPAL
            )
            rapido = (
                getattr(self._settings, "ANTHROPIC_MODEL_RAPIDO", None) or _DEFAULT_MODEL_RAPIDO
            )

        if alias == "rapido":
            return rapido
        if alias == "principal":
            if tenant_flags.get("models.premium") is False:
                logger.info(
                    "Degradando alias 'principal' a modelo rápido: plan sin flag models.premium"
                )
                return rapido
            return principal
        raise ValueError(f"alias LLM desconocido: {alias!r}")

    def _config_models(self, config: LLMProviderConfig) -> tuple[str, str]:
        """Modelos `(principal, rápido)` para un `provider_config` explícito:
        prefiere lo que trae la config del tenant y cae a un default sano por
        proveedor si falta.

        - `anthropic`/`vertex`: mismo patrón que el modo legacy — variable de
          entorno de plataforma (`ANTHROPIC_MODEL_*`/`VERTEX_MODEL_*`) y, si
          tampoco está, una constante hardcodeada (`_DEFAULT_*`).
        - `openai_compat`/`claude_cli`/`codex_cli`/`ollama`: no hay variable
          de plataforma pinned para "el modelo" de un endpoint arbitrario o
          un binario local — el tenant lo trae en su config. Si falta
          `model_rapido`, usa `model_principal` (que a su vez puede quedar
          en `""`: los proveedores CLI interpretan un modelo vacío como "usa
          el default configurado del propio CLI", ver `claude_cli.py`).
        """
        if config.kind == "anthropic":
            principal = config.model_principal or (
                getattr(self._settings, "ANTHROPIC_MODEL_PRINCIPAL", None)
                or _DEFAULT_MODEL_PRINCIPAL
            )
            rapido = config.model_rapido or (
                getattr(self._settings, "ANTHROPIC_MODEL_RAPIDO", None) or _DEFAULT_MODEL_RAPIDO
            )
            return principal, rapido

        if config.kind == "vertex":
            principal = config.model_principal or (
                getattr(self._settings, "VERTEX_MODEL_PRINCIPAL", None)
                or _DEFAULT_VERTEX_MODEL_PRINCIPAL
            )
            rapido = config.model_rapido or (
                getattr(self._settings, "VERTEX_MODEL_RAPIDO", None)
                or _DEFAULT_VERTEX_MODEL_RAPIDO
            )
            return principal, rapido

        principal = config.model_principal or ""
        rapido = config.model_rapido or principal
        return principal, rapido

    def _get_provider(self) -> LLMProvider:
        if self._provider is None:
            self._provider = self._build_provider()
        return self._provider

    def _build_provider(self) -> LLMProvider:
        if self._provider_config is not None:
            return self._build_provider_from_config(self._provider_config)
        return self._build_provider_legacy()

    def _build_provider_legacy(self) -> LLMProvider:
        api_key = getattr(self._settings, "ANTHROPIC_API_KEY", None)
        if api_key:
            return AnthropicProvider(api_key=api_key)

        openai_base_url = getattr(self._settings, "OPENAI_COMPAT_BASE_URL", None)
        if openai_base_url:
            openai_api_key = getattr(self._settings, "OPENAI_COMPAT_API_KEY", None) or ""
            return OpenAICompatProvider(base_url=openai_base_url, api_key=openai_api_key)

        raise LLMError(
            "No hay proveedor LLM configurado: define ANTHROPIC_API_KEY "
            "u OPENAI_COMPAT_BASE_URL (ver ARCHITECTURE.md §10.2)."
        )

    def _build_provider_from_config(self, config: LLMProviderConfig) -> LLMProvider:
        # `api_key`/`base_url` de un `provider_config` de TENANT jamás caen a
        # `self._settings` (a diferencia de `_build_provider_legacy`, donde
        # `self._settings` SÍ es la única fuente porque no hay tenant de por
        # medio): `self._settings` es el mismo objeto `Settings` de
        # PLATAFORMA compartido entre tenants (`edecan_api.deps.get_llm_router`/
        # `edecan_worker.deps.Deps._resolve_tenant_llm_router` lo pasan tal
        # cual junto con `provider_config`), y `ANTHROPIC_API_KEY`/
        # `OPENAI_COMPAT_API_KEY` de plataforma existen para el modo legacy y
        # para los jobs de sistema sin tenant (`docs/proveedores-llm.md`), NO
        # como red de seguridad para un tenant que dejó un campo vacío. Con
        # `kind="openai_compat"` en particular, `base_url` lo elige el propio
        # tenant (`api_key` es opcional en `PUT /v1/credentials/llm` para ese
        # `kind`, ver `credentials.py::_LLM_KINDS_REQUIEREN_API_KEY`) — si
        # `api_key` cayera a `OPENAI_COMPAT_API_KEY` de plataforma, un tenant
        # podría leer el `Authorization: Bearer …` real del operador en SU
        # PROPIO servidor con solo dejar `api_key` vacío (fuga de credencial +
        # viola "Edecán nunca usa una credencial de LLM compartida de la
        # plataforma", `deps.py::get_llm_router`). Sin `api_key` propia, el
        # tenant simplemente pega sin `Authorization` (`api_key = ""`, igual
        # que un endpoint OpenAI-compatible sin auth) en vez de heredar en
        # silencio la de alguien más — mismo criterio fail-closed que
        # `_LOCAL_ONLY_KINDS` unas líneas más abajo.
        if config.kind == "anthropic":
            api_key = config.api_key
            if not api_key:
                raise LLMError(
                    "Proveedor 'anthropic' seleccionado sin api_key (ver Configuración)."
                )
            return AnthropicProvider(api_key=api_key)

        if config.kind == "openai_compat":
            base_url = config.base_url
            if not base_url:
                raise LLMError("Proveedor 'openai_compat' seleccionado sin base_url.")
            api_key = config.api_key or ""
            return OpenAICompatProvider(base_url=base_url, api_key=api_key)

        if config.kind == "vertex":
            return VertexAIProvider(config)

        if config.kind in _LOCAL_ONLY_KINDS and not getattr(
            self._settings, "EDECAN_LOCAL_MODE", False
        ):
            raise LLMError(
                f"Proveedor {config.kind!r} requiere EDECAN_LOCAL_MODE=True: "
                "un CLI local/Ollama solo puede ejecutarse desde la app de "
                "escritorio (apps/local), nunca desde un despliegue hospedado "
                "compartido (ver ARCHITECTURE.md §12.c)."
            )

        if config.kind == "claude_cli":
            kwargs = self._cli_provider_kwargs(config, path_setting="CLAUDE_CLI_PATH")
            return ClaudeCLIProvider(**kwargs)

        if config.kind == "codex_cli":
            kwargs = self._cli_provider_kwargs(config, path_setting="CODEX_CLI_PATH")
            return CodexCLIProvider(**kwargs)

        if config.kind == "ollama":
            base_url = config.base_url or _DEFAULT_OLLAMA_BASE_URL
            return OllamaProvider(base_url=base_url, model_principal=config.model_principal)

        raise LLMError(f"kind de proveedor LLM desconocido: {config.kind!r}")

    def _cli_provider_kwargs(
        self, config: LLMProviderConfig, *, path_setting: str
    ) -> dict[str, Any]:
        """kwargs comunes a `ClaudeCLIProvider`/`CodexCLIProvider` (§12.c):
        `binary_path` cae a `settings.CLAUDE_CLI_PATH`/`CODEX_CLI_PATH` y
        `timeout_seconds` a `settings.LLM_CLI_TIMEOUT_SECONDS` (§12.g) cuando
        `extra` no los trae (`path_setting` indica cuál de los dos settings de
        path corresponde a este `kind`).

        `timeout_seconds` se OMITE (nunca se pasa `None` explícito) si ni
        `extra` ni `settings` lo fijan, para que el provider caiga en su
        propio `DEFAULT_TIMEOUT_SECONDS` hardcodeado — pasar `None` desactivaría
        el timeout del subproceso (`asyncio.wait_for(..., timeout=None)` espera
        para siempre).
        """
        kwargs: dict[str, Any] = {
            "binary_path": config.extra.get("binary_path")
            or getattr(self._settings, path_setting, None)
        }
        timeout_seconds = config.extra.get("timeout_seconds") or getattr(
            self._settings, "LLM_CLI_TIMEOUT_SECONDS", None
        )
        if timeout_seconds:
            kwargs["timeout_seconds"] = timeout_seconds
        return kwargs
