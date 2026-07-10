package cc.edecan.app.ui.components

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.dp
import cc.edecan.shared.CanalVenta

/** Paleta fija para los "gajos" de la dona — arranca en los acentos de
 * marca (`EdecanColors.Morado`/`.Azul`, duplicados acá como [Color] crudo
 * porque este archivo no depende de `ui.theme` para mantenerse reusable) y
 * sigue con colores bien diferenciables entre sí; el último es el gris que
 * usa el bucket "otros" que ya arma el backend (`edecan_business.kpis`). */
private val PaletaDona = listOf(
    Color(0xFF8257F5),
    Color(0xFF4A7DFA),
    Color(0xFF22C55E),
    Color(0xFFF59E0B),
    Color(0xFFEC4899),
    Color(0xFF14B8A6),
    Color(0xFF94A3B8),
)

/**
 * Dona de "ventas por canal" (`NegociosKpis.porCanal`) dibujada a mano con
 * `Canvas` de Compose — cero dependencias nuevas de charting (pedido
 * explícito del work package). Sin datos (todos los canales en `total <= 0`,
 * o la lista vacía) dibuja un anillo gris apagado en vez de nada, para que
 * la tarjeta nunca quede vacía o rota.
 */
@Composable
fun DonutChart(datos: List<CanalVenta>, modifier: Modifier = Modifier) {
    val total = datos.sumOf { it.total.coerceAtLeast(0.0) }

    Row(modifier = modifier, verticalAlignment = Alignment.CenterVertically) {
        Canvas(modifier = Modifier.size(110.dp)) {
            val grosor = size.minDimension * 0.24f
            if (total <= 0.0) {
                drawArc(
                    color = Color.Gray.copy(alpha = 0.25f),
                    startAngle = 0f,
                    sweepAngle = 360f,
                    useCenter = false,
                    style = Stroke(width = grosor),
                )
                return@Canvas
            }
            var anguloInicio = -90f
            datos.forEachIndexed { indice, canal ->
                val proporcion = (canal.total.coerceAtLeast(0.0) / total).toFloat()
                if (proporcion <= 0f) return@forEachIndexed
                val barrido = proporcion * 360f
                drawArc(
                    color = PaletaDona[indice % PaletaDona.size],
                    startAngle = anguloInicio,
                    sweepAngle = barrido,
                    useCenter = false,
                    style = Stroke(width = grosor),
                )
                anguloInicio += barrido
            }
        }

        Column(modifier = Modifier.padding(start = 16.dp)) {
            if (datos.isEmpty()) {
                Text(
                    "Todavía no hay ventas registradas este mes.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            datos.forEachIndexed { indice, canal ->
                val porcentaje = if (total > 0.0) canal.total.coerceAtLeast(0.0) / total * 100 else 0.0
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.padding(vertical = 3.dp),
                ) {
                    Box(
                        modifier = Modifier
                            .size(10.dp)
                            .clip(CircleShape)
                            .background(PaletaDona[indice % PaletaDona.size]),
                    )
                    Text(
                        "${canal.canal} · ${"%.0f".format(porcentaje)}%",
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.padding(start = 6.dp),
                    )
                }
            }
        }
    }
}
