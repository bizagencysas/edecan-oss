import EdecanKit
import SwiftUI

/// Tarjeta `gym_checkin` en el chat: la pregunta "¿Vas a ir al gym hoy?" con
/// botones Sí/No. Tocar uno llama a `APIClient.gymCheckin(respuesta:)` con la
/// respuesta que corresponde a la `accion` del botón (`gym_yes`/`gym_no`).
///
/// Persistencia de la respuesta (bug "la tarjeta reaparece al reabrir"):
/// - El servidor guarda el check-in (`/plan/today` → `checkin_hoy`) y es la
///   fuente de verdad.
/// - Al responder, además se persiste el día en `GymCheckinEstadoLocal`
///   (UserDefaults, namespaced por usuario) para no depender de la red al
///   reabrir.
/// - Al aparecer, la tarjeta de HOY restaura ese estado: flag local primero
///   (sin red) y, si no hay, `checkin_hoy` del servidor (cubre
///   re-instalación/otro dispositivo). La tarjeta NUNCA se marca respondida
///   antes del tap: solo tras un check-in aceptado o ya existente.
struct GymCheckinCardView: View {
    let bloque: GymCheckinBlock
    let client: APIClient?
    /// `createdAt` del mensaje que trae este bloque: solo la tarjeta de HOY
    /// consulta el estado del check-in del día. Las tarjetas viejas del
    /// historial se muestran como quedaron (sin auto-marcarse).
    let fechaMensaje: Date?

    @State private var respondido = false
    @State private var enviando = false
    @State private var aviso: String?
    @State private var plan: GymPlan?
    @State private var usuarioID: String?

    /// Flag local persistido por usuario y día (UserDefaults real de la app).
    private let estadoLocal = GymCheckinEstadoLocal()

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
        .task {
            await restaurarEstadoRespondido()
        }
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

    /// Solo la tarjeta del mensaje de HOY refleja el check-in de hoy. Un
    /// mensaje aún no persistido (`createdAt == nil`, se está transmitiendo)
    /// también cuenta como "de hoy".
    private var esDeHoy: Bool {
        guard let fechaMensaje else { return true }
        return Calendar.current.isDateInToday(fechaMensaje)
    }

    private func restaurarEstadoRespondido() async {
        guard esDeHoy, !respondido, let client else { return }
        if usuarioID == nil {
            usuarioID = (try? await client.me())?.user.id
        }
        // 1) Flag local: sobrevive al cierre de la app aunque no haya red.
        if let uid = usuarioID,
            let respuesta = estadoLocal.respuesta(
                dia: estadoLocal.diaISO(), usuarioID: uid
            ) {
            if respuesta == "si" || respuesta == "no" {
                respondido = true
                return
            }
        }
        // 2) Servidor: `checkin_hoy` es la fuente de verdad y cubre
        //    re-instalación, otro dispositivo o un flag local ajeno.
        if let checkin = try? await client.gymCheckinDeHoy() {
            respondido = true
            if let uid = usuarioID {
                estadoLocal.marcar(
                    respuesta: checkin.respuesta,
                    dia: estadoLocal.diaISO(),
                    usuarioID: uid
                )
            }
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
                // Persistir SOLO tras un check-in aceptado por el servidor:
                // la tarjeta no puede desaparecer antes de que la respuesta
                // exista. Sin `usuarioID` (me() caído), el estado queda en
                // memoria y la próxima apertura lo restaura `checkin_hoy`.
                if usuarioID == nil {
                    usuarioID = (try? await client.me())?.user.id
                }
                if let uid = usuarioID {
                    estadoLocal.marcar(
                        respuesta: respuesta,
                        dia: estadoLocal.diaISO(),
                        usuarioID: uid
                    )
                }
                aviso = out.message.isEmpty ? nil : out.message
                if respuesta == "si" { plan = out.plan }
            } catch {
                aviso = error.localizedDescription
            }
            enviando = false
        }
    }
}