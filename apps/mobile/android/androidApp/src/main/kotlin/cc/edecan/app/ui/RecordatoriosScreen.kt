@file:OptIn(ExperimentalMaterial3Api::class)

package cc.edecan.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
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
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import cc.edecan.app.ui.components.EmptyState
import cc.edecan.app.ui.components.SelectorFecha
import cc.edecan.app.ui.components.SelectorHora
import cc.edecan.app.ui.components.formatearFechaHora
import cc.edecan.app.ui.theme.EdecanColors
import cc.edecan.app.vm.RecordatoriosViewModel
import cc.edecan.app.vm.SessionViewModel
import cc.edecan.app.vm.completados
import cc.edecan.app.vm.pendientes
import cc.edecan.shared.Reminder
import java.time.LocalDate
import java.time.LocalTime

private val ETIQUETAS_ESTADO_RECORDATORIO = mapOf(
    "pending" to "Pendiente",
    "sent" to "Completado",
    "cancelled" to "Cancelado",
)

/**
 * Pestaña "Recordatorios" (`/v1/reminders`, `ARCHITECTURE.md` §10.3,
 * §10.12, §10.11, WP-V5-07): pendientes/completados, alta con texto +
 * fecha/hora (pickers de Material3, `ui/components/FechaHoraPickers.kt`) y
 * "completar" a mano. Se llega acá SOLO desde Inicio, no es pestaña de la
 * barra inferior. Lógica real en [RecordatoriosViewModel]; esta pantalla
 * solo dibuja su estado.
 */
@Composable
fun RecordatoriosScreen(
    sessionViewModel: SessionViewModel = viewModel(),
    recordatoriosViewModel: RecordatoriosViewModel = viewModel(),
    onVolver: () -> Unit = {},
) {
    val uiState by recordatoriosViewModel.uiState.collectAsState()
    val api = sessionViewModel.api

    LaunchedEffect(api) { api?.let { recordatoriosViewModel.cargar(it) } }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Recordatorios") },
                navigationIcon = { IconButton(onClick = onVolver) { Text("←") } },
            )
        },
    ) { padding ->
        Column(modifier = Modifier.padding(padding).fillMaxSize().padding(16.dp)) {
            FormularioNuevoRecordatorio(
                creando = uiState.creando,
                error = uiState.errorCrear,
                onCrear = { texto, fecha, hora -> api?.let { recordatoriosViewModel.crear(it, texto, fecha, hora) } },
            )

            uiState.errorLista?.let { error ->
                Text(
                    error,
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(top = 12.dp),
                )
            }

            if (uiState.cargando && uiState.recordatorios.isEmpty()) {
                Box(modifier = Modifier.fillMaxSize()) {
                    CircularProgressIndicator(modifier = Modifier.align(Alignment.Center))
                }
            } else if (uiState.recordatorios.isEmpty()) {
                EmptyState(
                    emoji = "🔔",
                    titulo = "Sin recordatorios",
                    descripcion = "Crea uno arriba, o pídeselo a tu asistente en el chat.",
                    etiquetaRoadmap = null,
                )
            } else {
                val pendientes = uiState.pendientes
                val completados = uiState.completados
                Column(modifier = Modifier.padding(top = 20.dp)) {
                    Text("Pendientes", style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(bottom = 8.dp))
                    if (pendientes.isEmpty()) {
                        Text(
                            "No tienes recordatorios pendientes.",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    } else {
                        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            pendientes.forEach { recordatorio ->
                                FilaRecordatorio(
                                    recordatorio = recordatorio,
                                    ocupado = recordatorio.id in uiState.idsOcupados,
                                    onCompletar = { api?.let { recordatoriosViewModel.completar(it, recordatorio.id) } },
                                )
                            }
                        }
                    }

                    if (completados.isNotEmpty()) {
                        Text(
                            "Completados",
                            style = MaterialTheme.typography.titleSmall,
                            modifier = Modifier.padding(top = 20.dp, bottom = 8.dp),
                        )
                        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            completados.forEach { recordatorio -> FilaRecordatorio(recordatorio, ocupado = false, onCompletar = null) }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun FormularioNuevoRecordatorio(
    creando: Boolean,
    error: String?,
    onCrear: (texto: String, fecha: LocalDate, hora: LocalTime) -> Unit,
) {
    var texto by remember { mutableStateOf("") }
    var fecha by remember { mutableStateOf(LocalDate.now()) }
    var hora by remember { mutableStateOf(LocalTime.now().plusHours(1).withMinute(0).withSecond(0).withNano(0)) }

    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(16.dp)) {
            Text("Nuevo recordatorio", style = MaterialTheme.typography.titleSmall)
            OutlinedTextField(
                value = texto,
                onValueChange = { texto = it },
                placeholder = { Text("Llamar al contador") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth().padding(top = 10.dp, bottom = 10.dp),
            )
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                SelectorFecha(fecha = fecha, onFechaCambia = { fecha = it })
                SelectorHora(hora = hora, onHoraCambia = { hora = it })
            }
            error?.let {
                Text(
                    it,
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(top = 10.dp),
                )
            }
            Button(
                onClick = {
                    onCrear(texto, fecha, hora)
                    texto = ""
                },
                enabled = !creando && texto.isNotBlank(),
                colors = ButtonDefaults.buttonColors(containerColor = EdecanColors.Morado),
                modifier = Modifier.fillMaxWidth().padding(top = 12.dp),
            ) {
                if (creando) {
                    CircularProgressIndicator(modifier = Modifier.padding(end = 8.dp))
                }
                Text("Crear")
            }
        }
    }
}

@Composable
private fun FilaRecordatorio(recordatorio: Reminder, ocupado: Boolean, onCompletar: (() -> Unit)?) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(14.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(modifier = Modifier.weight(1f).padding(end = 10.dp)) {
                Text(recordatorio.message, style = MaterialTheme.typography.bodyMedium)
                Text(
                    formatearFechaHora(recordatorio.dueAt) + (recordatorio.rrule?.let { " · $it" } ?: ""),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            if (onCompletar != null) {
                OutlinedButton(onClick = onCompletar, enabled = !ocupado) { Text("Completar") }
            } else {
                EstadoBadgeRecordatorio(recordatorio.status)
            }
        }
    }
}

@Composable
private fun EstadoBadgeRecordatorio(estado: String) {
    val color = when (estado) {
        "sent" -> Color(0xFF22C55E)
        "cancelled" -> Color(0xFF94A3B8)
        else -> Color(0xFF94A3B8)
    }
    Box(
        modifier = Modifier
            .clip(RoundedCornerShape(8.dp))
            .background(color.copy(alpha = 0.15f))
            .padding(horizontal = 8.dp, vertical = 3.dp),
    ) {
        Text(ETIQUETAS_ESTADO_RECORDATORIO[estado] ?: estado, style = MaterialTheme.typography.labelSmall, color = color)
    }
}
