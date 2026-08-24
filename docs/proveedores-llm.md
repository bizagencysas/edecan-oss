# Proveedor de inferencia

La composición actual de Edecán usa Cloudflare Workers AI para toda inferencia
que no pertenece al IDE.

## Modelo activo

`@cf/zai-org/glm-4.7-flash` atiende:

- conversaciones normales;
- voz y llamadas;
- tool calling ligero;
- jobs y automatizaciones fuera del IDE.

`@cf/zai-org/glm-5.2` atiende:

- ingeniería de software (Forge);
- tareas compuestas con razonamiento profundo y herramientas complejas.

La persona nunca selecciona un modelo. `TaskRouter` decide leyendo `config/modelos.yml`
según la superficie y el perfil de tarea. Una petición de cliente no puede sobrescribir esa
decisión.

## Contrato intercambiable y `WorkersAIProvider`

`LLMProvider` define `complete()` y `stream()` sobre tipos comunes.
`WorkersAIProvider` es el adaptador nativo sobre la REST API de Cloudflare Workers AI (`POST /accounts/{id}/ai/run/{modelo}`).

Características principales:
- **Respuesta enriquecida (`ProbeCompletionResponse`)**: preserva `reasoning_content`, `cached_tokens` (uso de caché de prefijo), `neurons` (cómputo real Cloudflare) y los argumentos crudos de herramientas.
- **Desempaquetado seguro**: desencapsula `{"result": ..., "success": true/false}` y valida el flag de éxito incluso con HTTP 200.
- **Errores tipados**: lanza `CredencialInvalidaError` (extrae código Cloudflare como 5018), `PeticionInvalidaError`, `LimiteDeTasaError` (429) y `FalloTransitorioError` (5xx).
- **Reserva de presupuesto y reintento de razonamiento**: reintenta automáticamente si una llamada a modelo con razonamiento agota `max_tokens` y devuelve `content` vacío.

## Configuración del host

```dotenv
CLOUDFLARE_ACCOUNT_ID=
CLOUDFLARE_API_TOKEN=
WORKERS_AI_CHAT_MODEL=@cf/zai-org/glm-4.7-flash
WORKERS_AI_TIMEOUT_SECONDS=120
```

Las credenciales son de infraestructura. No viven en `TokenVault`, no se
configuran por tenant y no se exponen en la interfaz.

## IDE / Forge

El IDE vive en un runtime de ingeniería separado pero utiliza el mismo contrato `LLMProvider` y la misma base de perfiles configurada en `config/modelos.yml`.
