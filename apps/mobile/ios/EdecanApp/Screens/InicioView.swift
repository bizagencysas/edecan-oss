import SwiftUI
import EdecanKit

/// Pestaña "Inicio": saludo con el nombre de pila (`GET /v1/me`, mismo
/// criterio que usa el panel web) + accesos directos, como el mockup.
struct InicioView: View {
    @Environment(SessionStore.self) private var session
    @State private var mostrandoChat = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    saludo
                    accesosDirectos
                    if let error = session.errorMensaje {
                        Text(error)
                            .font(.footnote)
                            .foregroundStyle(.red)
                            .tarjetaVidrio(esquina: 14)
                    }
                }
                .padding()
            }
            .background(EdecanTheme.degradado.opacity(0.06).ignoresSafeArea())
            .navigationTitle("Inicio")
            .task { await session.cargarMe() }
            .refreshable { await session.cargarMe() }
        }
    }

    private var saludo: some View {
        VStack(alignment: .leading, spacing: 6) {
            if session.cargandoMe && session.me == nil {
                ProgressView()
            } else {
                Text("Hola, \(session.me?.nombrePila ?? "de nuevo") 👋")
                    .font(.largeTitle.weight(.bold))
            }
            if let tenant = session.me?.tenant {
                Text(tenant.name)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var accesosDirectos: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: 14)], spacing: 14) {
            AccesoDirecto(icono: "bubble.left.and.bubble.right.fill", titulo: "Chat", subtitulo: "Habla con Edecán")
            NavigationLink {
                MisionesView()
            } label: {
                AccesoDirecto(
                    icono: "point.3.filled.connected.trianglepath.dotted",
                    titulo: "Misiones",
                    subtitulo: "Objetivos con sub-agentes"
                )
            }
            .buttonStyle(.plain)
            NavigationLink {
                AutomatizacionesView()
            } label: {
                AccesoDirecto(
                    icono: "bolt.badge.clock.fill",
                    titulo: "Automatizaciones",
                    subtitulo: "Reglas de agenda o webhook"
                )
            }
            .buttonStyle(.plain)
            NavigationLink {
                RecordatoriosView()
            } label: {
                AccesoDirecto(icono: "bell.badge.fill", titulo: "Recordatorios", subtitulo: "Pendientes y completados")
            }
            .buttonStyle(.plain)
            NavigationLink {
                RemotoView()
            } label: {
                AccesoDirecto(icono: "display", titulo: "Remoto", subtitulo: "Ver y controlar tu Mac/PC")
            }
            .buttonStyle(.plain)
            AccesoDirecto(icono: "dollarsign.circle.fill", titulo: "Finanzas", subtitulo: "Próximamente en la app")
            AccesoDirecto(icono: "doc.text.fill", titulo: "Archivos", subtitulo: "Próximamente en la app")
        }
    }
}

private struct AccesoDirecto: View {
    let icono: String
    let titulo: String
    let subtitulo: String

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Image(systemName: icono)
                .font(.title2)
                .foregroundStyle(EdecanTheme.degradado)
            Text(titulo)
                .font(.headline)
            Text(subtitulo)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .tarjetaVidrio(esquina: 18)
    }
}
