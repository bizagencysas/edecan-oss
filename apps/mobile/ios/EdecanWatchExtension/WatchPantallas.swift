import SwiftUI

struct ResumenDiaView: View {
    @Environment(WatchSessionManager.self) private var manager
    @Environment(WatchSalud.self) private var salud
    @Environment(AguaStore.self) private var agua

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                Text(saludo)
                    .font(.headline)
                Text(salud.resumenDelDia(manager: manager, agua: agua))
                    .font(.caption)
                    .fixedSize(horizontal: false, vertical: true)

                HStack {
                    metrica("\(salud.pasos?.formatted() ?? "—")", "pasos")
                    metrica(salud.bpm.map(String.init) ?? "—", "lpm")
                    metrica(String(format: "%.1f", agua.litros), "L")
                }

                if manager.enLlamada {
                    Label(manager.nombreLlamada ?? "En llamada", systemImage: "phone.fill")
                        .font(.caption)
                        .foregroundStyle(.orange)
                }
                if let aviso = manager.proximoAviso {
                    Button("Hecho: \(aviso.mensaje)") { manager.completarAviso(aviso.id) }
                        .font(.caption)
                }
            }
            .padding(.horizontal, 4)
        }
        .navigationTitle("Hoy")
        .task { await salud.refrescar() }
        .refreshable { await salud.refrescar() }
    }

    private func metrica(_ valor: String, _ etiqueta: String) -> some View {
        VStack {
            Text(valor).font(.caption.weight(.bold)).monospacedDigit()
            Text(etiqueta).font(.caption2).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
    }

    private var saludo: String {
        let hora = Calendar.current.component(.hour, from: Date())
        switch hora {
        case 5..<12: return "Buenos días"
        case 12..<19: return "Buenas tardes"
        default: return "Buenas noches"
        }
    }
}

struct SaludView: View {
    @Environment(WatchSalud.self) private var salud

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                if salud.sinPermiso {
                    Text("Activa Salud para Edecán en Ajustes del reloj.")
                        .font(.caption)
                }

                bloque("Pasos", valor: salud.pasos.map { $0.formatted() } ?? "—", progreso: salud.progresoPasos, meta: "\(salud.metaPasos.formatted())", tint: .green)
                bloque("Mover", valor: salud.kcal.map { "\(Int($0.rounded())) kcal" } ?? "—", progreso: salud.progresoKcal, meta: "\(Int(salud.metaKcal)) kcal", tint: .red)
                bloque("Ejercicio", valor: salud.minutosEjercicio.map { "\($0) min" } ?? "—", progreso: salud.progresoEjercicio, meta: "\(salud.metaEjercicio) min", tint: .green)
                bloque("Pie", valor: salud.horasPie.map { "\($0) h" } ?? "—", progreso: salud.progresoPie, meta: "\(salud.metaPie) h", tint: .cyan)

                if let bpm = salud.bpm {
                    Label("\(bpm) lpm ahora", systemImage: "heart.fill")
                        .foregroundStyle(.red)
                }
                if let km = salud.distanciaKm {
                    Label(String(format: "%.2f km", km), systemImage: "figure.walk")
                }
                if let pisos = salud.pisos, pisos > 0 {
                    Label("\(pisos) pisos", systemImage: "stairs")
                }
                if let sueno = salud.suenoHoras {
                    Label(String(format: "Sueño %.1f h", sueno), systemImage: "bed.double.fill")
                }
                if let reposo = salud.reposo {
                    Label("Reposo \(reposo) lpm", systemImage: "heart.circle")
                }
                if let hrv = salud.hrv {
                    Label(String(format: "HRV %.0f ms", hrv), systemImage: "waveform.path.ecg")
                }
                if let ox = salud.oxigeno {
                    Label("SpO₂ \(ox)%", systemImage: "lungs.fill")
                }
            }
            .padding(.horizontal, 4)
        }
        .navigationTitle("Salud")
        .task { await salud.refrescar() }
        .refreshable { await salud.refrescar() }
    }

    private func bloque(_ titulo: String, valor: String, progreso: Double, meta: String, tint: Color) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(titulo).font(.caption2)
                Spacer()
                Text(valor).font(.caption.weight(.semibold)).monospacedDigit()
            }
            ProgressView(value: progreso).tint(tint)
            Text("meta \(meta)").font(.caption2).foregroundStyle(.secondary)
        }
    }
}

struct HabitosView: View {
    @Environment(AguaStore.self) private var agua
    @Environment(WatchSalud.self) private var salud
    @State private var finRespiro: Date?
    @State private var inicioRespiro: Date?

    var body: some View {
        List {
            Section("Recordatorios del reloj") {
                Button("Dormir a las 22:30") {
                    agua.avisarALas(hora: 22, minuto: 30, id: "sueno", titulo: "Hora de dormir", cuerpo: "Edecán: cierra el día. El cuerpo te lo pide.")
                }
                Button("Levantarme cada hora") {
                    agua.avisarALas(hora: Calendar.current.component(.hour, from: Date()) + 1, minuto: 0, id: "pie", titulo: "Ponte de pie", cuerpo: "Un minuto de pie. El reloj te vuelve a llamar.")
                }
                Button("Estirar en 45 min") {
                    agua.avisarEn(minutos: 45, mensaje: "Estira cuello y cadera un minuto.")
                }
            }
            Section("Respirar") {
                if let fin = finRespiro, fin > Date.now {
                    Text(timerInterval: Date.now...fin, countsDown: true, showsHours: false)
                        .font(.title2.monospacedDigit())
                    Text("Inhala 4 · aguanta 4 · exhala 4")
                        .font(.caption2)
                } else {
                    Button("Un minuto de calma") {
                        inicioRespiro = Date.now
                        finRespiro = Date.now.addingTimeInterval(60)
                    }
                }
            }
        }
        .navigationTitle("Hábitos")
        .onChange(of: finRespiro) { _, nuevo in
            guard let nuevo, nuevo <= Date.now, let inicio = inicioRespiro else { return }
            Task { await salud.guardarMindful(desde: inicio, hasta: Date()) }
        }
        .task(id: finRespiro) {
            guard let fin = finRespiro, let inicio = inicioRespiro else { return }
            let espera = fin.timeIntervalSinceNow
            guard espera > 0 else { return }
            try? await Task.sleep(for: .seconds(espera))
            await salud.guardarMindful(desde: inicio, hasta: Date())
            inicioRespiro = nil
            finRespiro = nil
        }
    }
}

struct EdecanHablarView: View {
    @Environment(WatchSessionManager.self) private var manager
    @State private var texto = ""
    @State private var mision = ""

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 8) {
                TextField("Dile algo a Edecán", text: $texto)
                Button("Enviar") {
                    let t = texto.trimmingCharacters(in: .whitespacesAndNewlines)
                    guard !t.isEmpty else { return }
                    manager.hablar(t)
                    texto = ""
                }
                .disabled(texto.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)

                ForEach(["¿Qué toca ahora?", "Estoy ocupado, guarda esto", "Resume mi día", "No molestar 1 hora"], id: \.self) { chip in
                    Button(chip) { manager.hablar(chip) }
                        .font(.caption)
                }

                Button("Deshacer lo último") { manager.deshacer() }
                    .font(.caption)
                    .tint(.orange)

                TextField("Nueva misión", text: $mision)
                Button("Crear misión") {
                    let t = mision.trimmingCharacters(in: .whitespacesAndNewlines)
                    guard !t.isEmpty else { return }
                    manager.crearMision(t)
                    mision = ""
                }
                .disabled(mision.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)

                if let r = manager.ultimaRespuesta, !r.isEmpty {
                    Text(r)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    Text("El iPhone entrega el recado al chat principal. La respuesta corta aparece aquí.")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
            .padding(.horizontal, 4)
        }
        .navigationTitle("Edecán")
    }
}

struct AprobacionesWatchView: View {
    @Environment(WatchSessionManager.self) private var manager

    var body: some View {
        List {
            if manager.aprobaciones.isEmpty {
                Text("Nada que aprobar.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(manager.aprobaciones) { item in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(item.nombre).font(.caption.weight(.semibold))
                        if !item.detalle.isEmpty {
                            Text(item.detalle).font(.caption2).foregroundStyle(.secondary)
                        }
                        HStack {
                            Button("Sí") { manager.decidirAprobacion(id: item.id, ok: true) }
                                .tint(.green)
                            Button("No") { manager.decidirAprobacion(id: item.id, ok: false) }
                                .tint(.red)
                        }
                    }
                }
            }
        }
        .navigationTitle("Aprobar")
    }
}

struct MisionesWatchView: View {
    @Environment(WatchSessionManager.self) private var manager
    @State private var steer = ""

    var body: some View {
        List {
            if manager.misiones.isEmpty {
                Text("Ninguna misión en curso.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(manager.misiones) { m in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(m.objetivo).font(.caption)
                        Text(etiqueta(m.status)).font(.caption2).foregroundStyle(.secondary)
                        if m.status == "waiting_confirmation" {
                            HStack {
                                Button("Sí") { manager.confirmarMision(id: m.id, ok: true) }
                                    .tint(.green)
                                Button("No") { manager.confirmarMision(id: m.id, ok: false) }
                                    .tint(.red)
                            }
                        }
                        if m.status == "paused" {
                            Button("Reanudar") { manager.reanudarMision(m.id) }
                        } else if m.status == "planning" || m.status == "running" || m.status == "waiting_confirmation" {
                            Button("Pausar") { manager.pausarMision(m.id) }
                        }
                        if m.status == "running" || m.status == "paused" || m.status == "waiting_confirmation" {
                            TextField("Dirigir", text: $steer)
                            Button("Enviar dirección") {
                                let t = steer.trimmingCharacters(in: .whitespacesAndNewlines)
                                guard !t.isEmpty else { return }
                                manager.dirigirMision(id: m.id, texto: t)
                                steer = ""
                            }
                        }
                        Button("Cancelar", role: .destructive) { manager.cancelarMision(m.id) }
                            .font(.caption2)
                    }
                }
            }
        }
        .navigationTitle("Misiones")
    }

    private func etiqueta(_ status: String) -> String {
        switch status {
        case "planning": return "Planificando"
        case "running": return "En curso"
        case "waiting_confirmation": return "Esperando tu sí"
        case "paused": return "Pausada"
        default: return status
        }
    }
}

struct RutinasWatchView: View {
    @Environment(WatchSessionManager.self) private var manager

    var body: some View {
        List {
            if manager.rutinas.isEmpty {
                Text("Sin rutinas en el iPhone.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(manager.rutinas) { r in
                    Toggle(r.nombre, isOn: Binding(
                        get: { r.enabled },
                        set: { manager.toggleRutina(id: r.id, enabled: $0) }
                    ))
                    .font(.caption)
                }
            }
        }
        .navigationTitle("Rutinas")
    }
}

struct EquipoWatchView: View {
    @Environment(WatchSessionManager.self) private var manager

    var body: some View {
        List {
            if manager.equipo.isEmpty {
                Text("Nadie en el equipo todavía.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(manager.equipo) { w in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(w.nombre).font(.caption)
                        Text(w.status).font(.caption2).foregroundStyle(.secondary)
                    }
                }
            }
        }
        .navigationTitle("Equipo")
    }
}

struct LlamadaWatchView: View {
    @Environment(WatchSessionManager.self) private var manager
    @State private var susurro = ""

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 8) {
                if manager.enLlamada {
                    Label(manager.nombreLlamada ?? "En curso", systemImage: "phone.fill")
                        .font(.headline)
                    if let turno = manager.turnoLlamada {
                        Text((manager.turnoRol == "assistant" || manager.turnoRol == "agent" ? "Agente: " : "Contacto: ") + turno)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    TextField("Mensaje para el agente", text: $susurro)
                    Button("Susurrar") {
                        let t = susurro.trimmingCharacters(in: .whitespacesAndNewlines)
                        guard !t.isEmpty else { return }
                        manager.susurrar(t)
                        susurro = ""
                    }
                    ForEach(["Que llame más tarde", "Estoy en una reunión", "Un minuto"], id: \.self) { frase in
                        Button(frase) { manager.susurrar(frase) }
                            .font(.caption)
                    }
                } else {
                    Text("No hay llamada en curso. Cuando el agente atienda, puedes enviarle contexto desde aquí.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .padding(.horizontal, 4)
        }
        .navigationTitle("Llamada")
    }
}
