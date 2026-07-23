package cc.edecan.app.vm

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import cc.edecan.shared.ApiException
import cc.edecan.shared.EdecanApi
import cc.edecan.shared.IdeTreeNode
import cc.edecan.shared.IdeTreeOut
import cc.edecan.shared.esBinario
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/** Una fila ya "aplanada" del árbol recursivo de `GET /v1/ide/tree` — el
 * árbol que manda el companion viene anidado (`IdeTreeNode.children`);
 * `IdeScreen` pinta una `LazyColumn` plana con sangría por [profundidad]
 * en vez de un árbol expandible/colapsable (el companion ya acota
 * profundidad/tamaño del lado del servidor — `MAX_TREE_DEPTH`/
 * `MAX_TREE_ENTRIES` en `edecan_companion.actions` — así que la lista plana
 * ya viene razonablemente chica). */
data class IdeEntrada(val nombre: String, val ruta: String, val esDirectorio: Boolean, val profundidad: Int)

data class IdeUiState(
    val cargando: Boolean = false,
    /** `null` = todavía no se sabe; `false` = sin companion conectado. */
    val conectado: Boolean? = null,
    val entradas: List<IdeEntrada> = emptyList(),
    val truncado: Boolean = false,
    val archivoRuta: String? = null,
    val archivoContenido: String? = null,
    val archivoContenidoOriginal: String? = null,
    val cargandoArchivo: Boolean = false,
    val guardandoArchivo: Boolean = false,
    val rutaActual: String = "",
    val comando: String = "",
    val salidaTerminal: String = "",
    val ejecutandoComando: Boolean = false,
    val errorMensaje: String? = null,
)

/**
 * Estado y lógica del IDE en Modo avanzado, solo lectura: `GET /v1/ide/status`
 * (¿hay companion conectado?), `GET /v1/ide/tree` (árbol del sandbox) y
 * `GET /v1/ide/file` (contenido de un archivo) — `ARCHITECTURE.md` §11,
 * `ROADMAP_V2.md` §7.6/§7.8, `docs/ide.md`. Sin editar/correr comandos/
 * buscar en este esqueleto (eso es el resto de las rutas de `/v1/ide`,
 * fuera del alcance pedido para este work package).
 */
class IdeViewModel : ViewModel() {
    private val _uiState = MutableStateFlow(IdeUiState())
    val uiState: StateFlow<IdeUiState> = _uiState.asStateFlow()

    private var yaCargado = false

    fun cargar(api: EdecanApi, forzar: Boolean = false) {
        if (yaCargado && !forzar) return
        yaCargado = true
        viewModelScope.launch {
            _uiState.update { it.copy(cargando = true, errorMensaje = null) }
            try {
                val estado = api.ideStatus()
                if (!estado.connected) {
                    _uiState.update { it.copy(cargando = false, conectado = false) }
                    return@launch
                }
                val ruta = _uiState.value.rutaActual.trim().ifBlank { null }
                val arbol = api.ideTree(ruta)
                _uiState.update {
                    it.copy(
                        cargando = false,
                        conectado = true,
                        entradas = aplanar(arbol),
                        rutaActual = arbol.path.takeUnless { value -> value == "." }.orEmpty(),
                        truncado = arbol.truncated,
                    )
                }
            } catch (e: ApiException) {
                _uiState.update { it.copy(cargando = false, errorMensaje = e.message) }
            }
        }
    }

    fun cambiarRuta(value: String) = _uiState.update { it.copy(rutaActual = value) }
    fun cambiarComando(value: String) = _uiState.update { it.copy(comando = value) }

    fun ejecutar(api: EdecanApi) {
        val command = _uiState.value.comando.trim()
        if (command.isEmpty() || _uiState.value.ejecutandoComando) return
        viewModelScope.launch {
            _uiState.update {
                it.copy(
                    comando = "",
                    ejecutandoComando = true,
                    salidaTerminal = it.salidaTerminal + "\n$ $command\n",
                    errorMensaje = null,
                )
            }
            try {
                val result = api.ideRun(command)
                _uiState.update {
                    it.copy(
                        ejecutandoComando = false,
                        salidaTerminal = it.salidaTerminal + result.stdout + result.stderr +
                            "\n[exit ${result.exitCode}]\n",
                    )
                }
            } catch (e: ApiException) {
                _uiState.update {
                    it.copy(
                        ejecutandoComando = false,
                        salidaTerminal = it.salidaTerminal + "\n${e.message}\n",
                        errorMensaje = e.message,
                    )
                }
            }
        }
    }

    fun abrirArchivo(ruta: String, api: EdecanApi) {
        viewModelScope.launch {
            _uiState.update {
                it.copy(archivoRuta = ruta, archivoContenido = null, cargandoArchivo = true, errorMensaje = null)
            }
            try {
                val archivo = api.ideFile(ruta)
                val contenido = if (archivo.esBinario) {
                    "(archivo binario — no se puede previsualizar en la app)"
                } else {
                    archivo.content
                }
                _uiState.update {
                    it.copy(
                        cargandoArchivo = false,
                        archivoContenido = contenido,
                        archivoContenidoOriginal = contenido,
                    )
                }
            } catch (e: ApiException) {
                _uiState.update { it.copy(cargandoArchivo = false, errorMensaje = e.message) }
            }
        }
    }

    fun cambiarContenido(value: String) = _uiState.update { it.copy(archivoContenido = value) }

    fun guardarArchivo(api: EdecanApi) {
        val state = _uiState.value
        val ruta = state.archivoRuta ?: return
        val content = state.archivoContenido ?: return
        if (content == state.archivoContenidoOriginal || state.guardandoArchivo) return
        viewModelScope.launch {
            _uiState.update { it.copy(guardandoArchivo = true, errorMensaje = null) }
            try {
                api.ideWrite(ruta, content)
                _uiState.update {
                    it.copy(guardandoArchivo = false, archivoContenidoOriginal = content)
                }
            } catch (e: ApiException) {
                _uiState.update { it.copy(guardandoArchivo = false, errorMensaje = e.message) }
            }
        }
    }

    fun cerrarArchivo() {
        _uiState.update {
            it.copy(archivoRuta = null, archivoContenido = null, archivoContenidoOriginal = null)
        }
    }

    private fun aplanar(arbol: IdeTreeOut): List<IdeEntrada> {
        val resultado = mutableListOf<IdeEntrada>()
        fun recorrer(nodos: List<IdeTreeNode>, prefijo: String, profundidad: Int) {
            for (nodo in nodos) {
                val ruta = if (prefijo.isBlank() || prefijo == ".") nodo.name else "$prefijo/${nodo.name}"
                resultado += IdeEntrada(nodo.name, ruta, nodo.isDir, profundidad)
                nodo.children?.let { recorrer(it, ruta, profundidad + 1) }
            }
        }
        recorrer(arbol.entries, arbol.path, 0)
        return resultado
    }
}
