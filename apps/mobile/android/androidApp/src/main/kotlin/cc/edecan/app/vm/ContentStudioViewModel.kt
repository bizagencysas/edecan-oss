package cc.edecan.app.vm

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import cc.edecan.shared.EdecanApi
import cc.edecan.shared.SocialContentDraft
import cc.edecan.shared.SocialContentRequest
import cc.edecan.shared.createSocialContent
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class ContentStudioUiState(
    val creating: Boolean = false,
    val stage: String = "",
    val errorMessage: String? = null,
    val noticeMessage: String? = null,
    val draft: SocialContentDraft? = null,
    val editedParts: List<String> = emptyList(),
    val imageBytes: ByteArray? = null,
) {
    override fun equals(other: Any?): Boolean = other is ContentStudioUiState &&
        creating == other.creating && stage == other.stage && errorMessage == other.errorMessage &&
        noticeMessage == other.noticeMessage && draft == other.draft && editedParts == other.editedParts &&
        imageBytes.contentEqualsNullable(other.imageBytes)

    override fun hashCode(): Int {
        var result = creating.hashCode()
        result = 31 * result + stage.hashCode()
        result = 31 * result + (errorMessage?.hashCode() ?: 0)
        result = 31 * result + (noticeMessage?.hashCode() ?: 0)
        result = 31 * result + (draft?.hashCode() ?: 0)
        result = 31 * result + editedParts.hashCode()
        return 31 * result + (imageBytes?.contentHashCode() ?: 0)
    }
}

private fun ByteArray?.contentEqualsNullable(other: ByteArray?): Boolean = when {
    this === other -> true
    this == null || other == null -> false
    else -> contentEquals(other)
}

class ContentStudioViewModel : ViewModel() {
    private val _uiState = MutableStateFlow(ContentStudioUiState())
    val uiState: StateFlow<ContentStudioUiState> = _uiState.asStateFlow()
    private var creationJob: Job? = null

    fun create(api: EdecanApi, request: SocialContentRequest) {
        creationJob?.cancel()
        creationJob = viewModelScope.launch {
            _uiState.value = ContentStudioUiState(
                creating = true,
                stage = if (request.withImage) {
                    "Preparando el texto y la imagen"
                } else {
                    "Preparando el texto"
                },
            )
            try {
                val draft = api.createSocialContent(request)
                _uiState.update {
                    it.copy(
                        creating = false,
                        draft = draft,
                        editedParts = draft.parts,
                        stage = if (draft.imageArtifact != null) "Cargando la imagen" else "",
                    )
                }
                draft.imageArtifact?.let { artifact ->
                    try {
                        val image = api.downloadArtifact(artifact)
                        _uiState.update { it.copy(imageBytes = image.bytes, stage = "") }
                    } catch (error: CancellationException) {
                        throw error
                    } catch (_: Throwable) {
                        _uiState.update {
                            it.copy(
                                stage = "",
                                noticeMessage = "El texto está listo, pero la imagen no se pudo cargar. Puedes compartir el texto ahora.",
                            )
                        }
                    }
                }
            } catch (_: CancellationException) {
                // Una creación nueva o el cierre de la pantalla reemplaza el trabajo anterior.
            } catch (error: Throwable) {
                _uiState.update {
                    it.copy(creating = false, stage = "", errorMessage = error.message ?: "No se pudo crear el borrador.")
                }
            }
        }
    }

    fun updatePart(index: Int, text: String) {
        _uiState.update { state ->
            if (index !in state.editedParts.indices) return@update state
            state.copy(editedParts = state.editedParts.toMutableList().apply { this[index] = text })
        }
    }

    fun setNotice(message: String?) {
        _uiState.update { it.copy(noticeMessage = message) }
    }

    fun reset() {
        creationJob?.cancel()
        creationJob = null
        _uiState.value = ContentStudioUiState()
    }
}
