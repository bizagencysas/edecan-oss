package cc.edecan.app.vm

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import cc.edecan.shared.ApiException
import cc.edecan.shared.EdecanApi
import cc.edecan.shared.PhoneCall
import cc.edecan.shared.isTerminal
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class LlamadasUiState(
    val cargando: Boolean = false,
    val llamadas: List<PhoneCall> = emptyList(),
    val errorMensaje: String? = null,
    val mensajeNoDisponible: String? = null,
)

internal fun LlamadasUiState.alIniciarCarga(): LlamadasUiState =
    copy(cargando = true, errorMensaje = null, mensajeNoDisponible = null)

internal fun LlamadasUiState.alCargar(llamadas: List<PhoneCall>): LlamadasUiState =
    copy(cargando = false, llamadas = llamadas, errorMensaje = null, mensajeNoDisponible = null)

internal fun LlamadasUiState.alFallar(error: ApiException): LlamadasUiState {
    val serverError = error as? ApiException.Servidor
    return if (serverError?.status == 403) {
        copy(
            cargando = false,
            llamadas = emptyList(),
            errorMensaje = null,
            mensajeNoDisponible = serverError.detalle,
        )
    } else {
        // Un fallo de actualización no borra el último historial visible.
        copy(cargando = false, errorMensaje = error.message, mensajeNoDisponible = null)
    }
}

/** Estado del historial de telefonía real (`GET /v1/phone/calls`). */
class LlamadasViewModel : ViewModel() {
    private val _uiState = MutableStateFlow(LlamadasUiState())
    val uiState: StateFlow<LlamadasUiState> = _uiState.asStateFlow()

    private var yaCargado = false
    private var pollingIniciado = false

    fun cargar(api: EdecanApi, forzar: Boolean = false) {
        if (_uiState.value.cargando || (yaCargado && !forzar)) return
        yaCargado = true
        viewModelScope.launch { refrescar(api, mostrarCargando = true) }
        iniciarPolling(api)
    }

    private fun iniciarPolling(api: EdecanApi) {
        if (pollingIniciado) return
        pollingIniciado = true
        viewModelScope.launch {
            while (true) {
                delay(POLL_INTERVAL_MS)
                if (_uiState.value.llamadas.any { !it.isTerminal }) {
                    refrescar(api, mostrarCargando = false)
                }
            }
        }
    }

    private suspend fun refrescar(api: EdecanApi, mostrarCargando: Boolean) {
        if (mostrarCargando) _uiState.update(LlamadasUiState::alIniciarCarga)
        try {
            _uiState.update { it.alCargar(api.phoneCalls()) }
        } catch (error: ApiException) {
            _uiState.update { it.alFallar(error) }
        }
    }

    private companion object {
        const val POLL_INTERVAL_MS = 3_000L
    }
}
