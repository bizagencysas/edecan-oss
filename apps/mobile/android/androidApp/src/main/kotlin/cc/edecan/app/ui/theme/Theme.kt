package cc.edecan.app.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color

/**
 * Acentos morado/azul del mockup del panel web
 * (`apps/web/src/app/(app)/app/`, `DIRECCION_ACTUAL.md`) — el equivalente
 * Kotlin de `EdecanTheme` en `Theme.swift` (iOS). Mismos valores exactos de
 * color en ambas plataformas a propósito.
 */
object EdecanColors {
    val Morado = Color(0xFF8257F5)
    val Azul = Color(0xFF4A7DFA)

    /** Mismo degradado que `EdecanTheme.degradado` en iOS — usado a mano
     * (no vía `MaterialTheme.colorScheme`) en los pocos lugares que lo
     * necesitan tal cual: la burbuja del usuario en `ChatScreen` y el
     * encabezado de `OnboardingScreen`. */
    val Degradado = Brush.linearGradient(listOf(Morado, Azul))
}

private val EsquemaClaro = lightColorScheme(
    primary = EdecanColors.Morado,
    secondary = EdecanColors.Azul,
    tertiary = EdecanColors.Azul,
)

private val EsquemaOscuro = darkColorScheme(
    primary = EdecanColors.Morado,
    secondary = EdecanColors.Azul,
    tertiary = EdecanColors.Azul,
)

/** Envoltorio de `MaterialTheme` para toda la app — claro/oscuro según el
 * sistema, mismo criterio que `TarjetaVidrio`/`EdecanTheme` en iOS
 * (funciona en ambos sin condicionales propios en cada pantalla). Sin
 * "dynamic color" (Material You) a propósito: la identidad de marca
 * morado/azul es fija, no debe variar según el wallpaper del usuario. */
@Composable
fun EdecanTheme(content: @Composable () -> Unit) {
    val colorScheme = if (isSystemInDarkTheme()) EsquemaOscuro else EsquemaClaro
    MaterialTheme(colorScheme = colorScheme, content = content)
}
