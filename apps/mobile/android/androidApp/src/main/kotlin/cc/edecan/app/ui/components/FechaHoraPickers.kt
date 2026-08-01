@file:OptIn(ExperimentalMaterial3Api::class)

package cc.edecan.app.ui.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.DatePicker
import androidx.compose.material3.DatePickerDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TimePicker
import androidx.compose.material3.rememberDatePickerState
import androidx.compose.material3.rememberTimePickerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import java.time.Instant
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.util.Locale

/**
 * Botón + `DatePickerDialog`/`TimePicker` de Material3 para elegir fecha y
 * hora locales — usados por `RecordatoriosScreen` (WP-V5-07, "alta con texto
 * + fecha/hora pickers de Material3") al crear un recordatorio. Compose
 * Multiplatform 1.11.1 sustituye `compose.material3` por el `androidx.
 * compose.material3` real en el target Android (`gradle/libs.versions.toml`),
 * así que estos son los mismos `DatePicker`/`TimePicker` de Jetpack Compose.
 */

private val FORMATO_FECHA_CORTA = DateTimeFormatter.ofPattern("d MMM yyyy", Locale.forLanguageTag("es"))
private val FORMATO_HORA_CORTA = DateTimeFormatter.ofPattern("HH:mm", Locale.forLanguageTag("es"))

/**
 * Botón que abre un `DatePickerDialog` y reporta la fecha elegida.
 *
 * `DatePickerState.selectedDateMillis` representa medianoche UTC del día
 * elegido (documentado así por la propia API de Material3) — acá se
 * extraen solo year/month/day de ese instante EN UTC (`ZoneOffset.UTC`, no
 * `ZoneId.systemDefault()`) para no arrastrar el desfase horario del
 * dispositivo al combinar el resultado con [SelectorHora] después; es el
 * mismo *gotcha* documentado en la guía oficial de Compose para este picker.
 */
@Composable
fun SelectorFecha(fecha: LocalDate, onFechaCambia: (LocalDate) -> Unit, modifier: Modifier = Modifier) {
    var mostrarDialogo by remember { mutableStateOf(false) }

    OutlinedButton(onClick = { mostrarDialogo = true }, modifier = modifier) {
        Text(fecha.format(FORMATO_FECHA_CORTA))
    }

    if (mostrarDialogo) {
        val estado = rememberDatePickerState(
            initialSelectedDateMillis = fecha.atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli(),
        )
        DatePickerDialog(
            onDismissRequest = { mostrarDialogo = false },
            confirmButton = {
                TextButton(onClick = {
                    estado.selectedDateMillis?.let { millis ->
                        onFechaCambia(Instant.ofEpochMilli(millis).atZone(ZoneOffset.UTC).toLocalDate())
                    }
                    mostrarDialogo = false
                }) { Text("Aceptar") }
            },
            dismissButton = { TextButton(onClick = { mostrarDialogo = false }) { Text("Cancelar") } },
        ) {
            DatePicker(state = estado)
        }
    }
}

/**
 * Botón que abre un diálogo con `TimePicker` y reporta la hora elegida.
 * Material3 no trae un `TimePickerDialog` ya armado (a diferencia de
 * `DatePickerDialog`) — este envuelve `TimePicker` a mano en `Dialog` +
 * `Surface`, el patrón que recomienda la propia documentación de Compose
 * para este caso.
 */
@Composable
fun SelectorHora(hora: LocalTime, onHoraCambia: (LocalTime) -> Unit, modifier: Modifier = Modifier) {
    var mostrarDialogo by remember { mutableStateOf(false) }

    OutlinedButton(onClick = { mostrarDialogo = true }, modifier = modifier) {
        Text(hora.format(FORMATO_HORA_CORTA))
    }

    if (mostrarDialogo) {
        val estado = rememberTimePickerState(initialHour = hora.hour, initialMinute = hora.minute, is24Hour = true)
        Dialog(onDismissRequest = { mostrarDialogo = false }) {
            Surface(shape = MaterialTheme.shapes.extraLarge) {
                Column(modifier = Modifier.padding(24.dp)) {
                    TimePicker(state = estado)
                    Row(
                        modifier = Modifier.padding(top = 12.dp),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        TextButton(onClick = { mostrarDialogo = false }) { Text("Cancelar") }
                        Button(onClick = {
                            onHoraCambia(LocalTime.of(estado.hour, estado.minute))
                            mostrarDialogo = false
                        }) { Text("Aceptar") }
                    }
                }
            }
        }
    }
}
