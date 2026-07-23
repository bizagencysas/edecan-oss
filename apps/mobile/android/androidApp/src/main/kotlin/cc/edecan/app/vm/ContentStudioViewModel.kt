package cc.edecan.app.vm

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import cc.edecan.shared.ApiException
import cc.edecan.shared.ArtifactRef
import cc.edecan.shared.EdecanApi
import cc.edecan.shared.StudioActionRequest
import cc.edecan.shared.StudioExportFormat
import cc.edecan.shared.StudioProjectSummary
import cc.edecan.shared.StudioRevision
import cc.edecan.shared.UploadedFile
import cc.edecan.shared.studioAction
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class ContentStudioUiState(
    val projects: List<StudioProjectSummary> = emptyList(),
    val project: StudioProjectSummary? = null,
    val revisions: List<StudioRevision> = emptyList(),
    val selectedRevisionId: String? = null,
    val artifacts: List<ArtifactRef> = emptyList(),
    val previewArtifact: ArtifactRef? = null,
    val references: List<UploadedFile> = emptyList(),
    val working: Boolean = false,
    val uploadingReference: Boolean = false,
    val stage: String = "",
    val errorMessage: String? = null,
    val noticeMessage: String? = null,
)

class ContentStudioViewModel : ViewModel() {
    private val _uiState = MutableStateFlow(ContentStudioUiState())
    val uiState: StateFlow<ContentStudioUiState> = _uiState.asStateFlow()
    private var operationJob: Job? = null
    private var referenceJob: Job? = null

    fun loadProjects(api: EdecanApi) = runOperation("Buscando tus proyectos") {
        val response = api.studioAction(StudioActionRequest(action = "list"))
        _uiState.update { it.copy(projects = response.projects) }
    }

    fun create(api: EdecanApi, request: StudioActionRequest) =
        runOperation("Creando propuestas y preparando la vista previa") {
            val response = api.studioAction(request)
            val created = response.project ?: throw ApiException.RespuestaInvalida()
            val history = api.studioAction(
                StudioActionRequest(action = "history", projectId = created.id),
            )
            _uiState.update {
                it.copy(
                    project = history.project ?: created,
                    revisions = history.revisions,
                    selectedRevisionId = response.revisionId
                        ?: history.revisions.lastOrNull { revision -> revision.archivedAt == null }?.id,
                    artifacts = response.artifacts,
                    noticeMessage = response.message,
                )
            }
        }

    fun openProject(api: EdecanApi, project: StudioProjectSummary) =
        runOperation("Abriendo el proyecto") {
            val history = api.studioAction(
                StudioActionRequest(action = "history", projectId = project.id),
            )
            _uiState.update {
                it.copy(
                    project = history.project ?: project,
                    revisions = history.revisions,
                    selectedRevisionId = history.revisions
                        .lastOrNull { revision -> revision.archivedAt == null }?.id,
                    artifacts = emptyList(),
                )
            }
        }

    fun edit(api: EdecanApi, instruction: String) {
        val state = _uiState.value
        val project = state.project ?: return
        runOperation("Aplicando el cambio sin perder la versión anterior") {
            val response = api.studioAction(
                StudioActionRequest(
                    action = "edit",
                    projectId = project.id,
                    revisionId = state.selectedRevisionId,
                    instruction = instruction.trim(),
                ),
            )
            val history = api.studioAction(
                StudioActionRequest(action = "history", projectId = project.id),
            )
            _uiState.update {
                it.copy(
                    project = history.project ?: project,
                    revisions = history.revisions,
                    selectedRevisionId = response.revisionId
                        ?: history.revisions.lastOrNull { revision -> revision.archivedAt == null }?.id,
                    artifacts = mergeArtifacts(it.artifacts, response.artifacts),
                    noticeMessage = response.message,
                )
            }
        }
    }

    fun selectRevision(revisionId: String) {
        _uiState.update { it.copy(selectedRevisionId = revisionId) }
    }

    fun openFormat(api: EdecanApi, format: StudioExportFormat) {
        val state = _uiState.value
        val project = state.project ?: return
        runOperation("Preparando ${format.name} de forma privada") {
            val action = when (format) {
                StudioExportFormat.HTML -> "read"
                StudioExportFormat.PNG -> "render"
                StudioExportFormat.PDF -> "export"
            }
            val response = api.studioAction(
                StudioActionRequest(
                    action = action,
                    projectId = project.id,
                    revisionId = state.selectedRevisionId,
                    exportFormat = format.takeIf { it == StudioExportFormat.PDF },
                ),
            )
            val artifact = response.artifacts.firstOrNull { it.matches(format) }
                ?: response.artifacts.firstOrNull()
                ?: throw ApiException.RespuestaInvalida()
            _uiState.update {
                it.copy(
                    artifacts = mergeArtifacts(it.artifacts, response.artifacts),
                    previewArtifact = artifact,
                )
            }
        }
    }

    internal fun uploadReference(api: EdecanApi, local: ArchivoSubidaLocal) {
        referenceJob?.cancel()
        referenceJob = viewModelScope.launch {
            _uiState.update { it.copy(uploadingReference = true, errorMessage = null) }
            try {
                if (_uiState.value.references.size >= 12) return@launch
                val uploaded = api.uploadFile(local.contenido(), local.filename, local.mime)
                _uiState.update { state ->
                    state.copy(references = (state.references + uploaded).take(12))
                }
            } catch (_: CancellationException) {
                return@launch
            } catch (error: Throwable) {
                _uiState.update {
                    it.copy(errorMessage = error.message ?: "No se pudo subir la referencia.")
                }
            } finally {
                local.eliminar()
                _uiState.update { it.copy(uploadingReference = false) }
            }
        }
    }

    fun removeReference(id: String) {
        _uiState.update { it.copy(references = it.references.filterNot { file -> file.id == id }) }
    }

    fun clearPreview() {
        _uiState.update { it.copy(previewArtifact = null) }
    }

    fun closeProject() {
        operationJob?.cancel()
        _uiState.update {
            it.copy(
                project = null,
                revisions = emptyList(),
                selectedRevisionId = null,
                artifacts = emptyList(),
                previewArtifact = null,
                noticeMessage = null,
                errorMessage = null,
            )
        }
    }

    fun setNotice(message: String?) {
        _uiState.update { it.copy(noticeMessage = message) }
    }

    private fun runOperation(stage: String, operation: suspend () -> Unit) {
        operationJob?.cancel()
        operationJob = viewModelScope.launch {
            _uiState.update {
                it.copy(working = true, stage = stage, errorMessage = null, noticeMessage = null)
            }
            try {
                operation()
            } catch (_: CancellationException) {
                return@launch
            } catch (error: Throwable) {
                _uiState.update {
                    it.copy(errorMessage = error.message ?: "Studio no pudo completar esa operación.")
                }
            } finally {
                _uiState.update { it.copy(working = false, stage = "") }
            }
        }
    }
}

private fun mergeArtifacts(current: List<ArtifactRef>, incoming: List<ArtifactRef>): List<ArtifactRef> =
    (current + incoming).distinctBy(ArtifactRef::fileId)

private fun ArtifactRef.matches(format: StudioExportFormat): Boolean {
    val name = filename.lowercase()
    return when (format) {
        StudioExportFormat.HTML -> name.endsWith(".html") || mime == "text/html"
        StudioExportFormat.PNG -> name.endsWith(".png") || mime == "image/png"
        StudioExportFormat.PDF -> name.endsWith(".pdf") || mime == "application/pdf"
    }
}
