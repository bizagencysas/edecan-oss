import SwiftUI
import EdecanKit

/// Ajustes. Encabezado de cuenta, estado de conexión
/// de LLM/voz/imágenes/búsqueda (`GET /v1/credentials`) con un selector
/// "Conectar LLM" ("pegar y validar"), y "Cerrar sesión" — editar persona,
/// tema y notificaciones sigue siendo placeholder.
struct PerfilView: View {
    @Environment(PairingStore.self) private var pairingStore
    @Environment(SessionStore.self) private var session
    @State private var credencialesVM = CredencialesViewModel()
    @State private var mostrarConfirmacionSalir = false
    @State private var mostrarHojaLLM = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 20) {
                    encabezado

                    seccionConexiones

                    modoAvanzado

                    EmptyStateView(
                        icono: "person.text.rectangle.fill",
                        titulo: "Más ajustes en camino",
                        descripcion: "Editar tu persona (tono, formalidad, instrucciones), tema de la app y notificaciones push van a vivir aquí — hoy se editan desde el panel web.",
                        etiquetaRoadmap: "Próximamente"
                    )
                    .frame(minHeight: 160)

                    Button("Cerrar sesión", role: .destructive) {
                        mostrarConfirmacionSalir = true
                    }
                    .buttonStyle(.bordered)
                }
                .padding()
            }
            .background(EdecanTheme.degradado.opacity(0.05).ignoresSafeArea())
            .navigationTitle("Ajustes")
            .task {
                await session.cargarMe()
                await credencialesVM.cargar(client: session.client)
            }
            .refreshable {
                await session.cargarMe()
                await credencialesVM.cargar(client: session.client)
            }
            .sheet(isPresented: $mostrarHojaLLM, onDismiss: {
                Task { await credencialesVM.cargar(client: session.client) }
            }) {
                ConectarLLMSheet(viewModel: credencialesVM, deteccion: credencialesVM.deteccion)
                    .environment(session)
            }
            .confirmationDialog(
                "¿Cerrar sesión en este dispositivo?",
                isPresented: $mostrarConfirmacionSalir,
                titleVisibility: .visible
            ) {
                Button("Cerrar sesión", role: .destructive, action: cerrarSesion)
                Button("Cancelar", role: .cancel) {}
            }
        }
    }

    private var encabezado: some View {
        VStack(spacing: 10) {
            Image(systemName: "person.crop.circle.fill")
                .font(.system(size: 60))
                .foregroundStyle(EdecanTheme.degradado)
            if let me = session.me {
                Text(me.user.email)
                    .font(.headline)
                Text(me.tenant.name)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            } else {
                ProgressView()
            }
        }
        .frame(maxWidth: .infinity)
        .padding(24)
        .tarjetaVidrio(esquina: 22)
    }

    private var modoAvanzado: some View {
        DisclosureGroup("Modo avanzado") {
            VStack(spacing: 0) {
                NavigationLink {
                    IDEView()
                } label: {
                    filaAvanzada(icono: "chevron.left.forwardslash.chevron.right", titulo: "IDE")
                }
                Divider().padding(.vertical, 8)
                NavigationLink {
                    NegociosView()
                } label: {
                    filaAvanzada(icono: "chart.pie.fill", titulo: "Negocios")
                }
            }
            .padding(.top, 12)
        }
        .padding(16)
        .tarjetaVidrio(esquina: 18)
    }

    private func filaAvanzada(icono: String, titulo: String) -> some View {
        HStack {
            Image(systemName: icono).frame(width: 24).foregroundStyle(EdecanTheme.morado)
            Text(titulo).foregroundStyle(.primary)
            Spacer()
            Image(systemName: "chevron.right").font(.caption).foregroundStyle(.tertiary)
        }
    }

    private var seccionConexiones: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Conexiones").font(.headline)
                Spacer()
                if credencialesVM.cargando { ProgressView().controlSize(.small) }
            }

            if let error = credencialesVM.errorMensaje {
                Text(error).font(.footnote).foregroundStyle(.red)
            }

            VStack(spacing: 0) {
                FilaConexion(
                    titulo: "LLM (cerebro del asistente)",
                    subtitulo: descripcionLLM,
                    conectado: credencialesVM.credenciales?.llm != nil
                ) {
                    mostrarHojaLLM = true
                }
                Divider().padding(.vertical, 10)
                FilaConexion(
                    titulo: "Voz — transcripción (STT)",
                    subtitulo: credencialesVM.credenciales?.voiceStt?.provider ?? "No conectado — la voz cae a un stub sin sonido real",
                    conectado: credencialesVM.credenciales?.voiceStt != nil,
                    accion: nil
                )
                Divider().padding(.vertical, 10)
                FilaConexion(
                    titulo: "Voz — síntesis (TTS)",
                    subtitulo: credencialesVM.credenciales?.voiceTts?.provider ?? "No conectado — la voz cae a un stub sin sonido real",
                    conectado: credencialesVM.credenciales?.voiceTts != nil,
                    accion: nil
                )
                Divider().padding(.vertical, 10)
                FilaConexion(
                    titulo: "Generación de imágenes",
                    subtitulo: credencialesVM.credenciales?.images.map { "\($0.model ?? "modelo desconocido")" } ?? "No conectado",
                    conectado: credencialesVM.credenciales?.images != nil,
                    accion: nil
                )
                Divider().padding(.vertical, 10)
                FilaConexion(
                    titulo: "Búsqueda web",
                    subtitulo: credencialesVM.credenciales?.search?.provider ?? "No conectado",
                    conectado: credencialesVM.credenciales?.search != nil,
                    accion: nil
                )
            }
        }
        .padding(16)
        .tarjetaVidrio(esquina: 18)
    }

    private var descripcionLLM: String {
        guard let llm = credencialesVM.credenciales?.llm, let kind = llm.kind else {
            return "No conectado — toca para elegir un proveedor"
        }
        let nombre = LLMProviderKind(rawValue: kind)?.nombreVisible ?? kind
        if let masked = llm.masked {
            return "\(nombre) · \(masked)"
        }
        return nombre
    }

    private func cerrarSesion() {
        let deviceId = pairingStore.deviceId
        pairingStore.olvidarEmparejamiento()
        Task {
            await session.cerrarSesion(deviceId: deviceId)
        }
    }
}

private struct FilaConexion: View {
    let titulo: String
    let subtitulo: String
    let conectado: Bool
    var accion: (() -> Void)? = nil

    var body: some View {
        Button {
            accion?()
        } label: {
            HStack(spacing: 10) {
                Circle()
                    .fill(conectado ? Color.green : Color.secondary.opacity(0.35))
                    .frame(width: 9, height: 9)
                VStack(alignment: .leading, spacing: 2) {
                    Text(titulo).font(.subheadline).foregroundStyle(.primary)
                    Text(subtitulo).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
                Spacer()
                if accion != nil {
                    Image(systemName: "chevron.right").font(.caption).foregroundStyle(.tertiary)
                }
            }
        }
        .buttonStyle(.plain)
        .disabled(accion == nil)
    }
}
