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

// MARK: - Chip «Asignado a …» (routing automático, contrato BotBeta)

/// Copy humano para beats de herramientas en vivo — nunca nombres crudos de API.
/// Contrato BotBeta: preferir `avisar_avance` (`kind=aviso`); si no llega,
/// mapear `tool_start`/`tool_end` acá; herramientas pesadas (browser/computer)
/// nunca dejan el hilo en silencio.
enum BeatHerramientaCopy {
    private static let genericos: Set<String> = [
        "ejecutando…", "ejecutando...", "trabajando", "edecán sigue trabajando",
    ]

    /// Meta-tools: no chip «herramienta activa» ni beats genéricos de inicio.
    static let silenciosas: Set<String> = [
        "avisar_avance", "enviar_mensaje_bot", "preguntar_al_usuario",
    ]

    /// Browser/computer/IDE: turnos largos — beat visible si falta `avisar_avance`.
    static let pesadas: Set<String> = [
        "usar_computadora",
        "navegar_web", "navegar_web_interactivo", "extraer_datos_web", "comparar_precios",
        "buscar_web", "acceder_codigo_local", "delegar_al_ide", "delegar_mision",
    ]

    static func esPesada(_ nombre: String) -> Bool {
        pesadas.contains(nombre)
    }

    /// Texto del beat mid-turn (`avisar_avance` → `result_preview` / `kind=aviso`).
    static func avisoAvance(en preview: String) -> String? {
        let limpio = preview.trimmingCharacters(in: .whitespacesAndNewlines)
        return limpio.isEmpty ? nil : limpio
    }

    /// Beat al abrir una tool pesada cuando el bot no llamó `avisar_avance`.
    static func beatParaToolStart(nombre: String) -> String? {
        guard !silenciosas.contains(nombre), esPesada(nombre) else { return nil }
        return mensaje(nombre: nombre, progreso: nil)
    }

    /// Beat al cerrar tool: aviso humano, OAuth, o preview legible; pesadas solo si hay texto útil.
    static func beatParaToolEnd(nombre: String, preview: String) -> String? {
        if nombre == "avisar_avance" {
            return avisoAvance(en: preview)
        }
        guard !silenciosas.contains(nombre), nombre != "delegar_al_ide" else { return nil }
        if let oauth = avisoOAuth(en: preview) { return oauth }
        let limpio = preview.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !limpio.isEmpty else { return nil }
        if textoEsHumano(limpio) { return limpio }
        if esPesada(nombre) {
            return mensaje(nombre: nombre, progreso: limpio)
        }
        return nil
    }

    private static func textoEsHumano(_ bruto: String) -> Bool {
        let lower = bruto.lowercased()
        return !genericos.contains(lower) && !bruto.contains("_")
    }

    private static let clavesRed: [(clave: String, etiqueta: String)] = [
        ("linkedin", "LinkedIn"),
        ("x (twitter)", "X"),
        ("twitter", "X"),
        ("meta (facebook/instagram)", "Meta"),
        ("meta", "Meta"),
        ("youtube", "YouTube"),
    ]

    /// Si el texto del bot/tool reporta OAuth faltante, devuelve copy corto
    /// («LinkedIn no conectado»). Solo parsea mensajes reales — nunca inventa.
    static func avisoOAuth(en texto: String?) -> String? {
        guard let bruto = texto?.trimmingCharacters(in: .whitespacesAndNewlines),
              !bruto.isEmpty else { return nil }
        let lower = bruto.lowercased()

        if lower.contains("ninguna red social está conectada") {
            return "Ninguna red social conectada"
        }

        for par in clavesRed {
            if lower.contains("no tienes conectada una cuenta de \(par.clave)")
                || lower.contains("no tienes conectada una cuenta de \(par.etiqueta.lowercased())") {
                return "\(par.etiqueta) no conectado"
            }
        }

        for par in clavesRed {
            if lower.contains("\(par.clave): no conectada")
                || lower.contains("\(par.etiqueta.lowercased()): no conectada")
                || lower.contains("\(par.etiqueta) no conectad") {
                return "\(par.etiqueta) no conectado"
            }
        }

        if lower.contains("faltan:") || lower.contains("falta:") {
            for par in clavesRed where lower.contains(par.clave) || lower.contains(par.etiqueta.lowercased()) {
                if lower.contains("no conectad") || lower.contains("faltan") {
                    return "\(par.etiqueta) no conectado"
                }
            }
        }

        return nil
    }

    static func mensaje(nombre herramienta: String, progreso: String?) -> String {
        if let oauth = avisoOAuth(en: progreso) {
            return oauth
        }
        if let progreso = progreso?.trimmingCharacters(in: .whitespacesAndNewlines),
           !progreso.isEmpty,
           !genericos.contains(progreso.lowercased()),
           !progreso.contains("_") {
            return progreso
        }
        switch herramienta {
        case "estado_conectores_sociales": return "Revisando conexiones OAuth…"
        case "acceder_codigo_local": return "Revisando el código…"
        case "buscar_web": return "Buscando en la web…"
        case "navegar_web": return "Navegando en la web…"
        case "navegar_web_interactivo": return "Interactuando con el navegador…"
        case "extraer_datos_web": return "Extrayendo datos de la página…"
        case "comparar_precios": return "Comparando precios…"
        case "consultar_documentos": return "Consultando documentos…"
        case "leer_archivo": return "Leyendo archivos…"
        case "generar_contenido": return "Generando contenido…"
        case "publicar_social": return "Publicando en red social…"
        case "delegar_al_ide": return "Delegando al IDE…"
        case "delegar_mision": return "Encolando misión…"
        case "usar_computadora": return "Usando tu computadora…"
        case "run_command": return "Ejecutando comando…"
        case "conectar_mcp", "listar_mcp": return "Conectando herramientas…"
        default:
            return "Trabajando…"
        }
    }
}

struct AsignadoAChipView: View {
    let nombreBot: String
    var worker: PersistentWorker?
    var cara: CaraSnapshot?

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "person.crop.circle.badge.checkmark")
                .font(.caption.weight(.semibold))
                .foregroundStyle(EdecanTheme.morado)
            Text("Asignado a")
                .font(.caption)
                .foregroundStyle(.secondary)
            if let worker {
                TeamCaraMini(worker: worker, size: 20, activo: true)
            } else if let cara {
                TeamCaraMini(cara: cara, nombre: nombreBot, size: 20, activo: true)
            }
            Text(nombreBot)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.primary.opacity(0.88))
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
        .capsulaVidrio()
        .frame(maxWidth: .infinity, alignment: .center)
        .padding(.vertical, 2)
        .transition(.opacity.combined(with: .move(edge: .top)))
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Asignado a \(nombreBot)")
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
        if nota.tipo == "asignacion" { return "Se lo pasé a" }
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
                Text("\(nombreBot) · \(BeatHerramientaCopy.mensaje(nombre: herramienta, progreso: detalle))")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.primary.opacity(0.88))
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .transition(.opacity.combined(with: .scale(scale: 0.98)))
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
    var etiquetaAccion: String = "Delegar al equipo"
    var pieSinDetalle: String = "Toca ↑ para que el equipo lo investigue"

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
                Text(Self.subtitulo(sugerencia, pieSinDetalle: pieSinDetalle))
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
            .accessibilityLabel(etiquetaAccion)
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
    static func sugerenciasAccionables(
        _ todas: [AutomationSuggestion],
        agentId: String? = nil
    ) -> [AutomationSuggestion] {
        todas.filter { item in
            let etapa = SuggestionStage(item.stage)
            guard etapa == .action || etapa == .suggestion || etapa == .draft
                || item.failureCount != nil
            else { return false }
            guard let agentId else { return true }
            guard let itemAgent = item.agentId else { return true }
            return itemAgent == agentId
        }
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

    static func subtitulo(
        _ sugerencia: AutomationSuggestion,
        pieSinDetalle: String = "Toca ↑ para que el equipo lo investigue"
    ) -> String {
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
        if partes.isEmpty { return pieSinDetalle }
        return partes.joined(separator: " · ")
    }

    /// Prompt 1:1 para un bot (no equipo).
    static func promptParaBot(_ sugerencia: AutomationSuggestion, nombreBot: String) -> String {
        if let task = sugerencia.task?.trimmingCharacters(in: .whitespacesAndNewlines),
           !task.isEmpty {
            return "\(nombreBot), esto Needs you: \(task). Resuélvelo y cuéntame en este chat."
        }
        if let nombre = sugerencia.nombre?.trimmingCharacters(in: .whitespacesAndNewlines),
           !nombre.isEmpty {
            let fallas = sugerencia.failureCount.map { " (falló \($0) veces seguidas)" } ?? ""
            return "\(nombreBot), revisa por qué falló «\(nombre)»\(fallas) y propón el arreglo aquí."
        }
        if let reason = sugerencia.reason?.trimmingCharacters(in: .whitespacesAndNewlines),
           !reason.isEmpty {
            return "\(nombreBot), Needs you: \(reason). Dime qué harás y ejecútalo."
        }
        return "\(nombreBot), hay algo pendiente (Needs you). Revisa Actividad/sugerencias y actúa en este chat."
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
    /// Omitir sin responder (Grok dismiss). Si nil, no se muestra el botón.
    var onDescartar: (() -> Void)? = nil

    @State private var marcadasEnCurso: Set<String> = []
    @State private var textoLibre = ""
    @FocusState private var libreEnfocado: Bool

    private var estado: EstadoDePregunta {
        HiloDePreguntas.estado(de: bloque, respuesta: respuestaPosterior)
    }

    private var respondida: Bool { estado.respondida }
    private var seleccionadas: Set<String> {
        respondida ? estado.opcionesMarcadas : marcadasEnCurso
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                if let header = bloque.header, !header.isEmpty {
                    Text(header.uppercased())
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(EdecanTheme.morado)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 3)
                        .background(EdecanTheme.morado.opacity(0.12), in: Capsule())
                }
                if bloque.multiSelect && !respondida {
                    Text("Varias")
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(EdecanTheme.azul)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 3)
                        .background(EdecanTheme.azul.opacity(0.12), in: Capsule())
                }
                Spacer(minLength: 0)
                if respondida {
                    Label("Respondida", systemImage: "checkmark.circle.fill")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(.green)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 3)
                        .background(Color.green.opacity(0.12), in: Capsule())
                } else if onDescartar != nil {
                    Button {
                        Haptico.ligero()
                        onDescartar?()
                    } label: {
                        Text("Omitir")
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(.secondary)
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Omitir pregunta")
                }
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
                    Text(seleccionadas.isEmpty ? "Elige una o más" : "Enviar \(seleccionadas.count)")
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
                VStack(alignment: .leading, spacing: 8) {
                    TextField("Escribe tu respuesta…", text: $textoLibre, axis: .vertical)
                        .lineLimit(1...4)
                        .focused($libreEnfocado)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 10)
                        .background(Color.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                    Button {
                        let limpio = textoLibre.trimmingCharacters(in: .whitespacesAndNewlines)
                        guard !limpio.isEmpty else { return }
                        Haptico.ligero()
                        onResponder(limpio)
                        textoLibre = ""
                        libreEnfocado = false
                    } label: {
                        Text("Enviar texto libre")
                            .font(.caption.weight(.semibold))
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 9)
                            .foregroundStyle(EdecanTheme.morado)
                            .background(EdecanTheme.morado.opacity(0.12), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                    }
                    .buttonStyle(.plain)
                    .disabled(textoLibre.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    .opacity(textoLibre.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? 0.45 : 1)
                }
            }
        }
        .padding(14)
        .tarjetaVidrio(esquina: 16)
        .overlay(
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .strokeBorder(EdecanTheme.morado.opacity(respondida ? 0.12 : 0.32), lineWidth: 1.2)
        )
        .transition(.opacity.combined(with: .move(edge: .bottom)))
    }

    private func filaOpcion(_ opcion: QuestionOption) -> some View {
        let marcada = seleccionadas.contains(opcion.id)
        let icono = bloque.multiSelect
            ? (marcada ? "checkmark.square.fill" : "square")
            : (marcada ? "checkmark.circle.fill" : "circle")
        return HStack(alignment: .top, spacing: 10) {
            Image(systemName: icono)
                .font(.system(size: 18, weight: .semibold))
                .foregroundStyle(marcada ? EdecanTheme.morado : .secondary)
                .frame(width: 22, height: 22)
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
        .frame(minHeight: 44, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(marcada ? EdecanTheme.morado.opacity(0.12) : Color.secondary.opacity(0.06))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .strokeBorder(marcada ? EdecanTheme.morado.opacity(0.35) : Color.clear, lineWidth: 1)
        )
    }

    private func elegir(_ opcion: QuestionOption) {
        if bloque.multiSelect {
            if marcadasEnCurso.contains(opcion.id) { marcadasEnCurso.remove(opcion.id) }
            else { marcadasEnCurso.insert(opcion.id) }
            return
        }
        Haptico.ligero()
        onResponder(opcion.messageText)
    }

    private func enviarMultiple() {
        let textos = bloque.options
            .filter { seleccionadas.contains($0.id) }
            .map(\.messageText)
        guard !textos.isEmpty else { return }
        Haptico.ligero()
        // Una opción por línea (contrato multiSelect Grok / widget).
        onResponder(textos.joined(separator: "\n"))
    }
}
