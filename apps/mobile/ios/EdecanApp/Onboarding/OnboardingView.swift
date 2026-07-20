import SwiftUI
import EdecanKit

/// Bienvenida de pasos cortos: 1) URL del servidor propio del tenant, 2)
/// iniciar sesión (o 2b) crear una cuenta nueva). Mismo principio de "pocos
/// clicks" que `DIRECCION_ACTUAL.md` pide para la app de escritorio,
/// aplicado aquí al emparejamiento móvil: nada de formularios largos, sin
/// servidor "de fábrica" — cada cliente trae el suyo (self-host o su app de
/// escritorio Tauri).
///
/// El token de sesión que deja `POST /v1/auth/login`/`POST /v1/auth/register`
/// ES el emparejamiento dispositivo/tenant en este v1 — ver el docstring de
/// `PairingStore` (EdecanKit) para el detalle y por qué es intencional.
/// Ambos caminos, además, intentan registrar este teléfono en `devices`
/// (`SessionStore.emparejarDispositivo`, WP-V4-01 en paralelo) — best-effort,
/// nunca bloquea si ese endpoint todavía no existe en el servidor.
struct OnboardingView: View {
    private enum Paso {
        case servidor
        case sesion
        case registro
    }

    @Environment(PairingStore.self) private var pairingStore
    @Environment(SessionStore.self) private var session

    @State private var paso: Paso = .servidor
    @State private var urlTexto = ""
    @State private var email = ""
    @State private var password = ""
    @State private var totp = ""
    @State private var nombreEmpresa = ""
    @State private var cargando = false
    @State private var errorMensaje: String?
    @FocusState private var campoEnfocado: Bool

    var body: some View {
        ZStack {
            EdecanTheme.degradado.opacity(0.15).ignoresSafeArea()
            ScrollView {
                VStack(spacing: 28) {
                    encabezado
                    Group {
                        switch paso {
                        case .servidor: pasoServidor
                        case .sesion: pasoSesion
                        case .registro: pasoRegistro
                        }
                    }
                    .tarjetaVidrio(esquina: 24)
                }
                .padding(.horizontal, 24)
                .padding(.top, 60)
            }
        }
        .onAppear {
            if let url = pairingStore.serverURL {
                urlTexto = url.absoluteString
                paso = .sesion
            }
        }
    }

    private var encabezado: some View {
        VStack(spacing: 8) {
            Image(systemName: "sparkles")
                .font(.system(size: 40))
                .foregroundStyle(EdecanTheme.degradado)
            Text("Edecán")
                .font(.largeTitle.weight(.bold))
            Text(tituloDelPaso)
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
    }

    private var tituloDelPaso: String {
        switch paso {
        case .servidor: return "¿Dónde vive tu servidor?"
        case .sesion: return "Inicia sesión"
        case .registro: return "Crea tu cuenta"
        }
    }

    private var pasoServidor: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Pega la URL de TU instalación de Edecán — self-host o la app de escritorio de tu tenant. No hay un servidor por defecto: cada cliente trae el suyo.")
                .font(.footnote)
                .foregroundStyle(.secondary)
            TextField("https://mi-servidor.ejemplo.com", text: $urlTexto)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .keyboardType(.URL)
                .textFieldStyle(.roundedBorder)
                .focused($campoEnfocado)
            if let errorMensaje {
                Text(errorMensaje).font(.footnote).foregroundStyle(.red)
            }
            Button("Continuar", action: continuarConServidor)
                .buttonStyle(.borderedProminent)
                .tint(EdecanTheme.morado)
                .disabled(urlTexto.trimmingCharacters(in: .whitespaces).isEmpty)
                .frame(maxWidth: .infinity)
        }
        .padding(20)
    }

    private var pasoSesion: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Iniciar sesión empareja este teléfono con tu cuenta: mientras la sesión siga activa, este dispositivo queda emparejado con tu tenant.")
                .font(.footnote)
                .foregroundStyle(.secondary)
            TextField("Correo", text: $email)
                .textInputAutocapitalization(.never)
                .keyboardType(.emailAddress)
                .autocorrectionDisabled()
                .textFieldStyle(.roundedBorder)
            SecureField("Contraseña", text: $password)
                .textFieldStyle(.roundedBorder)
            TextField("Código de 6 dígitos (solo si tienes 2FA activado)", text: $totp)
                .keyboardType(.numberPad)
                .textFieldStyle(.roundedBorder)
            if let errorMensaje {
                Text(errorMensaje).font(.footnote).foregroundStyle(.red)
            }
            HStack {
                Button("Cambiar servidor") { paso = .servidor }
                    .buttonStyle(.bordered)
                Spacer()
                Button {
                    Task { await iniciarSesion() }
                } label: {
                    if cargando {
                        ProgressView().tint(.white)
                    } else {
                        Text("Entrar").fontWeight(.semibold)
                    }
                }
                .buttonStyle(.borderedProminent)
                .tint(EdecanTheme.morado)
                .disabled(cargando || email.isEmpty || password.isEmpty)
            }
            Button("¿No tienes cuenta? Crear una") {
                errorMensaje = nil
                paso = .registro
            }
            .font(.footnote)
            .frame(maxWidth: .infinity)
        }
        .padding(20)
    }

    private var pasoRegistro: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Crea el tenant de tu equipo — quedas como owner, con una persona de Edecán ya lista para chatear.")
                .font(.footnote)
                .foregroundStyle(.secondary)
            TextField("Nombre de tu empresa/equipo", text: $nombreEmpresa)
                .textFieldStyle(.roundedBorder)
            TextField("Correo", text: $email)
                .textInputAutocapitalization(.never)
                .keyboardType(.emailAddress)
                .autocorrectionDisabled()
                .textFieldStyle(.roundedBorder)
            SecureField("Contraseña (mínimo 8 caracteres)", text: $password)
                .textFieldStyle(.roundedBorder)
            if let errorMensaje {
                Text(errorMensaje).font(.footnote).foregroundStyle(.red)
            }
            HStack {
                Button("Volver a iniciar sesión") {
                    errorMensaje = nil
                    paso = .sesion
                }
                .buttonStyle(.bordered)
                Spacer()
                Button {
                    Task { await crearCuenta() }
                } label: {
                    if cargando {
                        ProgressView().tint(.white)
                    } else {
                        Text("Crear cuenta").fontWeight(.semibold)
                    }
                }
                .buttonStyle(.borderedProminent)
                .tint(EdecanTheme.morado)
                .disabled(cargando || !formularioDeRegistroValido)
            }
        }
        .padding(20)
    }

    private var formularioDeRegistroValido: Bool {
        !nombreEmpresa.trimmingCharacters(in: .whitespaces).isEmpty
            && !email.trimmingCharacters(in: .whitespaces).isEmpty
            && password.count >= 8
    }

    private func continuarConServidor() {
        errorMensaje = nil
        let texto = urlTexto.trimmingCharacters(in: .whitespaces)
        guard let url = URL(string: texto), let esquema = url.scheme, esquema.hasPrefix("http") else {
            errorMensaje = "Escribe una URL completa, con http:// o https:// al inicio."
            return
        }
        pairingStore.guardarServidor(url)
        session.actualizarBaseURL(url)
        paso = .sesion
    }

    private func iniciarSesion() async {
        guard let client = session.client else {
            errorMensaje = "Primero define la URL del servidor."
            paso = .servidor
            return
        }
        cargando = true
        errorMensaje = nil
        do {
            try await client.login(email: email, password: password, totpCode: totp)
            await completarEmparejamiento()
        } catch {
            errorMensaje = error.localizedDescription
        }
        cargando = false
    }

    private func crearCuenta() async {
        guard let client = session.client else {
            errorMensaje = "Primero define la URL del servidor."
            paso = .servidor
            return
        }
        cargando = true
        errorMensaje = nil
        do {
            try await client.registrar(
                email: email, password: password, tenantName: nombreEmpresa
            )
            await completarEmparejamiento()
        } catch {
            errorMensaje = error.localizedDescription
        }
        cargando = false
    }

    /// Común a login y registro: marca el dispositivo emparejado (v1: sesión
    /// = emparejamiento), carga `/v1/me`, e intenta además el registro real
    /// por dispositivo (`POST /v1/devices`, best-effort — ver
    /// `SessionStore.emparejarDispositivo`).
    private func completarEmparejamiento() async {
        session.marcarSesionValida()
        pairingStore.marcarEmparejado()
        await session.cargarMe()
        await session.emparejarDispositivo(pairingStore: pairingStore)
    }
}
