package cc.edecan.shared

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * Tests de serialización de `AutomationsModels.kt` contra ejemplos reales de
 * `apps/api/edecan_api/routers/automations.py::_public_automation`
 * (WP-V5-07).
 */
class AutomationsModelsTest {

    @Test
    fun automation_decodifica_trigger_schedule_y_accion_agent_instruction() {
        val json = """
            {"id":"a1","nombre":"Reporte de ventas diario","descripcion":"",
             "trigger":{"kind":"schedule","rrule":"FREQ=DAILY;BYHOUR=9;BYMINUTE=0"},
             "accion":{"kind":"agent_instruction","instruccion":"Resume las ventas de hoy","agente":null},
             "enabled":true,"next_run_at":"2026-07-09T09:00:00Z","last_run_at":null,
             "created_at":"2026-07-01T10:00:00Z","updated_at":"2026-07-01T10:00:00Z"}
        """.trimIndent()
        val automation = edecanJson.decodeFromString(Automation.serializer(), json)
        assertEquals("schedule", automation.trigger.kind)
        assertEquals("FREQ=DAILY;BYHOUR=9;BYMINUTE=0", automation.trigger.rrule)
        assertEquals("Resume las ventas de hoy", automation.accion.instruccion)
        assertTrue(automation.enabled)
        assertNull(automation.hookSecret)
    }

    @Test
    fun automation_decodifica_trigger_webhook_redactado_sin_secreto_en_claro() {
        // `_public_automation` NUNCA expone `hook_secret` en un GET normal —
        // solo `has_secret`/`hook_url` (ver docstring del router).
        val json = """
            {"id":"a2","nombre":"Hook entrante","descripcion":"",
             "trigger":{"kind":"webhook","has_secret":true,"hook_url":"http://localhost:8000/v1/hooks/a2"},
             "accion":{"kind":"agent_instruction","instruccion":"Procesa el payload","agente":null},
             "enabled":true,"next_run_at":null,"last_run_at":null,
             "created_at":"2026-07-01T10:00:00Z","updated_at":"2026-07-01T10:00:00Z"}
        """.trimIndent()
        val automation = edecanJson.decodeFromString(Automation.serializer(), json)
        assertEquals("webhook", automation.trigger.kind)
        assertTrue(automation.trigger.hasSecret)
        assertEquals("http://localhost:8000/v1/hooks/a2", automation.trigger.hookUrl)
        assertNull(automation.trigger.rrule)
        assertNull(automation.hookSecret) // nunca en claro en un GET.
    }

    @Test
    fun automation_recien_creada_SI_trae_hook_secret_una_sola_vez() {
        val json = """
            {"id":"a3","nombre":"Hook nuevo","descripcion":"",
             "trigger":{"kind":"webhook","has_secret":true,"hook_url":"http://localhost:8000/v1/hooks/a3"},
             "accion":{"kind":"agent_instruction","instruccion":"Procesa el payload","agente":null},
             "enabled":true,"next_run_at":null,"last_run_at":null,
             "created_at":"2026-07-01T10:00:00Z","updated_at":"2026-07-01T10:00:00Z",
             "hook_secret":"abc123secreto"}
        """.trimIndent()
        val automation = edecanJson.decodeFromString(Automation.serializer(), json)
        assertEquals("abc123secreto", automation.hookSecret)
    }

    @Test
    fun automationRun_decodifica_status_y_detalle_como_jsonElement() {
        val json = """
            {"id":"r1","status":"done","detalle":{"resultado":"ok"},
             "started_at":"2026-07-01T09:00:00Z","finished_at":"2026-07-01T09:00:05Z"}
        """.trimIndent()
        val run = edecanJson.decodeFromString(AutomationRun.serializer(), json)
        assertEquals("done", run.status)
        assertEquals("2026-07-01T09:00:05Z", run.finishedAt)
        assertFalse(run.detalle == null)
    }
}
