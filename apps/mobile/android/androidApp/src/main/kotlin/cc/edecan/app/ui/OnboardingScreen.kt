package cc.edecan.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import cc.edecan.app.ui.theme.EdecanColors
import cc.edecan.app.vm.ModoSesion
import cc.edecan.app.vm.PasoOnboarding
import cc.edecan.app.vm.SessionViewModel

/**
 * Bienvenida de 2 pasos: 1) URL del servidor propio del tenant, 2) iniciar
 * sesión. Mismo principio de "pocos clicks" que `DIRECCION_ACTUAL.md` pide
 * para la app de escritorio, aplicado aquí al emparejamiento móvil: nada de
 * formularios largos, sin servidor "de fábrica" — cada cliente trae el
 * suyo (self-host o su app de escritorio Tauri). Mismo contenido que
 * `OnboardingView.swift` (iOS); el token de sesión que deja
 * `POST /v1/auth/login` ES el emparejamiento dispositivo/tenant en este
 * v1 — ver el docstring de `TokenStore` (`shared`) para el detalle.
 */
@Composable
fun OnboardingScreen(sessionViewModel: SessionViewModel = viewModel()) {
    val uiState by sessionViewModel.uiState.collectAsState()
    var urlTexto by remember(uiState.serverUrl) { mutableStateOf(uiState.serverUrl.orEmpty()) }
    var email by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var totp by remember { mutableStateOf("") }
    var tenantName by remember { mutableStateOf("") }
    var errorLocal by remember { mutableStateOf<String?>(null) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(brush = EdecanColors.Degradado, alpha = 0.12f) // mismo criterio que
            // `EdecanTheme.degradado.opacity(0.15)` de fondo en `OnboardingView.swift` (iOS).
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 24.dp, vertical = 60.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Encabezado(paso = uiState.paso)
        Spacer(modifier = Modifier.height(28.dp))

        when (uiState.paso) {
            PasoOnboarding.SERVIDOR -> PasoServidor(
                urlTexto = urlTexto,
                onUrlCambia = { urlTexto = it; errorLocal = null },
                error = errorLocal,
                onContinuar = {
                    val texto = urlTexto.trim()
                    if (!texto.startsWith("http://") && !texto.startsWith("https://")) {
                        errorLocal = "Escribe una URL completa, con http:// o https:// al inicio."
                        return@PasoServidor
                    }
                    sessionViewModel.definirServidor(texto)
                },
            )

            PasoOnboarding.SESION -> PasoSesion(
                modo = uiState.modoSesion,
                onCambiarModo = sessionViewModel::cambiarModoSesion,
                email = email,
                onEmailCambia = { email = it },
                password = password,
                onPasswordCambia = { password = it },
                totp = totp,
                onTotpCambia = { totp = it },
                tenantName = tenantName,
                onTenantNameCambia = { tenantName = it },
                cargando = uiState.iniciandoSesion,
                error = uiState.errorMensaje,
                onCambiarServidor = sessionViewModel::irAPasoServidor,
                onEntrar = {
                    if (uiState.modoSesion == ModoSesion.REGISTRAR) {
                        sessionViewModel.registrar(email, password, tenantName)
                    } else {
                        sessionViewModel.iniciarSesion(email, password, totp)
                    }
                },
            )
        }
    }
}

@Composable
private fun Encabezado(paso: PasoOnboarding) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text("✨", style = MaterialTheme.typography.displayMedium)
        Text("Edecán", style = MaterialTheme.typography.headlineLarge)
        Text(
            if (paso == PasoOnboarding.SERVIDOR) "Conecta tu Edecán" else "Inicia sesión",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun TarjetaOnboarding(content: @Composable ColumnScope.() -> Unit) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(24.dp))
            .background(MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f))
            .padding(20.dp),
        content = content,
    )
}

@Composable
private fun PasoServidor(
    urlTexto: String,
    onUrlCambia: (String) -> Unit,
    error: String?,
    onContinuar: () -> Unit,
) {
    TarjetaOnboarding {
        Text(
            "Abre Edecán en tu computador y copia la dirección que aparece en " +
                "Ajustes > Conectar teléfono. Solo tendrás que hacerlo una vez.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(modifier = Modifier.height(12.dp))
        OutlinedTextField(
            value = urlTexto,
            onValueChange = onUrlCambia,
            placeholder = { Text("https://mi-servidor.ejemplo.com") },
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
            modifier = Modifier.fillMaxWidth(),
        )
        if (error != null) {
            Spacer(modifier = Modifier.height(8.dp))
            Text(error, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
        }
        Spacer(modifier = Modifier.height(16.dp))
        Button(
            onClick = onContinuar,
            enabled = urlTexto.isNotBlank(),
            colors = ButtonDefaults.buttonColors(containerColor = EdecanColors.Morado),
            modifier = Modifier.fillMaxWidth(),
        ) { Text("Continuar") }
    }
}

@Composable
private fun PasoSesion(
    modo: ModoSesion,
    onCambiarModo: (ModoSesion) -> Unit,
    email: String,
    onEmailCambia: (String) -> Unit,
    password: String,
    onPasswordCambia: (String) -> Unit,
    totp: String,
    onTotpCambia: (String) -> Unit,
    tenantName: String,
    onTenantNameCambia: (String) -> Unit,
    cargando: Boolean,
    error: String?,
    onCambiarServidor: () -> Unit,
    onEntrar: () -> Unit,
) {
    val esRegistro = modo == ModoSesion.REGISTRAR

    TarjetaOnboarding {
        Text(
            if (esRegistro) {
                "Crea tu espacio. Edecán quedará listo para conversar, organizar y trabajar contigo."
            } else {
                "Entra con la misma cuenta que usas en tu computador. Tus chats y tareas " +
                    "aparecerán aquí."
            },
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(modifier = Modifier.height(12.dp))
        OutlinedTextField(
            value = email,
            onValueChange = onEmailCambia,
            placeholder = { Text("Correo") },
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email),
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(modifier = Modifier.height(8.dp))
        OutlinedTextField(
            value = password,
            onValueChange = onPasswordCambia,
            placeholder = { Text(if (esRegistro) "Contraseña (mínimo 8 caracteres)" else "Contraseña") },
            singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(modifier = Modifier.height(8.dp))
        if (esRegistro) {
            OutlinedTextField(
                value = tenantName,
                onValueChange = onTenantNameCambia,
                placeholder = { Text("Nombre de tu negocio o equipo") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
        } else {
            OutlinedTextField(
                value = totp,
                onValueChange = onTotpCambia,
                placeholder = { Text("Código de 6 dígitos (solo si tienes 2FA activado)") },
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
                modifier = Modifier.fillMaxWidth(),
            )
        }
        if (error != null) {
            Spacer(modifier = Modifier.height(8.dp))
            Text(error, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
        }
        Spacer(modifier = Modifier.height(16.dp))
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
            OutlinedButton(onClick = onCambiarServidor) { Text("Cambiar conexión") }
            Spacer(modifier = Modifier.weight(1f))
            Button(
                onClick = onEntrar,
                enabled = !cargando && email.isNotBlank() && password.isNotBlank() &&
                    (!esRegistro || tenantName.isNotBlank()),
                colors = ButtonDefaults.buttonColors(containerColor = EdecanColors.Morado),
            ) {
                if (cargando) {
                    CircularProgressIndicator(modifier = Modifier.height(18.dp), color = Color.White, strokeWidth = 2.dp)
                } else {
                    Text(if (esRegistro) "Crear cuenta" else "Entrar")
                }
            }
        }
        Spacer(modifier = Modifier.height(8.dp))
        TextButton(
            onClick = { onCambiarModo(if (esRegistro) ModoSesion.INICIAR else ModoSesion.REGISTRAR) },
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text(if (esRegistro) "¿Ya tienes cuenta? Inicia sesión" else "¿No tienes cuenta? Regístrate")
        }
    }
}
