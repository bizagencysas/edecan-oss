@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package cc.edecan.app.ui

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
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
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import cc.edecan.app.vm.ContentStudioViewModel
import cc.edecan.app.vm.SessionViewModel
import cc.edecan.shared.StudioActionRequest
import cc.edecan.shared.StudioExportFormat
import cc.edecan.shared.StudioProjectMode
import cc.edecan.shared.StudioProjectQuality
import cc.edecan.shared.StudioProjectSummary
import cc.edecan.shared.StudioRevision
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private enum class StudioPage { PROJECTS, CREATE }

/** Workspace creativo contextual. No expone herramientas internas: una frase
 * crea un proyecto reversible; las peticiones multimedia más abiertas vuelven
 * al chat, donde ya existen progreso, confirmaciones y artefactos privados. */
@Composable
fun ContentStudioScreen(
    onVolver: () -> Unit = {},
    onOpenChat: (String) -> Unit = {},
    sessionViewModel: SessionViewModel = viewModel(),
    contentViewModel: ContentStudioViewModel = viewModel(),
) {
    val uiState by contentViewModel.uiState.collectAsState()
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var pageName by rememberSaveable { mutableStateOf(StudioPage.PROJECTS.name) }
    val page = StudioPage.valueOf(pageName)

    var prompt by rememberSaveable { mutableStateOf("") }
    var projectName by rememberSaveable { mutableStateOf("") }
    var brandName by rememberSaveable { mutableStateOf("") }
    var modeName by rememberSaveable { mutableStateOf(StudioProjectMode.GENERAL.name) }
    var qualityName by rememberSaveable { mutableStateOf(StudioProjectQuality.BALANCED.name) }
    var count by rememberSaveable { mutableStateOf(2) }
    var instruction by rememberSaveable { mutableStateOf("") }
    var universalRequest by rememberSaveable { mutableStateOf("") }

    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        val api = sessionViewModel.api
        if (uri != null && api != null) {
            scope.launch {
                try {
                    val local = withContext(Dispatchers.IO) {
                        prepararArchivoSeleccionado(context.applicationContext, uri)
                    }
                    contentViewModel.uploadReference(api, local)
                } catch (error: Throwable) {
                    contentViewModel.setNotice(
                        "No pude preparar ese archivo: ${error.message ?: "error desconocido"}",
                    )
                }
            }
        }
    }

    uiState.previewArtifact?.let { artifact ->
        SecurePreviewDialog(
            target = SecurePreviewTarget.Artifact(artifact),
            api = sessionViewModel.api,
            onDismiss = contentViewModel::clearPreview,
        )
    }

    LaunchedEffect(sessionViewModel.api) {
        sessionViewModel.api?.let(contentViewModel::loadProjects)
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(uiState.project?.name ?: "Crear") },
                navigationIcon = {
                    IconButton(
                        onClick = {
                            if (uiState.project != null) {
                                contentViewModel.closeProject()
                                sessionViewModel.api?.let(contentViewModel::loadProjects)
                            } else onVolver()
                        },
                    ) { Text("←") }
                },
            )
        },
    ) { padding ->
        Column(
            verticalArrangement = Arrangement.spacedBy(14.dp),
            modifier = Modifier
                .padding(padding)
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
        ) {
            if (uiState.project != null) {
                ProjectDetail(
                    project = uiState.project!!,
                    revisions = uiState.revisions,
                    selectedRevisionId = uiState.selectedRevisionId,
                    instruction = instruction,
                    onInstructionChange = { instruction = it },
                    working = uiState.working,
                    hasImagePreview = uiState.artifacts.any { it.mime?.startsWith("image/") == true },
                    onOpenCurrentPreview = {
                        // La misma descarga Bearer del visor; no se comparte una URL.
                        sessionViewModel.api?.let { api ->
                            contentViewModel.openFormat(api, StudioExportFormat.PNG)
                        }
                    },
                    onFormat = { format ->
                        sessionViewModel.api?.let { contentViewModel.openFormat(it, format) }
                    },
                    onEdit = {
                        sessionViewModel.api?.let { api ->
                            contentViewModel.edit(api, instruction)
                        }
                    },
                    onRevision = { revision ->
                        contentViewModel.selectRevision(revision.id)
                        sessionViewModel.api?.let { contentViewModel.openFormat(it, StudioExportFormat.PNG) }
                    },
                )
            } else {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                    StudioPage.entries.forEach { option ->
                        val selected = page == option
                        if (selected) {
                            Button(
                                onClick = { pageName = option.name },
                                modifier = Modifier.weight(1f),
                            ) { Text(if (option == StudioPage.PROJECTS) "Mis proyectos" else "Crear algo") }
                        } else {
                            OutlinedButton(
                                onClick = { pageName = option.name },
                                modifier = Modifier.weight(1f),
                            ) { Text(if (option == StudioPage.PROJECTS) "Mis proyectos" else "Crear algo") }
                        }
                    }
                }

                if (page == StudioPage.PROJECTS) {
                    ProjectLibrary(
                        projects = uiState.projects,
                        working = uiState.working,
                        onCreate = { pageName = StudioPage.CREATE.name },
                        onOpen = { project ->
                            sessionViewModel.api?.let { contentViewModel.openProject(it, project) }
                        },
                        onRefresh = {
                            sessionViewModel.api?.let(contentViewModel::loadProjects)
                        },
                    )
                } else {
                    Text("¿Qué quieres crear?", style = MaterialTheme.typography.headlineSmall)
                    Text(
                        "Dilo como se lo dirías a una persona. Edecán se encarga del resto.",
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    OutlinedTextField(
                        value = prompt,
                        onValueChange = { prompt = it },
                        label = { Text("Ej. Una página elegante para mi cafetería") },
                        minLines = 4,
                        maxLines = 9,
                        enabled = !uiState.working,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    SimpleMenu(
                        label = "Tipo",
                        value = StudioProjectMode.valueOf(modeName).label,
                        options = StudioProjectMode.entries.map { it.label },
                        enabled = !uiState.working,
                    ) { selected ->
                        modeName = StudioProjectMode.entries.first { it.label == selected }.name
                    }
                    Card(modifier = Modifier.fillMaxWidth()) {
                        Column(verticalArrangement = Arrangement.spacedBy(10.dp), modifier = Modifier.padding(14.dp)) {
                            OutlinedTextField(
                                value = projectName,
                                onValueChange = { projectName = it },
                                label = { Text("Nombre del proyecto (opcional)") },
                                modifier = Modifier.fillMaxWidth(),
                            )
                            OutlinedTextField(
                                value = brandName,
                                onValueChange = { brandName = it },
                                label = { Text("Marca (opcional)") },
                                modifier = Modifier.fillMaxWidth(),
                            )
                        }
                    }
                    SimpleMenu(
                        label = "Calidad",
                        value = StudioProjectQuality.valueOf(qualityName).label,
                        options = StudioProjectQuality.entries.map { it.label },
                        enabled = !uiState.working,
                    ) { selected ->
                        qualityName = StudioProjectQuality.entries.first { it.label == selected }.name
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                        Text("Propuestas distintas: $count", modifier = Modifier.weight(1f).padding(top = 12.dp))
                        OutlinedButton(onClick = { if (count > 1) count-- }, enabled = count > 1 && !uiState.working) { Text("−") }
                        OutlinedButton(onClick = { if (count < 4) count++ }, enabled = count < 4 && !uiState.working) { Text("+") }
                    }
                    Card(modifier = Modifier.fillMaxWidth()) {
                        Column(verticalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(14.dp)) {
                            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                                Column {
                                    Text("Referencias", fontWeight = FontWeight.Bold)
                                    Text("Imágenes, logos o documentos privados", style = MaterialTheme.typography.bodySmall)
                                }
                                TextButton(
                                    onClick = { picker.launch(arrayOf("*/*")) },
                                    enabled = !uiState.working && !uiState.uploadingReference && uiState.references.size < 12,
                                ) { Text(if (uiState.uploadingReference) "Subiendo…" else "+ Añadir") }
                            }
                            uiState.references.forEach { file ->
                                Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                                    Text("📎 ${file.filename}", maxLines = 1, modifier = Modifier.weight(1f))
                                    TextButton(onClick = { contentViewModel.removeReference(file.id) }) { Text("Quitar") }
                                }
                            }
                        }
                    }
                    Button(
                        enabled = prompt.isNotBlank() && !uiState.working && !uiState.uploadingReference && sessionViewModel.api != null,
                        onClick = {
                            sessionViewModel.api?.let { api ->
                                contentViewModel.create(
                                    api,
                                    StudioActionRequest(
                                        action = "create",
                                        prompt = prompt.trim(),
                                        projectName = projectName.trim().ifBlank { null },
                                        brandName = brandName.trim().ifBlank { null },
                                        mode = StudioProjectMode.valueOf(modeName),
                                        count = count,
                                        quality = StudioProjectQuality.valueOf(qualityName),
                                        files = uiState.references.map { it.id },
                                    ),
                                )
                            }
                        },
                        modifier = Modifier.fillMaxWidth(),
                    ) { Text(if (uiState.working) "Creando…" else "✨ Crear proyecto") }

                    UniversalCreationCard(
                        request = universalRequest,
                        onRequestChange = { universalRequest = it },
                        onPreset = { universalRequest = it },
                        onContinue = {
                            val request = universalRequest.trim()
                            if (request.isNotEmpty()) onOpenChat(request)
                        },
                    )
                }
            }

            uiState.errorMessage?.let { ErrorCard(it) }
            uiState.noticeMessage?.let { NoticeCard(it) }
            if (uiState.working) WorkingCard(uiState.stage)
        }
    }
}

@Composable
private fun ProjectLibrary(
    projects: List<StudioProjectSummary>,
    working: Boolean,
    onCreate: () -> Unit,
    onOpen: (StudioProjectSummary) -> Unit,
    onRefresh: () -> Unit,
) {
    Text("Todo lo que has creado", style = MaterialTheme.typography.headlineSmall)
    Text(
        "Cada cambio queda guardado. Abre un proyecto y continúa con una frase.",
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    if (projects.isEmpty() && !working) {
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp), modifier = Modifier.padding(18.dp)) {
                Text("✨ Tu Studio está listo", style = MaterialTheme.typography.titleMedium)
                Text("Crea una página, una app, un post o una presentación desde una sola frase.")
                Button(onClick = onCreate, modifier = Modifier.fillMaxWidth()) { Text("Crear mi primer proyecto") }
            }
        }
    } else {
        projects.forEach { project ->
            Card(onClick = { onOpen(project) }, enabled = !working, modifier = Modifier.fillMaxWidth()) {
                Row(modifier = Modifier.padding(14.dp).fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text(project.name, style = MaterialTheme.typography.titleMedium)
                        Text(
                            "${modeLabel(project.mode)} · ${project.revisionCount} versiones" +
                                (project.updatedAt?.let { " · ${it.take(10)}" } ?: ""),
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    Text("›")
                }
            }
        }
    }
    OutlinedButton(onClick = onRefresh, enabled = !working) { Text("↻ Actualizar") }
}

@Composable
private fun ProjectDetail(
    project: StudioProjectSummary,
    revisions: List<StudioRevision>,
    selectedRevisionId: String?,
    instruction: String,
    onInstructionChange: (String) -> Unit,
    working: Boolean,
    hasImagePreview: Boolean,
    onOpenCurrentPreview: () -> Unit,
    onFormat: (StudioExportFormat) -> Unit,
    onEdit: () -> Unit,
    onRevision: (StudioRevision) -> Unit,
) {
    Text(project.name, style = MaterialTheme.typography.headlineSmall)
    Text(
        "${modeLabel(project.mode)} · ${revisions.count { it.archivedAt == null }} versiones guardadas",
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(verticalArrangement = Arrangement.spacedBy(10.dp), modifier = Modifier.padding(14.dp)) {
            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text("Vista previa", style = MaterialTheme.typography.titleMedium)
                if (hasImagePreview) TextButton(onClick = onOpenCurrentPreview) { Text("Abrir") }
            }
            Text("Siempre se descarga de forma privada.", style = MaterialTheme.typography.bodySmall)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                StudioExportFormat.entries.forEach { format ->
                    OutlinedButton(
                        onClick = { onFormat(format) },
                        enabled = !working,
                        modifier = Modifier.weight(1f),
                    ) { Text(format.name) }
                }
            }
        }
    }
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(verticalArrangement = Arrangement.spacedBy(10.dp), modifier = Modifier.padding(14.dp)) {
            Text("Pídele un cambio", style = MaterialTheme.typography.titleMedium)
            OutlinedTextField(
                value = instruction,
                onValueChange = onInstructionChange,
                label = { Text("Ej. Haz el título más elegante") },
                minLines = 3,
                maxLines = 7,
                modifier = Modifier.fillMaxWidth(),
            )
            Button(
                onClick = onEdit,
                enabled = instruction.isNotBlank() && !working,
                modifier = Modifier.fillMaxWidth(),
            ) { Text("✨ Aplicar y guardar nueva versión") }
        }
    }
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(14.dp)) {
            Text("Variantes e historial", style = MaterialTheme.typography.titleMedium)
            revisions.asReversed().forEach { revision ->
                RevisionRow(
                    revision = revision,
                    selected = selectedRevisionId == revision.id,
                    enabled = !working && revision.archivedAt == null,
                    onClick = { onRevision(revision) },
                )
            }
        }
    }
}

@Composable
private fun RevisionRow(
    revision: StudioRevision,
    selected: Boolean,
    enabled: Boolean,
    onClick: () -> Unit,
) {
    OutlinedButton(onClick = onClick, enabled = enabled, modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.fillMaxWidth()) {
            Text("${if (selected) "✓ " else ""}${revision.label}", fontWeight = FontWeight.Bold)
            Text(
                listOfNotNull(
                    "${revision.width} × ${revision.height}".takeIf { revision.width > 0 },
                    revision.createdAt?.take(10),
                    revision.instruction.takeIf(String::isNotBlank),
                ).joinToString(" · "),
                style = MaterialTheme.typography.bodySmall,
                maxLines = 2,
            )
        }
    }
}

@Composable
private fun UniversalCreationCard(
    request: String,
    onRequestChange: (String) -> Unit,
    onPreset: (String) -> Unit,
    onContinue: () -> Unit,
) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(verticalArrangement = Arrangement.spacedBy(10.dp), modifier = Modifier.padding(14.dp)) {
            Text("¿Necesitas algo diferente?", style = MaterialTheme.typography.titleMedium)
            Text(
                "Imagen, video, campaña, producto, personaje o análisis: pídelo con naturalidad y continúa en el chat.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp), modifier = Modifier.fillMaxWidth()) {
                listOf(
                    "Imagen" to "Crea una imagen original para ",
                    "Video" to "Crea y planifica un video para ",
                    "Campaña" to "Crea una campaña completa para ",
                ).forEach { (label, value) ->
                    TextButton(onClick = { onPreset(value) }, modifier = Modifier.weight(1f)) { Text(label) }
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp), modifier = Modifier.fillMaxWidth()) {
                listOf(
                    "Producto" to "Diseña un producto completo para ",
                    "Personaje" to "Crea un personaje coherente para ",
                    "Analizar" to "Analiza esto a fondo y entrégame conclusiones accionables: ",
                ).forEach { (label, value) ->
                    TextButton(onClick = { onPreset(value) }, modifier = Modifier.weight(1f)) { Text(label) }
                }
            }
            OutlinedTextField(
                value = request,
                onValueChange = onRequestChange,
                label = { Text("Describe lo que necesitas") },
                minLines = 2,
                modifier = Modifier.fillMaxWidth(),
            )
            Button(onClick = onContinue, enabled = request.isNotBlank(), modifier = Modifier.fillMaxWidth()) {
                Text("Continuar en el chat")
            }
            Text(
                "Allí verás el progreso y los archivos a medida que Edecán trabaja.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun SimpleMenu(
    label: String,
    value: String,
    options: List<String>,
    enabled: Boolean = true,
    onChange: (String) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    Card(modifier = Modifier.fillMaxWidth()) {
        TextButton(onClick = { expanded = true }, enabled = enabled, modifier = Modifier.fillMaxWidth()) {
            Text("$label: $value")
        }
        DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            options.forEach { option ->
                DropdownMenuItem(
                    text = { Text(option) },
                    onClick = { onChange(option); expanded = false },
                )
            }
        }
    }
}

@Composable
private fun WorkingCard(stage: String) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp), modifier = Modifier.padding(16.dp)) {
            CircularProgressIndicator()
            Column {
                Text("Edecán está trabajando…", style = MaterialTheme.typography.titleMedium)
                Text(stage, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

@Composable
private fun ErrorCard(message: String) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Text(message, color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(14.dp))
    }
}

@Composable
private fun NoticeCard(message: String) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Text(message, color = MaterialTheme.colorScheme.tertiary, modifier = Modifier.padding(14.dp))
    }
}

private fun modeLabel(mode: String): String =
    StudioProjectMode.entries.firstOrNull { it.name.equals(mode, ignoreCase = true) }?.label ?: "Proyecto"
