package cc.edecan.app.vm

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import cc.edecan.shared.ApiException
import cc.edecan.shared.EdecanApi
import cc.edecan.shared.Reminder
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter

data class RecordatoriosUiState(
    val cargando: Boolean = false,
    val recordatorios: List<Reminder> = emptyList(),
    val errorLista: String? = null,
    val creando: Boolean = false,
    val errorCrear: String? = null,
    /** ids con un `PUT` (completar) en vuelo. */
    val idsOcupados: Set<String> = emptySet(),
)

/** `Reminder.status == "pending"`, ordenados por fecha (los más próximos
 * primero) — igual criterio de orden que `repo.list_reminders`
 * (`ORDER BY due_at ASC`). */
val RecordatoriosUiState.pendientes: List<Reminder>
    get() = recordatorios.filter { it.status == "pending" }.sortedBy { it.dueAt }

/** Cualquier estado que ya NO sea `"pending"` (`"sent"`/`"cancelled"`, o
 * cualquier otro que el backend agregue mañana) cuenta como "completado" en
 * esta pantalla — no hay un status `"done"` propio en el backend (ver KDoc
 * de [Reminder]). */
val RecordatoriosUiState.completados: List<Reminder>
    get() = recordatorios.filter { it.status != "pending" }.sortedByDescending { it.dueAt }

/**
 * Estado y lógica de la pestaña "Recordatorios" (`/v1/reminders`,
 * `ARCHITECTURE.md` §10.3/§10.12/§10.11, WP-V5-07): pendientes/completados,
 * alta con texto + fecha/hora (`ui/components/FechaHoraPickers.kt`), y
 * "completar" a mano antes de que `send_reminder_scan` lo alcance solo.
 */
class RecordatoriosViewModel : ViewModel() {
    private val _uiState = MutableStateFlow(RecordatoriosUiState())
    val uiState: StateFlow<RecordatoriosUiState> = _uiState.asStateFlow()

    private var yaCargado = false

    fun cargar(api: EdecanApi, forzar: Boolean = false) {
        if (yaCargado && !forzar) return
        yaCargado = true
        viewModelScope.launch {
            _uiState.update { it.copy(cargando = true, errorLista = null) }
            try {
                val recordatorios = api.listReminders()
                _uiState.update { it.copy(cargando = false, recordatorios = recordatorios) }
            } catch (e: ApiException) {
                _uiState.update { it.copy(cargando = false, errorLista = e.message) }
            }
        }
    }

    /** `POST /v1/reminders` — combina [fecha]/[hora] en la zona del teléfono
     * y usa el canal móvil; la UI agenda además el respaldo local. */
    fun crear(
        api: EdecanApi,
        texto: String,
        fecha: LocalDate,
        hora: LocalTime,
        onCreado: (Reminder) -> Unit = {},
    ) {
        val limpio = texto.trim()
        if (limpio.isEmpty() || _uiState.value.creando) return
        val dueAtIso = fecha.atTime(hora).atZone(ZoneId.systemDefault()).format(DateTimeFormatter.ISO_OFFSET_DATE_TIME)
        viewModelScope.launch {
            _uiState.update { it.copy(creando = true, errorCrear = null) }
            try {
                val creado = api.createReminder(texto = limpio, fecha = dueAtIso, canal = "mobile")
                _uiState.update {
                    it.copy(creando = false, recordatorios = listOf(creado) + it.recordatorios)
                }
                val fcmDisponible = runCatching { api.pushStatus().fcm }.getOrDefault(false)
                if (!fcmDisponible) onCreado(creado)
            } catch (e: ApiException) {
                _uiState.update { it.copy(creando = false, errorCrear = e.message) }
            }
        }
    }

    /** `PUT /v1/reminders/{id} {status: "sent"}` — "completar" un pendiente
     * a mano (ver `EdecanApi.completeReminder`). */
    fun completar(api: EdecanApi, reminderId: String) {
        if (reminderId in _uiState.value.idsOcupados) return
        viewModelScope.launch {
            _uiState.update { it.copy(idsOcupados = it.idsOcupados + reminderId, errorLista = null) }
            try {
                val actualizado = api.completeReminder(reminderId)
                _uiState.update { estado ->
                    estado.copy(
                        idsOcupados = estado.idsOcupados - reminderId,
                        recordatorios = estado.recordatorios.map { r -> if (r.id == reminderId) actualizado else r },
                    )
                }
            } catch (e: ApiException) {
                _uiState.update {
                    it.copy(idsOcupados = it.idsOcupados - reminderId, errorLista = e.message)
                }
            }
        }
    }
}
