import SwiftUI
import WatchKit

@main
struct EdecanWatchApp: App {
    @WKApplicationDelegateAdaptor(WatchAppDelegate.self) private var delegate
    @State private var sessionManager = WatchSessionManager()
    @State private var agua = AguaStore.compartido
    @State private var salud = WatchSalud.compartido
    @State private var entreno = WatchEntrenamiento.compartido

    var body: some Scene {
        WindowGroup {
            NavigationStack {
                HubWatchView()
            }
            .environment(sessionManager)
            .environment(agua)
            .environment(salud)
            .environment(entreno)
            .task {
                await agua.arrancar()
                await salud.arrancar()
            }
        }
    }
}

struct HubWatchView: View {
    @Environment(WatchSessionManager.self) private var manager
    @Environment(WatchSalud.self) private var salud
    @Environment(WatchEntrenamiento.self) private var entreno
    @Environment(AguaStore.self) private var agua
    @State private var mostrarGymLive = false

    var body: some View {
        List {
            Section {
                NavigationLink {
                    ResumenDiaView()
                } label: {
                    fila("sparkles", "Hoy", salud.pasos.map { "\($0.formatted()) pasos" } ?? "Resumen del día")
                }
                NavigationLink {
                    SaludView()
                } label: {
                    fila("heart.fill", "Salud", detalleSalud)
                }
                NavigationLink {
                    AguaView()
                } label: {
                    fila("drop.fill", "Agua", String(format: "%.1f L", agua.litros))
                }
                NavigationLink {
                    ContentView()
                } label: {
                    fila(
                        "figure.strengthtraining.traditional",
                        "Gym",
                        detalleGym
                    )
                }
            }
            Section {
                NavigationLink {
                    AvisosView()
                } label: {
                    fila("bell.fill", "Avisos", manager.avisosPendientes.isEmpty ? "Nada pendiente" : "\(manager.avisosPendientes.count)")
                }
                NavigationLink {
                    EdecanHablarView()
                } label: {
                    fila("waveform", "Edecán", "Hablarle o mandar un recado")
                }
                NavigationLink {
                    AprobacionesWatchView()
                } label: {
                    fila("checkmark.shield.fill", "Aprobar", manager.aprobaciones.isEmpty ? "Sin pendientes" : "\(manager.aprobaciones.count)")
                }
                NavigationLink {
                    MisionesWatchView()
                } label: {
                    fila(
                        "target",
                        "Misiones",
                        manager.misiones.isEmpty ? "Ninguna" : "\(manager.misiones.count)"
                    )
                }
                NavigationLink {
                    LlamadaWatchView()
                } label: {
                    fila("phone.fill", "Llamada", manager.enLlamada ? (manager.nombreLlamada ?? "En curso") : "Sin llamada")
                }
                NavigationLink {
                    RutinasWatchView()
                } label: {
                    fila("clock.arrow.2.circlepath", "Rutinas", manager.rutinas.isEmpty ? "Ninguna" : "\(manager.rutinasActivas) on")
                }
                NavigationLink {
                    EquipoWatchView()
                } label: {
                    fila("person.2.fill", "Equipo", manager.equipo.isEmpty ? "Nadie" : "\(manager.equipo.count)")
                }
                NavigationLink {
                    HabitosView()
                } label: {
                    fila("moon.zzz.fill", "Hábitos", "Dormir, pie, respirar")
                }
            }
        }
        .navigationTitle("Edecán")
        .navigationDestination(isPresented: $mostrarGymLive) {
            ContentView()
        }
        .onChange(of: manager.mostrarLiveEntrenamiento) { _, visible in
            if visible { mostrarGymLive = true }
        }
        .onChange(of: entreno.activo) { _, activo in
            if activo { mostrarGymLive = true }
        }
        .onReceive(NotificationCenter.default.publisher(for: .edecanWatchMostrarGymLive)) { _ in
            mostrarGymLive = true
        }
    }

    private var detalleGym: String {
        if manager.sesionActiva || entreno.activo {
            if let bpm = entreno.bpm ?? manager.frecuenciaCardiaca {
                return "\(Int(bpm)) LPM · en vivo"
            }
            return "En vivo"
        }
        if manager.pausada { return "Pausado" }
        return manager.tituloPlan ?? "Entrenamiento"
    }

    private var detalleSalud: String {
        var bits: [String] = []
        if let p = salud.pasos { bits.append("\(p.formatted()) pasos") }
        if let b = salud.bpm { bits.append("\(b) lpm") }
        return bits.isEmpty ? "Pasos, sueño, movimiento" : bits.joined(separator: " · ")
    }

    private func fila(_ icono: String, _ titulo: String, _ sub: String) -> some View {
        Label {
            VStack(alignment: .leading, spacing: 2) {
                Text(titulo)
                Text(sub)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
        } icon: {
            Image(systemName: icono)
        }
    }
}
