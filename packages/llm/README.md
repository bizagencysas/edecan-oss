# packages/llm — `edecan_llm`

Abstracción de proveedor de modelos de lenguaje (`ARCHITECTURE.md` §3, §10.6 y §12).

- Interfaz única `LLMProvider` (`complete`, `stream`) sobre tipos comunes (`ChatMessage`, `ToolSpec`, `ToolCall`, `Usage`, `CompletionRequest`, `CompletionResponse`, `StreamChunk`).
- `LLMRouter.resolve(alias, tenant_flags)` mapea los alias lógicos `"principal"` y `"rapido"` a un `(LLMProvider, modelo)`. Dos modos:
  - **Legacy** (sin `provider_config`): variables de entorno de plataforma (`ANTHROPIC_API_KEY`/`OPENAI_COMPAT_*`) — comportamiento sin cambios desde v1.
  - **Con `provider_config: LLMProviderConfig`** (`edecan_llm.config`, WP-V3-03): el tenant elige explícitamente su proveedor desde la pantalla de Configuración — bring-your-own-credentials para todo, incluido "usa el Claude CLI/Ollama que ya tengo instalado".

## Proveedores

| `kind` | Clase | Requiere | Notas |
|---|---|---|---|
| `anthropic` | `AnthropicProvider` | API key de Anthropic | Primario — REST puro a `https://api.anthropic.com/v1/messages`. |
| `openai_compat` | `OpenAICompatProvider` | `base_url` + API key | Cualquier endpoint `/chat/completions` compatible (OpenAI, Groq, Together.ai, un LLM local, etc.). |
| `vertex` | `VertexAIProvider` | API key de Gemini, **o** proyecto GCP + service account | Dos modos, ver `docs/proveedores-llm.md`. El modo `service_account` necesita el extra opcional `edecan-llm[vertex]`. |
| `claude_cli` | `ClaudeCLIProvider` | El binario `claude` instalado y autenticado (`claude login`) | Sin API key — usa la suscripción de Claude Code que el cliente ya tiene. Solo tiene sentido con el backend corriendo local (app de escritorio). |
| `codex_cli` | `CodexCLIProvider` | El binario `codex` instalado y autenticado (`codex login`) | Análogo a `claude_cli`, con el binario de OpenAI. |
| `ollama` | `OllamaProvider` | Ollama corriendo en la máquina (`http://localhost:11434` por defecto) | 100% local — sin API key, sin llamadas de red externas. |
| `bedrock` | `BedrockProvider` | — | Stub v1, sin implementar. |

`detect_local_providers(settings=None)` (síncrono, nunca lanza) — usado por la pantalla de Configuración/wizard de primer arranque para ofrecer "usar lo que ya tengo instalado" con un clic: detecta si `claude`/`codex` están en el `PATH` (y su versión) y si Ollama está corriendo (y qué modelos tiene descargados).

Los proveedores CLI (`claude_cli`, `codex_cli`) no aceptan tool-schemas nativos: usan el protocolo de tool-calling **por prompt** de `edecan_llm.prompted_tools` (`render_tools_block`/`parse_tool_call`/`render_prompt`) — mejor esfuerzo, no tan confiable como el tool-use nativo de Anthropic/OpenAI/Gemini/Ollama. Detalle completo, limitaciones y cómo se conecta cada proveedor desde la pantalla de Configuración: [`docs/proveedores-llm.md`](../../docs/proveedores-llm.md).

Todo uso se reporta por callback (`on_usage`) hacia `usage_events` para medición y facturación por tenant.

Los tests de este paquete no hacen llamadas de red reales ni ejecutan binarios reales — usan `respx` para simular las respuestas HTTP de cada proveedor, y scripts ejecutables fake (`tmp_path`) para los proveedores CLI.
