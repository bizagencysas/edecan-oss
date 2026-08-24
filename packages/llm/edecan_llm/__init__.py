"""`edecan_llm` — abstracción de proveedor LLM (`ARCHITECTURE.md` §3, §10.6, §12).

Interfaz única `LLMProvider` (`complete`/`stream`) y `TaskRouter` automático.
La inferencia normal usa `WorkersAIProvider`; el IDE tiene un runtime y un
router de ingeniería separados, pero reutiliza el mismo contrato de proveedor.
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
from .task_router import TaskDecision, TaskKind, TaskRouter
from .vertex import VertexAIProvider
from .workers_ai import WorkersAIProvider

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
    "TaskDecision",
    "TaskKind",
    "TaskRouter",
    "Usage",
    "VertexAIProvider",
    "WorkersAIProvider",
    "detect_local_providers",
    "choose_discovered_models",
    "discovered_model_ids",
    "parse_tool_call",
    "render_prompt",
    "render_tools_block",
]
