package cc.edecan.app.vm

import cc.edecan.shared.REMOTE_MOUSE_BUTTONS
import cc.edecan.shared.REMOTE_POINTER_ACCIONES
import cc.edecan.shared.REMOTE_SPECIAL_KEYS
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * Tests JVM puros de [TECLAS_ESPECIALES] — el vocabulario/etiquetas de la
 * barra de teclado de `RemotoScreen` (WP-V6-09), movidos a esta clase
 * precisamente para poder testearlos sin Compose/Android (mismo criterio que
 * `LlmKindTest.kt` con `LlmKind`). Fija que coincide EXACTO con el
 * vocabulario real de `edecan_api.routers.remote.SpecialKey`
 * (`shared/RemoteModels.kt::REMOTE_SPECIAL_KEYS`).
 */
class RemotoTeclasTest {

    @Test
    fun teclasEspeciales_valores_coinciden_exacto_con_REMOTE_SPECIAL_KEYS_en_el_mismo_orden() {
        assertEquals(REMOTE_SPECIAL_KEYS, TECLAS_ESPECIALES.map { it.valor })
    }

    @Test
    fun teclasEspeciales_son_exactamente_8_sin_duplicados() {
        assertEquals(8, TECLAS_ESPECIALES.size)
        assertEquals(TECLAS_ESPECIALES.size, TECLAS_ESPECIALES.map { it.valor }.toSet().size)
    }

    @Test
    fun cada_tecla_especial_tiene_etiqueta_y_titulo_no_vacios() {
        TECLAS_ESPECIALES.forEach { tecla ->
            assertTrue(tecla.etiqueta.isNotBlank(), "${tecla.valor} sin etiqueta")
            assertTrue(tecla.titulo.isNotBlank(), "${tecla.valor} sin título")
        }
    }

    @Test
    fun vocabulario_de_pointer_accion_y_mouse_button_sigue_siendo_el_de_remote_py() {
        // Sanity check adicional (no específico de la UI): estos dos sets se
        // usan directo en `RemotoViewModel.enviarPointer` — un cambio de
        // vocabulario en el backend debe hacer fallar ESTE test primero.
        assertEquals(setOf("move", "click", "double_click", "right_click"), REMOTE_POINTER_ACCIONES)
        assertEquals(setOf("left", "right", "middle"), REMOTE_MOUSE_BUTTONS)
    }
}
