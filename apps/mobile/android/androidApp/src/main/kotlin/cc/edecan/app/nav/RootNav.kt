package cc.edecan.app.nav

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.size
import androidx.activity.compose.BackHandler
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.compose.material3.LocalContentColor
import androidx.lifecycle.viewmodel.compose.viewModel
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
import cc.edecan.app.vm.ChatViewModel
import cc.edecan.shared.AssistantDestination
import cc.edecan.app.notifications.NotificationRoute
import androidx.compose.runtime.LaunchedEffect

private val AssistantDestination.etiqueta: String
    get() = when (this) {
        AssistantDestination.EDECAN -> "Edecan"
        AssistantDestination.ACTIVITY -> "Actividad"
        AssistantDestination.YOU -> "Tú"
    }

/** Solo tres espacios humanos. Crear nace del compositor; Remoto, del
 * contexto de Actividad. Las capacidades técnicas siguen existiendo como
 * pantallas secundarias y no compiten con el asistente. */
private enum class PantallaSecundaria {
    CREAR,
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
 * Raíz de la app ya emparejada: `Scaffold` + `NavigationBar` de 3 destinos,
 * conmutados con estado local simple (`remember { mutableStateOf(...) }`) —
 * sin `androidx.navigation`: son 3 pestañas planas y un único nivel
 * secundario. [BackHandler] cierra ese nivel antes de salir y
 * `rememberSaveable` restaura ambos estados tras recrear la Activity.
 *
 * Encima de esas pestañas, [PantallaSecundaria] agrega un
 * segundo nivel de navegación de un solo paso — mismo `remember {
 * mutableStateOf(...) }` local, sin tocar la `NavigationBar` de abajo.
 */
@Composable
fun RootNav(
    sessionKey: Long,
    notificationRoute: NotificationRoute? = null,
    onNotificationRouteConsumed: () -> Unit = {},
) {
    val chatViewModel: ChatViewModel = viewModel()
    var seleccion by rememberSaveable(sessionKey) { mutableStateOf(AssistantDestination.EDECAN) }
    var pantallaSecundaria by rememberSaveable(sessionKey) { mutableStateOf<PantallaSecundaria?>(null) }
    var solicitudChat by rememberSaveable(sessionKey) { mutableStateOf<String?>(null) }

    LaunchedEffect(notificationRoute) {
        when (notificationRoute) {
            NotificationRoute.ASSISTANT -> seleccion = AssistantDestination.EDECAN
            NotificationRoute.ACTIVITY -> seleccion = AssistantDestination.ACTIVITY
            NotificationRoute.SETTINGS -> seleccion = AssistantDestination.YOU
            NotificationRoute.CREATE -> pantallaSecundaria = PantallaSecundaria.CREAR
            NotificationRoute.REMOTE -> pantallaSecundaria = PantallaSecundaria.REMOTO
            null -> return@LaunchedEffect
        }
        onNotificationRouteConsumed()
    }

    DisposableEffect(sessionKey) {
        onDispose {
            solicitudChat = null
            pantallaSecundaria = null
            seleccion = AssistantDestination.EDECAN
        }
    }

    BackHandler(enabled = pantallaSecundaria != null) { pantallaSecundaria = null }

    val secundaria = pantallaSecundaria
    if (secundaria != null) {
        val volver = { pantallaSecundaria = null }
        when (secundaria) {
            PantallaSecundaria.CREAR -> ContentStudioScreen(onVolver = volver)
            PantallaSecundaria.MISIONES -> MisionesScreen(onVolver = volver)
            PantallaSecundaria.AUTOMATIZACIONES -> AutomatizacionesScreen(onVolver = volver)
            PantallaSecundaria.RECORDATORIOS -> RecordatoriosScreen(onVolver = volver)
            PantallaSecundaria.REMOTO -> RemotoScreen(onVolver = volver)
            PantallaSecundaria.VOZ -> VozScreen(chatViewModel = chatViewModel, onVolver = volver)
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
                        icon = { IconoDestino(pestana) },
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
                    chatViewModel = chatViewModel,
                    onOpenVoice = { pantallaSecundaria = PantallaSecundaria.VOZ },
                    onOpenScreen = { screen ->
                        when (screen) {
                            "assistant" -> { seleccion = AssistantDestination.EDECAN; true }
                            "create" -> { pantallaSecundaria = PantallaSecundaria.CREAR; true }
                            "remote" -> { pantallaSecundaria = PantallaSecundaria.REMOTO; true }
                            "activity" -> { seleccion = AssistantDestination.ACTIVITY; true }
                            "settings" -> { seleccion = AssistantDestination.YOU; true }
                            "skills" -> { pantallaSecundaria = PantallaSecundaria.CAPACIDADES; true }
                            // Viajes sigue siendo una capacidad del chat; órdenes
                            // y archivos son resultados consultables desde Actividad.
                            // No reintroducimos módulos SaaS como pestañas nuevas.
                            "travel" -> { seleccion = AssistantDestination.EDECAN; true }
                            "orders", "files" -> { seleccion = AssistantDestination.ACTIVITY; true }
                            else -> false
                        }
                    },
                    solicitudInicial = solicitudChat,
                    onSolicitudConsumida = { solicitudChat = null },
                )
                AssistantDestination.ACTIVITY -> InicioScreen(
                    onAbrirMisiones = { pantallaSecundaria = PantallaSecundaria.MISIONES },
                    onAbrirAutomatizaciones = { pantallaSecundaria = PantallaSecundaria.AUTOMATIZACIONES },
                    onAbrirRecordatorios = { pantallaSecundaria = PantallaSecundaria.RECORDATORIOS },
                    onAbrirRemoto = { pantallaSecundaria = PantallaSecundaria.REMOTO },
                )
                AssistantDestination.YOU -> PerfilScreen(
                    onAbrirContenido = { pantallaSecundaria = PantallaSecundaria.CREAR },
                    onAbrirIde = { pantallaSecundaria = PantallaSecundaria.IDE },
                    onAbrirCapacidades = { pantallaSecundaria = PantallaSecundaria.CAPACIDADES },
                    onAbrirNegocios = { pantallaSecundaria = PantallaSecundaria.NEGOCIOS },
                )
            }
        }
    }
}

/** Iconos propios mínimos: mantienen la barra sobria sin sumar una librería
 * completa de iconos por tres glifos. El color lo aporta NavigationBarItem. */
@Composable
private fun IconoDestino(destino: AssistantDestination) {
    val color = LocalContentColor.current
    Canvas(
        modifier = Modifier.size(23.dp).semantics {
            contentDescription = "Ir a ${destino.etiqueta}"
        },
    ) {
        val stroke = Stroke(width = 1.9.dp.toPx(), cap = StrokeCap.Round)
        when (destino) {
            AssistantDestination.EDECAN -> {
                drawRoundRect(
                    color = color,
                    topLeft = Offset(size.width * .12f, size.height * .15f),
                    size = Size(size.width * .76f, size.height * .62f),
                    cornerRadius = CornerRadius(size.width * .20f),
                    style = stroke,
                )
                drawLine(
                    color,
                    Offset(size.width * .36f, size.height * .76f),
                    Offset(size.width * .25f, size.height * .88f),
                    strokeWidth = stroke.width,
                    cap = StrokeCap.Round,
                )
            }
            AssistantDestination.ACTIVITY -> {
                drawCircle(color, radius = size.minDimension * .37f, style = stroke)
                drawLine(
                    color,
                    center,
                    Offset(center.x, center.y - size.height * .20f),
                    strokeWidth = stroke.width,
                    cap = StrokeCap.Round,
                )
                drawLine(
                    color,
                    center,
                    Offset(center.x + size.width * .17f, center.y + size.height * .10f),
                    strokeWidth = stroke.width,
                    cap = StrokeCap.Round,
                )
            }
            AssistantDestination.YOU -> {
                drawCircle(
                    color,
                    radius = size.minDimension * .15f,
                    center = Offset(center.x, size.height * .31f),
                    style = stroke,
                )
                drawArc(
                    color = color,
                    startAngle = 205f,
                    sweepAngle = 130f,
                    useCenter = false,
                    topLeft = Offset(size.width * .19f, size.height * .48f),
                    size = Size(size.width * .62f, size.height * .45f),
                    style = stroke,
                )
            }
        }
    }
}
