import SwiftUI
import EdecanKit

/// Control de gastos por LLM (`GET /v1/usage/modelos`): cuántos tokens de
/// entrada/salida, cuántas llamadas y cuánto costo tiene cada modelo
/// (Sol, Terra, Luna, Astra, Workers AI…). Vive en Perfil.
struct UsoModelosView: View {
    @Environment(SessionStore.self) private var session
    @State private var periodo: String = "30"
    @State private var modelos: [UsoModelo] = []
    @State private var cargando = true
    @State private var error: String?

    private let periodos: [(String, String)] = [
        ("7", "7 días"),
        ("30", "30 días"),
        ("todo", "Todo"),
    ]

    var body: some View {
        List {
            Section {
                Picker("Período", selection: $periodo) {
                    ForEach(periodos, id: \.0) { valor, titulo in
                        Text(titulo).tag(valor)
                    }
                }
                .pickerStyle(.segmented)
            }

            if let error {
                Section {
                    Text(error)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            } else if modelos.isEmpty && !cargando {
                Section {
                    Text("Todavía no hay uso de modelos en este período.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            } else {
                Section {
                    ForEach(modelos) { modelo in
                        filaModelo(modelo)
                    }
                } footer: {
                    if !modelos.isEmpty {
                        Text("Tokens de entrada y salida por vuelta del modelo, con su costo estimado.")
                    }
                }
            }

            if !modelos.isEmpty {
                Section("Total del período") {
                    totales
                }
            }
        }
        .navigationTitle("Uso de modelos")
        .navigationBarTitleDisplayMode(.inline)
        .overlay { if cargando && modelos.isEmpty { ProgressView() } }
        .task { await cargar() }
        .onChange(of: periodo) { _, _ in
            Task { await cargar() }
        }
        .refreshable { await cargar() }
    }

    private func filaModelo(_ modelo: UsoModelo) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(nombreBonito(modelo.model))
                    .font(.subheadline.weight(.semibold))
                Spacer()
                Text("\(modelo.llamadas) llamadas")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            HStack(spacing: 14) {
                metrica("Entrada", modelo.tokensEntrada)
                metrica("Salida", modelo.tokensSalida)
                metrica("Total", modelo.tokensTotales)
                if modelo.costoUsd > 0 {
                    metrica("Costo", nil, usd: modelo.costoUsd)
                }
            }
        }
        .padding(.vertical, 2)
    }

    private func metrica(_ titulo: String, _ cantidad: Int?, usd: Double? = nil) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(titulo)
                .font(.caption2)
                .foregroundStyle(.secondary)
            if let usd {
                Text(String(format: "$%.2f", usd))
                    .font(.footnote.weight(.medium))
                    .monospacedDigit()
            } else if let cantidad {
                Text(formatear(cantidad))
                    .font(.footnote.weight(.medium))
                    .monospacedDigit()
            }
        }
    }

    private var totales: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Entrada").font(.footnote).foregroundStyle(.secondary)
                Spacer()
                Text(formatear(modelos.reduce(0) { $0 + $1.tokensEntrada }))
                    .font(.footnote.weight(.semibold)).monospacedDigit()
            }
            HStack {
                Text("Salida").font(.footnote).foregroundStyle(.secondary)
                Spacer()
                Text(formatear(modelos.reduce(0) { $0 + $1.tokensSalida }))
                    .font(.footnote.weight(.semibold)).monospacedDigit()
            }
            HStack {
                Text("Costo").font(.footnote).foregroundStyle(.secondary)
                Spacer()
                Text(String(format: "$%.2f", modelos.reduce(0) { $0 + $1.costoUsd }))
                    .font(.footnote.weight(.semibold)).monospacedDigit()
            }
        }
    }

    private func nombreBonito(_ modelo: String) -> String {
        switch modelo {
        case "gpt-5.6-sol-2": return "Sol"
        case "gpt-5.6-terra", "gpt-5.6-terra-2": return "Terra"
        case "gpt-5.6-luna": return "Luna"
        case "gpt-6-astra": return "Astra"
        default: return modelo
        }
    }

    private func formatear(_ n: Int) -> String {
        if n >= 1_000_000 { return String(format: "%.1fM", Double(n) / 1_000_000) }
        if n >= 1_000 { return String(format: "%.1fk", Double(n) / 1_000) }
        return "\(n)"
    }

    private func cargar() async {
        guard let client = session.client else {
            error = "No hay sesión activa."
            cargando = false
            return
        }
        cargando = modelos.isEmpty
        error = nil
        defer { cargando = false }
        do {
            let salida = try await client.usoPorModelo(periodo: periodo)
            modelos = salida.modelos
        } catch {
            self.error = "No se pudo consultar el uso: \(error.localizedDescription)"
        }
    }
}