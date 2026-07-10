@file:OptIn(ExperimentalMaterial3Api::class)

package cc.edecan.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
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
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import cc.edecan.app.ui.components.DonutChart
import cc.edecan.app.ui.theme.EdecanColors
import cc.edecan.app.vm.NegociosViewModel
import cc.edecan.app.vm.SessionViewModel
import cc.edecan.shared.ActividadItem
import cc.edecan.shared.Invoice
import cc.edecan.shared.montoDouble
import java.util.Locale

/**
 * Pestaña "Negocios": KPIs del mes (`GET /v1/negocios/kpis`) + dona de
 * ventas por canal (`DonutChart`, Canvas puro) + últimas facturas (`GET
 * /v1/negocios/facturas`) — `ROADMAP_V2.md` WP-V2-12, `docs/negocios.md`.
 * Lógica real en [NegociosViewModel]; esta pantalla solo dibuja su estado.
 */
@Composable
fun NegociosScreen(
    sessionViewModel: SessionViewModel = viewModel(),
    negociosViewModel: NegociosViewModel = viewModel(),
) {
    val uiState by negociosViewModel.uiState.collectAsState()
    val api = sessionViewModel.api

    LaunchedEffect(api) { api?.let { negociosViewModel.cargar(it) } }

    Scaffold(topBar = { TopAppBar(title = { Text("Negocios") }) }) { padding ->
        if (uiState.cargando && uiState.kpis == null) {
            Box(modifier = Modifier.padding(padding).fillMaxSize()) {
                CircularProgressIndicator(modifier = Modifier.padding(32.dp))
            }
            return@Scaffold
        }

        Column(
            modifier = Modifier
                .padding(padding)
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
        ) {
            val kpis = uiState.kpis
            if (kpis != null) {
                Text(
                    "Mes: ${kpis.mes}",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )

                // Grid de 2 columnas hecho a mano con Row/weight — a
                // propósito, NO `LazyVerticalGrid`: anidar un layout lazy
                // dentro de este `Column.verticalScroll(...)` (misma
                // dirección, vertical) revienta en tiempo real con
                // `IllegalStateException: Vertically scrollable component
                // was measured with an infinity maximum height constraints`
                // (el propio mensaje de Compose recomienda exactamente
                // esto: para una lista corta y fija, un layout normal en
                // vez de anidar Lazy dentro de Lazy/scroll). Son solo 6
                // tarjetas fijas — no hay pérdida real de rendimiento.
                val tarjetasKpi = listOf(
                    Triple("Ingresos", formatearMonto(kpis.ingresos), EdecanColors.Morado),
                    Triple("Gastos", formatearMonto(kpis.gastos), Color(0xFFEF4444)),
                    Triple("Beneficio", formatearMonto(kpis.beneficio), Color(0xFF22C55E)),
                    Triple("Facturado", formatearMonto(kpis.facturado), EdecanColors.Azul),
                    Triple("Cobrado", formatearMonto(kpis.cobrado), Color(0xFF14B8A6)),
                    Triple("Nuevos clientes", kpis.nuevosClientes.toString(), EdecanColors.Morado),
                )
                Column(
                    modifier = Modifier.padding(top = 8.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    tarjetasKpi.chunked(2).forEach { fila ->
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.spacedBy(12.dp),
                        ) {
                            fila.forEach { (titulo, valor, color) ->
                                KpiCard(titulo, valor, color, modifier = Modifier.weight(1f))
                            }
                            if (fila.size == 1) Spacer(modifier = Modifier.weight(1f))
                        }
                    }
                }

                Card(modifier = Modifier.fillMaxWidth().padding(top = 20.dp)) {
                    Column(modifier = Modifier.padding(16.dp)) {
                        Text("Ventas por canal", style = MaterialTheme.typography.titleSmall)
                        DonutChart(kpis.porCanal, modifier = Modifier.padding(top = 12.dp))
                    }
                }

                if (kpis.actividad.isNotEmpty()) {
                    Text(
                        "Actividad reciente",
                        style = MaterialTheme.typography.titleSmall,
                        modifier = Modifier.padding(top = 20.dp, bottom = 8.dp),
                    )
                    Card(modifier = Modifier.fillMaxWidth()) {
                        Column {
                            kpis.actividad.forEach { evento -> FilaActividad(evento) }
                        }
                    }
                }
            }

            Text(
                "Facturas recientes",
                style = MaterialTheme.typography.titleSmall,
                modifier = Modifier.padding(top = 20.dp, bottom = 8.dp),
            )
            if (uiState.facturas.isEmpty()) {
                Text(
                    "Todavía no creaste ninguna factura.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            } else {
                Card(modifier = Modifier.fillMaxWidth()) {
                    Column {
                        uiState.facturas.take(15).forEach { factura -> FilaFactura(factura) }
                    }
                }
            }

            uiState.errorMensaje?.let { error ->
                Text(
                    error,
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(top = 16.dp),
                )
            }
        }
    }
}

@Composable
private fun KpiCard(titulo: String, valor: String, color: Color, modifier: Modifier = Modifier) {
    Card(modifier = modifier) {
        Column(modifier = Modifier.padding(14.dp)) {
            Text(titulo, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(
                valor,
                style = MaterialTheme.typography.titleLarge,
                color = color,
                modifier = Modifier.padding(top = 4.dp),
            )
        }
    }
}

@Composable
private fun FilaActividad(evento: ActividadItem) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Column(modifier = Modifier.padding(end = 8.dp)) {
            Text(evento.descripcion, style = MaterialTheme.typography.bodyMedium)
            Text(
                evento.fecha.take(10),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Text(
            "${if (evento.monto >= 0) "+" else ""}${formatearMonto(evento.monto)} ${evento.moneda}",
            style = MaterialTheme.typography.bodyMedium,
            color = if (evento.monto >= 0) Color(0xFF22C55E) else Color(0xFFEF4444),
        )
    }
}

@Composable
private fun FilaFactura(factura: Invoice) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Column(modifier = Modifier.padding(end = 8.dp)) {
            Text(factura.numero, style = MaterialTheme.typography.bodyMedium)
            Text(
                factura.clienteNombre,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Column(horizontalAlignment = Alignment.End) {
            Text("${formatearMonto(factura.total.montoDouble())} ${factura.moneda}", style = MaterialTheme.typography.bodyMedium)
            EstadoChip(factura.status)
        }
    }
}

@Composable
private fun EstadoChip(status: String) {
    val (etiqueta, color) = when (status) {
        "draft" -> "Borrador" to Color(0xFF94A3B8)
        "sent" -> "Enviada" to EdecanColors.Azul
        "paid" -> "Pagada" to Color(0xFF22C55E)
        "void" -> "Anulada" to Color(0xFFEF4444)
        else -> status to Color(0xFF94A3B8)
    }
    Box(
        modifier = Modifier
            .padding(top = 4.dp)
            .clip(RoundedCornerShape(8.dp))
            .background(color.copy(alpha = 0.15f))
            .padding(horizontal = 8.dp, vertical = 2.dp),
    ) {
        Text(etiqueta, style = MaterialTheme.typography.labelSmall, color = color)
    }
}

private fun formatearMonto(valor: Double): String = String.format(Locale.getDefault(), "%,.2f", valor)
