package cc.edecan.app.ui

import kotlin.test.Test
import kotlin.test.assertEquals

class ImagePreviewSizingTest {
    @Test
    fun `small images are not sampled`() {
        assertEquals(1, calcularMuestraImagen(width = 900, height = 600, maxEdge = 2048))
    }

    @Test
    fun `large portrait and landscape images are bounded with a power of two`() {
        assertEquals(4, calcularMuestraImagen(width = 8064, height = 6048, maxEdge = 2048))
        assertEquals(8, calcularMuestraImagen(width = 4000, height = 12000, maxEdge = 2048))
    }

    @Test
    fun `invalid metadata degrades to a safe sample`() {
        assertEquals(1, calcularMuestraImagen(width = -1, height = -1, maxEdge = 2048))
        assertEquals(1, calcularMuestraImagen(width = 4000, height = 3000, maxEdge = 0))
    }
}
