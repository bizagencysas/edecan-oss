@file:OptIn(ExperimentalMaterial3Api::class)

package cc.edecan.app.ui

import android.graphics.BitmapFactory
import android.util.Base64
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectDragGestures
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
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
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
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import cc.edecan.app.ui.components.formatearFechaHora
import cc.edecan.app.ui.theme.EdecanColors
import cc.edecan.app.vm.RemotoUiState
import cc.edecan.app.vm.RemotoViewModel
import cc.edecan.app.vm.SessionViewModel
import cc.edecan.app.vm.TECLAS_ESPECIALES
import cc.edecan.shared.FLAG_COMPANION_REMOTE_INPUT
import cc.edecan.shared.FLAG_COMPANION_REMOTE_VIEW
import cc.edecan.shared.REMOTE_KIND_CONTROL
import cc.edecan.shared.REMOTE_KIND_VIEW
import cc.edecan.shared.REMOTE_STATUS_ACTIVE
import cc.edecan.shared.REMOTE_STATUS_DENIED
import cc.edecan.shared.REMOTE_STATUS_ENDED
import cc.edecan.shared.REMOTE_STATUS_PENDING
import cc.edecan.shared.RemoteFrame
import cc.edecan.shared.RemoteSession
import cc.edecan.shared.boolFlag
import cc.edecan.shared.haTerminado
import cc.edecan.shared.isControl
import cc.edecan.shared.mapPointToRemoteCoords
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

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
            TopAppBar(
                title = { Text("Remoto") },
                navigationIcon = {
                    if (mostrarVolver) IconButton(onClick = onVolver) { Text("←") }
                },
            )
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
                else -> SesionActivaColumn(
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

// ---------------------------------------------------------------------------
// Sin sesión: "Nueva sesión" (consentimiento) + historial.
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

        Text(
            "Sesiones anteriores",
            style = MaterialTheme.typography.titleSmall,
            modifier = Modifier.padding(top = 24.dp, bottom = 8.dp),
        )
        when {
            uiState.cargandoSesiones && uiState.sesiones.isEmpty() ->
                CircularProgressIndicator(modifier = Modifier.padding(vertical = 12.dp))
            uiState.sesiones.isEmpty() -> Text(
                "Todavía no iniciaste ninguna sesión remota.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            else -> Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                uiState.sesiones.forEach { sesion -> FilaHistorialSesion(sesion) }
            }
        }
        uiState.errorLista?.let { error ->
            Text(
                error,
                color = MaterialTheme.colorScheme.error,
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.padding(top = 8.dp),
            )
        }
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

private val ETIQUETAS_ESTADO_REMOTO = mapOf(
    REMOTE_STATUS_PENDING to "Pendiente",
    REMOTE_STATUS_ACTIVE to "Activa",
    REMOTE_STATUS_ENDED to "Terminada",
    REMOTE_STATUS_DENIED to "Denegada",
)

@Composable
private fun FilaHistorialSesion(sesion: RemoteSession) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Row(modifier = Modifier.fillMaxWidth().padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(if (sesion.isControl) "🖱️" else "👁️", modifier = Modifier.padding(end = 10.dp))
            Column(modifier = Modifier.weight(1f)) {
                Text(if (sesion.isControl) "Control remoto" else "Solo vista", style = MaterialTheme.typography.bodyMedium)
                Text(
                    "${formatearFechaHora(sesion.createdAt)} · ${sesion.framesCount} frames",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Text(
                ETIQUETAS_ESTADO_REMOTO[sesion.status] ?: sesion.status,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
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
private fun SesionActivaColumn(
    uiState: RemotoUiState,
    onActualizar: () -> Unit,
    onTerminar: () -> Unit,
    onPointer: (RemotePointerCommand) -> Unit,
    onTexto: (String) -> Unit,
    onTecla: (String, List<String>) -> Unit,
) {
    val sesion = uiState.sesionActual ?: return
    Column(modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp)) {
        // Guardrail no negociable: SIEMPRE visible mientras haya sesión, sin
        // importar el sub-estado (esperando aprobación o ya activa) — ver el
        // docstring de `RemotoScreen`.
        BannerSesionActiva(sesion = sesion, terminando = uiState.terminando, onTerminar = onTerminar)

        val frame = uiState.frame
        if (frame == null) {
            Spacer(modifier = Modifier.height(16.dp))
            EsperandoAprobacionCard(cargando = uiState.cargandoFrame, error = uiState.errorFrame, onReintentar = onActualizar)
        } else {
            VisorRemoto(
                sesion = sesion,
                frame = frame,
                cargandoFrame = uiState.cargandoFrame,
                enviandoInput = uiState.enviandoInput,
                errorMensaje = uiState.errorFrame,
                onActualizar = onActualizar,
                onPointer = onPointer,
                onTexto = onTexto,
                onTecla = onTecla,
            )
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
private fun VisorRemoto(
    sesion: RemoteSession,
    frame: RemoteFrame,
    cargandoFrame: Boolean,
    enviandoInput: Boolean,
    errorMensaje: String?,
    onActualizar: () -> Unit,
    onPointer: (RemotePointerCommand) -> Unit,
    onTexto: (String) -> Unit,
    onTecla: (String, List<String>) -> Unit,
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

    Column(modifier = Modifier.fillMaxWidth().padding(top = 16.dp)) {
        Card {
            Column(modifier = Modifier.padding(12.dp)) {
                Text(
                    if (esControl) {
                        "Toca para hacer clic, doble toque para doble clic, mantén presionado para clic derecho. " +
                            "Pellizca para acercar/alejar la vista."
                    } else {
                        "Se actualiza pidiéndole un frame nuevo al companion — no es video en vivo."
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(top = 10.dp)
                        .height(340.dp)
                        .clip(RoundedCornerShape(12.dp))
                        .background(Color.Black),
                    contentAlignment = Alignment.Center,
                ) {
                    if (bitmap != null) {
                        Image(
                            bitmap = bitmap,
                            contentDescription = "Última captura de la pantalla remota",
                            contentScale = ContentScale.Fit,
                            modifier = Modifier
                                .fillMaxSize()
                                .onSizeChanged { tamanoElemento = it }
                                .graphicsLayer(scaleX = zoom, scaleY = zoom, translationX = pan.x, translationY = pan.y)
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
                                    } else {
                                        Modifier
                                    },
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
                                ),
                        )
                    } else {
                        CircularProgressIndicator(color = Color.White)
                    }
                }

                Row(
                    modifier = Modifier.fillMaxWidth().padding(top = 10.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        "Frames recibidos: ${sesion.framesCount}",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    OutlinedButton(onClick = onActualizar, enabled = !cargandoFrame) {
                        if (cargandoFrame) {
                            CircularProgressIndicator(modifier = Modifier.size(14.dp), strokeWidth = 2.dp)
                        } else {
                            Text("Actualizar")
                        }
                    }
                }

                errorMensaje?.let {
                    Text(
                        it,
                        color = MaterialTheme.colorScheme.error,
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.padding(top = 6.dp),
                    )
                }
            }
        }

        if (esControl) {
            Spacer(modifier = Modifier.height(16.dp))
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(modifier = Modifier.padding(12.dp)) {
                    Text("Mouse y scroll", style = MaterialTheme.typography.titleSmall)
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedButton(
                            enabled = !enviandoInput,
                            onClick = {
                                onPointer(
                                    RemotePointerCommand(
                                        x = frame.width / 2, y = frame.height / 2, accion = "right_click",
                                    )
                                )
                            },
                        ) { Text("Clic derecho") }
                        OutlinedButton(
                            enabled = !enviandoInput,
                            onClick = {
                                onPointer(
                                    RemotePointerCommand(
                                        x = frame.width / 2, y = frame.height / 2,
                                        accion = "scroll", deltaY = 420,
                                    )
                                )
                            },
                        ) { Text("Scroll ↑") }
                        OutlinedButton(
                            enabled = !enviandoInput,
                            onClick = {
                                onPointer(
                                    RemotePointerCommand(
                                        x = frame.width / 2, y = frame.height / 2,
                                        accion = "scroll", deltaY = -420,
                                    )
                                )
                            },
                        ) { Text("Scroll ↓") }
                    }
                }
            }
            Spacer(modifier = Modifier.height(12.dp))
            BarraTeclado(enviando = enviandoInput, onTexto = onTexto, onTecla = onTecla)
        }
    }
}

@Composable
private fun BarraTeclado(
    enviando: Boolean,
    onTexto: (String) -> Unit,
    onTecla: (String, List<String>) -> Unit,
) {
    var texto by remember { mutableStateOf("") }

    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(12.dp)) {
            Text("Escribir en el equipo remoto", style = MaterialTheme.typography.titleSmall)
            Text(
                "Se envía carácter por carácter al companion, como si lo tipearas ahí.",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(bottom = 8.dp),
            )
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

            Text(
                "Teclas especiales",
                style = MaterialTheme.typography.labelMedium,
                modifier = Modifier.padding(top = 14.dp, bottom = 6.dp),
            )
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                TECLAS_ESPECIALES.chunked(4).forEach { fila ->
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        fila.forEach { tecla ->
                            OutlinedButton(
                            onClick = { onTecla(tecla.valor, emptyList()) },
                                enabled = !enviando,
                                contentPadding = PaddingValues(horizontal = 12.dp, vertical = 8.dp),
                                modifier = Modifier.semantics { contentDescription = tecla.titulo },
                            ) {
                                Text(tecla.etiqueta)
                            }
                        }
                    }
                }
            }

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
