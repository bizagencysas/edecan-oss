# ADR-0002 — Edecán AI Workforce OS (Grok Bot++ / "Build your team")

Estado: **adoptado (en ejecución por waves)**
Fecha: 2026-08-25
Fuente: `product design` (233 apartados) + auditoría de arquitectura.

## Contexto

El objetivo es convertir Edecán en un "AI Workforce Operating System / Personal AI
Staff": entidades de agente persistentes y de primer nivel (perfil, avatar, rol,
permisos, memoria, routines), chats de equipo, handoffs, approvals durables,
computer/takeover, routing de modelos y voz como control plane. No se trata de
"agregar bots" ni de clonar Grok Bot.

Regla fundamental (directiva §11): **no destruir la arquitectura que funciona**.
Preservar `Web UI → routers/ide.py → IDE_ACTIONS bridge → SessionManager →
MotorOpencode → opencode serve → Workers AI`, y el principio
`LLM proposes/intends → harness validates → tool executes → result verified → UI reflects reality`.

## Hallazgos de la auditoría (qué existe)

Tres subsistemas de "agente" desconectados, sin registro común:

1. **Misiones** (`edecan_agents`): `agent_missions` + `agent_steps` + `Orchestrator`
   + `DelegarMisionTool` + `run_mission.py`. **Working**: state machine durable
   (planning/running/waiting_confirmation/done/error), waves con dependencias,
   replan acotado, confirm/resume persistido, síntesis final.
2. **Workers persistentes** (`persistent_agents`): identidad `name`+`purpose`+`status`
   + `tools`+`schedule`+`budget` (JSONB) + `persistent_agent_handoffs`. **Partial**:
   `permissions` nunca se aplica en runtime, `memory` siempre `{}`, `schedule` sin
   superficie UI, handoffs sin productor end-to-end.
3. **Sub-agentes IDE** (`ide_equipo`/`ide_reparto`/`ide_worktrees`): paralelismo de
   edición de código, efímero, sin identidad persistente. **Working** pero no es el
   "workforce".

Infraestructura reutilizable ya **working**:
- **Memoria**: `memory_items` + pgvector + consolidación + perfil vivo (plana, sin namespaces).
- **Routines/automations**: `automations` + `automation_runs` + scheduler rrule/webhook + `run_automation` (lease/lock parcial, sin $).
- **Connectors**: OAuth Google/MS/LinkedIn/X/Slack/YouTube/Meta + vault AES-GCM.
- **MCP**: cliente stdio/HTTP + SSRF + por-tenant (sin health ni scope por agente).
- **Computer/browser**: `usar_computadora` (companion-gated) + browser GET-only (sin aislamiento ni takeover).
- **Voz**: STT/TTS + realtime WS + teléfono + clonación (sin orquestación multi-agente).
- **Approvals**: chat = **efímero** (Redis 900s); misiones = **durable**. Mixto.
- **Usage**: `usage_events` (tokens, sin $).

## Decisiones de arquitectura

1. **Un solo agente de primer nivel.** Extender `persistent_agents` (NO duplicar una
   tabla `agents`): se convierte en la entidad persistente con perfil rico (avatar,
   rol, job spec, personalidad, instrucciones, approval policy, autonomía, model
   policy). El runtime de ejecución reutiliza el motor de **misiones** existente:
   cada tarea del agente se materializa como una misión, de modo que hereda waves,
   replan, confirm/resume, checkpoints y síntesis ya probados.

2. **Tareas durables como misión.** No crear un tercer motor de tareas. `tasks` =
   vista de más alto nivel sobre `agent_missions` + `agent_steps`, con `owner_agent_id`
   para enlazar tarea → agente. Los estados de la directiva (§43) se mapean a los
   estados de misión existentes (WAITING_APPROVAL ↔ waiting_confirmation, etc.).

3. **Approvals durables.** Migrar el approval de chat peligroso (hoy Redis 900s) a una
   tabla `pending_approvals` durable con resume idempotente. Las misiones ya lo hacen;
   unificar.

4. **Memoria con namespace.** Añadir columna `namespace` (conversation/agent/user/
   workspace/organization) y `source_trust` a `memory_items`, sin romper el modelo
   plano actual (default namespace="user"). El `memory` JSONB del agente se vuelve
   un espejo/cache del namespace del agente.

5. **Handoffs reales.** Convertir `persistent_agent_handoffs` en el bus de delegación:
   `DelegarMisionTool` (y un nuevo path de coordinación) produce handoffs; el destino
   los ejecuta como misiones. Estado visible en UI.

6. **Routines = `automations` existente.** Sin un segundo scheduler. Añadir
   lease/lock unificado + idempotencia por tool-call + dead-letter a nivel de run.

7. **No se toca el IDE.** `usar_computadora`/IDE sigue como está; el "Computer viewer"
   y el aislamiento por agente se añaden encima (waves F).

8. **Routing de modelos ya existe** (`edecan_llm.task_router` + `capability_routing`);
   se expone política por agente (`model_policy` JSONB) en vez de hardcodear.

## Roadmap (waves, adaptadas a lo encontrado)

- **Wave A (ahora):** data model de identidad de agente + enlace tarea↔agente.
- **Wave B:** perfiles + UI moderna (roster persistente, avatares, home, sidebar).
- **Wave C:** permisos de tools + approval engine durable.
- **Wave D:** tareas durables + routines (lease/lock/idempotencia).
- **Wave E:** handoffs + team chats + protocolo agent-to-agent.
- **Wave F:** computer/browser + takeover + aislamiento por agente.
- **Wave G:** memoria con namespace + skills.
- **Wave H:** Teach a Task.
- **Wave I:** voz como orquestación.
- **Wave J:** proactivo + passive task mining.

## Definición de done (resumen §233)

Edecán se comporta como un workforce digital persistente: creo un teammate, le doy
trabajo, recuerda cómo trabajo, usa las tools que autorizo, navega/usa computadora,
puedo verlo y tomar control, genera artifacts, delega a otro agente, varios coordinan
en un chat compartido, tareas largas sobreviven desconexiones, lo programado corre,
los approvals sobreviven recargas, el trabajo fallido no desaparece, los secretos y
permisos están técnicamente aplicados, y el routing no gasta inference flagship en
trabajo determinístico. Todo se siente simple.