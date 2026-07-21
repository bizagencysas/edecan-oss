import SwiftUI
import EdecanKit

/// Actividad: accesos simples al trabajo que Edecan ejecuta o vigila.
struct InicioView: View {
    @Environment(SessionStore.self) private var session

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    introduccion
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
            .navigationTitle("Actividad")
            .task { await session.cargarMe() }
            .refreshable { await session.cargarMe() }
        }
    }

    private var introduccion: some View {
        VStack(alignment: .leading, spacing: 6) {
            if session.cargandoMe && session.me == nil {
                ProgressView()
            } else {
                Text("Todo lo que Edecan está haciendo")
                    .font(.title2.weight(.bold))
                Text("Revisa avances, decisiones pendientes y rutinas desde un solo lugar.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var accesosDirectos: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: 14)], spacing: 14) {
            NavigationLink {
                MisionesView()
            } label: {
                AccesoDirecto(
                    icono: "point.3.filled.connected.trianglepath.dotted",
                    titulo: "Trabajo delegado",
                    subtitulo: "Objetivos y aprobaciones"
                )
            }
            .buttonStyle(.plain)
            NavigationLink {
                AutomatizacionesView()
            } label: {
                AccesoDirecto(
                    icono: "bolt.badge.clock.fill",
                    titulo: "Rutinas",
                    subtitulo: "Acciones programadas"
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
