@file:OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)

package cc.edecan.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import cc.edecan.app.ui.components.EmptyState
import cc.edecan.app.ui.theme.EdecanColors
import cc.edecan.app.vm.LlmKind
import cc.edecan.app.vm.PerfilUiState
import cc.edecan.app.vm.PerfilViewModel
import cc.edecan.app.vm.SessionViewModel

/**
 * Pestaña "Perfil". El encabezado (correo, tenant, plan), "Cerrar sesión" y
 * la sección "Conectar LLM" (`GET/PUT /v1/credentials`, `GET
 * /v1/setup/status` — "pegar y validar", `DIRECCION_ACTUAL.md`) son reales;
 * editar persona/tema/notificaciones sigue siendo placeholder. Mismo
 * encabezado que `PerfilView.swift` (iOS); la sección de credenciales es
 * nueva de este work package (WP-V4-04).
 */
@Composable
fun PerfilScreen(
    sessionViewModel: SessionViewModel = viewModel(),
    perfilViewModel: PerfilViewModel = viewModel(),
) {
    val uiState by sessionViewModel.uiState.collectAsState()
    val perfilState by perfilViewModel.uiState.collectAsState()
    var mostrarConfirmacionSalir by remember { mutableStateOf(false) }

    LaunchedEffect(sessionViewModel.api) { sessionViewModel.api?.let { perfilViewModel.cargar(it) } }

    Scaffold(topBar = { TopAppBar(title = { Text("Perfil") }) }) { padding ->
        // `verticalScroll` (no `LazyColumn`): esta pantalla no tiene ningún
        // componente Lazy en su árbol (Card/Column/OutlinedTextField/
        // FilterChip/EmptyState, todos "normales"), así que scrollear el
        // Column entero es seguro — evita que el formulario "Conectar LLM"
        // (varios campos según el `kind`) empuje el botón "Cerrar sesión"
        // fuera de la pantalla en equipos chicos.
        Column(
            modifier = Modifier
                .padding(padding)
                .padding(16.dp)
                .fillMaxWidth()
                .verticalScroll(rememberScrollState()),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(20.dp),
        ) {
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(
                    modifier = Modifier.padding(24.dp).fillMaxWidth(),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    Text("👤", style = MaterialTheme.typography.displaySmall)
                    val me = uiState.me
                    if (me != null) {
                        Text(me.user.email, style = MaterialTheme.typography.titleMedium)
                        Text(
                            "${me.tenant.name} · plan ${me.tenant.planKey}",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    } else if (uiState.cargandoMe) {
                        CircularProgressIndicator(modifier = Modifier.padding(8.dp))
                    }
                }
            }

            SeccionConectarLlm(
                perfilState = perfilState,
                onElegirKind = perfilViewModel::elegirKind,
                onConectar = { apiKey, baseUrl, modelPrincipal ->
                    sessionViewModel.api?.let {
                        perfilViewModel.conectarLlm(it, apiKey, baseUrl, modelPrincipal)
                    }
                },
            )

            EmptyState(
                emoji = "⚙️",
                titulo = "Más ajustes en camino",
                descripcion = "Editar tu persona (tono, formalidad, instrucciones), tema de la app " +
                    "y dispositivos emparejados van a vivir aquí — hoy se editan desde el panel web.",
                modifier = Modifier.fillMaxWidth(),
                etiquetaRoadmap = null,
            )

            Button(
                onClick = { mostrarConfirmacionSalir = true },
                colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.errorContainer),
            ) {
                Text("Cerrar sesión", color = MaterialTheme.colorScheme.onErrorContainer)
            }
        }
    }

    if (mostrarConfirmacionSalir) {
        AlertDialog(
            onDismissRequest = { mostrarConfirmacionSalir = false },
            title = { Text("¿Cerrar sesión en este dispositivo?") },
            confirmButton = {
                TextButton(onClick = {
                    mostrarConfirmacionSalir = false
                    sessionViewModel.cerrarSesion()
                }) { Text("Cerrar sesión") }
            },
            dismissButton = {
                TextButton(onClick = { mostrarConfirmacionSalir = false }) { Text("Cancelar") }
            },
        )
    }
}

@Composable
private fun SeccionConectarLlm(
    perfilState: PerfilUiState,
    onElegirKind: (LlmKind) -> Unit,
    onConectar: (apiKey: String, baseUrl: String, modelPrincipal: String) -> Unit,
) {
    var apiKey by remember { mutableStateOf("") }
    var baseUrl by remember { mutableStateOf("") }
    var modelPrincipal by remember { mutableStateOf("") }

    val kindsDisponibles = LlmKind.entries.filter { !it.soloLocal || perfilState.setupStatus?.localMode == true }
    val kind = perfilState.kindSeleccionado

    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(16.dp)) {
            Text("Conectar LLM", style = MaterialTheme.typography.titleSmall)

            perfilState.credenciales?.llm?.let { conectado ->
                val etiquetaConectada = LlmKind.entries.find { it.valor == conectado.kind }?.etiqueta ?: conectado.kind
                Text(
                    "Conectado ahora: $etiquetaConectada" +
                        (conectado.masked?.let { " ($it)" } ?: "") +
                        (conectado.modelPrincipal?.let { " · $it" } ?: ""),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 4.dp),
                )
            }

            FlowRow(
                modifier = Modifier.padding(top = 12.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                kindsDisponibles.forEach { candidato ->
                    FilterChip(
                        selected = candidato == kind,
                        onClick = { onElegirKind(candidato) },
                        label = { Text(candidato.etiqueta) },
                    )
                }
            }

            Column(modifier = Modifier.padding(top = 12.dp)) {
                if (kind.aceptaApiKey) {
                    OutlinedTextField(
                        value = apiKey,
                        onValueChange = { apiKey = it },
                        label = { Text(if (kind.apiKeyObligatoria) "API key" else "API key (opcional)") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp),
                    )
                }
                if (kind.aceptaBaseUrl) {
                    OutlinedTextField(
                        value = baseUrl,
                        onValueChange = { baseUrl = it },
                        label = {
                            Text(
                                if (kind == LlmKind.OLLAMA) "URL base (opcional, http://localhost:11434 por defecto)"
                                else "URL base",
                            )
                        },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp),
                    )
                }
                if (kind == LlmKind.OLLAMA) {
                    OutlinedTextField(
                        value = modelPrincipal,
                        onValueChange = { modelPrincipal = it },
                        label = { Text("Modelo ya descargado (p. ej. llama3.1)") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp),
                    )
                } else if (kind.aceptaApiKey) {
                    OutlinedTextField(
                        value = modelPrincipal,
                        onValueChange = { modelPrincipal = it },
                        label = { Text("Modelo (opcional, usa el que recomienda Edecán por defecto)") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp),
                    )
                }
                if (kind.soloLocal && kind != LlmKind.OLLAMA) {
                    Text(
                        "Edecán va a correr «${if (kind == LlmKind.CLAUDE_CLI) "claude" else "codex"} --version» " +
                            "en esta máquina para confirmar que está instalado — no hace falta ninguna clave.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(bottom = 8.dp),
                    )
                }

                perfilState.errorConexion?.let { error ->
                    Text(
                        error,
                        color = MaterialTheme.colorScheme.error,
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.padding(bottom = 8.dp),
                    )
                }
                if (perfilState.conectadoOk) {
                    Text(
                        "Conectado ✅",
                        color = VerdeExito,
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.padding(bottom = 8.dp),
                    )
                }

                Button(
                    onClick = { onConectar(apiKey, baseUrl, modelPrincipal) },
                    enabled = !perfilState.conectando &&
                        (!kind.apiKeyObligatoria || apiKey.isNotBlank()) &&
                        (kind != LlmKind.OPENAI_COMPAT || baseUrl.isNotBlank()) &&
                        (kind != LlmKind.OLLAMA || modelPrincipal.isNotBlank()),
                    colors = ButtonDefaults.buttonColors(containerColor = EdecanColors.Morado),
                ) {
                    if (perfilState.conectando) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(16.dp).padding(end = 4.dp),
                            color = Color.White,
                            strokeWidth = 2.dp,
                        )
                    }
                    Text("Conectar")
                }
            }
        }
    }
}

private val VerdeExito = Color(0xFF22C55E)
