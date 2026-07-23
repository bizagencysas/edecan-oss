"""`edecan_llm` — abstracción de proveedor LLM (`ARCHITECTURE.md` §3, §10.6, §12).

Interfaz única `LLMProvider` (`complete`/`stream`) implementada por
`AnthropicProvider` (primaria), `OpenAICompatProvider`, `VertexAIProvider`
(Gemini API o Vertex AI real), `ClaudeCLIProvider`/`CodexCLIProvider`
(binarios locales ya autenticados), `OllamaProvider` (modelos locales) y
`BedrockProvider` (stub). `LLMRouter` resuelve alias lógicos (`"principal"`,
`"rapido"`) a `(proveedor, modelo)` según variables de entorno y los flags de
plan del tenant — o, si se le pasa un `LLMProviderConfig` (WP-V3-03), según
la selección explícita del tenant en la pantalla de Configuración.
`detect_local_providers()` ayuda a esa pantalla a ofrecer "usar mi Claude
CLI/Ollama ya instalado" con un clic (`DIRECCION_ACTUAL.md`, "configuración
de pocos clicks").
"""

from __future__ import annotations

from .anthropic import AnthropicProvider
from .base import (
    ChatMessage,
    CompletionRequest,
    CompletionResponse,
    LLMProvider,
    StreamChunk,
    ToolCall,
    ToolSpec,
    Usage,
)
from .bedrock import BedrockProvider
from .claude_cli import ClaudeCLIProvider
from .codex_cli import CodexCLIProvider
from .config import LLMProviderConfig
from .detect import detect_local_providers
from .errors import (
    CLINotAuthenticatedError,
    CLINotInstalledError,
    LLMError,
    ProviderDownError,
    RateLimitedError,
)
from .model_selection import ModelChoice, choose_discovered_models, discovered_model_ids
from .ollama import OllamaProvider
from .openai_compat import OpenAICompatProvider
from .prompted_tools import parse_tool_call, render_prompt, render_tools_block
from .router import LLMRouter
from .vertex import VertexAIProvider

__all__ = [
    "AnthropicProvider",
    "BedrockProvider",
    "CLINotAuthenticatedError",
    "CLINotInstalledError",
    "ChatMessage",
    "ClaudeCLIProvider",
    "CodexCLIProvider",
    "CompletionRequest",
    "CompletionResponse",
    "LLMError",
    "LLMProvider",
    "LLMProviderConfig",
    "LLMRouter",
    "ModelChoice",
    "OllamaProvider",
    "OpenAICompatProvider",
    "ProviderDownError",
    "RateLimitedError",
    "StreamChunk",
    "ToolCall",
    "ToolSpec",
    "Usage",
    "VertexAIProvider",
    "detect_local_providers",
    "choose_discovered_models",
    "discovered_model_ids",
    "parse_tool_call",
    "render_prompt",
    "render_tools_block",
]
