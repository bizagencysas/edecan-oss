# packages/automations — `edecan_automations`

Automatizaciones: reglas **disparador → acción** (`ROADMAP_V2.md` §4 WP-V2-07,
`ARCHITECTURE.md` §10.7/§10.11/§10.12). Un disparador es una agenda (`rrule`
RFC 5545) o un webhook entrante autenticado con un secreto por automatización;
la acción, hoy, siempre es "correr esta instrucción con el agente en modo
headless, con un toolset seguro (nada `dangerous`, nada de crear más
automatizaciones o misiones)".

## Módulos

- `engine.py` — validación pura, sin IO: `validate_trigger`/`validate_accion`
  (contra `edecan_schemas.automations.TriggerDef`/`AccionDef`) y
  `compute_next_run(rrule, after)` (`python-dateutil`, tz-aware UTC).
- `runner.py` — `run_automation(automation, deps)`: arma un `Agent` de
  `edecan_core` con un `ToolRegistry` filtrado (sin tools `dangerous`, sin
  `delegar_mision`/`gestionar_automatizacion`) y corre UN turno headless con
  `accion.instruccion`. Persiste el resultado vía callables inyectados en
  `deps` — este módulo no importa `edecan_db` ni abre sesiones.
- `tools.py` — `gestionar_automatizacion` (`Tool` de `edecan_core`, flag
  `automations.rules`, `dangerous=True`): crear/listar/activar/desactivar
  automatizaciones desde el propio chat.

## Quién usa qué

- `apps/api/edecan_api/routers/automations.py` (CRUD autenticado) y
  `routers/hooks.py` (webhook público) importan `engine` para validar antes
  de guardar en Postgres — hablan SQL parametrizado directo contra las
  tablas `automations`/`automation_runs` (`ROADMAP_V2.md` §7.4): ese esquema
  está pinned por nombre de tabla/columna, no por una clase ORM de
  `edecan_db.models` (mismo criterio que `edecan_api.repo`/`edecan_worker.repo`
  con las tablas v1 — ver el docstring de esos módulos).
- `apps/worker/edecan_worker/handlers/automation_scan.py` (barrido
  cross-tenant de agenda) y `handlers/run_automation.py` (corre una,
  filtrando por `tenant_id`) importan `engine`/`runner` de forma perezosa
  (`edecan_automations` es un paquete hermano que se construye en paralelo,
  ARCHITECTURE.md §10.1) y son quienes de verdad hablan con Postgres.

## Seguridad de un run headless

Un `automation_run` corre **sin usuario presente**: nadie puede aprobar una
tool `dangerous` en vivo. Por eso `runner.run_automation`:

1. Fuerza `ctx.extras["approved_tool_calls"] = set()` siempre — ninguna
   `dangerous` puede colarse como "ya aprobada" por un wiring accidental.
2. Además, ni siquiera OFRECE al modelo las tools `dangerous`, ni
   `delegar_mision` ni `gestionar_automatizacion` por nombre (doble barrera:
   la segunda evita que una automatización se dispare a sí misma en cadena,
   incluso si algún día esas dos tools dejaran de ser `dangerous`).

Si el modelo igual pide algo fuera de ese conjunto (no debería, porque no se
le ofrece — pero un proveedor LLM no está *forzado* a respetar `tools`), el
gate de `edecan_core.agent.Agent.run_turn` lo detiene con
`confirmation_required` de todas formas: el run queda `waiting_confirmation`
con `detalle.pendiente` y no ejecuta nada. Salud/legal/finanzas quedan fuera
del alcance de este paquete: solo orquesta el turno, cada `Tool` concreta es
responsable de sus propios disclaimers.

## Tests

Offline, deterministas, sin importar paquetes hermanos (`ARCHITECTURE.md`
§10.1): `tests/conftest.py` arma un `ToolContext` falso por duck-typing
(`SimpleNamespace`, mismo patrón que `packages/toolkit/tests/conftest.py`) y
`tests/test_runner.py` monkeypatchea `edecan_automations.runner.Agent` con un
agente-script en vez de importar el real.
