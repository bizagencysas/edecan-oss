package cc.edecan.shared

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

/**
 * Tests de `RemoteCoords.kt` — mismos escenarios de letterbox que
 * `coords.ts` (panel web), con números redondos para que las aserciones no
 * dependan de precisión de punto flotante. `RemotoScreen` (`androidApp`)
 * confía en que esta lógica NUNCA manda coordenadas inventadas cuando el
 * toque cae fuera del contenido real de la imagen.
 */
class RemoteCoordsTest {

    // -------------------------------------------------------------------
    // remoteContainedImageRect
    // -------------------------------------------------------------------

    @Test
    fun containedRect_mismo_aspect_llena_el_elemento_completo_sin_letterbox() {
        val rect = remoteContainedImageRect(elementWidth = 200.0, elementHeight = 100.0, naturalWidth = 1920.0, naturalHeight = 960.0)
        assertEquals(RemoteContainedRect(0.0, 0.0, 200.0, 100.0), rect)
    }

    @Test
    fun containedRect_frame_mas_ancho_que_el_elemento_deja_franjas_arriba_y_abajo() {
        // Elemento cuadrado (aspect 1.0), frame 1:1 en este caso concreto no
        // aplica -- se prueba con un elemento MÁS angosto que el frame:
        // elemento 100x200 (aspect 0.5), frame 100x100 (aspect 1.0).
        val rect = remoteContainedImageRect(elementWidth = 100.0, elementHeight = 200.0, naturalWidth = 100.0, naturalHeight = 100.0)
        assertEquals(RemoteContainedRect(left = 0.0, top = 50.0, width = 100.0, height = 100.0), rect)
    }

    @Test
    fun containedRect_frame_mas_angosto_que_el_elemento_deja_franjas_a_los_lados() {
        // Elemento 200x100 (aspect 2.0), frame 100x100 (aspect 1.0).
        val rect = remoteContainedImageRect(elementWidth = 200.0, elementHeight = 100.0, naturalWidth = 100.0, naturalHeight = 100.0)
        assertEquals(RemoteContainedRect(left = 50.0, top = 0.0, width = 100.0, height = 100.0), rect)
    }

    @Test
    fun containedRect_con_tamano_degenerado_no_lanza_y_devuelve_el_elemento_tal_cual() {
        val rect = remoteContainedImageRect(elementWidth = 0.0, elementHeight = 100.0, naturalWidth = 100.0, naturalHeight = 100.0)
        assertEquals(RemoteContainedRect(0.0, 0.0, 0.0, 100.0), rect)
    }

    // -------------------------------------------------------------------
    // mapPointToRemoteCoords — letterbox arriba/abajo (elemento angosto)
    // -------------------------------------------------------------------

    @Test
    fun mapPoint_letterbox_vertical_toque_en_la_franja_vacia_arriba_es_null() {
        val punto = mapPointToRemoteCoords(
            pointX = 50.0, pointY = 0.0,
            elementWidth = 100.0, elementHeight = 200.0,
            frameWidth = 100, frameHeight = 100,
        )
        assertNull(punto)
    }

    @Test
    fun mapPoint_letterbox_vertical_toque_en_la_franja_vacia_abajo_es_null() {
        val punto = mapPointToRemoteCoords(
            pointX = 50.0, pointY = 199.0,
            elementWidth = 100.0, elementHeight = 200.0,
            frameWidth = 100, frameHeight = 100,
        )
        assertNull(punto)
    }

    @Test
    fun mapPoint_letterbox_vertical_centro_mapea_al_centro_real_del_frame() {
        val punto = mapPointToRemoteCoords(
            pointX = 50.0, pointY = 100.0,
            elementWidth = 100.0, elementHeight = 200.0,
            frameWidth = 100, frameHeight = 100,
        )
        assertEquals(RemotePoint(50, 50, nx = 0.5, ny = 0.5), punto)
    }

    @Test
    fun mapPoint_letterbox_vertical_borde_superior_del_contenido_mapea_a_y_cero() {
        val punto = mapPointToRemoteCoords(
            pointX = 50.0, pointY = 50.0, // justo donde empieza el contenido real (top=50)
            elementWidth = 100.0, elementHeight = 200.0,
            frameWidth = 100, frameHeight = 100,
        )
        assertEquals(0, punto?.y)
    }

    // -------------------------------------------------------------------
    // mapPointToRemoteCoords — letterbox a los lados (elemento ancho)
    // -------------------------------------------------------------------

    @Test
    fun mapPoint_letterbox_horizontal_toque_en_la_franja_vacia_izquierda_es_null() {
        val punto = mapPointToRemoteCoords(
            pointX = 0.0, pointY = 50.0,
            elementWidth = 200.0, elementHeight = 100.0,
            frameWidth = 100, frameHeight = 100,
        )
        assertNull(punto)
    }

    @Test
    fun mapPoint_letterbox_horizontal_toque_en_la_franja_vacia_derecha_es_null() {
        val punto = mapPointToRemoteCoords(
            pointX = 199.0, pointY = 50.0,
            elementWidth = 200.0, elementHeight = 100.0,
            frameWidth = 100, frameHeight = 100,
        )
        assertNull(punto)
    }

    @Test
    fun mapPoint_letterbox_horizontal_centro_mapea_al_centro_real_del_frame() {
        val punto = mapPointToRemoteCoords(
            pointX = 100.0, pointY = 50.0,
            elementWidth = 200.0, elementHeight = 100.0,
            frameWidth = 100, frameHeight = 100,
        )
        assertEquals(RemotePoint(50, 50, nx = 0.5, ny = 0.5), punto)
    }

    @Test
    fun mapPoint_borde_derecho_del_contenido_se_recorta_al_ultimo_pixel_valido_no_al_ancho_exacto() {
        // El borde derecho del contenido real cae en pointX=150 (left=50,
        // width=100) -> relX=100 = contained.width exacto -> x = 100, pero
        // el índice válido máximo es frameWidth-1 = 99 (mismo criterio que
        // `mapClientPointToRemoteCoords` en coords.ts).
        val punto = mapPointToRemoteCoords(
            pointX = 150.0, pointY = 50.0,
            elementWidth = 200.0, elementHeight = 100.0,
            frameWidth = 100, frameHeight = 100,
        )
        assertEquals(99, punto?.x)
    }

    // -------------------------------------------------------------------
    // Coordenada NORMALIZADA (`nx`/`ny`) — lo único que sobrevive a que el
    // frame viaje comprimido: el companion la multiplica por el tamaño REAL
    // de la pantalla remota (en puntos lógicos), no por `frameWidth`.
    // -------------------------------------------------------------------

    @Test
    fun mapPoint_devuelve_la_fraccion_normalizada_del_frame_comprimido() {
        // 1600x1034 = lo que de verdad recibe el teléfono cuando la Mac
        // captura 3456x2234 Retina y el backend pide `max_width: 1600`.
        val punto = mapPointToRemoteCoords(
            pointX = 200.0, pointY = 129.25,
            elementWidth = 400.0, elementHeight = 258.5,
            frameWidth = 1600, frameHeight = 1034,
        )
        assertEquals(0.5, punto?.nx)
        assertEquals(0.5, punto?.ny)
        assertEquals(800, punto?.x) // los píxeles del frame siguen ahí, sin cambios
    }

    @Test
    fun mapPoint_la_fraccion_llega_a_uno_en_la_esquina_aunque_el_pixel_se_recorte() {
        // El síntoma reportado: la franja derecha y el Dock eran inalcanzables.
        // `x` se recorta a `frameWidth - 1` (contrato viejo), pero `nx` sí
        // llega a 1.0 y el companion lo resuelve al último punto real.
        val punto = mapPointToRemoteCoords(
            pointX = 150.0, pointY = 100.0,
            elementWidth = 200.0, elementHeight = 100.0,
            frameWidth = 100, frameHeight = 100,
        )
        assertEquals(1.0, punto?.nx)
        assertEquals(1.0, punto?.ny)
        assertEquals(99, punto?.x)
    }

    @Test
    fun mapPoint_la_fraccion_ignora_el_origen_del_monitor_porque_es_relativa_al_frame() {
        // `originX/Y` son PUNTOS del escritorio global y solo aplican a x/y:
        // el companion ya suma el origen del display al resolver `nx`/`ny`.
        val punto = mapPointToRemoteCoords(
            pointX = 50.0, pointY = 50.0,
            elementWidth = 100.0, elementHeight = 100.0,
            frameWidth = 100, frameHeight = 100,
            originX = 2056, originY = -120,
        )
        assertEquals(2056 + 50, punto?.x)
        assertEquals(-120 + 50, punto?.y)
        assertEquals(0.5, punto?.nx)
        assertEquals(0.5, punto?.ny)
    }

    // -------------------------------------------------------------------
    // Guardas de tamaño inválido
    // -------------------------------------------------------------------

    @Test
    fun mapPoint_frame_sin_dimensiones_es_null_sin_lanzar() {
        assertNull(
            mapPointToRemoteCoords(
                pointX = 10.0, pointY = 10.0,
                elementWidth = 100.0, elementHeight = 100.0,
                frameWidth = 0, frameHeight = 0,
            ),
        )
    }

    @Test
    fun mapPoint_elemento_sin_dimensiones_es_null_sin_lanzar() {
        assertNull(
            mapPointToRemoteCoords(
                pointX = 10.0, pointY = 10.0,
                elementWidth = 0.0, elementHeight = 0.0,
                frameWidth = 100, frameHeight = 100,
            ),
        )
    }
}
