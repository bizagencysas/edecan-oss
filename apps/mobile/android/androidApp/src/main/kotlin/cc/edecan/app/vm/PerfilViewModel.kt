package cc.edecan.app.vm

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import cc.edecan.shared.ApiException
import cc.edecan.shared.CredentialsOut
import cc.edecan.shared.EdecanApi
import cc.edecan.shared.LiveProfile
import cc.edecan.shared.ProfileIdentity
import cc.edecan.shared.SetupStatusOut
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class PerfilUiState(
    val perfilVivo: LiveProfile? = null,
    val cargandoPerfil: Boolean = false,
    val guardandoPerfil: Boolean = false,
    val perfilGuardado: Boolean = false,
    val errorPerfil: String? = null,
    val cargando: Boolean = false,
    val credenciales: CredentialsOut? = null,
    val setupStatus: SetupStatusOut? = null,
    val errorCarga: String? = null,
)

/**
 * Perfil e indicadores de salud. La inferencia es administrada por Edecán:
 * la app no elige proveedor, modelo ni credenciales de LLM.
 */
class PerfilViewModel : ViewModel() {
    private val _uiState = MutableStateFlow(PerfilUiState())
    val uiState: StateFlow<PerfilUiState> = _uiState.asStateFlow()

    private var yaCargado = false

    fun cargarPerfil(api: EdecanApi, forzar: Boolean = false) {
        if (_uiState.value.perfilVivo != null && !forzar) return
        viewModelScope.launch {
            _uiState.update { it.copy(cargandoPerfil = true, errorPerfil = null) }
            try {
                val perfil = api.liveProfile()
                _uiState.update { it.copy(cargandoPerfil = false, perfilVivo = perfil) }
            } catch (e: ApiException) {
                _uiState.update { it.copy(cargandoPerfil = false, errorPerfil = e.message) }
            }
        }
    }

    fun guardarPerfil(
        api: EdecanApi,
        identidad: ProfileIdentity,
        resumen: String,
        onSaved: () -> Unit = {},
    ) {
        if (_uiState.value.guardandoPerfil) return
        viewModelScope.launch {
            _uiState.update {
                it.copy(guardandoPerfil = true, perfilGuardado = false, errorPerfil = null)
            }
            try {
                val actualizado = api.updateLiveProfile(identidad, resumen.trim())
                _uiState.update {
                    it.copy(
                        guardandoPerfil = false,
                        perfilGuardado = true,
                        perfilVivo = actualizado,
                    )
                }
                onSaved()
            } catch (e: ApiException) {
                _uiState.update { it.copy(guardandoPerfil = false, errorPerfil = e.message) }
            }
        }
    }

    fun cargar(api: EdecanApi, forzar: Boolean = false) {
        if (yaCargado && !forzar) return
        yaCargado = true
        viewModelScope.launch {
            _uiState.update { it.copy(cargando = true, errorCarga = null) }
            try {
                val credenciales = api.credentials()
                val configuracionSetup = try {
                    api.setupStatus()
                } catch (e: ApiException) {
                    null
                }
                _uiState.update {
                    it.copy(
                        cargando = false,
                        credenciales = credenciales,
                        setupStatus = configuracionSetup,
                    )
                }
            } catch (e: ApiException) {
                _uiState.update { it.copy(cargando = false, errorCarga = e.message) }
            }
        }
    }
}
