import EdecanKit
import PhotosUI
import SwiftUI
import UIKit

/// Chat 1:1 con un bot persistente — SIEMPRE VIVO: puedes seguir escribiendo
/// mientras el bot trabaja (los mensajes se encolan y responde en orden),
/// las imágenes se ven y se amplían con zoom, y los mensajes se copian.
struct BotChatView: View {
    @Environment(SessionStore.self) private var session
    let bot: PersistentWorker

    @State private var items: [ItemMensajeBot] = []
    @State private var texto = ""
    @State private var cargando = true
    @State private var error: String?
    @FocusState private var campoEnfocado: Bool
    @State private var anclaFinal = "bot-final"

    private let sseClient = SSEClient()
    /// Cadena de envíos: cada mensaje espera al turno anterior. Así el chat
    /// nunca se cierra — escribes aunque el bot esté trabajando y responde
    /// en orden (el modelo de Grok Bot).
    @State private var cadenaEnvio: Task<Void, Never>?
    @State private var enviandoTurno = false

    // Adjuntos pendientes (imágenes): se suben a /v1/files y viajan como ids.
    @State private var adjuntosPendientes: [AdjuntoBot] = []
    @State private var seleccionFotos: [PhotosPickerItem] = []
    @State private var previewTarget: SecurePreviewTarget?
    @Namespace private var composerNamespace

    var body: some View {
        VStack(spacing: 0) {
            listaDeMensajes
            if let error {
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .padding(.horizontal)
                    .padding(.top, 6)
            }
            barraDeEntrada
        }
        .estiloPantallaBots()
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(.hidden, for: .navigationBar)
        .toolbar {
            ToolbarItem(placement: .principal) {
                cabeceraBot
            }
        }
        .task { await cargar() }
        .onChange(of: seleccionFotos) { _, nuevos in
            Task { await subirFotos(nuevos) }
            seleccionFotos = []
        }
        .fullScreenCover(item: $previewTarget) { target in
            SecurePreviewSheet(target: target, client: session.client)
        }
    }

    /// Cabecera Liquid Glass: cara + nombre en pill flotante sobre el chat.
    private var cabeceraBot: some View {
        HStack(spacing: 10) {
            GrokFaceAvatar(bot: bot, size: 28, showOnline: false, animado: true, activo: enviandoTurno)
            VStack(alignment: .leading, spacing: 1) {
                Text(bot.nombreVisible)
                    .font(.subheadline.weight(.semibold))
                    .lineLimit(1)
                if enviandoTurno {
                    Text("Trabajando…")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .transition(.opacity.combined(with: .move(edge: .top)))
                }
            }
            // Sin spinner en la cabecera: la cara animada (habla + halo) ES el
            // indicador de trabajo — un spinner encima del nombre leía como
            // «app rota» en vez de «bot vivo».
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .capsulaVidrio()
        .animation(.spring(response: 0.32, dampingFraction: 0.82), value: enviandoTurno)
    }

    private var listaDeMensajes: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 18) {
                    if cargando && items.isEmpty {
                        ProgressView("Cargando el chat…")
                            .frame(maxWidth: .infinity)
                            .padding(.top, 60)
                    } else if items.isEmpty {
                        estadoVacio
                    }
                    ForEach(filas) { fila in
                        Group {
                            switch fila {
                            case .mensaje(let item):
                                burbuja(item)
                                    .id(item.id)
                            case .narracion(let nota):
                                filaNarracion(nota)
                                    .id(nota.id)
                            }
                        }
                        .transition(.asymmetric(
                            insertion: .opacity.combined(with: .move(edge: .bottom)),
                            removal: .opacity
                        ))
                    }
                    Color.clear.frame(height: 1).id(anclaFinal)
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
            }
            .scrollDismissesKeyboard(.interactively)
            .onChange(of: items.count) { _, _ in
                withAnimation(.spring(response: 0.35, dampingFraction: 0.88)) {
                    proxy.scrollTo(anclaFinal, anchor: .bottom)
                }
            }
        }
    }

    /// Hero de chat vacío: el bot en grande, su identidad y sugerencias.
    private var estadoVacio: some View {
        VStack(spacing: 16) {
            GrokFaceAvatar(bot: bot, size: 96, showOnline: false)
                .padding(.top, 30)

            VStack(spacing: 6) {
                Text(bot.nombreVisible)
                    .font(.title2.weight(.bold))
                if !bot.purpose.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    Text(bot.purpose)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                        .lineLimit(3)
                        .frame(maxWidth: 320)
                }
            }

            HStack(spacing: 8) {
                sugerencia("Preséntate")
                sugerencia("¿En qué eres experto?")
                sugerencia("¿Qué sabes de mí?")
            }
            .padding(.horizontal, 4)

            Text("Escribe abajo, toca una sugerencia o mándale una imagen.")
                .font(.caption)
                .foregroundStyle(.tertiary)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 30)
        .padding(.bottom, 20)
    }

    private func sugerencia(_ texto: String) -> some View {
        Button {
            withAnimation(.easeOut(duration: 0.15)) {
                self.texto = texto
            }
            campoEnfocado = true
        } label: {
            Text(texto)
                .font(.footnote.weight(.medium))
                .foregroundStyle(.primary)
                .padding(.horizontal, 14)
                .padding(.vertical, 9)
                .capsulaVidrio()
        }
        .buttonStyle(.plain)
    }

    // MARK: - Mensajes

    /// Agrupa los eventos de narración consecutivos del MISMO bot en una
    /// sola nota: «N mensajes con [cara] X». El contenido es interno: la
    /// nota solo cuenta el hecho, nunca pega el mensaje literal.
    private var filas: [FilaChatBot] {
        var resultado: [FilaChatBot] = []
        for item in items {
            if let ev = item.evento {
                if case .narracion(var nota) = resultado.last, nota.de == ev.de {
                    nota.mensajes.append(ev.goal)
                    nota.ultimo = ev.goal
                    nacido(nota, en: &resultado)
                } else {
                    resultado.append(
                        .narracion(NotaNarracion(
                            de: ev.de,
                            tipo: ev.escribioA ? "escribio" : "recibio",
                            cara: ev.cara,
                            mensajes: [ev.goal],
                            ultimo: ev.goal
                        ))
                    )
                }
            } else {
                resultado.append(.mensaje(item))
            }
        }
        return resultado
    }

    private func nacido(_ nota: NotaNarracion, en resultado: inout [FilaChatBot]) {
        resultado[resultado.count - 1] = .narracion(nota)
    }

    /// La nota de narración estilo Grok: línea centrada con la carita +
    /// el nombre. El CONTENIDO entre bots es interno: se indica el hecho
    /// («Escribió a X» / «Mensaje de X»), nunca el mensaje literal.
    @ViewBuilder
    private func filaNarracion(_ nota: NotaNarracion) -> some View {
        HStack(spacing: 8) {
            if nota.tipo == "escribio" {
                Image(systemName: "arrow.right")
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(.tertiary)
            }
            Text(etiquetaNarracion(nota))
                .font(.caption)
                .foregroundStyle(.secondary)
            if let cara = nota.cara {
                caraSnapshot(cara, nombre: nota.de, size: 20)
            } else {
                Circle()
                    .fill(EdecanTheme.morado.opacity(0.25))
                    .frame(width: 20, height: 20)
            }
            Text(nota.de)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            if nota.mensajes.count > 1 {
                Text("· \(nota.mensajes.count)")
                    .font(.caption2.weight(.medium))
                    .foregroundStyle(.tertiary)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 6)
        .capsulaVidrio()
        .frame(maxWidth: .infinity, alignment: .center)
        .padding(.vertical, 2)
        .transition(.opacity.combined(with: .move(edge: .top)))
    }

    private func etiquetaNarracion(_ nota: NotaNarracion) -> String {
        if nota.mensajes.count > 1 {
            return "\(nota.mensajes.count) mensajes con"
        }
        return nota.tipo == "escribio" ? "Escribió a" : "Mensaje de"
    }

    @ViewBuilder
    private func burbuja(_ item: ItemMensajeBot) -> some View {
        // Evento de narración entre bots: texto pequeño y centrado, con la
        // cara del otro bot. No es burbuja ni es tocable.
        if let evento = item.evento {
            HStack(spacing: 7) {
                if let cara = evento.cara {
                    caraSnapshot(cara, nombre: evento.de, size: 22)
                }
                Text(evento.texto)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(3)
                    .multilineTextAlignment(.leading)
            }
            .frame(maxWidth: .infinity, alignment: .center)
            .padding(.vertical, 2)
            .id(item.id)
        } else if item.enProgreso && item.texto.isEmpty {
            FilaEstadoTrabajandoBot(bot: bot)
                .frame(maxWidth: .infinity, alignment: .leading)
                .id(item.id)
        } else if item.esUsuario {
            HStack(alignment: .top, spacing: 0) {
                Spacer(minLength: 48)
                VStack(alignment: .trailing, spacing: 6) {
                    if !item.adjuntos.isEmpty {
                        adjuntosEnBurbuja(item.adjuntos)
                    }
                    if !item.texto.isEmpty {
                        Text(item.texto)
                            .font(.subheadline)
                            .fixedSize(horizontal: false, vertical: true)
                            .textSelection(.enabled)
                    }
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .foregroundStyle(.primary)
                .tarjetaVidrio(esquina: 16, tint: EdecanTheme.morado)
                .frame(maxWidth: 320, alignment: .trailing)
                .contextMenu {
                    if !item.texto.isEmpty {
                        Button {
                            UIPasteboard.general.string = item.texto
                        } label: {
                            Label("Copiar", systemImage: "doc.on.doc")
                        }
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .trailing)
        } else {
            // El bot: burbuja Liquid Glass (misma familia que la del dueño,
            // sin tint morado) — antes el texto iba directo sobre la
            // superficie y se veía pegado al fondo.
            HStack(alignment: .top, spacing: 8) {
                GrokFaceAvatar(
                    bot: bot,
                    size: 30,
                    showOnline: false,
                    animado: true,
                    activo: item.enProgreso
                )
                VStack(alignment: .leading, spacing: 6) {
                    if !item.adjuntos.isEmpty {
                        adjuntosEnBurbuja(item.adjuntos)
                    }
                    if !item.texto.isEmpty {
                        textoRico(item.texto)
                            .textSelection(.enabled)
                            .padding(.horizontal, 14)
                            .padding(.vertical, 10)
                            .tarjetaVidrio(esquina: 16)
                            .frame(maxWidth: 320, alignment: .leading)
                    }
                    if item.enProgreso, let estado = item.estadoTrabajo, !estado.isEmpty {
                        // Narración EN VIVO de lo que hace: cada tool que
                        // llama se cuenta aquí, en su voz — no un mudo spinner.
                        HStack(spacing: 6) {
                            Image(systemName: "ellipsis.bubble")
                                .font(.caption2)
                                .foregroundStyle(EdecanTheme.morado)
                            Text(estado)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .lineLimit(2)
                        }
                        .padding(.horizontal, 12)
                        .padding(.vertical, 6)
                        .capsulaVidrio()
                        .transition(.opacity.combined(with: .move(edge: .bottom)))
                    }
                }
                Spacer(minLength: 40)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .contextMenu {
                if !item.texto.isEmpty {
                    Button {
                        UIPasteboard.general.string = item.texto
                    } label: {
                        Label("Copiar", systemImage: "doc.on.doc")
                    }
                }
            }
        }
    }

    /// Texto con markdown vivo LÍNEA POR LÍNEA: negritas, cursivas, `código`
    /// y enlaces, PRESERVANDO los saltos de línea y las viñetas.
    ///
    /// Por qué no `AttributedString(markdown:)` del texto completo: el parse
    /// de bloques se come los `\n` y las listas (el dueño lo vio: todo el
    /// texto salía corrido, "impecables.React"). Y por qué no la variante
    /// `inlineOnlyPreservingWhitespace` sobre el texto completo: crashea con
    /// listas/bloques (crash del 28-ago). Línea por línea no hay bloques →
    /// inline seguro + `\n` literales entre líneas.
    private func textoRico(_ texto: String) -> Text {
        var resultado: Text? = nil
        for lineaOriginal in texto.components(separatedBy: "\n") {
            var linea = lineaOriginal
            let recortada = linea.trimmingCharacters(in: .whitespaces)
            // Viñetas markdown → punto visual (el parse inline las deja
            // literales; el "• " lee mejor y conserva el salto).
            if recortada.hasPrefix("- ") || recortada.hasPrefix("* ") {
                if let rango = linea.range(of: "- ") ?? linea.range(of: "* ") {
                    linea.replaceSubrange(rango, with: "• ")
                }
            }
            let atribuido = (try? AttributedString(
                markdown: linea,
                options: AttributedString.MarkdownParsingOptions(
                    interpretedSyntax: .inlineOnlyPreservingWhitespace
                )
            )) ?? AttributedString(linea)
            let pedazo = Text(atribuido)
            resultado = (resultado == nil) ? pedazo : resultado! + Text("\n") + pedazo
        }
        return (resultado ?? Text("")).font(.subheadline)
    }

    /// Imágenes adjuntas: miniatura autenticada; tocar abre el visor seguro
    /// con zoom (`SecurePreviewSheet`).
    @ViewBuilder
    private func adjuntosEnBurbuja(_ adjuntos: [AdjuntoBot]) -> some View {
        ForEach(adjuntos) { adjunto in
            if (adjunto.mime ?? "").lowercased().hasPrefix("image/") {
                ImagenAdjuntaBot(fileId: adjunto.fileId, client: session.client) {
                    abrirAdjunto(adjunto)
                }
            } else {
                // Documento generado (PDF, markdown, hoja de cálculo…): chip
                // tocable que abre el visor seguro (SecurePreviewSheet).
                Button {
                    abrirAdjunto(adjunto)
                } label: {
                    HStack(spacing: 8) {
                        Image(systemName: iconoDocumento(adjunto.mime))
                            .font(.system(size: 16, weight: .medium))
                            .foregroundStyle(EdecanTheme.morado)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(adjunto.filename)
                                .font(.footnote.weight(.medium))
                                .foregroundStyle(.primary)
                                .lineLimit(1)
                            Text(nombreTipoDocumento(adjunto.mime))
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                        Spacer(minLength: 6)
                        Image(systemName: "eye")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    .padding(.horizontal, 12)
                    .padding(.vertical, 9)
                    .frame(maxWidth: 280, alignment: .leading)
                    .tarjetaVidrio(esquina: 12)
                }
                .buttonStyle(.plain)
            }
        }
    }

    private func abrirAdjunto(_ adjunto: AdjuntoBot) {
        previewTarget = .artifact(
            ArtifactRef(fileId: adjunto.fileId, filename: adjunto.filename, mime: adjunto.mime)
        )
    }

    private func iconoDocumento(_ mime: String?) -> String {
        switch (mime ?? "").lowercased() {
        case let m where m.contains("pdf"): "doc.richtext"
        case let m where m.contains("spreadsheet") || m.contains("excel") || m.contains("csv"): "tablecells"
        case let m where m.contains("markdown") || m.contains("text/"): "doc.text"
        default: "doc"
        }
    }

    private func nombreTipoDocumento(_ mime: String?) -> String {
        switch (mime ?? "").lowercased() {
        case let m where m.contains("pdf"): "PDF"
        case let m where m.contains("spreadsheet") || m.contains("excel"): "Hoja de cálculo"
        case let m where m.contains("csv"): "CSV"
        case let m where m.contains("markdown"): "Markdown"
        default: "Documento"
        }
    }

    /// Cara-orbe desde el snapshot del backend (sin worker completo).
    @ViewBuilder
    private func caraSnapshot(_ cara: CaraSnapshot, nombre: String, size: CGFloat) -> some View {
        CaraOrbe(
            nombre: nombre,
            formaBot: cara.shape ?? "circle",
            fillHex: cara.fill ?? "#6366f1",
            accentHex: cara.accent,
            ojoIzq: cara.eyes?.left.map {
                OjoDeCara(
                    x: CGFloat($0.x ?? 0.34), y: CGFloat($0.y ?? 0.38),
                    rx: CGFloat($0.rx ?? 0.07), ry: CGFloat($0.ry ?? 0.08),
                    rotation: CGFloat($0.rotation ?? 0)
                )
            },
            ojoDer: cara.eyes?.right.map {
                OjoDeCara(
                    x: CGFloat($0.x ?? 0.66), y: CGFloat($0.y ?? 0.38),
                    rx: CGFloat($0.rx ?? 0.07), ry: CGFloat($0.ry ?? 0.08),
                    rotation: CGFloat($0.rotation ?? 0)
                )
            },
            size: size,
            animado: true,
            activo: false
        )
    }

    // MARK: - Entrada

    private var barraDeEntrada: some View {
        VStack(spacing: 0) {
            ContenedorVidrioBots(spacing: 10) {
                HStack(alignment: .bottom, spacing: 10) {
                    PhotosPicker(selection: $seleccionFotos, maxSelectionCount: 4, matching: .images) {
                        Image(systemName: "plus")
                            .font(.system(size: 18, weight: .semibold))
                            .foregroundStyle(.primary)
                            .frame(width: 40, height: 40)
                    }
                    .accessibilityLabel("Adjuntar imagen")
                    .capsulaVidrio()
                    .vidrioMorphID("adjuntar", in: composerNamespace)

                    TextField("Escribe a \(bot.nombreVisible)…", text: $texto, axis: .vertical)
                        .lineLimit(1...5)
                        .focused($campoEnfocado)
                        .submitLabel(.send)
                        .onSubmit {
                            guard botonHabilitado else { return }
                            encolarEnvio()
                        }
                        .textFieldStyle(.plain)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 11)
                        .capsulaVidrio()
                        .vidrioMorphID("campo", in: composerNamespace)

                    // Sin spinner en el botón de enviar: escribir y encolar
                    // SIEMPRE está permitido mientras el bot trabaja (el
                    // servidor serializa los turnos). El estado «trabajando»
                    // vive en la cabecera y en la fila de estado.
                    BotonEnviarNegro(habilitado: botonHabilitado) {
                        encolarEnvio()
                    }
                    .vidrioMorphID("enviar", in: composerNamespace)
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
            }
            .padding(.horizontal, 12)
            .padding(.top, 6)
            .shadow(color: Color.black.opacity(0.05), radius: 16, y: -4)
        }
        .background(
            LinearGradient(
                colors: [Color.white.opacity(0.001), Color.white.opacity(0.85)],
                startPoint: .top,
                endPoint: .bottom
            )
            .ignoresSafeArea(edges: .bottom)
        )
        .safeAreaPadding(.bottom, 6)
    }

    /// Escribir SIEMPRE está permitido, aunque el bot esté trabajando: el
    /// mensaje se encola y se envía cuando el turno anterior termina.
    private var botonHabilitado: Bool {
        let hayTexto = !texto.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        let hayAdjuntos = !adjuntosPendientes.isEmpty
        return hayTexto || hayAdjuntos
    }

    private func encolarEnvio() {
        let limpio = texto.trimmingCharacters(in: .whitespacesAndNewlines)
        let adjuntos = adjuntosPendientes
        guard !limpio.isEmpty || !adjuntos.isEmpty, let client = session.client else { return }
        adjuntosPendientes = []

        // Comandos locales: `/clear` (y sinónimos) reinician el chat del bot
        // sin pasarlo por el modelo — mismo criterio que el chat principal.
        let comando = limpio.lowercased()
        if ["/clear", "/reset", "/nuevo", "/new"].contains(comando) {
            texto = ""
            campoEnfocado = false
            Task { await limpiarChatBot(client: client) }
            return
        }

        items.append(ItemMensajeBot(id: UUID().uuidString, esUsuario: true, texto: limpio, adjuntos: adjuntos.map {
            AdjuntoBot(fileId: $0.fileId, filename: $0.filename, mime: $0.mime)
        }))
        // El texto viaja con el mensaje y la caja queda limpia: escribir de
        // nuevo no debe exigir borrar lo anterior.
        texto = ""

        // Envío INMEDIATO, como un chat con una persona: cada mensaje sale
        // en el instante en que lo mandas, sin esperar a que el bot termine
        // de responder el anterior. El ORDEN lo garantiza el servidor (lock
        // por worker en `send_worker_message`): los turnos corren en
        // secuencia aunque los envíos lleguen a la vez.
        cadenaEnvio = Task {
            await hacerEnvio(client: client, texto: limpio, adjuntos: adjuntos)
        }
    }

    private func limpiarChatBot(client: APIClient) async {
        do {
            try await client.clearWorkerMessages(workerId: bot.id)
            items.removeAll()
            let saludo = "¡Listo! Empezamos de cero, tú y yo. ¿Qué hacemos hoy?"
            items = [ItemMensajeBot(id: UUID().uuidString, esUsuario: false, texto: saludo, nombreRemitente: bot.nombreVisible)]
            error = nil
        } catch {
            self.error = "No pude limpiar el chat. Revisa que la Mac esté encendida."
        }
    }

    private func hacerEnvio(client: APIClient, texto: String, adjuntos: [AdjuntoBot]) async {
        enviandoTurno = true
        defer { enviandoTurno = false }
        let respuestaId = UUID().uuidString
        let claveIdempotencia = UUID().uuidString
        items.append(
            ItemMensajeBot(
                id: respuestaId,
                esUsuario: false,
                texto: "",
                nombreRemitente: bot.nombreVisible,
                enProgreso: true
            )
        )

        do {
            let request = try await construirPeticion(
                client: client, texto: texto, adjuntos: adjuntos, clave: claveIdempotencia
            )
            // THROTTLE del streaming: antes cada delta de texto disparaba un
            // re-layout de SwiftUI (markdown incluido) — con respuestas largas
            // el main thread se saturaba y el watchdog de escena mataba la
            // app (0x8BADF00D, crash 29-ago 23:20). Acumulamos y pintamos
            // máximo ~8 veces por segundo; el texto final se pinta completo
            // al recibir .done.
            var textoParcial = ""
            var ultimoPintado = Date.distantPast
            func pintar(final: Bool = false) {
                let ahora = Date()
                if final || ahora.timeIntervalSince(ultimoPintado) > 0.12 {
                    ultimoPintado = ahora
                    guard let indice = items.firstIndex(where: { $0.id == respuestaId }) else { return }
                    items[indice].texto = textoParcial
                }
            }
            func narrar(_ id: String, _ estado: String) {
                guard let indice = items.firstIndex(where: { $0.id == id }) else { return }
                items[indice].estadoTrabajo = estado
            }
            var falloDelTurno = false
            for try await evento in sseClient.stream(request) {
                switch evento {
                case .textDelta(let delta):
                    textoParcial += delta
                    pintar()
                case .toolStart(_, let name, _):
                    narrar(respuestaId, "usando \(name)…")
                case .toolProgress(_, _, _, let mensaje):
                    narrar(respuestaId, mensaje)
                case .toolEnd(_, let name, let preview, _, _, _, _):
                    if name == "avisar_avance", !preview.isEmpty {
                        // El bot se avisó a sí mismo: el aviso es un mensaje
                        // real suyo — burbuja propia, ya persistida en el
                        // servidor.
                        items.append(
                            ItemMensajeBot(
                                id: UUID().uuidString,
                                esUsuario: false,
                                texto: preview,
                                nombreRemitente: bot.nombreVisible
                            )
                        )
                        narrar(respuestaId, "aviso enviado")
                    } else {
                        narrar(respuestaId, "✓ \(name)")
                    }
                case .done:
                    pintar(final: true)
                    if let indice = items.firstIndex(where: { $0.id == respuestaId }) {
                        items[indice].enProgreso = false
                        items[indice].estadoTrabajo = nil
                    }
                case .error:
                    falloDelTurno = true
                    pintar(final: true)
                    if let indice = items.firstIndex(where: { $0.id == respuestaId }) {
                        items[indice].enProgreso = false
                        if items[indice].texto.isEmpty {
                            items[indice].texto = "Ups, se me enredó a mitad de camino. Pídemelo de nuevo y lo intento por otra vía."
                        } else {
                            items.append(
                                ItemMensajeBot(
                                    id: UUID().uuidString,
                                    esUsuario: false,
                                    texto: "…perdón, se me cortó justo ahí. Dime «continúa» y sigo.",
                                    nombreRemitente: bot.nombreVisible
                                )
                            )
                        }
                    }
                default:
                    break
                }
            }
            if !falloDelTurno {
                quitarBurbujaVacia(respuestaId)
                return
            }
        } catch is CancellationError {
            // La vista se fue; el servidor conserva el turno y lo completa.
            // Al volver, `cargar()` trae la respuesta ya persistida.
            return
        } catch {
            // CONEXIÓN PERDIDA a mitad del turno. Nada de disculpas: el turno
            // SIGUE corriendo en la Mac (productor desacoplado del socket en
            // el backend). Reconectamos con la MISMA Idempotency-Key: el
            // backend responde 409 mientras sigue en vuelo y entrega el
            // replay exacto del turno completo en cuanto termina.
        }

        await reconectarTurno(
            client: client,
            texto: texto,
            adjuntos: adjuntos,
            clave: claveIdempotencia,
            respuestaId: respuestaId
        )
    }

    /// Reconexión con la misma Idempotency-Key: 409 = sigue en vuelo (esperar
    /// y reintentar); 200 = replay del turno completo; timeout honesto a los
    /// ~10 minutos (el push del servidor avisa igualmente al terminar).
    private func reconectarTurno(
        client: APIClient,
        texto: String,
        adjuntos: [AdjuntoBot],
        clave: String,
        respuestaId: String
    ) async {
        let limite = Date().addingTimeInterval(600)
        while Date() < limite {
            try? await Task.sleep(for: .seconds(2))
            do {
                let request = try await construirPeticion(
                    client: client, texto: texto, adjuntos: adjuntos, clave: clave
                )
                var textoParcial = ""
                var ultimoPintado = Date.distantPast
                func pintar(final: Bool = false) {
                    let ahora = Date()
                    if final || ahora.timeIntervalSince(ultimoPintado) > 0.12 {
                        ultimoPintado = ahora
                        guard let indice = items.firstIndex(where: { $0.id == respuestaId }) else { return }
                        items[indice].texto = textoParcial
                    }
                }
                func narrar(_ id: String, _ estado: String) {
                    guard let indice = items.firstIndex(where: { $0.id == id }) else { return }
                    items[indice].estadoTrabajo = estado
                }
                var termino = false
            var falloServidor = false
            for try await evento in sseClient.stream(request) {
                switch evento {
                case .textDelta(let delta):
                    textoParcial += delta
                    pintar()
                case .toolStart(_, let name, _):
                    narrar(respuestaId, "usando \(name)…")
                case .toolProgress(_, _, _, let mensaje):
                    narrar(respuestaId, mensaje)
                case .toolEnd(_, let name, let preview, _, _, _, _):
                    if name == "avisar_avance", !preview.isEmpty {
                        items.append(
                            ItemMensajeBot(
                                id: UUID().uuidString,
                                esUsuario: false,
                                texto: preview,
                                nombreRemitente: bot.nombreVisible
                            )
                        )
                    } else {
                        narrar(respuestaId, "✓ \(name)")
                    }
                case .done:
                    pintar(final: true)
                    termino = true
                case .error:
                    falloServidor = true
                default:
                    break
                }
            }
            guard termino, !falloServidor else { return }
            if let indice = items.firstIndex(where: { $0.id == respuestaId }) {
                items[indice].enProgreso = false
                items[indice].estadoTrabajo = nil
                if items[indice].texto.isEmpty {
                    items[indice].texto = "Listo, terminé (terminé el trabajo en segundo plano)."
                }
            }
            quitarBurbujaVacia(respuestaId)
            return
            } catch is CancellationError {
                return
            } catch let sseError as SSEClient.SSEError {
                if case .servidor(let status, _) = sseError, status == 409 {
                    continue // sigue en vuelo: esperar y reintentar
                }
                if case .servidor(let status, _) = sseError, (400..<500).contains(status) {
                    // Rechazo definitivo (hash distinto, clave inválida…):
                    // reintentar no sirve. Mensaje honesto y fin.
                    marcarBurbujaAtascada(respuestaId)
                    return
                }
                continue // red caída de nuevo: seguir esperando
            } catch {
                continue // conexión de nuevo caída: seguir esperando
            }
        }
        marcarBurbujaAtascada(respuestaId)
    }

    /// Tras el timeout de reconexión: honesto y accionable — el push del
    /// servidor avisa igualmente cuando el turno termine.
    private func marcarBurbujaAtascada(_ respuestaId: String) {
        if let indice = items.firstIndex(where: { $0.id == respuestaId }) {
            items[indice].enProgreso = false
            if items[indice].texto.isEmpty {
                items[indice].texto =
                    "Sigo trabajando en segundo plano; cuando termine te llega el aviso."
            }
        }
    }

    private func quitarBurbujaVacia(_ id: String) {
        guard let indice = items.firstIndex(where: { $0.id == id }),
              items[indice].texto.isEmpty
        else { return }
        items.remove(at: indice)
    }

    private func construirPeticion(
        client: APIClient, texto: String, adjuntos: [AdjuntoBot], clave: String
    ) async throws -> URLRequest {
        let url = try await client.urlCompleta("/v1/agents/workers/\(bot.id)/message")
        let token = try await client.tokenDeAccesoValido()
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        // Idempotencia: con esta clave el backend desacopla el turno del
        // socket — si la app se suspende o la red cae, el mismo POST con la
        // misma clave entrega el replay exacto sin duplicar el trabajo.
        request.setValue(clave, forHTTPHeaderField: "Idempotency-Key")
        struct Cuerpo: Encodable {
            let text: String
            let attachments: [String]
        }
        request.httpBody = try JSONEncoder().encode(
            Cuerpo(text: texto, attachments: adjuntos.map(\.fileId))
        )
        return request
    }

    private func cargar() async {
        guard let client = session.client else {
            error = "No hay sesión activa."
            cargando = false
            return
        }
        cargando = items.isEmpty
        error = nil
        defer { cargando = false }
        do {
            let historial = try await client.listWorkerMessages(workerId: bot.id)
            if items.isEmpty || !enviandoTurno {
                items = historial.map { ItemMensajeBot.desde($0, nombreBot: bot.nombreVisible) }
            }
        } catch {
            self.error = "No pude cargar lo que hablamos. Revisa que la Mac esté encendida y vuelve a entrar."
        }
    }

    private func subirFotos(_ seleccion: [PhotosPickerItem]) async {
        guard let client = session.client else { return }
        for item in seleccion.prefix(4) {
            guard let data = try? await item.loadTransferable(type: Data.self),
                  let imagen = UIImage(data: data)
            else { continue }
            // Redimensionar y comprimir antes de subir: una foto de 12 MP
            // (>5 MB) rompía la visión del bot y el turno colgaba. Bajarla a
            // ~1280 px y JPEG 0.85 la hace analizable al instante.
            let tam = imagen.size
            let maxLado: CGFloat = 1280
            let escala = min(1, maxLado / max(tam.width, tam.height))
            let sizeMenor = CGSize(width: tam.width * escala, height: tam.height * escala)
            let formato = UIGraphicsImageRenderer(size: sizeMenor)
            let comprimida = formato.image { _ in
                imagen.draw(in: CGRect(origin: .zero, size: sizeMenor))
            }
            let url = FileManager.default.temporaryDirectory
                .appendingPathComponent(UUID().uuidString + ".jpg")
            guard let jpg = comprimida.jpegData(compressionQuality: 0.85),
                  (try? jpg.write(to: url)) != nil
            else { continue }
            do {
                let archivo = try await client.subirArchivo(
                    desde: url,
                    filename: "foto-\(Int(Date().timeIntervalSince1970)).jpg",
                    mimeType: "image/jpeg"
                )
                adjuntosPendientes.append(
                    AdjuntoBot(fileId: archivo.id, filename: archivo.filename, mime: "image/jpeg")
                )
            } catch {
                self.error = "No pude subir la imagen. Intenta de nuevo."
            }
            try? FileManager.default.removeItem(at: url)
        }
    }
}

/// Un adjunto dentro del chat de bot: imagen ya subida a /v1/files.
struct AdjuntoBot: Identifiable, Sendable {
    let fileId: String
    let filename: String
    let mime: String?

    var id: String { fileId }
}

/// Imagen autenticada dentro del chat: miniatura del servidor; tocar abre el
/// visor seguro con zoom.
private struct ImagenAdjuntaBot: View {
    let fileId: String
    let client: APIClient?
    let alTocar: () -> Void
    @State private var imagen: UIImage?

    var body: some View {
        Group {
            if let imagen {
                Image(uiImage: imagen)
                    .resizable()
                    .scaledToFit()
                    .frame(width: 240, height: 200)
                    .frame(maxWidth: 240, maxHeight: 200)
                    .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            } else {
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .fill(Color.secondary.opacity(0.12))
                    .frame(width: 180, height: 130)
                    .overlay(ProgressView())
            }
        }
        .onTapGesture { alTocar() }
        .task(id: fileId) {
            guard let client else { return }
            let descarga = try? await client.descargarMiniatura(
                ArtifactRef(fileId: fileId, filename: "imagen", mime: "image/jpeg"),
                maxPixeles: 900
            )
            if let data = descarga?.data {
                imagen = UIImage(data: data)
            }
        }
    }
}

/// Una nota de narración entre bots, agrupada por el mismo interlocutor.
private struct NotaNarracion: Identifiable {
    let de: String
    let tipo: String   // "escribio" | "recibio"
    let cara: CaraSnapshot?
    var mensajes: [String]
    var ultimo: String
    var id: String { "\(de)-\(mensajes.count)-\(ultimo)" }
}

/// Fila del chat: un mensaje normal o una nota de narración entre bots.
private enum FilaChatBot: Identifiable {
    case mensaje(ItemMensajeBot)
    case narracion(NotaNarracion)
    var id: String {
        switch self {
        case .mensaje(let item): item.id
        case .narracion(let nota): nota.id
        }
    }
}

/// Fila «{Nombre} está trabajando» del chat 1:1: cara del bot ENCIENDIDA.
private struct FilaEstadoTrabajandoBot: View {
    let bot: PersistentWorker

    var body: some View {
        HStack(alignment: .center, spacing: 10) {
            GrokFaceAvatar(bot: bot, size: 30, showOnline: false, animado: true, activo: true)
            HStack(spacing: 0) {
                Text("\(bot.nombreVisible) está ")
                    .foregroundStyle(.secondary)
                Text("trabajando")
                    .foregroundStyle(.primary.opacity(0.88))
                    .fontWeight(.medium)
            }
            .font(.subheadline)
            Spacer(minLength: 0)
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(EstadoTrabajandoChat.etiquetaAccesibilidad(nombreAgente: bot.nombreVisible))
    }
}

private struct EventoNarracion {
    let texto: String
    let de: String
    let goal: String
    let escribioA: Bool
    let cara: CaraSnapshot?
}

private struct ItemMensajeBot: Identifiable {
    let id: String
    var esUsuario: Bool
    var texto: String
    var nombreRemitente: String?
    var enProgreso: Bool
    var adjuntos: [AdjuntoBot]
    var evento: EventoNarracion?
    /// Narración EN VIVO del trabajo (toolStart/toolProgress/toolEnd):
    /// «usando buscar_web…», «✓ buscar_web». Mientras el bot trabaja, el
    /// dueño ve QUÉ hace — no un mudo «Trabajando…».
    var estadoTrabajo: String?

    init(
        id: String,
        esUsuario: Bool,
        texto: String,
        nombreRemitente: String? = nil,
        enProgreso: Bool = false,
        adjuntos: [AdjuntoBot] = [],
        evento: EventoNarracion? = nil,
        estadoTrabajo: String? = nil
    ) {
        self.id = id
        self.esUsuario = esUsuario
        self.texto = texto
        self.nombreRemitente = nombreRemitente
        self.enProgreso = enProgreso
        self.adjuntos = adjuntos
        self.evento = evento
        self.estadoTrabajo = estadoTrabajo
    }

    static func desde(_ mensaje: TeamMessage, nombreBot: String) -> ItemMensajeBot {
        var evento: EventoNarracion?
        var adjuntos: [AdjuntoBot] = []
        if mensaje.kind == "evento", let ev = mensaje.evento, !ev.isEmpty {
            evento = EventoNarracion(
                texto: mensaje.text,
                de: mensaje.de ?? "",
                goal: mensaje.goal ?? "",
                escribioA: ev == "escribio_a",
                cara: mensaje.cara
            )
        }
        if let refs = mensaje.adjuntos {
            adjuntos = refs.map {
                AdjuntoBot(fileId: $0.fileId, filename: $0.filename ?? "archivo", mime: $0.mime)
            }
        }
        return ItemMensajeBot(
            id: mensaje.id,
            esUsuario: mensaje.esDelDueno,
            texto: mensaje.text,
            nombreRemitente: mensaje.esDelDueno ? nil : (mensaje.senderName ?? nombreBot),
            enProgreso: false,
            adjuntos: adjuntos,
            evento: evento
        )
    }
}