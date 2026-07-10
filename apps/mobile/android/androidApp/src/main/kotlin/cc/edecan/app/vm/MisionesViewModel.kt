package cc.edecan.app.vm

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import cc.edecan.shared.ACTIVE_MISSION_STATUSES
import cc.edecan.shared.ApiException
import cc.edecan.shared.EdecanApi
import cc.edecan.shared.Mission
import cc.edecan.shared.MissionDetail
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class MisionesUiState(
    val cargando: Boolean = false,
    val misiones: List<Mission> = emptyList(),
    val errorLista: String? = null,
    val creando: Boolean = false,
    val errorCrear: String? = null,
    val seleccionId: String? = null,
    val detalle: MissionDetail? = null,
    val cargandoDetalle: Boolean = false,
    val errorDetalle: String? = null,
    val accionOcupada: Boolean = false,
)

/**
 * Estado y lógica de la pestaña "Misiones" (`/v1/missions`,
 * `ARCHITECTURE.md` §11, `ROADMAP_V2.md` §7.4/§7.9, WP-V5-07). `missions.py`
 * NO expone SSE (a diferencia de `/v1/conversations/{id}/messages`) — la
 * planificación/ejecución real corre asíncrona en el worker
 * (`edecan_worker.handlers.run_mission`), así que esta pantalla refresca por
 * *polling* mientras haya algo activo, mismo criterio e intervalo que
 * `apps/web/src/app/(app)/app/misiones/page.tsx` (`POLL_INTERVAL_MS`).
 */
class MisionesViewModel : ViewModel() {
    private val _uiState = MutableStateFlow(MisionesUiState())
    val uiState: StateFlow<MisionesUiState> = _uiState.asStateFlow()

    private var yaCargado = false
    private var pollingIniciado = false

    /** Se llama desde `LaunchedEffect(api)` en `MisionesScreen` — carga la
     * lista una vez y arranca el *polling* en segundo plano (se cancela solo
     * cuando el `ViewModel` se limpia, `viewModelScope`). */
    fun cargar(api: EdecanApi) {
        if (!yaCargado) {
            yaCargado = true
            viewModelScope.launch { refrescarLista(api, mostrarCargando = true) }
        }
        iniciarPolling(api)
    }

    private fun iniciarPolling(api: EdecanApi) {
        if (pollingIniciado) return
        pollingIniciado = true
        viewModelScope.launch {
            while (true) {
                delay(POLL_INTERVAL_MS)
                if (_uiState.value.misiones.any { it.status in ACTIVE_MISSION_STATUSES }) {
                    refrescarLista(api, mostrarCargando = false)
                }
                val seleccionada = _uiState.value.detalle?.mission
                if (seleccionada != null && seleccionada.status in ACTIVE_MISSION_STATUSES) {
                    refrescarDetalle(api, seleccionada.id, mostrarCargando = false)
                }
            }
        }
    }

    private suspend fun refrescarLista(api: EdecanApi, mostrarCargando: Boolean) {
        if (mostrarCargando) _uiState.update { it.copy(cargando = true, errorLista = null) }
        try {
            val misiones = api.listMissions()
            _uiState.update { it.copy(cargando = false, misiones = misiones, errorLista = null) }
        } catch (e: ApiException) {
            _uiState.update { it.copy(cargando = false, errorLista = e.message) }
        }
    }

    /** `POST /v1/missions {objetivo}` — al terminar, refresca la lista y
     * selecciona la misión recién creada para que el usuario vea su avance
     * de una. */
    fun crear(api: EdecanApi, objetivo: String) {
        val limpio = objetivo.trim()
        if (limpio.isEmpty() || _uiState.value.creando) return
        viewModelScope.launch {
            _uiState.update { it.copy(creando = true, errorCrear = null) }
            try {
                val mision = api.createMission(limpio)
                _uiState.update { it.copy(creando = false) }
                refrescarLista(api, mostrarCargando = false)
                seleccionar(api, mision.id)
            } catch (e: ApiException) {
                _uiState.update { it.copy(creando = false, errorCrear = e.message) }
            }
        }
    }

    fun seleccionar(api: EdecanApi, missionId: String) {
        _uiState.update { it.copy(seleccionId = missionId, errorDetalle = null) }
        viewModelScope.launch { refrescarDetalle(api, missionId, mostrarCargando = true) }
    }

    fun cerrarDetalle() {
        _uiState.update { it.copy(seleccionId = null, detalle = null, errorDetalle = null) }
    }

    private suspend fun refrescarDetalle(api: EdecanApi, missionId: String, mostrarCargando: Boolean) {
        if (mostrarCargando) _uiState.update { it.copy(cargandoDetalle = true, errorDetalle = null) }
        try {
            val detalle = api.getMission(missionId)
            // La selección pudo cambiar mientras esta llamada estaba en vuelo
            // (p. ej. el usuario volvió a la lista) — no pisar un detalle
            // distinto con una respuesta vieja.
            if (_uiState.value.seleccionId == missionId) {
                _uiState.update { it.copy(cargandoDetalle = false, detalle = detalle, errorDetalle = null) }
            }
        } catch (e: ApiException) {
            if (_uiState.value.seleccionId == missionId) {
                _uiState.update { it.copy(cargandoDetalle = false, errorDetalle = e.message) }
            }
        }
    }

    /** `POST /v1/missions/{id}/confirm {approved}` — Aprobar/Rechazar de la
     * tarjeta de confirmación cuando la misión seleccionada está
     * `waiting_confirmation`. */
    fun confirmar(api: EdecanApi, aprobado: Boolean) {
        val missionId = _uiState.value.seleccionId ?: return
        if (_uiState.value.accionOcupada) return
        viewModelScope.launch {
            _uiState.update { it.copy(accionOcupada = true, errorDetalle = null) }
            try {
                api.confirmMission(missionId, aprobado)
                refrescarDetalle(api, missionId, mostrarCargando = false)
                refrescarLista(api, mostrarCargando = false)
            } catch (e: ApiException) {
                _uiState.update { it.copy(errorDetalle = e.message) }
            } finally {
                _uiState.update { it.copy(accionOcupada = false) }
            }
        }
    }

    private companion object {
        const val POLL_INTERVAL_MS = 2000L
    }
}
