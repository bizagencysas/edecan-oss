package cc.edecan.app.ui

import android.media.MediaDataSource
import android.media.MediaPlayer
import android.view.SurfaceView
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.unit.dp
import cc.edecan.app.ui.theme.EdecanColors
import cc.edecan.shared.ArtifactRef
import cc.edecan.shared.ChatAction
import cc.edecan.shared.ChatBlock
import cc.edecan.shared.DownloadedArtifact
import cc.edecan.shared.EdecanApi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import java.io.IOException
import kotlin.math.min

/** Resultado privado listo para render. [Stream] evita copiar audio/video a
 * memoria: el reproductor pide ventanas Range a [EdecanApi], que obtiene un
 * token vigente por solicitud y puede renovarlo sin congelar credenciales. */
internal sealed interface VistaPreviaPrivada {
    data class Imagen(val descarga: DownloadedArtifact) : VistaPreviaPrivada

    class Stream(
        val artifact: ArtifactRef,
        val api: EdecanApi,
    ) : VistaPreviaPrivada
}

/** Renderer Android del contrato `ChatBlock` v1. No interpreta HTML ni URLs
 * arbitrarias y delega toda acción a [onAction] para aplicar los guardrails
 * de navegación de la pantalla. */
@Composable
internal fun BloquesRicosMensaje(
    bloques: List<ChatBlock>,
    previews: Map<String, VistaPreviaPrivada>,
    previewsCargando: Set<String>,
    onCargarPreview: (ChatBlock.Media) -> Unit,
    onAction: (ChatAction) -> Unit,
) {
    bloques.forEachIndexed { index, bloque ->
        if (bloque.esBloqueViaje()) {
            if (index == 0 || !bloques[index - 1].esBloqueViaje()) {
                CarruselViajes(
                    bloques = bloques.drop(index).takeWhile { it.esBloqueViaje() },
                    onAction = onAction,
                )
            }
        } else {
            when (bloque) {
                is ChatBlock.Media -> TarjetaMedia(
                    bloque = bloque,
                    preview = previews[bloque.artifact.fileId],
                    cargando = bloque.artifact.fileId in previewsCargando,
                    onCargar = { onCargarPreview(bloque) },
                )
                is ChatBlock.LinkPreview -> EnlaceInternet(bloque, onAction)
                is ChatBlock.Unknown -> bloque.fallbackText?.takeIf { it.isNotBlank() }?.let {
                    Text(it, style = MaterialTheme.typography.bodySmall)
                }
                is ChatBlock.Flight, is ChatBlock.Hotel -> Unit
            }
        }
    }
}

@Composable
private fun CarruselViajes(bloques: List<ChatBlock>, onAction: (ChatAction) -> Unit) {
    LazyRow(
        horizontalArrangement = Arrangement.spacedBy(10.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        itemsIndexed(bloques) { _, bloque ->
            when (bloque) {
                is ChatBlock.Flight -> TarjetaVuelo(bloque, onAction)
                is ChatBlock.Hotel -> TarjetaHotel(bloque, onAction)
                else -> Unit
            }
        }
    }
}

private fun ChatBlock.esBloqueViaje(): Boolean = this is ChatBlock.Flight || this is ChatBlock.Hotel

@Composable
private fun EnlaceInternet(bloque: ChatBlock.LinkPreview, onAction: (ChatAction) -> Unit) {
    val titulo = bloque.title.trim().ifBlank {
        bloque.siteName?.trim().orEmpty().ifBlank { bloque.url }
    }
    TextButton(
        onClick = { onAction(ChatAction.OpenUrl("open-link", "Abrir enlace", bloque.url)) },
        contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 0.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Text(
            text = "↗ $titulo",
            style = MaterialTheme.typography.bodyMedium,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.fillMaxWidth(),
        )
    }
}

@Composable
private fun TarjetaVuelo(bloque: ChatBlock.Flight, onAction: (ChatAction) -> Unit) {
    TarjetaRica(modifier = Modifier.width(236.dp), compacta = true) {
        EtiquetaFuente(bloque.sourceMode, bloque.provider)
        Text("${bloque.origin} → ${bloque.destination}", style = MaterialTheme.typography.titleSmall)
        Text(
            bloque.airline,
            style = MaterialTheme.typography.bodySmall,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
        val horario = listOfNotNull(bloque.departure, bloque.arrival).joinToString(" → ")
        if (horario.isNotBlank()) {
            Text(horario, style = MaterialTheme.typography.labelSmall, maxLines = 2)
        }
        Text(
            if (bloque.stops == 0) "Directo" else "${bloque.stops} escala(s)",
            style = MaterialTheme.typography.labelSmall,
        )
        Text("${bloque.currency} ${bloque.price}", style = MaterialTheme.typography.titleSmall)
        bloque.taxes?.let {
            Text("Impuestos: $it", style = MaterialTheme.typography.labelSmall, maxLines = 1)
        }
        bloque.cancellation?.let {
            Text(it, style = MaterialTheme.typography.labelSmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
        }
        bloque.expiresAt?.let {
            Text("Válida hasta: $it", style = MaterialTheme.typography.labelSmall, maxLines = 1)
        }
        Acciones(bloque.actions, onAction, compactas = true)
    }
}

@Composable
private fun TarjetaHotel(bloque: ChatBlock.Hotel, onAction: (ChatAction) -> Unit) {
    TarjetaRica(modifier = Modifier.width(236.dp), compacta = true) {
        EtiquetaFuente(bloque.sourceMode, bloque.provider)
        Text(
            bloque.name,
            style = MaterialTheme.typography.titleSmall,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
        Text(
            listOfNotNull(bloque.city, bloque.rating?.let { "$it★" }).joinToString(" · "),
            style = MaterialTheme.typography.bodySmall,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
        val fechas = listOfNotNull(bloque.checkin, bloque.checkout).joinToString(" → ")
        if (fechas.isNotBlank()) Text(fechas, style = MaterialTheme.typography.labelSmall, maxLines = 2)
        Text("${bloque.currency} ${bloque.price}", style = MaterialTheme.typography.titleSmall)
        bloque.taxes?.let {
            Text("Impuestos: $it", style = MaterialTheme.typography.labelSmall, maxLines = 1)
        }
        bloque.cancellation?.let {
            Text(it, style = MaterialTheme.typography.labelSmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
        }
        bloque.expiresAt?.let {
            Text("Válida hasta: $it", style = MaterialTheme.typography.labelSmall, maxLines = 1)
        }
        Acciones(bloque.actions, onAction, compactas = true)
    }
}

@Composable
private fun EtiquetaFuente(sourceMode: String, provider: String?) {
    val normalizedMode = sourceMode.lowercase()
    val etiqueta = when (normalizedMode) {
        "demo" -> "DEMOSTRACIÓN"
        "live" -> "EN VIVO"
        else -> "FUENTE NO VERIFICADA"
    }
    Text(
        listOfNotNull(etiqueta, provider?.takeIf { it.isNotBlank() }).joinToString(" · "),
        color = if (normalizedMode == "demo") MaterialTheme.colorScheme.error else EdecanColors.Morado,
        style = MaterialTheme.typography.labelSmall,
    )
}

@Composable
private fun Acciones(
    acciones: List<ChatAction>,
    onAction: (ChatAction) -> Unit,
    compactas: Boolean = false,
) {
    if (acciones.isEmpty()) return
    Row(
        horizontalArrangement = Arrangement.spacedBy(if (compactas) 4.dp else 8.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        acciones.take(3).forEach { accion ->
            if (accion !is ChatAction.Unknown && accion.label.isNotBlank()) {
                OutlinedButton(
                    onClick = { onAction(accion) },
                    contentPadding = if (compactas) {
                        androidx.compose.foundation.layout.PaddingValues(horizontal = 10.dp, vertical = 4.dp)
                    } else {
                        androidx.compose.material3.ButtonDefaults.ContentPadding
                    },
                ) {
                    Text(
                        accion.label,
                        style = if (compactas) {
                            MaterialTheme.typography.labelSmall
                        } else {
                            MaterialTheme.typography.labelLarge
                        },
                        maxLines = 1,
                    )
                }
            }
        }
    }
}

@Composable
private fun TarjetaMedia(
    bloque: ChatBlock.Media,
    preview: VistaPreviaPrivada?,
    cargando: Boolean,
    onCargar: () -> Unit,
) {
    val kind = bloque.mediaKind.lowercase()
    var autoSolicitado by remember(bloque.artifact.fileId) { mutableStateOf(false) }
    LaunchedEffect(kind, bloque.artifact.fileId, preview, cargando, autoSolicitado) {
        if (kind == "image" && preview == null && !cargando && !autoSolicitado) {
            autoSolicitado = true
            onCargar()
        }
    }
    TarjetaRica {
        Text(
            bloque.caption ?: bloque.alt.ifBlank { bloque.artifact.filename },
            style = MaterialTheme.typography.bodySmall,
        )
        when {
            cargando -> CircularProgressIndicator(modifier = Modifier.size(24.dp), strokeWidth = 2.dp)
            preview == null -> Button(onClick = onCargar) {
                Text(
                    when (kind) {
                        "image" -> "Cargar imagen"
                        "audio" -> "Cargar audio"
                        else -> "Cargar video"
                    },
                )
            }
            kind == "image" && preview is VistaPreviaPrivada.Imagen -> ImagenPrivada(preview.descarga)
            kind == "video" && preview is VistaPreviaPrivada.Stream -> VideoPrivado(preview)
            kind == "audio" && preview is VistaPreviaPrivada.Stream -> AudioPrivado(preview)
            else -> Text(bloque.fallbackText ?: "Medio no compatible")
        }
    }
}

@Composable
private fun ImagenPrivada(descarga: DownloadedArtifact) {
    val bitmap by produceState<ImageBitmap?>(initialValue = null, descarga.bytes) {
        value = withContext(Dispatchers.Default) { decodificarImagenAcotada(descarga.bytes) }
    }
    bitmap?.let {
        Image(
            bitmap = it,
            contentDescription = descarga.artifact.filename,
            contentScale = ContentScale.Fit,
            modifier = Modifier.fillMaxWidth().heightIn(max = 360.dp),
        )
    } ?: CircularProgressIndicator(modifier = Modifier.size(24.dp), strokeWidth = 2.dp)
}

@Composable
private fun VideoPrivado(source: VistaPreviaPrivada.Stream) {
    var preparado by remember(source) { mutableStateOf(false) }
    var fallo by remember(source) { mutableStateOf(false) }
    var reproduciendo by remember(source) { mutableStateOf(false) }
    val dataSource = remember(source) { FuenteMediaPrivada(source.api, source.artifact) }
    val player = remember(source) { MediaPlayer() }
    DisposableEffect(player, dataSource) {
        player.setOnPreparedListener { preparado = true }
        player.setOnCompletionListener { reproduciendo = false }
        player.setOnErrorListener { _, _, _ -> fallo = true; true }
        runCatching {
            player.setDataSource(dataSource)
            player.prepareAsync()
        }.onFailure { fallo = true }
        onDispose {
            player.setDisplay(null)
            player.release()
            dataSource.close()
        }
    }
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        AndroidView(
            factory = { SurfaceView(it) },
            update = { view -> player.setDisplay(view.holder) },
            modifier = Modifier.fillMaxWidth().aspectRatio(16f / 9f),
        )
        when {
            fallo -> Text("No se pudo preparar el video privado.", style = MaterialTheme.typography.bodySmall)
            !preparado -> CircularProgressIndicator(modifier = Modifier.size(24.dp), strokeWidth = 2.dp)
            else -> Button(onClick = {
                if (player.isPlaying) {
                    player.pause()
                    reproduciendo = false
                } else {
                    player.start()
                    reproduciendo = true
                }
            }) { Text(if (reproduciendo) "Pausar" else "Reproducir") }
        }
    }
}

@Composable
private fun AudioPrivado(source: VistaPreviaPrivada.Stream) {
    var preparado by remember(source) { mutableStateOf(false) }
    var fallo by remember(source) { mutableStateOf(false) }
    var reproduciendo by remember(source) { mutableStateOf(false) }
    val dataSource = remember(source) { FuenteMediaPrivada(source.api, source.artifact) }
    val player = remember(source) { MediaPlayer() }
    DisposableEffect(player, dataSource) {
        player.setOnPreparedListener { preparado = true }
        player.setOnCompletionListener { reproduciendo = false }
        player.setOnErrorListener { _, _, _ -> fallo = true; true }
        runCatching {
            player.setDataSource(dataSource)
            player.prepareAsync()
        }.onFailure { fallo = true }
        onDispose {
            player.release()
            dataSource.close()
        }
    }
    if (fallo) {
        Text("No se pudo preparar el audio privado.", style = MaterialTheme.typography.bodySmall)
    } else if (!preparado) {
        CircularProgressIndicator(modifier = Modifier.size(24.dp), strokeWidth = 2.dp)
    } else {
        Button(onClick = {
            if (player.isPlaying) {
                player.pause()
                reproduciendo = false
            } else {
                player.start()
                reproduciendo = true
            }
        }) { Text(if (reproduciendo) "Pausar" else "Reproducir") }
    }
}

/** Puente síncrono exigido por MediaPlayer. Cada lectura delega en la API,
 * que toma el access token actual y repite exactamente la misma ventana tras
 * un refresh. La ventana queda acotada para que el extractor no fuerce un
 * buffer grande aunque solicite muchos bytes de una vez. */
internal class FuenteMediaPrivada(
    private val api: EdecanApi,
    private val artifact: ArtifactRef,
) : MediaDataSource() {
    @Volatile private var cerrada = false
    @Volatile private var totalSize: Long? = null

    override fun readAt(position: Long, buffer: ByteArray, offset: Int, size: Int): Int {
        if (cerrada) throw IOException("La fuente multimedia ya está cerrada")
        if (position < 0 || offset < 0 || size < 0 || offset + size > buffer.size) {
            throw IndexOutOfBoundsException("Ventana multimedia inválida")
        }
        if (size == 0) return 0
        if (totalSize?.let { position >= it } == true) return -1
        return try {
            val window = runBlocking(Dispatchers.IO) {
                api.privateMediaRange(artifact, position, min(size, MAX_MEDIA_RANGE_BYTES))
            }
            window.totalSize?.let { totalSize = it }
            val relativeOffset = (position - window.offset).toInt()
            if (relativeOffset < 0 || relativeOffset >= window.bytes.size) return -1
            val copied = min(size, window.bytes.size - relativeOffset)
            window.bytes.copyInto(buffer, offset, relativeOffset, relativeOffset + copied)
            if (copied == 0) -1 else copied
        } catch (error: Exception) {
            throw IOException("No se pudo leer el archivo privado", error)
        }
    }

    override fun getSize(): Long {
        if (cerrada) throw IOException("La fuente multimedia ya está cerrada")
        totalSize?.let { return it }
        return try {
            val probe = runBlocking(Dispatchers.IO) { api.privateMediaRange(artifact, 0, 1) }
            probe.totalSize?.also { totalSize = it } ?: -1L
        } catch (error: Exception) {
            throw IOException("No se pudo consultar el tamaño del archivo privado", error)
        }
    }

    override fun close() {
        cerrada = true
    }

    private companion object {
        const val MAX_MEDIA_RANGE_BYTES = 512 * 1024
    }
}

@Composable
private fun TarjetaRica(
    modifier: Modifier = Modifier.fillMaxWidth(),
    compacta: Boolean = false,
    content: @Composable ColumnScope.() -> Unit,
) {
    Card(
        modifier = modifier,
        shape = RoundedCornerShape(if (compacta) 12.dp else 14.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
    ) {
        Column(
            verticalArrangement = Arrangement.spacedBy(if (compacta) 4.dp else 6.dp),
            modifier = Modifier.fillMaxWidth().padding(if (compacta) 10.dp else 12.dp),
            content = content,
        )
    }
}
