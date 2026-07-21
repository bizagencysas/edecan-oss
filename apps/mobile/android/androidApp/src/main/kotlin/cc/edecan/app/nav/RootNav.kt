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
import cc.edecan.app.ui.CapabilitiesScreen
import cc.edecan.app.ui.ContentStudioScreen
import cc.edecan.app.ui.IdeScreen
import cc.edecan.app.ui.InicioScreen
import cc.edecan.app.ui.MisionesScreen
import cc.edecan.app.ui.NegociosScreen
import cc.edecan.app.ui.PerfilScreen
import cc.edecan.app.ui.RecordatoriosScreen
import cc.edecan.app.ui.RemotoScreen
import cc.edecan.app.ui.VozScreen
import cc.edecan.shared.AssistantDestination

private val AssistantDestination.etiqueta: String
    get() = when (this) {
        AssistantDestination.EDECAN -> "Edecan"
        AssistantDestination.STUDIO -> "Crear"
        AssistantDestination.REMOTE -> "Remoto"
        AssistantDestination.ACTIVITY -> "Actividad"
        AssistantDestination.SETTINGS -> "Ajustes"
    }

private val AssistantDestination.emoji: String
    get() = when (this) {
        AssistantDestination.EDECAN -> "💬"
        AssistantDestination.STUDIO -> "✨"
        AssistantDestination.REMOTE -> "🖥️"
        AssistantDestination.ACTIVITY -> "🕘"
        AssistantDestination.SETTINGS -> "⚙️"
    }

/** Cinco destinos humanos. Voz se abre desde Chat; IDE, Skills/MCP y
 * Negocios viven en Ajustes avanzados. */
private enum class PantallaSecundaria {
    MISIONES,
    AUTOMATIZACIONES,
    RECORDATORIOS,
    REMOTO,
    VOZ,
    IDE,
    NEGOCIOS,
    CAPACIDADES,
}

/**
 * Raíz de la app ya emparejada: `Scaffold` + `NavigationBar` de 5 destinos,
 * conmutados con estado local simple (`remember { mutableStateOf(...) }`) —
 * sin `androidx.navigation` a propósito: no hay back-stack ni deep links
 * que gestionar en este esqueleto (5 pestañas planas, cada una su propia
 * pantalla independiente), así que sumar esa dependencia sería peso extra
 * sin beneficio real. Mismo criterio simple que el `@State private var
 * seleccion` + `TabView(selection:)` de `RootTabView.swift` (iOS).
 *
 * Encima de esas pestañas, [PantallaSecundaria] agrega un
 * segundo nivel de navegación de un solo paso — mismo `remember {
 * mutableStateOf(...) }` local, sin tocar la `NavigationBar` de abajo.
 */
@Composable
fun RootNav() {
    var seleccion by remember { mutableStateOf(AssistantDestination.EDECAN) }
    var pantallaSecundaria by remember { mutableStateOf<PantallaSecundaria?>(null) }
    var solicitudChat by remember { mutableStateOf<String?>(null) }

    val secundaria = pantallaSecundaria
    if (secundaria != null) {
        val volver = { pantallaSecundaria = null }
        when (secundaria) {
            PantallaSecundaria.MISIONES -> MisionesScreen(onVolver = volver)
            PantallaSecundaria.AUTOMATIZACIONES -> AutomatizacionesScreen(onVolver = volver)
            PantallaSecundaria.RECORDATORIOS -> RecordatoriosScreen(onVolver = volver)
            PantallaSecundaria.REMOTO -> RemotoScreen(onVolver = volver)
            PantallaSecundaria.VOZ -> VozScreen(onVolver = volver)
            PantallaSecundaria.IDE -> IdeScreen(onVolver = volver)
            PantallaSecundaria.NEGOCIOS -> NegociosScreen(onVolver = volver)
            PantallaSecundaria.CAPACIDADES -> CapabilitiesScreen(onVolver = volver)
        }
        return
    }

    Scaffold(
        bottomBar = {
            NavigationBar {
                AssistantDestination.entries.forEach { pestana ->
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
                AssistantDestination.EDECAN -> ChatScreen(
                    onOpenVoice = { pantallaSecundaria = PantallaSecundaria.VOZ },
                    solicitudInicial = solicitudChat,
                    onSolicitudConsumida = { solicitudChat = null },
                )
                AssistantDestination.STUDIO -> ContentStudioScreen { solicitud ->
                    solicitudChat = solicitud
                    seleccion = AssistantDestination.EDECAN
                }
                AssistantDestination.REMOTE -> RemotoScreen(mostrarVolver = false)
                AssistantDestination.ACTIVITY -> InicioScreen(
                    onAbrirMisiones = { pantallaSecundaria = PantallaSecundaria.MISIONES },
                    onAbrirAutomatizaciones = { pantallaSecundaria = PantallaSecundaria.AUTOMATIZACIONES },
                    onAbrirRecordatorios = { pantallaSecundaria = PantallaSecundaria.RECORDATORIOS },
                    onAbrirRemoto = { pantallaSecundaria = PantallaSecundaria.REMOTO },
                )
                AssistantDestination.SETTINGS -> PerfilScreen(
                    onAbrirIde = { pantallaSecundaria = PantallaSecundaria.IDE },
                    onAbrirCapacidades = { pantallaSecundaria = PantallaSecundaria.CAPACIDADES },
                    onAbrirNegocios = { pantallaSecundaria = PantallaSecundaria.NEGOCIOS },
                )
            }
        }
    }
}
