# Traspaso: conectar Edecán a Workers AI

> Verificado con llamadas reales contra la API el 27 de julio de 2026.
> Nada de este documento es de memoria ni de la documentación: todo salió de una respuesta real.

---

## 0. Lo primero, porque cambia el orden del trabajo

**Hoy Edecán no tiene un cerebro conectado: tiene un agente entero subcontratado.**

`packages/llm/edecan_llm/codex_cli.py:49` lo dice en su propio comentario:

> «`codex exec` es un agente de código completo, no un endpoint de inferencia»

Codex CLI está haciendo, gratis y fuera de Edecán, todo esto: planificar, ejecutar comandos,
editar archivos, reintentar ante el error, y gestionar su propio contexto. Usa sus propias
herramientas (`shell_tool`, `unified_exec`) y tool-calling por prompt vía `prompted_tools`.

Workers AI es un endpoint de inferencia. No planifica, no ejecuta nada, no edita archivos, no
reintenta. **En el momento del cambio, todo ese bucle tiene que existir dentro de Edecán.**

### La consecuencia práctica

Cambiar el cerebro **no** produce un agente. Produce un modelo excelente que no puede hacer nada:

```text
modelo brillante → genera código → no puede ejecutarlo → no ve el error real → supone que funciona
```

Eso es cierto siempre. Lo que **no** aplica aquí es la urgencia: Edecán no está en producción —
Aria sigue intacto y es el sistema que de verdad opera—, así que no hay nada que proteger y el
cambio no tiene riesgo de romper trabajo real.

**Orden recomendado, con ese contexto:**

1. Añadir el proveedor `workers_ai` a `packages/llm`, probado y disponible.
2. Usarlo ya para lo que **no** necesita bucle —chat, redacción, clasificación, resúmenes,
   enrutado— según `config/modelos.yml`. Ahí el cambio es hoy y no hay contrapartida.
3. Para ingeniería, `codex_cli` **puede** quedarse, pero ya no por prudencia: se queda porque es
   una **línea base de comparación** útil. Cuando el bucle propio de Forge corra el mismo banco de
   tareas, se sabrá con números si mejora o empeora. Si estorba para cualquier otra cosa, se quita
   sin ceremonia.

Los dos pueden convivir: `packages/llm/edecan_llm/config.py` ya permite un proveedor por uso.

---

## 1. Estado real de la cuenta

| | |
|---|---|
| Account ID | `bd97ab5c87d3d2f6d99f93465aa63679` |
| Token | funciona (verificado con HTTP 200 sobre `@cf/meta/llama-3.1-8b-instruct`) |
| **Kimi K3** | **403, code 5018 — la cuenta NO tiene acceso todavía.** Solicitado |
| Modelos Kimi visibles | `@cf/moonshotai/kimi-k2.7-code`, `@cf/moonshotai/kimi-k2.6` |

Las credenciales viven en `.env` (permisos 600, ignorado por git en `.gitignore:2`):
`CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`, `FORGE_PROBE_MODEL`, `FORGE_PROBE_MODEL_PUENTE`.

---

## 2. La API, exactamente

```
POST https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/run/{MODELO}
Authorization: Bearer {API_TOKEN}
Content-Type: application/json
```

`{MODELO}` incluye el prefijo: `@cf/moonshotai/kimi-k2.7-code`.

Cuerpo estilo OpenAI (`messages`, `max_tokens`, `stream`, `tools`, `tool_choice`).
La respuesta viene envuelta en el sobre de Cloudflare:

```json
{ "result": { "choices": [...], "usage": {...} }, "success": true, "errors": [], "messages": [] }
```

Hay que desenvolver `result` **y** comprobar `success`: un fallo llega con HTTP 200 en algunos
casos y con `success: false`.

### Capacidades declaradas de `kimi-k2.7-code`

`context_window: 262144` · `function_calling: true` · `reasoning: true` · `vision: true`

### Precio real (viene en la API, no hay que codificarlo a mano)

```
GET /client/v4/accounts/{ACCOUNT_ID}/ai/models/search?search=kimi
```

| Concepto | USD por millón |
|---|---|
| Entrada | 0,95 |
| Salida | 4,00 |
| **Entrada cacheada** | **0,19** |

Leerlo de la API en vez de fijarlo evita que el modelo de coste envejezca, y hace que cambiar de
modelo actualice los precios solo.

---

## 3. Las tres trampas que van a morder

### 3.1 El razonamiento va en otro campo y se come el presupuesto

La respuesta trae **dos** campos de texto:

```json
"message": {
  "content": "FORGE OK",
  "reasoning_content": "We need respond exactly with two words...",
  "role": "assistant"
}
```

El razonamiento está **siempre activo**, se factura como salida (4 USD/M) y consume `max_tokens`.

Medición real: una respuesta de **dos palabras** gastó **65 tokens de salida**, ~57 de ellos de
razonamiento. Es 8× de sobrecoste en respuestas cortas.

Y el fallo silencioso: con `max_tokens: 32`, la respuesta llegó con **`content` vacío** y
`finish_reason: length` — y se cobró igual. Un integrador que ponga `max_tokens` ajustado verá
respuestas vacías intermitentes sin ningún error.

**Qué hacer:** reservar presupuesto de razonamiento (mínimo ~200 tokens por encima de lo que se
espera de contenido), tratar `content` vacío con `finish_reason: length` como error recuperable, y
no mandar `reasoning_content` de vuelta en el historial (es del turno, no de la conversación).

### 3.2 `tool_calls` trae los argumentos como *string*, no como objeto

Forma OpenAI: `choices[0].message.tool_calls[].function.arguments` es un **string JSON** que hay
que parsear. Cuando el argumento lleva un bloque de código —comillas, llaves, barras invertidas,
f-strings, acentos— es donde se rompe. Hay que validar que el texto llega **byte a byte idéntico**,
no solo que el JSON parsea.

`packages/llm/edecan_llm/openai_compat.py` ya hace esta traducción: reutilizarla, no reescribirla.

### 3.3 La señal de caché está ahí, y vale 5×

```json
"usage": {
  "prompt_tokens": 26,
  "completion_tokens": 65,
  "prompt_tokens_details": { "cached_tokens": 0 },
  "neurons": 25.88
}
```

`cached_tokens` es la caché de prefijo y `neurons` es la unidad de facturación de Cloudflare.
Registrar ambos desde el primer día.

Con entrada cacheada a 0,19 frente a 0,95, **mantener el prefijo del prompt estable** (sistema →
herramientas → contexto estable → historial → turno actual, en ese orden y sin reordenar) vale del
orden de un 65 % más de trabajo con el mismo crédito. No es una optimización de más adelante.

---

## 4. Dónde encaja en el repo

- Nuevo adaptador en `packages/llm/edecan_llm/workers_ai.py`, implementando el `LLMProvider` de
  `base.py`. **No** un camino paralelo nuevo.
- Añadir `"workers_ai"` al conjunto permitido en `packages/llm/edecan_llm/config.py:26` y a la
  documentación de `kind` (líneas 39 y 47).
- Exportarlo en `packages/llm/edecan_llm/__init__.py`.
- Actualizar `docs/proveedores-llm.md`.
- Tests con `respx` (ya es dependencia de desarrollo): éxito, `success: false` con HTTP 200,
  401/403, 429 con reintento, timeout, streaming SSE, `tool_calls` con bloque de código
  multilínea, y parseo de `cached_tokens` / `neurons`.

Referencia: `packages/forge-probe/` tiene ya el contrato de `ModelCard` y la sonda que mide todo
esto de forma reproducible.

---

## 5. Lo que sigue sin saberse

Que K3 sea mejor no responde a las preguntas que dimensionan el sistema:

- Cuánto contexto **útil** tiene de verdad (262k o 1M anunciados no dicen cuánto recuerda a 200k).
- Cuál es su fiabilidad real metiendo bloques de código en argumentos JSON, medida con intervalo
  de confianza y no con una impresión.
- Cuál es el sobrecoste real de su razonamiento, y si `reasoning_effort` lo gradúa de verdad.
- Cuánto cuesta una tarea completa de principio a fin.

Eso lo mide `packages/forge-probe`. Son unos pocos dólares y dos semanas, y los números salen
iguales para K3 que para cualquier otro modelo que venga después.
