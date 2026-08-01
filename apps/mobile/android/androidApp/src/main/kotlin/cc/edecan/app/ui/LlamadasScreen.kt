@file:OptIn(ExperimentalMaterial3Api::class)

package cc.edecan.app.ui

import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import cc.edecan.app.ui.components.EmptyState
import cc.edecan.app.ui.components.formatearFechaHora
import cc.edecan.app.ui.theme.EdecanColors
import cc.edecan.app.vm.LlamadasViewModel
import cc.edecan.app.vm.SessionViewModel
import cc.edecan.shared.PhoneCall
import cc.edecan.shared.PhoneCallSummary
import cc.edecan.shared.agentLabel
import cc.edecan.shared.contactNumber
import cc.edecan.shared.isTerminal

/** Historial de llamadas como pantalla secundaria de Actividad. */
@Composable
fun LlamadasScreen(
    sessionViewModel: SessionViewModel = viewModel(),
    llamadasViewModel: LlamadasViewModel = viewModel(),
    onVolver: () -> Unit = {},
    onPedirLlamada: () -> Unit = {},
) {
    val uiState by llamadasViewModel.uiState.collectAsState()
    val api = sessionViewModel.api
    var expandidaId by rememberSaveable { mutableStateOf<String?>(null) }

    LaunchedEffect(api) { api?.let { llamadasViewModel.cargar(it) } }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Llamadas") },
                navigationIcon = { IconButton(onClick = onVolver) { Text("←") } },
                actions = { TextButton(onClick = onPedirLlamada) { Text("Nueva") } },
            )
        },
    ) { padding ->
        when {
            uiState.cargando && uiState.llamadas.isEmpty() -> Box(
                modifier = Modifier.padding(padding).fillMaxSize(),
            ) {
                CircularProgressIndicator(modifier = Modifier.align(Alignment.Center))
            }
            uiState.mensajeNoDisponible != null && uiState.llamadas.isEmpty() -> Box(
                modifier = Modifier.padding(padding).fillMaxSize().padding(16.dp),
            ) {
                EmptyState(
                    emoji = "📵",
                    titulo = "Llamadas no habilitadas",
                    descripcion = uiState.mensajeNoDisponible.orEmpty(),
                    etiquetaRoadmap = null,
                )
            }
            uiState.llamadas.isEmpty() -> Column(
                modifier = Modifier.padding(padding).fillMaxSize().padding(16.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center,
            ) {
                EmptyState(
                    emoji = "📞",
                    titulo = "Aún no hay llamadas",
                    descripcion = "Pídesela a Edecan con una frase. Antes de llamar, siempre te mostrará el destino y el objetivo para confirmar.",
                    etiquetaRoadmap = null,
                )
                OutlinedButton(onClick = onPedirLlamada, modifier = Modifier.padding(top = 12.dp)) {
                    Text("Pedir una llamada")
                }
            }
            else -> LazyColumn(
                modifier = Modifier.padding(padding).fillMaxSize(),
                contentPadding = PaddingValues(16.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                uiState.errorMensaje?.let { error ->
                    item(key = "error") {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text(
                                error,
                                color = MaterialTheme.colorScheme.error,
                                style = MaterialTheme.typography.bodySmall,
                                modifier = Modifier.weight(1f),
                            )
                            OutlinedButton(onClick = { api?.let { llamadasViewModel.cargar(it, forzar = true) } }) {
                                Text("Reintentar")
                            }
                        }
                    }
                }
                items(uiState.llamadas, key = { it.id }) { llamada ->
                    LlamadaCard(
                        llamada = llamada,
                        expandida = expandidaId == llamada.id,
                        onClick = {
                            expandidaId = if (expandidaId == llamada.id) null else llamada.id
                        },
                    )
                }
            }
        }
    }
}

@Composable
private fun LlamadaCard(llamada: PhoneCall, expandida: Boolean, onClick: () -> Unit) {
    Card(modifier = Modifier.fillMaxWidth().clickable(onClick = onClick)) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.Top) {
                Text(if (llamada.direction == "incoming") "↙" else "↗", style = MaterialTheme.typography.titleLarge)
                Column(modifier = Modifier.padding(start = 12.dp).weight(1f)) {
                    Text(llamada.contactNumber, style = MaterialTheme.typography.titleMedium)
                    Text(
                        llamada.goal.ifBlank { "Llamada telefónica" },
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        style = MaterialTheme.typography.bodyMedium,
                        maxLines = if (expandida) Int.MAX_VALUE else 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
                EstadoLlamada(llamada.status)
            }

            Row(
                modifier = Modifier.fillMaxWidth().padding(top = 10.dp),
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                (llamada.durationSeconds ?: llamada.summary?.durationSeconds)?.let {
                    Text(formatearDuracion(it), style = MaterialTheme.typography.labelSmall)
                }
                (llamada.startedAt ?: llamada.createdAt)?.takeIf { it.isNotBlank() }?.let {
                    Text(
                        formatearFechaHora(it),
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Spacer(modifier = Modifier.weight(1f))
                Text(if (expandida) "Ocultar" else "Ver resumen", style = MaterialTheme.typography.labelSmall)
            }

            if (expandida) DetalleLlamada(llamada)
        }
    }
}

@Composable
private fun DetalleLlamada(llamada: PhoneCall) {
    HorizontalDivider(modifier = Modifier.padding(vertical = 14.dp))
    llamada.agentLabel?.let { DatoDetalle("Agente", it) }
    llamada.error?.takeIf { it.isNotBlank() }?.let {
        Text(
            "⚠ $it",
            color = MaterialTheme.colorScheme.error,
            style = MaterialTheme.typography.bodySmall,
            modifier = Modifier.padding(bottom = 10.dp),
        )
    }

    val summary = llamada.summary
    if (summary == null) {
        Text(
            if (llamada.isTerminal) "Esta llamada terminó sin un resumen disponible."
            else "El resumen aparecerá aquí cuando termine la llamada.",
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            style = MaterialTheme.typography.bodyMedium,
        )
        return
    }

    ResumenLista("Puntos clave", summary.keyPoints, "No se registraron puntos clave.")
    ResumenLista("Compromisos", summary.commitments, "No quedaron compromisos.")
    ResumenLista("Próximos pasos", summary.nextSteps, "No quedaron pasos pendientes.")
    Transcripcion(summary)
}

@Composable
private fun ResumenLista(titulo: String, elementos: List<String>, vacio: String) {
    Text(
        titulo,
        style = MaterialTheme.typography.titleSmall,
        fontWeight = FontWeight.SemiBold,
        modifier = Modifier.padding(top = 10.dp, bottom = 4.dp),
    )
    if (elementos.isEmpty()) {
        Text(vacio, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    } else {
        elementos.forEach { elemento ->
            Text("• $elemento", style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(bottom = 3.dp))
        }
    }
}

@Composable
private fun Transcripcion(summary: PhoneCallSummary) {
    val transcript = summary.transcript
    val texto = if (!transcript.available) {
        "No hay transcripción disponible"
    } else if (transcript.turnCount == 1) {
        "Transcripción disponible · 1 intervención"
    } else {
        "Transcripción disponible · ${transcript.turnCount} intervenciones"
    }
    Text(
        (if (transcript.available) "✓ " else "○ ") + texto,
        color = if (transcript.available) Color(0xFF15803D) else MaterialTheme.colorScheme.onSurfaceVariant,
        style = MaterialTheme.typography.bodySmall,
        modifier = Modifier.padding(top = 14.dp),
    )
}

@Composable
private fun DatoDetalle(etiqueta: String, valor: String) {
    Row(modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp)) {
        Text(etiqueta, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(modifier = Modifier.weight(1f))
        Text(valor, style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.Medium)
    }
}

@Composable
private fun EstadoLlamada(status: String) {
    val (nombre, color) = when (status) {
        "draft" -> "Por confirmar" to EdecanColors.Azul
        "confirmed" -> "Confirmada" to EdecanColors.Azul
        "queued" -> "En cola" to EdecanColors.Azul
        "ringing" -> "Sonando" to EdecanColors.Morado
        "in_progress" -> "En curso" to EdecanColors.Morado
        "completed" -> "Completada" to Color(0xFF15803D)
        "failed" -> "Fallida" to Color(0xFFDC2626)
        "busy" -> "Ocupado" to Color(0xFFDC2626)
        "no_answer" -> "Sin respuesta" to Color(0xFFDC2626)
        "cancelled" -> "Cancelada" to MaterialTheme.colorScheme.onSurfaceVariant
        else -> status.replace('_', ' ').replaceFirstChar { it.uppercase() } to EdecanColors.Azul
    }
    Text(nombre, color = color, style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.SemiBold)
}

private fun formatearDuracion(totalSeconds: Int): String {
    val minutos = totalSeconds.coerceAtLeast(0) / 60
    val segundos = (totalSeconds.coerceAtLeast(0) % 60).toString().padStart(2, '0')
    return "$minutos:$segundos"
}
