package cc.edecan.app.vm

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import cc.edecan.shared.ApiException
import cc.edecan.shared.CredentialsOut
import cc.edecan.shared.EdecanApi
import cc.edecan.shared.LlmCredentialsIn
import cc.edecan.shared.LlmModelsIn
import cc.edecan.shared.LlmModelsOut
import cc.edecan.shared.LiveProfile
import cc.edecan.shared.ProfileIdentity
import cc.edecan.shared.SetupStatusOut
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/** Los `kind` de `PUT /v1/credentials/llm` (`edecan_api.routers.credentials`,
 * mismo vocabulario/orden que su `_LLM_KINDS`) más los campos que decide
 * mostrar/exigir el formulario de `PerfilScreen` — vive acá (no como
 * extension properties sueltas en la UI) para que sea un único punto de
 * verdad testeable sin Compose (`androidApp/src/test`, ver `LlmKindTest`).
 *
 * - [soloLocal]: solo se ofrece si `SetupStatusOut.localMode` (backend
 *   corriendo en la máquina del propio cliente, `ARCHITECTURE.md` §12.b).
 * - [aceptaApiKey]/[apiKeyObligatoria]: mismo criterio que
 *   `credentials.py::_LLM_KINDS_REQUIEREN_API_KEY` — `openai_compat` acepta
 *   una `api_key` opcional pero no la exige; `anthropic`/`vertex` sí.
 * - [aceptaBaseUrl]: `openai_compat` la exige (validado en el propio
 *   backend), `ollama` la acepta opcional (default `localhost:11434`).
 */
enum class LlmKind(
    val valor: String,
    val etiqueta: String,
    val soloLocal: Boolean,
    val aceptaApiKey: Boolean,
    val apiKeyObligatoria: Boolean,
    val aceptaBaseUrl: Boolean,
) {
    ANTHROPIC(
        "anthropic", "Anthropic",
        soloLocal = false, aceptaApiKey = true, apiKeyObligatoria = true, aceptaBaseUrl = false,
    ),
    OPENAI_COMPAT(
        "openai_compat", "Compatible con OpenAI",
        soloLocal = false, aceptaApiKey = true, apiKeyObligatoria = false, aceptaBaseUrl = true,
    ),
    VERTEX(
        "vertex", "Vertex / Gemini",
        soloLocal = false, aceptaApiKey = true, apiKeyObligatoria = true, aceptaBaseUrl = false,
    ),
    CLAUDE_CLI(
        "claude_cli", "Claude CLI (local)",
        soloLocal = true, aceptaApiKey = false, apiKeyObligatoria = false, aceptaBaseUrl = false,
    ),
    CODEX_CLI(
        "codex_cli", "Codex CLI (local)",
        soloLocal = true, aceptaApiKey = false, apiKeyObligatoria = false, aceptaBaseUrl = false,
    ),
    OLLAMA(
        "ollama", "Ollama (local)",
        soloLocal = true, aceptaApiKey = false, apiKeyObligatoria = false, aceptaBaseUrl = true,
    ),
}

data class PerfilUiState(
    val perfilVivo: LiveProfile? = null,
    val cargandoPerfil: Boolean = false,
    val guardandoPerfil: Boolean = false,
    val perfilGuardado: Boolean = false,
    val errorPerfil: String? = null,
    val cargando: Boolean = false,
    val credenciales: CredentialsOut? = null,
    val setupStatus: SetupStatusOut? = null,
    val kindSeleccionado: LlmKind = LlmKind.ANTHROPIC,
    val conectando: Boolean = false,
    val errorConexion: String? = null,
    val conectadoOk: Boolean = false,
    val catalogoModelos: LlmModelsOut? = null,
    val modeloActivoPrincipal: String = "",
    val modeloActivoRapido: String = "",
    val modeloActivoProfundo: String = "",
    val esfuerzoProfundo: String = "xhigh",
    val actualizandoModelo: Boolean = false,
    val modeloActualizado: Boolean = false,
    val errorModelo: String? = null,
    val errorCarga: String? = null,
)

/**
 * Estado y lógica de la sección "Conectar LLM" de Ajustes:
 * `GET /v1/credentials` + `GET /v1/setup/status` para saber qué hay
 * conectado hoy y si el backend corre en modo local (habilita
 * `claude_cli`/`codex_cli`/`ollama`), y `PUT /v1/credentials/llm` para
 * "pegar y validar" una credencial nueva (`DIRECCION_ACTUAL.md`, "Principio
 * de UX no negociable").
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
                    null // el wizard de arranque es opcional; sin él, se asume hosted (sin CLI/Ollama).
                }
                val kindYaConectado = credenciales.llm?.kind?.let { valorKind ->
                    LlmKind.entries.find { candidato -> candidato.valor == valorKind }
                }
                val catalogo = if (credenciales.llm != null) {
                    try {
                        api.modelosLlm()
                    } catch (_: ApiException) {
                        null
                    }
                } else {
                    null
                }
                _uiState.update { estado ->
                    estado.copy(
                        cargando = false,
                        credenciales = credenciales,
                        setupStatus = configuracionSetup,
                        kindSeleccionado = kindYaConectado ?: estado.kindSeleccionado,
                        catalogoModelos = catalogo,
                        modeloActivoPrincipal = catalogo?.modelPrincipal
                            ?: credenciales.llm?.modelPrincipal.orEmpty(),
                        modeloActivoRapido = catalogo?.modelRapido
                            ?: credenciales.llm?.modelRapido.orEmpty(),
                        modeloActivoProfundo = catalogo?.modelProfundo
                            ?: credenciales.llm?.modelProfundo.orEmpty(),
                        esfuerzoProfundo = catalogo?.reasoningEffortProfundo ?: "xhigh",
                    )
                }
            } catch (e: ApiException) {
                _uiState.update { it.copy(cargando = false, errorCarga = e.message) }
            }
        }
    }

    fun elegirKind(kind: LlmKind) {
        _uiState.update { it.copy(kindSeleccionado = kind, errorConexion = null, conectadoOk = false) }
    }

    fun elegirModeloPrincipal(modelo: String) {
        _uiState.update {
            it.copy(modeloActivoPrincipal = modelo, errorModelo = null, modeloActualizado = false)
        }
    }

    fun elegirModeloRapido(modelo: String) {
        _uiState.update {
            it.copy(modeloActivoRapido = modelo, errorModelo = null, modeloActualizado = false)
        }
    }

    fun elegirModeloProfundo(modelo: String) {
        _uiState.update {
            it.copy(modeloActivoProfundo = modelo, errorModelo = null, modeloActualizado = false)
        }
    }

    fun elegirEsfuerzoProfundo(esfuerzo: String) {
        _uiState.update {
            it.copy(esfuerzoProfundo = esfuerzo, errorModelo = null, modeloActualizado = false)
        }
    }

    fun actualizarModelos(api: EdecanApi) {
        if (_uiState.value.actualizandoModelo) return
        val principal = _uiState.value.modeloActivoPrincipal.trim()
        val rapido = _uiState.value.modeloActivoRapido.trim().ifBlank { principal }
        val profundo = _uiState.value.modeloActivoProfundo.trim().ifBlank { principal }
        if (principal.isBlank()) {
            _uiState.update { it.copy(errorModelo = "Escribe o elige un modelo principal.") }
            return
        }
        viewModelScope.launch {
            _uiState.update {
                it.copy(actualizandoModelo = true, errorModelo = null, modeloActualizado = false)
            }
            try {
                api.actualizarModelosLlm(
                    LlmModelsIn(
                        modelPrincipal = principal,
                        modelRapido = rapido,
                        modelProfundo = profundo,
                        reasoningEffortProfundo = _uiState.value.esfuerzoProfundo,
                    ),
                )
                val catalogo = api.modelosLlm()
                val credenciales = api.credentials()
                _uiState.update {
                    it.copy(
                        actualizandoModelo = false,
                        modeloActualizado = true,
                        catalogoModelos = catalogo,
                        credenciales = credenciales,
                        modeloActivoPrincipal = catalogo.modelPrincipal.orEmpty(),
                        modeloActivoRapido = catalogo.modelRapido.orEmpty(),
                        modeloActivoProfundo = catalogo.modelProfundo.orEmpty(),
                        esfuerzoProfundo = catalogo.reasoningEffortProfundo ?: "xhigh",
                    )
                }
            } catch (e: ApiException) {
                _uiState.update {
                    it.copy(actualizandoModelo = false, errorModelo = e.message)
                }
            }
        }
    }

    /** `modelRapido` no tiene campo propio en el formulario a propósito
     * (simplificación deliberada: casi nadie necesita overridear el modelo
     * "rápido" del alias `"rapido"` por separado del `"principal"` en la
     * primera conexión) — siempre se manda `null`, así que el backend usa
     * su propio default para ese alias. */
    fun conectarLlm(api: EdecanApi, apiKey: String, baseUrl: String, modelPrincipal: String) {
        if (_uiState.value.conectando) return
        val kind = _uiState.value.kindSeleccionado
        viewModelScope.launch {
            _uiState.update { it.copy(conectando = true, errorConexion = null, conectadoOk = false) }
            try {
                api.conectarLlm(
                    LlmCredentialsIn(
                        kind = kind.valor,
                        apiKey = apiKey.trim().ifBlank { null },
                        baseUrl = baseUrl.trim().ifBlank { null },
                        modelPrincipal = modelPrincipal.trim().ifBlank { null },
                        modelRapido = null,
                        validate = true,
                    ),
                )
                val credenciales = api.credentials()
                val catalogo = try {
                    api.modelosLlm()
                } catch (_: ApiException) {
                    null
                }
                _uiState.update {
                    it.copy(
                        conectando = false,
                        conectadoOk = true,
                        credenciales = credenciales,
                        catalogoModelos = catalogo,
                        modeloActivoPrincipal = catalogo?.modelPrincipal.orEmpty(),
                        modeloActivoRapido = catalogo?.modelRapido.orEmpty(),
                        modeloActivoProfundo = catalogo?.modelProfundo.orEmpty(),
                        esfuerzoProfundo = catalogo?.reasoningEffortProfundo ?: "xhigh",
                    )
                }
            } catch (e: ApiException) {
                _uiState.update { it.copy(conectando = false, errorConexion = e.message) }
            }
        }
    }
}
