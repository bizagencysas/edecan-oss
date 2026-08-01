package cc.edecan.app.vm

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import cc.edecan.shared.ApiException
import cc.edecan.shared.Automation
import cc.edecan.shared.AutomationRun
import cc.edecan.shared.EdecanApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class AutomatizacionesUiState(
    val cargando: Boolean = false,
    val automatizaciones: List<Automation> = emptyList(),
    val errorLista: String? = null,
    val creando: Boolean = false,
    val errorCrear: String? = null,
    /** ids con un `PATCH enabled` en vuelo — el Switch de esa fila queda
     * deshabilitado mientras tanto (`AutomatizacionesScreen`). */
    val idsEnCambio: Set<String> = emptySet(),
    val seleccionId: String? = null,
    val corridas: List<AutomationRun> = emptyList(),
    val cargandoCorridas: Boolean = false,
    val errorCorridas: String? = null,
)

/**
 * Estado y lógica de la pestaña "Automatizaciones" (`/v1/automations`,
 * `ROADMAP_V2.md` §7.4/§7.6/§7.10, WP-V5-07): lista con Switch optimista
 * (revierte a mano si el `PATCH` falla), alta simple (siempre agenda +
 * instrucción, ver `EdecanApi.createAutomation`) y detalle de corridas
 * (`GET /v1/automations/{id}/runs`).
 */
class AutomatizacionesViewModel : ViewModel() {
    private val _uiState = MutableStateFlow(AutomatizacionesUiState())
    val uiState: StateFlow<AutomatizacionesUiState> = _uiState.asStateFlow()

    private var yaCargado = false

    fun cargar(api: EdecanApi, forzar: Boolean = false) {
        if (yaCargado && !forzar) return
        yaCargado = true
        viewModelScope.launch {
            _uiState.update { it.copy(cargando = true, errorLista = null) }
            try {
                val automatizaciones = api.listAutomations()
                _uiState.update { it.copy(cargando = false, automatizaciones = automatizaciones) }
            } catch (e: ApiException) {
                _uiState.update { it.copy(cargando = false, errorLista = e.message) }
            }
        }
    }

    /** Alta simple: siempre `kind = "schedule"` (ver `EdecanApi.createAutomation`
     * y "AutomatizacionesScreen" para los presets de `rrule`). */
    fun crear(api: EdecanApi, nombre: String, rrule: String, instruccion: String) {
        val nombreLimpio = nombre.trim()
        val instruccionLimpia = instruccion.trim()
        if (nombreLimpio.isEmpty() || instruccionLimpia.isEmpty() || _uiState.value.creando) return
        viewModelScope.launch {
            _uiState.update { it.copy(creando = true, errorCrear = null) }
            try {
                val creada = api.createAutomation(nombre = nombreLimpio, rrule = rrule, instruccion = instruccionLimpia)
                _uiState.update {
                    it.copy(creando = false, automatizaciones = listOf(creada) + it.automatizaciones)
                }
            } catch (e: ApiException) {
                _uiState.update { it.copy(creando = false, errorCrear = e.message) }
            }
        }
    }

    /** Switch optimista: aplica `habilitado` en el estado local YA, dispara
     * el `PATCH` de verdad, y si falla revierte la fila a su valor anterior
     * (nunca deja el Switch "mintiendo" sobre lo que el servidor guardó). */
    fun alternar(api: EdecanApi, automation: Automation, habilitado: Boolean) {
        val id = automation.id
        if (id in _uiState.value.idsEnCambio) return
        _uiState.update { estado ->
            estado.copy(
                automatizaciones = estado.automatizaciones.map { a -> if (a.id == id) a.copy(enabled = habilitado) else a },
                idsEnCambio = estado.idsEnCambio + id,
                errorLista = null,
            )
        }
        viewModelScope.launch {
            try {
                val actualizada = api.toggleAutomation(id, habilitado)
                _uiState.update { estado ->
                    estado.copy(
                        automatizaciones = estado.automatizaciones.map { a -> if (a.id == id) actualizada else a },
                        idsEnCambio = estado.idsEnCambio - id,
                    )
                }
            } catch (e: ApiException) {
                _uiState.update { estado ->
                    estado.copy(
                        automatizaciones = estado.automatizaciones.map { a -> if (a.id == id) a.copy(enabled = !habilitado) else a },
                        idsEnCambio = estado.idsEnCambio - id,
                        errorLista = e.message,
                    )
                }
            }
        }
    }

    fun seleccionar(api: EdecanApi, automationId: String) {
        _uiState.update { it.copy(seleccionId = automationId, corridas = emptyList(), errorCorridas = null) }
        viewModelScope.launch {
            _uiState.update { it.copy(cargandoCorridas = true) }
            try {
                val corridas = api.listAutomationRuns(automationId)
                _uiState.update { it.copy(cargandoCorridas = false, corridas = corridas) }
            } catch (e: ApiException) {
                _uiState.update { it.copy(cargandoCorridas = false, errorCorridas = e.message) }
            }
        }
    }

    fun cerrarDetalle() {
        _uiState.update { it.copy(seleccionId = null, corridas = emptyList(), errorCorridas = null) }
    }
}
