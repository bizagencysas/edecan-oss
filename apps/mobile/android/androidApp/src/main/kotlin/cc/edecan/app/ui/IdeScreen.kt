@file:OptIn(ExperimentalMaterial3Api::class)

package cc.edecan.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.IconButton
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
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import cc.edecan.app.ui.components.EmptyState
import cc.edecan.app.vm.IdeEntrada
import cc.edecan.app.vm.IdeViewModel
import cc.edecan.app.vm.SessionViewModel

/**
 * Pestaña "IDE" — solo lectura: árbol del sandbox del companion de
 * escritorio emparejado (`GET /v1/ide/tree`) y contenido de un archivo
 * (`GET /v1/ide/file`), `ARCHITECTURE.md` §11, `docs/ide.md`. Editar,
 * correr comandos o buscar (resto de las rutas de `/v1/ide`) queda para una
 * siguiente iteración — ver `docs/movil-android.md`. Lógica real en [IdeViewModel];
 * esta pantalla solo dibuja su estado.
 */
@Composable
fun IdeScreen(
    sessionViewModel: SessionViewModel = viewModel(),
    ideViewModel: IdeViewModel = viewModel(),
) {
    val uiState by ideViewModel.uiState.collectAsState()
    val api = sessionViewModel.api

    LaunchedEffect(api) { api?.let { ideViewModel.cargar(it) } }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(uiState.archivoRuta ?: "IDE") },
                navigationIcon = {
                    if (uiState.archivoRuta != null) {
                        IconButton(onClick = ideViewModel::cerrarArchivo) { Text("←") }
                    }
                },
            )
        },
    ) { padding ->
        Box(modifier = Modifier.padding(padding).fillMaxSize()) {
            when {
                uiState.cargando && uiState.conectado == null -> CircularProgressIndicator(
                    modifier = Modifier.padding(32.dp),
                )
                uiState.conectado == false -> EmptyState(
                    emoji = "💻",
                    titulo = "Sin companion conectado",
                    descripcion = "Empareja tu companion de escritorio desde el panel web " +
                        "(`POST /v1/companion/pair-code`) para poder ver tu proyecto acá.",
                    etiquetaRoadmap = null,
                )
                uiState.archivoRuta != null -> VisorArchivo(
                    contenido = uiState.archivoContenido,
                    cargando = uiState.cargandoArchivo,
                )
                else -> ArbolDeArchivos(
                    entradas = uiState.entradas,
                    truncado = uiState.truncado,
                    onAbrirArchivo = { ruta -> api?.let { ideViewModel.abrirArchivo(ruta, it) } },
                )
            }

            uiState.errorMensaje?.let { error ->
                Text(
                    error,
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.align(Alignment.BottomCenter).padding(16.dp),
                )
            }
        }
    }
}

@Composable
private fun ArbolDeArchivos(
    entradas: List<IdeEntrada>,
    truncado: Boolean,
    onAbrirArchivo: (String) -> Unit,
) {
    if (entradas.isEmpty()) {
        EmptyState(
            emoji = "📁",
            titulo = "Sandbox vacío",
            descripcion = "Tu companion todavía no tiene archivos en su carpeta compartida.",
            etiquetaRoadmap = null,
        )
        return
    }
    Column(modifier = Modifier.fillMaxSize()) {
        if (truncado) {
            Text(
                "Árbol truncado: hay más archivos de los que se muestran.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(12.dp),
            )
        }
        LazyColumn(modifier = Modifier.fillMaxSize()) {
            items(entradas, key = { it.ruta }) { entrada -> FilaArbol(entrada, onAbrirArchivo) }
        }
    }
}

@Composable
private fun FilaArbol(entrada: IdeEntrada, onAbrirArchivo: (String) -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(enabled = !entrada.esDirectorio) { onAbrirArchivo(entrada.ruta) }
            .padding(start = 16.dp + (entrada.profundidad * 16).dp, top = 10.dp, bottom = 10.dp, end = 16.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(if (entrada.esDirectorio) "📁" else "📄", modifier = Modifier.width(28.dp))
        Text(entrada.nombre, style = MaterialTheme.typography.bodyMedium)
    }
}

@Composable
private fun VisorArchivo(contenido: String?, cargando: Boolean) {
    if (cargando || contenido == null) {
        Box(modifier = Modifier.fillMaxSize()) {
            CircularProgressIndicator(modifier = Modifier.align(Alignment.Center))
        }
        return
    }
    Column(modifier = Modifier.fillMaxSize()) {
        HorizontalDivider()
        Box(
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .horizontalScroll(rememberScrollState())
                .padding(16.dp),
        ) {
            Text(contenido, fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodySmall)
        }
    }
}
