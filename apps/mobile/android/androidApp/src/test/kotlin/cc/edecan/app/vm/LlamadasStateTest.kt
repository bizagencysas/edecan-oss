package cc.edecan.app.vm

import cc.edecan.shared.ApiException
import cc.edecan.shared.PhoneCall
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

class LlamadasStateTest {
    @Test
    fun cargaExitosaReemplazaHistorialYLimpiaErrores() {
        val result = LlamadasUiState(
            cargando = true,
            errorMensaje = "anterior",
            mensajeNoDisponible = "anterior",
        ).alCargar(listOf(PhoneCall(id = "call-1")))

        assertFalse(result.cargando)
        assertEquals("call-1", result.llamadas.single().id)
        assertNull(result.errorMensaje)
        assertNull(result.mensajeNoDisponible)
    }

    @Test
    fun errorTransitorioConservaUltimoHistorialVisible() {
        val anterior = PhoneCall(id = "call-visible")

        val result = LlamadasUiState(cargando = true, llamadas = listOf(anterior))
            .alFallar(ApiException.SinConexion("sin red"))

        assertEquals(listOf(anterior), result.llamadas)
        assertTrue(result.errorMensaje?.contains("sin red") == true)
        assertNull(result.mensajeNoDisponible)
    }

    @Test
    fun planSinTelefoniaMuestraEstadoNoDisponibleSinFalsoHistorial() {
        val result = LlamadasUiState(
            cargando = true,
            llamadas = listOf(PhoneCall(id = "vieja")),
        ).alFallar(ApiException.Servidor(403, "Telefonía no incluida"))

        assertFalse(result.cargando)
        assertTrue(result.llamadas.isEmpty())
        assertEquals("Telefonía no incluida", result.mensajeNoDisponible)
        assertNull(result.errorMensaje)
    }
}
