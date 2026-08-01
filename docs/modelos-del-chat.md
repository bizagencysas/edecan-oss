# Los modelos del chat

El chat de Edecán te deja elegir con qué cerebro habla, desde el selector del composer (macOS y iOS). Los nombres son formas poéticas; esta tabla dice cuál es cuál de verdad.

## Los cuatro principales

| Nombre | Modelo real de Workers AI | Contexto | Ve imágenes | Esfuerzo | Latencia |
|---|---|---|---|---|---|
| **Copla** | `@cf/meta/llama-4-scout-17b-16e-instruct` | 131k | ✅ | — | **0.65 s** |
| **Silva** | `@cf/moonshotai/kimi-k2.7-code` | **262k** | ✅ | ✅ | 1.31 s |
| **Soneto** | `@cf/google/gemma-4-26b-a4b-it` | 256k | ✅ | ✅ | 2.08 s |
| **Oda** | `@cf/moonshotai/kimi-k2.6` | **262k** | ✅ | ✅ | 2.43 s |

**Copla** es el default y el más rápido: verso corto y popular, para el 90 % de las conversaciones. Es el único **sin** razonamiento, así que es también el único donde la fila *Esfuerzo* no aparece — un control que no cambiaría nada no se muestra.

**Silva** es verso libre y extenso: 262k de contexto y el especialista en código de los cuatro.

**Soneto** es estructura y equilibrio. Arquitectura MoE (`a4b` = 4 000 M de parámetros activos), de ahí que rinda como uno grande sin costar como uno grande.

**Oda** es la forma elevada: el más profundo, para lo difícil.

**Los cuatro ven imágenes.** No por lo que declara la API de Cloudflare, sino comprobado: se les mandó un PNG de 64×64 con la mitad de arriba roja y la de abajo azul, y se les pidió ubicar **ambas** mitades (adivinando un color solo no se acierta). Los cuatro respondieron "arriba=rojo, abajo=azul". Acierto de herramientas: **9/9 los cuatro** (3 casos × 3 corridas, con 20 herramientas ofrecidas).

## Más modelos

Ocho modelos más, aquí en el orden en que se comportaron. Todos con function calling — sin eso un asistente con herramientas no sirve — pero **ninguno ve imágenes**, y el selector los etiqueta como tales. Si mandas una captura, usa uno de los cuatro de arriba.

Acierto medido igual que los principales, con 20 herramientas ofrecidas (3 casos × 2 corridas):

| Nombre | Modelo real | Contexto | Razona | Acierto | Latencia |
|---|---|---|---|---|---|
| Mistral Small 3.1 | `@cf/mistralai/mistral-small-3.1-24b-instruct` | 128k | — | 6/6 | 1.41 s |
| GPT-OSS 120B | `@cf/openai/gpt-oss-120b` | 128k | ✅ | 6/6 | 1.74 s |
| Llama 3.3 70B | `@cf/meta/llama-3.3-70b-instruct-fp8-fast` | 24k | — | 6/6 | 1.79 s |
| Qwen3 30B | `@cf/qwen/qwen3-30b-a3b-fp8` | 32k | ✅ | 6/6 | 3.52 s |
| GLM 4.7 Flash | `@cf/zai-org/glm-4.7-flash` | 131k | ✅ | 6/6 | 3.72 s |
| GPT-OSS 20B | `@cf/openai/gpt-oss-20b` | 128k | ✅ | 5/6 | 0.84 s |
| Granite 4.0 Micro | `@cf/ibm-granite/granite-4.0-h-micro` | 131k | — | 5/6 | 1.64 s |
| Nemotron 120B | `@cf/nvidia/nemotron-3-120b-a12b` | 256k | ✅ | 5/6 | 2.56 s |

Los tres últimos fallan una de seis, y vale saber cómo: **GPT-OSS 20B** y **Granite** devuelven a veces la llamada a la herramienta como **texto** en vez de como `tool_call` — cuando pasa, la herramienta no se ejecuta y el asistente parece "hablar de" hacer algo en lugar de hacerlo. **Nemotron** sí llama una herramienta, pero una equivocada.

## Lo que NO está y por qué

- **GLM 5.2** — capaz, 262k, pero tarda **42 s por vuelta** del ciclo agente↔herramientas. Con las 53 vueltas que llegó a dar una edición, son horas. No sirve para trabajo agéntico por muy bueno que sea. Medido, no estimado; ver `config/modelos.yml`.
- **Llama 3.2 11B Vision** — ve imágenes pero **no tiene function calling**. Un Edecán que no puede usar herramientas no es Edecán.
- **Kimi K3 y K6** — no existen en el catálogo de esta cuenta. Lo más nuevo de Moonshot disponible es K2.7 y K2.6, que son Silva y Oda.
- **qwq-32b, deepseek-r1-distill, gemma-sea-lion, los `-lora`, llama-guard** — sin function calling, o modelos base/de moderación que no son para conversar.

## Dos trampas medidas (para quien toque esto)

**El presupuesto de tokens y la visión.** Con `max_tokens=100`, Silva y Oda devuelven contenido **vacío** al mirar una imagen; con 2 000 aciertan. Son modelos que razonan y se les va el presupuesto pensando antes de responder. Es el mismo fallo que costó caro en el loop del agente (ver `_MAX_TOKENS_POR_ITERACION` en `packages/core/edecan_core/agent.py`). Por eso el nivel más bajo de *Esfuerzo* tiene un piso: un esfuerzo bajo que devuelva respuestas vacías sería peor que no tener el control.

Esto también explica mediciones antiguas que parecían decir que Soneto y los kimis eran malos con herramientas: no eran incapaces, se quedaban sin aire.

**El tamaño mínimo de imagen.** Soneto rechaza imágenes de menos de 10 px de lado con `HTTP 400 image dimensions must be at least 10px`. Si en algún punto el chat genera miniaturas, ojo con ese piso.

## Dónde se decide el modelo

Hay más de un sitio y el orden importa. De mayor a menor precedencia:

1. La selección de la conversación (lo que eliges en el selector).
2. `WORKERS_AI_CHAT_MODEL` en `~/Library/Application Support/cc.edecan.desktop/data/platform-config.json` — **este gana sobre el código**. Cambiar solo el repo no tiene efecto en una instalación de escritorio ya existente.
3. `WORKERS_AI_CHAT_MODEL` en `apps/api/edecan_api/config.py`.
4. `MODELO_POR_DEFECTO` en `packages/llm/edecan_llm/workers_ai.py`.

El default de los niveles 2–4 **debe ver imágenes**: el chat acepta jpeg, png, gif y webp (`_DIRECT_VISION_MIMES` en `apps/api/edecan_api/routers/conversations.py`) y los inserta directo en el turno. Poner ahí un modelo ciego rompe las capturas de pantalla en silencio, sin ningún error visible.

Para saber qué modelo corrió de verdad en una llamada, la bitácora es `~/Library/Application Support/cc.edecan.desktop/data/llm-calls.jsonl`: registra por iteración el modelo, las herramientas ofrecidas, las pedidas y si la salida vino vacía.

## Cómo se releen las capacidades

El catálogo vivo de la cuenta, con los flags de visión, function calling y razonamiento:

```bash
curl -s -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  "https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/ai/models/search?per_page=200&task=Text%20Generation"
```

Los flags son un punto de partida, **no la verdad**: Soneto declara visión y aun así rechazaba una imagen de 8×8. Cualquier modelo nuevo que se agregue al selector se prueba con una imagen real y con herramientas antes de prometerle nada al usuario.
