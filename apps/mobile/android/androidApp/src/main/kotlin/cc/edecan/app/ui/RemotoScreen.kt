@file:OptIn(ExperimentalMaterial3Api::class)

package cc.edecan.app.ui

import android.graphics.BitmapFactory
import android.app.Activity
import android.util.Base64
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.rememberTransformableState
import androidx.compose.foundation.gestures.transformable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.positionChange
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import cc.edecan.app.ui.theme.EdecanColors
import cc.edecan.app.vm.RemotoUiState
import cc.edecan.app.vm.RemotoViewModel
import cc.edecan.app.vm.SessionViewModel
import cc.edecan.app.vm.TECLAS_ESPECIALES
import cc.edecan.shared.FLAG_COMPANION_REMOTE_INPUT
import cc.edecan.shared.FLAG_COMPANION_REMOTE_VIEW
import cc.edecan.shared.REMOTE_KIND_CONTROL
import cc.edecan.shared.REMOTE_KIND_VIEW
import cc.edecan.shared.REMOTE_STATUS_DENIED
import cc.edecan.shared.RemoteFrame
import cc.edecan.shared.RemoteSession
import cc.edecan.shared.boolFlag
import cc.edecan.shared.haTerminado
import cc.edecan.shared.isControl
import cc.edecan.shared.mapPointToRemoteCoords
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlin.math.abs

/**
 * Pestaña "Remoto" (`/v1/remote`, `ARCHITECTURE.md` §13.c/§14,
 * `docs/control-remoto.md` §7bis/§10 — WP-V6-09, espejo Android de WP-V6-08
 * en iOS): visor de la pantalla del Mac del tenant + control de teclado/mouse
 * cuando el plan trae `companion.remote_input`. Se llega acá SOLO desde
 * Inicio (`InicioScreen`, `RootNav.kt`), mismo criterio de "pantalla
 * secundaria" que Misiones/Automatizaciones/Recordatorios (WP-V5-07).
 *
 * Lógica real en [RemotoViewModel]; esta pantalla solo dibuja su estado y
 * traduce toques sobre el frame a coordenadas reales
 * (`cc.edecan.shared.mapPointToRemoteCoords`).
 *
 * GUARDRAIL NO NEGOCIABLE, dibujado literalmente en esta pantalla (ver
 * [BannerSesionActiva]): mientras haya una sesión en curso (pendiente o
 * activa), el indicador "Sesión ... activa" y el botón "Terminar" están
 * SIEMPRE visibles — nunca ocultables, nunca detrás de un menú.
 */
@Composable
fun RemotoScreen(
    sessionViewModel: SessionViewModel = viewModel(),
    remotoViewModel: RemotoViewModel = viewModel(),
    onVolver: () -> Unit = {},
    mostrarVolver: Boolean = true,
) {
    val sessionState by sessionViewModel.uiState.collectAsState()
    val uiState by remotoViewModel.uiState.collectAsState()
    val api = sessionViewModel.api

    val tieneFlagVista = sessionState.me?.flags?.boolFlag(FLAG_COMPANION_REMOTE_VIEW) ?: false
    val tieneFlagControl = sessionState.me?.flags?.boolFlag(FLAG_COMPANION_REMOTE_INPUT) ?: false
    val sesionInmersiva = uiState.sesionActual?.let { !it.haTerminado } == true

    BarrasSistemaInmersivas(activas = sesionInmersiva)

    LaunchedEffect(api) { api?.let { remotoViewModel.cargar(it) } }

    // Cero *polling* huérfano: al salir de esta pantalla (el usuario volvió a
    // Inicio) se pausa el polling del ViewModel — la sesión remota en sí
    // sigue activa del lado del servidor, ver el docstring de
    // `RemotoViewModel.pausarPolling`. `cargar()` la reanuda si se vuelve.
    DisposableEffect(Unit) {
        onDispose { remotoViewModel.pausarPolling() }
    }

    Scaffold(
        topBar = {
            if (!sesionInmersiva) {
                TopAppBar(
                    title = { Text("Remoto") },
                    navigationIcon = {
                        if (mostrarVolver) IconButton(onClick = onVolver) { Text("←") }
                    },
                )
            }
        },
    ) { padding ->
        Box(modifier = Modifier.padding(padding).fillMaxSize()) {
            val sesionActual = uiState.sesionActual
            when {
                !tieneFlagVista -> Box(modifier = Modifier.fillMaxSize().padding(24.dp), contentAlignment = Alignment.Center) {
                    Card {
                        Text(
                            "El control remoto de tu Mac no está incluido en tu plan actual" +
                                (sessionState.me?.tenant?.planKey?.let { " ($it)" } ?: "") +
                                ". Mejóralo desde el panel web (Ajustes → Facturación) para activarlo.",
                            style = MaterialTheme.typography.bodyMedium,
                            modifier = Modifier.padding(20.dp),
                        )
                    }
                }
                sesionActual == null -> ListaYNuevaSesion(
                    uiState = uiState,
                    permiteControl = tieneFlagControl,
                    onIniciar = { kind -> api?.let { remotoViewModel.iniciar(it, kind) } },
                )
                sesionActual.haTerminado -> Box(modifier = Modifier.fillMaxSize().padding(24.dp), contentAlignment = Alignment.Center) {
                    SesionTerminadaCard(sesion = sesionActual, onVolver = remotoViewModel::descartarSesionTerminada)
                }
                else -> SesionActivaInmersiva(
                    uiState = uiState,
                    onActualizar = { api?.let { remotoViewModel.actualizarFrame(it) } },
                    onTerminar = { api?.let { remotoViewModel.terminar(it) } },
                    onPointer = { comando ->
                        api?.let {
                            remotoViewModel.enviarPointer(
                                it, comando.x, comando.y, comando.accion, comando.button,
                                comando.startX, comando.startY, comando.deltaX, comando.deltaY,
                            )
                        }
                    },
                    onTexto = { texto -> api?.let { remotoViewModel.enviarTexto(it, texto) } },
                    onTecla = { tecla, modifiers ->
                        api?.let { remotoViewModel.enviarTecla(it, tecla, modifiers) }
                    },
                )
            }
        }
    }
}

@Composable
private fun BarrasSistemaInmersivas(activas: Boolean) {
    val activity = LocalContext.current as? Activity
    DisposableEffect(activity, activas) {
        val window = activity?.window
        val controller = window?.let { WindowCompat.getInsetsController(it, it.decorView) }
        if (activas) {
            controller?.hide(WindowInsetsCompat.Type.systemBars())
            controller?.systemBarsBehavior =
                WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        } else {
            controller?.show(WindowInsetsCompat.Type.systemBars())
        }
        onDispose {
            if (activas) controller?.show(WindowInsetsCompat.Type.systemBars())
        }
    }
}

// ---------------------------------------------------------------------------
// Sin sesión: consentimiento e inicio.
// ---------------------------------------------------------------------------

@Composable
private fun ListaYNuevaSesion(
    uiState: RemotoUiState,
    permiteControl: Boolean,
    onIniciar: (String) -> Unit,
) {
    Column(modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp)) {
        NuevaSesionCard(
            permiteControl = permiteControl,
            iniciando = uiState.iniciando,
            error = uiState.errorIniciar,
            onIniciar = onIniciar,
        )
    }
}

@Composable
private fun NuevaSesionCard(
    permiteControl: Boolean,
    iniciando: Boolean,
    error: String?,
    onIniciar: (String) -> Unit,
) {
    var quiereControl by remember { mutableStateOf(false) }
    var entendido by remember { mutableStateOf(false) }
    val kind = if (permiteControl && quiereControl) REMOTE_KIND_CONTROL else REMOTE_KIND_VIEW
    val esControl = kind == REMOTE_KIND_CONTROL

    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(16.dp)) {
            Text(
                if (esControl) "Iniciar sesión de control remoto" else "Iniciar sesión de vista remota",
                style = MaterialTheme.typography.titleMedium,
            )
            Text(
                if (esControl) {
                    "Vas a ver y manejar tu computadora desde este teléfono."
                } else {
                    "Vas a ver la pantalla de tu computadora sin mover el mouse ni escribir."
                },
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 4.dp),
            )
            Text(
                "Este teléfono ya está vinculado porque escaneaste el QR de la computadora. " +
                    "Confirma esta sesión una vez y podrás terminarla cuando quieras.",
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier
                    .padding(top = 12.dp)
                    .clip(RoundedCornerShape(10.dp))
                    .background(MaterialTheme.colorScheme.surfaceVariant)
                    .padding(10.dp),
            )

            if (permiteControl) {
                Row(
                    modifier = Modifier.fillMaxWidth().padding(top = 12.dp).clickable { quiereControl = !quiereControl },
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    // `onCheckedChange = null` a propósito: el `Row` entero ya es
                    // `clickable` (arriba) y maneja el toggle — patrón recomendado
                    // de accesibilidad de Android para "fila con checkbox líder"
                    // (evita un doble-toggle si el toque cae justo sobre el
                    // widget Y una doble locución de lector de pantalla).
                    Checkbox(checked = quiereControl, onCheckedChange = null)
                    Text(
                        "También quiero usar el mouse y el teclado",
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
            }

            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = if (permiteControl) 4.dp else 12.dp)
                    .clickable { entendido = !entendido },
                verticalAlignment = Alignment.CenterVertically,
            ) {
                // Mismo criterio de accesibilidad que el checkbox de arriba: el
                // `Row` completo ya es `clickable`.
                Checkbox(checked = entendido, onCheckedChange = null)
                Text(
                    if (esControl) {
                        "Confirmo que quiero ver y controlar mi computadora desde este teléfono."
                    } else {
                        "Confirmo que quiero ver la pantalla de mi computadora desde este teléfono."
                    },
                    style = MaterialTheme.typography.bodySmall,
                )
            }

            error?.let {
                Text(
                    it,
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(top = 8.dp),
                )
            }

            Button(
                onClick = { onIniciar(kind) },
                enabled = entendido && !iniciando,
                colors = ButtonDefaults.buttonColors(containerColor = EdecanColors.Morado),
                modifier = Modifier.fillMaxWidth().padding(top = 14.dp),
            ) {
                if (iniciando) {
                    CircularProgressIndicator(
                        modifier = Modifier.padding(end = 8.dp).size(18.dp),
                        color = Color.White,
                        strokeWidth = 2.dp,
                    )
                }
                Text(if (esControl) "Iniciar sesión de control remoto" else "Iniciar sesión de vista remota")
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Sesión terminada/denegada — pantalla de salida antes de volver a la lista.
// ---------------------------------------------------------------------------

@Composable
private fun SesionTerminadaCard(sesion: RemoteSession, onVolver: () -> Unit) {
    val denegada = sesion.status == REMOTE_STATUS_DENIED
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.fillMaxWidth().padding(24.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            Text(if (denegada) "🚫" else "✅", style = MaterialTheme.typography.displayMedium)
            Text(
                if (denegada) "El companion denegó esta sesión" else "Sesión terminada",
                style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.padding(top = 8.dp),
            )
            Text(
                if (denegada) {
                    "Alguien frente a tu Mac rechazó la aprobación local — no salió ningún frame ni se movió nada."
                } else {
                    "Frames recibidos: ${sesion.framesCount}"
                },
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                textAlign = TextAlign.Center,
                modifier = Modifier.padding(top = 4.dp),
            )
            Button(onClick = onVolver, modifier = Modifier.padding(top = 16.dp)) { Text("Volver") }
        }
    }
}

// ---------------------------------------------------------------------------
// Sesión pendiente/activa — indicador permanente + visor/espera.
// ---------------------------------------------------------------------------

@Composable
private fun SesionActivaInmersiva(
    uiState: RemotoUiState,
    onActualizar: () -> Unit,
    onTerminar: () -> Unit,
    onPointer: (RemotePointerCommand) -> Unit,
    onTexto: (String) -> Unit,
    onTecla: (String, List<String>) -> Unit,
) {
    val sesion = uiState.sesionActual ?: return
    val frame = uiState.frame
    var mostrarTeclado by remember(sesion.id) { mutableStateOf(false) }
    var ultimoPuntoX by remember(sesion.id) { mutableStateOf<Int?>(null) }
    var ultimoPuntoY by remember(sesion.id) { mutableStateOf<Int?>(null) }

    fun enviarDesdeUltimoPunto(accion: String, deltaY: Int = 0) {
        val actual = frame ?: return
        onPointer(
            RemotePointerCommand(
                x = ultimoPuntoX ?: actual.width / 2,
                y = ultimoPuntoY ?: actual.height / 2,
                accion = accion,
                deltaY = deltaY,
            )
        )
    }

    Box(modifier = Modifier.fillMaxSize().background(Color.Black)) {
        if (frame == null) {
            Box(
                modifier = Modifier.fillMaxSize().padding(horizontal = 20.dp, vertical = 72.dp),
                contentAlignment = Alignment.Center,
            ) {
                EsperandoAprobacionCard(
                    cargando = uiState.cargandoFrame,
                    error = uiState.errorFrame,
                    onReintentar = onActualizar,
                )
            }
        } else {
            VisorRemotoInmersivo(
                sesion = sesion,
                frame = frame,
                onPointer = { comando ->
                    ultimoPuntoX = comando.x
                    ultimoPuntoY = comando.y
                    onPointer(comando)
                },
            )
        }

        Row(
            modifier = Modifier
                .align(Alignment.TopCenter)
                .fillMaxWidth()
                .safeDrawingPadding()
                .padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Row(
                modifier = Modifier
                    .clip(RoundedCornerShape(999.dp))
                    .background(Color.Black.copy(alpha = 0.68f))
                    .padding(horizontal = 12.dp, vertical = 9.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Box(
                    modifier = Modifier
                        .size(9.dp)
                        .clip(CircleShape)
                        .background(if (sesion.status == "active") Color(0xFF22C55E) else Color(0xFFF59E0B))
                )
                Spacer(Modifier.width(8.dp))
                Text(
                    if (sesion.status == "active") "En vivo" else "Conectando",
                    color = Color.White,
                    style = MaterialTheme.typography.labelMedium,
                )
                if (uiState.enviandoInput) {
                    Spacer(Modifier.width(8.dp))
                    CircularProgressIndicator(
                        modifier = Modifier.size(14.dp),
                        color = Color.White,
                        strokeWidth = 2.dp,
                    )
                }
            }
            Spacer(Modifier.weight(1f))
            Button(
                onClick = onTerminar,
                enabled = !uiState.terminando,
                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFDC2626)),
                contentPadding = PaddingValues(horizontal = 14.dp, vertical = 9.dp),
            ) {
                Text("Terminar")
            }
        }

        Column(
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .fillMaxWidth()
                .imePadding()
                .navigationBarsPadding()
                .padding(horizontal = 10.dp, vertical = 8.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            uiState.errorFrame?.let {
                Text(
                    it,
                    color = Color.White,
                    style = MaterialTheme.typography.labelSmall,
                    maxLines = 3,
                    modifier = Modifier
                        .padding(bottom = 8.dp)
                        .clip(RoundedCornerShape(999.dp))
                        .background(Color(0xFFDC2626).copy(alpha = 0.9f))
                        .padding(horizontal = 12.dp, vertical = 8.dp),
                )
            }

            if (sesion.isControl && mostrarTeclado) {
                BarraTeclado(
                    enviando = uiState.enviandoInput,
                    compacta = true,
                    onTexto = onTexto,
                    onTecla = onTecla,
                )
                Spacer(Modifier.height(8.dp))
            }

            if (sesion.isControl) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(999.dp))
                        .background(Color.Black.copy(alpha = 0.72f))
                        .padding(6.dp),
                    horizontalArrangement = Arrangement.SpaceEvenly,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    ControlDockButton("⌨", "Teclado", true) { mostrarTeclado = !mostrarTeclado }
                    ControlDockButton("◉", "Derecho", !uiState.enviandoInput) {
                        enviarDesdeUltimoPunto("right_click")
                    }
                    ControlDockButton("↑", "Subir", !uiState.enviandoInput) {
                        enviarDesdeUltimoPunto("scroll", 520)
                    }
                    ControlDockButton("↓", "Bajar", !uiState.enviandoInput) {
                        enviarDesdeUltimoPunto("scroll", -520)
                    }
                    ControlDockButton("↻", "Actualizar", !uiState.cargandoFrame) { onActualizar() }
                }
            } else {
                Text(
                    "Solo vista",
                    color = Color.White,
                    style = MaterialTheme.typography.labelMedium,
                    modifier = Modifier
                        .clip(RoundedCornerShape(999.dp))
                        .background(Color.Black.copy(alpha = 0.7f))
                        .padding(horizontal = 14.dp, vertical = 9.dp),
                )
            }
        }
    }
}

@Composable
private fun ControlDockButton(etiqueta: String, titulo: String, habilitado: Boolean, onClick: () -> Unit) {
    Button(
        onClick = onClick,
        enabled = habilitado,
        colors = ButtonDefaults.buttonColors(
            containerColor = Color.Transparent,
            disabledContainerColor = Color.Transparent,
            contentColor = Color.White,
            disabledContentColor = Color.White.copy(alpha = 0.45f),
        ),
        contentPadding = PaddingValues(horizontal = 5.dp, vertical = 5.dp),
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(etiqueta, style = MaterialTheme.typography.titleMedium)
            Text(titulo, style = MaterialTheme.typography.labelSmall)
        }
    }
}

@Composable
private fun BannerSesionActiva(sesion: RemoteSession, terminando: Boolean, onTerminar: () -> Unit) {
    val esControl = sesion.isControl
    val colorAcento = if (esControl) Color(0xFFEF4444) else EdecanColors.Morado
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(colorAcento.copy(alpha = 0.12f))
            .padding(horizontal = 14.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(modifier = Modifier.size(10.dp).clip(CircleShape).background(colorAcento))
        Spacer(modifier = Modifier.width(10.dp))
        Text(
            if (esControl) "Sesión de control remoto activa — se está controlando tu Mac" else "Sesión de vista remota activa — solo lectura",
            style = MaterialTheme.typography.labelLarge,
            modifier = Modifier.weight(1f),
        )
        Spacer(modifier = Modifier.width(8.dp))
        Button(
            onClick = onTerminar,
            enabled = !terminando,
            colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFEF4444)),
        ) {
            if (terminando) {
                CircularProgressIndicator(modifier = Modifier.size(16.dp), color = Color.White, strokeWidth = 2.dp)
            } else {
                Text("Terminar")
            }
        }
    }
}

@Composable
private fun EsperandoAprobacionCard(cargando: Boolean, error: String?, onReintentar: () -> Unit) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.fillMaxWidth().padding(32.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            if (cargando) {
                CircularProgressIndicator(modifier = Modifier.padding(bottom = 16.dp))
                Text("Esperando aprobación en tu Mac…", style = MaterialTheme.typography.titleMedium, textAlign = TextAlign.Center)
                Text(
                    "Tu companion te va a pedir confirmar esta sesión localmente — puede tardar hasta unos 30 segundos.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.padding(top = 8.dp),
                )
            } else {
                Text("🕓", style = MaterialTheme.typography.displayMedium)
                Text("Todavía no hay respuesta", style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(top = 8.dp))
                error?.let {
                    Text(
                        it,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error,
                        textAlign = TextAlign.Center,
                        modifier = Modifier.padding(top = 8.dp),
                    )
                }
                Button(onClick = onReintentar, modifier = Modifier.padding(top = 16.dp)) { Text("Reintentar") }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Visor: frame + zoom/pan + tap-to-click + barra de teclado.
// ---------------------------------------------------------------------------

/** Decodifica `frame.image_b64` (JPEG/PNG en base64) a [ImageBitmap] en
 * `Dispatchers.Default` (nunca en el hilo principal — una captura de
 * pantalla completa puede pesar varios cientos de KB) — `null` mientras
 * decodifica o si el decode falla (nunca lanza, `runCatching`). */
@Composable
private fun rememberFrameBitmap(imageB64: String?): ImageBitmap? {
    var bitmap by remember { mutableStateOf<ImageBitmap?>(null) }
    LaunchedEffect(imageB64) {
        bitmap = if (imageB64.isNullOrEmpty()) {
            null
        } else {
            withContext(Dispatchers.Default) {
                runCatching {
                    val bytes = Base64.decode(imageB64, Base64.DEFAULT)
                    BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.asImageBitmap()
                }.getOrNull()
            }
        }
    }
    return bitmap
}

@Composable
private fun VisorRemotoInmersivo(
    sesion: RemoteSession,
    frame: RemoteFrame,
    onPointer: (RemotePointerCommand) -> Unit,
) {
    val esControl = sesion.isControl
    val bitmap = rememberFrameBitmap(frame.imageB64)

    // Zoom/pan puramente VISUAL (inspeccionar de cerca) — el mapeo de
    // coordenadas de un toque a `input_pointer` sigue funcionando igual a
    // cualquier zoom: Compose reporta el offset de `pointerInput` ya en el
    // espacio de coordenadas LOCAL del nodo (des-transformado respecto a
    // cualquier `graphicsLayer` ancestro), así que [tamanoElemento] (tamaño
    // de LAYOUT, que `graphicsLayer` no altera) + ese offset alcanzan sin
    // ningún ajuste manual por el zoom actual.
    var zoom by remember(sesion.id) { mutableFloatStateOf(1f) }
    var pan by remember(sesion.id) { mutableStateOf(Offset.Zero) }
    // La sobrecarga moderna incluye el centroide como primer argumento. Este
    // visor mantiene su comportamiento previo (zoom centrado por graphicsLayer),
    // por lo que no necesita consumirlo todavía.
    val transformState = rememberTransformableState { _, zoomChange, panChange, _ ->
        zoom = (zoom * zoomChange).coerceIn(1f, 4f)
        pan += panChange
    }
    var tamanoElemento by remember { mutableStateOf(IntSize.Zero) }

    fun manejarToque(offset: Offset, accion: String) {
        val tam = tamanoElemento
        if (tam.width <= 0 || tam.height <= 0) return
        val punto = mapPointToRemoteCoords(
            pointX = offset.x.toDouble(),
            pointY = offset.y.toDouble(),
            elementWidth = tam.width.toDouble(),
            elementHeight = tam.height.toDouble(),
            frameWidth = frame.width,
            frameHeight = frame.height,
            originX = frame.originX,
            originY = frame.originY,
        ) ?: return // cayó en la franja vacía del letterbox -- se ignora, nunca se manda una coordenada inventada.
        onPointer(RemotePointerCommand(x = punto.x, y = punto.y, accion = accion))
    }

    fun manejarArrastre(inicio: Offset, fin: Offset) {
        val tam = tamanoElemento
        if (tam.width <= 0 || tam.height <= 0) return
        fun mapear(offset: Offset) = mapPointToRemoteCoords(
            pointX = offset.x.toDouble(), pointY = offset.y.toDouble(),
            elementWidth = tam.width.toDouble(), elementHeight = tam.height.toDouble(),
            frameWidth = frame.width, frameHeight = frame.height,
            originX = frame.originX, originY = frame.originY,
        )
        val start = mapear(inicio) ?: return
        val end = mapear(fin) ?: return
        onPointer(
            RemotePointerCommand(
                x = end.x, y = end.y, accion = "drag", startX = start.x, startY = start.y,
            )
        )
    }

    Box(
        modifier = Modifier.fillMaxSize().background(Color.Black),
        contentAlignment = Alignment.Center,
    ) {
        if (bitmap != null) {
            Image(
                bitmap = bitmap,
                contentDescription = "Pantalla remota interactiva",
                contentScale = ContentScale.Fit,
                modifier = Modifier
                    .fillMaxSize()
                    .onSizeChanged { tamanoElemento = it }
                    .graphicsLayer(
                        scaleX = zoom,
                        scaleY = zoom,
                        translationX = pan.x,
                        translationY = pan.y,
                    )
                    .transformable(transformState)
                    .then(
                        if (esControl) {
                            Modifier.pointerInput(frame.width, frame.height) {
                                detectTapGestures(
                                    onTap = { offset -> manejarToque(offset, "click") },
                                    onDoubleTap = { offset -> manejarToque(offset, "double_click") },
                                    onLongPress = { offset -> manejarToque(offset, "right_click") },
                                )
                            }
                        } else Modifier
                    )
                    .then(
                        if (esControl) {
                            Modifier.pointerInput(frame.seq, "drag") {
                                var inicio = Offset.Zero
                                var ultimo = Offset.Zero
                                detectDragGestures(
                                    onDragStart = { inicio = it; ultimo = it },
                                    onDrag = { change, _ -> ultimo = change.position; change.consume() },
                                    onDragEnd = { manejarArrastre(inicio, ultimo) },
                                )
                            }
                        } else Modifier
                    )
                    .then(
                        if (esControl) {
                            Modifier.pointerInput(frame.seq, "two-finger-scroll") {
                                awaitEachGesture {
                                    awaitFirstDown(requireUnconsumed = false)
                                    var desplazamiento = 0f
                                    var detectoDosDedos = false
                                    while (true) {
                                        val evento = awaitPointerEvent()
                                        val activos = evento.changes.filter { it.pressed }
                                        if (activos.size >= 2) {
                                            detectoDosDedos = true
                                            desplazamiento += activos
                                                .take(2)
                                                .map { it.positionChange().y }
                                                .average()
                                                .toFloat()
                                            activos.forEach { it.consume() }
                                        }
                                        if (evento.changes.none { it.pressed }) break
                                    }
                                    if (detectoDosDedos && abs(desplazamiento) > 18f) {
                                        onPointer(
                                            RemotePointerCommand(
                                                x = frame.width / 2,
                                                y = frame.height / 2,
                                                accion = "scroll",
                                                deltaY = if (desplazamiento < 0) 520 else -520,
                                            )
                                        )
                                    }
                                }
                            }
                        } else Modifier
                    ),
            )
        } else {
            CircularProgressIndicator(color = Color.White)
        }
    }
}

@Composable
private fun BarraTeclado(
    enviando: Boolean,
    compacta: Boolean = false,
    onTexto: (String) -> Unit,
    onTecla: (String, List<String>) -> Unit,
) {
    var texto by remember { mutableStateOf("") }

    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(12.dp)) {
            Text("Escribir en el equipo remoto", style = MaterialTheme.typography.titleSmall)
            if (!compacta) {
                Text(
                    "Se envía carácter por carácter al companion, como si lo tipearas ahí.",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(bottom = 8.dp),
                )
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(
                    value = texto,
                    onValueChange = { texto = it },
                    placeholder = { Text("Escribe aquí y pulsa Enviar…") },
                    enabled = !enviando,
                    modifier = Modifier.weight(1f),
                )
                Spacer(modifier = Modifier.width(8.dp))
                Button(
                    onClick = { if (texto.isNotEmpty()) { onTexto(texto); texto = "" } },
                    enabled = !enviando && texto.isNotEmpty(),
                ) { Text("Enviar") }
            }

            Row(
                horizontalArrangement = Arrangement.spacedBy(6.dp),
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 8.dp)
                    .horizontalScroll(rememberScrollState()),
            ) {
                TECLAS_ESPECIALES.take(if (compacta) 8 else TECLAS_ESPECIALES.size).forEach { tecla ->
                    OutlinedButton(
                        onClick = { onTecla(tecla.valor, emptyList()) },
                        enabled = !enviando,
                        contentPadding = PaddingValues(horizontal = 8.dp, vertical = 6.dp),
                        modifier = Modifier.semantics { contentDescription = tecla.titulo },
                    ) {
                        Text(tecla.etiqueta)
                    }
                }
            }

            if (!compacta) {
                Text("Atajos", style = MaterialTheme.typography.labelMedium, modifier = Modifier.padding(top = 10.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    listOf("a" to "⌘A", "c" to "⌘C", "v" to "⌘V", "x" to "⌘X", "z" to "⌘Z", "s" to "⌘S")
                        .forEach { (key, label) ->
                            OutlinedButton(onClick = { onTecla(key, listOf("command")) }, enabled = !enviando) {
                                Text(label)
                            }
                        }
                }
            }
        }
    }
}

private data class RemotePointerCommand(
    val x: Int,
    val y: Int,
    val accion: String,
    val button: String? = null,
    val startX: Int? = null,
    val startY: Int? = null,
    val deltaX: Int = 0,
    val deltaY: Int = 0,
)
