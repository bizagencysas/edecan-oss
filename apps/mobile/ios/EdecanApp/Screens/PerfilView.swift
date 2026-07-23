import SwiftUI
import EdecanKit

/// El espacio personal de la app móvil.
///
/// Las credenciales, proveedores y demás infraestructura se configuran en la
/// computadora donde vive Edecán. El iPhone muestra a la persona, su asistente
/// y accesos útiles; nunca intenta editar secretos ni convierte el perfil en un
/// panel técnico.
struct PerfilView: View {
    @Environment(PairingStore.self) private var pairingStore
    @Environment(SessionStore.self) private var session
    @Environment(TabRouter.self) private var router
    @Environment(PushNotificationCoordinator.self) private var push
    @State private var mostrarConfirmacionSalir = false
    @State private var mostrarEstudioDeContenido = false
    @State private var perfilVivo: LiveProfile?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 20) {
                    tarjetaDePersona
                    perfilPersonal
                    tarjetaDeEdecan
                    tarjetaNotificaciones
                    herramientas
                    modoAvanzado
                    version

                    Button("Desvincular este iPhone", role: .destructive) {
                        mostrarConfirmacionSalir = true
                    }
                    .buttonStyle(.bordered)
                }
                .padding()
            }
            .background(EdecanTheme.degradado.opacity(0.05).ignoresSafeArea())
            .navigationTitle("Tú")
            .task { await cargarPerfilCompleto() }
            .refreshable { await cargarPerfilCompleto() }
            .sheet(isPresented: $mostrarEstudioDeContenido) {
                ContentStudioView()
                    .environment(router)
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

    private var tarjetaDePersona: some View {
        VStack(spacing: 10) {
            ZStack {
                Circle()
                    .fill(EdecanTheme.degradado)
                    .frame(width: 68, height: 68)
                Text(inicialDePersona)
                    .font(.title.bold())
                    .foregroundStyle(.white)
            }

            if let me = session.me {
                Text(nombreMostrado)
                    .font(.title3.bold())
                Text(me.user.email)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                Text(me.tenant.name)
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            } else if session.cargandoMe {
                ProgressView()
            } else {
                Text("Tu perfil")
                    .font(.headline)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(24)
        .tarjetaVidrio(esquina: 22)
    }

    private var perfilPersonal: some View {
        NavigationLink {
            PerfilEditorView { actualizado in
                perfilVivo = actualizado
            }
        } label: {
            HStack(spacing: 14) {
                ZStack {
                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                        .fill(EdecanTheme.morado.opacity(0.13))
                        .frame(width: 48, height: 48)
                    Image(systemName: "person.text.rectangle.fill")
                        .foregroundStyle(EdecanTheme.morado)
                }
                VStack(alignment: .leading, spacing: 3) {
                    Text("Perfil")
                        .font(.headline)
                        .foregroundStyle(.primary)
                    Text("Tu nombre, contexto y cómo quieres que Edecán te hable")
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
            .tarjetaVidrio(esquina: 20)
        }
        .buttonStyle(.plain)
    }

    private var tarjetaDeEdecan: some View {
        HStack(spacing: 16) {
            ZStack {
                RoundedRectangle(cornerRadius: 18, style: .continuous)
                    .fill(EdecanTheme.degradado)
                    .frame(width: 64, height: 64)
                Image(systemName: "headphones")
                    .font(.system(size: 28, weight: .semibold))
                    .foregroundStyle(.white)
            }

            VStack(alignment: .leading, spacing: 5) {
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
                Text("Tu asistente personal para pensar, crear, organizar y hacer.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Spacer(minLength: 0)
        }
        .padding(18)
        .tarjetaVidrio(esquina: 20)
    }

    private var tarjetaNotificaciones: some View {
        Button {
            Task { await push.pedirPermiso() }
        } label: {
            HStack(spacing: 12) {
                Image(systemName: "bell.badge.fill")
                    .foregroundStyle(EdecanTheme.morado)
                    .frame(width: 42, height: 42)
                    .background(EdecanTheme.morado.opacity(0.12), in: RoundedRectangle(cornerRadius: 11))
                VStack(alignment: .leading, spacing: 2) {
                    Text("Avisos")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(.primary)
                    Text(push.estado.texto)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.leading)
                }
                Spacer()
            }
            .padding(16)
            .tarjetaVidrio(esquina: 20)
        }
        .buttonStyle(.plain)
        .disabled(push.estado == .activo)
    }

    private var herramientas: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("TU EDECÁN")
                .font(.caption.weight(.bold))
                .foregroundStyle(.secondary)
                .padding(.bottom, 12)

            Button {
                mostrarEstudioDeContenido = true
            } label: {
                fila(icono: "wand.and.stars", titulo: "Crear contenido", subtitulo: "Posts, imágenes e ideas completas")
            }
            .buttonStyle(.plain)

            separador

            NavigationLink {
                RemotoView()
            } label: {
                fila(icono: "display", titulo: "Control remoto", subtitulo: "Usa tu computadora desde el iPhone")
            }
            .buttonStyle(.plain)

            separador

            NavigationLink {
                CapabilitiesView()
            } label: {
                fila(icono: "sparkles.rectangle.stack.fill", titulo: "Capacidades", subtitulo: "Descubre todo lo que puede hacer")
            }
            .buttonStyle(.plain)
        }
        .padding(16)
        .tarjetaVidrio(esquina: 20)
    }

    private var modoAvanzado: some View {
        DisclosureGroup("Modo avanzado") {
            VStack(spacing: 0) {
                NavigationLink {
                    IDEView()
                } label: {
                    fila(icono: "chevron.left.forwardslash.chevron.right", titulo: "Construir con Edecán", subtitulo: "Código, sitios y aplicaciones")
                }
                .buttonStyle(.plain)

                separador

                NavigationLink {
                    NegociosView()
                } label: {
                    fila(icono: "chart.pie.fill", titulo: "Negocios", subtitulo: "Decisiones, métricas y estrategia")
                }
                .buttonStyle(.plain)
            }
            .padding(.top, 12)
        }
        .tint(EdecanTheme.morado)
        .padding(16)
        .tarjetaVidrio(esquina: 18)
    }

    private func fila(icono: String, titulo: String, subtitulo: String) -> some View {
        HStack(spacing: 12) {
            ZStack {
                RoundedRectangle(cornerRadius: 11, style: .continuous)
                    .fill(EdecanTheme.morado.opacity(0.12))
                    .frame(width: 42, height: 42)
                Image(systemName: icono)
                    .foregroundStyle(EdecanTheme.morado)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(titulo)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.primary)
                Text(subtitulo)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
            Spacer(minLength: 8)
            Image(systemName: "chevron.right")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.tertiary)
        }
        .contentShape(Rectangle())
        .padding(.vertical, 4)
    }

    private var separador: some View {
        Divider().padding(.vertical, 10)
    }

    private var version: some View {
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? ""
        let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? ""
        return Text("Edecán \(version) (\(build))")
            .font(.caption2)
            .foregroundStyle(.tertiary)
            .frame(maxWidth: .infinity)
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
            // El encabezado sigue funcionando con /v1/me. El editor mostrará
            // el error concreto si la persona decide abrirlo.
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
