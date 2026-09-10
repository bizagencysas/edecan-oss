import SwiftUI
import EdecanKit

/// El espacio personal de la app móvil.
///
/// Cuenta, perfil vivo, redes OAuth conectadas y accesos útiles — con Liquid Glass
/// y secciones claras. OAuth social vive aquí (no solo en Conectores / Poderes).
struct PerfilView: View {
    @Environment(PairingStore.self) private var pairingStore
    @Environment(SessionStore.self) private var session
    @Environment(TabRouter.self) private var router
    @Environment(PushNotificationCoordinator.self) private var push
    @Environment(AppUpdateCoordinator.self) private var updates
    @Environment(\.openURL) private var openURL
    @State private var mostrarConfirmacionSalir = false
    @State private var mostrarEstudioDeContenido = false
    @State private var mostrarLinkedInStudio = false
    @State private var perfilVivo: LiveProfile?

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(spacing: 18) {
                    tarjetaDePersona
                    seccionCuenta
                    seccionRedesConectadas
                    if let update = updates.availableUpdate {
                        tarjetaActualizacion(update)
                    }
                    seccionTuEdecan
                    seccionAjustes
                    modoAvanzado
                    version

                    Button("Desvincular este iPhone", role: .destructive) {
                        mostrarConfirmacionSalir = true
                    }
                    .buttonStyle(.bordered)
                    .padding(.top, 4)
                }
                .padding(.horizontal, 18)
                .padding(.vertical, 12)
            }
            .scrollIndicators(.hidden)
            .background(FondoBotsLight())
            .navigationTitle("Tú")
            .navigationBarTitleDisplayMode(.large)
            .task { await cargarPerfilCompleto() }
            .refreshable { await cargarPerfilCompleto() }
            .sheet(isPresented: $mostrarEstudioDeContenido) {
                ContentStudioView()
                    .environment(router)
            }
            .sheet(isPresented: $mostrarLinkedInStudio) {
                LinkedInStudioView()
            }
            .confirmationDialog(
                "¿Desvincular este iPhone?",
                isPresented: $mostrarConfirmacionSalir,
                titleVisibility: .visible
            ) {
                Button("Desvincular", role: .destructive, action: cerrarSesion)
                Button("Cancelar", role: .cancel) {}
            } message: {
                Text("Para volver a usar Edecán tendrás que escanear nuevamente el QR de tu computadora.")
            }
        }
    }

    // MARK: - Cuenta

    private var tarjetaDePersona: some View {
        VStack(spacing: 12) {
            ZStack {
                Circle()
                    .fill(EdecanTheme.degradado)
                    .frame(width: 76, height: 76)
                    .shadow(color: EdecanTheme.morado.opacity(0.35), radius: 16, y: 6)
                Text(inicialDePersona)
                    .font(.title.bold())
                    .foregroundStyle(.white)
            }

            if let me = session.me {
                Text(nombreMostrado)
                    .font(.title2.bold())
                Text(me.user.email)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                Text(me.tenant.name)
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 4)
                    .background(.quaternary.opacity(0.5), in: Capsule())
            } else if session.cargandoMe {
                ProgressView()
                    .padding(.top, 8)
            } else {
                Text("Tu perfil")
                    .font(.headline)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 28)
        .padding(.horizontal, 20)
        .tarjetaVidrioFlotante(esquina: 24)
    }

    private var seccionCuenta: some View {
        VStack(alignment: .leading, spacing: 12) {
            encabezadoSeccion("CUENTA")

            NavigationLink {
                PerfilEditorView { actualizado in
                    perfilVivo = actualizado
                }
            } label: {
                filaTarjeta(
                    icono: "person.text.rectangle.fill",
                    titulo: "Perfil",
                    subtitulo: "Nombre, contexto y cómo quieres que Edecán te hable"
                )
            }
            .buttonStyle(.plain)

            tarjetaDeEdecan

            NavigationLink {
                MemoriaView()
            } label: {
                filaTarjeta(
                    icono: "brain.head.profile.fill",
                    titulo: "Memoria",
                    subtitulo: "Lo que Edecán sabe de ti"
                )
            }
            .buttonStyle(.plain)
        }
    }

    private var tarjetaDeEdecan: some View {
        HStack(spacing: 16) {
            ZStack {
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .fill(EdecanTheme.degradado)
                    .frame(width: 56, height: 56)
                Image(systemName: "headphones")
                    .font(.system(size: 24, weight: .semibold))
                    .foregroundStyle(.white)
            }

            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 7) {
                    Text("Edecán")
                        .font(.headline)
                    Circle()
                        .fill(session.me == nil ? Color.orange : Color.green)
                        .frame(width: 8, height: 8)
                    Text(session.me == nil ? "Conectando" : "Listo")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                }
                Text("Tu asistente para pensar, crear, organizar y hacer.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Spacer(minLength: 0)
        }
        .padding(16)
        .tarjetaVidrioFlotante(esquina: 20)
    }

    // MARK: - Redes

    private var seccionRedesConectadas: some View {
        VStack(alignment: .leading, spacing: 12) {
            encabezadoSeccion("REDES")
            NavigationLink {
                RedesView()
            } label: {
                filaTarjeta(
                    icono: "person.2.wave.2.fill",
                    titulo: "Redes",
                    subtitulo: "Conecta LinkedIn, X, Meta y YouTube para que Edecán publique por ti"
                )
            }
            .buttonStyle(.plain)
        }
    }

    // MARK: - Herramientas

    private var seccionTuEdecan: some View {
        VStack(alignment: .leading, spacing: 12) {
            encabezadoSeccion("TU EDECÁN")

            VStack(spacing: 0) {
                Button {
                    mostrarLinkedInStudio = true
                } label: {
                    filaInterna(icono: "briefcase.fill", titulo: "LinkedIn Studio", subtitulo: "Imagen, copy y aprobación para posts")
                }
                .buttonStyle(.plain)

                separadorInterno

                Button {
                    mostrarEstudioDeContenido = true
                } label: {
                    filaInterna(icono: "wand.and.stars", titulo: "Crear contenido", subtitulo: "Posts, imágenes e ideas completas")
                }
                .buttonStyle(.plain)

                separadorInterno

                NavigationLink {
                    RemotoView()
                } label: {
                    filaInterna(icono: "display", titulo: "Control remoto", subtitulo: "Usa tu computadora desde el iPhone")
                }
                .buttonStyle(.plain)

                separadorInterno

                NavigationLink {
                    CapabilitiesView()
                } label: {
                    filaInterna(icono: "sparkles.rectangle.stack.fill", titulo: "Capacidades", subtitulo: "Descubre todo lo que puede hacer")
                }
                .buttonStyle(.plain)

                separadorInterno

                NavigationLink {
                    UsoModelosView()
                } label: {
                    filaInterna(icono: "chart.bar.doc.horizontal.fill", titulo: "Uso de modelos", subtitulo: "Tokens y costo por LLM")
                }
                .buttonStyle(.plain)
            }
            .padding(16)
            .tarjetaVidrioFlotante(esquina: 20)
        }
    }

    // MARK: - Ajustes

    private var seccionAjustes: some View {
        VStack(alignment: .leading, spacing: 12) {
            encabezadoSeccion("AJUSTES")

            VStack(spacing: 0) {
                Button {
                    Task { await push.pedirPermiso() }
                } label: {
                    filaInterna(
                        icono: "bell.badge.fill",
                        titulo: "Avisos",
                        subtitulo: push.estado.texto,
                        deshabilitado: push.estado == .activo
                    )
                }
                .buttonStyle(.plain)
                .disabled(push.estado == .activo)

                separadorInterno

                NavigationLink {
                    SeguridadView()
                } label: {
                    filaInterna(icono: "lock.shield.fill", titulo: "Seguridad", subtitulo: "Cuentas, autonomía y freno de emergencia")
                }
                .buttonStyle(.plain)

                separadorInterno

                NavigationLink {
                    PrivacidadView()
                } label: {
                    filaInterna(icono: "hand.raised.fill", titulo: "Privacidad", subtitulo: "Exporta datos o elimina tu memoria")
                }
                .buttonStyle(.plain)
            }
            .padding(16)
            .tarjetaVidrioFlotante(esquina: 20)
        }
    }

    private func tarjetaActualizacion(_ update: IOSUpdateManifest) -> some View {
        Button {
            updates.markUpdateOpened(update)
            openURL(update.installURL)
        } label: {
            HStack(spacing: 14) {
                ZStack {
                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                        .fill(EdecanTheme.morado.opacity(0.13))
                        .frame(width: 48, height: 48)
                    Image(systemName: "arrow.down.app.fill")
                        .foregroundStyle(EdecanTheme.morado)
                }
                VStack(alignment: .leading, spacing: 3) {
                    Text("Actualización \(update.versionText)")
                        .font(.headline)
                        .foregroundStyle(.primary)
                    Text(
                        update.releaseNotes.isEmpty
                            ? "Hay una nueva versión de Edecán."
                            : update.releaseNotes
                    )
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)
                }
                Spacer(minLength: 4)
                Text(update.installKind.actionTitle)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(EdecanTheme.morado)
            }
            .padding(16)
            .tarjetaVidrioFlotante(esquina: 20)
        }
        .buttonStyle(.plain)
        .accessibilityHint("Abre el mecanismo oficial configurado para instalarla")
    }

    private var modoAvanzado: some View {
        DisclosureGroup {
            VStack(spacing: 0) {
                NavigationLink {
                    IDEView()
                } label: {
                    filaInterna(icono: "chevron.left.forwardslash.chevron.right", titulo: "Construir con Edecán", subtitulo: "Código, sitios y aplicaciones")
                }
                .buttonStyle(.plain)

                separadorInterno

                NavigationLink {
                    NegociosView()
                } label: {
                    filaInterna(icono: "chart.pie.fill", titulo: "Negocios", subtitulo: "Decisiones, métricas y estrategia")
                }
                .buttonStyle(.plain)
            }
            .padding(.top, 8)
        } label: {
            Text("Modo avanzado")
                .font(.subheadline.weight(.semibold))
        }
        .tint(EdecanTheme.morado)
        .padding(16)
        .tarjetaVidrio(esquina: 18)
    }

    // MARK: - Componentes

    private func encabezadoSeccion(_ titulo: String) -> some View {
        Text(titulo)
            .font(.caption.weight(.bold))
            .foregroundStyle(.secondary)
            .tracking(0.6)
            .padding(.horizontal, 4)
    }

    private func filaTarjeta(icono: String, titulo: String, subtitulo: String) -> some View {
        HStack(spacing: 14) {
            iconoCuadrado(icono)
            VStack(alignment: .leading, spacing: 3) {
                Text(titulo)
                    .font(.headline)
                    .foregroundStyle(.primary)
                Text(subtitulo)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 4)
            Image(systemName: "chevron.right")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.tertiary)
        }
        .padding(16)
        .tarjetaVidrioFlotante(esquina: 20)
    }

    private func filaInterna(
        icono: String,
        titulo: String,
        subtitulo: String,
        deshabilitado: Bool = false
    ) -> some View {
        HStack(spacing: 12) {
            iconoCuadrado(icono)
            VStack(alignment: .leading, spacing: 2) {
                Text(titulo)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(deshabilitado ? .secondary : .primary)
                Text(subtitulo)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)
            }
            Spacer(minLength: 8)
            if !deshabilitado {
                Image(systemName: "chevron.right")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.tertiary)
            }
        }
        .contentShape(Rectangle())
        .padding(.vertical, 4)
    }

    private func iconoCuadrado(_ icono: String) -> some View {
        ZStack {
            RoundedRectangle(cornerRadius: 11, style: .continuous)
                .fill(EdecanTheme.morado.opacity(0.12))
                .frame(width: 42, height: 42)
            Image(systemName: icono)
                .foregroundStyle(EdecanTheme.morado)
        }
    }

    private var separadorInterno: some View {
        Divider().padding(.vertical, 10)
    }

    private var version: some View {
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? ""
        let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? ""
        return Text("Edecán \(version) (\(build))")
            .font(.caption2)
            .foregroundStyle(.tertiary)
            .frame(maxWidth: .infinity)
            .padding(.bottom, 8)
            .accessibilityLabel("Versión \(version), compilación \(build)")
    }

    private var inicialDePersona: String {
        let nombre = nombreMostrado.trimmingCharacters(in: .whitespacesAndNewlines)
        return String(nombre.first ?? "T").uppercased()
    }

    private var nombreMostrado: String {
        let elegido = perfilVivo?.datos.identidad.nombrePreferido
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if !elegido.isEmpty { return elegido }
        return session.me?.nombrePila.capitalized ?? "Tu perfil"
    }

    private func cargarPerfilCompleto() async {
        await session.cargarMe()
        guard let client = session.client else { return }
        do {
            perfilVivo = try await client.perfilVivo()
        } catch is CancellationError {
            return
        } catch {
            // El encabezado sigue funcionando con /v1/me.
        }
    }

    private func cerrarSesion() {
        let deviceId = pairingStore.deviceId
        pairingStore.olvidarEmparejamiento()
        Task {
            await push.revocar()
            await session.cerrarSesion(deviceId: deviceId)
        }
    }
}
