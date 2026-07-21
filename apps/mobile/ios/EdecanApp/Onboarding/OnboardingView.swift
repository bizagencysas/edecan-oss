import EdecanKit
import SwiftUI
import UIKit

/// Emparejamiento QR primero. URL/usuario/contraseña se conserva como ruta
/// avanzada para self-hosts y recuperación manual, sin competir con el flujo
/// normal de un solo escaneo.
struct OnboardingView: View {
    private enum Paso {
        case qr
        case servidor
        case sesion
        case registro
    }

    @Environment(PairingStore.self) private var pairingStore
    @Environment(SessionStore.self) private var session

    @State private var paso: Paso = .qr
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
                        case .qr: pasoQR
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
            if let url = pairingStore.serverURL { urlTexto = url.absoluteString }
            intentarProcesarEnlacePendiente()
        }
        .onChange(of: pairingStore.pendingPairingLink?.id) { _, _ in
            intentarProcesarEnlacePendiente()
        }
        .onChange(of: pairingStore.pairingLinkError) { _, error in
            guard let error else { return }
            paso = .qr
            errorMensaje = error
        }
    }

    private var encabezado: some View {
        VStack(spacing: 8) {
            Image(systemName: paso == .qr ? "qrcode.viewfinder" : "sparkles")
                .font(.system(size: 42))
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
        case .qr: "Conecta este iPhone"
        case .servidor: "Configuración avanzada"
        case .sesion: "Inicia sesión"
        case .registro: "Crea tu cuenta"
        }
    }

    private var pasoQR: some View {
        VStack(spacing: 18) {
            Image(systemName: "iphone.gen3.radiowaves.left.and.right")
                .font(.system(size: 44))
                .foregroundStyle(EdecanTheme.morado)
            Text("Escanea el QR de tu Edecan")
                .font(.title3.weight(.bold))
            Text("En tu computador abre Ajustes → Conectar teléfono y escanea el código con la Cámara del iPhone. Edecan se abrirá y quedará conectado automáticamente.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            if cargando {
                HStack(spacing: 10) {
                    ProgressView()
                    Text("Conectando de forma segura…")
                }
                .font(.subheadline.weight(.medium))
            }
            if let errorMensaje {
                Text(errorMensaje)
                    .font(.footnote)
                    .foregroundStyle(.red)
                    .multilineTextAlignment(.center)
            }
            Button("Configurar manualmente") {
                errorMensaje = nil
                pairingStore.limpiarErrorDeEnlace()
                paso = pairingStore.serverURL == nil ? .servidor : .sesion
            }
            .buttonStyle(.bordered)
            .disabled(cargando)
        }
        .frame(maxWidth: .infinity)
        .padding(22)
    }

    private var pasoServidor: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Usa este camino solo si no puedes generar el QR. Escribe la dirección de tu servidor y luego inicia sesión.")
                .font(.footnote)
                .foregroundStyle(.secondary)
            TextField("https://mi-servidor.ejemplo.com", text: $urlTexto)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .keyboardType(.URL)
                .textFieldStyle(.roundedBorder)
                .focused($campoEnfocado)
            mensajeDeError
            HStack {
                Button("Volver al QR") { paso = .qr }
                    .buttonStyle(.bordered)
                Spacer()
                Button("Continuar", action: continuarConServidor)
                    .buttonStyle(.borderedProminent)
                    .tint(EdecanTheme.morado)
                    .disabled(urlTexto.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .padding(20)
    }

    private var pasoSesion: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Entra con la misma cuenta que usas en tu computador.")
                .font(.footnote)
                .foregroundStyle(.secondary)
            TextField("Correo", text: $email)
                .textInputAutocapitalization(.never)
                .keyboardType(.emailAddress)
                .autocorrectionDisabled()
                .textFieldStyle(.roundedBorder)
            SecureField("Contraseña", text: $password)
                .textFieldStyle(.roundedBorder)
            TextField("Código de 6 dígitos (solo si usas 2FA)", text: $totp)
                .keyboardType(.numberPad)
                .textFieldStyle(.roundedBorder)
            mensajeDeError
            HStack {
                Button("Cambiar conexión") { paso = .servidor }
                    .buttonStyle(.bordered)
                Spacer()
                Button {
                    Task { await iniciarSesion() }
                } label: {
                    if cargando { ProgressView().tint(.white) }
                    else { Text("Entrar").fontWeight(.semibold) }
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
            Text("Crea tu espacio. Luego podrás generar el QR para tus otros dispositivos.")
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
            mensajeDeError
            HStack {
                Button("Volver") { errorMensaje = nil; paso = .sesion }
                    .buttonStyle(.bordered)
                Spacer()
                Button {
                    Task { await crearCuenta() }
                } label: {
                    if cargando { ProgressView().tint(.white) }
                    else { Text("Crear cuenta").fontWeight(.semibold) }
                }
                .buttonStyle(.borderedProminent)
                .tint(EdecanTheme.morado)
                .disabled(cargando || !formularioDeRegistroValido)
            }
        }
        .padding(20)
    }

    @ViewBuilder
    private var mensajeDeError: some View {
        if let errorMensaje { Text(errorMensaje).font(.footnote).foregroundStyle(.red) }
    }

    private var formularioDeRegistroValido: Bool {
        !nombreEmpresa.trimmingCharacters(in: .whitespaces).isEmpty
            && !email.trimmingCharacters(in: .whitespaces).isEmpty
            && password.count >= 8
    }

    private func intentarProcesarEnlacePendiente() {
        guard !cargando, let link = pairingStore.consumirEnlacePendiente() else { return }
        paso = .qr
        Task { await reclamar(link) }
    }

    private func reclamar(_ link: PairingLink) async {
        cargando = true
        errorMensaje = nil
        session.actualizarBaseURL(link.serverURL)
        do {
            guard let client = session.client else { throw APIClient.APIError.urlInvalida }
            let device = UIDevice.current
            let claim = try await client.reclamarEmparejamiento(
                pairingToken: link.token,
                nombre: device.name,
                fingerprint: device.identifierForVendor?.uuidString
            )
            session.marcarSesionValida()
            try pairingStore.completarEmparejamientoQR(
                serverURL: link.serverURL,
                deviceId: claim.deviceId,
                deviceToken: claim.deviceToken
            )
            _ = await session.cargarMe()
        } catch {
            errorMensaje = error.localizedDescription
        }
        cargando = false
        intentarProcesarEnlacePendiente()
    }

    private func continuarConServidor() {
        errorMensaje = nil
        let texto = urlTexto.trimmingCharacters(in: .whitespaces)
        do {
            let url = try ServerURLPolicy.parseAndValidate(texto)
            try pairingStore.guardarServidor(url)
            session.actualizarBaseURL(url)
            paso = .sesion
        } catch {
            errorMensaje = error.localizedDescription
        }
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
            await completarEmparejamientoManual()
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
            try await client.registrar(email: email, password: password, tenantName: nombreEmpresa)
            await completarEmparejamientoManual()
        } catch {
            errorMensaje = error.localizedDescription
        }
        cargando = false
    }

    private func completarEmparejamientoManual() async {
        session.marcarSesionValida()
        pairingStore.marcarEmparejado()
        await session.cargarMe()
        await session.emparejarDispositivo(pairingStore: pairingStore)
    }
}
