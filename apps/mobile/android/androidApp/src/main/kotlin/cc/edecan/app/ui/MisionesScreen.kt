@file:OptIn(ExperimentalMaterial3Api::class)

package cc.edecan.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
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
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import cc.edecan.app.ui.components.EmptyState
import cc.edecan.app.ui.components.formatearFechaHora
import cc.edecan.app.ui.theme.EdecanColors
import cc.edecan.app.vm.MisionesUiState
import cc.edecan.app.vm.MisionesViewModel
import cc.edecan.app.vm.SessionViewModel
import cc.edecan.shared.Mission
import cc.edecan.shared.MissionStep
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import androidx.compose.ui.platform.LocalContext
import cc.edecan.app.notifications.EdecanNotifications
import cc.edecan.app.notifications.NotificationRoute

/** Etiquetas en español de `MissionOut.status`/`MissionStepOut.status`
 * (`edecan_schemas.missions.MISSION_STATUSES`/`MISSION_STEP_STATUSES`) —
 * mismo vocabulario que `MissionStatusBadge.tsx`/`StepStatusBadge` en el
 * panel web, para que ambas plataformas hablen igual. */
private val ETIQUETAS_ESTADO_MISION = mapOf(
    "planning" to "Planificando",
    "running" to "En curso",
    "waiting_confirmation" to "Esperando confirmación",
    "done" to "Completada",
    "error" to "Error",
    "cancelled" to "Cancelada",
    "pending" to "Pendiente",
    "skipped" to "Omitido",
)

private val COLOR_ESTADO_MISION = mapOf(
    "planning" to Color(0xFF94A3B8),
    "pending" to Color(0xFF94A3B8),
    "running" to EdecanColors.Azul,
    "waiting_confirmation" to Color(0xFFF59E0B),
    "done" to Color(0xFF22C55E),
    "error" to Color(0xFFEF4444),
    "cancelled" to Color(0xFF94A3B8),
    "skipped" to Color(0xFF94A3B8),
)

/**
 * Pestaña "Misiones" (`/v1/missions`, `ARCHITECTURE.md` §11,
 * `ROADMAP_V2.md` §7.4/§7.9, WP-V5-07): lista con badge por estado, alta de
 * misión, detalle con pasos + resultado, y tarjeta Aprobar/Rechazar cuando
 * la misión queda `waiting_confirmation` — todo cableado a los endpoints
 * reales de `EdecanApi`. Se llega acá desde Actividad (`InicioScreen`,
 * `RootNav.kt`), no es una pestaña de la barra inferior. Lógica real en
 * [MisionesViewModel]; esta pantalla solo dibuja su estado (lista o
 * detalle, según `uiState.seleccionId`).
 */
@Composable
fun MisionesScreen(
    sessionViewModel: SessionViewModel = viewModel(),
    misionesViewModel: MisionesViewModel = viewModel(),
    onVolver: () -> Unit = {},
) {
    val uiState by misionesViewModel.uiState.collectAsState()
    val api = sessionViewModel.api
    val context = LocalContext.current
    var estadosObservados by remember { mutableStateOf<Map<String, String>>(emptyMap()) }

    LaunchedEffect(api) { api?.let { misionesViewModel.cargar(it) } }
    LaunchedEffect(uiState.misiones.map { it.id to it.status }) {
        val current = uiState.misiones.associate { it.id to it.status }
        if (estadosObservados.isNotEmpty()) {
            uiState.misiones.filter { mission ->
                estadosObservados[mission.id]?.let { it != mission.status } == true &&
                    mission.status in setOf("done", "error", "cancelled")
            }.forEach { mission ->
                EdecanNotifications.show(
                    context = context,
                    title = if (mission.status == "done") "Trabajo terminado" else "El trabajo necesita atención",
                    body = mission.objetivo,
                    channel = EdecanNotifications.WORK,
                    route = NotificationRoute.ACTIVITY,
                    stableId = mission.id.hashCode(),
                )
            }
        }
        estadosObservados = current
    }

    if (uiState.seleccionId != null) {
        DetalleMision(
            uiState = uiState,
            onVolver = misionesViewModel::cerrarDetalle,
            onAprobar = { api?.let { misionesViewModel.confirmar(it, true) } },
            onRechazar = { api?.let { misionesViewModel.confirmar(it, false) } },
        )
        return
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Misiones") },
                navigationIcon = { IconButton(onClick = onVolver) { Text("←") } },
            )
        },
    ) { padding ->
        Column(modifier = Modifier.padding(padding).fillMaxSize().padding(16.dp)) {
            FormularioNuevaMision(
                creando = uiState.creando,
                error = uiState.errorCrear,
                onCrear = { objetivo -> api?.let { misionesViewModel.crear(it, objetivo) } },
            )

            Text(
                "Tus misiones",
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
                uiState.cargando && uiState.misiones.isEmpty() -> Box(modifier = Modifier.fillMaxSize()) {
                    CircularProgressIndicator(modifier = Modifier.align(Alignment.Center))
                }
                uiState.misiones.isEmpty() -> EmptyState(
                    emoji = "🧭",
                    titulo = "Sin misiones todavía",
                    descripcion = "Crea una arriba, o pídeselo a tu asistente en el chat con delegar_mision.",
                    etiquetaRoadmap = null,
                )
                else -> LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    items(uiState.misiones, key = { it.id }) { mision ->
                        FilaMision(mision) { api?.let { misionesViewModel.seleccionar(it, mision.id) } }
                    }
                }
            }
        }
    }
}

@Composable
private fun FormularioNuevaMision(creando: Boolean, error: String?, onCrear: (String) -> Unit) {
    var objetivo by remember { mutableStateOf("") }

    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(16.dp)) {
            Text("Nueva misión", style = MaterialTheme.typography.titleSmall)
            OutlinedTextField(
                value = objetivo,
                onValueChange = { objetivo = it },
                placeholder = { Text("Ej: Investiga a mis 3 principales competidores y resume sus precios.") },
                minLines = 2,
                maxLines = 4,
                modifier = Modifier.fillMaxWidth().padding(top = 10.dp, bottom = 8.dp),
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
                onClick = { onCrear(objetivo); objetivo = "" },
                enabled = !creando && objetivo.isNotBlank(),
                colors = ButtonDefaults.buttonColors(containerColor = EdecanColors.Morado),
                modifier = Modifier.fillMaxWidth(),
            ) {
                if (creando) {
                    CircularProgressIndicator(
                        modifier = Modifier.padding(end = 8.dp),
                        color = Color.White,
                    )
                }
                Text("Crear misión")
            }
        }
    }
}

@Composable
private fun FilaMision(mision: Mission, onClick: () -> Unit) {
    Card(modifier = Modifier.fillMaxWidth(), onClick = onClick) {
        Column(modifier = Modifier.padding(14.dp)) {
            Text(
                mision.objetivo,
                style = MaterialTheme.typography.bodyMedium,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
            Row(
                modifier = Modifier.fillMaxWidth().padding(top = 6.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    formatearFechaHora(mision.createdAt),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                EstadoBadgeMision(mision.status)
            }
        }
    }
}

@Composable
private fun DetalleMision(
    uiState: MisionesUiState,
    onVolver: () -> Unit,
    onAprobar: () -> Unit,
    onRechazar: () -> Unit,
) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(uiState.detalle?.mission?.objetivo ?: "Misión", maxLines = 1, overflow = TextOverflow.Ellipsis) },
                navigationIcon = { IconButton(onClick = onVolver) { Text("←") } },
            )
        },
    ) { padding ->
        Box(modifier = Modifier.padding(padding).fillMaxSize()) {
            val detalle = uiState.detalle
            when {
                uiState.cargandoDetalle && detalle == null ->
                    CircularProgressIndicator(modifier = Modifier.align(Alignment.Center))
                detalle == null -> EmptyState(
                    emoji = "🧭",
                    titulo = "No se pudo cargar la misión",
                    descripcion = uiState.errorDetalle ?: "Vuelve a intentarlo.",
                    etiquetaRoadmap = null,
                )
                else -> {
                    val mision = detalle.mission
                    Column(
                        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
                    ) {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text(
                                "Creada ${formatearFechaHora(mision.createdAt)}",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                            EstadoBadgeMision(mision.status)
                        }

                        mision.error?.let { error ->
                            Text(
                                error,
                                color = MaterialTheme.colorScheme.error,
                                style = MaterialTheme.typography.bodySmall,
                                modifier = Modifier.padding(top = 12.dp),
                            )
                        }

                        mision.resultado?.let { resultado ->
                            Card(
                                modifier = Modifier.fillMaxWidth().padding(top = 12.dp),
                                colors = CardDefaults.cardColors(containerColor = Color(0xFF22C55E).copy(alpha = 0.10f)),
                            ) {
                                Column(modifier = Modifier.padding(14.dp)) {
                                    Text(
                                        "RESULTADO",
                                        style = MaterialTheme.typography.labelSmall,
                                        color = Color(0xFF15803D),
                                    )
                                    Text(resultado, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(top = 4.dp))
                                }
                            }
                        }

                        if (mision.status == "waiting_confirmation") {
                            TarjetaAprobacionMision(
                                steps = detalle.steps,
                                ocupado = uiState.accionOcupada,
                                onAprobar = onAprobar,
                                onRechazar = onRechazar,
                            )
                        }

                        Text(
                            "Pasos",
                            style = MaterialTheme.typography.titleSmall,
                            modifier = Modifier.padding(top = 20.dp, bottom = 8.dp),
                        )
                        if (detalle.steps.isEmpty()) {
                            Text(
                                "Todavía no hay pasos planificados.",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        } else {
                            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                                detalle.steps.forEach { paso -> FilaPaso(paso) }
                            }
                        }

                        uiState.errorDetalle?.let { error ->
                            Text(
                                error,
                                color = MaterialTheme.colorScheme.error,
                                style = MaterialTheme.typography.bodySmall,
                                modifier = Modifier.padding(top = 12.dp),
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun FilaPaso(paso: MissionStep) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(14.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    "#${paso.seq}",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(end = 6.dp),
                )
                Text(
                    formatearNombreAgente(paso.agente),
                    style = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.weight(1f),
                )
                EstadoBadgeMision(paso.status)
            }
            Text(
                paso.instruccion,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 6.dp),
            )
            paso.resultado?.let { resultado ->
                Text(
                    resultado,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(top = 8.dp)
                        .clip(RoundedCornerShape(10.dp))
                        .background(MaterialTheme.colorScheme.surfaceVariant)
                        .padding(10.dp),
                )
            }
        }
    }
}

/** Tarjeta Aprobar/Rechazar cuando la misión queda `waiting_confirmation` —
 * mismo endpoint (`POST /v1/missions/{id}/confirm`) que la tarjeta de
 * `ChatScreen`, mismo estilo visual. Si el paso pendiente trae
 * `usage.pending_tool_call` (`StepTimeline.tsx` en el panel web), muestra
 * también qué herramienta quiere usar el agente — puramente informativo, los
 * botones funcionan igual aunque no se pueda leer ese detalle. */
@Composable
private fun TarjetaAprobacionMision(
    steps: List<MissionStep>,
    ocupado: Boolean,
    onAprobar: () -> Unit,
    onRechazar: () -> Unit,
) {
    val herramienta = herramientaPendiente(steps)
    Card(
        modifier = Modifier.fillMaxWidth().padding(top = 16.dp),
        colors = CardDefaults.cardColors(containerColor = EdecanColors.Morado.copy(alpha = 0.10f)),
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Text("Edecán necesita tu confirmación", style = MaterialTheme.typography.titleSmall)
            if (herramienta != null) {
                Text(
                    "Quiere usar «${herramienta.first}»" + (herramienta.second.takeIf { it.isNotBlank() }?.let { " ($it)" } ?: ""),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 4.dp),
                )
            }
            Row(modifier = Modifier.padding(top = 12.dp)) {
                OutlinedButton(onClick = onRechazar, enabled = !ocupado) { Text("Rechazar") }
                Spacer(modifier = Modifier.padding(start = 8.dp))
                Button(
                    onClick = onAprobar,
                    enabled = !ocupado,
                    colors = ButtonDefaults.buttonColors(containerColor = EdecanColors.Morado),
                ) { Text("Aprobar") }
            }
        }
    }
}

@Composable
private fun EstadoBadgeMision(estado: String) {
    val color = COLOR_ESTADO_MISION[estado] ?: Color(0xFF94A3B8)
    Box(
        modifier = Modifier
            .clip(RoundedCornerShape(8.dp))
            .background(color.copy(alpha = 0.15f))
            .padding(horizontal = 8.dp, vertical = 3.dp),
    ) {
        Text(ETIQUETAS_ESTADO_MISION[estado] ?: estado, style = MaterialTheme.typography.labelSmall, color = color)
    }
}

/** `"investigador"` -> `"Investigador"` — mismo criterio que
 * `formatAgentName` en `StepTimeline.tsx` (panel web), adaptado a Kotlin. */
private fun formatearNombreAgente(agente: String): String =
    agente.split("_").joinToString(" ") { parte -> parte.replaceFirstChar { it.uppercase() } }

/** Lee `usage.pending_tool_call.{name,args}` del último paso
 * `waiting_confirmation` (mismo *shape* que `MissionStep.usage` en
 * `apps/web/src/lib/api-misiones.ts`) — devuelve `null` si no hay ninguno o
 * el JSON no tiene la forma esperada, nunca lanza. */
private fun herramientaPendiente(steps: List<MissionStep>): Pair<String, String>? {
    val paso = steps.lastOrNull { it.status == "waiting_confirmation" } ?: return null
    val usage = paso.usage as? JsonObject ?: return null
    val pendingCall = usage["pending_tool_call"] as? JsonObject ?: return null
    val nombre = (pendingCall["name"] as? JsonPrimitive)?.contentOrNull ?: return null
    val args = pendingCall["args"] as? JsonObject
    val argsTexto = args?.entries?.joinToString(" · ") { (clave, valor) ->
        "$clave: ${(valor as? JsonPrimitive)?.contentOrNull ?: valor}"
    } ?: ""
    return nombre to argsTexto
}
