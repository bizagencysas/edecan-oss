# `edecan_llm`

Contrato genérico y enrutamiento automático para la inferencia de Edecán.

## Arquitectura actual

- `LLMProvider` define una interfaz única: `complete()` y `stream()`.
- `WorkersAIProvider` adapta esa interfaz al endpoint OpenAI-compatible de
  Cloudflare Workers AI.
- `TaskRouter` clasifica la tarea mediante metadatos internos. La persona no
  ve ni elige proveedor o modelo.
- `LLMRouter` compone ambas piezas y sobrescribe cualquier modelo enviado por
  un cliente con la decisión de plataforma.

Todas las superficies que no pertenecen al IDE usan
`@cf/zai-org/glm-4.7-flash`:

- chat normal;
- voz y llamadas;
- herramientas ligeras;
- trabajos asíncronos fuera del IDE.

El razonamiento interno de GLM se desactiva con
`chat_template_kwargs.enable_thinking=false` para priorizar velocidad y costo.

## Límite del IDE

El IDE conserva un runtime y un router de ingeniería separados del chat. Ese
runtime reutiliza el contrato `LLMProvider` y el adaptador
`WorkersAIProvider`, pero no pasa por el `TaskRouter` conversacional. Así puede
mantener sesiones, herramientas, permisos, modelos y presupuestos propios sin
acoplar el resto de Edecán al proveedor. Si una petición marcada como `ide`,
`engineering` o `code` llega al router de chat, falla de forma explícita en
vez de usar el modelo rápido por accidente.

## Configuración

```dotenv
CLOUDFLARE_ACCOUNT_ID=
CLOUDFLARE_API_TOKEN=
WORKERS_AI_CHAT_MODEL=@cf/zai-org/glm-4.7-flash
WORKERS_AI_IDE_MODEL=@cf/zai-org/glm-5.2
WORKERS_AI_IDE_VISION_MODEL=@cf/moonshotai/kimi-k2.7-code
WORKERS_AI_TIMEOUT_SECONDS=60
```

La cuenta y el token pertenecen al operador de la instalación. No son
credenciales por tenant y nunca se devuelven a clientes.

## Intercambiar proveedor

Los agentes, llamadas y workers dependen de `LLMProvider`, no de Cloudflare.
Para incorporar OpenAI, Anthropic, Google u otro backend se implementa un
adaptador y se inyecta una nueva `ProviderFactory` al construir `LLMRouter`.
Los consumidores y el `TaskRouter` no cambian.

## Pruebas aisladas

```bash
uv run --isolated --project packages/llm pytest packages/llm/tests
```

Los tests unitarios interceptan HTTP. La verificación real de Cloudflare debe
ejecutarse deliberadamente en un entorno con las dos credenciales configuradas
y nunca imprimir el token.
