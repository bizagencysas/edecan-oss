import SwiftUI
import EdecanKit

/// Redes sociales de Edecán. La casa es el VPS: conectas LinkedIn, X, Meta y
/// YouTube desde aquí, el proveedor se abre en el navegador del móvil y el token
/// se guarda en el servidor — nunca en el teléfono.
struct RedesView: View {
    @Environment(SessionStore.self) private var session

    @State private var conectores: [ConnectorListItem] = []
    @State private var cargando = false
    @State private var errorCarga: String?
    @State private var conectandoKey: String?
    @State private var desconectandoId: String?
    @State private var aviso: String?
    @State private var avisoEsError = false
    @State private var cuentaPendiente: RedesCuentaPendiente?
    @State private var conectorParaConfigurar: ConnectorListItem?

    private var filasSociales: [ConnectorListItem] {
        let porClave = Dictionary(
            uniqueKeysWithValues: conectores.filter(\.esOAuthSocial).map { ($0.key, $0) }
        )
        return SocialOAuthConnectorKey.allCases.map { key in
            porClave[key.rawValue] ?? .filaCatalogo(key)
        }
    }

    var body: some View {
        List {
            Section {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Conecta tus redes para que Edecán publique por ti.")
                        .font(.footnote.weight(.semibold))
                    Text("Al conectar, se abre la red en el navegador y vuelves automáticamente. Tu acceso queda guardado en el servidor, no en este teléfono.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .listRowBackground(Color.clear)
            }

            if let errorCarga {
                Section {
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

            if let aviso {
                Section {
                    Label(aviso, systemImage: avisoEsError ? "exclamationmark.triangle.fill" : "checkmark.circle.fill")
                        .font(.footnote)
                        .foregroundStyle(avisoEsError ? .red : .green)
                }
            }

            Section {
                if cargando && conectores.isEmpty {
                    HStack {
                        Spacer()
                        ProgressView()
                        Spacer()
                    }
                    .padding(.vertical, 20)
                } else {
                    ForEach(filasSociales) { connector in
                        tarjetaRed(connector)
                    }
                }
            } header: {
                Text("Redes sociales")
            } footer: {
                Text("Primera vez: pega tu app OAuth (client id y secret) del proveedor. Después «Conectar» te lleva a la red y vuelves solo.")
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Redes")
        .navigationBarTitleDisplayMode(.large)
        .task { await cargar() }
        .refreshable { await cargar() }
        .onReceive(NotificationCenter.default.publisher(for: .edecanConectoresOAuth)) { _ in
            Task { await cargar() }
        }
        .sheet(item: $conectorParaConfigurar) { connector in
            ConfigurarAppOAuthSheet(connector: connector) { texto, esError in
                aviso = texto
                avisoEsError = esError
                Task { await cargar() }
            }
        }
        .confirmationDialog(
            "¿Desconectar esta cuenta?",
            isPresented: Binding(
                get: { cuentaPendiente != nil },
                set: { if !$0 { cuentaPendiente = nil } }
            ),
            titleVisibility: .visible,
            presenting: cuentaPendiente
        ) { pendiente in
            Button("Desconectar", role: .destructive) {
                Task { await desconectar(key: pendiente.key, accountId: pendiente.accountId) }
            }
            Button("Cancelar", role: .cancel) { cuentaPendiente = nil }
        } message: { pendiente in
            Text("Dejarás de publicar con «\(pendiente.etiqueta)» hasta que la vuelvas a conectar.")
        }
    }

    // MARK: - Tarjeta de cada red

    @ViewBuilder
    private func tarjetaRed(_ connector: ConnectorListItem) -> some View {
        let meta = SocialOAuthConnectorKey(rawValue: connector.key)
        let cuentasActivas = connector.accounts.filter(\.conectada)

        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 12) {
                ZStack {
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .fill(colorRed(meta).opacity(0.14))
                        .frame(width: 44, height: 44)
                    Image(systemName: meta?.iconoSistema ?? "link.circle.fill")
                        .font(.system(size: 18, weight: .semibold))
                        .foregroundStyle(colorRed(meta))
                }
                VStack(alignment: .leading, spacing: 3) {
                    Text(meta?.titulo ?? connector.displayName)
                        .font(.headline)
                    Text(subtitulo(connector))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                if conectandoKey == connector.key {
                    ProgressView()
                } else {
                    estadoPill(connector)
                }
            }

            if connector.appConfigured == false {
                Button {
                    conectorParaConfigurar = connector
                } label: {
                    Label("Vincular mi cuenta", systemImage: "key.fill")
                        .font(.subheadline.weight(.semibold))
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 10)
                }
                .buttonStyle(.bordered)
                .tint(colorRed(meta))
            } else if connector.oauthRedirectUri != nil {
                Button {
                    Task { await conectar(connector.key) }
                } label: {
                    Label(
                        connector.tieneCuenta ? "Conectar otra cuenta" : "Conectar",
                        systemImage: "plus.circle.fill"
                    )
                    .font(.subheadline.weight(.semibold))
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 10)
                }
                .buttonStyle(.borderedProminent)
                .tint(colorRed(meta))
                .disabled(conectandoKey != nil)
            }

            if !cuentasActivas.isEmpty {
                Divider().opacity(0.4)
                ForEach(cuentasActivas) { cuenta in
                    HStack(spacing: 10) {
                        Image(systemName: "checkmark.seal.fill")
                            .foregroundStyle(.green)
                            .font(.caption)
                        Text(cuenta.etiqueta)
                            .font(.subheadline)
                        Spacer()
                        if desconectandoId == cuenta.id {
                            ProgressView()
                        } else {
                            Button("Quitar", role: .destructive) {
                                cuentaPendiente = RedesCuentaPendiente(
                                    key: connector.key,
                                    accountId: cuenta.id,
                                    etiqueta: cuenta.etiqueta
                                )
                            }
                            .font(.caption)
                        }
                    }
                }
            }
        }
        .padding(.vertical, 6)
    }

    @ViewBuilder
    private func estadoPill(_ connector: ConnectorListItem) -> some View {
        let activas = connector.accounts.filter(\.conectada).count
        if activas > 0 {
            Text("Conectada")
                .font(.caption.weight(.bold))
                .padding(.horizontal, 10)
                .padding(.vertical, 5)
                .background(Color.green.opacity(0.14), in: Capsule())
                .foregroundStyle(.green)
        } else {
            Text("Sin conectar")
                .font(.caption.weight(.bold))
                .padding(.horizontal, 10)
                .padding(.vertical, 5)
                .background(Color.secondary.opacity(0.14), in: Capsule())
                .foregroundStyle(.secondary)
        }
    }

    private func subtitulo(_ connector: ConnectorListItem) -> String {
        if connector.tieneCuenta { return "Edecán puede publicar por ti" }
        if connector.appConfigured == false { return "Vincula tu cuenta para empezar" }
        return "Lista para conectar"
    }

    private func colorRed(_ key: SocialOAuthConnectorKey?) -> Color {
        switch key {
        case .linkedin: return Color(red: 0.0, green: 0.46, blue: 0.71)
        case .x: return .primary
        case .meta: return Color(red: 0.0, green: 0.45, blue: 0.85)
        case .youtube: return Color(red: 0.9, green: 0.0, blue: 0.0)
        case nil: return EdecanTheme.morado
        }
    }

    // MARK: - Acciones

    private func cargar() async {
        guard let client = session.client else { return }
        cargando = true
        defer { cargando = false }
        do {
            conectores = try await client.listConnectors()
            errorCarga = nil
        } catch {
            errorCarga = "No se pudieron cargar las redes."
        }
    }

    private func conectar(_ key: String) async {
        guard let client = session.client else { return }
        conectandoKey = key
        aviso = nil
        defer { conectandoKey = nil }
        do {
            let out = try await client.getConnectorAuthorizeUrl(key: key, returnTo: "mobile")
            guard let url = URL(string: out.url) else {
                aviso = "No se pudo abrir la conexión."
                avisoEsError = true
                return
            }
            switch await SocialOAuthSession.start(providerURL: url) {
            case .success(let okKey):
                let nombre = SocialOAuthConnectorKey(rawValue: okKey.isEmpty ? key : okKey)?.titulo ?? okKey
                aviso = "\(nombre) conectada."
                avisoEsError = false
                await cargar()
            case .failure(let failKey, let error):
                let nombre = SocialOAuthConnectorKey(rawValue: failKey.isEmpty ? key : failKey)?.titulo ?? failKey
                aviso = "\(nombre): \(error)"
                avisoEsError = true
            case .cancelled:
                break
            }
        } catch {
            aviso = error.localizedDescription
            avisoEsError = true
        }
    }

    private func desconectar(key: String, accountId: String) async {
        guard let client = session.client else { return }
        cuentaPendiente = nil
        desconectandoId = accountId
        defer { desconectandoId = nil }
        do {
            try await client.disconnectConnector(key: key, accountId: accountId)
            aviso = "Cuenta desconectada."
            avisoEsError = false
            await cargar()
        } catch {
            aviso = error.localizedDescription
            avisoEsError = true
        }
    }
}

private struct RedesCuentaPendiente: Identifiable {
    let key: String
    let accountId: String
    let etiqueta: String
    var id: String { accountId }
}