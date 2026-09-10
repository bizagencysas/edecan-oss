import SwiftUI
import EdecanKit

/// Presentación de la sección OAuth social: lista agrupada (Conectores) o tarjetas
/// Liquid Glass embebidas en Perfil.
enum SocialConnectorsPresentation: Sendable {
    case listSection(compact: Bool = false)
    case profileGlass
}

/// Redes sociales OAuth vía VPS (`GET /v1/connectors`, authorize con `return_to=mobile`, `DELETE` por cuenta).
struct SocialConnectorsSection: View {
    @Environment(SessionStore.self) private var session
    var presentation: SocialConnectorsPresentation = .listSection(compact: false)

    @State private var conectores: [ConnectorListItem] = []
    @State private var cargando = false
    @State private var errorCarga: String?
    @State private var conectandoKey: String?
    @State private var desconectandoId: String?
    @State private var mensaje: String?
    @State private var mensajeEsError = false
    @State private var cuentaPendienteDesconectar: CuentaDesconexionPendiente?
    @State private var conectorParaConfigurar: ConnectorListItem?

    private var compact: Bool {
        if case .listSection(let compact) = presentation { return compact }
        return false
    }

    private var esPerfil: Bool {
        if case .profileGlass = presentation { return true }
        return false
    }

    /// Siempre las cuatro redes; estado y cuentas solo del ítem real del VPS (o vacío).
    private var filasSociales: [ConnectorListItem] {
        let porClave = Dictionary(
            uniqueKeysWithValues: conectores.filter(\.esOAuthSocial).map { ($0.key, $0) }
        )
        return SocialOAuthConnectorKey.allCases.map { key in
            porClave[key.rawValue] ?? .filaCatalogo(key)
        }
    }

    var body: some View {
        Group {
            switch presentation {
            case .listSection:
                cuerpoLista
            case .profileGlass:
                cuerpoPerfil
            }
        }
        .task { await cargar() }
        .refreshable { await cargar() }
        .onReceive(NotificationCenter.default.publisher(for: .edecanConectoresOAuth)) { _ in
            Task { await cargar() }
        }
        .confirmationDialog(
            "¿Desconectar esta cuenta?",
            isPresented: Binding(
                get: { cuentaPendienteDesconectar != nil },
                set: { if !$0 { cuentaPendienteDesconectar = nil } }
            ),
            titleVisibility: .visible,
            presenting: cuentaPendienteDesconectar
        ) { pendiente in
            Button("Desconectar", role: .destructive) {
                Task { await desconectar(key: pendiente.key, accountId: pendiente.accountId) }
            }
            Button("Cancelar", role: .cancel) {
                cuentaPendienteDesconectar = nil
            }
        } message: { pendiente in
            Text("Se revocará «\(pendiente.etiqueta)» en el VPS. Los bots dejarán de publicar con esa cuenta hasta que vuelvas a conectar.")
        }
        .sheet(item: $conectorParaConfigurar) { connector in
            ConfigurarAppOAuthSheet(connector: connector) { texto, esError in
                mensaje = texto
                mensajeEsError = esError
                Task { await cargar() }
            }
        }
    }

    private var cuerpoLista: some View {
        Section {
            if compact {
                Text("OAuth en el VPS (LinkedIn, X, Meta, YouTube). Conectar y desconectar desde el iPhone.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .listRowBackground(Color.clear)
            }
            bannerErrorCarga
            bannerMensaje
            if cargando && conectores.isEmpty {
                ProgressView()
            } else {
                ForEach(filasSociales) { connector in
                    fila(connector)
                }
            }
        } header: {
            Text(compact ? "Redes OAuth (VPS)" : "Redes sociales")
        } footer: {
            if !compact {
                Text("Primera vez: configura tu app OAuth (BYO) desde aquí, con el client id/secret del proveedor. Luego «Conectar» abre el proveedor en el navegador del móvil y vuelve a Edecán. El estado «Conectada» solo aparece cuando el VPS confirma token en vault.")
            }
        }
    }

    private var cuerpoPerfil: some View {
        VStack(alignment: .leading, spacing: 12) {
            VStack(alignment: .leading, spacing: 6) {
                Text("REDES CONECTADAS")
                    .font(.caption.weight(.bold))
                    .foregroundStyle(.secondary)
                    .tracking(0.6)
                Text("LinkedIn, X, Meta y YouTube — conectar y desconectar sin salir de Perfil. Sin prisa; «Pendiente» significa fila sin token en vault todavía.")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.horizontal, 4)

            bannerErrorCargaPerfil
            bannerMensajePerfil

            if cargando && conectores.isEmpty {
                HStack {
                    Spacer()
                    ProgressView()
                    Spacer()
                }
                .padding(28)
                .frame(maxWidth: .infinity)
                .tarjetaVidrioFlotante(esquina: 20)
            } else {
                ForEach(filasSociales) { connector in
                    filaPerfil(connector)
                }
            }

            NavigationLink {
                ConectoresView()
            } label: {
                HStack(spacing: 10) {
                    Image(systemName: "cable.connector")
                        .foregroundStyle(EdecanTheme.morado)
                    Text("MCP y conectores avanzados")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(EdecanTheme.morado)
                    Spacer()
                    Image(systemName: "chevron.right")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(.tertiary)
                }
                .padding(.horizontal, 4)
                .padding(.vertical, 6)
            }
            .buttonStyle(.plain)
        }
    }

    @ViewBuilder
    private var bannerErrorCarga: some View {
        if let errorCarga {
            VStack(alignment: .leading, spacing: 8) {
                Text(errorCarga)
                    .font(.footnote)
                    .foregroundStyle(.red)
                Button("Reintentar") {
                    Task { await cargar() }
                }
                .font(.footnote.weight(.semibold))
            }
        }
    }

    @ViewBuilder
    private var bannerErrorCargaPerfil: some View {
        if let errorCarga {
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 10) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundStyle(.red)
                    Text(errorCarga)
                        .font(.caption)
                        .foregroundStyle(.primary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Button("Reintentar") {
                    Task { await cargar() }
                }
                .font(.caption.weight(.semibold))
                .buttonStyle(.bordered)
                .tint(EdecanTheme.morado)
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .tarjetaVidrio(esquina: 14, tint: .red)
        }
    }

    @ViewBuilder
    private var bannerMensaje: some View {
        if let mensaje {
            Text(mensaje)
                .font(.footnote)
                .foregroundStyle(mensajeEsError ? .red : .green)
        }
    }

    @ViewBuilder
    private var bannerMensajePerfil: some View {
        if let mensaje {
            HStack(spacing: 10) {
                Image(systemName: mensajeEsError ? "exclamationmark.triangle.fill" : "checkmark.circle.fill")
                    .foregroundStyle(mensajeEsError ? .red : .green)
                Text(mensaje)
                    .font(.caption)
                    .foregroundStyle(mensajeEsError ? .red : .primary)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
            }
            .padding(12)
            .tarjetaVidrio(esquina: 14, tint: mensajeEsError ? .red : .green)
        }
    }

    @ViewBuilder
    private func fila(_ connector: ConnectorListItem) -> some View {
        contenidoFila(connector)
            .padding(.vertical, 4)
    }

    @ViewBuilder
    private func filaPerfil(_ connector: ConnectorListItem) -> some View {
        contenidoFila(connector)
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .tarjetaVidrioFlotante(esquina: 20)
    }

    @ViewBuilder
    private func contenidoFila(_ connector: ConnectorListItem) -> some View {
        let meta = SocialOAuthConnectorKey(rawValue: connector.key)
        let cuentasActivas = connector.accounts.filter(\.conectada)
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 12) {
                ZStack {
                    Circle()
                        .fill(EdecanTheme.morado.opacity(0.12))
                        .frame(width: 40, height: 40)
                    Image(systemName: meta?.iconoSistema ?? "link.circle.fill")
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundStyle(EdecanTheme.morado)
                }
                VStack(alignment: .leading, spacing: 2) {
                    Text(meta?.titulo ?? connector.displayName)
                        .font(.subheadline.weight(.semibold))
                    Text(estadoTexto(connector))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                if conectandoKey == connector.key {
                    ProgressView()
                } else {
                    estadoBadge(connector)
                }
            }

            if connector.appConfigured == false {
                Button {
                    conectorParaConfigurar = connector
                } label: {
                    Label("Configurar app OAuth", systemImage: "key.fill")
                        .font(.caption.weight(.semibold))
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 10)
                }
                .buttonStyle(.bordered)
                .tint(EdecanTheme.morado)
                Text("Pega aquí tu client id/secret BYO; se guardan en el VPS, no en el teléfono.")
                    .font(.caption2)
                    .foregroundStyle(.orange)
            } else if connector.oauthRedirectUri != nil {
                Button {
                    Task { await conectar(connector.key) }
                } label: {
                    Text(connector.tieneCuenta ? "Conectar otra cuenta" : "Conectar")
                        .font(.caption.weight(.semibold))
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 10)
                }
                .buttonStyle(.borderedProminent)
                .tint(EdecanTheme.morado)
                .disabled(conectandoKey != nil)
            }

            ForEach(cuentasActivas) { cuenta in
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(cuenta.etiqueta)
                            .font(.caption.weight(.medium))
                    }
                    Spacer()
                    if desconectandoId == cuenta.id {
                        ProgressView()
                    } else {
                        Button("Desconectar", role: .destructive) {
                            cuentaPendienteDesconectar = CuentaDesconexionPendiente(
                                key: connector.key,
                                accountId: cuenta.id,
                                etiqueta: cuenta.etiqueta
                            )
                        }
                        .font(.caption2)
                    }
                }
                .padding(.vertical, 4)
                if esPerfil {
                    Divider().opacity(0.35)
                }
            }
        }
    }

    @ViewBuilder
    private func estadoBadge(_ connector: ConnectorListItem) -> some View {
        let activas = connector.accounts.filter(\.conectada)
        let huerfanas = connector.accounts.filter(\.esPendiente)
        if !activas.isEmpty {
            Text("\(activas.count) cuenta\(activas.count == 1 ? "" : "s")")
                .font(.caption2.weight(.bold))
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .background(EdecanTheme.morado.opacity(0.14), in: Capsule())
                .foregroundStyle(EdecanTheme.morado)
        } else if !huerfanas.isEmpty {
            Text("Pendiente")
                .font(.caption2.weight(.bold))
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .background(Color.orange.opacity(0.14), in: Capsule())
                .foregroundStyle(.orange)
        } else {
            Text("Sin conectar")
                .font(.caption2.weight(.bold))
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .background(Color.secondary.opacity(0.14), in: Capsule())
                .foregroundStyle(.secondary)
        }
    }

    private func estadoTexto(_ connector: ConnectorListItem) -> String {
        if connector.appConfigured == false { return "Falta vincular tu cuenta" }
        let n = connector.accounts.filter(\.conectada).count
        if n > 0 { return "\(n) cuenta\(n == 1 ? "" : "s") conectada\(n == 1 ? "" : "s")" }
        if connector.accounts.contains(where: \.esPendiente) {
            return "Pendiente"
        }
        return "Sin conectar"
    }

    private func cargar() async {
        guard let client = session.client else { return }
        cargando = true
        defer { cargando = false }
        do {
            conectores = try await client.listConnectors()
            errorCarga = nil
        } catch {
            errorCarga = "No se pudieron cargar las redes. \(error.localizedDescription)"
        }
    }

    private func conectar(_ key: String) async {
        guard let client = session.client else { return }
        conectandoKey = key
        mensaje = nil
        defer { conectandoKey = nil }
        do {
            let out = try await client.getConnectorAuthorizeUrl(key: key, returnTo: "mobile")
            guard let url = URL(string: out.url) else {
                mensaje = "URL de autorización inválida"
                mensajeEsError = true
                return
            }
            switch await SocialOAuthSession.start(providerURL: url) {
            case .success(let okKey):
                mensaje = "Listo: \(SocialOAuthConnectorKey(rawValue: okKey.isEmpty ? key : okKey)?.titulo ?? okKey)"
                mensajeEsError = false
                await cargar()
            case .failure(let failKey, let error):
                let nombre = SocialOAuthConnectorKey(rawValue: failKey.isEmpty ? key : failKey)?.titulo ?? failKey
                mensaje = "\(nombre): \(error)"
                mensajeEsError = true
            case .cancelled:
                break
            }
        } catch {
            mensaje = error.localizedDescription
            mensajeEsError = true
        }
    }

    private func desconectar(key: String, accountId: String) async {
        guard let client = session.client else { return }
        cuentaPendienteDesconectar = nil
        desconectandoId = accountId
        defer { desconectandoId = nil }
        do {
            try await client.disconnectConnector(key: key, accountId: accountId)
            mensaje = "Cuenta desconectada."
            mensajeEsError = false
            await cargar()
        } catch {
            mensaje = error.localizedDescription
            mensajeEsError = true
        }
    }

    }

private struct CuentaDesconexionPendiente: Identifiable {
    let key: String
    let accountId: String
    let etiqueta: String
    var id: String { accountId }
}

/// Form para pegar la app OAuth propia (BYO) del tenant. Guarda en el VPS vía
/// `PUT /v1/connectors/{key}/app-credentials`; nada se persiste en el iPhone.
struct ConfigurarAppOAuthSheet: View {
    let connector: ConnectorListItem
    let onDone: (String, Bool) -> Void

    @Environment(\.dismiss) private var dismiss
    @Environment(SessionStore.self) private var session
    @State private var clientId = ""
    @State private var clientSecret = ""
    @State private var guardando = false
    @State private var errorLocal: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text("Registra la app OAuth que creaste con \(connector.displayName). El client_id y el secret se guardan cifrados en el VPS — nunca en este teléfono.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Section("Credenciales") {
                    TextField("Client ID", text: $clientId)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    SecureField("Client Secret (opcional)", text: $clientSecret)
                }
                if let url = connector.oauthRedirectUri, !url.isEmpty {
                    Section("Callback / Redirect URI (pega esto en la app del proveedor)") {
                        Text(url)
                            .font(.caption.monospaced())
                            .textSelection(.enabled)
                    }
                }
                if let errorLocal {
                    Section {
                        Text(errorLocal)
                            .font(.caption)
                            .foregroundStyle(.red)
                    }
                }
            }
            .navigationTitle("App OAuth · \(connector.displayName)")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancelar") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    if guardando {
                        ProgressView()
                    } else {
                        Button("Guardar") { Task { await guardar() } }
                            .disabled(clientId.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    }
                }
            }
        }
    }

    private func guardar() async {
        guard let client = session.client else { return }
        let id = clientId.trimmingCharacters(in: .whitespacesAndNewlines)
        let secret = clientSecret.trimmingCharacters(in: .whitespacesAndNewlines)
        guardando = true
        defer { guardando = false }
        do {
            try await client.putConnectorAppCredentials(
                key: connector.key,
                clientId: id,
                clientSecret: secret.isEmpty ? nil : secret
            )
            onDone("App OAuth de \(connector.displayName) guardada en el VPS.", false)
            dismiss()
        } catch {
            errorLocal = error.localizedDescription
        }
    }
}
