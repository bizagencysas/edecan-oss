# Inferencia administrada con Cloudflare Workers AI

Edecán usa una arquitectura de proveedor intercambiable, pero su composición
actual envía toda la inferencia que no pertenece al IDE a Cloudflare Workers
AI.

## Flujo

```text
chat / voz / llamada / worker
             |
             v
        LLMRouter
             |
             v
        TaskRouter
             |
             v
   WorkersAIProvider
             |
             v
 @cf/zai-org/glm-4.7-flash
```

La persona nunca selecciona un modelo. La superficie añade metadatos internos
como `channel=phone` o `task_type=voice`, y `TaskRouter` toma la decisión.
Incluso si un cliente antiguo envía otro nombre de modelo, `LLMRouter` lo
reemplaza antes de llamar al proveedor.

## Responsabilidades

### `LLMProvider`

Contrato agnóstico con tipos comunes para mensajes, herramientas, uso,
completions y streaming. El resto del sistema depende solo de este contrato.

### `WorkersAIProvider`

Traduce el contrato interno al endpoint OpenAI-compatible de Workers AI:

```text
https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1
```

La autenticación se toma exclusivamente del entorno del host. La credencial no
se guarda por tenant, no se acepta desde el chat y no se muestra en ninguna
respuesta.

### `TaskRouter`

Enruta chat, voz, llamadas, herramientas ligeras y trabajos asíncronos fuera
del IDE a `@cf/zai-org/glm-4.7-flash`. El pensamiento extendido del modelo se
desactiva para favorecer latencia y costo.

## IDE: runtime independiente, proveedor compartido

El IDE no pasa por el `TaskRouter` del chat. Su ciclo de agente, herramientas,
terminal, archivos, Git, skills y MCP vive en el companion de escritorio. Ese
runtime sí usa el adaptador común `WorkersAIProvider`, con rutas automáticas:

- texto e ingeniería: `@cf/zai-org/glm-5.2`;
- una sesión con imágenes: `@cf/moonshotai/kimi-k2.7-code`, porque GLM-5.2 es
  texto-only en el endpoint actual y Kimi conserva visión, razonamiento y
  function calling.

La persona nunca elige estos modelos. El modelo aporta inteligencia; el
companion conserva los poderes y el aislamiento del workspace.

## Variables

```dotenv
CLOUDFLARE_ACCOUNT_ID=
CLOUDFLARE_API_TOKEN=
WORKERS_AI_CHAT_MODEL=@cf/zai-org/glm-4.7-flash
WORKERS_AI_IDE_MODEL=@cf/zai-org/glm-5.2
WORKERS_AI_IDE_VISION_MODEL=@cf/moonshotai/kimi-k2.7-code
WORKERS_AI_TIMEOUT_SECONDS=60
```

`CLOUDFLARE_API_TOKEN` debe tener únicamente los permisos necesarios para
Workers AI. Nunca debe entrar al repositorio, logs, capturas o respuestas.

## Agregar otro proveedor en el futuro

1. Implementar `LLMProvider`.
2. Traducir los tipos comunes al protocolo del proveedor.
3. Inyectar una nueva `ProviderFactory` al construir `LLMRouter`.
4. Mantener la decisión de producto dentro de `TaskRouter`.
5. Ejecutar la misma suite contractual de completions, streaming, herramientas
   y errores.

No se modifican agentes, telefonía, workers ni clientes.

## Verificación

Suite aislada:

```bash
uv run --isolated --project packages/llm pytest packages/llm/tests
```

La prueba real debe cubrir, sin imprimir secretos:

1. completion normal;
2. streaming;
3. una tool call estructurada;
4. intento de sobrescribir el modelo desde el cliente;
5. rechazo explícito de una tarea del IDE.
