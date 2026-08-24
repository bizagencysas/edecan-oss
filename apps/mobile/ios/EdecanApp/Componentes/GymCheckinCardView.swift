import EdecanKit
import SwiftUI

/// Tarjeta `gym_checkin` en el chat: la pregunta "¿Vas a ir al gym hoy?" con
/// botones Sí/No. Tocar uno llama a `APIClient.gymCheckin(respuesta:)` con la
/// respuesta que corresponde a la `accion` del botón (`gym_yes`/`gym_no`).
/// Una vez respondida queda deshabilitada, igual que ``QuestionCardView``.
struct GymCheckinCardView: View {
    let bloque: GymCheckinBlock
    let client: APIClient?

    @State private var respondido = false
    @State private var enviando = false
    @State private var aviso: String?
    @State private var plan: GymPlan?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if respondido {
                Label("Respondida", systemImage: "checkmark.circle.fill")
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(.green)
            }

            if !bloque.titulo.isEmpty {
                Text(bloque.titulo)
                    .font(.subheadline.weight(.semibold))
                    .fixedSize(horizontal: false, vertical: true)
            }

            ViewThatFits(in: .horizontal) {
                HStack(spacing: 8) { botones }
                VStack(alignment: .leading, spacing: 8) { botones }
            }

            if let aviso, !aviso.isEmpty {
                Text(aviso)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            if let plan, respondido {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(Array(plan.exercises.enumerated()), id: \.offset) { indice, ejercicio in
                        HStack {
                            Text("\(indice + 1). \(ejercicio.name)")
                                .font(.footnote.weight(.medium))
                            Spacer()
                            Text("\(ejercicio.sets) × \(ejercicio.repetitions)")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    Text("Para el collage y registrar las series: Actividad → Entrenamiento.")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .padding(.top, 2)
                }
                .padding(.top, 2)
            }
        }
        .padding(14)
        .tarjetaVidrio(esquina: 16)
        .overlay(
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .strokeBorder(EdecanTheme.morado.opacity(respondido ? 0.12 : 0.32), lineWidth: 1.2)
        )
    }

    @ViewBuilder
    private var botones: some View {
        ForEach(bloque.botones.indices, id: \.self) { indice in
            let boton = bloque.botones[indice]
            Button {
                responder(boton)
            } label: {
                Text(boton.label.isEmpty ? "Responder" : boton.label)
                    .font(.footnote.weight(.semibold))
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 10)
                    .background(EdecanTheme.degradado, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                    .foregroundStyle(.white)
            }
            .buttonStyle(.plain)
            .disabled(respondido || enviando)
            .opacity(respondido || enviando ? 0.5 : 1)
        }
    }

    private func responder(_ boton: GymCheckinBoton) {
        let respuesta: String
        switch boton.accion {
        case "gym_yes": respuesta = "si"
        case "gym_no": respuesta = "no"
        default: return
        }
        guard let client else { return }
        enviando = true
        Task {
            do {
                let out = try await client.gymCheckin(respuesta: respuesta)
                respondido = true
                aviso = out.message.isEmpty ? nil : out.message
                if respuesta == "si" { plan = out.plan }
            } catch {
                aviso = error.localizedDescription
            }
            enviando = false
        }
    }
}