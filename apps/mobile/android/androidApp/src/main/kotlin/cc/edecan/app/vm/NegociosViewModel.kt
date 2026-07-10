package cc.edecan.app.vm

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import cc.edecan.shared.ApiException
import cc.edecan.shared.EdecanApi
import cc.edecan.shared.Invoice
import cc.edecan.shared.NegociosKpis
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class NegociosUiState(
    val cargando: Boolean = false,
    val kpis: NegociosKpis? = null,
    val facturas: List<Invoice> = emptyList(),
    val errorMensaje: String? = null,
)

/**
 * Estado y lógica de la pestaña Negocios: KPIs del mes (`GET
 * /v1/negocios/kpis`) + últimas facturas (`GET /v1/negocios/facturas`,
 * `ROADMAP_V2.md` WP-V2-12, `docs/negocios.md`). Ambas llamadas van en
 * paralelo — ninguna depende de la otra — así que un error en una no debe
 * tumbar a la otra: se manejan con sus propios try/catch en vez de una sola
 * llamada `Deferred.await()` conjunta que fallaría entera si cualquiera de
 * las dos lanza.
 */
class NegociosViewModel : ViewModel() {
    private val _uiState = MutableStateFlow(NegociosUiState())
    val uiState: StateFlow<NegociosUiState> = _uiState.asStateFlow()

    private var yaCargado = false

    /** Se llama desde `LaunchedEffect(Unit)` en `NegociosScreen` — `forzar`
     * permite un "pull to refresh"/reintento manual sin depender de que la
     * pantalla se recomponga desde cero. */
    fun cargar(api: EdecanApi, forzar: Boolean = false) {
        if (yaCargado && !forzar) return
        yaCargado = true
        viewModelScope.launch {
            _uiState.update { it.copy(cargando = true, errorMensaje = null) }

            val kpis = try {
                api.negociosKpis()
            } catch (e: ApiException) {
                _uiState.update { it.copy(errorMensaje = e.message) }
                null
            }
            val facturas = try {
                api.negociosFacturas()
            } catch (e: ApiException) {
                _uiState.update { it.copy(errorMensaje = combinarErrores(it.errorMensaje, e.message)) }
                emptyList()
            }

            _uiState.update {
                it.copy(cargando = false, kpis = kpis ?: it.kpis, facturas = facturas)
            }
        }
    }

    /** Junta dos mensajes de error (KPIs y facturas pueden fallar por
     * separado) sin perder el primero si ambos fallan. */
    private fun combinarErrores(previo: String?, nuevo: String?): String? =
        listOfNotNull(previo, nuevo).distinct().joinToString(" · ").ifBlank { null }
}
