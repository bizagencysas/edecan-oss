# Automatizaciones

Reglas **disparador → acción**: un disparador (agenda o webhook entrante) dispara una instrucción que el agente corre solo, sin que nadie esté mirando ("modo headless"). `ROADMAP_V2.md` §4 WP-V2-07; `ARCHITECTURE.md` §10.7/§10.11/§10.12.

## Modelo de datos

Una automatización (`automations`, `ROADMAP_V2.md` §7.4) tiene:

- `trigger` (jsonb) — unión discriminada por `kind` (`edecan_schemas.automations.TriggerDef`):
  - `{"kind": "schedule", "rrule": "FREQ=DAILY;BYHOUR=9"}` — RFC 5545, interpretada con `python-dateutil` (`edecan_automations.engine.compute_next_run`). El backend recalcula `next_run_at` cada vez que se crea o se edita el `trigger`.
  - `{"kind": "webhook", "hook_secret": "..."}` — disparo entrante en `POST /v1/hooks/{id}`. El secreto lo genera el servidor (`secrets.token_urlsafe(24)`) al crear la automatización o al cambiar su `trigger` a `webhook`; **nunca** lo propone el cliente. `next_run_at` queda siempre `NULL` para este tipo — el barrido de agenda (`automation_scan`, ver abajo) lo excluye automáticamente.
- `accion` (jsonb) — hoy solo existe la variante `{"kind": "agent_instruction", "instruccion": "...", "agente": null}` (`edecan_schemas.automations.AccionDef`): corre `instruccion` con el agente en modo headless.
- `enabled` — si está en `false`, ni el barrido de agenda ni `run_automation` la ejecutan (con una excepción: `POST /{id}/probar` sí corre incluso si está desactivada, a propósito — sirve para probar un borrador antes de activarlo, y no toca `enabled`/`next_run_at`).
- `next_run_at`/`last_run_at` — bookkeeping de agenda; `automation_runs` (`ROADMAP_V2.md` §7.4) guarda cada corrida (`status`, `detalle` jsonb, `started_at`/`finished_at`).

## Disparo por agenda

`edecan_worker.handlers.automation_scan` (job de sistema, sin tenant propio — como `send_reminder_scan`) barre **todos los tenants**: cada 60s en dev (`edecan_worker.scheduler`, cadencia separada de los jobs de 30s: ver el docstring de ese módulo, sección "Dos cadencias, un solo loop"), y cada minuto en producción vía **EventBridge Scheduler** (`aws_scheduler_schedule.automation_scan`, `infra/terraform/modules/scheduler/`, `ARCHITECTURE.md` §7). Busca `automations` con `enabled=true` y `next_run_at` vencido, **adelanta `next_run_at` a la próxima ocurrencia ANTES de encolar** `run_automation` (evita doble disparo si el barrido se solapa con la corrida anterior o el worker se cae a mitad de camino), y encola un job por cada una con el `tenant_id` correspondiente.

## Disparo por webhook

`POST /v1/hooks/{automation_id}` (público, **sin** `Authorization: Bearer`) — la autenticación es el header `X-Hook-Secret`, comparado en tiempo constante (`hmac.compare_digest`) contra `trigger.hook_secret`. Reglas:

- **Todo fallo responde 404** (automatización inexistente, secreto incorrecto, `trigger.kind` distinto de `webhook`, o `enabled=false`) — nunca 401/403, para no confirmarle a quien no tiene el secreto correcto que el `automation_id` existe siquiera.
- **Rate limit**: máx. 30 llamadas/minuto por automatización (Redis, `INCR`+`EXPIRE`, ventana fija) — pasado ese límite, `429`.
- Cada disparo exitoso deja una fila en `audit_log` (`action="automation.hook_triggered"`) antes de encolar `run_automation`.
- El secreto y la URL completa (`{PUBLIC_BASE_URL}/v1/hooks/{id}`) solo viajan en el cuerpo de la respuesta del `POST`/`PATCH` de `/v1/automations` que los generó — cualquier lectura posterior (`GET`) los redacta a `{"kind": "webhook", "has_secret": true, "hook_url": "..."}`.

## Ejecución headless (`run_automation`)

`edecan_worker.handlers.run_automation` carga la automatización (filtrando por `tenant_id`), verifica `enabled` y que el plan del tenant siga trayendo el flag `automations.rules` (protege contra un downgrade de plan entre el encolado y la corrida), y delega en `edecan_automations.runner.run_automation`, que:

1. Arma un `Agent` (`edecan_core`) con un `ToolRegistry` **filtrado**: nunca ofrece tools `dangerous`, ni `delegar_mision` ni `gestionar_automatizacion` por nombre (doble barrera contra que una automatización dispare más trabajo autónomo en cadena — ver el docstring de `edecan_automations.runner` para el razonamiento completo).
2. Corre UN turno con `accion.instruccion` como `user_text` y `PersonaConfig` del usuario dueño de la automatización — **nunca** hay un humano confirmando en vivo, así que `approved_tool_calls` queda siempre vacío, sin importar lo que el caller haya dejado en `ctx.extras`.
3. Si el modelo pide algo fuera de ese conjunto seguro de todas formas (no debería, porque no se le ofrece — pero un proveedor LLM no está *forzado* a respetar la lista de `tools`), el gate normal de `Agent.run_turn` lo detiene: el run queda `status="waiting_confirmation"` con `detalle.pendiente` (nombre/argumentos de la tool) y **no ejecuta nada**. Ese run no se reanuda solo — hace falta revisión humana y, si corresponde, correr la automatización de nuevo.
4. Si el turno termina en texto, el run queda `status="done"` con `detalle.resultado`. Si algo falla, `status="error"` con `detalle.error`.

**Salud/legal/finanzas quedan fuera del alcance de este paquete**: si la instrucción de una automatización usa una tool de esos dominios, los disclaimers obligatorios los pone esa tool (no `edecan_automations`).

## Gestión desde el chat

`gestionar_automatizacion` (`edecan_automations.tools`, flag `automations.rules`) permite crear (solo por agenda — nunca genera un webhook desde el chat), listar, activar y desactivar automatizaciones. Es `dangerous=True` para las **cuatro** acciones (no solo crear/activar): `Tool.dangerous` es un atributo fijo por herramienta, no por argumento — el costo es que `listar`/`desactivar` también piden confirmación humana de más, a cambio de la garantía de que `crear`/`activar` jamás corren sin que alguien las apruebe.

## Límites y plan

- Flag `automations.rules` (§10.13/§7.2) gatea el router `/v1/automations`, el webhook (indirectamente: sin una fila creada no hay nada que disparar) y la tool.
- Límite `limits.automations_active` (`-1` = ilimitado) cuenta automatizaciones con `enabled=true` — se revisa al crear una con `enabled=true` y al activar una que estaba desactivada (`PATCH {enabled: true}` o la tool `activar`), nunca al editar otros campos ni al desactivar.

## Referencia de endpoints (`/v1/automations`, Bearer + flag `automations.rules`)

| Método | Ruta | Qué hace |
|---|---|---|
| `POST` | `/v1/automations` | Crea (`{nombre, descripcion?, trigger, accion, enabled?}`); valida con `edecan_automations.engine` antes de guardar. Si `trigger.kind="webhook"`, la respuesta trae `hook_secret` (una sola vez). |
| `GET` | `/v1/automations` | Lista las del tenant. |
| `GET` | `/v1/automations/{id}` | Detalle de una. |
| `PATCH` | `/v1/automations/{id}` | Edita `{enabled?, nombre?, descripcion?, trigger?, accion?}` (parcial). Cambiar `trigger` recalcula `next_run_at`. |
| `DELETE` | `/v1/automations/{id}` | Borra. |
| `POST` | `/v1/automations/{id}/probar` | Encola `run_automation` ya mismo (funciona incluso desactivada). |
| `GET` | `/v1/automations/{id}/runs` | Feed de corridas (`status`, `detalle`, `started_at`, `finished_at`). |
| `POST` | `/v1/hooks/{id}` | Público — dispara vía webhook (header `X-Hook-Secret`). Ver arriba. |

## NOTA — disparo por agenda en producción

`infra/terraform/modules/scheduler/` define una regla EventBridge Scheduler por tipo de barrido (`aws_scheduler_schedule.reminder_scan` y `aws_scheduler_schedule.automation_scan`, mismo rol IAM, `ARCHITECTURE.md` §7): las automatizaciones con trigger `schedule` ya no dependen únicamente de `edecan_worker.scheduler` (dev/self-host) ni de disparar `POST /{id}/probar` a mano para correr en un despliegue real — igual que el resto de la infraestructura de este repo, la regla está **escrita** en Terraform pero su aplicación a una cuenta AWS real es un paso operativo aparte (`infra/terraform/README.md`). Los webhooks (`POST /v1/hooks/{id}`) no dependen de ningún scheduler externo — funcionan en cualquier entorno donde la API esté corriendo.
