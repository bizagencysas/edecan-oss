@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package cc.edecan.app.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.graphics.BitmapFactory
import androidx.compose.foundation.Image
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
import androidx.compose.material3.Checkbox
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
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.FileProvider
import androidx.lifecycle.viewmodel.compose.viewModel
import cc.edecan.app.vm.ContentStudioViewModel
import cc.edecan.app.vm.SessionViewModel
import cc.edecan.shared.ArtifactRef
import cc.edecan.shared.SocialContentPlatform
import cc.edecan.shared.SocialContentRequest
import java.io.File
import cc.edecan.app.notifications.EdecanNotifications
import cc.edecan.app.notifications.NotificationRoute

/** Mini estudio móvil: crea, muestra, deja editar y entrega manualmente un
 * post real. No publica ni obliga a volver al chat para encontrar el resultado. */
@Composable
fun ContentStudioScreen(
    onVolver: () -> Unit = {},
    sessionViewModel: SessionViewModel = viewModel(),
    contentViewModel: ContentStudioViewModel = viewModel(),
) {
    val uiState by contentViewModel.uiState.collectAsState()
    val context = LocalContext.current
    var platformName by rememberSaveable { mutableStateOf(SocialContentPlatform.LINKEDIN.name) }
    var objective by rememberSaveable { mutableStateOf("Enseñar algo útil") }
    var tone by rememberSaveable { mutableStateOf("Claro y humano") }
    var topic by rememberSaveable { mutableStateOf("") }
    var withImage by rememberSaveable { mutableStateOf(true) }
    val platform = SocialContentPlatform.valueOf(platformName)

    LaunchedEffect(uiState.draft?.copy) {
        val draft = uiState.draft ?: return@LaunchedEffect
        EdecanNotifications.show(
            context = context,
            title = "Contenido listo",
            body = "Tu borrador ya está listo para revisar.",
            channel = EdecanNotifications.CONTENT,
            route = NotificationRoute.CREATE,
            stableId = draft.copy.hashCode(),
        )
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Crear") },
                navigationIcon = { IconButton(onClick = onVolver) { Text("←") } },
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
            if (uiState.draft == null) {
                Text("De una idea a una publicación completa", style = MaterialTheme.typography.headlineSmall)
                Text(
                    "Edecan prepara el texto y, si quieres, una imagen. Tú revisas y compartes.",
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                OutlinedTextField(
                    value = topic,
                    onValueChange = { topic = it },
                    label = { Text("¿Sobre qué quieres publicar?") },
                    minLines = 3,
                    enabled = !uiState.creating,
                    modifier = Modifier.fillMaxWidth(),
                )
                SimpleMenu(
                    "Plataforma",
                    platform.label,
                    SocialContentPlatform.entries.map { it.label },
                    enabled = !uiState.creating,
                ) { label ->
                    platformName = SocialContentPlatform.entries.first { it.label == label }.name
                }
                SimpleMenu(
                    "Objetivo",
                    objective,
                    listOf("Enseñar algo útil", "Contar una historia", "Lanzar un producto", "Generar conversación"),
                    enabled = !uiState.creating,
                ) { objective = it }
                SimpleMenu(
                    "Tono",
                    tone,
                    listOf("Claro y humano", "Profesional", "Directo", "Inspirador", "Divertido"),
                    enabled = !uiState.creating,
                ) { tone = it }
                Card(modifier = Modifier.fillMaxWidth()) {
                    Row(modifier = Modifier.padding(12.dp)) {
                        Checkbox(
                            checked = withImage,
                            onCheckedChange = { withImage = it },
                            enabled = !uiState.creating,
                        )
                        Text("Crear también una imagen original", modifier = Modifier.padding(top = 12.dp))
                    }
                }
                if (uiState.creating) {
                    Card(modifier = Modifier.fillMaxWidth()) {
                        Row(
                            horizontalArrangement = Arrangement.spacedBy(12.dp),
                            modifier = Modifier.padding(16.dp),
                        ) {
                            CircularProgressIndicator()
                            Column {
                                Text("Creando tu borrador…", style = MaterialTheme.typography.titleMedium)
                                Text(uiState.stage, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                        }
                    }
                }
                uiState.errorMessage?.let { ErrorCard(it) }
                Button(
                    enabled = topic.isNotBlank() && !uiState.creating && sessionViewModel.api != null,
                    onClick = {
                        sessionViewModel.api?.let { api ->
                            contentViewModel.create(
                                api,
                                SocialContentRequest(
                                    platform = platform,
                                    topic = topic.trim(),
                                    objective = objective,
                                    tone = tone,
                                    withImage = withImage,
                                ),
                            )
                        }
                    },
                    modifier = Modifier.fillMaxWidth(),
                ) { Text(if (uiState.creating) "Creando…" else "Crear borrador") }

                Text("Ideas rápidas", style = MaterialTheme.typography.titleMedium)
                listOf(
                    "Explica una idea difícil de forma sencilla",
                    "Convierte una experiencia en una lección útil",
                    "Presenta un producto sin sonar a publicidad",
                ).forEach { idea ->
                    OutlinedButton(onClick = { topic = idea }, enabled = !uiState.creating) { Text(idea) }
                }
            } else {
                Text("✓ Borrador listo", style = MaterialTheme.typography.headlineSmall, color = MaterialTheme.colorScheme.primary)
                Text(
                    "Revísalo, ajusta lo que quieras y compártelo cuando esté a tu gusto.",
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                if (uiState.stage.isNotBlank() && uiState.imageBytes == null) {
                    Card(modifier = Modifier.fillMaxWidth()) {
                        Row(
                            horizontalArrangement = Arrangement.spacedBy(12.dp),
                            modifier = Modifier.padding(16.dp),
                        ) {
                            CircularProgressIndicator()
                            Text(uiState.stage, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
                uiState.editedParts.forEachIndexed { index, part ->
                    Card(modifier = Modifier.fillMaxWidth()) {
                        Column(verticalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(14.dp)) {
                            Text(
                                if (uiState.editedParts.size > 1) {
                                    "Parte ${index + 1} de ${uiState.editedParts.size}"
                                } else {
                                    "Texto para ${platform.label}"
                                },
                                style = MaterialTheme.typography.titleMedium,
                            )
                            OutlinedTextField(
                                value = part,
                                onValueChange = { contentViewModel.updatePart(index, it) },
                                minLines = if (uiState.editedParts.size > 1) 4 else 8,
                                modifier = Modifier.fillMaxWidth(),
                            )
                            Text(
                                "${part.length}/${platform.characterLimit} caracteres",
                                color = if (part.length <= platform.characterLimit) {
                                    MaterialTheme.colorScheme.onSurfaceVariant
                                } else {
                                    MaterialTheme.colorScheme.error
                                },
                            )
                        }
                    }
                }

                val imageBytes = uiState.imageBytes
                if (imageBytes != null) {
                    val bitmap = remember(imageBytes) {
                        BitmapFactory.decodeByteArray(imageBytes, 0, imageBytes.size)?.asImageBitmap()
                    }
                    if (bitmap != null) {
                        Card(modifier = Modifier.fillMaxWidth()) {
                            Column(verticalArrangement = Arrangement.spacedBy(10.dp), modifier = Modifier.padding(14.dp)) {
                                Text("Imagen", style = MaterialTheme.typography.titleMedium)
                                Image(
                                    bitmap = bitmap,
                                    contentDescription = uiState.draft?.altText?.ifBlank { "Imagen creada para la publicación" },
                                    contentScale = ContentScale.FillWidth,
                                    modifier = Modifier.fillMaxWidth(),
                                )
                                uiState.draft?.altText?.takeIf { it.isNotBlank() }?.let {
                                    Text("Texto alternativo: $it", style = MaterialTheme.typography.bodySmall)
                                }
                            }
                        }
                    }
                }
                uiState.noticeMessage?.let { NoticeCard(it) }

                val valid = uiState.editedParts.isNotEmpty() && uiState.editedParts.all {
                    it.isNotBlank() && it.length <= platform.characterLimit
                }
                val shareText = uiState.editedParts.joinToString("\n\n") { it.trim() }
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp), modifier = Modifier.fillMaxWidth()) {
                    OutlinedButton(
                        enabled = valid,
                        onClick = {
                            val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                            clipboard.setPrimaryClip(ClipData.newPlainText("Publicación", shareText))
                            contentViewModel.setNotice("Texto copiado.")
                        },
                        modifier = Modifier.weight(1f),
                    ) { Text("Copiar") }
                    Button(
                        enabled = valid,
                        onClick = {
                            shareContent(
                                context,
                                shareText,
                                uiState.draft?.imageArtifact,
                                uiState.imageBytes,
                            )
                        },
                        modifier = Modifier.weight(1f),
                    ) { Text("Compartir") }
                }
                Text(
                    "Nada se publica automáticamente.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                TextButton(
                    onClick = {
                        contentViewModel.reset()
                        topic = ""
                    },
                    modifier = Modifier.fillMaxWidth(),
                ) { Text("Crear otra publicación") }
            }
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

private fun shareContent(
    context: Context,
    text: String,
    artifact: ArtifactRef?,
    imageBytes: ByteArray?,
) {
    val imageUri = if (artifact != null && imageBytes != null) {
        val safeId = artifact.fileId.filter { it.isLetterOrDigit() || it == '-' || it == '_' }
            .take(80).ifBlank { "content" }
        val directory = File(context.cacheDir, "shared_artifacts/$safeId").apply { mkdirs() }
        val safeName = artifact.filename.replace('\\', '/').substringAfterLast('/')
            .filterNot { it.isISOControl() || it == ':' }.take(180).ifBlank { "post.png" }
        val file = File(directory, safeName).apply { outputStream().use { it.write(imageBytes) } }
        FileProvider.getUriForFile(context, "${context.packageName}.files", file)
    } else {
        null
    }
    val intent = Intent(Intent.ACTION_SEND).apply {
        type = if (imageUri != null) artifact?.mime ?: "image/*" else "text/plain"
        putExtra(Intent.EXTRA_TEXT, text)
        imageUri?.let { uri ->
            putExtra(Intent.EXTRA_STREAM, uri)
            clipData = ClipData.newRawUri(artifact?.filename ?: "Imagen", uri)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
    }
    context.startActivity(Intent.createChooser(intent, "Compartir publicación"))
}
