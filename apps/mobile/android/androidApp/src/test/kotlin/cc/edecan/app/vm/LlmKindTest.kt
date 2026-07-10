package cc.edecan.app.vm

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * Tests JVM puros de [LlmKind] — el vocabulario/campos que decide qué pinta
 * el formulario "Conectar LLM" de `PerfilScreen`, movidos a esta clase
 * precisamente para poder testearlos sin Compose/Android (ver su KDoc).
 * Fija el mismo contrato que `edecan_api.routers.credentials`
 * (`_LLM_KINDS`, `_LLM_KINDS_REQUIEREN_API_KEY`, `_LOCAL_ONLY_LLM_KINDS`).
 */
class LlmKindTest {

    @Test
    fun anthropic_y_vertex_exigen_api_key() {
        assertTrue(LlmKind.ANTHROPIC.apiKeyObligatoria)
        assertTrue(LlmKind.VERTEX.apiKeyObligatoria)
        assertFalse(LlmKind.ANTHROPIC.aceptaBaseUrl)
        assertFalse(LlmKind.VERTEX.aceptaBaseUrl)
    }

    @Test
    fun openai_compat_acepta_api_key_opcional_y_exige_base_url() {
        assertTrue(LlmKind.OPENAI_COMPAT.aceptaApiKey)
        assertFalse(LlmKind.OPENAI_COMPAT.apiKeyObligatoria)
        assertTrue(LlmKind.OPENAI_COMPAT.aceptaBaseUrl)
    }

    @Test
    fun ollama_acepta_base_url_opcional_y_ningun_api_key() {
        assertFalse(LlmKind.OLLAMA.aceptaApiKey)
        assertTrue(LlmKind.OLLAMA.aceptaBaseUrl)
    }

    @Test
    fun los_tres_cli_locales_no_llevan_ningun_secreto() {
        assertFalse(LlmKind.CLAUDE_CLI.aceptaApiKey)
        assertFalse(LlmKind.CODEX_CLI.aceptaApiKey)
        assertFalse(LlmKind.OLLAMA.aceptaApiKey)
    }

    @Test
    fun solo_los_tres_locales_estan_marcados_soloLocal() {
        val locales = LlmKind.entries.filter { it.soloLocal }.toSet()
        assertEquals(setOf(LlmKind.CLAUDE_CLI, LlmKind.CODEX_CLI, LlmKind.OLLAMA), locales)
    }

    @Test
    fun ningun_kind_solo_local_exige_api_key() {
        // Los tres locales se validan corriendo `<binario> --version` (o,
        // en el caso de Ollama, un ping HTTP local) — ninguno debería
        // quedar marcado como si necesitara una API key.
        LlmKind.entries.filter { it.soloLocal }.forEach { kind ->
            assertFalse(kind.apiKeyObligatoria, "$kind no debería exigir api_key")
        }
    }

    @Test
    fun cada_valor_coincide_con_el_vocabulario_de_credentials_py() {
        val esperado = setOf("anthropic", "openai_compat", "vertex", "claude_cli", "codex_cli", "ollama")
        assertEquals(esperado, LlmKind.entries.map { it.valor }.toSet())
    }

    @Test
    fun cada_kind_tiene_una_etiqueta_no_vacia_para_el_FilterChip() {
        LlmKind.entries.forEach { kind -> assertTrue(kind.etiqueta.isNotBlank(), "$kind sin etiqueta") }
    }
}
