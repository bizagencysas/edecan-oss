package cc.edecan.app.ui

import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration

/**
 * Renderizador Markdown pequeño y determinista para el chat. No convierte
 * HTML ni ejecuta enlaces: conserva el texto como contenido y aplica solo
 * presentación segura a encabezados, listas, citas, énfasis y código.
 */
internal fun markdownParaChat(markdown: String): AnnotatedString = buildAnnotatedString {
    var dentroDeCodigo = false
    val lineas = markdown.lines()
    lineas.forEachIndexed { index, lineaOriginal ->
        val lineaRecortada = lineaOriginal.trimStart()
        if (lineaRecortada.startsWith("```")) {
            dentroDeCodigo = !dentroDeCodigo
        } else {
            val inicio = length
            if (dentroDeCodigo) {
                append(lineaOriginal)
                addStyle(SpanStyle(fontFamily = FontFamily.Monospace), inicio, length)
            } else {
                appendBloqueMarkdown(lineaOriginal)
            }
        }
        if (index != lineas.lastIndex && !lineaRecortada.startsWith("```")) append('\n')
    }
}
private fun AnnotatedString.Builder.appendBloqueMarkdown(linea: String) {
    val trimmed = linea.trimStart()
    val indentacion = linea.take(linea.length - trimmed.length)
    append(indentacion)

    val headingMarks = trimmed.takeWhile { it == '#' }.length
    if (headingMarks in 1..6 && trimmed.getOrNull(headingMarks) == ' ') {
        val inicio = length
        appendInlineMarkdown(trimmed.drop(headingMarks + 1))
        addStyle(SpanStyle(fontWeight = FontWeight.Bold), inicio, length)
        return
    }

    if (trimmed.startsWith("> ") || trimmed == ">") {
        val inicio = length
        append("› ")
        appendInlineMarkdown(trimmed.removePrefix(">").trimStart())
        addStyle(SpanStyle(fontStyle = FontStyle.Italic), inicio, length)
        return
    }

    if (trimmed.length >= 2 && trimmed[0] in setOf('-', '*', '+') && trimmed[1] == ' ') {
        append("• ")
        appendInlineMarkdown(trimmed.drop(2))
        return
    }

    appendInlineMarkdown(trimmed)
}

private fun AnnotatedString.Builder.appendInlineMarkdown(texto: String) {
    var indice = 0
    while (indice < texto.length) {
        if (texto[indice] == '\\' && indice + 1 < texto.length) {
            append(texto[indice + 1])
            indice += 2
            continue
        }

        val token = when {
            texto.startsWith("**", indice) -> "**"
            texto.startsWith("__", indice) -> "__"
            texto.startsWith("~~", indice) -> "~~"
            texto[indice] == '`' -> "`"
            texto[indice] == '*' -> "*"
            texto[indice] == '_' -> "_"
            else -> null
        }
        if (token == null) {
            append(texto[indice])
            indice += 1
            continue
        }

        val cierre = texto.indexOf(token, startIndex = indice + token.length)
        if (cierre <= indice + token.length) {
            append(token)
            indice += token.length
            continue
        }

        val inicioEstilo = length
        append(texto.substring(indice + token.length, cierre))
        val estilo = when (token) {
            "**", "__" -> SpanStyle(fontWeight = FontWeight.Bold)
            "~~" -> SpanStyle(textDecoration = TextDecoration.LineThrough)
            "`" -> SpanStyle(fontFamily = FontFamily.Monospace)
            else -> SpanStyle(fontStyle = FontStyle.Italic)
        }
        addStyle(estilo, inicioEstilo, length)
        indice = cierre + token.length
    }
}
