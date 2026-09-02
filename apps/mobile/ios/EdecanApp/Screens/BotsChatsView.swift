import SwiftUI
import EdecanKit

/// Lista estilo Grok Bot: bots 1:1 y chats de grupo mezclados en un solo
/// lugar, presentados como tarjetas de Liquid Glass.
struct BotsChatsView: View {
    @Environment(SessionStore.self) private var session
    @State private var bots: [PersistentWorker] = []
    @State private var equipos: [Team] = []
    @State private var filas: [FilaChat] = []
    @State private var busqueda = ""
    @State private var cargando = true
    @State private var error: String?
    @State private var proximamenteEquipos = false
    @State private var ocupado = false
    @State private var creandoBot = false
    @State private var creandoGrupo = false
    @State private var mostrandoBusqueda = false
    @State private var botPorEliminar: PersistentWorker?
    @State private var rutaChat: RutaBotsChat?

    var body: some View {
        VStack(spacing: 0) {
            cabecera
            ScrollView {
                LazyVStack(spacing: 12) {
                    if let error {
                        Text(error)
                            .font(.footnote)
                            .foregroundStyle(.red)
                            .padding(12)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .tarjetaVidrioFlotante(esquina: 14)
                    }
                    if proximamenteEquipos {
                        filaProximamente
                    }
                    if filasFiltradas.isEmpty && !cargando {
                        estadoVacio
                    }
                    ForEach(filasFiltradas) { fila in
                        Button {
                            abrir(fila)
                        } label: {
                            FilaChatView(fila: fila)
                        }
                        .buttonStyle(TarjetaChatButtonStyle())
                        .contextMenu {
                            if case .bot(let bot) = fila.tipo {
                                Button(role: .destructive) {
                                    botPorEliminar = bot
                                } label: {
                                    Label("Eliminar bot", systemImage: "trash")
                                }
                            }
                        }
                    }
                }
                .padding(.horizontal, 18)
                .padding(.top, 4)
                .padding(.bottom, 24)
            }
            .scrollIndicators(.hidden)
        }
        .estiloPantallaBots()
        .navigationBarHidden(true)
        .navigationDestination(item: $rutaChat) { ruta in
            switch ruta {
            case .bot(let id):
                if let bot = bots.first(where: { $0.id == id }) {
                    BotChatView(bot: bot)
                }
            case .grupo(let id):
                if let equipo = equipos.first(where: { $0.id == id }) {
                    TeamConversationView(equipo: equipo)
                }
            }
        }
        .overlay {
            if cargando && filas.isEmpty {
                ProgressView()
            }
        }
        .task { await cargar() }
        .refreshable { await cargar() }
        .sheet(isPresented: $creandoBot) {
            NavigationStack {
                NuevoBotSheet { nombre, descripcion, relacion, instrucciones, acentoHex in
                    Task {
                        await crearBot(
                            nombre: nombre,
                            descripcion: descripcion,
                            relacion: relacion,
                            instrucciones: instrucciones,
                            acentoHex: acentoHex
                        )
                    }
                }
            }
        }
        .sheet(isPresented: $creandoGrupo) {
            NavigationStack {
                NuevoGrupoSheet(workers: bots) { nombre, miembros in
                    Task { await crearGrupo(nombre: nombre, miembros: miembros) }
                }
            }
        }
        .confirmationDialog(
            "¿Eliminar \(botPorEliminar?.nombreVisible ?? "este bot")?",
            isPresented: Binding(
                get: { botPorEliminar != nil },
                set: { if !$0 { botPorEliminar = nil } }
            ),
            titleVisibility: .visible
        ) {
            Button("Eliminar bot y su chat", role: .destructive) {
                if let bot = botPorEliminar {
                    Task { await eliminarBot(bot) }
                }
                botPorEliminar = nil
            }
            Button("Cancelar", role: .cancel) { botPorEliminar = nil }
        } message: {
            Text("Se borra el bot, su chat y su memoria de conversación. No se puede deshacer.")
        }
    }

    /// Cabecera estilo Grok: logo de la app a la izquierda y dos círculos
    /// de vidrio (búsqueda, crear) a la derecha. Sin barra de navegación.
    private var cabecera: some View {
        VStack(spacing: 10) {
            HStack {
                Spacer()
                if mostrandoBusqueda {
                    botonCabeza(sistema: "xmark", etiqueta: "Cerrar búsqueda") {
                        withAnimation(.easeOut(duration: 0.18)) {
                            busqueda = ""
                            mostrandoBusqueda = false
                        }
                    }
                } else {
                    botonCabeza(sistema: "magnifyingglass", etiqueta: "Buscar") {
                        withAnimation(.easeOut(duration: 0.18)) { mostrandoBusqueda = true }
                    }
                    botonCabezaPlus
                }
            }
            .padding(.horizontal, 18)
            .padding(.top, 6)

            if mostrandoBusqueda {
                TextField("Buscar chats", text: $busqueda)
                    .textFieldStyle(.plain)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 12)
                    .capsulaVidrio()
                    .padding(.horizontal, 18)
                    .transition(.move(edge: .top).combined(with: .opacity))
            }
        }
        .padding(.bottom, 6)
    }

    /// El «+» es un `Menu` NATIVO anclado al botón (hereda el Liquid Glass
    /// del sistema). El `confirmationDialog` anterior flotaba centrado en la
    /// mitad/bajo de la pantalla — descolgado del botón. El menú contextual
    /// es la pieza correcta para dos acciones y vive junto al botón.
    private var botonCabezaPlus: some View {
        Menu {
            Button {
                creandoBot = true
            } label: {
                Label("Nuevo bot", systemImage: "person.crop.circle.badge.plus")
            }
            Button {
                creandoGrupo = true
            } label: {
                Label("Nuevo grupo", systemImage: "person.3.fill")
            }
        } label: {
            Image(systemName: "plus")
                .font(.system(size: 18, weight: .medium))
                .foregroundStyle(.primary)
                .frame(width: 44, height: 44)
                .capsulaVidrio()
        }
        .accessibilityLabel("Nuevo bot o grupo")
    }

    private func botonCabeza(sistema: String, etiqueta: String, accion: @escaping () -> Void) -> some View {
        BotonVidrioCircular(sistema: sistema, etiqueta: etiqueta, accion: accion)
    }

    private var estadoVacio: some View {
        VStack(spacing: 14) {
            ZStack {
                Circle()
                    .fill(EdecanTheme.morado.opacity(0.14))
                    .frame(width: 74, height: 74)
                Image(systemName: "sparkles")
                    .font(.system(size: 30, weight: .medium))
                    .foregroundStyle(EdecanTheme.morado)
            }
            VStack(spacing: 6) {
                Text("Empieza un chat")
                    .font(.headline)
                Text("Crea un bot con memoria propia o un grupo donde varios bots conversen contigo.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Button {
                creandoBot = true
            } label: {
                Label("Crear bot", systemImage: "plus")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.white)
                    .padding(.horizontal, 18)
                    .padding(.vertical, 12)
                    .background(Capsule().fill(EdecanTheme.botonEnviarNegro))
            }
            .buttonStyle(.plain)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 40)
    }

    private var filaProximamente: some View {
        VStack(alignment: .leading, spacing: 6) {
            Label("Grupos próximamente", systemImage: "hourglass")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(EdecanTheme.morado)
            Text("Los chats de grupo están llegando al servidor. Tus bots 1:1 siguen disponibles.")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .tarjetaVidrioFlotante(esquina: 14)
    }

    private func abrir(_ fila: FilaChat) {
        switch fila.tipo {
        case .bot(let bot):
            rutaChat = .bot(bot.id)
        case .grupo(let equipo):
            rutaChat = .grupo(equipo.id)
        }
    }

    private var filasFiltradas: [FilaChat] {
        let q = busqueda.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !q.isEmpty else { return filas }
        return filas.filter {
            $0.titulo.localizedCaseInsensitiveContains(q)
                || $0.subtitulo.localizedCaseInsensitiveContains(q)
        }
    }

    private func cargar() async {
        guard let client = session.client else {
            error = "No hay sesión activa."
            cargando = false
            return
        }
        cargando = filas.isEmpty
        error = nil
        proximamenteEquipos = false
        defer { cargando = false }

        do {
            do {
                bots = try await cargarConReintento { try await client.listWorkers() }
            } catch let apiError as APIClient.APIError where apiError.esProximamente {
                // Sin workers en el plan: lista vacía, no es un error de red.
                bots = []
            }
            do {
                equipos = try await cargarConReintento { try await client.listTeams() }
            } catch let apiError as APIClient.APIError where apiError.esProximamente {
                proximamenteEquipos = true
                equipos = []
            }
            await reconstruirFilas(client: client)
        } catch {
            self.error = error.localizedDescription
        }
    }

    /// Un blip transitorio (túnel reconectando, salto de red) no debe tirar
    /// la pantalla: UN reintento a los ~700 ms recupera la inmensa mayoría.
    /// Solo una vez — un 429 real necesita su ventana completa, no un reintento
    /// inmediato que lo empeora.
    private func cargarConReintento<T>(_ operacion: () async throws -> T) async throws -> T {
        do {
            return try await operacion()
        } catch {
            try? await Task.sleep(nanoseconds: 700_000_000)
            return try await operacion()
        }
    }

    private func reconstruirFilas(client: APIClient) async {
        var nuevas: [FilaChat] = []
        let fuentes: [FilaChat.Tipo] = bots.map { .bot($0) } + equipos.map { .grupo($0) }

        // Tandas de 4: la ráfaga de N previews a la vez a través del túnel es
        // justo el momento frágil cuando hay muchas cosas cargando. Escalonar
        // mantiene la lista fluida y acota el daño de un pico de red.
        var indice = 0
        while indice < fuentes.count {
            let lote = Array(fuentes[indice..<min(indice + 4, fuentes.count)])
            indice += lote.count
            await withTaskGroup(of: FilaChat?.self) { group in
                for fuente in lote {
                    group.addTask {
                        switch fuente {
                        case .bot(let bot):
                            let preview = await Self.vistaPreviaBot(client: client, bot: bot)
                            return FilaChat(
                                tipo: .bot(bot), titulo: bot.nombreVisible,
                                subtitulo: preview.snippet, fecha: preview.fecha
                            )
                        case .grupo(let equipo):
                            let preview = await Self.vistaPreviaGrupo(client: client, equipo: equipo)
                            return FilaChat(
                                tipo: .grupo(equipo),
                                titulo: equipo.name.isEmpty ? "Grupo" : equipo.name,
                                subtitulo: preview.snippet,
                                fecha: preview.fecha
                            )
                        }
                    }
                }
                for await fila in group {
                    if let fila { nuevas.append(fila) }
                }
            }
        }

        nuevas.sort { ($0.fecha ?? .distantPast) > ($1.fecha ?? .distantPast) }
        filas = nuevas
    }

    private static func vistaPreviaBot(client: APIClient, bot: PersistentWorker) async -> (snippet: String, fecha: Date?) {
        if let mensajes = try? await client.listWorkerMessages(workerId: bot.id),
           let ultimo = mensajes.last,
           !ultimo.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return (ultimo.text, ultimo.createdAt ?? bot.updatedAt)
        }
        let proposito = bot.purpose.trimmingCharacters(in: .whitespacesAndNewlines)
        return (proposito.isEmpty ? "Chat 1:1" : proposito, bot.updatedAt)
    }

    private static func vistaPreviaGrupo(client: APIClient, equipo: Team) async -> (snippet: String, fecha: Date?) {
        if let mensajes = try? await client.listTeamMessages(teamId: equipo.id),
           let ultimo = mensajes.last,
           !ultimo.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return (ultimo.text, ultimo.createdAt ?? equipo.createdAt)
        }
        if let descripcion = equipo.description, !descripcion.isEmpty {
            return (descripcion, equipo.createdAt)
        }
        let miembros = equipo.members.count
        return (
            miembros == 0 ? "Grupo sin miembros" : "\(miembros) bot\(miembros == 1 ? "" : "s")",
            equipo.createdAt
        )
    }

    private func eliminarBot(_ bot: PersistentWorker) async {
        guard let client = session.client else { return }
        ocupado = true
        defer { ocupado = false }
        do {
            try await client.deleteWorker(id: bot.id)
            filas.removeAll { fila in
                if case .bot(let b) = fila.tipo { return b.id == bot.id }
                return false
            }
            bots.removeAll { $0.id == bot.id }
            await cargar()
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func crearBot(
        nombre: String,
        descripcion: String,
        relacion: String = "profesional",
        instrucciones: String = "",
        acentoHex: String? = nil
    ) async {
        guard let client = session.client else { return }
        ocupado = true
        defer { ocupado = false }
        do {
            _ = try await client.createWorker(
                name: nombre,
                purpose: descripcion,
                displayName: nombre,
                avatarAccentHex: acentoHex,
                instructions: instrucciones.isEmpty ? nil : instrucciones,
                relation: relacion == "amigo" || relacion == "coach" ? relacion : "profesional"
            )
            creandoBot = false
            await cargar()
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func crearGrupo(nombre: String, miembros: [String]) async {
        guard let client = session.client else { return }
        ocupado = true
        defer { ocupado = false }
        do {
            let equipo = try await client.createTeam(name: nombre)
            for agenteId in miembros {
                try? await client.addTeamMember(teamId: equipo.id, agentId: agenteId)
            }
            creandoGrupo = false
            await cargar()
        } catch let apiError as APIClient.APIError {
            self.error = apiError.esProximamente
                ? "Los chats de grupo están llegando al servidor."
                : apiError.localizedDescription
        } catch {
            self.error = error.localizedDescription
        }
    }
}

private enum RutaBotsChat: Hashable {
    case bot(String)
    case grupo(String)
}

private struct FilaChat: Identifiable {
    enum Tipo {
        case bot(PersistentWorker)
        case grupo(Team)
    }

    let tipo: Tipo
    let titulo: String
    let subtitulo: String
    let fecha: Date?

    var id: String {
        switch tipo {
        case .bot(let bot): return "bot-\(bot.id)"
        case .grupo(let equipo): return "team-\(equipo.id)"
        }
    }
}

/// Fila como tarjeta flotante de vidrio — sin chrome de List ni chevron.
private struct FilaChatView: View {
    let fila: FilaChat

    var body: some View {
        HStack(spacing: 14) {
            leadingIcon
            VStack(alignment: .leading, spacing: 4) {
                Text(fila.titulo)
                    .font(.body.weight(.semibold))
                    .lineLimit(1)
                Text(fila.subtitulo)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
            Spacer(minLength: 8)
            if let fecha = fila.fecha {
                Text(Self.etiquetaDia(fecha))
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 14)
        .tarjetaVidrioFlotante(esquina: 20)
        .contentShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
    }

    /// «Ayer» si fue ayer, hora si es hoy, fecha corta si es anterior.
    static func etiquetaDia(_ fecha: Date) -> String {
        let cal = Calendar.current
        if cal.isDateInToday(fecha) {
            return fecha.formatted(date: .omitted, time: .shortened)
        }
        if cal.isDateInYesterday(fecha) {
            return "Ayer"
        }
        return fecha.formatted(.dateTime.day().month(.abbreviated))
    }

    @ViewBuilder
    private var leadingIcon: some View {
        switch fila.tipo {
        case .bot(let bot):
            AvatarBot(bot: bot, tamanio: 56)
        case .grupo:
            ZStack {
                Circle().fill(EdecanTheme.morado.opacity(0.14))
                Image(systemName: "person.3.fill")
                    .font(.system(size: 17, weight: .medium))
                    .foregroundStyle(EdecanTheme.morado)
            }
            .frame(width: 44, height: 44)
        }
    }
}

/// Press feedback sutil en tarjetas de chat (sin tinte gris de List).
private struct TarjetaChatButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.985 : 1)
            .opacity(configuration.isPressed ? 0.92 : 1)
            .animation(.easeOut(duration: 0.14), value: configuration.isPressed)
    }
}

private struct AvatarBot: View {
    let bot: PersistentWorker
    var tamanio: CGFloat = 44

    var body: some View {
        if bot.avatarStyle == "grok_face" {
            // La vida se ve en el chat (aro encendido al trabajar); la lista
            // queda limpia sin punto verde, como el capture de Grok.
            GrokFaceAvatar(bot: bot, size: tamanio, showOnline: false)
        } else {
            ZStack {
                Circle().fill(degradado)
                Text(iniciales)
                    .font(.system(size: tamanio * 0.40, weight: .bold, design: .rounded))
                    .foregroundStyle(.white)
            }
            .frame(width: tamanio, height: tamanio)
        }
    }

    private var acento: Color {
        if let hex = bot.avatarAccentHex {
            return AcentoAvatar.color(hex: hex)
        }
        return AcentoAvatar.determinista(bot.nombreVisible).color
    }

    private var degradado: LinearGradient {
        LinearGradient(
            colors: [acento, acento.opacity(0.74), acento.opacity(0.48)],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
    }

    private var iniciales: String {
        if let letras = bot.avatarInitials,
           !letras.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return letras
        }
        let partes = bot.nombreVisible.split(separator: " ").prefix(2)
        return partes.map { String($0.prefix(1)).uppercased() }.joined()
    }
}

private struct NuevoBotSheet: View {
    @Environment(\.dismiss) private var dismiss
    @Namespace private var vidrioNamespace

    @State private var nombre = ""
    @State private var descripcion = ""
    @State private var relacion = "profesional"
    @State private var instrucciones = ""
    @State private var forma = "circle"
    @State private var acentoHex = AcentoAvatar.tonos[0].hex

    let onCrear: (String, String, String, String, String?) -> Void

    private let formas: [(id: String, titulo: String, icono: String)] = [
        ("circle", "Círculo", "circle.fill"),
        ("rounded_square", "Cuadrado", "square.fill"),
        ("oval", "Óvalo", "oval.fill"),
        ("hexagon", "Hexágono", "hexagon.fill"),
        ("squircle", "Squircle", "app.fill"),
    ]

    private var nombreLimpio: String {
        nombre.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var valido: Bool {
        !nombreLimpio.isEmpty && !descripcion.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private var acentoColor: Color {
        AcentoAvatar.color(hex: acentoHex)
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 18) {
                seccionPreview
                seccionIdentidad
                seccionPersonalizacion
                seccionRelacion
                botonCrear
            }
            .padding(.horizontal, 18)
            .padding(.vertical, 12)
        }
        .estiloPantallaBots()
        .navigationTitle("Nuevo bot")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                Button("Cancelar") { dismiss() }
            }
        }
    }

    private var seccionPreview: some View {
        VStack(spacing: 14) {
            Text("Vista previa")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .leading)

            ContenedorVidrioBots(spacing: 16) {
                VStack(spacing: 12) {
                    CaraOrbe(
                        nombre: nombreLimpio.isEmpty ? "Tu bot" : nombreLimpio,
                        formaBot: forma,
                        fillHex: acentoHex,
                        accentHex: acentoHex,
                        ojoIzq: OjoDeCara(x: 0.34, y: 0.38, rx: 0.07, ry: 0.08, rotation: -8),
                        ojoDer: OjoDeCara(x: 0.66, y: 0.38, rx: 0.07, ry: 0.08, rotation: 8),
                        size: 96,
                        animado: true,
                        activo: false
                    )
                    .vidrioMorphID("preview-cara", in: vidrioNamespace)

                    VStack(spacing: 4) {
                        Text(nombreLimpio.isEmpty ? "Tu bot" : nombreLimpio)
                            .font(.title3.weight(.bold))
                        Text(descripcion.isEmpty ? "Su descripción aparecerá aquí." : descripcion)
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                            .multilineTextAlignment(.center)
                            .lineLimit(3)
                    }
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 20)
                .padding(.horizontal, 16)
                .tarjetaVidrio(esquina: 22, tint: acentoColor)
                .vidrioMorphID("preview-card", in: vidrioNamespace)
            }
        }
    }

    private var seccionIdentidad: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Identidad")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            VStack(spacing: 12) {
                campoGlass("Nombre", text: $nombre, hint: "Ej. Analista, Coach…")
                campoGlass("Descripción", text: $descripcion, hint: "En qué es experto y cómo te ayuda", axis: .vertical)
            }
            .padding(14)
            .tarjetaVidrio(esquina: 18)
            Text("La descripción define personalidad y rol. Podrás cambiarla después.")
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
    }

    private var seccionPersonalizacion: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Apariencia")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 14) {
                Text("Color")
                    .font(.footnote.weight(.medium))
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 10) {
                        ForEach(AcentoAvatar.tonos) { tono in
                            Button {
                                withAnimation(.spring(response: 0.32, dampingFraction: 0.78)) {
                                    acentoHex = tono.hex
                                }
                            } label: {
                                Circle()
                                    .fill(tono.color)
                                    .frame(width: 34, height: 34)
                                    .overlay {
                                        if acentoHex == tono.hex {
                                            Image(systemName: "checkmark")
                                                .font(.caption.weight(.bold))
                                                .foregroundStyle(.white)
                                        }
                                    }
                                    .padding(5)
                                    .capsulaVidrio()
                                    .scaleEffect(acentoHex == tono.hex ? 1.06 : 1)
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }

                Text("Forma")
                    .font(.footnote.weight(.medium))
                HStack(spacing: 8) {
                    ForEach(formas, id: \.id) { item in
                        Button {
                            withAnimation(.spring(response: 0.35, dampingFraction: 0.8)) {
                                forma = item.id
                            }
                        } label: {
                            VStack(spacing: 4) {
                                Image(systemName: item.icono)
                                    .font(.system(size: 16, weight: .medium))
                                Text(item.titulo)
                                    .font(.caption2)
                                    .lineLimit(1)
                            }
                            .foregroundStyle(forma == item.id ? Color.primary : .secondary)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 10)
                            .tarjetaVidrio(esquina: 12, tint: forma == item.id ? acentoColor : nil)
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
            .padding(14)
            .tarjetaVidrio(esquina: 18)
            Text("El color se guarda al crear. Forma y ojos se generan en el servidor según el nombre.")
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
    }

    private var seccionRelacion: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Cómo te trata")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            VStack(spacing: 12) {
                HStack(spacing: 8) {
                    pillRelacion("Socio", tag: "profesional")
                    pillRelacion("Amigo", tag: "amigo")
                    pillRelacion("Coach", tag: "coach")
                }
                campoGlass("Instrucciones extra (opcional)", text: $instrucciones, hint: "Tono, límites, preferencias…", axis: .vertical)
            }
            .padding(14)
            .tarjetaVidrio(esquina: 18)
        }
    }

    private func pillRelacion(_ titulo: String, tag: String) -> some View {
        Button {
            withAnimation(.easeOut(duration: 0.18)) { relacion = tag }
        } label: {
            Text(titulo)
                .font(.footnote.weight(.semibold))
                .frame(maxWidth: .infinity)
                .padding(.vertical, 10)
                .foregroundStyle(relacion == tag ? .white : .primary)
                .background {
                    if relacion == tag {
                        Capsule().fill(EdecanTheme.botonEnviarNegro)
                    } else {
                        Capsule().fill(.clear)
                    }
                }
                .capsulaVidrio()
        }
        .buttonStyle(.plain)
    }

    private var botonCrear: some View {
        Button {
            onCrear(
                nombreLimpio,
                descripcion.trimmingCharacters(in: .whitespacesAndNewlines),
                relacion,
                instrucciones.trimmingCharacters(in: .whitespacesAndNewlines),
                acentoHex
            )
            dismiss()
        } label: {
            Text("Crear bot")
                .font(.headline.weight(.semibold))
                .foregroundStyle(.white)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 16)
                .background(
                    Capsule().fill(valido ? EdecanTheme.botonEnviarNegro : Color.secondary.opacity(0.35))
                )
        }
        .buttonStyle(.plain)
        .disabled(!valido)
        .padding(.top, 4)
        .padding(.bottom, 8)
    }

    private func campoGlass(
        _ titulo: String,
        text: Binding<String>,
        hint: String,
        axis: Axis = .horizontal
    ) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(titulo)
                .font(.footnote.weight(.medium))
                .foregroundStyle(.secondary)
            if axis == .vertical {
                TextField(hint, text: text, axis: .vertical)
                    .lineLimit(3...6)
            } else {
                TextField(hint, text: text)
            }
        }
        .textFieldStyle(.plain)
    }
}

private struct NuevoGrupoSheet: View {
    @Environment(\.dismiss) private var dismiss
    let workers: [PersistentWorker]
    let onCrear: (String, [String]) -> Void
    @State private var nombre = ""
    @State private var seleccionados = Set<String>()

    private var nombreLimpio: String {
        nombre.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var valido: Bool {
        !nombreLimpio.isEmpty && !seleccionados.isEmpty
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 18) {
                seccionNombre
                seccionMiembros
                botonCrear
            }
            .padding(.horizontal, 18)
            .padding(.vertical, 12)
        }
        .estiloPantallaBots()
        .navigationTitle("Nuevo grupo")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                Button("Cancelar") { dismiss() }
            }
        }
    }

    private var seccionNombre: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Nombre del grupo")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            VStack(spacing: 12) {
                campoGlass("Nombre", text: $nombre, hint: "Ej. Comercial, Operaciones…")
            }
            .padding(14)
            .tarjetaVidrio(esquina: 18)
        }
    }

    private var seccionMiembros: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Bots en el grupo")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                Spacer()
                if !seleccionados.isEmpty {
                    Text("\(seleccionados.count) seleccionado\(seleccionados.count == 1 ? "" : "s")")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
            }
            if workers.isEmpty {
                VStack(spacing: 8) {
                    Image(systemName: "person.crop.circle.badge.plus")
                        .font(.title2)
                        .foregroundStyle(EdecanTheme.morado.opacity(0.7))
                    Text("Crea al menos un bot antes de armar un grupo.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 24)
                .tarjetaVidrio(esquina: 18)
            } else {
                VStack(spacing: 8) {
                    ForEach(workers) { worker in
                        Button {
                            toggle(worker.id)
                        } label: {
                            HStack(spacing: 12) {
                                AvatarBot(bot: worker, tamanio: 40)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(worker.nombreVisible)
                                        .foregroundStyle(.primary)
                                        .font(.subheadline.weight(.medium))
                                    if !worker.purpose.isEmpty {
                                        Text(worker.purpose)
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                            .lineLimit(1)
                                    }
                                }
                                Spacer(minLength: 6)
                                Image(systemName: seleccionados.contains(worker.id) ? "checkmark.circle.fill" : "circle")
                                    .font(.title3)
                                    .foregroundStyle(
                                        seleccionados.contains(worker.id) ? EdecanTheme.morado : Color.secondary.opacity(0.35)
                                    )
                            }
                            .padding(.horizontal, 12)
                            .padding(.vertical, 10)
                            .tarjetaVidrio(
                                esquina: 14,
                                tint: seleccionados.contains(worker.id) ? EdecanTheme.morado : nil
                            )
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(12)
                .tarjetaVidrio(esquina: 18)
            }
        }
    }

    private var botonCrear: some View {
        Button {
            onCrear(nombreLimpio, Array(seleccionados))
            dismiss()
        } label: {
            Text("Crear grupo")
                .font(.headline.weight(.semibold))
                .foregroundStyle(.white)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 16)
                .background(
                    Capsule().fill(valido ? EdecanTheme.botonEnviarNegro : Color.secondary.opacity(0.35))
                )
        }
        .buttonStyle(.plain)
        .disabled(!valido)
        .padding(.top, 4)
        .padding(.bottom, 8)
    }

    private func campoGlass(
        _ titulo: String,
        text: Binding<String>,
        hint: String
    ) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(titulo)
                .font(.footnote.weight(.medium))
                .foregroundStyle(.secondary)
            TextField(hint, text: text)
        }
        .textFieldStyle(.plain)
    }

    private func toggle(_ id: String) {
        withAnimation(.easeOut(duration: 0.18)) {
            if seleccionados.contains(id) {
                seleccionados.remove(id)
            } else {
                seleccionados.insert(id)
            }
        }
    }
}
