@file:OptIn(ExperimentalMaterial3Api::class)

package cc.edecan.app.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import cc.edecan.app.vm.SessionViewModel

private data class AccesoDirecto(
    val emoji: String,
    val titulo: String,
    val subtitulo: String,
    /** `null` = todavía no tiene pantalla propia ("Próximamente en la
     * app") — la tarjeta queda inerte, mismo criterio que `EmptyState`
     * (`etiquetaRoadmap`). */
    val onClick: (() -> Unit)? = null,
)

/**
 * Actividad: accesos simples al trabajo que Edecan ejecuta o vigila.
 * "Misiones"/"Automatizaciones"/"Recordatorios" (WP-V5-07) se navegan SOLO
 * desde acá, vía [onAbrirMisiones]/[onAbrirAutomatizaciones]/
 * [onAbrirRecordatorios] — `RootNav.kt` las muestra como una pantalla propia
 * encima de la barra inferior en vez de sumarlas como pestañas nuevas (ver
 * su KDoc). Mismo contenido que `InicioView.swift` (iOS).
 */
@Composable
fun InicioScreen(
    sessionViewModel: SessionViewModel = viewModel(),
    onAbrirMisiones: () -> Unit = {},
    onAbrirAutomatizaciones: () -> Unit = {},
    onAbrirRecordatorios: () -> Unit = {},
    onAbrirRemoto: () -> Unit = {},
) {
    val uiState by sessionViewModel.uiState.collectAsState()

    LaunchedEffect(Unit) { sessionViewModel.cargarMe() }

    val accesos = listOf(
        AccesoDirecto("🧭", "Trabajo delegado", "Objetivos y aprobaciones", onAbrirMisiones),
        AccesoDirecto("⚡", "Rutinas", "Acciones programadas", onAbrirAutomatizaciones),
        AccesoDirecto("🔔", "Recordatorios", "Pendientes y completados", onAbrirRecordatorios),
        AccesoDirecto("🖥️", "Remoto", "Ver y controlar tu Mac, con tu aprobación", onAbrirRemoto),
    )

    Scaffold(topBar = { TopAppBar(title = { Text("Actividad") }) }) { padding ->
        Column(
            modifier = Modifier.padding(padding).padding(16.dp).verticalScroll(rememberScrollState()),
        ) {
            if (uiState.cargandoMe && uiState.me == null) {
                CircularProgressIndicator(modifier = Modifier.padding(bottom = 12.dp))
            } else {
                Text(
                    "Todo lo que Edecan está haciendo",
                    style = MaterialTheme.typography.headlineMedium,
                )
                Text(
                    "Revisa avances, decisiones pendientes y rutinas desde un solo lugar.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            // Grid de 2 columnas hecho a mano con Row/weight — a propósito,
            // NO `LazyVerticalGrid`: este `Column` ahora scrollea
            // (`verticalScroll`, 6 tarjetas fijas ya no caben siempre en una
            // pantalla chica) y anidar un layout lazy dentro de un scroll
            // del mismo eje revienta en tiempo real con
            // `IllegalStateException: Vertically scrollable component was
            // measured with an infinity maximum height constraints` (mismo
            // gotcha ya documentado en `NegociosScreen.kt::KpiCard`). Son
            // solo 6 tarjetas fijas — no hay pérdida real de rendimiento.
            Column(
                modifier = Modifier.padding(vertical = 20.dp),
                verticalArrangement = Arrangement.spacedBy(14.dp),
            ) {
                accesos.chunked(2).forEach { fila ->
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(14.dp),
                    ) {
                        fila.forEach { acceso -> AccesoDirectoCard(acceso, modifier = Modifier.weight(1f)) }
                        if (fila.size == 1) Spacer(modifier = Modifier.weight(1f))
                    }
                }
            }

            uiState.errorMensaje?.let { error ->
                Text(error, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}

@Composable
private fun AccesoDirectoCard(acceso: AccesoDirecto, modifier: Modifier = Modifier) {
    val alClick = acceso.onClick
    val base = modifier.fillMaxWidth()
    Card(modifier = if (alClick != null) base.clickable(onClick = alClick) else base) {
        Column(modifier = Modifier.padding(16.dp)) {
            Text(acceso.emoji, style = MaterialTheme.typography.headlineSmall)
            Text(
                acceso.titulo,
                style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.padding(top = 8.dp),
            )
            Text(
                acceso.subtitulo,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}
