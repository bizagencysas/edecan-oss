import SwiftUI
import WatchKit

/// Entrenamiento en el Watch. La corona ajusta kilos o reps; el botón manda
/// esos números al iPhone (`gymRegistrarSerie`).
struct ContentView: View {
    @Environment(WatchSessionManager.self) private var manager
    @Environment(WatchEntrenamiento.self) private var entreno
    @State private var coronaEnPeso = true
    @State private var pesoKg = 0.0
    @State private var reps = 8.0
    @FocusState private var coronaActiva: Bool

    var body: some View {
        Group {
            if let restante = manager.descansoRestante, restante > 0 {
                descanso(restante)
            } else if manager.mostrarLiveEntrenamiento {
                sesion
            } else {
                sinSesion
            }
        }
        .onAppear { aplicarSugeridos() }
        .onChange(of: manager.ejercicioActual) { _, _ in aplicarSugeridos() }
        .onChange(of: manager.pesoSugerido) { _, _ in aplicarSugeridos() }
        .onChange(of: manager.repsSugeridas) { _, _ in aplicarSugeridos() }
    }

    private var bpm: Double? { entreno.bpm ?? manager.frecuenciaCardiaca }
    private var kcal: Double? { entreno.kcal ?? manager.calorias }

    private var sinSesion: some View {
        VStack(spacing: 8) {
            Image(systemName: "figure.strengthtraining.traditional")
                .font(.title2)
                .foregroundStyle(.secondary)
            Text("Sin plan de hoy")
                .font(.headline)
            if manager.racha > 0 {
                Label("Racha \(manager.racha) sem", systemImage: "flame.fill")
                    .font(.caption)
                    .foregroundStyle(.orange)
            }
            Text("Si hay sesión en el iPhone, aparece aquí. También puedes empezar desde el reloj.")
                .font(.caption2)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            Button {
                manager.alternar()
            } label: {
                Label("Empezar", systemImage: "play.fill")
            }
            .tint(.green)
        }
        .padding(.horizontal, 4)
    }

    private var sesion: some View {
        VStack(spacing: 6) {
            Text(manager.tituloPlan ?? (manager.ejercicioActual ?? "Entrenamiento"))
                .font(.headline)
                .lineLimit(2)
                .multilineTextAlignment(.center)

            if let cronometro = manager.cronometro, manager.sesionActiva || entreno.activo {
                Text(Date.now.addingTimeInterval(-cronometro), style: .timer)
                    .font(.system(.title2, design: .monospaced))
                    .monospacedDigit()
            } else if manager.pausada {
                Text("Pausado")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else if !manager.sesionActiva, !entreno.activo {
                Text("Listo para empezar")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            HStack(spacing: 14) {
                if let frecuencia = bpm {
                    VStack(spacing: 2) {
                        Image(systemName: "heart.fill")
                            .foregroundStyle(.red)
                        Text("\(Int(frecuencia))")
                            .font(.title3.weight(.bold).monospacedDigit())
                        Text("LPM")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
                if let kcal {
                    VStack(spacing: 2) {
                        Image(systemName: "flame.fill")
                            .foregroundStyle(.orange)
                        Text("\(Int(kcal))")
                            .font(.title3.weight(.bold).monospacedDigit())
                        Text("kcal")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
            }

            if manager.sesionActiva || manager.pausada || entreno.activo {
                if let ej = manager.ejercicioActual, !ej.isEmpty {
                    Text("\(ej) · \(manager.seriesHechas)/\(max(manager.seriesTotales, 1))")
                        .font(.caption.weight(.semibold))
                        .lineLimit(2)
                        .multilineTextAlignment(.center)
                }
                if manager.pesoSugerido != nil || manager.repsSugeridas != nil {
                    let kg = manager.pesoSugerido.map { String(format: "%.1f kg", $0) } ?? "— kg"
                    let reps = manager.repsSugeridas.map { "\($0) reps" } ?? "— reps"
                    Text("\(kg) · \(reps)")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }

            if manager.sesionActiva {
                HStack {
                    Button("kg") { coronaEnPeso = true; coronaActiva = true }
                        .tint(coronaEnPeso ? .green : .secondary)
                    Button("reps") { coronaEnPeso = false; coronaActiva = true }
                        .tint(!coronaEnPeso ? .green : .secondary)
                }
                .controlSize(.mini)
                Text(coronaEnPeso ? String(format: "%.1f kg", pesoKg) : "\(Int(reps.rounded())) reps")
                    .font(.title3.monospacedDigit())
                    .focusable()
                    .focused($coronaActiva)
                    .digitalCrownRotation(
                        valorCorona,
                        from: 0,
                        through: coronaEnPeso ? 300 : 40,
                        by: coronaEnPeso ? 0.5 : 1,
                        sensitivity: .medium,
                        isContinuous: false,
                        isHapticFeedbackEnabled: true
                    )
                Text("Gira la corona")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }

            Button {
                manager.alternar()
            } label: {
                Label(
                    manager.sesionActiva ? "Pausar" : "Entrenar",
                    systemImage: manager.sesionActiva ? "pause.fill" : "play.fill"
                )
                .font(.headline)
                .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .tint(manager.sesionActiva ? .orange : .green)
            .controlSize(.large)

            if manager.sesionActiva {
                Button("Serie hecha") {
                    manager.registrarSerie(reps: Int(reps.rounded()), peso: pesoKg)
                }
                .buttonStyle(.bordered)
                .tint(.green)
                .controlSize(.small)
            }
            if manager.sesionActiva || manager.pausada {
                Button("Terminar") { manager.terminarGym() }
                    .tint(.red)
                    .controlSize(.small)
            }
        }
        .padding(.horizontal, 4)
    }

    private func descanso(_ restante: Int) -> some View {
        VStack(spacing: 8) {
            Image(systemName: "pause.circle.fill")
                .font(.title2)
                .foregroundStyle(.yellow)
            Text("Descanso · \(manager.descansoEjercicio ?? "")")
                .font(.headline)
                .lineLimit(2)
                .multilineTextAlignment(.center)
            if let fin = manager.descansoFin {
                Text(timerInterval: Date.now...fin, countsDown: true, showsHours: false)
                    .font(.system(.title, design: .rounded).weight(.bold))
                    .monospacedDigit()
            } else {
                Text("\(restante)")
                    .font(.system(.title, design: .rounded).weight(.bold))
                    .monospacedDigit()
            }
            Button("Saltar descanso") { manager.saltarDescanso() }
                .controlSize(.small)
            Button {
                manager.alternar()
            } label: {
                Label("Pausar sesión", systemImage: "pause.fill")
            }
            .tint(.orange)
            .controlSize(.small)
        }
        .padding(.horizontal, 4)
    }

    private var valorCorona: Binding<Double> {
        Binding(
            get: { coronaEnPeso ? pesoKg : reps },
            set: { nuevo in
                if coronaEnPeso {
                    pesoKg = nuevo
                } else {
                    reps = nuevo
                }
            }
        )
    }

    private func aplicarSugeridos() {
        if let p = manager.pesoSugerido, p > 0 { pesoKg = p }
        if let r = manager.repsSugeridas, r > 0 { reps = Double(r) }
    }
}
