package cc.edecan.app.nav

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import cc.edecan.app.ui.AutomatizacionesScreen
import cc.edecan.app.ui.ChatScreen
import cc.edecan.app.ui.IdeScreen
import cc.edecan.app.ui.InicioScreen
import cc.edecan.app.ui.MisionesScreen
import cc.edecan.app.ui.NegociosScreen
import cc.edecan.app.ui.PerfilScreen
import cc.edecan.app.ui.RecordatoriosScreen
import cc.edecan.app.ui.RemotoScreen
import cc.edecan.app.ui.VozScreen

/** Las 6 pestañas del mockup del panel web: Inicio, Chat, IDE, Negocios,
 * Voz, Perfil (`DIRECCION_ACTUAL.md` "Apps móviles", `ROADMAP_V2.md` §6.1)
 * — mismas 6 que `RootTabView.swift` (iOS), mismo orden. "Voz" reemplaza a
 * "Llamadas" del mockup original (WP-V4-04): lo que hay hoy es push-to-talk
 * contra el mismo asistente (`VozScreen`), no telefonía — ver su docstring. */
private enum class Pestana(val etiqueta: String, val emoji: String) {
    INICIO("Inicio", "🏠"),
    CHAT("Chat", "💬"),
    IDE("IDE", "💻"),
    NEGOCIOS("Negocios", "📊"),
    VOZ("Voz", "🎙️"),
    PERFIL("Perfil", "👤"),
}

/** Pantallas de nivel "secundario" (WP-V5-07, WP-V6-09 suma REMOTO): NO son
 * pestañas de la barra inferior — se llega a ellas SOLO desde los accesos
 * directos de [InicioScreen] y cubren todo el contenido (barra inferior
 * incluida) mientras están abiertas, con su propio botón "atrás" que vuelve
 * a Inicio. Mismo criterio de "push" de un solo nivel que usa
 * `InicioView.swift` (iOS) con `NavigationStack` para sus propios accesos
 * directos. */
private enum class PantallaSecundaria { MISIONES, AUTOMATIZACIONES, RECORDATORIOS, REMOTO }

/**
 * Raíz de la app ya emparejada: `Scaffold` + `NavigationBar` de 6 destinos,
 * conmutados con estado local simple (`remember { mutableStateOf(...) }`) —
 * sin `androidx.navigation` a propósito: no hay back-stack ni deep links
 * que gestionar en este esqueleto (6 pestañas planas, cada una su propia
 * pantalla independiente), así que sumar esa dependencia sería peso extra
 * sin beneficio real. Mismo criterio simple que el `@State private var
 * seleccion` + `TabView(selection:)` de `RootTabView.swift` (iOS).
 *
 * Encima de esas 6 pestañas, [PantallaSecundaria] (WP-V5-07) agrega un
 * segundo nivel de navegación de un solo paso — mismo `remember {
 * mutableStateOf(...) }` local, sin tocar la `NavigationBar` de abajo.
 */
@Composable
fun RootNav() {
    var seleccion by remember { mutableStateOf(Pestana.INICIO) }
    var pantallaSecundaria by remember { mutableStateOf<PantallaSecundaria?>(null) }

    val secundaria = pantallaSecundaria
    if (secundaria != null) {
        val volver = { pantallaSecundaria = null }
        when (secundaria) {
            PantallaSecundaria.MISIONES -> MisionesScreen(onVolver = volver)
            PantallaSecundaria.AUTOMATIZACIONES -> AutomatizacionesScreen(onVolver = volver)
            PantallaSecundaria.RECORDATORIOS -> RecordatoriosScreen(onVolver = volver)
            PantallaSecundaria.REMOTO -> RemotoScreen(onVolver = volver)
        }
        return
    }

    Scaffold(
        bottomBar = {
            NavigationBar {
                Pestana.entries.forEach { pestana ->
                    NavigationBarItem(
                        selected = seleccion == pestana,
                        onClick = { seleccion = pestana },
                        icon = { Text(pestana.emoji) },
                        label = { Text(pestana.etiqueta) },
                    )
                }
            }
        },
    ) { padding ->
        // Cada pantalla trae su propio `Scaffold`+`TopAppBar` interno (ver
        // InicioScreen/ChatScreen/etc.) — este `Box` solo le reserva a ESE
        // Scaffold interno el espacio que ya descuenta la NavigationBar de
        // abajo, para que ningún contenido quede tapado detrás de ella.
        Box(modifier = Modifier.padding(padding)) {
            when (seleccion) {
                Pestana.INICIO -> InicioScreen(
                    onAbrirMisiones = { pantallaSecundaria = PantallaSecundaria.MISIONES },
                    onAbrirAutomatizaciones = { pantallaSecundaria = PantallaSecundaria.AUTOMATIZACIONES },
                    onAbrirRecordatorios = { pantallaSecundaria = PantallaSecundaria.RECORDATORIOS },
                    onAbrirRemoto = { pantallaSecundaria = PantallaSecundaria.REMOTO },
                )
                Pestana.CHAT -> ChatScreen()
                Pestana.IDE -> IdeScreen()
                Pestana.NEGOCIOS -> NegociosScreen()
                Pestana.VOZ -> VozScreen()
                Pestana.PERFIL -> PerfilScreen()
            }
        }
    }
}
