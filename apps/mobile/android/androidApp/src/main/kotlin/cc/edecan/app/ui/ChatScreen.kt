@file:OptIn(ExperimentalMaterial3Api::class)

package cc.edecan.app.ui

import android.content.ClipData
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.core.content.FileProvider
import cc.edecan.app.ui.components.EmptyState
import cc.edecan.app.ui.theme.EdecanColors
import cc.edecan.app.vm.ChatViewModel
import cc.edecan.app.vm.ConfirmacionPendiente
import cc.edecan.app.vm.MensajeUi
import cc.edecan.app.vm.SessionViewModel
import cc.edecan.shared.ArtifactRef
import cc.edecan.shared.DownloadedArtifact
import java.io.File
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
) {
    val sessionState by sessionViewModel.uiState.collectAsState()
    val chatState by chatViewModel.uiState.collectAsState()
    var textoActual by remember { mutableStateOf("") }
    var artefactoDescargandoId by remember { mutableStateOf<String?>(null) }
    var errorArtefacto by remember { mutableStateOf<String?>(null) }
    val context = LocalContext.current
    val coroutineScope = rememberCoroutineScope()
    val listState = rememberLazyListState()

    LaunchedEffect(chatState.mensajes.size, chatState.mensajes.lastOrNull()?.texto) {
        if (chatState.mensajes.isNotEmpty()) listState.animateScrollToItem(chatState.mensajes.lastIndex)
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Edecán") },
                actions = {
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
        Column(modifier = Modifier.padding(padding).fillMaxSize()) {
            Box(modifier = Modifier.weight(1f)) {
                if (chatState.mensajes.isEmpty()) {
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
                                artefactoDescargandoId = artefactoDescargandoId,
                                onAbrirArtefacto = { artefacto ->
                                    val api = sessionViewModel.api ?: return@BurbujaMensaje
                                    if (artefactoDescargandoId != null) return@BurbujaMensaje
                                    artefactoDescargandoId = artefacto.fileId
                                    errorArtefacto = null
                                    coroutineScope.launch {
                                        try {
                                            val descarga = api.downloadArtifact(artefacto)
                                            val uri = withContext(Dispatchers.IO) {
                                                guardarArtefactoEnCache(context, descarga)
                                            }
                                            compartirArtefacto(context, descarga.artifact, uri)
                                        } catch (error: Exception) {
                                            errorArtefacto = "No se pudo descargar ${artefacto.filename}: " +
                                                (error.message ?: "error desconocido")
                                        } finally {
                                            artefactoDescargandoId = null
                                        }
                                    }
                                },
                            )
                        }
                        chatState.herramientaActiva?.let { nombre ->
                            item(key = "herramienta-activa") { IndicadorHerramienta(nombre) }
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

            (errorArtefacto ?: chatState.errorMensaje)?.let { error ->
                Text(
                    error,
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
                )
            }

            BarraDeEntrada(
                texto = textoActual,
                onTextoCambia = { textoActual = it },
                habilitado = textoActual.isNotBlank() && !chatState.enviando &&
                    chatState.confirmacionPendiente == null,
                onEnviar = {
                    val api = sessionViewModel.api
                    if (api == null) {
                        return@BarraDeEntrada
                    }
                    val texto = textoActual
                    textoActual = ""
                    chatViewModel.enviar(texto, api)
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
            "Revisa qué hay en pantalla antes de aprobar: Edecán nunca debe navegar, hacer clic, " +
            "escribir ni leer contenido de LinkedIn, ni completar un pago, cobro o inicio de sesión " +
            "por ti. Si eso es lo que está a punto de hacer, rechaza."
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
    artefactoDescargandoId: String?,
    onAbrirArtefacto: (ArtifactRef) -> Unit,
) {
    val esUsuario = mensaje.rol == MensajeUi.Rol.USUARIO
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
                Text(
                    mensaje.texto,
                    color = if (esUsuario) Color.White else MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            if (!esUsuario) {
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
                            Text("↓ ")
                        }
                        Text(artefacto.filename, maxLines = 1, modifier = Modifier.weight(1f))
                        Text(" ↗")
                    }
                }
            }
        }
    }
}

/** Aviso corto de "Edecán está usando «herramienta»" mientras el turno del
 * agente ejecuta una tool (evento SSE `tool_start` sin su `tool_end`
 * todavía — `docs/api.md` §"Conversaciones y chat (SSE)"). */
@Composable
private fun IndicadorHerramienta(nombre: String) {
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
            "${accionEnLenguajeClaro(nombre).replaceFirstChar { it.uppercase() }}…",
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
    onEnviar: () -> Unit,
) {
    Row(
        verticalAlignment = Alignment.Bottom,
        modifier = Modifier.fillMaxWidth().padding(12.dp),
    ) {
        OutlinedTextField(
            value = texto,
            onValueChange = onTextoCambia,
            placeholder = { Text("Escríbele a Edecán…") },
            modifier = Modifier.weight(1f),
            maxLines = 4,
        )
        IconButton(onClick = onEnviar, enabled = habilitado) {
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
