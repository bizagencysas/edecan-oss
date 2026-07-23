package cc.edecan.app.ui

import android.Manifest
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.viewmodel.compose.viewModel
import cc.edecan.app.vm.PerfilViewModel
import cc.edecan.app.vm.SessionViewModel
import cc.edecan.shared.LiveProfile
import cc.edecan.shared.ProfileIdentity
import cc.edecan.shared.nombrePila
import cc.edecan.app.notifications.EdecanNotifications

/**
 * Pestaña personal. Igual que iOS, aquí no se administran API keys ni
 * infraestructura: la persona ve su identidad, su Edecán y accesos útiles.
 * El editor usa `/v1/perfil`, la misma fuente de verdad del panel de
 * computador y del system prompt de cada conversación.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PerfilScreen(
    sessionViewModel: SessionViewModel = viewModel(),
    perfilViewModel: PerfilViewModel = viewModel(),
    onAbrirContenido: () -> Unit = {},
    onAbrirIde: () -> Unit = {},
    onAbrirCapacidades: () -> Unit = {},
    onAbrirNegocios: () -> Unit = {},
) {
    val sessionState by sessionViewModel.uiState.collectAsState()
    val perfilState by perfilViewModel.uiState.collectAsState()
    var editandoPerfil by remember { mutableStateOf(false) }
    var confirmarSalida by remember { mutableStateOf(false) }
    val context = LocalContext.current
    var permisoAvisos by remember { mutableStateOf(EdecanNotifications.permissionGranted(context)) }
    val pedirAvisos = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        permisoAvisos = granted
        if (granted) EdecanNotifications.refreshRemoteRegistration(context)
    }

    LaunchedEffect(sessionViewModel.api) {
        sessionViewModel.api?.let { perfilViewModel.cargarPerfil(it) }
    }

    if (editandoPerfil) {
        EditorPerfil(
            perfil = perfilState.perfilVivo,
            cargando = perfilState.cargandoPerfil,
            guardando = perfilState.guardandoPerfil,
            error = perfilState.errorPerfil,
            onVolver = { editandoPerfil = false },
            onGuardar = { identidad, resumen ->
                sessionViewModel.api?.let { api ->
                    perfilViewModel.guardarPerfil(api, identidad, resumen) {
                        editandoPerfil = false
                    }
                }
            },
        )
        return
    }

    val identidad = perfilState.perfilVivo?.datos?.identidad
    val nombre = identidad?.nombrePreferido?.trim().orEmpty().ifBlank {
        sessionState.me?.nombrePila?.replaceFirstChar { it.uppercase() } ?: "Tu perfil"
    }

    Scaffold(topBar = { TopAppBar(title = { Text("Tú") }) }) { padding ->
        Column(
            modifier = Modifier
                .padding(padding)
                .padding(16.dp)
                .fillMaxWidth()
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(
                    modifier = Modifier.padding(24.dp).fillMaxWidth(),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    Text(nombre.take(1).uppercase(), style = MaterialTheme.typography.displaySmall)
                    Text(nombre, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                    sessionState.me?.let {
                        Text(it.user.email, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Text(it.tenant.name, style = MaterialTheme.typography.bodySmall)
                    }
                }
            }

            Card(
                onClick = {
                    if (Build.VERSION.SDK_INT >= 33 && !permisoAvisos) {
                        pedirAvisos.launch(Manifest.permission.POST_NOTIFICATIONS)
                    } else {
                        EdecanNotifications.refreshRemoteRegistration(context)
                    }
                },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Column(modifier = Modifier.padding(18.dp)) {
                    Text("Avisos", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                    Text(
                        when {
                            !permisoAvisos -> "Activa recordatorios, llamadas y trabajos terminados"
                            EdecanNotifications.remoteConfigured(context) -> "Avisos locales y remotos activados"
                            else -> "Avisos locales activados · push remoto es opcional en OSS"
                        },
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(top = 4.dp),
                    )
                }
            }

            Card(onClick = { editandoPerfil = true }, modifier = Modifier.fillMaxWidth()) {
                Column(modifier = Modifier.padding(18.dp)) {
                    Text("Perfil", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                    Text(
                        "Tu nombre, contexto y cómo quieres que Edecán te hable",
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(top = 4.dp),
                    )
                    if (perfilState.cargandoPerfil) {
                        CircularProgressIndicator(modifier = Modifier.padding(top = 10.dp))
                    }
                }
            }

            Card(modifier = Modifier.fillMaxWidth()) {
                Column(modifier = Modifier.padding(18.dp)) {
                    Text("Edecán · Listo", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                    Text(
                        "Tu asistente personal para pensar, crear, organizar y hacer.",
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(top = 4.dp),
                    )
                }
            }

            Card(modifier = Modifier.fillMaxWidth()) {
                Column(
                    modifier = Modifier.padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Text("TU EDECÁN", style = MaterialTheme.typography.labelMedium)
                    Button(onClick = onAbrirContenido, modifier = Modifier.fillMaxWidth()) {
                        Text("Crear contenido")
                    }
                    Button(onClick = onAbrirCapacidades, modifier = Modifier.fillMaxWidth()) {
                        Text("Capacidades")
                    }
                }
            }

            Card(modifier = Modifier.fillMaxWidth()) {
                Column(
                    modifier = Modifier.padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Text("Modo avanzado", style = MaterialTheme.typography.titleMedium)
                    Text(
                        "Herramientas especializadas cuando quieras construir o dirigir.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        Button(onClick = onAbrirIde, modifier = Modifier.weight(1f)) {
                            Text("Construir")
                        }
                        Button(onClick = onAbrirNegocios, modifier = Modifier.weight(1f)) {
                            Text("Negocios")
                        }
                    }
                }
            }

            Button(
                onClick = { confirmarSalida = true },
                colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.errorContainer),
                modifier = Modifier.align(Alignment.CenterHorizontally),
            ) {
                Text("Desvincular este teléfono", color = MaterialTheme.colorScheme.onErrorContainer)
            }
        }
    }

    if (confirmarSalida) {
        AlertDialog(
            onDismissRequest = { confirmarSalida = false },
            title = { Text("¿Desvincular este teléfono?") },
            text = { Text("Para volver a usar Edecán tendrás que escanear el QR de tu computadora.") },
            confirmButton = {
                TextButton(onClick = {
                    confirmarSalida = false
                    sessionViewModel.cerrarSesion()
                }) { Text("Desvincular") }
            },
            dismissButton = {
                TextButton(onClick = { confirmarSalida = false }) { Text("Cancelar") }
            },
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun EditorPerfil(
    perfil: LiveProfile?,
    cargando: Boolean,
    guardando: Boolean,
    error: String?,
    onVolver: () -> Unit,
    onGuardar: (ProfileIdentity, String) -> Unit,
) {
    var identidad by remember { mutableStateOf(ProfileIdentity()) }
    var resumen by remember { mutableStateOf("") }

    LaunchedEffect(perfil?.version) {
        perfil?.let {
            identidad = it.datos.identidad
            resumen = it.resumen
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Perfil") },
                navigationIcon = { TextButton(onClick = onVolver) { Text("Volver") } },
                actions = {
                    TextButton(
                        onClick = { onGuardar(identidad, resumen) },
                        enabled = !cargando && !guardando,
                    ) { Text(if (guardando) "Guardando…" else "Guardar") }
                },
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .padding(padding)
                .padding(16.dp)
                .fillMaxWidth()
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            if (cargando) CircularProgressIndicator()
            Text("Cómo identificarte", style = MaterialTheme.typography.titleMedium)
            Campo("Nombre preferido", identidad.nombrePreferido) { identidad = identidad.copy(nombrePreferido = it) }
            Campo("Nombre completo", identidad.nombreCompleto) { identidad = identidad.copy(nombreCompleto = it) }
            Campo("Pronombres", identidad.pronombres) { identidad = identidad.copy(pronombres = it) }
            Campo("Fecha de nacimiento", identidad.fechaNacimiento) { identidad = identidad.copy(fechaNacimiento = it) }

            Text("Tu contexto", style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(top = 8.dp))
            Campo("País", identidad.pais) { identidad = identidad.copy(pais = it) }
            Campo("Ciudad", identidad.ciudad) { identidad = identidad.copy(ciudad = it) }
            Campo("Zona horaria", identidad.zonaHoraria) { identidad = identidad.copy(zonaHoraria = it) }
            Campo("A qué te dedicas", identidad.ocupacion) { identidad = identidad.copy(ocupacion = it) }
            Campo("Idioma preferido", identidad.idiomaPreferido) { identidad = identidad.copy(idiomaPreferido = it) }
            Campo("Cómo quieres que te hable", identidad.formaDeTrato, 3) { identidad = identidad.copy(formaDeTrato = it) }
            Campo("Sobre ti", identidad.biografia, 5) { identidad = identidad.copy(biografia = it) }
            Campo("Síntesis de preferencias, proyectos y objetivos", resumen, 4) { resumen = it }

            error?.let {
                Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
            }
            Button(
                onClick = { onGuardar(identidad, resumen) },
                enabled = !cargando && !guardando,
                modifier = Modifier.fillMaxWidth(),
            ) {
                if (guardando) CircularProgressIndicator() else Text("Guardar mi perfil")
            }
        }
    }
}

@Composable
private fun Campo(label: String, value: String, minLines: Int = 1, onValueChange: (String) -> Unit) {
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        label = { Text(label) },
        minLines = minLines,
        maxLines = if (minLines == 1) 1 else minLines + 3,
        modifier = Modifier.fillMaxWidth(),
    )
}
