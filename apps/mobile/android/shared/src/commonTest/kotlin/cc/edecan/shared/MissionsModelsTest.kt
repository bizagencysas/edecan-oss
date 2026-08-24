package cc.edecan.shared

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonPrimitive
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * Tests de serialización de `MissionsModels.kt` contra ejemplos reales de
 * `apps/api/edecan_api/routers/missions.py`/`edecan_schemas.missions`
 * (WP-V5-07) — mismo criterio que `ModelsTest.kt`: fija la FORMA exacta del
 * JSON, no solo que "decodifique algo".
 */
class MissionsModelsTest {

    @Test
    fun mission_decodifica_una_fila_real_de_v1_missions() {
        val json = """
            {"id":"m1","tenant_id":"t1","user_id":"u1",
             "objetivo":"Investiga a los 3 principales competidores",
             "status":"waiting_confirmation",
             "plan":[{"seq":1,"agente":"investigador","instruccion":"Buscar competidores"}],
             "resultado":null,"presupuesto":{"max_steps":8},"error":null,
             "created_at":"2026-07-01T10:00:00Z","updated_at":"2026-07-01T10:05:00Z"}
        """.trimIndent()
        val mission = edecanJson.decodeFromString(Mission.serializer(), json)
        assertEquals("m1", mission.id)
        assertEquals("waiting_confirmation", mission.status)
        assertNull(mission.resultado)
        assertNull(mission.error)
        val presupuesto = mission.presupuesto as? JsonObject
        assertEquals(8, presupuesto?.get("max_steps")?.jsonPrimitive?.int)
    }

    @Test
    fun missionDetail_decodifica_mission_mas_steps_incluyendo_pending_tool_call() {
        // El paso #2 trae `usage.pending_tool_call` (mismo shape que
        // `MissionStep.usage` en `apps/web/src/lib/api-misiones.ts`) — lo que
        // `ui/MisionesScreen.kt` lee a mano para la tarjeta de aprobación.
        val json = """
            {"mission":{"id":"m1","tenant_id":"t1","user_id":"u1","objetivo":"Enviar reporte",
              "status":"waiting_confirmation","plan":null,"resultado":null,"presupuesto":{"max_steps":8},
              "error":null,"created_at":"2026-07-01T10:00:00Z","updated_at":"2026-07-01T10:05:00Z"},
             "steps":[
               {"id":"s1","tenant_id":"t1","mission_id":"m1","seq":1,"agente":"investigador",
                "instruccion":"Buscar datos","status":"done","resultado":"Encontré 3 competidores.",
                "usage":{"input_tokens":100},"created_at":"2026-07-01T10:00:00Z","updated_at":"2026-07-01T10:01:00Z"},
               {"id":"s2","tenant_id":"t1","mission_id":"m1","seq":2,"agente":"redactor",
                "instruccion":"Enviar el reporte por correo","status":"waiting_confirmation","resultado":null,
                "usage":{"pending_tool_call":{"id":"call_1","name":"enviar_correo","args":{"para":"x@acme.test"}}},
                "created_at":"2026-07-01T10:01:00Z","updated_at":"2026-07-01T10:02:00Z"}
             ]}
        """.trimIndent()
        val detail = edecanJson.decodeFromString(MissionDetail.serializer(), json)
        assertEquals("waiting_confirmation", detail.mission.status)
        assertEquals(2, detail.steps.size)
        assertEquals("done", detail.steps[0].status)
        assertEquals("Encontré 3 competidores.", detail.steps[0].resultado)

        val pasoPendiente = detail.steps[1]
        assertEquals("waiting_confirmation", pasoPendiente.status)
        val pendingCall = (pasoPendiente.usage as? JsonObject)?.get("pending_tool_call") as? JsonObject
        assertEquals("enviar_correo", pendingCall?.get("name")?.jsonPrimitive?.content)
        val args = pendingCall?.get("args") as? JsonObject
        assertEquals("x@acme.test", (args?.get("para") as? JsonPrimitive)?.content)
    }

    @Test
    fun activeMissionStatuses_es_exactamente_planning_y_running() {
        assertEquals(setOf("planning", "running"), ACTIVE_MISSION_STATUSES)
        assertTrue("planning" in ACTIVE_MISSION_STATUSES)
        assertTrue("running" in ACTIVE_MISSION_STATUSES)
        assertFalse("waiting_confirmation" in ACTIVE_MISSION_STATUSES)
        assertFalse("done" in ACTIVE_MISSION_STATUSES)
    }
}
