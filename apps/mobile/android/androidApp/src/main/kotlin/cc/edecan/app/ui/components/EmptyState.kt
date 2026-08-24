package cc.edecan.app.ui.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import cc.edecan.app.ui.theme.EdecanColors

/**
 * Estado vacío reutilizable — el equivalente Kotlin de `EmptyStateView.swift`
 * (iOS), mismo espíritu que `EmptyState` en `apps/web/src/components/ui.tsx`
 * para que la app se sienta consistente con el panel web. Usado tanto por
 * pantallas todavía sin funcionalidad real como por estados vacíos legítimos
 * de pantallas ya reales (p. ej. `IdeScreen` sin companion conectado o con
 * un sandbox vacío) — `etiquetaRoadmap` (default `"Próximamente"`) distingue
 * ambos casos: pásalo `null` para un estado vacío real.
 */
@Composable
fun EmptyState(
    emoji: String,
    titulo: String,
    descripcion: String,
    modifier: Modifier = Modifier,
    etiquetaRoadmap: String? = "Próximamente",
) {
    Column(
        modifier = modifier.fillMaxSize().padding(32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text(text = emoji, style = MaterialTheme.typography.displayMedium)
        Spacer(Modifier.height(8.dp))
        Text(
            text = titulo,
            style = MaterialTheme.typography.titleLarge,
            textAlign = TextAlign.Center,
        )
        Text(
            text = descripcion,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = TextAlign.Center,
            modifier = Modifier.padding(top = 8.dp, start = 16.dp, end = 16.dp),
        )
        if (etiquetaRoadmap != null) {
            Card(
                modifier = Modifier.padding(top = 16.dp),
                colors = CardDefaults.cardColors(containerColor = EdecanColors.Morado.copy(alpha = 0.12f)),
            ) {
                Text(
                    text = etiquetaRoadmap,
                    style = MaterialTheme.typography.labelMedium,
                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
                )
            }
        }
    }
}
