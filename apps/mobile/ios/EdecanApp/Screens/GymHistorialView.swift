import Charts
import EdecanKit
import Foundation
import SwiftUI

/// Historial del gimnasio: sesiones pasadas con sus series, racha en
/// semanas, peso corporal reciente y una sugerencia de readiness desde
/// HealthKit. Todo best-effort: si el backend no tiene sesiones o HealthKit
/// no devuelve datos, la pantalla degrada sin inventar nada.
struct GymHistorialView: View {
    @Environment(SessionStore.self) private var session
    @State private var health = HealthKitManager()
    @State private var sesiones: [GymSession] = []
    @State private var streak = 0
    @State private var peso: Double?
    @State private var pesos: [(fecha: Date, kg: Double)] = []
    @State private var readiness: String?
    @State private var cargando = false
    @State private var errorMensaje: String?

    private static let isoConFraccion: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        // El backend manda `started_at` con microsegundos; sin
        // `.withFractionalSeconds` el parseo devuelve `nil`.
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    /// Fallback sin fracción de segundo (algunos orígenes mandan el ISO pelado).
    private static let isoSimple = ISO8601DateFormatter()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                NavigationLink {
                    GymReporteSemanalView()
                } label: {
                    HStack(spacing: 12) {
                        Image(systemName: "chart.line.uptrend.xyaxis")
                            .font(.title2)
                            .foregroundStyle(EdecanTheme.morado)
                        VStack(alignment: .leading, spacing: 3) {
                            Text("📈 Reporte semanal de IA")
                                .font(.headline)
                            Text("Toca para ver tu progreso y recomendaciones")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Spacer(minLength: 0)
                        Image(systemName: "chevron.right")
                            .font(.caption)
                            .foregroundStyle(.tertiary)
                    }
                    .padding(16)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .tarjetaVidrio(esquina: 18)
                }
                .buttonStyle(.plain)

                if let errorMensaje {
                    Text(errorMensaje)
                        .font(.footnote)
                        .foregroundStyle(.red)
                        .padding(12)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .tarjetaVidrio(esquina: 14)
                }

                if cargando && sesiones.isEmpty {
                    ProgressView("Cargando historial…")
                        .frame(maxWidth: .infinity, minHeight: 180)
                } else if sesiones.isEmpty {
                    EmptyStateView(
                        icono: "figure.strengthtraining.traditional",
                        titulo: "Sin historial todavía",
                        descripcion: "Cuando termines tus primeras sesiones, aquí verás tu progreso."
                    )
                } else {
                    if streak > 0 {
                        tarjetaRacha
                    }
                    if let peso {
                        tarjetaPeso(peso)
                    }
                    tarjetaPesoHistorico
                    if let readiness {
                        tarjetaReadiness(readiness)
                    }
                    if let progreso = lineaProgreso() {
                        tarjetaProgreso(progreso)
                    }
                    ForEach(sesiones) { sesion in
                        tarjetaSesion(sesion)
                    }
                }
            }
            .padding()
        }
        .background(EdecanTheme.degradado.opacity(0.12).ignoresSafeArea())
        .navigationTitle("Historial")
        .navigationBarTitleDisplayMode(.inline)
        .task {
            await cargar()
        }
        .refreshable {
            await cargar()
        }
    }

    // MARK: - Tarjetas

    private var tarjetaRacha: some View {
        HStack(spacing: 10) {
            Text("🔥")
                .font(.title2)
            Text(streak == 1 ? "1 semana seguida" : "\(streak) semanas seguidas")
                .font(.headline)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .tarjetaVidrio(esquina: 18)
    }

    private func tarjetaPeso(_ peso: Double) -> some View {
        HStack(spacing: 10) {
            Image(systemName: "scalemass.fill")
                .foregroundStyle(EdecanTheme.morado)
            Text("Peso actual: \(textoPeso(peso)) kg")
                .font(.subheadline)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .tarjetaVidrio(esquina: 16)
    }

    private var tarjetaPesoHistorico: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Peso (últimos 90 días)")
                .font(.headline)
            if pesos.isEmpty {
                Text("todavía no hay suficientes mediciones")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            } else {
                Chart {
                    ForEach(pesos, id: \.fecha) { punto in
                        LineMark(
                            x: .value("Fecha", punto.fecha),
                            y: .value("kg", punto.kg)
                        )
                    }
                }
                .chartYAxis {
                    AxisMarks(position: .leading) { value in
                        AxisGridLine()
                        AxisValueLabel {
                            if let kg = value.as(Double.self) {
                                Text(kg, format: .number.precision(.fractionLength(1)))
                            }
                        }
                    }
                }
                .frame(height: 180)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .tarjetaVidrio(esquina: 16)
    }

    private func tarjetaReadiness(_ texto: String) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "heart.text.square.fill")
                .foregroundStyle(EdecanTheme.degradado)
            Text(texto)
                .font(.subheadline)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .tarjetaVidrio(esquina: 16)
    }

    private func tarjetaProgreso(_ texto: String) -> some View {
        HStack(spacing: 10) {
            Image(systemName: "arrow.up.right")
                .foregroundStyle(.green)
            Text(texto)
                .font(.subheadline.weight(.medium))
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .tarjetaVidrio(esquina: 16)
    }

    private func tarjetaSesion(_ sesion: GymSession) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Text(sesion.plan.title)
                    .font(.subheadline.weight(.semibold))
                Spacer()
                if let fecha = fechaDe(sesion.startedAt) {
                    Text(fecha, format: .dateTime.day().month(.abbreviated))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            if !sesion.plan.objective.isEmpty {
                Text(sesion.plan.objective)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            if sesion.series.isEmpty {
                Text("sin series registradas")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(lineasEjercicios(sesion), id: \.self) { linea in
                        Text(linea)
                            .font(.caption)
                    }
                }
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .tarjetaVidrio(esquina: 16)
    }

    // MARK: - Carga

    private func cargar() async {
        await health.solicitarAutorizacion()
        cargando = true
        defer { cargando = false }
        if let client = session.client {
            do {
                let out = try await client.gymHistorial(limit: 30)
                sesiones = out.sessions
                streak = out.streak
            } catch {
                errorMensaje = error.localizedDescription
            }
        }
        peso = await health.pesoReciente()
        pesos = await health.pesoHistorico()
        readiness = await health.readinessResumen()
    }

    // MARK: - Helpers

    /// Una línea por ejercicio del plan que tenga series registradas:
    /// "Sentadilla: 3 series · 40kg".
    private func lineasEjercicios(_ sesion: GymSession) -> [String] {
        let seriesPorEjercicio = Dictionary(grouping: sesion.series) { $0.exerciseIndex }
        return seriesPorEjercicio.keys.sorted().compactMap { indice in
            guard sesion.plan.exercises.indices.contains(indice) else { return nil }
            let series = seriesPorEjercicio[indice] ?? []
            let nombre = sesion.plan.exercises[indice].name
            let peso = series.compactMap { $0.weightKg }.last
            let conteo = series.count
            if let peso {
                return "\(nombre): \(conteo) series · \(textoPeso(peso))kg"
            }
            return "\(nombre): \(conteo) series"
        }
    }

    /// Progreso simple del primer ejercicio con series a lo largo de varias
    /// sesiones: "Sentadilla: 40kg → 45kg → 47.5kg". `nil` con una sola
    /// sesión o sin al menos dos pesos distintos registrados.
    private func lineaProgreso() -> String? {
        guard sesiones.count > 1 else { return nil }
        guard let primerIndice = sesiones
            .flatMap({ $0.series.map(\.exerciseIndex) })
            .min()
        else { return nil }
        let pesosPorSesion = sesiones.reversed().compactMap { sesion -> Double? in
            let series = sesion.series.filter { $0.exerciseIndex == primerIndice }
            return series.compactMap { $0.weightKg }.last
        }
        let pesos = pesosPorSesion.filter { $0 > 0 }
        guard pesos.count >= 2 else { return nil }
        let nombre = sesiones.first { $0.plan.exercises.indices.contains(primerIndice) }?
            .plan.exercises[primerIndice].name ?? "Progreso"
        let cadena = pesos.map { textoPeso($0) + "kg" }.joined(separator: " → ")
        return "\(nombre): \(cadena)"
    }

    /// "82.5" o "40" — sin decimales para kilos redondos, con uno para el resto.
    private func textoPeso(_ peso: Double) -> String {
        if peso == peso.rounded() {
            return String(format: "%.0f", peso)
        }
        return String(format: "%.1f", peso)
    }

    private func fechaDe(_ raw: String?) -> Date? {
        guard let raw else { return nil }
        return Self.isoConFraccion.date(from: raw) ?? Self.isoSimple.date(from: raw)
    }
}