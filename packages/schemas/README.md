# packages/schemas — `edecan_schemas`

Contratos **Pydantic v2** compartidos por el resto del monorepo, sin más dependencias (§10.5 de `ARCHITECTURE.md`).

Incluye, entre otros:

- `PersonaConfig` — configuración "nivel Dios" del asistente (nombre, idioma, tono, formalidad, instrucciones, rasgos, memoria).
- `TokenBundle` — credenciales de un conector (access/refresh token, scopes, expiración).
- `JobEnvelope` y `JOB_TYPES` — los 7 tipos de job pinned que consume `apps/worker`.
- `AgentEvent`, `ToolSpec`, `ToolCallData` — eventos y contratos del loop de tool-use del agente.
- `edecan_schemas.plans` — `PlanDef` y `PLANES` (flags y límites por plan, ver `ARCHITECTURE.md` §10.13).

Layout esperado: `packages/schemas/edecan_schemas/` + `packages/schemas/tests/` (§10.1). Los tests de este paquete no importan paquetes hermanos.
