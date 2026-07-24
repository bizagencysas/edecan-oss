@file:OptIn(ExperimentalMaterial3Api::class)

package cc.edecan.app.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AssistChip
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import cc.edecan.app.ui.components.EmptyState
import cc.edecan.app.vm.IdeEntrada
import cc.edecan.app.vm.IdeUiState
import cc.edecan.app.vm.IdeViewModel
import cc.edecan.app.vm.SessionViewModel
import cc.edecan.shared.EdecanApi
import cc.edecan.shared.IdeSession
import cc.edecan.shared.IdeSessionEvent

private enum class IdeTab(val label: String) {
    FILES("Archivos"),
    AGENT("Agente"),
    TERMINAL("Terminal"),
    GIT("Git"),
}

/**
 * Estudio de código móvil de Edecán.
 *
 * El teléfono controla proyectos autorizados en la app maestra. Agente y
 * terminal son sesiones durables del companion: minimizar Android no
 * cancela procesos, y al volver la vista rehidrata cada timeline por cursor.
 */
@Composable
fun IdeScreen(
    sessionViewModel: SessionViewModel = viewModel(),
    ideViewModel: IdeViewModel = viewModel(),
    onVolver: () -> Unit = {},
) {
    val state by ideViewModel.uiState.collectAsStateWithLifecycle()
    val api = sessionViewModel.api
    val lifecycleOwner = LocalLifecycleOwner.current
    var tab by remember { mutableStateOf(IdeTab.FILES) }
    var confirmarDescartar by remember { mutableStateOf(false) }
    val archivoModificado =
        state.archivoRuta != null &&
            !state.archivoEsBinario &&
            state.archivoContenido != state.archivoContenidoOriginal
    val volverDesdeEditor = {
        if (archivoModificado) confirmarDescartar = true
        else ideViewModel.cerrarArchivo()
    }

    LaunchedEffect(api) { api?.let { ideViewModel.cargar(it) } }
    DisposableEffect(lifecycleOwner, api) {
        val observer = LifecycleEventObserver { _, event ->
            when (event) {
                Lifecycle.Event.ON_RESUME -> api?.let { ideViewModel.cargar(it) }
                Lifecycle.Event.ON_STOP -> ideViewModel.pausar()
                else -> Unit
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose {
            lifecycleOwner.lifecycle.removeObserver(observer)
            ideViewModel.pausar()
        }
    }
    BackHandler(enabled = state.archivoRuta != null) { volverDesdeEditor() }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        state.archivoRuta
                            ?: state.workspaces.firstOrNull { it.id == state.workspaceId }?.name
                            ?: "Estudio",
                    )
                },
                navigationIcon = {
                    IconButton(
                        onClick = if (state.archivoRuta != null) {
                            volverDesdeEditor
                        } else {
                            onVolver
                        },
                    ) { Text("←") }
                },
            )
        },
    ) { padding ->
        Box(modifier = Modifier.padding(padding).fillMaxSize()) {
            when {
                state.cargando && state.conectado == null -> {
                    CircularProgressIndicator(modifier = Modifier.align(Alignment.Center))
                }
                state.conectado == false -> {
                    EmptyState(
                        emoji = "💻",
                        titulo = "Computadora no disponible",
                        descripcion = "Abre Edecán en tu computadora. Tus sesiones se recuperarán solas al volver.",
                        etiquetaRoadmap = null,
                    )
                }
                state.archivoRuta != null -> {
                    FileEditor(
                        state = state,
                        onChange = ideViewModel::cambiarContenido,
                        onSave = { api?.let(ideViewModel::guardarArchivo) },
                    )
                }
                state.workspaces.isEmpty() -> {
                    WorkspaceOnboarding(
                        state = state,
                        onPath = ideViewModel::cambiarNuevaRuta,
                        onAuthorize = { api?.let(ideViewModel::autorizarRuta) },
                    )
                }
                else -> {
                    Column(modifier = Modifier.fillMaxSize()) {
                        WorkspaceBar(
                            state = state,
                            onSelect = { id -> api?.let { ideViewModel.seleccionarWorkspace(id, it) } },
                            onPath = ideViewModel::cambiarNuevaRuta,
                            onAuthorize = { api?.let(ideViewModel::autorizarRuta) },
                        )
                        TabBar(selected = tab, onSelect = { tab = it })
                        when (tab) {
                            IdeTab.FILES -> FilesPane(
                                state = state,
                                onRoute = ideViewModel::cambiarRuta,
                                onOpenRoute = { api?.let(ideViewModel::abrirRuta) },
                                onOpenFile = { path ->
                                    api?.let { ideViewModel.abrirArchivo(path, it) }
                                },
                            )
                            IdeTab.AGENT -> AgentPane(
                                state = state,
                                onPrompt = ideViewModel::cambiarAgentePrompt,
                                onProvider = ideViewModel::cambiarAgenteProvider,
                                onStart = { api?.let(ideViewModel::iniciarAgente) },
                                onCancel = { api?.let(ideViewModel::cancelarAgente) },
                                onSelectSession = { id ->
                                    api?.let { ideViewModel.seleccionarAgente(id, it) }
                                },
                            )
                            IdeTab.TERMINAL -> TerminalPane(
                                state = state,
                                onInput = ideViewModel::cambiarTerminalEntrada,
                                onStart = { api?.let(ideViewModel::iniciarTerminal) },
                                onSend = { api?.let(ideViewModel::enviarTerminal) },
                                onClose = { api?.let(ideViewModel::cerrarTerminal) },
                                onSelectSession = { id ->
                                    api?.let { ideViewModel.seleccionarTerminal(id, it) }
                                },
                            )
                            IdeTab.GIT -> GitPane(
                                state = state,
                                api = api,
                                viewModel = ideViewModel,
                            )
                        }
                    }
                }
            }

            state.errorMensaje?.let { error ->
                Card(
                    colors = CardDefaults.cardColors(
                        containerColor = MaterialTheme.colorScheme.errorContainer,
                    ),
                    modifier = Modifier
                        .align(Alignment.BottomCenter)
                        .padding(12.dp),
                ) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        modifier = Modifier.padding(start = 12.dp),
                    ) {
                        Text(
                            error,
                            color = MaterialTheme.colorScheme.onErrorContainer,
                            style = MaterialTheme.typography.bodySmall,
                            modifier = Modifier.weight(1f).padding(vertical = 12.dp),
                        )
                        TextButton(onClick = ideViewModel::descartarError) {
                            Text("Cerrar")
                        }
                    }
                }
            }
        }
    }

    if (confirmarDescartar) {
        AlertDialog(
            onDismissRequest = { confirmarDescartar = false },
            title = { Text("Descartar cambios") },
            text = { Text("Este archivo tiene cambios sin guardar.") },
            confirmButton = {
                TextButton(
                    onClick = {
                        confirmarDescartar = false
                        ideViewModel.cerrarArchivo()
                    },
                ) { Text("Descartar") }
            },
            dismissButton = {
                TextButton(onClick = { confirmarDescartar = false }) {
                    Text("Seguir editando")
                }
            },
        )
    }
}

@Composable
private fun WorkspaceOnboarding(
    state: IdeUiState,
    onPath: (String) -> Unit,
    onAuthorize: () -> Unit,
) {
    Column(
        modifier = Modifier.fillMaxSize().padding(24.dp),
        verticalArrangement = Arrangement.Center,
    ) {
        Text("Abre un proyecto", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
        Text(
            "Escribe la ruta de una carpeta. Edecán pedirá autorización en tu computadora una sola vez.",
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(vertical = 10.dp),
        )
        OutlinedTextField(
            value = state.nuevaRuta,
            onValueChange = onPath,
            label = { Text("/ruta/de/tu/proyecto") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        Button(
            onClick = onAuthorize,
            enabled = state.nuevaRuta.isNotBlank() && !state.autorizandoRuta,
            modifier = Modifier.fillMaxWidth().padding(top = 12.dp),
        ) {
            if (state.autorizandoRuta) CircularProgressIndicator(modifier = Modifier.size(18.dp))
            else Text("Autorizar proyecto")
        }
    }
}

@Composable
private fun WorkspaceBar(
    state: IdeUiState,
    onSelect: (String) -> Unit,
    onPath: (String) -> Unit,
    onAuthorize: () -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    var adding by remember { mutableStateOf(false) }
    val current = state.workspaces.firstOrNull { it.id == state.workspaceId }
    Card(
        modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 6.dp),
        shape = RoundedCornerShape(18.dp),
    ) {
        Column(modifier = Modifier.padding(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(
                    modifier = Modifier
                        .weight(1f)
                        .clickable(
                            enabled = !state.cambiandoWorkspace && !state.autorizandoRuta,
                        ) {
                            expanded = true
                        },
                ) {
                    Text(current?.name ?: "Proyecto", fontWeight = FontWeight.SemiBold)
                    Text(
                        current?.path.orEmpty(),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                    )
                    DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
                        state.workspaces.forEach { workspace ->
                            DropdownMenuItem(
                                text = {
                                    Column {
                                        Text(workspace.name)
                                        Text(workspace.path, style = MaterialTheme.typography.labelSmall)
                                    }
                                },
                                onClick = {
                                    expanded = false
                                    onSelect(workspace.id)
                                },
                                enabled = !state.cambiandoWorkspace && !state.autorizandoRuta,
                            )
                        }
                    }
                }
                if (state.cambiandoWorkspace) {
                    CircularProgressIndicator(modifier = Modifier.size(20.dp))
                }
                TextButton(
                    onClick = { adding = !adding },
                    enabled = !state.cambiandoWorkspace && !state.autorizandoRuta,
                ) { Text(if (adding) "Cerrar" else "+ Proyecto") }
            }
            if (adding) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    OutlinedTextField(
                        value = state.nuevaRuta,
                        onValueChange = onPath,
                        label = { Text("Ruta absoluta") },
                        singleLine = true,
                        modifier = Modifier.weight(1f),
                    )
                    Button(
                        onClick = onAuthorize,
                        enabled = state.nuevaRuta.isNotBlank() &&
                            !state.autorizandoRuta &&
                            !state.cambiandoWorkspace,
                        modifier = Modifier.padding(start = 8.dp),
                    ) {
                        if (state.autorizandoRuta) {
                            CircularProgressIndicator(modifier = Modifier.size(18.dp))
                        } else {
                            Text("Abrir")
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun TabBar(selected: IdeTab, onSelect: (IdeTab) -> Unit) {
    Row(
        modifier = Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 8.dp),
    ) {
        IdeTab.entries.forEach { tab ->
            AssistChip(
                onClick = { onSelect(tab) },
                label = { Text(tab.label) },
                leadingIcon = { if (tab == selected) Text("●") },
                modifier = Modifier.padding(horizontal = 4.dp),
            )
        }
    }
}

@Composable
private fun FilesPane(
    state: IdeUiState,
    onRoute: (String) -> Unit,
    onOpenRoute: () -> Unit,
    onOpenFile: (String) -> Unit,
) {
    Column(modifier = Modifier.fillMaxSize()) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp),
        ) {
            OutlinedTextField(
                value = state.rutaActual,
                onValueChange = onRoute,
                label = { Text("Carpeta dentro del proyecto") },
                singleLine = true,
                modifier = Modifier.weight(1f),
            )
            Button(onClick = onOpenRoute, modifier = Modifier.padding(start = 8.dp)) { Text("Ir") }
        }
        if (state.cargando) {
            CircularProgressIndicator(modifier = Modifier.padding(24.dp))
        } else {
            FileTree(
                entries = state.entradas,
                truncated = state.truncado,
                onOpenFile = onOpenFile,
                onOpenDirectory = { path ->
                    onRoute(path)
                    onOpenRoute()
                },
            )
        }
    }
}

@Composable
private fun FileTree(
    entries: List<IdeEntrada>,
    truncated: Boolean,
    onOpenFile: (String) -> Unit,
    onOpenDirectory: (String) -> Unit,
) {
    if (entries.isEmpty()) {
        EmptyState(
            emoji = "📁",
            titulo = "Carpeta vacía",
            descripcion = "No hay archivos visibles en esta ruta.",
            etiquetaRoadmap = null,
        )
        return
    }
    LazyColumn(modifier = Modifier.fillMaxSize()) {
        if (truncated) {
            item {
                Text(
                    "Hay más archivos. Abre una subcarpeta para verlos.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(12.dp),
                )
            }
        }
        items(entries, key = { it.ruta }) { entry ->
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable {
                        if (entry.esDirectorio) onOpenDirectory(entry.ruta)
                        else onOpenFile(entry.ruta)
                    }
                    .padding(
                        start = 16.dp + (entry.profundidad * 16).dp,
                        top = 10.dp,
                        bottom = 10.dp,
                        end = 16.dp,
                    ),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(if (entry.esDirectorio) "📁" else "📄", modifier = Modifier.width(30.dp))
                Text(entry.nombre)
            }
        }
    }
}

@Composable
private fun AgentPane(
    state: IdeUiState,
    onPrompt: (String) -> Unit,
    onProvider: (String) -> Unit,
    onStart: () -> Unit,
    onCancel: () -> Unit,
    onSelectSession: (String) -> Unit,
) {
    Column(modifier = Modifier.fillMaxSize().padding(horizontal = 12.dp)) {
        if (state.agentes.isNotEmpty()) {
            SessionPicker(
                sessions = state.agentes,
                selectedId = state.agente?.id,
                label = "Trabajos",
                onSelect = onSelectSession,
            )
        }
        state.agente?.let { session ->
            SessionHeader(
                title = session.title.ifBlank { "Agente del proyecto" },
                status = session.status,
                active = session.activa,
                onStop = onCancel,
            )
        }
        EventTimeline(
            events = state.agenteEventos,
            emptyText = "Describe un cambio. Edecán trabajará en esta carpeta y mostrará cada paso aquí.",
            modifier = Modifier.weight(1f),
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf("auto" to "Auto", "codex" to "Codex", "claude" to "Claude").forEach { (value, label) ->
                AssistChip(
                    onClick = { onProvider(value) },
                    label = { Text(label) },
                    leadingIcon = { if (state.agenteProvider == value) Text("●") },
                )
            }
        }
        OutlinedTextField(
            value = state.agentePrompt,
            onValueChange = onPrompt,
            label = { Text("¿Qué debe construir o arreglar?") },
            minLines = 2,
            maxLines = 5,
            modifier = Modifier.fillMaxWidth(),
        )
        Button(
            onClick = onStart,
            enabled = state.agentePrompt.isNotBlank() && !state.iniciandoAgente,
            modifier = Modifier.fillMaxWidth().padding(vertical = 10.dp),
        ) {
            if (state.iniciandoAgente) CircularProgressIndicator(modifier = Modifier.size(18.dp))
            else Text("Iniciar trabajo")
        }
    }
}

@Composable
private fun TerminalPane(
    state: IdeUiState,
    onInput: (String) -> Unit,
    onStart: () -> Unit,
    onSend: () -> Unit,
    onClose: () -> Unit,
    onSelectSession: (String) -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(12.dp)
            .background(Color(0xFF0B0D13), RoundedCornerShape(18.dp)),
    ) {
        if (state.terminales.isNotEmpty()) {
            SessionPicker(
                sessions = state.terminales,
                selectedId = state.terminal?.id,
                label = "Terminales",
                onSelect = onSelectSession,
                dark = true,
            )
        }
        state.terminal?.let { session ->
            SessionHeader(
                title = session.title.ifBlank { "Terminal" },
                status = session.status,
                active = session.activa,
                onStop = onClose,
                dark = true,
            )
        }
        val output = state.terminalEventos.joinToString(separator = "") { event ->
            if (event.type == "output") event.text else "\n${event.text}\n"
        }
        val terminalScroll = rememberScrollState()
        LaunchedEffect(output, terminalScroll.maxValue) {
            terminalScroll.scrollTo(terminalScroll.maxValue)
        }
        Text(
            output.ifBlank { "Terminal persistente del proyecto.\n" },
            color = Color(0xFF65E6B1),
            fontFamily = FontFamily.Monospace,
            modifier = Modifier
                .weight(1f)
                .fillMaxWidth()
                .verticalScroll(terminalScroll)
                .padding(14.dp),
        )
        if (state.terminal == null || state.terminal.activa.not()) {
            Button(
                onClick = onStart,
                enabled = !state.iniciandoTerminal,
                modifier = Modifier.fillMaxWidth().padding(10.dp),
            ) { Text("Nueva terminal") }
        } else {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier.fillMaxWidth().padding(10.dp),
            ) {
                Text("$", color = Color(0xFF65E6B1))
                OutlinedTextField(
                    value = state.terminalEntrada,
                    onValueChange = onInput,
                    singleLine = true,
                    modifier = Modifier.weight(1f).padding(horizontal = 8.dp),
                )
                Button(
                    onClick = onSend,
                    enabled = state.terminalEntrada.isNotBlank(),
                ) { Text("↑") }
            }
        }
    }
}

@Composable
private fun GitPane(state: IdeUiState, api: EdecanApi?, viewModel: IdeViewModel) {
    var confirmarPush by remember { mutableStateOf(false) }
    if (state.gitCargando && state.gitStatus == null) {
        Box(modifier = Modifier.fillMaxSize()) {
            CircularProgressIndicator(modifier = Modifier.align(Alignment.Center))
        }
        return
    }
    val status = state.gitStatus
    if (status == null) {
        EmptyState(
            emoji = "🌿",
            titulo = "Git no está disponible",
            descripcion = "Este proyecto no parece ser un repositorio Git.",
            etiquetaRoadmap = null,
        )
        return
    }
    LazyColumn(modifier = Modifier.fillMaxSize().padding(horizontal = 12.dp)) {
        item {
            Card(modifier = Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
                Column(modifier = Modifier.padding(14.dp)) {
                    Text(
                        status.branch ?: "Sin rama",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold,
                    )
                    Text(
                        "${status.ahead} por subir · ${status.behind} por traer",
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    TextButton(onClick = { api?.let(viewModel::cargarGit) }) { Text("Actualizar") }
                }
            }
        }
        items(status.files, key = { "${it.originalPath}:${it.path}" }) { file ->
            Row(
                modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("${file.indexStatus}${file.worktreeStatus}", fontFamily = FontFamily.Monospace)
                Text(file.path, modifier = Modifier.weight(1f).padding(horizontal = 8.dp))
                TextButton(
                    onClick = {
                        api?.let {
                            if (file.staged) viewModel.unstage(file.path, it)
                            else viewModel.stage(file.path, it)
                        }
                    },
                    enabled = !state.gitCargando,
                ) { Text(if (file.staged) "Quitar" else "Preparar") }
            }
            HorizontalDivider()
        }
        item {
            OutlinedTextField(
                value = state.gitCommitMessage,
                onValueChange = viewModel::cambiarCommitMessage,
                label = { Text("Mensaje del commit") },
                modifier = Modifier.fillMaxWidth().padding(top = 12.dp),
            )
            Button(
                onClick = { api?.let(viewModel::commit) },
                enabled = state.gitCommitMessage.isNotBlank() && !state.gitCargando,
                modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
            ) { Text("Crear commit") }
            OutlinedTextField(
                value = state.gitBranchName,
                onValueChange = viewModel::cambiarBranchName,
                label = { Text("Nueva rama") },
                modifier = Modifier.fillMaxWidth().padding(top = 14.dp),
            )
            OutlinedButton(
                onClick = { api?.let(viewModel::crearRama) },
                enabled = state.gitBranchName.isNotBlank() && !state.gitCargando,
                modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
            ) { Text("Crear y cambiar") }
            OutlinedButton(
                onClick = { confirmarPush = true },
                enabled = !state.gitCargando,
                modifier = Modifier.fillMaxWidth().padding(vertical = 12.dp),
            ) { Text("Subir rama") }
            state.gitDiff?.text?.takeIf { it.isNotBlank() }?.let { diff ->
                Text("Cambios", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                Text(
                    diff,
                    fontFamily = FontFamily.Monospace,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier
                        .fillMaxWidth()
                        .horizontalScroll(rememberScrollState())
                        .padding(vertical = 8.dp),
                )
                if (state.gitDiff.truncated) {
                    Text(
                        "La vista de cambios fue recortada por tamaño.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            Text("Historial", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
        }
        items(state.gitLog?.commits.orEmpty(), key = { it.hash }) { commit ->
            Column(modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp)) {
                Text(commit.subject, fontWeight = FontWeight.SemiBold)
                Text(
                    "${commit.shortHash} · ${commit.author}",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        item { Spacer(modifier = Modifier.height(28.dp)) }
    }
    if (confirmarPush) {
        AlertDialog(
            onDismissRequest = { confirmarPush = false },
            title = { Text("Subir la rama") },
            text = {
                Text(
                    "Se enviará ${status.branch ?: "la rama actual"} al remoto origin. " +
                        "La computadora pedirá la aprobación final.",
                )
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        confirmarPush = false
                        api?.let(viewModel::push)
                    },
                ) { Text("Continuar") }
            },
            dismissButton = {
                TextButton(onClick = { confirmarPush = false }) { Text("Cancelar") }
            },
        )
    }
}

@Composable
private fun SessionPicker(
    sessions: List<IdeSession>,
    selectedId: String?,
    label: String,
    onSelect: (String) -> Unit,
    dark: Boolean = false,
) {
    var expanded by remember { mutableStateOf(false) }
    val selected = sessions.firstOrNull { it.id == selectedId }
    Box(modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp)) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .clickable { expanded = true }
                .padding(vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                "$label: ${selected?.title?.takeIf { it.isNotBlank() } ?: "Sin selección"}",
                color = if (dark) Color.White else MaterialTheme.colorScheme.onSurface,
                style = MaterialTheme.typography.labelMedium,
                modifier = Modifier.weight(1f),
            )
            Text(
                "⌄",
                color = if (dark) Color.White else MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            sessions.forEach { session ->
                DropdownMenuItem(
                    text = {
                        Column {
                            Text(session.title.ifBlank { "Sesión" })
                            Text(
                                session.status,
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    },
                    onClick = {
                        expanded = false
                        onSelect(session.id)
                    },
                )
            }
        }
    }
}

@Composable
private fun SessionHeader(
    title: String,
    status: String,
    active: Boolean,
    onStop: () -> Unit,
    dark: Boolean = false,
) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(
                title,
                fontWeight = FontWeight.SemiBold,
                color = if (dark) Color.White else MaterialTheme.colorScheme.onSurface,
            )
            Text(
                status,
                style = MaterialTheme.typography.bodySmall,
                color = if (active) Color(0xFF48C78E) else MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        if (active) TextButton(onClick = onStop) { Text("Detener") }
    }
}

@Composable
private fun EventTimeline(
    events: List<IdeSessionEvent>,
    emptyText: String,
    modifier: Modifier = Modifier,
) {
    if (events.isEmpty()) {
        Box(modifier = modifier.fillMaxWidth()) {
            Text(
                emptyText,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.align(Alignment.Center).padding(24.dp),
            )
        }
        return
    }
    val listState = rememberLazyListState()
    LaunchedEffect(events.lastOrNull()?.cursor) {
        if (events.isNotEmpty()) listState.animateScrollToItem(events.lastIndex)
    }
    LazyColumn(state = listState, modifier = modifier.fillMaxWidth()) {
        items(events, key = { it.cursor }) { event ->
            Row(modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp)) {
                Text("●", color = MaterialTheme.colorScheme.primary, modifier = Modifier.width(24.dp))
                Column {
                    Text(event.type.replace('_', ' '), style = MaterialTheme.typography.labelMedium)
                    if (event.text.isNotBlank()) {
                        Text(
                            event.text,
                            style = MaterialTheme.typography.bodySmall,
                            fontFamily = if (event.stream != null) FontFamily.Monospace else FontFamily.Default,
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun FileEditor(
    state: IdeUiState,
    onChange: (String) -> Unit,
    onSave: () -> Unit,
) {
    val content = state.archivoContenido
    if (state.cargandoArchivo) {
        Box(modifier = Modifier.fillMaxSize()) {
            CircularProgressIndicator(modifier = Modifier.align(Alignment.Center))
        }
        return
    }
    if (state.archivoEsBinario) {
        EmptyState(
            emoji = "📦",
            titulo = "Archivo no editable",
            descripcion = "Este archivo no es texto UTF-8. No se modificará desde el teléfono.",
            etiquetaRoadmap = null,
        )
        return
    }
    if (content == null) {
        EmptyState(
            emoji = "⚠️",
            titulo = "No se pudo abrir",
            descripcion = state.errorMensaje ?: "Vuelve a la lista e inténtalo de nuevo.",
            etiquetaRoadmap = null,
        )
        return
    }
    val modified = content != state.archivoContenidoOriginal
    Column(modifier = Modifier.fillMaxSize()) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(if (modified) "Cambios sin guardar" else "Guardado", modifier = Modifier.weight(1f))
            Button(onClick = onSave, enabled = modified && !state.guardandoArchivo) {
                if (state.guardandoArchivo) CircularProgressIndicator(modifier = Modifier.size(18.dp))
                else Text("Guardar")
            }
        }
        OutlinedTextField(
            value = content,
            onValueChange = onChange,
            textStyle = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace),
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .horizontalScroll(rememberScrollState())
                .padding(16.dp),
        )
    }
}
