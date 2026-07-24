@file:OptIn(ExperimentalMaterial3Api::class)

package cc.edecan.app.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.OpenableColumns
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.Image
import androidx.compose.foundation.interaction.collectIsDraggedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.core.content.FileProvider
import androidx.core.net.toUri
import cc.edecan.app.ui.components.EmptyState
import cc.edecan.app.ui.theme.EdecanColors
import cc.edecan.app.vm.ChatViewModel
import cc.edecan.app.vm.ArchivoSubidaLocal
import cc.edecan.app.vm.ConfirmacionPendiente
import cc.edecan.app.vm.EstadoAdjunto
import cc.edecan.app.vm.EstadoEntrega
import cc.edecan.app.vm.MensajeUi
import cc.edecan.app.vm.SessionViewModel
import cc.edecan.app.vm.tituloEstado
import cc.edecan.shared.ArtifactRef
import cc.edecan.shared.ChatAction
import cc.edecan.shared.ChatBlock
import cc.edecan.shared.DownloadedArtifact
import cc.edecan.shared.EdecanApi
import java.io.File
import java.net.URI
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Superficie principal "Edecan" — lista de mensajes, input y envío hacia
 * `POST /v1/conversations/{id}/messages` (SSE) apendeando `text_delta` a
 * medida que llega, más un indicador mientras el agente usa una
 * herramienta (`tool_start`/`tool_end`). Lógica real en [ChatViewModel];
 * esta pantalla solo dibuja su estado. Mismo contenido que `ChatView.swift`
 * (iOS).
 */
@Composable
fun ChatScreen(
    sessionViewModel: SessionViewModel = viewModel(),
    chatViewModel: ChatViewModel = viewModel(),
    onOpenVoice: () -> Unit = {},
    onOpenScreen: (String) -> Boolean = { false },
    solicitudInicial: String? = null,
    onSolicitudConsumida: () -> Unit = {},
) {
    val chatState by chatViewModel.uiState.collectAsState()
    var historialAbierto by remember { mutableStateOf(false) }
    var renombrandoChat by remember { mutableStateOf(false) }
    var tituloNuevo by remember { mutableStateOf("") }
    var artefactoDescargandoId by remember { mutableStateOf<String?>(null) }
    var errorArtefacto by remember { mutableStateOf<String?>(null) }
    var previews by remember { mutableStateOf<Map<String, VistaPreviaPrivada>>(emptyMap()) }
    var previewsCargando by remember { mutableStateOf<Set<String>>(emptySet()) }
    var securePreviewTarget by remember { mutableStateOf<SecurePreviewTarget?>(null) }
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    val coroutineScope = rememberCoroutineScope()
    val listState = rememberLazyListState()
    val listaArrastrada by listState.interactionSource.collectIsDraggedAsState()
    var cantidadMensajesAnterior by remember { mutableStateOf(0) }
    var conversacionAnterior by remember { mutableStateOf<String?>(null) }
    val selectorArchivo = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        val api = sessionViewModel.api
        if (uri != null && api != null) {
            coroutineScope.launch {
                try {
                    val local = withContext(Dispatchers.IO) {
                        prepararArchivoSeleccionado(context.applicationContext, uri)
                    }
                    chatViewModel.subirAdjunto(local, api)
                } catch (error: Exception) {
                    errorArtefacto = "No pude preparar ese archivo: ${error.message ?: "error desconocido"}"
                }
            }
        }
    }

    securePreviewTarget?.let { target ->
        SecurePreviewDialog(
            target = target,
            api = sessionViewModel.api,
            onDismiss = { securePreviewTarget = null },
        )
    }

    LaunchedEffect(sessionViewModel.api) {
        sessionViewModel.api?.let(chatViewModel::cargar)
    }

    DisposableEffect(lifecycleOwner, sessionViewModel.api) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) {
                sessionViewModel.api?.let(chatViewModel::reanudarAlVolver)
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }

    LaunchedEffect(
        chatState.conversationId,
        chatState.mensajes.size,
        chatState.mensajes.lastOrNull(),
    ) {
        val cantidad = chatState.mensajes.size
        if (cantidad == 0) {
            cantidadMensajesAnterior = 0
            conversacionAnterior = chatState.conversationId
            return@LaunchedEffect
        }
        val cambioConversacion = chatState.conversationId != conversacionAnterior
        val agregoMensaje = cambioConversacion || cantidad != cantidadMensajesAnterior
        val ultimoVisible = listState.layoutInfo.visibleItemsInfo.lastOrNull()?.index
        if (agregoMensaje || debeSeguirDelta(ultimoVisible, cantidad, listaArrastrada)) {
            if (agregoMensaje) listState.animateScrollToItem(cantidad - 1)
            else listState.scrollToItem(cantidad - 1)
        }
        cantidadMensajesAnterior = cantidad
        conversacionAnterior = chatState.conversationId
    }

    LaunchedEffect(solicitudInicial) {
        val solicitud = solicitudInicial?.trim().orEmpty()
        if (solicitud.isNotEmpty()) {
            onSolicitudConsumida()
            // Crear siempre vuelve al mismo hilo como borrador revisable.
            chatViewModel.actualizarBorrador(solicitud)
        }
    }

    if (renombrandoChat) {
        AlertDialog(
            onDismissRequest = { renombrandoChat = false },
            title = { Text("Renombrar conversación") },
            text = {
                OutlinedTextField(
                    value = tituloNuevo,
                    onValueChange = { tituloNuevo = it.take(120) },
                    label = { Text("Nombre") },
                    singleLine = true,
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    val id = chatState.conversationId
                    val api = sessionViewModel.api
                    if (id != null && api != null) {
                        chatViewModel.renombrarConversacion(id, tituloNuevo, api)
                    }
                    renombrandoChat = false
                }) { Text("Guardar") }
            },
            dismissButton = {
                TextButton(onClick = { renombrandoChat = false }) { Text("Cancelar") }
            },
        )
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("Edecán")
                        chatState.conversationId?.let { id ->
                            val titulo = chatState.tituloConversacion?.takeIf { it.isNotBlank() } ?: "Conversación"
                            Text(
                                "$titulo · Chat ${id.take(8).uppercase()}",
                                style = MaterialTheme.typography.labelSmall,
                                maxLines = 1,
                            )
                        }
                    }
                },
                actions = {
                    Box {
                        IconButton(
                            onClick = { historialAbierto = true },
                            enabled = !chatState.enviando,
                            modifier = Modifier.semantics { contentDescription = "Abrir historial de chats" },
                        ) { Text("☰", style = MaterialTheme.typography.titleLarge) }
                        DropdownMenu(
                            expanded = historialAbierto,
                            onDismissRequest = { historialAbierto = false },
                        ) {
                            DropdownMenuItem(
                                text = { Text("＋ Chat nuevo") },
                                onClick = {
                                    historialAbierto = false
                                    chatViewModel.nuevoChat()
                                },
                            )
                            if (chatState.conversationId != null) {
                                DropdownMenuItem(
                                    text = { Text("Renombrar chat actual") },
                                    onClick = {
                                        historialAbierto = false
                                        tituloNuevo = chatState.tituloConversacion.orEmpty()
                                        renombrandoChat = true
                                    },
                                )
                            }
                            chatState.conversaciones.forEach { conversation ->
                                DropdownMenuItem(
                                    text = {
                                        Text(
                                            conversation.title?.takeIf { it.isNotBlank() } ?: "Conversación",
                                            maxLines = 1,
                                        )
                                    },
                                    onClick = {
                                        historialAbierto = false
                                        sessionViewModel.api?.let {
                                            chatViewModel.seleccionarConversacion(conversation.id, it)
                                        }
                                    },
                                )
                            }
                        }
                    }
                    IconButton(
                        onClick = onOpenVoice,
                        modifier = Modifier.semantics { contentDescription = "Hablar con Edecán" },
                    ) {
                        Text("🎙️", style = MaterialTheme.typography.titleLarge)
                    }
                },
            )
        },
    ) { padding ->
        Column(modifier = Modifier.padding(padding).fillMaxSize().imePadding()) {
            Box(modifier = Modifier.weight(1f)) {
                if (chatState.cargandoHistorial && chatState.mensajes.isEmpty()) {
                    CircularProgressIndicator(modifier = Modifier.align(Alignment.Center))
                } else if (chatState.mensajes.isEmpty()) {
                    EmptyState(
                        emoji = "💬",
                        titulo = "Empieza una conversación",
                        descripcion = "Escríbele a Edecán abajo — puede agendar, buscar en tu correo, " +
                            "resumir finanzas y más, con tu confirmación cuando haga falta.",
                        etiquetaRoadmap = null,
                    )
                } else {
                    LazyColumn(
                        state = listState,
                        contentPadding = PaddingValues(16.dp),
                        verticalArrangement = Arrangement.spacedBy(12.dp),
                        modifier = Modifier.fillMaxSize(),
                    ) {
                        items(chatState.mensajes, key = { it.id }) { mensaje ->
                            BurbujaMensaje(
                                mensaje = mensaje,
                                api = sessionViewModel.api,
                                artefactoDescargandoId = artefactoDescargandoId,
                                previews = previews,
                                previewsCargando = previewsCargando,
                                onCargarPreview = { bloque ->
                                    val api = sessionViewModel.api ?: return@BurbujaMensaje
                                    val artifact = bloque.artifact
                                    if (artifact.fileId in previewsCargando || artifact.fileId in previews) {
                                        return@BurbujaMensaje
                                    }
                                    previewsCargando = previewsCargando + artifact.fileId
                                    errorArtefacto = null
                                    coroutineScope.launch {
                                        try {
                                            val mime = artifact.mime?.lowercase().orEmpty()
                                            val kind = bloque.mediaKind.lowercase()
                                            if (kind !in setOf("image", "video", "audio") ||
                                                !mime.startsWith("$kind/")
                                            ) {
                                                errorArtefacto = "No mostré ${artifact.filename}: el tipo privado " +
                                                    "del archivo no coincide con el preview solicitado."
                                            } else {
                                                val preview = if (kind == "image") {
                                                    VistaPreviaPrivada.Imagen(api.previewArtifact(artifact))
                                                } else {
                                                    VistaPreviaPrivada.Stream(
                                                        artifact = artifact,
                                                        api = api,
                                                    )
                                                }
                                                previews = previews + (artifact.fileId to preview)
                                            }
                                        } catch (error: Exception) {
                                            errorArtefacto = "No se pudo cargar ${artifact.filename}: " +
                                                (error.message ?: "error desconocido")
                                        } finally {
                                            previewsCargando = previewsCargando - artifact.fileId
                                        }
                                    }
                                },
                                onAction = { action ->
                                    when (action) {
                                        is ChatAction.OpenUrl -> {
                                            if (esUrlPublicaSegura(action.url)) {
                                                securePreviewTarget = SecurePreviewTarget.PublicUrl(action.url)
                                            } else {
                                                errorArtefacto = "No abrí ese enlace porque no es una URL pública segura."
                                            }
                                        }
                                        is ChatAction.OpenScreen -> {
                                            if (!onOpenScreen(action.screen)) {
                                                errorArtefacto = "La pantalla «${action.label}» todavía no está disponible aquí."
                                            }
                                        }
                                        is ChatAction.PrefillMessage -> {
                                            // Deliberado: sugerir no equivale a ejecutar. La persona
                                            // todavía debe revisar el texto y pulsar Enviar.
                                            chatViewModel.actualizarBorrador(action.message)
                                        }
                                        is ChatAction.Unknown -> Unit
                                    }
                                },
                                onAbrirArtefacto = { artefacto ->
                                    securePreviewTarget = SecurePreviewTarget.Artifact(artefacto)
                                },
                                onReintentar = {
                                    sessionViewModel.api?.let { chatViewModel.reintentarMensaje(mensaje.id, it) }
                                },
                            )
                        }
                    }
                }
            }

            chatState.confirmacionPendiente?.let { pendiente ->
                TarjetaConfirmacion(
                    pendiente = pendiente,
                    onAprobar = {
                        sessionViewModel.api?.let { chatViewModel.confirmar(aprobado = true, api = it) }
                    },
                    onRechazar = {
                        sessionViewModel.api?.let { chatViewModel.confirmar(aprobado = false, api = it) }
                    },
                )
            }

            if (chatState.recuperandoTurno) {
                Row(
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp),
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    CircularProgressIndicator(modifier = Modifier.size(18.dp), strokeWidth = 2.dp)
                    Text(
                        "Edecán sigue trabajando. Recuperando la respuesta…",
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
            }

            (errorArtefacto ?: chatState.errorMensaje)?.let { error ->
                Text(
                    error,
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
                )
            }

            AdjuntosComposer(
                adjuntos = chatState.adjuntosComposer,
                onQuitar = chatViewModel::quitarAdjunto,
                onReintentar = { localId ->
                    sessionViewModel.api?.let { chatViewModel.reintentarAdjunto(localId, it) }
                },
            )

            BarraDeEntrada(
                texto = chatState.borrador,
                onTextoCambia = chatViewModel::actualizarBorrador,
                habilitado = !chatState.enviando &&
                    chatState.confirmacionPendiente == null &&
                    chatState.adjuntosComposer.none { it.estado != EstadoAdjunto.LISTO } &&
                    (chatState.borrador.isNotBlank() || chatState.adjuntosComposer.any { it.estado == EstadoAdjunto.LISTO }),
                onAdjuntar = { selectorArchivo.launch(arrayOf("*/*")) },
                onPrefill = chatViewModel::actualizarBorrador,
                onEnviar = {
                    val api = sessionViewModel.api
                    if (api == null) {
                        return@BarraDeEntrada
                    }
                    chatViewModel.enviar(chatState.borrador, api)
                },
            )
        }
    }
}

/**
 * Advertencias específicas por herramienta, en lenguaje llano, ADEMÁS del
 * JSON/argumentos de abajo — mismo criterio y mismo texto (ES) que
 * `ADVERTENCIAS_POR_HERRAMIENTA` en
 * `apps/web/src/components/chat/ConfirmationCard.tsx` (hallazgo de auditoría
 * "riesgo-legal-tos": una tarjeta genérica no le da a quien aprueba ninguna
 * pista concreta de qué mirar antes de confirmar). `usar_computadora`
 * (control remoto de pantalla/mouse/teclado,
 * `packages/toolkit/edecan_toolkit/computadora.py`) es la más importante:
 * a diferencia de tools que reciben una URL que un guardrail de código puede
 * inspeccionar, esta actúa por coordenadas de pantalla y pulsaciones de
 * teclado — no hay nada que el código pueda revisar por su cuenta, así que
 * esta advertencia y el juicio de quien aprueba son la única defensa
 * consciente del contenido en pantalla en este punto. Extensible: las tools
 * sin entrada acá quedan exactamente igual que antes de este cambio (sin
 * advertencia extra).
 */
private val ADVERTENCIAS_POR_HERRAMIENTA: Map<String, String> = mapOf(
    "usar_computadora" to (
        "Esto va a mover el mouse, escribir o mirar la pantalla de tu computadora de verdad. " +
            "Revisa la app, el destino y el contenido exactos antes de aprobar. Puede continuar una " +
            "tarea en una sesión que ya abriste, incluida una publicación en LinkedIn, pero no debe " +
            "capturar contraseñas, hacer scraping o contacto masivo, ni completar un pago sin el flujo " +
            "específico que tú revisaste."
        ),
)

/** Traduce contratos internos a la acción que la persona está autorizando.
 * El fallback también elimina guiones bajos, así una tool nueva nunca obliga
 * a entender nombres de API para tomar una decisión sensible. */
private val ACCIONES_POR_HERRAMIENTA: Map<String, String> = mapOf(
    "acceder_codigo_local" to "editar el código local de Edecán",
    "ads_preparar_campana" to "preparar esta campaña publicitaria",
    "casa_controlar" to "controlar este dispositivo de tu casa",
    "configurar_credencial" to "guardar esta conexión privada",
    "enviar_correo" to "enviar este correo",
    "enviar_mensaje" to "enviar este mensaje",
    "gestionar_autorreparacion_local" to "reparar el núcleo local de Edecán",
    "gestionar_automatizacion" to "guardar esta automatización",
    "instalar_skill" to "instalar esta capacidad de terceros",
    "preparar_nomina" to "preparar esta nómina",
    "preparar_orden" to "preparar esta orden simulada",
    "preparar_pago" to "preparar este borrador de pago",
    "preparar_reserva" to "preparar esta reserva",
    "publicar_social" to "publicar este contenido",
    "reparar_con_skill_local" to "reparar Edecán con esta capacidad local",
    "usar_computadora" to "controlar tu computadora",
    "vehiculo_controlar" to "controlar este vehículo",
)

private fun accionEnLenguajeClaro(nombre: String): String =
    ACCIONES_POR_HERRAMIENTA[nombre] ?: nombre.replace('_', ' ')

/** Tarjeta Aprobar/Rechazar de una herramienta `dangerous` pendiente
 * (evento SSE `confirmation_required`, `ARCHITECTURE.md` §10.7/§10.12) —
 * reemplaza el aviso de solo-texto del esqueleto v1: ahora el turno se
 * puede resolver in-app (`POST /v1/conversations/{id}/confirm`) sin
 * depender del panel web. */
@Composable
internal fun TarjetaConfirmacion(
    pendiente: ConfirmacionPendiente,
    onAprobar: () -> Unit,
    onRechazar: () -> Unit,
) {
    val advertenciaEspecifica = ADVERTENCIAS_POR_HERRAMIENTA[pendiente.nombre]
    Card(
        modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
        colors = CardDefaults.cardColors(containerColor = EdecanColors.Morado.copy(alpha = 0.10f)),
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Text(
                "Edecán necesita tu permiso para ${accionEnLenguajeClaro(pendiente.nombre)}",
                style = MaterialTheme.typography.titleSmall,
            )
            advertenciaEspecifica?.let { advertencia ->
                Text(
                    advertencia,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                    modifier = Modifier.padding(top = 6.dp),
                )
            }
            if (pendiente.argumentos.isNotBlank()) {
                Text(
                    pendiente.argumentos,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 8.dp),
                )
            }
            Row(modifier = Modifier.padding(top = 12.dp)) {
                OutlinedButton(onClick = onRechazar) { Text("Cancelar") }
                Spacer(modifier = Modifier.padding(start = 8.dp))
                Button(
                    onClick = onAprobar,
                    colors = ButtonDefaults.buttonColors(containerColor = EdecanColors.Morado),
                ) { Text("Permitir") }
            }
        }
    }
}

@Composable
private fun BurbujaMensaje(
    mensaje: MensajeUi,
    api: EdecanApi?,
    artefactoDescargandoId: String?,
    previews: Map<String, VistaPreviaPrivada>,
    previewsCargando: Set<String>,
    onCargarPreview: (ChatBlock.Media) -> Unit,
    onAction: (ChatAction) -> Unit,
    onAbrirArtefacto: (ArtifactRef) -> Unit,
    onReintentar: () -> Unit,
) {
    val esUsuario = mensaje.rol == MensajeUi.Rol.USUARIO
    val context = LocalContext.current
    Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = if (esUsuario) Arrangement.End else Arrangement.Start) {
        Column(
            verticalArrangement = Arrangement.spacedBy(8.dp),
            modifier = Modifier
                .widthIn(max = 300.dp)
                .clip(RoundedCornerShape(18.dp))
                .background(if (esUsuario) EdecanColors.Morado else MaterialTheme.colorScheme.surfaceVariant)
                .padding(horizontal = 14.dp, vertical = 10.dp),
        ) {
            if (mensaje.texto.isEmpty() && mensaje.enProgreso) {
                CircularProgressIndicator(
                    modifier = Modifier.size(18.dp),
                    color = if (esUsuario) Color.White else MaterialTheme.colorScheme.primary,
                    strokeWidth = 2.dp,
                )
            } else if (mensaje.texto.isNotEmpty()) {
                SelectionContainer {
                    Text(
                        markdownParaChat(mensaje.texto),
                        color = if (esUsuario) Color.White else MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                TextButton(
                    onClick = {
                        val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                        clipboard.setPrimaryClip(
                            ClipData.newPlainText("Mensaje de Edecán", textoPlanoParaCopiar(mensaje.texto)),
                        )
                    },
                    contentPadding = PaddingValues(horizontal = 0.dp),
                ) {
                    Text(
                        "Copiar",
                        style = MaterialTheme.typography.labelSmall,
                        color = if (esUsuario) Color.White else MaterialTheme.colorScheme.primary,
                    )
                }
            }
            if (esUsuario && mensaje.adjuntos.isNotEmpty()) {
                mensaje.adjuntos.forEach { adjunto ->
                    if (adjunto.mime?.lowercase()?.startsWith("image/") == true) {
                        ImagenAdjuntaChat(adjunto, api)
                    } else {
                        Text(
                            "📎 ${adjunto.filename}",
                            style = MaterialTheme.typography.bodySmall,
                            color = Color.White.copy(alpha = 0.86f),
                            maxLines = 1,
                        )
                    }
                }
            }
            if (esUsuario) {
                when (mensaje.estadoEntrega) {
                    EstadoEntrega.ENVIANDO -> Text(
                        "Enviando…",
                        style = MaterialTheme.typography.labelSmall,
                        color = Color.White.copy(alpha = 0.72f),
                    )
                    EstadoEntrega.FALLIDO -> TextButton(onClick = onReintentar) {
                        Text("No se envió · Reintentar", color = Color.White)
                    }
                    EstadoEntrega.ENTREGADO, null -> Unit
                }
            }
            if (!esUsuario) {
                mensaje.trabajo?.let { ProgresoTrabajo(it) }
                BloquesRicosMensaje(
                    bloques = mensaje.bloques,
                    previews = previews,
                    previewsCargando = previewsCargando,
                    onCargarPreview = onCargarPreview,
                    onAction = onAction,
                )
                mensaje.artefactos.forEach { artefacto ->
                    OutlinedButton(
                        onClick = { onAbrirArtefacto(artefacto) },
                        enabled = artefactoDescargandoId == null,
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        if (artefactoDescargandoId == artefacto.fileId) {
                            CircularProgressIndicator(modifier = Modifier.size(15.dp), strokeWidth = 2.dp)
                            Spacer(modifier = Modifier.padding(start = 7.dp))
                        } else {
                            Text("⌕ ")
                        }
                        Text(artefacto.filename, maxLines = 1, modifier = Modifier.weight(1f))
                        Text(" Ver")
                    }
                }
            }
        }
    }
}

@Composable
private fun ImagenAdjuntaChat(artifact: ArtifactRef, api: EdecanApi?) {
    var image by remember(artifact.fileId) { mutableStateOf<androidx.compose.ui.graphics.ImageBitmap?>(null) }
    var cargaFinalizada by remember(artifact.fileId) { mutableStateOf(false) }
    LaunchedEffect(artifact.fileId, api) {
        if (image != null || cargaFinalizada) return@LaunchedEffect
        if (api == null) {
            cargaFinalizada = true
            return@LaunchedEffect
        }
        runCatching { api.downloadArtifact(artifact).bytes }.getOrNull()?.let { bytes ->
            image = withContext(Dispatchers.Default) { decodificarImagenAcotada(bytes) }
        }
        cargaFinalizada = true
    }
    Box(
        modifier = Modifier.fillMaxWidth().size(width = 272.dp, height = 190.dp)
            .clip(RoundedCornerShape(15.dp))
            .background(Color.White.copy(alpha = 0.10f)),
        contentAlignment = Alignment.Center,
    ) {
        image?.let {
            Image(
                bitmap = it,
                contentDescription = "Imagen adjunta: ${artifact.filename}",
                contentScale = ContentScale.Crop,
                modifier = Modifier.fillMaxSize(),
            )
        } ?: if (cargaFinalizada) {
            Text(
                "Imagen no disponible",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        } else {
            CircularProgressIndicator(modifier = Modifier.size(22.dp), color = Color.White)
        }
    }
}

@Composable
private fun ProgresoTrabajo(trabajo: cc.edecan.app.vm.TrabajoUi) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(
            modifier = Modifier.padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(7.dp),
        ) {
            Text(
                trabajo.tituloEstado,
                style = MaterialTheme.typography.labelLarge,
                fontWeight = FontWeight.Bold,
            )
            if (trabajo.segundos > 0) {
                Text(
                    duracionTrabajo(trabajo.segundos),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            trabajo.pasos.forEach { paso ->
                Row(verticalAlignment = Alignment.Top) {
                    Text(
                        when (paso.estado) {
                            cc.edecan.app.vm.PasoTrabajoUi.Estado.EJECUTANDO -> "◌"
                            cc.edecan.app.vm.PasoTrabajoUi.Estado.COMPLETADO -> "✓"
                            cc.edecan.app.vm.PasoTrabajoUi.Estado.ERROR -> "!"
                        },
                        color = if (paso.estado == cc.edecan.app.vm.PasoTrabajoUi.Estado.ERROR) {
                            MaterialTheme.colorScheme.error
                        } else MaterialTheme.colorScheme.primary,
                    )
                    Column(modifier = Modifier.padding(start = 8.dp)) {
                        Text(
                            accionEnLenguajeClaro(paso.nombre).replaceFirstChar { it.uppercase() },
                            style = MaterialTheme.typography.bodySmall,
                            fontWeight = FontWeight.SemiBold,
                        )
                        paso.detalle?.takeIf { it.isNotBlank() }?.let { detalle ->
                            Text(
                                detalle,
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                maxLines = 3,
                            )
                        }
                    }
                }
            }
            trabajo.missionError?.takeIf { it.isNotBlank() }?.let { error ->
                Text(
                    error,
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.error,
                )
            }
        }
    }
}

private fun duracionTrabajo(segundos: Int): String {
    val minutos = segundos / 60
    val resto = segundos % 60
    return if (minutos > 0) "${minutos}m ${resto}s" else "${resto}s"
}

@Composable
private fun AdjuntosComposer(
    adjuntos: List<cc.edecan.app.vm.AdjuntoComposerUi>,
    onQuitar: (String) -> Unit,
    onReintentar: (String) -> Unit,
) {
    if (adjuntos.isEmpty()) return
    Column(
        verticalArrangement = Arrangement.spacedBy(6.dp),
        modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp),
    ) {
        adjuntos.forEach { adjunto ->
            Card(modifier = Modifier.fillMaxWidth()) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 7.dp),
                ) {
                    val preview = remember(adjunto.previewBytes) {
                        adjunto.previewBytes?.let { decodificarImagenAcotada(it, maxEdge = 420) }
                    }
                    if (preview != null) {
                        Box(
                            modifier = Modifier.size(52.dp)
                                .clip(RoundedCornerShape(12.dp))
                                .background(MaterialTheme.colorScheme.surfaceVariant),
                            contentAlignment = Alignment.Center,
                        ) {
                            Image(
                                bitmap = preview,
                                contentDescription = "Vista previa de ${adjunto.filename}",
                                contentScale = ContentScale.Crop,
                                modifier = Modifier.fillMaxSize(),
                            )
                            if (adjunto.estado == EstadoAdjunto.SUBIENDO) {
                                Box(
                                    modifier = Modifier.fillMaxSize()
                                        .background(Color.Black.copy(alpha = 0.24f)),
                                    contentAlignment = Alignment.Center,
                                ) {
                                    CircularProgressIndicator(
                                        modifier = Modifier.size(19.dp),
                                        strokeWidth = 2.dp,
                                        color = Color.White,
                                    )
                                }
                            }
                        }
                        Spacer(modifier = Modifier.padding(start = 8.dp))
                    } else if (adjunto.estado == EstadoAdjunto.SUBIENDO) {
                        CircularProgressIndicator(modifier = Modifier.size(16.dp), strokeWidth = 2.dp)
                        Spacer(modifier = Modifier.padding(start = 8.dp))
                    } else {
                        Text(if (adjunto.estado == EstadoAdjunto.LISTO) "📎" else "⚠️")
                        Spacer(modifier = Modifier.padding(start = 6.dp))
                    }
                    Column(modifier = Modifier.weight(1f)) {
                        Text(adjunto.filename, style = MaterialTheme.typography.bodySmall, maxLines = 1)
                        Text(
                            when (adjunto.estado) {
                                EstadoAdjunto.SUBIENDO -> "Subiendo de forma privada…"
                                EstadoAdjunto.LISTO -> "Listo para enviar"
                                EstadoAdjunto.ERROR -> adjunto.error ?: "No se pudo subir"
                            },
                            style = MaterialTheme.typography.labelSmall,
                            color = if (adjunto.estado == EstadoAdjunto.ERROR) {
                                MaterialTheme.colorScheme.error
                            } else MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 1,
                        )
                    }
                    if (adjunto.estado == EstadoAdjunto.ERROR) {
                        TextButton(onClick = { onReintentar(adjunto.localId) }) { Text("Reintentar") }
                    }
                    IconButton(onClick = { onQuitar(adjunto.localId) }) { Text("×") }
                }
            }
        }
    }
}

/** Aviso corto de "Edecán está usando «herramienta»" mientras el turno del
 * agente ejecuta una tool (evento SSE `tool_start` sin su `tool_end`
 * todavía — `docs/api.md` §"Conversaciones y chat (SSE)"). */
@Composable
private fun IndicadorHerramienta(nombre: String, segundos: Int, mensaje: String?) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(14.dp))
            .background(MaterialTheme.colorScheme.surfaceVariant)
            .padding(horizontal = 14.dp, vertical = 8.dp),
    ) {
        CircularProgressIndicator(modifier = Modifier.size(14.dp), strokeWidth = 2.dp)
        Spacer(modifier = Modifier.padding(start = 8.dp))
        Text(
            buildString {
                append(mensaje ?: accionEnLenguajeClaro(nombre).replaceFirstChar { it.uppercase() })
                if (segundos > 0) {
                    val minutos = segundos / 60
                    val resto = segundos % 60
                    append(if (minutos > 0) " · ${minutos}m ${resto}s" else " · ${resto}s")
                } else append("…")
            },
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun BarraDeEntrada(
    texto: String,
    onTextoCambia: (String) -> Unit,
    habilitado: Boolean,
    onAdjuntar: () -> Unit,
    onPrefill: (String) -> Unit,
    onEnviar: () -> Unit,
) {
    var menuAbierto by remember { mutableStateOf(false) }
    val focusManager = LocalFocusManager.current
    Row(
        verticalAlignment = Alignment.Bottom,
        modifier = Modifier.fillMaxWidth().padding(12.dp),
    ) {
        Box {
            IconButton(
                onClick = { menuAbierto = true },
                modifier = Modifier.semantics { contentDescription = "Añadir al mensaje" },
            ) { Text("＋", style = MaterialTheme.typography.headlineSmall) }
            DropdownMenu(expanded = menuAbierto, onDismissRequest = { menuAbierto = false }) {
                PRESETS_CREACION.forEach { preset ->
                    DropdownMenuItem(
                        text = { Text(preset.label) },
                        leadingIcon = { Text(preset.emoji) },
                        onClick = {
                            menuAbierto = false
                            // Prefill únicamente: nunca envía ni ejecuta sin
                            // que la persona revise y pulse Enviar.
                            onPrefill(preset.message)
                        },
                    )
                }
                DropdownMenuItem(
                    text = { Text("Subir archivo") },
                    leadingIcon = { Text("📎") },
                    onClick = { menuAbierto = false; onAdjuntar() },
                )
            }
        }
        OutlinedTextField(
            value = texto,
            onValueChange = onTextoCambia,
            placeholder = { Text("Escríbele a Edecán…") },
            modifier = Modifier.weight(1f),
            maxLines = 4,
            keyboardOptions = KeyboardOptions(
                capitalization = KeyboardCapitalization.Sentences,
                imeAction = ImeAction.Send,
            ),
            keyboardActions = KeyboardActions(
                onSend = {
                    if (habilitado) {
                        onEnviar()
                        focusManager.clearFocus()
                    }
                },
            ),
        )
        IconButton(
            onClick = {
                onEnviar()
                focusManager.clearFocus()
            },
            enabled = habilitado,
        ) {
            // Emoji en vez de `androidx.compose.material.icons` a propósito
            // — cero dependencias adicionales por un solo glifo (ver
            // libs.versions.toml: este módulo no declara material-icons).
            Text(
                text = "➤",
                style = MaterialTheme.typography.headlineSmall,
                color = if (habilitado) EdecanColors.Morado else MaterialTheme.colorScheme.outline,
            )
        }
    }
}

/** Sigue el delta solo si la persona continúa cerca del final. Un gesto
 * activo o estar leyendo mensajes antiguos desactiva el seguimiento hasta
 * que vuelva al final; los mensajes nuevos se manejan aparte. */
internal fun debeSeguirDelta(
    ultimoVisible: Int?,
    totalMensajes: Int,
    usuarioArrastrando: Boolean,
): Boolean {
    if (totalMensajes <= 0 || usuarioArrastrando) return false
    return ultimoVisible == null || ultimoVisible >= totalMensajes - 2
}

private data class PresetCreacion(val emoji: String, val label: String, val message: String)

private val PRESETS_CREACION = listOf(
    PresetCreacion("📄", "Documento", "Ayúdame a crear un documento. Primero pregúntame lo necesario y luego prepáralo aquí."),
    PresetCreacion("🧾", "PDF", "Ayúdame a crear un PDF. Primero pregúntame el objetivo y el contenido que debe llevar."),
    PresetCreacion("📊", "Presentación", "Ayúdame a crear una presentación. Pregúntame para quién es y qué quiero lograr."),
    PresetCreacion("🌐", "Sitio", "Ayúdame a crear un sitio web. Primero entiende mi negocio, público y objetivo."),
    PresetCreacion("📱", "App", "Ayúdame a crear una app. Primero aclaremos el problema, las personas y el flujo principal."),
    PresetCreacion("✨", "Post", "Ayúdame a crear una publicación. Pregúntame la red, el tema, el tono y el objetivo."),
)

internal fun prepararArchivoSeleccionado(context: Context, uri: Uri): ArchivoSubidaLocal {
    val resolver = context.contentResolver
    val mime = resolver.getType(uri) ?: "application/octet-stream"
    var filename = "archivo"
    var declaredSize: Long? = null
    resolver.query(
        uri,
        arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE),
        null,
        null,
        null,
    )?.use { cursor ->
        if (cursor.moveToFirst()) {
            cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME).takeIf { it >= 0 }?.let { index ->
                filename = cursor.getString(index)?.takeIf { it.isNotBlank() } ?: filename
            }
            cursor.getColumnIndex(OpenableColumns.SIZE).takeIf { it >= 0 && !cursor.isNull(it) }?.let { index ->
                declaredSize = cursor.getLong(index).takeIf { it >= 0 }
            }
        }
    }
    declaredSize?.let { if (it > MAX_MOBILE_UPLOAD_BYTES) error("El archivo supera el límite móvil de 25 MB") }

    val pendingDir = File(context.cacheDir, "pending_uploads").apply {
        if (!exists() && !mkdirs()) error("No pude preparar el almacenamiento temporal")
        if (!isDirectory) error("No pude preparar el almacenamiento temporal")
    }
    // Recupera espacio de una selección que quedó huérfana por un cierre
    // abrupto. Los archivos del ViewModel activo son mucho más recientes.
    val staleBefore = System.currentTimeMillis() - PENDING_UPLOAD_MAX_AGE_MS
    pendingDir.listFiles()?.filter { it.isFile && it.lastModified() < staleBefore }?.forEach(File::delete)

    val staged = File.createTempFile("adjunto-", ".upload", pendingDir)
    try {
        resolver.openInputStream(uri)?.use { input ->
            staged.outputStream().buffered().use { output ->
                val buffer = ByteArray(64 * 1024)
                var total = 0L
                while (true) {
                    val read = input.read(buffer)
                    if (read < 0) break
                    total += read
                    if (total > MAX_MOBILE_UPLOAD_BYTES) {
                        error("El archivo supera el límite móvil de 25 MB")
                    }
                    output.write(buffer, 0, read)
                }
            }
        } ?: error("Android no permitió leer el archivo seleccionado")
        return ArchivoSubidaLocal(
            file = staged,
            filename = filename.take(255).ifBlank { "archivo" },
            mime = mime,
            previewBytes = crearMiniaturaCodificada(staged, mime),
        )
    } catch (error: Throwable) {
        staged.delete()
        throw error
    }
}

/** Coincide con el `MAX_UPLOAD_BYTES` predeterminado del servidor y, más
 * importante, evita cargar un documento arbitrariamente grande en RAM antes
 * de que el backend pueda rechazarlo. */
private const val MAX_MOBILE_UPLOAD_BYTES = 25L * 1024 * 1024
private const val PENDING_UPLOAD_MAX_AGE_MS = 24L * 60 * 60 * 1000

/** Escribe solo en `cacheDir/shared_artifacts` (expuesto en modo lectura por
 * el `FileProvider`) y devuelve un `content://`; ninguna app recibe una ruta
 * privada `file://`. */
private fun guardarArtefactoEnCache(context: Context, descarga: DownloadedArtifact): Uri {
    val idSeguro = descarga.artifact.fileId
        .filter { it.isLetterOrDigit() || it == '-' || it == '_' }
        .take(80)
        .ifBlank { "archivo" }
    val carpeta = File(context.cacheDir, "shared_artifacts/$idSeguro").apply { mkdirs() }
    val archivo = File(carpeta, nombreSeguro(descarga.artifact.filename))
    archivo.outputStream().use { it.write(descarga.bytes) }
    return FileProvider.getUriForFile(context, "${context.packageName}.files", archivo)
}

private fun nombreSeguro(filename: String): String {
    val ultimo = filename.replace('\\', '/').substringAfterLast('/').trim()
    val limpio = ultimo.filterNot { it.isISOControl() || it == ':' }.take(180)
    return limpio.ifBlank { "archivo" }
}

private fun compartirArtefacto(context: Context, artifact: ArtifactRef, uri: Uri) {
    val mime = artifact.mime?.takeIf { it.count { char -> char == '/' } == 1 } ?: "*/*"
    val intent = Intent(Intent.ACTION_SEND).apply {
        type = mime
        putExtra(Intent.EXTRA_STREAM, uri)
        clipData = ClipData.newRawUri(artifact.filename, uri)
        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    }
    context.startActivity(Intent.createChooser(intent, "Compartir ${artifact.filename}"))
}

/** Defensa en profundidad antes de entregar un deep link externo a Android.
 * El backend ya valida el mismo contrato; la app vuelve a comprobar esquema,
 * credenciales y hosts evidentemente locales para no confiar ciegamente en
 * un payload persistido de una versión anterior. */
internal fun esUrlPublicaSegura(raw: String): Boolean {
    val uri = runCatching { URI(raw.trim()) }.getOrNull() ?: return false
    if (uri.scheme?.lowercase() !in setOf("http", "https")) return false
    if (!uri.rawUserInfo.isNullOrBlank()) return false
    val host = uri.host?.trimEnd('.')?.lowercase() ?: return false
    if (host == "localhost" || host.endsWith(".localhost") || host.endsWith(".local") ||
        host.endsWith(".internal")
    ) return false
    return !esDireccionIpPrivadaOReservada(host)
}

private fun esDireccionIpPrivadaOReservada(host: String): Boolean {
    val ipv4 = host.split('.').mapNotNull { it.toIntOrNull() }
    if (ipv4.size == 4 && ipv4.all { it in 0..255 }) {
        val (a, b) = ipv4
        return a == 0 || a == 10 || a == 127 ||
            (a == 100 && b in 64..127) ||
            (a == 169 && b == 254) ||
            (a == 172 && b in 16..31) ||
            (a == 192 && b == 168) ||
            (a == 198 && b in 18..19) || a >= 224
    }
    if (':' !in host) return false
    val normalized = host.removePrefix("[").removeSuffix("]").lowercase()
    return normalized == "::" || normalized == "::1" || normalized.startsWith("fe8") ||
        normalized.startsWith("fe9") || normalized.startsWith("fea") || normalized.startsWith("feb") ||
        normalized.startsWith("fc") || normalized.startsWith("fd") || normalized.startsWith("ff")
}

private fun abrirUrlPublica(context: Context, url: String): Boolean {
    if (!esUrlPublicaSegura(url)) return false
    return runCatching {
        context.startActivity(
            Intent(Intent.ACTION_VIEW, url.toUri()).apply {
                addCategory(Intent.CATEGORY_BROWSABLE)
            },
        )
    }.isSuccess
}
