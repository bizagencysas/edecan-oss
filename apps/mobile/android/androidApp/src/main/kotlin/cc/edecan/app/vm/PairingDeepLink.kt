package cc.edecan.app.vm

import java.net.URI
import java.net.URLDecoder
import java.net.InetAddress

internal class SolicitudEmparejamiento(
    val serverUrl: String,
    val pairingToken: String,
)

/** Parser estricto del único deep link sensible que acepta la app. Evita
 * parámetros duplicados, credenciales embebidas y conservar el token fuera
 * del tiempo mínimo necesario para hacer el claim. */
internal fun parsearEnlaceEmparejamiento(raw: String): SolicitudEmparejamiento? {
    if (raw.length !in 1..MAX_PAIRING_LINK_CHARS) return null
    val uri = runCatching { URI(raw) }.getOrNull() ?: return null
    if (!uri.scheme.equals("edecan", ignoreCase = true) ||
        !uri.host.equals("pair", ignoreCase = true) ||
        !uri.fragment.isNullOrEmpty()
    ) return null

    val parameters = mutableMapOf<String, String>()
    for (part in uri.rawQuery?.split('&').orEmpty()) {
        if (part.isBlank()) continue
        val separator = part.indexOf('=')
        if (separator <= 0) return null
        val key = decodeQuery(part.substring(0, separator)) ?: return null
        val value = decodeQuery(part.substring(separator + 1)) ?: return null
        if (key in parameters) return null
        parameters[key] = value
    }
    if (parameters.keys != setOf("server", "token")) return null

    val token = parameters["token"]?.takeIf { it.isNotBlank() && it.length <= MAX_PAIRING_TOKEN_CHARS }
        ?: return null
    val server = validarUrlServidor(parameters["server"] ?: return null) ?: return null
    return SolicitudEmparejamiento(server, token)
}

/** Política única para QR, entrada manual y valor persistido. HTTPS acepta
 * hosts públicos; HTTP solo destinos inequívocamente locales/LAN. Android
 * no permite expresar rangos CIDR en Network Security Config, por eso este
 * gate debe ejecutarse antes de crear o reapuntar cualquier cliente API. */
internal fun validarUrlServidor(raw: String): String? {
    val normalized = raw.trim().trimEnd('/')
    val uri = runCatching { URI(normalized) }.getOrNull() ?: return null
    val scheme = uri.scheme?.lowercase() ?: return null
    val host = uri.host?.lowercase()?.takeIf { it.isNotBlank() } ?: return null
    if (scheme !in setOf("http", "https")) return null
    if (uri.rawUserInfo != null || uri.rawQuery != null || uri.rawFragment != null) return null
    if (uri.port !in -1..65535 || uri.port == 0) return null
    if (scheme == "http" && !esHostHttpLocal(host)) return null
    return normalized
}

private fun esHostHttpLocal(host: String): Boolean {
    if (host == "localhost" || host.endsWith(".localhost") || host.endsWith(".local")) return true
    if ('.' !in host && ':' !in host) return true // hostname LAN de una sola etiqueta.
    parsearIpv4(host)?.let { (a, b, _, _) ->
        return a == 10 || a == 127 ||
            (a == 172 && b in 16..31) ||
            (a == 192 && b == 168) ||
            (a == 169 && b == 254)
    }
    if (':' !in host || '%' in host) return false
    val address = runCatching { InetAddress.getByName(host.removePrefix("[").removeSuffix("]")) }
        .getOrNull() ?: return false
    val bytes = address.address
    if (bytes.size == 4) {
        return esHostHttpLocal(bytes.joinToString(".") { (it.toInt() and 0xff).toString() })
    }
    val first = bytes.firstOrNull()?.toInt()?.and(0xff) ?: return false
    val second = bytes.getOrNull(1)?.toInt()?.and(0xff) ?: return false
    return address.isLoopbackAddress || address.isLinkLocalAddress ||
        first == 0xfc || first == 0xfd || (first == 0xfe && second in 0x80..0xbf)
}

private fun parsearIpv4(host: String): List<Int>? {
    val parts = host.split('.')
    if (parts.size != 4) return null
    return parts.map { part ->
        if (part.isEmpty() || (part.length > 1 && part.startsWith('0'))) return null
        part.toIntOrNull()?.takeIf { it in 0..255 } ?: return null
    }
}

private fun decodeQuery(raw: String): String? = runCatching {
    URLDecoder.decode(raw, Charsets.UTF_8.name())
}.getOrNull()

private const val MAX_PAIRING_LINK_CHARS = 16 * 1024
private const val MAX_PAIRING_TOKEN_CHARS = 8 * 1024
