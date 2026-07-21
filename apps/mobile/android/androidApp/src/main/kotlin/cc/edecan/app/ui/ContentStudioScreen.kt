@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package cc.edecan.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.Checkbox
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

/** Convierte unas pocas decisiones humanas en una solicitud completa al chat. */
@Composable
fun ContentStudioScreen(onCrear: (String) -> Unit) {
    var plataforma by remember { mutableStateOf("LinkedIn") }
    var objetivo by remember { mutableStateOf("Enseñar algo útil") }
    var tono by remember { mutableStateOf("Claro y humano") }
    var tema by remember { mutableStateOf("") }
    var incluirImagen by remember { mutableStateOf(true) }

    Scaffold(topBar = { TopAppBar(title = { Text("Crear") }) }) { padding ->
        Column(
            verticalArrangement = Arrangement.spacedBy(14.dp),
            modifier = Modifier.padding(padding).fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        ) {
            Text("De una idea a una publicación completa")
            Text("Edecan prepara el copy, la imagen, el texto alternativo y los archivos para compartir.")
            OutlinedTextField(
                value = tema,
                onValueChange = { tema = it },
                label = { Text("¿Sobre qué quieres publicar?") },
                minLines = 3,
                modifier = Modifier.fillMaxWidth(),
            )
            SimpleMenu("Plataforma", plataforma, listOf("LinkedIn", "X", "Instagram", "Facebook", "Threads", "TikTok")) { plataforma = it }
            SimpleMenu("Objetivo", objetivo, listOf("Enseñar algo útil", "Contar una historia", "Lanzar un producto", "Generar conversación")) { objetivo = it }
            SimpleMenu("Tono", tono, listOf("Claro y humano", "Profesional", "Directo", "Inspirador", "Divertido")) { tono = it }
            Card(modifier = Modifier.fillMaxWidth()) {
                androidx.compose.foundation.layout.Row(modifier = Modifier.padding(12.dp)) {
                    Checkbox(checked = incluirImagen, onCheckedChange = { incluirImagen = it })
                    Text("Crear también una imagen original", modifier = Modifier.padding(top = 12.dp))
                }
            }
            Button(
                enabled = tema.isNotBlank(),
                onClick = {
                    val imagen = if (incluirImagen) {
                        "Incluye una imagen original y su texto alternativo."
                    } else {
                        "No generes imagen."
                    }
                    onCrear(
                        "Crea un paquete de contenido para $plataforma sobre: $tema. " +
                            "Objetivo: $objetivo. Tono: $tono. $imagen " +
                            "Usa la herramienta crear_contenido_social y entrégame todos los archivos aquí. " +
                            "No publiques nada sin mi confirmación."
                    )
                    tema = ""
                },
                modifier = Modifier.fillMaxWidth(),
            ) { Text("Crear en el chat") }
        }
    }
}

@Composable
private fun SimpleMenu(
    label: String,
    value: String,
    options: List<String>,
    onChange: (String) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    Card(modifier = Modifier.fillMaxWidth()) {
        TextButton(onClick = { expanded = true }, modifier = Modifier.fillMaxWidth()) {
            Text("$label: $value")
        }
        DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            options.forEach { option ->
                DropdownMenuItem(
                    text = { Text(option) },
                    onClick = { onChange(option); expanded = false },
                )
            }
        }
    }
}
