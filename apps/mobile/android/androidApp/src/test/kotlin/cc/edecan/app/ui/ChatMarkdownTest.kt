package cc.edecan.app.ui

import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class ChatMarkdownTest {
    @Test
    fun renderizaEstructuraYEstilosSinMostrarMarcadores() {
        val resultado = markdownParaChat(
            "# Resumen\n- **Listo** y `seguro`\n> _Revisa antes de enviar_",
        )

        assertEquals("Resumen\n• Listo y seguro\n› Revisa antes de enviar", resultado.text)
        assertTrue(resultado.spanStyles.any { it.item.fontWeight == FontWeight.Bold })
        assertTrue(resultado.spanStyles.any { it.item.fontFamily == FontFamily.Monospace })
        assertTrue(resultado.spanStyles.any { it.item.fontStyle == FontStyle.Italic })
    }

    @Test
    fun conservaMarcadoresIncompletosYEscapesComoTexto() {
        val resultado = markdownParaChat("Usa \\*asteriscos\\* y **sin cierre")

        assertEquals("Usa *asteriscos* y **sin cierre", resultado.text)
    }

    @Test
    fun bloqueDeCodigoConservaSaltosYQuitaLasCercas() {
        val resultado = markdownParaChat("```kotlin\nval listo = true\nprintln(listo)\n```")

        assertEquals("val listo = true\nprintln(listo)\n", resultado.text)
        assertTrue(resultado.spanStyles.count { it.item.fontFamily == FontFamily.Monospace } >= 2)
    }

    @Test
    fun autoScrollSigueDeltasSoloCercaDelFinalYSinArrastre() {
        assertTrue(debeSeguirDelta(ultimoVisible = 8, totalMensajes = 10, usuarioArrastrando = false))
        assertTrue(!debeSeguirDelta(ultimoVisible = 3, totalMensajes = 10, usuarioArrastrando = false))
        assertTrue(!debeSeguirDelta(ultimoVisible = 9, totalMensajes = 10, usuarioArrastrando = true))
    }
}
