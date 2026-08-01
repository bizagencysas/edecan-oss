# Handoff — Ronda 2

> Continúa [`handoff-forge.md`](handoff-forge.md). **La frontera de archivos de su §5 sigue vigente
> y es lo primero que hay que releer.** Lo de abajo asume T1–T4 hechas.

---

## Verificado de la ronda 1

No es un «bien hecho» de cortesía, es una comprobación que hice yo:

- `WorkersAIProvider` **funciona contra la API real**, no solo con transportes mockeados. Devolvió
  `'VIVO'` con `input_tokens=11 output_tokens=135`.
- `config/modelos.yml` quedó intacto. Se respetó la frontera.

Y un dato que sale de esa misma llamada y conviene mirar: **135 tokens de salida para responder una
palabra**. El sobrecoste de razonamiento no es solo del modelo grande; también está en
`glm-4.7-flash`, que es el del chat. Eso hace la T7 más urgente de lo que parece.

---

## T5 — Línea base del banco con el agente actual · **la más valiosa, hazla primero**

De los tres umbrales que bloquean el veredicto de la fase 0, `bench_success_rate` es el único que
puedes desbloquear tú, y es **el que más manda** de los tres.

`packages/forge-probe/bench/` tiene 30 tareas reales sobre Edecán y Acme, con criterio
ejecutable, y está verificado que **las 30 fallan hoy**. Trátalo como **solo lectura**: es mío.

Corre esas tareas contra el bucle de agente que YA existe (`edecan_core.agent`) usando el proveedor
nuevo, y publica la tasa de éxito.

Por qué importa tanto: hoy nadie sabe si Edecán resuelve el 10 % o el 70 % de un trabajo real. Sin
ese número, cualquier afirmación sobre autonomía es una opinión. Y cuando el bucle propio de Forge
exista, **este es el número que tiene que batir** — sin él, no habrá forma de saber si mejoró.

Empieza por las 5 `trivial` y las 8 `standard` de Edecán; las `guarded` tocan migraciones y las de
Acme son de otro repo. Si una tarea no se puede correr con el agente actual, **dilo**; un hueco
declarado vale, un hueco escondido no.

> **Criterio:** un `bench_success_rate` publicado con su `n`, el desglose por clase, el coste real en
> dólares por tarea, y la lista de tareas que no se pudieron intentar y por qué.
> Ojo al presupuesto: pon un tope y respétalo.

---

## T6 — Implementar la política de razonamiento

Está escrita en `config/modelos.yml` y **no la implementa nadie**. Tres partes, y la segunda es una
fuga de seguridad abierta:

1. **Al CAS por referencia, nunca en línea.** El `reasoning_content` es el 82 % de la salida medida.
   Guardarlo dentro del evento hace el journal ilegible e incompactable. Va como blob, y el evento
   lleva la referencia.
2. **La redacción de secretos DEBE cubrir el razonamiento.** Es justo donde el modelo repite en claro
   la clave que acaba de leer en un archivo, mientras `content` sale limpio. Un filtro que solo mira
   `content` deja la puerta abierta. Esto no es teórico: es el camino más corto a filtrar un token.
3. **Nunca vuelve al modelo.** El razonamiento de un turno no entra en el historial del siguiente.
   Es del turno, no de la conversación: reenviarlo cuesta entrada y empeora la salida.

> **Criterio:** un test que mete una clave con formato reconocible en el contexto, provoca que el
> modelo la repita en su razonamiento, y comprueba que **no aparece** ni en el journal, ni en los
> logs, ni en lo que ve el usuario. Más un test de que el historial del turno N+1 no contiene el
> razonamiento del turno N.

---

## T7 — Contabilidad de coste real por turno

Ya capturas `cached_tokens` y `neurons`. Falta persistirlos y agregarlos: coste por turno, por
sesión y por tarea, con el precio leído de la API de Cloudflare y **no codificado a mano**.

Sin esto los presupuestos de `config/modelos.yml` son decoración: no hay forma de cortar lo que no
se mide, y `presupuesto_usd_por_turno: 0.01` no significa nada.

Y hay un número concreto que quiero ver, porque decide si el diseño del contexto va bien o mal: **el
porcentaje de entrada servido desde caché**. Con la entrada cacheada a 5× menos, ese porcentaje es
la métrica que dice si la T4 está funcionando de verdad o solo pasando su test.

> **Criterio:** `coste_usd`, `tokens_cacheados` y `ratio_cache` visibles por turno, y un test que
> comprueba que el turno 2 de una conversación tiene `ratio_cache > 0`.

---

## T8 — Matar el alias de tres valores

`packages/agents/profiles.py` y `edecan_llm/router.py` traen de antes una selección por alias
(`principal` / `rapido` / `profundo`). Ahora existe `config/modelos.yml`.

**Dos sistemas de enrutado conviviendo divergen en semanas.** Uno tiene que morir, y es el viejo:
el alias de tres valores es acoplamiento a la topología de un proveedor y no expresa lo que la tarea
necesita.

Relacionado, y quiero una respuesta explícita: creaste `task_router.py` **y** modificaste
`router.py`. ¿Hay un solo camino de decisión o hay dos? Si hay dos, unifícalos ahora, que es barato.

> **Criterio:** `grep -rn "principal\|profundo" packages/ --include="*.py"` no devuelve ninguna
> selección de modelo, y existe un solo módulo que decide qué modelo se usa.

---

## T9 — Hacer imposible el acoplamiento a Cloudflare

La regla de portabilidad dice que el sistema se desarrolla **contra el proveedor más débil
disponible**. Hoy no la hace cumplir nada, y el crédito de 50.000 $ es el mejor incentivo que existe
para acoplarse sin darse cuenta — no por una decisión, sino por cincuenta pequeñas.

Ollama está instalado en esta máquina. Monta un modo de CI que corra la suite contra él: sin caché
de prefijo, sin tool-calling nativo fiable, con ventana pequeña.

Lo que falle ahí es exactamente una dependencia oculta de Workers AI.

> **Criterio:** `FORGE_PROVEEDOR=ollama uv run pytest packages/llm packages/core -q` en verde. Ese
> comando es la métrica de «tiempo de cambio de proveedor» que promete la arquitectura, convertida
> en algo que se ejecuta.

---

## Recordatorios que ya costaron tiempo

- **`uv sync --all-packages`**, nunca `uv sync` a secas: deja el entorno con un solo paquete y se
  lleva por delante `edecan_llm`.
- **No inventes mediciones.** Lo que no se midió es `None`, no un valor razonable.
- **Antes de concluir que un modelo falla, mira la traza cruda.** Un caso de prueba que el modelo no
  puede acertar mide un defecto del caso. Ya pasó: 14 «fallos» que eran un punto final ambiguo en el
  prompt.
- **Los tests no tocan la red.** Hay dinero real detrás de ese token.
