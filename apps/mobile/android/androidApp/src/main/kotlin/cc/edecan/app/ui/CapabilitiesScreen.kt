@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package cc.edecan.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import cc.edecan.app.vm.SessionViewModel
import cc.edecan.shared.ApiException
import cc.edecan.shared.McpServerSummary
import cc.edecan.shared.SkillSummary

@Composable
fun CapabilitiesScreen(
    onVolver: () -> Unit,
    sessionViewModel: SessionViewModel = viewModel(),
) {
    var skills by remember { mutableStateOf<List<SkillSummary>>(emptyList()) }
    var servers by remember { mutableStateOf<List<McpServerSummary>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var skillsError by remember { mutableStateOf<String?>(null) }
    var mcpError by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(sessionViewModel.api) {
        val api = sessionViewModel.api ?: return@LaunchedEffect
        loading = true
        try { skills = api.listSkills() } catch (_: ApiException) {
            skillsError = "No se pudieron consultar las habilidades."
        }
        try { servers = api.listMcpServers() } catch (error: ApiException) {
            mcpError = if ((error as? ApiException.Servidor)?.status == 403) {
                "Las conexiones externas no están habilitadas en este plan."
            } else "No se pudieron consultar las conexiones externas."
        }
        loading = false
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Capacidades") },
                navigationIcon = { TextButton(onClick = onVolver) { Text("Atrás") } },
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier.padding(padding).padding(16.dp).verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            Card(modifier = Modifier.fillMaxWidth()) {
                Text(
                    "✨ No tienes que elegir herramientas. Dile a Edecan qué necesitas y él usa la capacidad correcta.",
                    modifier = Modifier.padding(16.dp),
                    style = MaterialTheme.typography.bodyMedium,
                )
            }
            Text("Habilidades", style = MaterialTheme.typography.titleMedium)
            skillsError?.let { Text(it, color = MaterialTheme.colorScheme.onSurfaceVariant) }
            if (!loading && skills.isEmpty() && skillsError == null) {
                Text("No hay habilidades adicionales instaladas.")
            }
            skills.forEach { skill ->
                CapabilityCard(
                    icon = if (skill.enabled) "✅" else "⏸️",
                    title = skill.nombre,
                    detail = skill.descripcion,
                    status = if (skill.enabled) "Activa" else "Pausada",
                )
            }
            Text("Conexiones externas", style = MaterialTheme.typography.titleMedium)
            mcpError?.let { Text(it, color = MaterialTheme.colorScheme.onSurfaceVariant) }
            if (!loading && servers.isEmpty() && mcpError == null) {
                Text("No hay servicios externos conectados.")
            }
            servers.forEach { server ->
                CapabilityCard(
                    icon = "🔗",
                    title = server.nombre,
                    detail = if (server.transporte == "http") "Servicio web" else "Herramienta local",
                    status = if (server.estado == "active") "Conectada" else server.estado,
                )
            }
            if (loading) CircularProgressIndicator()
        }
    }
}

@Composable
private fun CapabilityCard(icon: String, title: String, detail: String, status: String) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier.padding(14.dp).fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text(icon)
            Column(modifier = Modifier.weight(1f)) {
                Text(title, style = MaterialTheme.typography.titleSmall)
                Text(detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Text(status, style = MaterialTheme.typography.labelSmall)
        }
    }
}
