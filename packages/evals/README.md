# packages/evals — `edecan_evals`

Suites de evaluación del agente: calidad y relevancia de respuestas, uso correcto de herramientas (`edecan_toolkit`, `edecan_core.Tool`), respeto de la persona configurada (`PersonaConfig`) y regresiones de memoria (`MemoryStore`).

Pensado para correr offline contra proveedores LLM stub/fake (o, opcionalmente, contra un proveedor real de forma manual y aislada) — nunca como parte del pipeline de tests deterministas de `make test`, que debe permanecer sin red real.

> **Dos cosas distintas conviven en este paquete**: (1) `packages/evals/tests/`
> — los tests de `edecan_evals` en sí (offline, deterministas, sí corren en
> `make test`, como cualquier otro paquete); y (2) `packages/evals/suites/`
> — las suites de evaluación del AGENTE que este paquete sabe ejecutar
> (offline por defecto; opcionalmente `--live`, manual, nunca desde `make test`).

## Estructura

```
packages/evals/
├── edecan_evals/
│   ├── schema.py   # Suite / Caso / Esperado / GuionEntry (Pydantic, sin deps hermanas)
│   ├── loader.py   # YAML -> Suite
│   ├── fakes.py    # FakeLLMProvider (doble determinista de edecan_llm.LLMProvider)
│   ├── judge.py    # rúbrica de juez LLM (alias "rapido"), SOLO --live
│   ├── runner.py   # orquestación + evaluación + CLI (único punto que toca edecan_core)
│   └── run.py       # `python -m edecan_evals.run`
├── suites/          # *.yaml — las suites reales (ver abajo)
├── artifacts/        # JSON de cada corrida (gitignored, ver artifacts/.gitignore)
└── tests/            # tests de ESTE paquete (offline, deterministas)
```

## Suites incluidas

| Suite | Qué valida |
|---|---|
| `tool_choice` | El agente elige la herramienta correcta para 8 solicitudes representativas del toolkit. |
| `persona_consistencia` | Formalidad (tú/usted) y uso de emojis según `PersonaConfig`. |
| `memoria` | Casos multi-turno: un hecho dicho en un turno se puede recuperar en el siguiente. |
| `seguridad_prompt_injection` | El agente rechaza instrucciones inyectadas en un documento/correo y no filtra secretos. |
| `sin_linkedin` | El agente rechaza cualquier pedido relacionado con LinkedIn y sugiere alternativas soportadas. |

## Cómo correr las suites

```bash
# Una suite, offline (FakeLLMProvider — sin red, gratis, determinista):
uv run python -m edecan_evals.run --suite tool_choice

# Todas las suites:
uv run python -m edecan_evals.run --suite todas

# Modo --live: usa el proveedor LLM real (ANTHROPIC_API_KEY u OPENAI_COMPAT_*
# del entorno). CONSUME TOKENS REALES — las herramientas siguen siendo dobles
# fake (ninguna acción real se ejecuta jamás desde este paquete), pero cada
# caso hace al menos una llamada real al modelo. Úsalo manualmente, nunca
# desde `make test` ni CI automático.
uv run python -m edecan_evals.run --suite persona_consistencia --live
```

Cada corrida imprime una tabla resumen por stdout y escribe un JSON con el detalle en `packages/evals/artifacts/` (no versionado).

## Imports diferidos (`edecan_core`)

`edecan_evals.runner` es el único módulo de este paquete cuyo camino de producción importa `edecan_core` (`Agent`, `ToolRegistry`, `Tool`, `ToolResult`) — y lo hace de forma diferida (dentro de una función, no a nivel de módulo). El resto del paquete (`schema`, `loader`, `fakes`, `judge`, y toda la evaluación pura de `runner`) no depende de ningún paquete hermano. Ver el docstring de `edecan_evals/runner.py` para el detalle de por qué y cómo se prueba esa ruta sin importar `edecan_core` en los tests (mismo patrón que `FakeTokenBundle` en `packages/connectors/tests/conftest.py`).

## Biblioteca de prompts

Las suites de este paquete están alineadas con `prompts/` (raíz del repo, ver su `README.md`): `persona_consistencia.yaml`/`seguridad_prompt_injection.yaml`/`sin_linkedin.yaml` validan el comportamiento que `prompts/persona_v2.md` describe (versión vigente; `persona_v1.md` queda como versión anterior inmutable), y `judge.py` embebe la rúbrica de `prompts/juez_v1.md`. Si iteras un prompt, corre las suites relevantes antes de portar el cambio al código de producción.
