package cc.edecan.shared

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

/**
 * Modelos de `/v1/automations` (`ROADMAP_V2.md` §7.4, §7.6, §7.10;
 * `apps/api/edecan_api/routers/automations.py::_public_automation`) —
 * pestaña "Automatizaciones" (WP-V5-07). Mismo criterio de defaults
 * tolerantes que [MissionsModels.kt]/`Models.kt`.
 */

/** `edecan_schemas.plans.FLAG_AUTOMATIONS_RULES` — gate de plan de TODO
 * `/v1/automations` (`automations.py::_require_automations_flag`). */
const val FLAG_AUTOMATIONS_RULES = "automations.rules"

/**
 * `Automation.trigger` — las dos variantes de `TriggerDef`
 * (`edecan_schemas.automations`, unión discriminada por `kind` del lado del
 * backend) aplanadas en un solo data class tolerante en vez de un sellado:
 * `rrule` solo viene poblado si `kind == "schedule"`; `hasSecret`/`hookUrl`
 * solo si `kind == "webhook"` — **nunca** `hook_secret` en claro aquí (ver
 * el docstring de `_public_automation`: se redacta a `has_secret`/`hook_url`
 * en todo `GET`). Este cliente móvil (WP-V5-07) solo ofrece crear
 * automatizaciones `kind == "schedule"` (ver `AutomatizacionesScreen`,
 * "alta simple"), pero el modelo decodifica ambas formas igual — la lista
 * puede traer automatizaciones tipo webhook creadas desde el panel web.
 */
@Serializable
data class AutomationTrigger(
    val kind: String = "schedule",
    val rrule: String? = null,
    @SerialName("has_secret") val hasSecret: Boolean = false,
    @SerialName("hook_url") val hookUrl: String? = null,
)

/** `Automation.accion` — única variante pinned hoy
 * (`AgentInstructionAccion`, `edecan_schemas.automations`). */
@Serializable
data class AutomationAccion(
    val kind: String = "agent_instruction",
    val instruccion: String = "",
    val agente: String? = null,
)

/**
 * Fila pública de `automations` (`GET/POST/PATCH /v1/automations*`).
 */
@Serializable
data class Automation(
    val id: String,
    val nombre: String = "",
    val descripcion: String = "",
    val trigger: AutomationTrigger = AutomationTrigger(),
    val accion: AutomationAccion = AutomationAccion(),
    val enabled: Boolean = true,
    @SerialName("next_run_at") val nextRunAt: String? = null,
    @SerialName("last_run_at") val lastRunAt: String? = null,
    @SerialName("created_at") val createdAt: String = "",
    @SerialName("updated_at") val updatedAt: String = "",
    /** Solo viene poblado en la respuesta puntual del `POST`/`PATCH` que
     * ACABA de generarlo (ver docstring de `_public_automation`) — este
     * cliente no crea automatizaciones tipo webhook (ver
     * `AutomatizacionesScreen`), así que en la práctica siempre llega
     * `null` aquí, pero se modela igual por completitud del contrato. */
    @SerialName("hook_secret") val hookSecret: String? = null,
)

/** Una fila de `GET /v1/automations/{id}/runs`. `detalle` queda como
 * [JsonElement] crudo — su forma depende de qué corrió esa automatización,
 * no está pinned (`automation_runs.detalle`, `ROADMAP_V2.md` §7.4). */
@Serializable
data class AutomationRun(
    val id: String,
    val status: String = "running",
    val detalle: JsonElement? = null,
    @SerialName("started_at") val startedAt: String? = null,
    @SerialName("finished_at") val finishedAt: String? = null,
)
