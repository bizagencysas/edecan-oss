import SwiftUI
import EdecanKit
import WidgetKit

/// Selección de pestaña compartida vía `@Environment` — permite que una
/// pantalla cambie la pestaña activa de otra (p. ej. ``VozView`` llevando a
/// Ajustes para conectar una credencial de voz real). Vive fuera de
/// ``RootTabView`` para que cualquier pantalla pueda leerla/escribirla sin
/// pasar bindings a mano por cada nivel.
@MainActor
@Observable
final class TabRouter {
    var seleccion: AssistantDestination = .edecan
    var solicitudPendiente: SolicitudRapida?
    var compartidoPendiente: CompartidoSolicitado?
    /// Frente 3 (deeplink): conversación que pidió abrir el último push
    /// tocado. `ChatView` la consume con el mismo patrón que
    /// `solicitudPendiente` — un `.onChange(of: conversacionPendiente?.id)`
    /// que llama a `consumirConversacionPendiente()` y abre esa conversación
    /// con `ChatViewModel.abrirConversacion(id:client:)`.
    var conversacionPendiente: ConversacionSolicitada?
    var abrirVozPendiente = false
    var presentacion: Presentacion?

    enum Presentacion: Identifiable {
        case remote
        /// Frente 6 (deeplink de llamada): abre `LlamadaEnVivoView` de esa
        /// llamada puntual encima de la pestaña que estuviera activa.
        case llamadaEnVivo(callId: String)
        /// `work_failed` / `work_completed` de una misión: Actividad no lista
        /// el trabajo, así que se abre el detalle encima.
        case mision(missionId: String)

        var id: String {
            switch self {
            case .remote: return "remote"
            case .llamadaEnVivo(let callId): return "llamada-\(callId)"
            case .mision(let missionId): return "mision-\(missionId)"
            }
        }
    }

    struct SolicitudRapida: Identifiable, Equatable {
        let id = UUID()
        let texto: String
    }

    struct CompartidoSolicitado: Identifiable {
        let id = UUID()
        let texto: String
        let archivos: [URL]
    }

    struct ConversacionSolicitada: Identifiable, Equatable {
        let id = UUID()
        let conversationId: String
    }

    func pedir(_ texto: String) {
        solicitudPendiente = SolicitudRapida(texto: texto)
        seleccion = .edecan
    }

    func consumirSolicitud() -> SolicitudRapida? {
        defer { solicitudPendiente = nil }
        return solicitudPendiente
    }

    func pedirCompartido(texto: String, archivos: [URL]) {
        compartidoPendiente = CompartidoSolicitado(texto: texto, archivos: archivos)
        seleccion = .edecan
    }

    func consumirCompartido() -> CompartidoSolicitado? {
        defer { compartidoPendiente = nil }
        return compartidoPendiente
    }

    /// Deja lista la conversación a abrir y cambia a la pestaña del
    /// asistente. Igual que `pedir(_:)`, no abre nada por sí sola — quien
    /// dibuja `ChatView` decide cuándo consumirla.
    func abrirConversacionDesdeNotificacion(_ conversationId: String) {
        conversacionPendiente = ConversacionSolicitada(conversationId: conversationId)
        seleccion = .edecan
    }

    func consumirConversacionPendiente() -> ConversacionSolicitada? {
        defer { conversacionPendiente = nil }
        return conversacionPendiente
    }

    func mostrarRemoto() {
        presentacion = .remote
    }

    func hablarConCompanero(nombre: String, proposito: String, tarea: String) {
        encargarACompanero(nombre: nombre, proposito: proposito, tarea: tarea)
        abrirVozPendiente = true
    }

    func encargarACompanero(nombre: String, proposito: String, tarea: String) {
        pedir(
            """
            Eres \(nombre), compañero de equipo de Edecán. Oficio: \(proposito).
            Haz el trabajo de punta a punta con las tools reales. No listes un plan.
            Solo vuelve cuando necesites mi OK.
            Encargo: \(tarea)
            """
        )
    }

    /// Frente 6 (deeplink de llamada): tocar el push de una llamada ENTRANTE
    /// debe abrir su vista en vivo, no solo dejar a la persona en la pestaña
    /// Actividad para que la busque a mano.
    func mostrarLlamadaEnVivo(callId: String) {
        presentacion = .llamadaEnVivo(callId: callId)
    }

    func mostrarMisionDesdeNotificacion(missionId: String) {
        presentacion = .mision(missionId: missionId)
        seleccion = .activity
    }

    /// Mensajes del asistente no vistos en la conversación abierta. Lo
    /// actualiza ``ChatView`` para el badge de la pestaña Edecán.
    var mensajesNoLeidosEnChat = 0
}

/// Navegación assistant-first: Edecan es la conversación universal,
/// Actividad concentra el trabajo delegado y Ajustes guarda configuración y
/// herramientas avanzadas. IDE, Negocios y Voz siguen existiendo, pero ya no
/// compiten con el asistente como pestañas independientes.
///
/// Nota Liquid Glass: un `TabView` estándar en iOS 26 YA adopta
/// automáticamente la barra flotante translúcida del sistema — no hace
/// falta ningún modifier extra para eso (es el comportamiento por defecto
/// del framework en este deployment target). El `if #available(iOS 26, *)`
/// con fallback a `.ultraThinMaterial` que pide la especificación de este
/// WP vive en ``TarjetaVidrio`` (`Theme.swift`), que sí se aplica a mano en
/// tarjetas/burbujas propias de esta app (`OnboardingView`, `ChatView`,
/// `EmptyStateView`) — ahí es donde el código realmente elige entre
/// `glassEffect` real y el fallback, no en la tab bar misma.
struct RootTabView: View {
    @State private var router = TabRouter()
    @Environment(SessionStore.self) private var session
    @Environment(PushNotificationCoordinator.self) private var push

    var body: some View {
        @Bindable var router = router
        TabView(selection: $router.seleccion) {
            ForEach(tabsVisibles) { tab in
                Group {
                    if tab.destino == .edecan, router.mensajesNoLeidosEnChat > 0 {
                        vista(tab.destino)
                            .badge(router.mensajesNoLeidosEnChat)
                    } else {
                        vista(tab.destino)
                    }
                }
                .tabItem { Label(tab.title, systemImage: tab.systemIcon) }
                .tag(tab.destino)
            }
        }
        .tint(EdecanTheme.morado)
        .environment(router)
        .task { await session.cargarMobileConfig() }
        .task {
            guard let client = session.client else { return }
            await WidgetSnapshotStore.refresh(client: client)
            WidgetCenter.shared.reloadTimelines(ofKind: "EdecanEstadoWidget")
        }
        .task {
            guard let pending = UserDefaults.standard.string(forKey: "cc.edecan.pending-share.v1") else {
                return
            }
            UserDefaults.standard.removeObject(forKey: "cc.edecan.pending-share.v1")
            router.pedir(pending)
        }
        .onReceive(NotificationCenter.default.publisher(for: .edecanShareText)) { notification in
            guard let text = notification.object as? String else { return }
            UserDefaults.standard.removeObject(forKey: "cc.edecan.pending-share.v1")
            router.pedir(text)
        }
        .onReceive(NotificationCenter.default.publisher(for: .edecanSharePayloads)) { notification in
            guard let payloads = notification.object as? [SharedSharePayload] else { return }
            router.pedirCompartido(
                texto: payloads.filter { $0.kind == "text" || $0.kind == "url" }
                    .map(\.value).joined(separator: "\n\n"),
                archivos: payloads.filter { $0.kind == "file" }
                    .map { URL(fileURLWithPath: $0.value) }
            )
        }
        .task {
            let payloads = SharePayloadStore.consume()
            guard !payloads.isEmpty else { return }
            router.pedirCompartido(
                texto: payloads.filter { $0.kind == "text" || $0.kind == "url" }
                    .map(\.value).joined(separator: "\n\n"),
                archivos: payloads.filter { $0.kind == "file" }
                    .map { URL(fileURLWithPath: $0.value) }
            )
        }
        .sheet(item: $router.presentacion) { presentacion in
            switch presentacion {
            case .remote:
                NavigationStack { RemotoView() }
                    .environment(router)
            case .llamadaEnVivo(let callId):
                NavigationStack { CargadorLlamadaEnVivo(callId: callId) }
            case .mision(let missionId):
                NavigationStack { CargadorMisionDesdeNotificacion(missionId: missionId) }
            }
        }
        .onChange(of: push.rutaPendiente) { _, route in
            guard let route else { return }
            switch route {
            case .assistant:
                // Frente 3 (deeplink): si el push traía `chat_id`, abre esa
                // conversación puntual; si no, comportamiento de siempre
                // (solo cambia a la pestaña del asistente).
                if let conversationId = push.conversacionPendiente {
                    router.abrirConversacionDesdeNotificacion(conversationId)
                } else {
                    router.seleccion = .edecan
                }
            case .activity:
                // Frente 6 (deeplink): una llamada ENTRANTE abre su vista en
                // vivo encima de Actividad. Una misión fallida o terminada
                // abre su detalle: Actividad sola es una grilla de atajos y
                // no muestra el trabajo. El resto (automatización,
                // recordatorio, resumen de llamada ya terminada) se queda
                // con el comportamiento de siempre.
                router.seleccion = .activity
                if let callId = push.llamadaPendiente {
                    router.mostrarLlamadaEnVivo(callId: callId)
                } else if let missionId = push.misionPendiente {
                    router.mostrarMisionDesdeNotificacion(missionId: missionId)
                }
            case .settings: router.seleccion = .settings
            case .create:
                router.pedir("Crea ")
            case .remote:
                router.mostrarRemoto()
            }
            push.rutaPendiente = nil
            push.conversacionPendiente = nil
            push.llamadaPendiente = nil
            push.misionPendiente = nil
        }
    }

    private var tabsVisibles: [ResolvedMobileTab] {
        var tabs = session.mobileConfig.tabs
            .filter(\.enabled)
            .compactMap(ResolvedMobileTab.init(config:))
        var vistos = Set<AssistantDestination>()
        tabs = tabs.filter { tab in
            guard !vistos.contains(tab.destino) else { return false }
            vistos.insert(tab.destino)
            return true
        }
        if !tabs.contains(where: { $0.destino == .equipo }) {
            tabs.append(
                ResolvedMobileTab(
                    id: "bots",
                    destino: .equipo,
                    title: "Bots",
                    systemIcon: "sparkles",
                    order: 1
                )
            )
        }
        tabs.sort { $0.order < $1.order }
        return tabs.isEmpty ? ResolvedMobileTab.fallback : tabs
    }

    @ViewBuilder
    private func vista(_ destino: AssistantDestination) -> some View {
        switch destino {
        case .edecan:
            ChatView()
        case .equipo, .teams:
            NavigationStack { BotsChatsView() }
        case .activity:
            InicioView()
        case .ide:
            IDERemoteSessionsView()
        case .settings:
            PerfilView()
        }
    }
}

private struct ResolvedMobileTab: Identifiable {
    let id: String
    let destino: AssistantDestination
    let title: String
    let systemIcon: String
    let order: Int

    init?(config: MobileTabConfig) {
        guard let destino = Self.destino(for: config.id) else { return nil }
        self.id = config.id
        self.destino = destino
        self.title = Self.tituloVisible(para: destino, configTitle: config.title)
        self.systemIcon = Self.iconoVisible(para: destino, configIcon: config.systemIcon)
        self.order = config.order
    }

    init(id: String, destino: AssistantDestination, title: String, systemIcon: String, order: Int) {
        self.id = id
        self.destino = destino
        self.title = title
        self.systemIcon = systemIcon
        self.order = order
    }

    static let fallback = [
        ResolvedMobileTab(id: "assistant", destino: .edecan, title: "Edecán", systemIcon: "bubble.left.and.bubble.right.fill", order: 0),
        ResolvedMobileTab(id: "bots", destino: .equipo, title: "Bots", systemIcon: "sparkles", order: 1),
        ResolvedMobileTab(id: "activity", destino: .activity, title: "Actividad", systemIcon: "clock.arrow.circlepath", order: 2),
        ResolvedMobileTab(id: "ide", destino: .ide, title: "IDE", systemIcon: "chevron.left.forwardslash.chevron.right", order: 3),
        ResolvedMobileTab(id: "profile", destino: .settings, title: "Tú", systemIcon: "person.crop.circle.fill", order: 4),
    ]

    private static func destino(for id: String) -> AssistantDestination? {
        switch id {
        case "assistant", "chat", "edecan":
            return .edecan
        case "bots", "equipo", "workers", "team", "teams", "equipos", "chats":
            return .equipo
        case "activity", "inicio":
            return .activity
        case "ide", "studio":
            return .ide
        case "profile", "settings", "you":
            return .settings
        default:
            return nil
        }
    }

    /// Bots agrupa chats 1:1 y de grupo; no confiamos en títulos viejos del servidor.
    private static func tituloVisible(para destino: AssistantDestination, configTitle: String) -> String {
        switch destino {
        case .equipo, .teams:
            return "Bots"
        default:
            return configTitle
        }
    }

    private static func iconoVisible(para destino: AssistantDestination, configIcon: String) -> String {
        switch destino {
        case .equipo, .teams:
            return "sparkles"
        default:
            return configIcon
        }
    }
}

/// Frente 6 (deeplink de llamada): `LlamadaEnVivoView` exige un
/// `PhoneCallOut` ya resuelto -- normalmente se llega por `NavigationLink`
/// desde `LlamadasView` con el objeto en mano. Tocar el push de una llamada
/// entrante solo trae el id, así que este cargador lo resuelve
/// (`APIClient.obtenerLlamada(id:)`) antes de mostrar la vista en vivo.
private struct CargadorLlamadaEnVivo: View {
    @Environment(SessionStore.self) private var session
    @Environment(\.dismiss) private var dismiss
    let callId: String
    @State private var llamada: PhoneCallOut?
    @State private var mensajeError: String?

    var body: some View {
        Group {
            if let llamada {
                LlamadaEnVivoView(llamada: llamada)
            } else if let mensajeError {
                EmptyStateView(
                    icono: "phone.badge.waveform",
                    titulo: "No se pudo abrir la llamada",
                    descripcion: mensajeError
                )
            } else {
                ProgressView("Abriendo la llamada…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                Button("Cerrar") { dismiss() }
            }
        }
        .task { await cargar() }
    }

    private func cargar() async {
        guard let client = session.client else {
            mensajeError = "No hay sesión activa."
            return
        }
        do {
            llamada = try await client.obtenerLlamada(id: callId)
        } catch {
            mensajeError = error.localizedDescription
        }
    }
}

/// `work_failed` / `work_completed` de una misión: el push solo trae el id,
/// así que este cargador reutiliza ``MisionDetalleView`` (la misma pantalla
/// que se abre desde Trabajo delegado) encima de Actividad.
private struct CargadorMisionDesdeNotificacion: View {
    @Environment(\.dismiss) private var dismiss
    let missionId: String
    @State private var viewModel = MisionesViewModel()

    var body: some View {
        MisionDetalleView(missionId: missionId, viewModel: viewModel)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cerrar") { dismiss() }
                }
            }
    }
}
