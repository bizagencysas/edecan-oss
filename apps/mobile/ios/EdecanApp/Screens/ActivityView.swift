import SwiftUI
import EdecanKit

/// "Actividad" — registro reciente de lo que Edecan ejecutó o vigiló
/// (`GET /v1/activity`, contrato en paralelo). Línea de tiempo con icono +
/// resumen + estado; alcanzable desde Inicio y desde Equipo. Una ruta que
/// todavía no aterrizó degrada con "Próximamente" (directiva §153).
struct ActivityView: View {
    @Environment(SessionStore.self) private var session
    @State private var eventos: [ActivityEvent] = []
    @State private var cargando = true
    @State private var error: String?
    @State private var proximamente = false

    var body: some View {
        List {
            if let error {
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(.red)
                    .listRowSeparator(.hidden)
            }
            if proximamente {
                filaProximamente
            }
            if eventos.isEmpty && !cargando && !proximamente {
                filaEstadoVacio
            }
            ForEach(grupos) { grupo in
                Section(grupo.titulo) {
                    ForEach(grupo.eventos) { evento in
                        FilaActividad(evento: evento)
                            .listRowSeparator(.hidden)
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Actividad")
        .navigationBarTitleDisplayMode(.large)
        .overlay {
            if cargando && eventos.isEmpty {
                ProgressView()
            }
        }
        .task { await cargar() }
        .refreshable { await cargar() }
    }

    private var filaProximamente: some View {
        VStack(alignment: .leading, spacing: 6) {
            Label("Próximamente", systemImage: "hourglass")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(EdecanTheme.morado)
            Text("El registro de actividad está llegando al servidor. Vuelve en un momento.")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
        .padding(.vertical, 4)
        .listRowSeparator(.hidden)
    }

    /// Estado vacío amable: una línea corta + un atajo, no una ilustración.
    private var filaEstadoVacio: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Aún no hay actividad")
                .font(.subheadline.weight(.semibold))
            Text("Cuando encargues trabajo a un compañero, aquí verás qué hizo.")
                .font(.footnote)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            NavigationLink {
                MisionesView()
            } label: {
                Label("Encargar trabajo", systemImage: "point.3.filled.connected.trianglepath.dotted")
                    .font(.subheadline.weight(.medium))
            }
        }
        .padding(.vertical, 6)
        .listRowSeparator(.hidden)
    }

    /// Eventos agrupados por tiempo relativo ("Hoy", "Ayer", "Esta semana",
    /// "Anteriormente") — una línea de tiempo se lee mejor en bloques que
    /// como una pila plana de filas.
    private var grupos: [GrupoActividad] {
        var orden: [String] = []
        var porTitulo: [String: [ActivityEvent]] = [:]
        for evento in eventos {
            let titulo = tituloDe(evento)
            if porTitulo[titulo] == nil { orden.append(titulo) }
            porTitulo[titulo, default: []].append(evento)
        }
        let canonico = ["Hoy", "Ayer", "Esta semana", "Anteriormente", "Sin fecha"]
        orden.sort {
            (canonico.firstIndex(of: $0) ?? canonico.count) < (canonico.firstIndex(of: $1) ?? canonico.count)
        }
        return orden.compactMap { titulo in
            porTitulo[titulo].map { GrupoActividad(titulo: titulo, eventos: $0) }
        }
    }

    private func tituloDe(_ evento: ActivityEvent) -> String {
        guard let at = evento.at else { return "Sin fecha" }
        let calendario = Calendar.current
        if calendario.isDateInToday(at) { return "Hoy" }
        if calendario.isDateInYesterday(at) { return "Ayer" }
        if let hace7 = calendario.date(byAdding: .day, value: -6, to: Date()),
           at >= calendario.startOfDay(for: hace7) {
            return "Esta semana"
        }
        return "Anteriormente"
    }

    private func cargar() async {
        guard let client = session.client else {
            error = "No hay sesión activa."
            cargando = false
            return
        }
        cargando = eventos.isEmpty
        error = nil
        proximamente = false
        defer { cargando = false }
        do {
            eventos = try await client.listActivity()
        } catch let apiError as APIClient.APIError {
            if apiError.esProximamente {
                proximamente = true
            } else {
                self.error = apiError.localizedDescription
            }
        } catch {
            self.error = error.localizedDescription
        }
    }
}

/// Bloque de la línea de tiempo: un título relativo ("Hoy") y sus eventos.
private struct GrupoActividad: Identifiable {
    let titulo: String
    let eventos: [ActivityEvent]
    var id: String { titulo }
}

private struct FilaActividad: View {
    let evento: ActivityEvent

    private var estilo: EstiloActividad { EstiloActividad.de(evento) }

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: estilo.icono)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(estilo.color)
                .frame(width: 30, height: 30)
                .background(estilo.color.opacity(0.12), in: Circle())
            VStack(alignment: .leading, spacing: 3) {
                if let agente = evento.agent, !agente.isEmpty {
                    Text(agente)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(estilo.color)
                }
                Text(evento.summary.isEmpty ? evento.type : evento.summary)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                if let at = evento.at {
                    Text(relativo(at))
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
            Spacer(minLength: 8)
            if let status = evento.status, !status.isEmpty {
                Text(estilo.etiqueta(status))
                    .font(.caption2.weight(.medium))
                    .foregroundStyle(estilo.color)
            }
        }
        .padding(.vertical, 4)
    }

    /// Marca de tiempo corta y relativa ("Hace 5 min"); para lo que ya
    /// tiene más de una semana, la fecha sola. Coincide con la forma en que
    /// ``WorkersView`` presenta la última actividad de un compañero.
    private func relativo(_ fecha: Date) -> String {
        let segundos = Date().timeIntervalSince(fecha)
        guard segundos >= 0 else { return fecha.formatted(date: .abbreviated, time: .shortened) }
        if segundos < 60 { return "Hace un momento" }
        let minutos = Int(segundos / 60)
        if minutos < 60 { return "Hace \(minutos) min" }
        let horas = minutos / 60
        if horas < 24 { return "Hace \(horas) h" }
        let dias = horas / 24
        if dias < 7 { return "Hace \(dias) d" }
        return fecha.formatted(date: .abbreviated, time: .omitted)
    }
}

/// Icono, color y etiqueta de un `ActivityEvent`. El color lo manda el
/// `status` cuando se reconoce; si no, un icono por `type` con acento neutro.
private struct EstiloActividad {
    let icono: String
    let color: Color

    func etiqueta(_ status: String) -> String {
        switch status.lowercased() {
        case "done", "completed", "success", "ok": return "Listo"
        case "error", "failed", "failure": return "Falló"
        case "running", "active": return "En curso"
        case "pending", "waiting", "waiting_confirmation": return "Pendiente"
        case "cancelled": return "Cancelado"
        default: return status
        }
    }

    static func de(_ evento: ActivityEvent) -> EstiloActividad {
        let status = (evento.status ?? "").lowercased()
        if status.contains("error") || status.contains("fail") || status.contains("cancelled") {
            return EstiloActividad(icono: "exclamationmark.triangle.fill", color: .red)
        }
        if status.contains("done") || status.contains("completed") || status.contains("success") || status == "ok" {
            return EstiloActividad(icono: "checkmark.circle.fill", color: .green)
        }
        if status.contains("running") || status == "active" {
            return EstiloActividad(icono: icono(porTipo: evento.type), color: EdecanTheme.morado)
        }
        if status.contains("pending") || status.contains("waiting") {
            return EstiloActividad(icono: icono(porTipo: evento.type), color: .orange)
        }
        return EstiloActividad(icono: icono(porTipo: evento.type), color: EdecanTheme.azul)
    }

    private static func icono(porTipo type: String) -> String {
        let t = type.lowercased()
        if t.contains("automat") || t.contains("routine") || t.contains("automation") {
            return "bolt.badge.clock.fill"
        }
        if t.contains("worker") || t.contains("agent") || t.contains("task") {
            return "person.fill"
        }
        if t.contains("mission") || t.contains("mision") || t.contains("goal") {
            return "point.3.filled.connected.trianglepath.dotted"
        }
        if t.contains("call") || t.contains("llamada") || t.contains("phone") {
            return "phone.fill"
        }
        if t.contains("approval") || t.contains("handoff") {
            return "hand.raised.fill"
        }
        if t.contains("computer") || t.contains("session") || t.contains("remote") {
            return "desktopcomputer"
        }
        if t.contains("connector") || t.contains("mcp") {
            return "cable.connector"
        }
        return "sparkles"
    }
}