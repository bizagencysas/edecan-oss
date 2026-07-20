# Proveedores LLM — API, CLI local u Ollama

Edecán necesita un modelo de lenguaje para pensar. Este documento explica las seis formas de conectarlo (`edecan_llm`, `ARCHITECTURE.md` §3, §10.6 y §12): cuál elegir, qué requiere cada una, y sus limitaciones. Filosofía del producto ([`credenciales.md`](./credenciales.md)): **todo lo trae el cliente** — nunca una llave compartida de la plataforma, y siempre el camino de menos clicks posible.

## Resumen — cuál elegir

| Proveedor | Elígelo si... | Requiere |
|---|---|---|
| [Claude CLI](#claude-cli-y-codex-cli-usa-lo-que-ya-tienes) | Ya pagas Claude Code | Nada nuevo — el binario `claude` autenticado en tu máquina |
| [Codex CLI](#claude-cli-y-codex-cli-usa-lo-que-ya-tienes) | Ya pagas Codex/ChatGPT con acceso a Codex | Nada nuevo — el binario `codex` autenticado en tu máquina |
| [Ollama](#ollama-100-local-y-gratis) | Quieres privacidad total y costo cero | Ollama instalado y corriendo, con al menos un modelo descargado |
| [Vertex AI / Gemini — API key](#vertex-ai--gemini) | Quieres empezar rápido con Google | Una API key de Google AI Studio (un campo) |
| [Vertex AI — proyecto GCP](#vertex-ai--gemini) | Ya tienes un proyecto de Google Cloud y facturación propia | Proyecto GCP + clave de cuenta de servicio (avanzado) |
| Anthropic (API key) | Quieres la API directa de Anthropic sin pasar por Claude Code | Una API key de Anthropic |
| OpenAI-compatible | Usas OpenAI, Groq, Together.ai, o cualquier endpoint `/chat/completions` | `base_url` + API key de ese proveedor |

Los CLIs y Ollama son las opciones de **cero configuración**: si ya están instalados en tu máquina, la pantalla de Configuración los detecta solos (`detect_local_providers`, ver más abajo) y basta un clic — nunca hace falta pegar una credencial nueva.

## El contrato: `LLMProviderConfig`

Todo lo que la pantalla de Configuración necesita guardar para un proveedor es un `LLMProviderConfig` (`edecan_llm/config.py`):

```python
@dataclass(frozen=True)
class LLMProviderConfig:
    kind: str  # "anthropic"|"openai_compat"|"vertex"|"claude_cli"|"codex_cli"|"ollama"
    api_key: str | None = None
    base_url: str | None = None
    model_principal: str | None = None
    model_rapido: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
```

`LLMRouter(settings, provider_config=mi_config)` construye el proveedor correspondiente y resuelve los alias `"principal"`/`"rapido"` según lo que traiga esa config (con un default sano por proveedor si falta algo — ver `edecan_llm.router.LLMRouter._config_models`). Sin `provider_config` (el valor por defecto, `None`), el router se comporta exactamente igual que antes de fase v3: resuelve desde `ANTHROPIC_API_KEY`/`OPENAI_COMPAT_*` de la plataforma.

## Auto-detección: `detect_local_providers`

```python
from edecan_llm import detect_local_providers

detect_local_providers(settings)
# {
#   "claude_cli": {"installed": True, "path": "/usr/local/bin/claude", "version": "1.2.3"},
#   "codex_cli":  {"installed": False, "path": None, "version": None},
#   "ollama":     {"running": True, "base_url": "http://localhost:11434", "models": ["llama3.1:8b"]},
# }
```

Síncrona, rápida (timeouts cortos: 5s para `--version`, 2s para Ollama) y **nunca lanza** — cualquier fallo se traduce en silencio a "no detectado". La pantalla de Configuración la llama al abrir para poder ofrecer "usar mi Claude CLI ya instalado" o "usar Ollama" como botones de un solo clic, sin pedir ninguna credencial.

## Claude CLI y Codex CLI: usa lo que ya tienes

`ClaudeCLIProvider` / `CodexCLIProvider` (`kind="claude_cli"`/`"codex_cli"`) ejecutan el binario (`claude`/`codex`) ya instalado y autenticado en la máquina del cliente como subproceso local — sin pedir ninguna API key nueva, reutilizando la suscripción que el cliente ya paga.

**Solo tienen sentido con el backend corriendo LOCAL** en la máquina del cliente (el modelo de la app de escritorio Tauri) — no aplican a un hosted multi-tenant compartido: no se puede tener la sesión CLI autenticada de cada cliente en un servidor compartido.

Configuración (`extra`):

```python
LLMProviderConfig(kind="claude_cli", extra={"binary_path": "/usr/local/bin/claude"})
```

`binary_path` es opcional — si falta, se busca `claude`/`codex` en el `PATH` (`shutil.which`). Si no se encuentra ninguno de los dos, `LLMError` con instrucciones ("instálalo desde claude.com/claude-code" / "instálalo desde github.com/openai/codex").

**Cómo funcionan por dentro:**

- El prompt siempre viaja por **stdin**, nunca como argumento de la línea de comandos — evita inyección de argv y sus límites de longitud. Nunca se usa `shell=True`.
- El modo "print" de ambos CLIs (`claude -p`, `codex exec`) es de **un solo turno** (sin memoria de proceso): todo el `system` + historial de la conversación se aplanan a un único prompt de texto en cada llamada (`edecan_llm.prompted_tools.render_prompt`).
- Timeout configurable (300s por defecto — generoso, porque un CLI puede tardar en razonar) con `asyncio.wait_for` + `kill()` del proceso si se excede.
- Si el proceso termina con error y el mensaje sugiere que falta iniciar sesión, se traduce a un error claro: "corre `claude login`" / "corre `codex login`".

**Limitación importante — tool-calling es "mejor esfuerzo":** ninguno de los dos CLIs tiene una API de tool-use estructurada como Anthropic/OpenAI/Gemini/Ollama. Cuando el turno ofrece herramientas, Edecán le agrega al prompt una instrucción (en español) pidiéndole que responda ÚNICAMENTE un JSON `{"tool_call": {"name": ..., "arguments": {...}}}` si necesita usar una. Si el CLI no sigue esa instrucción al pie de la letra (la envuelve en prosa, la ignora, la trunca), Edecán simplemente no detecta la llamada a herramienta y trata la respuesta como texto normal — no hay garantía del 100% como con el tool-use nativo. Ver `edecan_llm/prompted_tools.py`.

## Ollama: 100% local y gratis

`OllamaProvider` (`kind="ollama"`) habla con una instancia de [Ollama](https://ollama.com) corriendo en la máquina del cliente (`http://localhost:11434` por defecto, configurable con `base_url`) — la opción más privada: **ningún dato sale de la máquina**, sin API key, sin costo por token.

```python
LLMProviderConfig(kind="ollama", base_url="http://localhost:11434", model_principal="llama3.1:8b")
```

A diferencia de los CLIs, Ollama sí soporta tool-calling nativo (`POST /api/chat` con `tools`), así que las herramientas de Edecán funcionan igual de bien que con Anthropic/OpenAI — sin el "mejor esfuerzo" de los CLIs.

**Limitaciones a tener en cuenta:**

- El `model_principal` (y `model_rapido`) deben ser un modelo que el cliente ya haya descargado (`ollama pull llama3.1`) — a diferencia de Vertex/Anthropic, no hay un modelo por defecto universal que siempre exista, así que la pantalla de Configuración debe ofrecer el `models` que devuelve `detect_local_providers` en vez de un campo de texto libre.
- Calidad y velocidad dependen 100% del hardware del cliente y del modelo elegido — un modelo de 8B en una laptop no es comparable a Claude/Gemini en la nube.
- Timeout generoso (300s) porque los modelos locales pueden ser lentos.

### Ollama embebido en la app de escritorio (todavía más cero-fricción)

Lo de arriba asume que el cliente ya instaló Ollama por su cuenta. La app de escritorio (`apps/desktop`) va un paso más allá y puede **traer el binario de Ollama empaquetado adentro del instalador** (patrón adaptado de `open-jarvis/OpenJarvis`, Apache-2.0, ver `NOTICE`), arrancándolo sola en segundo plano — cero instalación aparte, cero paso manual más allá de activarlo. Es 100% opcional (para quien empaqueta y para quien usa la app) y no cambia nada de lo de arriba: sigue siendo el mismo `OllamaProvider`/`kind="ollama"` hablando con `http://localhost:11434`, la única diferencia es QUIÉN arrancó ese proceso. Detalle completo (cómo empaquetarlo, cómo activarlo, arquitectura interna): [`desktop.md`](./desktop.md) §10 y [`desktop-local.md`](./desktop-local.md) §9.

## Vertex AI / Gemini

`VertexAIProvider` (`kind="vertex"`) tiene **dos modos**, elegidos con `extra["mode"]`:

### Modo `api_key` (por defecto, recomendado para empezar)

El camino simple: la API pública de Gemini (`https://generativelanguage.googleapis.com`), autenticada con una sola API key de [Google AI Studio](https://aistudio.google.com/apikey).

```python
LLMProviderConfig(
    kind="vertex",
    api_key="TU_GEMINI_API_KEY_AQUI",
    extra={"mode": "api_key"},
)
```

Esto es lo que la pantalla de Configuración debe ofrecer primero: un campo, un botón "Conectar" — igual que Anthropic/OpenAI. **Nunca** empezar por pedirle a un cliente nuevo que configure un proyecto de Google Cloud.

### Modo `service_account` (avanzado — proyecto GCP propio)

El endpoint real de Vertex AI, para quien ya tiene un proyecto de Google Cloud y prefiere facturar ahí directamente:

```python
LLMProviderConfig(
    kind="vertex",
    extra={
        "mode": "service_account",
        "project_id": "mi-proyecto-gcp",
        "region": "us-central1",  # opcional, este es el default
        "service_account_json": "...",  # el JSON completo de la clave descargada de GCP
    },
)
```

Requiere el extra opcional de este paquete: `uv pip install "edecan-llm[vertex]"` (instala `google-auth`). Sin ese extra instalado, intentar usar este modo da un `LLMError` con esa misma instrucción — el import de `google.oauth2` es diferido y guardeado, así que el paquete entero sigue funcionando sin él si nadie usa este modo. El access token se refresca solo (`Credentials.refresh`, cacheado hasta 60s antes de expirar) — la app de escritorio debe mostrar este modo como "avanzado", separado del flujo simple de API key.

Ambos modos comparten toda la traducción de mensajes/herramientas al formato `generateContent` de Gemini (`system` → `systemInstruction`, `tool_use`/`tool_result` → `functionCall`/`functionResponse`, etc.).

## Anthropic y OpenAI-compatible

Mismos dos proveedores de siempre (`AnthropicProvider`/`OpenAICompatProvider`, ver `docs/configuracion.md` para los campos `ANTHROPIC_API_KEY`/`OPENAI_COMPAT_BASE_URL`/`OPENAI_COMPAT_API_KEY` que usa `apps/worker` para sus propios jobs de sistema), pero para el chat de un tenant es SIEMPRE `LLMProviderConfig(kind="anthropic", api_key=...)` / `LLMProviderConfig(kind="openai_compat", base_url=..., api_key=...)` con la API key que el propio tenant conectó (bring-your-own) — `apps/api` nunca depende de la de plataforma como alternativa (ver `docs/credenciales.md` "Orden de resolución").

## Resolución de modelo (`"principal"`/`"rapido"`)

`LLMRouter.resolve("principal"|"rapido", tenant_flags)` prioriza `provider_config.model_principal`/`model_rapido` sobre cualquier variable de entorno de plataforma. Si falta alguno, cae a un default sano según el proveedor:

- `anthropic`: `ANTHROPIC_MODEL_PRINCIPAL`/`ANTHROPIC_MODEL_RAPIDO` de `settings`, o `claude-sonnet-4-5`/`claude-haiku-4-5` si ni eso está.
- `vertex`: `VERTEX_MODEL_PRINCIPAL`/`VERTEX_MODEL_RAPIDO` de `settings`, o `gemini-2.5-pro`/`gemini-2.5-flash`.
- `openai_compat`/`claude_cli`/`codex_cli`/`ollama`: no hay un default universal sensato (no existe "el modelo de OpenAI-compat" ni "el modelo de Ollama" genérico) — si falta `model_rapido`, se usa `model_principal`. Para los CLIs, un `model_principal` vacío es válido a propósito: el binario usa su propio modelo configurado por defecto en vez de que Edecán le fuerce uno con `--model`.

El downgrade de `"principal"` a `"rapido"` cuando el plan del tenant no tiene el flag `models.premium` (`ARCHITECTURE.md` §10.13) se mantiene igual sin importar el proveedor elegido.

## Privacidad, por proveedor

| Proveedor | Tus mensajes salen a... |
|---|---|
| Ollama | A ningún lado — corre 100% en tu máquina. |
| Claude CLI / Codex CLI | A los servidores de Anthropic/OpenAI, bajo los términos de tu propia cuenta/suscripción (igual que usar `claude`/`codex` a mano). |
| Vertex AI / Gemini (`api_key`) | A los servidores de Google (Gemini API), bajo tu propia API key. |
| Vertex AI (`service_account`) | A los servidores de Google Cloud, dentro de tu propio proyecto GCP — mismo control de datos que cualquier otro servicio que corras en tu GCP. |
| Anthropic / OpenAI-compatible | Al proveedor que configures, bajo tu propia API key. |

Ninguno de estos proveedores lo opera ni lo paga el dueño de la plataforma Edecán — es siempre la cuenta/credencial del propio tenant (`ARCHITECTURE.md` §0.3).
