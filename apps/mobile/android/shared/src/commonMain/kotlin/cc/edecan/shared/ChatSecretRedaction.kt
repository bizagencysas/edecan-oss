package cc.edecan.shared

/** Evita pintar secretos en la burbuja optimista antes de que el master los
 * reciba, cifre y persista ya redactados. */
object ChatSecretRedaction {
    private val patterns = listOf(
        Regex("\\bsk[-_][A-Za-z0-9_-]{8,}", RegexOption.IGNORE_CASE),
        Regex("\\bBearer\\s+[A-Za-z0-9._~+/=-]{8,}", RegexOption.IGNORE_CASE),
        Regex("\\b(?:rk_live|rk_test|whsec)_[A-Za-z0-9]{8,}", RegexOption.IGNORE_CASE),
        Regex("\\b(?:AKIA|ASIA)[A-Z0-9]{16}\\b"),
    )

    fun redact(text: String): String = patterns.fold(text) { safe, pattern ->
        pattern.replace(safe, "[credencial protegida]")
    }
}
