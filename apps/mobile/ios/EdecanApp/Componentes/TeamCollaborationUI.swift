import EdecanKit
import SwiftUI

// MARK: - Barra de actividad paralela (Mac del dueño, no VMs por bot)

/// Muestra qué bots del equipo están activos en la Mac del dueño. Solo usa
/// estado real (`worker.status`, ids activos del stream SSE) — nunca timers
/// decorativos ni la metáfora de «una PC por bot».
struct TeamParallelMacBar: View {
    let workers: [PersistentWorker]
    let idsActivos: Set<String>

    private var activos: [PersistentWorker] {
        workers.filter { idsActivos.contains($0.id) || $0.status == "running" }
    }

    var body: some View {
        if activos.isEmpty { EmptyView() }
        else {
            HStack(spacing: 8) {
                Image(systemName: "desktopcomputer")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                Text(textoBarra)
                    .font(.caption.weight(.medium))
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                Spacer(minLength: 0)
                HStack(spacing: -6) {
                    ForEach(activos.prefix(4)) { bot in
                        TeamCaraMini(worker: bot, size: 22, activo: idsActivos.contains(bot.id))
                            .transition(.scale.combined(with: .opacity))
                    }
                }
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
            .background(.ultraThinMaterial, in: Capsule())
            .padding(.horizontal)
            .padding(.top, 6)
            .animation(.spring(response: 0.45, dampingFraction: 0.82), value: activos.map(\.id))
        }
    }

    private var textoBarra: String {
        let n = activos.count
        if n == 1, let uno = activos.first {
            return "\(uno.nombreVisible) trabaja en tu Mac"
        }
        let nombres = activos.prefix(3).map(\.nombreVisible).joined(separator: ", ")
        if n > 3 { return "\(nombres) y \(n - 3) más en tu Mac" }
        return "\(nombres) en paralelo en tu Mac"
    }
}

// MARK: - Cara mini (usa `CaraOrbe`, no edita GrokFaceAvatar)

struct TeamCaraMini: View {
    let worker: PersistentWorker?
    let cara: CaraSnapshot?
    let nombreFallback: String
    var size: CGFloat = 28
    var activo: Bool = false

    init(worker: PersistentWorker, size: CGFloat = 28, activo: Bool = false) {
        self.worker = worker
        self.cara = nil
        self.nombreFallback = worker.nombreVisible
        self.size = size
        self.activo = activo
    }

    init(cara: CaraSnapshot, nombre: String, size: CGFloat = 22, activo: Bool = false) {
        self.worker = nil
        self.cara = cara
        self.nombreFallback = nombre
        self.size = size
        self.activo = activo
    }

    var body: some View {
        if let worker {
            GrokFaceAvatar(bot: worker, size: size, showOnline: false, animado: true, activo: activo)
        } else if let cara {
            CaraOrbe(
                nombre: nombreFallback,
                formaBot: cara.shape ?? "circle",
                fillHex: cara.fill ?? "#6366f1",
                accentHex: cara.accent,
                ojoIzq: cara.eyes?.left.map(ojoDesde),
                ojoDer: cara.eyes?.right.map(ojoDesde),
                size: size,
                animado: true,
                activo: activo
            )
        } else {
            ZStack {
                Circle().fill(EdecanTheme.morado.opacity(0.18))
                Image(systemName: "sparkles")
                    .font(.system(size: size * 0.42, weight: .semibold))
                    .foregroundStyle(EdecanTheme.morado)
            }
            .frame(width: size, height: size)
        }
    }

    private func ojoDesde(_ o: CaraSnapshot.OjoSnapshot) -> OjoDeCara {
        OjoDeCara(
            x: CGFloat(o.x ?? 0.34), y: CGFloat(o.y ?? 0.38),
            rx: CGFloat(o.rx ?? 0.07), ry: CGFloat(o.ry ?? 0.08),
            rotation: CGFloat(o.rotation ?? 0)
        )
    }
}

// MARK: - Narración entre bots (escribió a N)

struct TeamNotaNarracion: Identifiable, Equatable {
    let id: String
    let de: String
    let tipo: String
    let cara: CaraSnapshot?
    var mensajes: [String]
    var ultimo: String
}

struct TeamNarracionRow: View {
    let nota: TeamNotaNarracion

    var body: some View {
        HStack(spacing: 6) {
            Text(etiqueta)
                .font(.caption)
                .foregroundStyle(.secondary)
            if let cara = nota.cara {
                TeamCaraMini(cara: cara, nombre: nota.de, size: 18)
            }
            Text(nota.de)
                .font(.caption.weight(.medium))
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .center)
        .padding(.vertical, 2)
        .transition(.opacity.combined(with: .move(edge: .top)))
    }

    private var etiqueta: String {
        if nota.mensajes.count > 1 { return "\(nota.mensajes.count) mensajes con" }
        return nota.tipo == "escribio" ? "Escribió a" : "Mensaje de"
    }
}

// MARK: - Herramienta en curso (harness real, no spinner decorativo)

struct TeamToolActivityRow: View {
    let nombreBot: String
    let herramienta: String
    let detalle: String

    var body: some View {
        HStack(spacing: 8) {
            ProgressView()
                .controlSize(.small)
            VStack(alignment: .leading, spacing: 2) {
                Text("\(nombreBot) · \(herramientaLegible)")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.primary.opacity(0.88))
                if !detalle.isEmpty {
                    Text(detalle)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .transition(.opacity.combined(with: .scale(scale: 0.98)))
    }

    private var herramientaLegible: String {
        herramienta
            .replacingOccurrences(of: "_", with: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

// MARK: - Needs you (proactive_scan → GET /v1/automations/suggestions)

/// Panel «Needs you» para el chat de equipo: muestra sugerencias del motor
/// `proactive_scan` (product design) y permite delegarlas al equipo con un tap.
/// Nunca crea ni activa rutinas por su cuenta.
struct TeamNeedsYouPanel: View {
    let sugerencias: [AutomationSuggestion]
    let onDelegar: (AutomationSuggestion) -> Void
    var onDescartar: ((AutomationSuggestion) -> Void)?

    var body: some View {
        if sugerencias.isEmpty { EmptyView() }
        else {
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 6) {
                    Image(systemName: "bell.badge.fill")
                        .foregroundStyle(.orange)
                    Text("Needs you")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(.orange)
                    Spacer(minLength: 0)
                    Text("\(sugerencias.count)")
                        .font(.caption2.weight(.bold))
                        .padding(.horizontal, 7)
                        .padding(.vertical, 3)
                        .background(.orange.opacity(0.14), in: Capsule())
                        .foregroundStyle(.orange)
                }
                ForEach(sugerencias.prefix(4)) { sugerencia in
                    fila(sugerencia)
                }
                if sugerencias.count > 4 {
                    Text("+\(sugerencias.count - 4) más en Actividad")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
            }
            .padding(14)
            .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .strokeBorder(.orange.opacity(0.28), lineWidth: 1)
            )
            .padding(.horizontal)
            .padding(.top, 4)
        }
    }

    private func fila(_ sugerencia: AutomationSuggestion) -> some View {
        let estilo = TeamEstiloEtapaProactiva.de(SuggestionStage(sugerencia.stage))
        return HStack(alignment: .top, spacing: 10) {
            Image(systemName: estilo.icono)
                .font(.caption.weight(.semibold))
                .foregroundStyle(estilo.color)
                .padding(.top, 2)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(sugerencia.titulo)
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(.primary)
                        .lineLimit(2)
                    Text(estilo.etiqueta)
                        .font(.caption2.weight(.semibold))
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(estilo.color.opacity(0.12), in: Capsule())
                        .foregroundStyle(estilo.color)
                }
                Text(TeamNeedsYouPanel.subtitulo(sugerencia))
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
            Spacer(minLength: 0)
            Button {
                onDelegar(sugerencia)
            } label: {
                Image(systemName: "arrow.up.circle.fill")
                    .font(.title3)
                    .foregroundStyle(EdecanTheme.morado)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Delegar al equipo")
            if let onDescartar {
                Button {
                    onDescartar(sugerencia)
                } label: {
                    Image(systemName: "xmark")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.tertiary)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Ocultar sugerencia")
            }
        }
        .padding(.vertical, 4)
    }

    /// Prompt que el equipo recibe al delegar una sugerencia proactiva.
    static func promptDelegacion(_ sugerencia: AutomationSuggestion) -> String {
        if let task = sugerencia.task?.trimmingCharacters(in: .whitespacesAndNewlines),
           !task.isEmpty {
            return "Investiguen y resuman en puntos: \(task)"
        }
        if let nombre = sugerencia.nombre?.trimmingCharacters(in: .whitespacesAndNewlines),
           !nombre.isEmpty {
            let fallas = sugerencia.failureCount.map { " (falló \($0) veces seguidas)" } ?? ""
            return "Revisen por qué falló la rutina «\(nombre)»\(fallas) y propongan arreglo."
        }
        if let reason = sugerencia.reason?.trimmingCharacters(in: .whitespacesAndNewlines),
           !reason.isEmpty {
            return "Revisen esto y cuéntenme qué hacer: \(reason)"
        }
        return "Revisen lo pendiente y propongan el siguiente paso."
    }

    static func subtitulo(_ sugerencia: AutomationSuggestion) -> String {
        var partes: [String] = []
        if let count = sugerencia.failureCount {
            partes.append("Falló \(count) veces seguidas")
        }
        if let reps = sugerencia.repetitions {
            partes.append("Repetida \(reps) veces")
        }
        if let reason = sugerencia.reason, !reason.isEmpty {
            partes.append(reason)
        }
        if partes.isEmpty { return "Toca ↑ para que el equipo lo investigue" }
        return partes.joined(separator: " · ")
    }
}

private struct TeamEstiloEtapaProactiva {
    let icono: String
    let color: Color
    let etiqueta: String

    static func de(_ etapa: SuggestionStage) -> TeamEstiloEtapaProactiva {
        switch etapa {
        case .observation:
            return TeamEstiloEtapaProactiva(icono: "eye.fill", color: .secondary, etiqueta: "Observación")
        case .suggestion:
            return TeamEstiloEtapaProactiva(icono: "lightbulb.fill", color: EdecanTheme.azul, etiqueta: "Sugerencia")
        case .draft:
            return TeamEstiloEtapaProactiva(icono: "doc.text.fill", color: EdecanTheme.morado, etiqueta: "Borrador")
        case .action:
            return TeamEstiloEtapaProactiva(icono: "flag.fill", color: .orange, etiqueta: "Acción")
        }
    }
}

// MARK: - Enviar / Detener (paridad ChatView, sin tocar Theme.swift)

/// Botón enviar o detener según si hay un turno SSE en curso.
struct TeamSendStopButton: View {
    let habilitadoEnviar: Bool
    let turnoEnCurso: Bool
    let onEnviar: () -> Void
    let onDetener: () -> Void

    @State private var pulsandoDetener = false

    var body: some View {
        if turnoEnCurso {
            Button(action: onDetener) {
                Image(systemName: "stop.circle.fill")
                    .font(.system(size: 34))
                    .foregroundStyle(.red)
                    .scaleEffect(pulsandoDetener ? 1.1 : 1.0)
            }
            .accessibilityLabel("Detener turno")
            .onAppear {
                withAnimation(.easeInOut(duration: 0.7).repeatForever(autoreverses: true)) {
                    pulsandoDetener = true
                }
            }
            .onDisappear {
                withAnimation(.easeOut(duration: 0.15)) { pulsandoDetener = false }
            }
        } else {
            Button(action: onEnviar) {
                ZStack {
                    Circle()
                        .fill(habilitadoEnviar ? Color.black : Color.black.opacity(0.35))
                        .frame(width: 36, height: 36)
                    Image(systemName: "arrow.up")
                        .font(.system(size: 16, weight: .bold))
                        .foregroundStyle(.white)
                        .opacity(habilitadoEnviar ? 1 : 0.5)
                }
            }
            .disabled(!habilitadoEnviar)
            .accessibilityLabel("Enviar al equipo")
        }
    }
}

// MARK: - Tarjeta de pregunta (equipo)

struct TeamQuestionCardView: View {
    let bloque: QuestionBlock
    let respuestaPosterior: String?
    let onResponder: (String) -> Void

    @State private var marcadasEnCurso: Set<String> = []

    private var estado: EstadoDePregunta {
        HiloDePreguntas.estado(de: bloque, respuesta: respuestaPosterior)
    }

    private var respondida: Bool { estado.respondida }
    private var seleccionadas: Set<String> {
        respondida ? estado.opcionesMarcadas : marcadasEnCurso
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let header = bloque.header, !header.isEmpty {
                Text(header.uppercased())
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(EdecanTheme.morado)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(EdecanTheme.morado.opacity(0.12), in: Capsule())
            }
            Text(bloque.question)
                .font(.subheadline.weight(.semibold))
                .fixedSize(horizontal: false, vertical: true)

            VStack(spacing: 8) {
                ForEach(bloque.options) { opcion in
                    Button { elegir(opcion) } label: { filaOpcion(opcion) }
                        .buttonStyle(.plain)
                        .disabled(respondida)
                }
            }

            if bloque.multiSelect && !respondida {
                Button { enviarMultiple() } label: {
                    Text("Enviar respuesta")
                        .font(.footnote.weight(.semibold))
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 11)
                        .background(EdecanTheme.degradado, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                        .foregroundStyle(.white)
                }
                .buttonStyle(.plain)
                .disabled(seleccionadas.isEmpty)
                .opacity(seleccionadas.isEmpty ? 0.5 : 1)
            }

            if bloque.allowFreeText && !respondida {
                Text("O escribe tu propia respuesta abajo.")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(14)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .strokeBorder(EdecanTheme.morado.opacity(respondida ? 0.12 : 0.32), lineWidth: 1.2)
        )
        .transition(.opacity.combined(with: .move(edge: .bottom)))
    }

    private func filaOpcion(_ opcion: QuestionOption) -> some View {
        HStack(spacing: 10) {
            Image(systemName: seleccionadas.contains(opcion.id) ? "checkmark.circle.fill" : "circle")
                .foregroundStyle(seleccionadas.contains(opcion.id) ? EdecanTheme.morado : .secondary)
            VStack(alignment: .leading, spacing: 2) {
                Text(opcion.label)
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(.primary)
                if let desc = opcion.description, !desc.isEmpty {
                    Text(desc)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(seleccionadas.contains(opcion.id) ? EdecanTheme.morado.opacity(0.1) : Color.secondary.opacity(0.06))
        )
    }

    private func elegir(_ opcion: QuestionOption) {
        if bloque.multiSelect {
            if marcadasEnCurso.contains(opcion.id) { marcadasEnCurso.remove(opcion.id) }
            else { marcadasEnCurso.insert(opcion.id) }
            return
        }
        onResponder(opcion.messageText)
    }

    private func enviarMultiple() {
        let textos = bloque.options
            .filter { seleccionadas.contains($0.id) }
            .map(\.messageText)
        guard !textos.isEmpty else { return }
        onResponder(textos.joined(separator: ", "))
    }
}
