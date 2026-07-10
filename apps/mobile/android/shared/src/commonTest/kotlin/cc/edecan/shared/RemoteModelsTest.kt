package cc.edecan.shared

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * Tests de serialización de `RemoteModels.kt` contra ejemplos reales de
 * `apps/api/edecan_api/routers/remote.py`/`edecan_schemas.devices.RemoteSessionOut`
 * (WP-V6-09) — mismo criterio que `MissionsModelsTest.kt`: fija la FORMA
 * exacta del JSON, no solo que "decodifique algo". Los fixtures de sesión
 * usan las mismas columnas EXACTAS que `SqlRepo._REMOTE_SESSION_COLUMNS`
 * (`apps/api/edecan_api/repo.py`); los de frame/input, el mismo shape que
 * `CANNED_FRAME_OK`/los asserts de `apps/api/tests/test_remote_router.py`.
 */
class RemoteModelsTest {

    // -------------------------------------------------------------------
    // RemoteSession
    // -------------------------------------------------------------------

    @Test
    fun remoteSession_decodifica_una_fila_pending_recien_creada() {
        val json = """
            {"id":"s1","tenant_id":"t1","user_id":"u1","device_id":null,
             "kind":"view","status":"pending","started_at":null,"ended_at":null,
             "frames_count":0,"created_at":"2026-07-01T10:00:00Z","updated_at":"2026-07-01T10:00:00Z"}
        """.trimIndent()
        val sesion = edecanJson.decodeFromString(RemoteSession.serializer(), json)
        assertEquals("s1", sesion.id)
        assertEquals(REMOTE_KIND_VIEW, sesion.kind)
        assertEquals(REMOTE_STATUS_PENDING, sesion.status)
        assertNull(sesion.deviceId)
        assertNull(sesion.startedAt)
        assertEquals(0, sesion.framesCount)
        assertFalse(sesion.isControl)
        assertFalse(sesion.haTerminado)
    }

    @Test
    fun remoteSession_decodifica_una_sesion_control_activa() {
        // `kind="control"` promovido por `POST /v1/remote/sessions` (WP-V4-10,
        // `Repo.mark_remote_session_kind`) — mismas columnas EXACTAS que
        // `SqlRepo._REMOTE_SESSION_COLUMNS`.
        val json = """
            {"id":"s2","tenant_id":"t1","user_id":"u1","device_id":null,
             "kind":"control","status":"active","started_at":"2026-07-01T10:00:05Z","ended_at":null,
             "frames_count":3,"created_at":"2026-07-01T10:00:00Z","updated_at":"2026-07-01T10:00:05Z"}
        """.trimIndent()
        val sesion = edecanJson.decodeFromString(RemoteSession.serializer(), json)
        assertTrue(sesion.isControl)
        assertFalse(sesion.haTerminado)
        assertEquals(3, sesion.framesCount)
        assertEquals("2026-07-01T10:00:05Z", sesion.startedAt)
    }

    @Test
    fun remoteSession_denied_y_ended_cuentan_como_haTerminado() {
        val denegada = edecanJson.decodeFromString(
            RemoteSession.serializer(),
            """{"id":"s3","tenant_id":"t1","user_id":"u1","kind":"view","status":"denied",
                "frames_count":0,"created_at":"2026-07-01T10:00:00Z","updated_at":"2026-07-01T10:00:00Z"}""",
        )
        assertTrue(denegada.haTerminado)

        val terminada = edecanJson.decodeFromString(
            RemoteSession.serializer(),
            """{"id":"s4","tenant_id":"t1","user_id":"u1","kind":"control","status":"ended",
                "started_at":"2026-07-01T10:00:00Z","ended_at":"2026-07-01T10:05:00Z",
                "frames_count":42,"created_at":"2026-07-01T10:00:00Z","updated_at":"2026-07-01T10:05:00Z"}""",
        )
        assertTrue(terminada.haTerminado)
        assertFalse(terminada.status == REMOTE_STATUS_ACTIVE)
    }

    // -------------------------------------------------------------------
    // RemoteFrame — GET /v1/remote/sessions/{id}/frame
    // -------------------------------------------------------------------

    @Test
    fun remoteFrame_decodifica_el_shape_real_del_router() {
        // Mismo `image_b64` que `CANNED_FRAME_OK` en `test_remote_router.py`.
        val json = """{"image_b64":"aGVsbG8=","width":1920,"height":1080,"seq":3}"""
        val frame = edecanJson.decodeFromString(RemoteFrame.serializer(), json)
        assertEquals("aGVsbG8=", frame.imageB64)
        assertEquals(1920, frame.width)
        assertEquals(1080, frame.height)
        assertEquals(3, frame.seq)
    }

    // -------------------------------------------------------------------
    // RemoteInputResult — POST /v1/remote/sessions/{id}/input
    // -------------------------------------------------------------------

    @Test
    fun remoteInputResult_decodifica_result_de_un_pointer() {
        val json = """{"ok":true,"result":{"x":100,"y":200,"accion":"click","button":"left"}}"""
        val resultado = edecanJson.decodeFromString(RemoteInputResult.serializer(), json)
        assertTrue(resultado.ok)
        val objeto = resultado.result as? JsonObject
        assertEquals("click", objeto?.get("accion")?.jsonPrimitive?.content)
    }

    @Test
    fun remoteInputResult_decodifica_result_nulo() {
        val json = """{"ok":true,"result":null}"""
        val resultado = edecanJson.decodeFromString(RemoteInputResult.serializer(), json)
        assertTrue(resultado.ok)
        assertNull(resultado.result)
    }

    // -------------------------------------------------------------------
    // Vocabulario pinned (edecan_api.routers.remote)
    // -------------------------------------------------------------------

    @Test
    fun vocabulario_de_pointer_accion_y_mouse_button_es_exacto() {
        assertEquals(setOf("move", "click", "double_click", "right_click"), REMOTE_POINTER_ACCIONES)
        assertEquals(setOf("left", "right", "middle"), REMOTE_MOUSE_BUTTONS)
    }

    @Test
    fun vocabulario_de_teclas_especiales_tiene_las_8_en_orden() {
        assertEquals(
            listOf("enter", "tab", "escape", "backspace", "arrow_up", "arrow_down", "arrow_left", "arrow_right"),
            REMOTE_SPECIAL_KEYS,
        )
    }

    // -------------------------------------------------------------------
    // remoteFramePollDelayMillis — función pura, sin coroutines/Android.
    // -------------------------------------------------------------------

    @Test
    fun pollDelay_con_el_default_real_del_backend_da_2000ms_como_el_panel_web() {
        // REMOTE_FRAME_MIN_INTERVAL_SECONDS default = 1.0 (config.py/.env.example);
        // AUTO_REFRESH_INTERVAL_MS del panel web = 2000 -- deben coincidir.
        assertEquals(2000L, remoteFramePollDelayMillis(DEFAULT_REMOTE_FRAME_MIN_INTERVAL_SECONDS))
        assertEquals(2000L, remoteFramePollDelayMillis()) // mismo default sin pasar nada.
    }

    @Test
    fun pollDelay_siempre_es_al_menos_el_doble_del_minimo_del_servidor() {
        assertEquals(4000L, remoteFramePollDelayMillis(2.0))
        assertEquals(1000L, remoteFramePollDelayMillis(0.5))
    }

    @Test
    fun pollDelay_nunca_baja_del_piso_ni_con_un_minimo_diminuto_o_invalido() {
        // 0.05s (REMOTE_INPUT_MIN_INTERVAL_SECONDS, no el de frames, pero
        // sirve para probar el piso) * 2 = 100ms, muy por debajo de un
        // polling razonable -- el piso evita una ráfaga cerrada.
        assertTrue(remoteFramePollDelayMillis(0.05) >= 500L)
        assertTrue(remoteFramePollDelayMillis(0.0) >= 500L)
        assertTrue(remoteFramePollDelayMillis(-1.0) >= 500L)
    }
}
