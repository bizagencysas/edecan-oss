import EdecanKit
import SwiftUI
import UIKit
import VisionKit

/// Conexión por CORREO + CLAVE primero (nivel ChatGPT/Grok): el dueño escribe
/// la dirección de su servidor —VPS en la nube o Mac en la red local—, la app
/// le muestra SIEMPRE a cuál se está conectando (insignia viva Nube/Mac) e
/// inicia sesión con su cuenta. Todo el arte es Liquid Glass real de iOS 26
/// (`glassEffect` + morphing entre pasos vía `GlassEffectContainer`). El QR
/// queda como ruta secundaria. La lógica (login, registro, emparejamiento
/// durable, claim por QR) es exactamente la misma de siempre — solo cambió
/// la capa visual.
struct OnboardingView: View {
    private enum Paso {
        case servidor
        case sesion
        case registro
        case qr
    }

    private enum LoginCampo: Hashable {
        case servidor
        case email
        case password
        case totp
        case empresa
    }

    @Environment(PairingStore.self) private var pairingStore
    @Environment(SessionStore.self) private var session
    @Environment(\.colorScheme) private var esquema

    @State private var paso: Paso = .servidor
    @State private var urlTexto = ""
    @State private var email = ""
    @State private var password = ""
    @State private var totp = ""
    @State private var nombreEmpresa = ""
    @State private var cargando = false
    @State private var errorMensaje: String?
    @State private var mostrandoEscaner = false
    @State private var mostrarPassword = false
    @State private var usar2FA = false
    @FocusState private var enfocado: LoginCampo?

    var body: some View {
        ZStack {
            FondoLogin()
            ScrollView {
                VStack(spacing: 24) {
                    encabezado
                    Group {
                        switch paso {
                        case .servidor: pasoServidor
                        case .sesion: pasoSesion
                        case .registro: pasoRegistro
                        case .qr: pasoQR
                        }
                    }
                    .transition(.opacity.combined(with: .scale(scale: 0.97)))
                }
                .padding(.horizontal, 24)
                .padding(.top, 48)
                .padding(.bottom, 32)
            }
            .scrollDismissesKeyboard(.interactively)
        }
        .animation(.snappy(duration: 0.3), value: paso)
        .onAppear {
            if let url = pairingStore.serverURL {
                urlTexto = url.absoluteString
                paso = .sesion
            }
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
        .onChange(of: paso) { _, nuevo in
            usar2FA = false
            totp = ""
            mostrarPassword = false
            switch nuevo {
            case .servidor:
                enfocado = .servidor
            case .sesion:
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) {
                    guard paso == .sesion else { return }
                    enfocado = .email
                }
            case .registro:
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) {
                    guard paso == .registro else { return }
                    enfocado = .empresa
                }
            case .qr:
                enfocado = nil
            }
        }
        .sheet(isPresented: $mostrandoEscaner) {
            NavigationStack {
                QRScannerView(
                    onScan: recibirCodigoEscaneado,
                    onError: mostrarErrorDelEscaner
                )
                .ignoresSafeArea(edges: .bottom)
                .overlay(alignment: .bottom) {
                    Text("Apunta al QR que muestra Edecán en tu computador")
                        .font(.subheadline.weight(.semibold))
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, 20)
                        .padding(.vertical, 12)
                        .background(.ultraThinMaterial, in: Capsule())
                        .padding(.bottom, 24)
                }
                .navigationTitle("Escanear QR")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Cancelar") { mostrandoEscaner = false }
                    }
                }
            }
        }
    }

    // MARK: - Fondo

    /// Fondo airy de Bots + manchas de color para dar profundidad al vidrio.
    private struct FondoLogin: View {
        var body: some View {
            GeometryReader { geo in
                ZStack {
                    EdecanTheme.fondoBotsLight
                    Circle()
                        .fill(EdecanTheme.morado.opacity(0.16))
                        .frame(width: 340)
                        .blur(radius: 72)
                        .offset(x: -110, y: -90)
                    Circle()
                        .fill(EdecanTheme.azul.opacity(0.15))
                        .frame(width: 300)
                        .blur(radius: 72)
                        .offset(x: geo.size.width - 190, y: geo.size.height - 330)
                }
            }
            .ignoresSafeArea()
        }
    }

    // MARK: - Hero

    private var encabezado: some View {
        VStack(spacing: 10) {
            Image(systemName: "sparkles")
                .font(.system(size: 30, weight: .medium))
                .foregroundStyle(EdecanTheme.degradado)
                .frame(width: 72, height: 72)
                .capsulaVidrio()
            Text("Edecán")
                .font(.largeTitle.weight(.bold))
            Text(subtituloDelPaso)
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
        .padding(.top, 12)
    }

    private var subtituloDelPaso: String {
        switch paso {
        case .servidor:
            "Conecta este iPhone con tu cuenta"
        case .sesion:
            "Inicia sesión con tu correo y clave"
        case .registro:
            "Crea tu espacio en un minuto"
        case .qr:
            "Escanea el código de tu computador"
        }
    }

    // MARK: - Paso 1: Servidor (Mac o VPS)

    private var pasoServidor: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Dirección de tu servidor")
                .font(.footnote.weight(.semibold))
                .foregroundStyle(.secondary)
            campo(
                icono: "server.rack",
                etiqueta: "Dirección del servidor",
                texto: $urlTexto,
                teclado: .URL,
                contentType: .URL,
                submit: .go,
                onEnviar: { continuarConServidor() }
            )
            .focused($enfocado, equals: .servidor)
            if let clasificacion = clasificarServidor(urlTexto) {
                insigniaServidor(clasificacion)
                    .transition(.opacity)
            }
            HStack(spacing: 10) {
                chipServidor("Mi VPS", icono: "cloud.fill", url: "https://edecan.example.com")
                chipServidor("Ejemplo Mac", icono: "laptopcomputer.and.iphone", url: "http://192.168.1.10:8765")
            }
            Text("Tu VPS vive 24/7 en la nube. Tu Mac solo está disponible cuando está encendida.")
                .font(.caption2)
                .foregroundStyle(.tertiary)
            mensajeDeError
            botonPrincipal("Continuar", habilitado: !urlTexto.trimmingCharacters(in: .whitespaces).isEmpty) {
                continuarConServidor()
            }
            botonSecundario("Prefiero escanear un QR") {
                errorMensaje = nil
                pairingStore.limpiarErrorDeEnlace()
                paso = .qr
            }
        }
        .padding(22)
        .tarjetaVidrio(esquina: 24, flotante: true)
    }

    // MARK: - Paso 2: Sesión (correo + clave)

    private var pasoSesion: some View {
        VStack(alignment: .leading, spacing: 16) {
            if let host = hostDelServidor() {
                HStack(spacing: 8) {
                    insigniaServidor(clasificarServidor(host) ?? .nube(host))
                    Spacer()
                    Button("Cambiar") {
                        errorMensaje = nil
                        paso = .servidor
                    }
                    .font(.footnote.weight(.medium))
                    .tint(EdecanTheme.morado)
                }
            }
            campo(
                icono: "envelope",
                etiqueta: "Correo",
                texto: $email,
                teclado: .emailAddress,
                contentType: .emailAddress,
                submit: .next
            )
            .focused($enfocado, equals: .email)
            campo(
                icono: "lock",
                etiqueta: "Contraseña",
                texto: $password,
                seguro: !mostrarPassword,
                contentType: .password,
                submit: password.isEmpty ? .next : .go,
                onEnviar: { Task { await iniciarSesion() } },
                trailing: {
                    Button {
                        mostrarPassword.toggle()
                    } label: {
                        Image(systemName: mostrarPassword ? "eye.slash" : "eye")
                            .font(.system(size: 14, weight: .medium))
                            .foregroundStyle(.secondary)
                    }
                    .buttonStyle(.plain)
                }
            )
            .focused($enfocado, equals: .password)
            if usar2FA {
                campo(
                    icono: "shield.lefthalf.filled",
                    etiqueta: "Código de 6 dígitos",
                    texto: $totp,
                    teclado: .numberPad,
                    contentType: .oneTimeCode,
                    submit: .go,
                    onEnviar: { Task { await iniciarSesion() } }
                )
                .focused($enfocado, equals: .totp)
                .transition(.opacity.combined(with: .move(edge: .top)))
            } else {
                Button("Tengo código de 2FA") {
                    withAnimation(.snappy(duration: 0.25)) { usar2FA = true }
                }
                .font(.footnote)
                .tint(EdecanTheme.morado)
            }
            mensajeDeError
            botonPrincipal("Entrar", habilitado: !email.isEmpty && !password.isEmpty) {
                Task { await iniciarSesion() }
            }
            botonSecundario("¿No tienes cuenta? Crear una") {
                errorMensaje = nil
                paso = .registro
            }
        }
        .padding(22)
        .tarjetaVidrio(esquina: 24, flotante: true)
    }

    // MARK: - Paso 3: Registro

    private var pasoRegistro: some View {
        VStack(alignment: .leading, spacing: 16) {
            if let host = hostDelServidor() {
                HStack(spacing: 8) {
                    insigniaServidor(clasificarServidor(host) ?? .nube(host))
                    Spacer()
                    Button("Cambiar") {
                        errorMensaje = nil
                        paso = .servidor
                    }
                    .font(.footnote.weight(.medium))
                    .tint(EdecanTheme.morado)
                }
            }
            campo(
                icono: "building.2",
                etiqueta: "Nombre de tu empresa/equipo",
                texto: $nombreEmpresa,
                submit: .next
            )
            .focused($enfocado, equals: .empresa)
            campo(
                icono: "envelope",
                etiqueta: "Correo",
                texto: $email,
                teclado: .emailAddress,
                contentType: .emailAddress,
                submit: .next
            )
            .focused($enfocado, equals: .email)
            campo(
                icono: "lock",
                etiqueta: "Contraseña (mínimo 8 caracteres)",
                texto: $password,
                seguro: !mostrarPassword,
                contentType: .newPassword,
                submit: .go,
                onEnviar: { Task { await crearCuenta() } },
                trailing: {
                    Button {
                        mostrarPassword.toggle()
                    } label: {
                        Image(systemName: mostrarPassword ? "eye.slash" : "eye")
                            .font(.system(size: 14, weight: .medium))
                            .foregroundStyle(.secondary)
                    }
                    .buttonStyle(.plain)
                }
            )
            mensajeDeError
            botonPrincipal("Crear cuenta", habilitado: formularioDeRegistroValido) {
                Task { await crearCuenta() }
            }
            botonSecundario("Ya tengo cuenta — iniciar sesión") {
                errorMensaje = nil
                paso = .sesion
            }
        }
        .padding(22)
        .tarjetaVidrio(esquina: 24, flotante: true)
    }

    // MARK: - Paso 4: QR (secundario)

    private var pasoQR: some View {
        VStack(spacing: 18) {
            Image(systemName: "qrcode.viewfinder")
                .font(.system(size: 44))
                .foregroundStyle(EdecanTheme.morado)
            Text("Escanea el QR de tu Edecan")
                .font(.title3.weight(.bold))
            Text("En tu computador abre Ajustes → Conectar teléfono, toca el botón y apunta al código.")
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
            botonPrincipal("Escanear QR", habilitado: !cargando) {
                abrirEscaner()
            }
            botonSecundario("Conectar con correo y clave") {
                errorMensaje = nil
                pairingStore.limpiarErrorDeEnlace()
                paso = pairingStore.serverURL == nil ? .servidor : .sesion
            }
        }
        .frame(maxWidth: .infinity)
        .padding(22)
        .tarjetaVidrio(esquina: 24, flotante: true)
    }

    // MARK: - Piezas de vidrio reutilizables

    private func campo(
        icono: String,
        etiqueta: String,
        texto: Binding<String>,
        seguro: Bool = false,
        teclado: UIKeyboardType = .default,
        contentType: UITextContentType? = nil,
        submit: SubmitLabel = .next,
        onEnviar: (() -> Void)? = nil,
        @ViewBuilder trailing: () -> some View = { EmptyView() }
    ) -> some View {
        HStack(spacing: 10) {
            Image(systemName: icono)
                .font(.system(size: 15, weight: .medium))
                .foregroundStyle(.secondary)
                .frame(width: 22)
            Group {
                if seguro {
                    SecureField(etiqueta, text: texto)
                        .textContentType(contentType)
                } else {
                    TextField(etiqueta, text: texto)
                        .textContentType(contentType)
                }
            }
            .keyboardType(teclado)
            .autocorrectionDisabled()
            .textInputAutocapitalization(.never)
            .submitLabel(submit)
            .font(.system(size: 16))
            .onSubmit { onEnviar?() }
            trailing()
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 13)
        .background(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .fill(EdecanTheme.fondoTarjeta(esquema))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .strokeBorder(.primary.opacity(0.07), lineWidth: 1)
        )
    }

    /// La insignia que le dice al dueño A QUÉ se conecta: nube (VPS) o Mac local.
    private func insigniaServidor(_ clasificacion: ClasificacionServidor) -> some View {
        HStack(spacing: 6) {
            Image(systemName: clasificacion.esNube ? "cloud.fill" : "laptopcomputer.and.iphone")
                .font(.system(size: 12, weight: .semibold))
            Text(clasificacion.titulo)
                .font(.footnote.weight(.semibold))
            Text("· \(clasificacion.host)")
                .font(.caption2)
                .lineLimit(1)
                .truncationMode(.middle)
        }
        .foregroundStyle(clasificacion.esNube ? EdecanTheme.morado : EdecanTheme.azul)
        .padding(.horizontal, 12)
        .padding(.vertical, 7)
        .capsulaVidrio()
    }

    private func chipServidor(_ titulo: String, icono: String, url: String) -> some View {
        Button {
            urlTexto = url
            errorMensaje = nil
            Haptico.ligero()
        } label: {
            HStack(spacing: 6) {
                Image(systemName: icono)
                    .font(.system(size: 12, weight: .semibold))
                Text(titulo)
                    .font(.footnote.weight(.semibold))
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
            .capsulaVidrio()
        }
        .buttonStyle(.plain)
    }

    private func botonPrincipal(_ titulo: String, habilitado: Bool, accion: @escaping () -> Void) -> some View {
        Button(action: accion) {
            Group {
                if cargando {
                    ProgressView().tint(.white)
                } else {
                    Text(titulo)
                        .font(.system(size: 17, weight: .semibold))
                        .foregroundStyle(.white)
                }
            }
            .frame(maxWidth: .infinity)
            .frame(height: 52)
            .background(
                RoundedRectangle(cornerRadius: 26, style: .continuous)
                    .fill(habilitado && !cargando ? EdecanTheme.botonEnviarNegro : Color.secondary.opacity(0.3))
            )
        }
        .buttonStyle(.plain)
        .disabled(!habilitado || cargando)
        .animation(.easeOut(duration: 0.15), value: habilitado)
    }

    private func botonSecundario(_ titulo: String, accion: @escaping () -> Void) -> some View {
        Button(titulo, action: accion)
            .font(.footnote.weight(.medium))
            .tint(EdecanTheme.morado)
            .frame(maxWidth: .infinity)
            .disabled(cargando)
    }

    @ViewBuilder
    private var mensajeDeError: some View {
        if let errorMensaje {
            HStack(spacing: 8) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.footnote)
                Text(errorMensaje)
                    .font(.footnote)
            }
            .foregroundStyle(.red)
            .padding(.horizontal, 12)
            .padding(.vertical, 9)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .fill(Color.red.opacity(0.08))
            )
            .transition(.opacity.combined(with: .move(edge: .top)))
        }
    }

    // MARK: - Clasificación del servidor (Nube vs Mac)

    private struct ClasificacionServidor {
        let titulo: String
        let host: String
        let esNube: Bool

        static func nube(_ host: String) -> ClasificacionServidor {
            ClasificacionServidor(titulo: "VPS · 24/7 en la nube", host: host, esNube: true)
        }

        static func mac(_ host: String) -> ClasificacionServidor {
            ClasificacionServidor(titulo: "Tu Mac (red local)", host: host, esNube: false)
        }
    }

    private func hostDelServidor() -> String? {
        let texto = urlTexto.trimmingCharacters(in: .whitespaces)
        if let url = URL(string: texto), let host = url.host(), !host.isEmpty { return host }
        if let url = pairingStore.serverURL, let host = url.host(), !host.isEmpty { return host }
        return nil
    }

    private func clasificarServidor(_ texto: String) -> ClasificacionServidor? {
        guard let host = URL(string: texto.trimmingCharacters(in: .whitespaces))?.host(),
              !host.isEmpty
        else { return nil }
        let esDireccionLocal = esDireccionIP(host)
            || host.lowercased().hasSuffix(".local")
        return esDireccionLocal ? .mac(host) : .nube(host)
    }

    private func esDireccionIP(_ host: String) -> Bool {
        let partes = host.split(separator: ".")
        guard partes.count == 4 else { return false }
        return partes.allSatisfy { !$0.isEmpty && $0.allSatisfy(\.isNumber) }
    }

    // MARK: - Errores amigables

    private func mensajeAmigable(_ error: Error) -> String {
        if let apiError = error as? APIClient.APIError {
            switch apiError {
            case .credencialesInvalidas:
                return "Correo o clave incorrectos."
            case .servidor(let status, _) where status == 401:
                return "Correo o clave incorrectos."
            case .servidor(let status, _) where status == 403:
                return "El servidor no permite esta conexión desde Internet. Verifica la dirección."
            case .sinConexion:
                return "No pude alcanzar el servidor. Revisa la dirección y tu Internet."
            case .urlInvalida:
                return "La dirección del servidor no es válida."
            default:
                break
            }
        }
        return error.localizedDescription
    }

    // MARK: - Lógica (idéntica a la de siempre)

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
            Haptico.exito()
        } catch {
            errorMensaje = mensajeAmigable(error)
            Haptico.error()
        }
        cargando = false
        intentarProcesarEnlacePendiente()
    }

    private func continuarConServidor() {
        errorMensaje = nil
        var texto = urlTexto.trimmingCharacters(in: .whitespaces)
        if !texto.isEmpty,
           !texto.lowercased().hasPrefix("http://"),
           !texto.lowercased().hasPrefix("https://")
        {
            let pareceLocal = esDireccionIP(texto)
                || texto.lowercased().hasSuffix(".local")
                || !texto.contains(".")
            texto = (pareceLocal ? "http://" : "https://") + texto
        }
        do {
            let url = try ServerURLPolicy.parseAndValidate(texto)
            try pairingStore.guardarServidor(url)
            session.actualizarBaseURL(url)
            urlTexto = url.absoluteString
            Haptico.ligero()
            withAnimation(.snappy(duration: 0.3)) { paso = .sesion }
        } catch {
            errorMensaje = mensajeAmigable(error)
            Haptico.error()
        }
    }

    private func iniciarSesion() async {
        guard !email.isEmpty, !password.isEmpty else { return }
        guard let client = session.client else {
            errorMensaje = "Primero define la URL del servidor."
            paso = .servidor
            return
        }
        cargando = true
        errorMensaje = nil
        enfocado = nil
        do {
            try await client.login(email: email, password: password, totpCode: totp)
            await completarEmparejamientoManual()
            Haptico.exito()
        } catch {
            errorMensaje = mensajeAmigable(error)
            Haptico.error()
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
        enfocado = nil
        do {
            try await client.registrar(email: email, password: password, tenantName: nombreEmpresa)
            await completarEmparejamientoManual()
            Haptico.exito()
        } catch {
            errorMensaje = mensajeAmigable(error)
            Haptico.error()
        }
        cargando = false
    }

    private func completarEmparejamientoManual() async {
        session.marcarSesionValida()
        pairingStore.marcarEmparejado()
        await session.cargarMe()
        await session.emparejarDispositivo(pairingStore: pairingStore)
    }

    private func abrirEscaner() {
        errorMensaje = nil
        pairingStore.limpiarErrorDeEnlace()
        guard DataScannerViewController.isSupported else {
            errorMensaje = "Este dispositivo no admite el escáner integrado. Puedes conectarlo con tu correo y clave."
            return
        }
        guard DataScannerViewController.isAvailable else {
            errorMensaje = "La cámara no está disponible. Revisa el permiso de Cámara de Edecán en Ajustes e inténtalo otra vez."
            return
        }
        mostrandoEscaner = true
    }

    private func recibirCodigoEscaneado(_ rawValue: String) {
        mostrandoEscaner = false
        guard let url = URL(string: rawValue) else {
            errorMensaje = "Ese QR no contiene un enlace válido de Edecán. Genera uno nuevo en tu computador."
            return
        }
        pairingStore.recibirEnlace(url)
    }

    private func mostrarErrorDelEscaner(_ message: String) {
        mostrandoEscaner = false
        errorMensaje = message
    }
}
