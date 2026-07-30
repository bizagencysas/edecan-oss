# prompts

Plantillas de **system prompt** versionadas, en español, usadas por `edecan_core.persona.build_system_prompt(persona, memories, extra_context)` (`ARCHITECTURE.md` §10.7) para construir el prompt final del agente a partir de:

- La `PersonaConfig` del tenant/usuario ("nivel Dios": nombre del asistente, tono, formalidad, instrucciones permanentes, rasgos).
- Las memorias recuperadas (`MemoryStore.search`).
- Contexto adicional del turno (por ejemplo, resultado de herramientas previas).

Las instrucciones que aporta el usuario dentro de su persona se insertan siempre en una sección delimitada y **nunca** tienen prioridad sobre las reglas de seguridad fijas del prompt base.

## Archivos en este directorio

- [`persona_v2.md`](./persona_v2.md) — copia canónica y comentada (versión vigente) de la plantilla de system prompt que arma `edecan_core.persona.build_system_prompt`, con changelog al pie. Versión anterior inmutable: [`persona_v1.md`](./persona_v1.md).
- [`consolidacion_memoria_v1.md`](./consolidacion_memoria_v1.md) — prompt del job `memory_consolidate` (`edecan_schemas.JOB_TYPES`, `ARCHITECTURE.md` §10.11): extrae `memory_items` (`fact`/`preference`/`event`/`entity`) de un fragmento de conversación.
- [`juez_v1.md`](./juez_v1.md) — rúbrica 1-5 del juez LLM de tono/persona que usa `packages/evals/edecan_evals/judge.py` (alias `"rapido"`), solo en modo `--live`.

## Versionado

Cada archivo es **inmutable una vez publicado**: iterar un prompt significa crear `persona_v2.md`, `consolidacion_memoria_v2.md` o `juez_v2.md` — **nunca** sobreescribir `_v1` (ni cualquier versión previa) en el mismo archivo. Esto permite:

- Comparar versiones lado a lado (diff de texto normal, sin depender de `git log` de un único archivo mutable).
- Correr `packages/evals` contra dos versiones y decidir con datos (aserciones deterministas de `Esperado` + juez `--live`) cuál promover.
- Hacer rollback instantáneo: apuntar de vuelta a la versión anterior sin reconstruir nada.

Cada archivo termina con una sección `## Changelog` que registra, por versión, qué cambió y por qué — se **añade** una entrada nueva al crear la siguiente versión; las entradas de versiones anteriores no se editan.

## `edecan_core` embebe su copia

`edecan_core.persona.build_system_prompt` (y, de forma análoga, el prompt de `memory_consolidate` en `apps/worker`/`edecan_core`, y `RUBRICA_JUEZ` en `edecan_evals/judge.py`) **no leen estos archivos en tiempo de ejecución**: cada uno tiene su propia copia embebida en código Python (f-strings/constantes), porque `packages/evals` y `edecan_core` son paquetes instalables independientes y no deben depender el uno del árbol de archivos del otro en producción.

Este directorio es entonces **la fuente para iterar y evaluar**, no el origen que el runtime carga:

1. Edita/crea la siguiente versión aquí (p. ej. `persona_v2.md`), con su changelog.
2. Corre las suites relevantes de `packages/evals/suites/` contra el cambio (offline con el guion fake para validar orquestación; `--live` + `judge.py` para calidad de tono/persona) antes de tocar código de producción.
3. Una vez validado, porta el texto a la copia embebida correspondiente (`edecan_core.persona`, el handler de `memory_consolidate`, o `edecan_evals.judge.RUBRICA_JUEZ`) en el mismo cambio que lo valida.

Cada copia embebida referencia en un comentario/docstring de qué archivo de `prompts/` es copia, para poder auditar que no divergieron (ver el encabezado de `edecan_evals/judge.py`).
