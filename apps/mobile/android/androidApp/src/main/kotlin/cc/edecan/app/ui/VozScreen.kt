@file:OptIn(ExperimentalMaterial3Api::class)

package cc.edecan.app.ui

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.viewmodel.compose.viewModel
import cc.edecan.app.ui.theme.EdecanColors
import cc.edecan.app.vm.SessionViewModel
import cc.edecan.app.vm.VozUiState
import cc.edecan.app.vm.VozViewModel

/**
 * Pantalla de voz accesible desde el micrófono de Chat: push-to-talk contra
 * el mismo asistente, no telefonía (Twilio bring-your-own sigue pendiente,
 * ver `docs/voz-telefonia.md`). Mantén presionado el botón para grabar, suelta
 * para transcribir (`POST /v1/voice/transcribe`) → mandarlo por el turno
 * normal del agente → escuchar la respuesta (`POST /v1/voice/speak`).
 * Lógica real en [VozViewModel]; esta pantalla solo dibuja su estado.
 */
@Composable
fun VozScreen(
    sessionViewModel: SessionViewModel = viewModel(),
    vozViewModel: VozViewModel = viewModel(),
    onVolver: () -> Unit = {},
) {
    val uiState by vozViewModel.uiState.collectAsState()
    val api = sessionViewModel.api
    val contexto = LocalContext.current

    var permisoConcedido by remember {
        mutableStateOf(
            ContextCompat.checkSelfPermission(contexto, Manifest.permission.RECORD_AUDIO) ==
                PackageManager.PERMISSION_GRANTED,
        )
    }
    val lanzadorPermiso =
        rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { concedido ->
            permisoConcedido = concedido
        }

    LaunchedEffect(api) { api?.let { vozViewModel.verificarVozConectada(it) } }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Hablar con Edecán") },
                navigationIcon = {
                    IconButton(
                        onClick = onVolver,
                        modifier = Modifier.semantics { contentDescription = "Volver a Edecán" },
                    ) {
                        Text("←", style = MaterialTheme.typography.titleLarge)
                    }
                },
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier.padding(padding).fillMaxSize().padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            if (uiState.vozNoConectada) {
                Card(
                    modifier = Modifier.fillMaxWidth(),
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
                ) {
                    Text(
                        "No conectaste un proveedor de voz propio: vas a escuchar solo silencio y la " +
                            "transcripción va a ser siempre el mismo texto de prueba, sin reconocimiento " +
                            "real. Conéctalo desde Ajustes.",
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.padding(12.dp),
                    )
                }
            }

            Spacer(modifier = Modifier.weight(1f))

            EstadoVoz(uiState)

            uiState.confirmacionPendiente?.let { pendiente ->
                TarjetaConfirmacion(
                    pendiente = pendiente,
                    onAprobar = {
                        api?.let { vozViewModel.resolverConfirmacion(aprobado = true, api = it) }
                    },
                    onRechazar = {
                        api?.let { vozViewModel.resolverConfirmacion(aprobado = false, api = it) }
                    },
                )
            }

            Spacer(modifier = Modifier.height(28.dp))

            BotonPushToTalk(
                grabando = uiState.grabando,
                habilitado = api != null && !uiState.procesando && !uiState.reproduciendo &&
                    uiState.confirmacionPendiente == null,
                permisoConcedido = permisoConcedido,
                onPedirPermiso = { lanzadorPermiso.launch(Manifest.permission.RECORD_AUDIO) },
                onIniciar = { vozViewModel.iniciarGrabacion() },
                onDetener = { api?.let { vozViewModel.detenerYEnviar(it) } },
                onCancelar = { vozViewModel.cancelarGrabacion() },
            )

            Spacer(modifier = Modifier.weight(1f))

            uiState.errorMensaje?.let { error ->
                Text(
                    error,
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp),
                )
            }
        }
    }
}

@Composable
private fun EstadoVoz(uiState: VozUiState) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        when {
            uiState.procesando -> {
                CircularProgressIndicator(modifier = Modifier.padding(bottom = 8.dp))
                Text(
                    "Transcribiendo y pensando…",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            uiState.grabando -> Text("Escuchando…", style = MaterialTheme.typography.bodyMedium)
            uiState.respuesta != null -> {
                uiState.textoTranscrito?.let {
                    Text(
                        "Tú: $it",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        textAlign = TextAlign.Center,
                    )
                    Spacer(modifier = Modifier.height(8.dp))
                }
                Text("Edecán: ${uiState.respuesta}", style = MaterialTheme.typography.bodyMedium, textAlign = TextAlign.Center)
                if (uiState.reproduciendo) {
                    Spacer(modifier = Modifier.height(8.dp))
                    Text(
                        "🔊 reproduciendo…",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            else -> Text(
                "Mantén presionado el micrófono y habla.",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                textAlign = TextAlign.Center,
            )
        }
    }
}

@Composable
private fun BotonPushToTalk(
    grabando: Boolean,
    habilitado: Boolean,
    permisoConcedido: Boolean,
    onPedirPermiso: () -> Unit,
    onIniciar: () -> Unit,
    onDetener: () -> Unit,
    onCancelar: () -> Unit,
) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Box(
            modifier = Modifier
                .size(120.dp)
                .clip(CircleShape)
                .background(
                    when {
                        grabando -> Color(0xFFEF4444)
                        habilitado -> EdecanColors.Morado
                        else -> MaterialTheme.colorScheme.outline
                    },
                )
                .pointerInput(habilitado, permisoConcedido) {
                    if (!habilitado) return@pointerInput
                    detectTapGestures(
                        onPress = {
                            if (!permisoConcedido) {
                                onPedirPermiso()
                                return@detectTapGestures
                            }
                            onIniciar()
                            val liberadoNormal = tryAwaitRelease()
                            if (liberadoNormal) onDetener() else onCancelar()
                        },
                    )
                },
            contentAlignment = Alignment.Center,
        ) {
            Text("🎙️", style = MaterialTheme.typography.displayMedium)
        }
        Spacer(modifier = Modifier.height(12.dp))
        Text(
            when {
                !permisoConcedido -> "Toca para permitir el micrófono"
                grabando -> "Suelta para enviar"
                else -> "Mantén presionado para hablar"
            },
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}
