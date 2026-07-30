package cc.edecan.app.ui.components

import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

private val FORMATO_FECHA_HORA = DateTimeFormatter.ofPattern("d MMM yyyy, HH:mm", Locale.forLanguageTag("es"))

/**
 * Convierte un `datetime` ISO-8601 con offset — como lo manda el backend
 * (`fastapi.encoders.jsonable_encoder` sobre un `datetime` aware, p. ej.
 * `Mission.created_at`, `Automation.next_run_at`, `Reminder.due_at`) — a un
 * texto corto en la zona horaria del propio dispositivo. Reutilizado por
 * `MisionesScreen`/`AutomatizacionesScreen`/`RecordatoriosScreen` (WP-V5-07)
 * en vez de repetir el mismo `runCatching` en cada una.
 *
 * Si `iso` es `null`/vacío o no se puede parsear (formato inesperado), se
 * devuelve tal cual (o `"—"` si está vacío) en vez de fallar — mismo
 * criterio tolerante que el resto de la app ante datos del servidor.
 */
fun formatearFechaHora(iso: String?): String {
    if (iso.isNullOrBlank()) return "—"
    return runCatching {
        OffsetDateTime.parse(iso).atZoneSameInstant(ZoneId.systemDefault()).format(FORMATO_FECHA_HORA)
    }.getOrDefault(iso)
}
