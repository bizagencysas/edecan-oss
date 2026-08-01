@file:OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)

package cc.edecan.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
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
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import cc.edecan.app.ui.components.EmptyState
import cc.edecan.app.ui.components.formatearFechaHora
import cc.edecan.app.ui.theme.EdecanColors
import cc.edecan.app.vm.AutomatizacionesUiState
import cc.edecan.app.vm.AutomatizacionesViewModel
import cc.edecan.app.vm.SessionViewModel
import cc.edecan.shared.Automation
import cc.edecan.shared.AutomationRun

/** Presets de `rrule` (RFC 5545) — MISMOS valores que
 * `AutomationForm.tsx` (panel web, `PRESETS`) para que una regla creada
 * desde el móvil se vea/agende exactamente igual desde el panel web. */
private data class PresetAgenda(val etiqueta: String, val rrule: String)

private val PRESETS_AGENDA = listOf(
    PresetAgenda("Diario a las 9:00", "FREQ=DAILY;BYHOUR=9;BYMINUTE=0"),
    PresetAgenda("Semanal (lunes) a las 9:00", "FREQ=WEEKLY;BYDAY=MO;BYHOUR=9;BYMINUTE=0"),
    PresetAgenda("Mensual (día 1) a las 9:00", "FREQ=MONTHLY;BYMONTHDAY=1;BYHOUR=9;BYMINUTE=0"),
)

private val ETIQUETAS_ESTADO_RUN = mapOf(
    "running" to "Ejecutando",
    "done" to "Hecho",
    "error" to "Error",
    "waiting_confirmation" to "Esperando confirmación",
)

/**
 * Pestaña "Automatizaciones" (`/v1/automations`, `ROADMAP_V2.md`
 * §7.4/§7.6/§7.10, WP-V5-07): lista con `Switch` optimista (revierte solo si
 * el `PATCH` falla, ver `AutomatizacionesViewModel.alternar`), alta simple
 * (agenda + instrucción) y detalle de corridas. Se llega acá SOLO desde
 * Inicio, no es pestaña de la barra inferior. Lógica real en
 * [AutomatizacionesViewModel]; esta pantalla solo dibuja su estado.
 */
@Composable
fun AutomatizacionesScreen(
    sessionViewModel: SessionViewModel = viewModel(),
    automatizacionesViewModel: AutomatizacionesViewModel = viewModel(),
    onVolver: () -> Unit = {},
) {
    val uiState by automatizacionesViewModel.uiState.collectAsState()
    val api = sessionViewModel.api

    LaunchedEffect(api) { api?.let { automatizacionesViewModel.cargar(it) } }

    val seleccionada = uiState.automatizaciones.find { it.id == uiState.seleccionId }
    if (uiState.seleccionId != null) {
        DetalleAutomatizacion(
            automatizacion = seleccionada,
            uiState = uiState,
            onVolver = automatizacionesViewModel::cerrarDetalle,
        )
        return
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Automatizaciones") },
                navigationIcon = { IconButton(onClick = onVolver) { Text("←") } },
            )
        },
    ) { padding ->
        Column(modifier = Modifier.padding(padding).fillMaxSize().padding(16.dp)) {
            FormularioNuevaAutomatizacion(
                creando = uiState.creando,
                error = uiState.errorCrear,
                onCrear = { nombre, rrule, instruccion ->
                    api?.let { automatizacionesViewModel.crear(it, nombre, rrule, instruccion) }
                },
            )

            Text(
                "Tus automatizaciones",
                style = MaterialTheme.typography.titleSmall,
                modifier = Modifier.padding(top = 20.dp, bottom = 8.dp),
            )
            uiState.errorLista?.let { error ->
                Text(
                    error,
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(bottom = 8.dp),
                )
            }

            when {
                uiState.cargando && uiState.automatizaciones.isEmpty() -> Box(modifier = Modifier.fillMaxSize()) {
                    CircularProgressIndicator(modifier = Modifier.align(Alignment.Center))
                }
                uiState.automatizaciones.isEmpty() -> EmptyState(
                    emoji = "⚡",
                    titulo = "Sin automatizaciones todavía",
                    descripcion = "Crea la primera arriba, o pídeselo a tu asistente en el chat con «gestionar_automatizacion».",
                    etiquetaRoadmap = null,
                )
                else -> LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    items(uiState.automatizaciones, key = { it.id }) { automatizacion ->
                        FilaAutomatizacion(
                            automatizacion = automatizacion,
                            ocupado = automatizacion.id in uiState.idsEnCambio,
                            onToggle = { habilitado ->
                                api?.let { automatizacionesViewModel.alternar(it, automatizacion, habilitado) }
                            },
                            onClick = { api?.let { automatizacionesViewModel.seleccionar(it, automatizacion.id) } },
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun FormularioNuevaAutomatizacion(
    creando: Boolean,
    error: String?,
    onCrear: (nombre: String, rrule: String, instruccion: String) -> Unit,
) {
    var nombre by remember { mutableStateOf("") }
    var preset by remember { mutableStateOf(PRESETS_AGENDA.first()) }
    var instruccion by remember { mutableStateOf("") }

    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(16.dp)) {
            Text("Nueva automatización", style = MaterialTheme.typography.titleSmall)

            OutlinedTextField(
                value = nombre,
                onValueChange = { nombre = it },
                label = { Text("Nombre") },
                placeholder = { Text("Reporte de ventas diario") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth().padding(top = 10.dp, bottom = 8.dp),
            )

            Text("Frecuencia", style = MaterialTheme.typography.labelMedium, modifier = Modifier.padding(bottom = 6.dp))
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                PRESETS_AGENDA.forEach { candidato ->
                    FilterChip(
                        selected = candidato == preset,
                        onClick = { preset = candidato },
                        label = { Text(candidato.etiqueta) },
                    )
                }
            }

            OutlinedTextField(
                value = instruccion,
                onValueChange = { instruccion = it },
                label = { Text("Instrucción para el agente") },
                placeholder = { Text("Resume las ventas de hoy y guárdalas como una nota.") },
                minLines = 2,
                maxLines = 4,
                modifier = Modifier.fillMaxWidth().padding(top = 12.dp, bottom = 8.dp),
            )

            error?.let {
                Text(
                    it,
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(bottom = 8.dp),
                )
            }

            Button(
                onClick = {
                    onCrear(nombre, preset.rrule, instruccion)
                    nombre = ""
                    instruccion = ""
                },
                enabled = !creando && nombre.isNotBlank() && instruccion.isNotBlank(),
                colors = ButtonDefaults.buttonColors(containerColor = EdecanColors.Morado),
                modifier = Modifier.fillMaxWidth(),
            ) {
                if (creando) {
                    CircularProgressIndicator(modifier = Modifier.padding(end = 8.dp))
                }
                Text("Crear automatización")
            }
        }
    }
}

@Composable
private fun FilaAutomatizacion(
    automatizacion: Automation,
    ocupado: Boolean,
    onToggle: (Boolean) -> Unit,
    onClick: () -> Unit,
) {
    Card(modifier = Modifier.fillMaxWidth(), onClick = onClick) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(automatizacion.nombre, style = MaterialTheme.typography.bodyMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(
                    triggerLabel(automatizacion) +
                        (automatizacion.nextRunAt?.let { " · próxima: ${formatearFechaHora(it)}" } ?: ""),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            Switch(checked = automatizacion.enabled, onCheckedChange = onToggle, enabled = !ocupado)
        }
    }
}

@Composable
private fun DetalleAutomatizacion(
    automatizacion: Automation?,
    uiState: AutomatizacionesUiState,
    onVolver: () -> Unit,
) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(automatizacion?.nombre ?: "Automatización", maxLines = 1, overflow = TextOverflow.Ellipsis) },
                navigationIcon = { IconButton(onClick = onVolver) { Text("←") } },
            )
        },
    ) { padding ->
        Box(modifier = Modifier.padding(padding).fillMaxSize()) {
            if (automatizacion == null) {
                EmptyState(emoji = "⚡", titulo = "No se encontró", descripcion = "Vuelve a la lista e intenta de nuevo.", etiquetaRoadmap = null)
                return@Box
            }
            Column(modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp)) {
                Text(triggerLabel(automatizacion), style = MaterialTheme.typography.bodyMedium)
                Text(
                    automatizacion.accion.instruccion,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 4.dp),
                )
                automatizacion.nextRunAt?.let {
                    Text(
                        "Próxima corrida: ${formatearFechaHora(it)}",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(top = 4.dp),
                    )
                }

                Text(
                    "Corridas recientes",
                    style = MaterialTheme.typography.titleSmall,
                    modifier = Modifier.padding(top = 20.dp, bottom = 8.dp),
                )
                when {
                    uiState.cargandoCorridas -> CircularProgressIndicator(modifier = Modifier.padding(16.dp))
                    uiState.corridas.isEmpty() -> Text(
                        "Todavía no corrió ninguna vez.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    else -> Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        uiState.corridas.forEach { corrida -> FilaCorrida(corrida) }
                    }
                }
                uiState.errorCorridas?.let { error ->
                    Text(error, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 12.dp))
                }
            }
        }
    }
}

@Composable
private fun FilaCorrida(corrida: AutomationRun) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(12.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                corrida.startedAt?.let { formatearFechaHora(it) } ?: "—",
                style = MaterialTheme.typography.bodySmall,
            )
            Text(
                ETIQUETAS_ESTADO_RUN[corrida.status] ?: corrida.status,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

private fun triggerLabel(automatizacion: Automation): String =
    if (automatizacion.trigger.kind == "schedule") automatizacion.trigger.rrule ?: "" else "Webhook"
