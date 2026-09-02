import Foundation
import EdecanKit

struct AprobacionPendienteVista: Sendable {
    let id: String
    let nombre: String
    let detalle: String
}

/// Cerebro del Watch en el iPhone: tablero completo y comandos reales
/// (gym, avisos, chat, aprobaciones, llamadas).
@MainActor
final class WatchCompanion {
    static let compartido = WatchCompanion()

    private var client: APIClient?
    private var loop: Task<Void, Never>?
    private var descansoTask: Task<Void, Never>?
    private let health = HealthKitManager.compartido
    private var chat: ChatViewModel?
    private var idsAprobacionesConocidas: Set<String>?

    func configurar(client: APIClient?) {
        self.client = client
        idsAprobacionesConocidas = nil
        PhoneWatchBridge.compartido.activarSiHaceFalta()
        PhoneWatchBridge.compartido.onComando = { [weak self] comando in
            Task { await self?.manejar(comando) }
        }
        loop?.cancel()
        descansoTask?.cancel()
        guard client != nil else { return }
        loop = Task { [weak self] in
            while !Task.isCancelled {
                await self?.sincronizar()
                let espera = PhoneWatchBridge.compartido.relojAlcanzable ? 8 : 30
                try? await Task.sleep(for: .seconds(espera))
            }
        }
        Task { await health.solicitarAutorizacion() }
    }

    func sincronizar() async {
        guard let client else { return }
        let sesionActual = try? await client.gymSesionActual()
        let planHoy = try? await client.gymPlanDeHoy()
        let historial = try? await client.gymHistorial(limit: 5)
        let recordatorios = try? await client.listReminders()
        let misiones = try? await client.listMissions()
        let approvals = try? await client.listApprovals()
        let llamadas = try? await client.listarLlamadas()
        let rutinas = try? await client.listAutomations()
        let equipo = try? await client.listWorkers()

        let racha = historial?.streak ?? 0
        let avisos = (recordatorios ?? []).filter { !$0.completado }.prefix(8)
        let misionesVivas = (misiones ?? []).filter(\.visibleEnWatch)
        let pendientes = (approvals ?? []).filter { $0.status == nil || $0.status == "pending" }.prefix(6)
        let llamada = (llamadas ?? []).first { $0.status == "ringing" || $0.status == "in_progress" }
        let rutinasOn = (rutinas ?? []).filter(\.enabled).count

        let viva = sesionActual?.status == "active" || sesionActual?.status == "paused"
        let activa = sesionActual?.status == "active"
        let pausada = sesionActual?.status == "paused"
        let titulo = sesionActual?.plan.title ?? planHoy?.title
        let tienePlan = sesionActual != nil || planHoy != nil
        var cronometro: TimeInterval?
        if let raw = sesionActual?.startedAt {
            let iso = ISO8601DateFormatter()
            iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            if let fecha = iso.date(from: raw) ?? ISO8601DateFormatter().date(from: raw) {
                cronometro = Date().timeIntervalSince(fecha)
            }
        }

        if viva {
            if PhoneWatchBridge.compartido.relojAlcanzable {
                if health.siguiendo {
                    health.abandonarSeguimientoSinGuardar()
                }
            } else if activa, !health.siguiendo {
                health.iniciarSeguimiento()
            } else if pausada {
                health.pausarSeguimiento()
            } else if activa {
                health.reanudarSeguimiento()
            }
        } else if health.siguiendo {
            await health.detenerSeguimiento(fin: Date())
        }

        var ejercicio = ""
        var hechas = 0
        var totales = 0
        var repsSug = 0
        var pesoSug: Double = 0
        if let sesion = sesionActual {
            let progreso = sesion.progress.exercises
            let ejercicios = sesion.plan.exercises
            if let actual = progreso.first(where: { $0.setsDone < $0.setsTotal }) {
                let indice = actual.index
                ejercicio = ejercicios.indices.contains(indice)
                    ? ejercicios[indice].name : "Ejercicio \(indice + 1)"
                hechas = actual.setsDone
                totales = actual.setsTotal
                if ejercicios.indices.contains(indice) {
                    repsSug = repeticionesPorDefecto(sesion: sesion, indice: indice, ejercicio: ejercicios[indice])
                    pesoSug = pesoPorDefecto(sesion: sesion, indice: indice) ?? 0
                }
            } else if let primero = ejercicios.first {
                ejercicio = primero.name
                repsSug = repeticionesPorDefecto(sesion: sesion, indice: 0, ejercicio: primero)
                pesoSug = pesoPorDefecto(sesion: sesion, indice: 0) ?? 0
            }
        }

        var payload: [String: Any] = [
            "sesionActiva": activa,
            "pausada": pausada,
            "tienePlan": tienePlan,
            "racha": racha,
            "misionesActivas": misionesVivas.count,
            "enLlamada": llamada != nil,
            "rutinasActivas": rutinasOn,
            "seriesHechas": hechas,
            "seriesTotales": totales,
            "ejercicioActual": ejercicio,
            "repsSugeridas": repsSug,
            "pesoSugerido": pesoSug,
        ]
        payload["tituloPlan"] = (tienePlan ? titulo : nil) ?? ""
        payload["nombreLlamada"] = llamada?.agent?.name ?? llamada?.goal ?? ""
        payload["idLlamada"] = llamada?.id ?? ""
        if let llamada, let detalle = try? await client.obtenerLlamada(id: llamada.id) {
            let turno = (detalle.events ?? []).last(where: { $0.eventType == "transcript" && !($0.text ?? "").isEmpty })
            payload["turnoLlamada"] = String((turno?.text ?? "").prefix(240))
            payload["turnoRol"] = turno?.role ?? ""
        } else {
            payload["turnoLlamada"] = ""
            payload["turnoRol"] = ""
        }
        if let cronometro { payload["cronometro"] = cronometro }
        if let bpm = health.frecuenciaCardiaca { payload["frecuenciaCardiaca"] = bpm }
        if let kcal = health.caloriasActivas { payload["calorias"] = kcal }

        payload["recordatorios"] = avisos.map { aviso in
            [
                "id": aviso.id,
                "mensaje": aviso.message,
                "vence": aviso.dueAt.timeIntervalSince1970,
                "pendiente": NSNumber(value: !aviso.completado),
            ]
        }
        payload["aprobaciones"] = pendientes.map { item in
            [
                "id": item.id,
                "nombre": item.name ?? "Herramienta",
                "detalle": String(item.argsPreview.prefix(80)),
            ]
        }
        payload["misiones"] = misionesVivas.prefix(6).map { m in
            ["id": m.id, "objetivo": m.objetivo, "status": m.status]
        }
        payload["rutinas"] = (rutinas ?? []).prefix(8).map { r in
            ["id": r.id, "nombre": r.nombre, "enabled": NSNumber(value: r.enabled)]
        }
        payload["equipo"] = (equipo ?? []).prefix(8).map { w in
            [
                "id": w.id,
                "nombre": w.displayName ?? w.name,
                "status": w.status,
            ]
        }

        PhoneWatchBridge.compartido.enviarTablero(payload)
        avisarAprobacionesNuevas(Array(pendientes))
    }

    private func manejar(_ comando: WatchComando) async {
        switch comando.nombre {
        case "alternar":
            await alternarGym()
        case "terminarGym":
            await terminarGym()
        case "completarRecordatorio":
            if let id = comando.id { _ = try? await client?.completeReminder(id: id) }
        case "crearRecordatorio":
            await crearAviso(mensaje: comando.mensaje, minutos: comando.minutos)
        case "hablar":
            await hablar(comando.mensaje)
        case "aprobacion":
            if let id = comando.id { await decidir(id: id, ok: comando.ok ?? false) }
        case "susurrar":
            if let id = comando.id, let texto = comando.mensaje {
                _ = try? await client?.susurrarLlamada(id: id, texto: texto)
            }
        case "registrarSerie":
            await registrarSerie(reps: comando.entero, peso: comando.peso)
        case "agua":
            await sumarAgua(ml: comando.entero ?? 250)
        case "saltarDescanso":
            terminarDescanso()
        case "metricasGym":
            health.aplicarMetricasExternas(bpm: comando.bpm, kcal: comando.kcal)
            return
        case "checkinSi":
            await iniciarEntrenamientoDesdeCheckin()
            return
        case "checkinNo":
            asegurarCliente()
            _ = try? await client?.gymCheckin(respuesta: "no")
            await sincronizar()
            return
        case "confirmarMision":
            if let id = comando.id { _ = try? await client?.confirmMission(id: id, approve: comando.ok ?? false) }
        case "cancelarMision":
            if let id = comando.id { _ = try? await client?.cancelMission(id: id) }
        case "pausarMision":
            if let id = comando.id { _ = try? await client?.pauseMission(id: id) }
        case "reanudarMision":
            if let id = comando.id { _ = try? await client?.resumeMission(id: id) }
        case "steering":
            if let id = comando.id, let texto = comando.mensaje {
                _ = try? await client?.steerMission(id: id, instruction: texto)
            }
        case "crearMision":
            if let texto = comando.mensaje, !texto.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                _ = try? await client?.createMission(objetivo: texto)
            }
        case "toggleRutina":
            if let id = comando.id { _ = try? await client?.toggleAutomation(id: id, enabled: comando.ok ?? false) }
        case "deshacer":
            _ = try? await client?.undoLastAction()
        default:
            break
        }
        await sincronizar()
    }

    private func alternarGym() async {
        guard let client else { return }
        do {
            if let sesion = try await client.gymSesionActual() {
                switch sesion.status {
                case "planned":
                    _ = try await client.gymIniciarSesion(sessionId: sesion.id)
                    iniciarHealthSiToca()
                case "paused":
                    _ = try await client.gymReanudarSesion(sessionId: sesion.id)
                    if !PhoneWatchBridge.compartido.relojAlcanzable {
                        health.reanudarSeguimiento()
                    }
                default:
                    if !PhoneWatchBridge.compartido.relojAlcanzable {
                        health.pausarSeguimiento()
                    }
                    _ = try await client.gymPausarSesion(sessionId: sesion.id)
                }
            } else {
                await iniciarEntrenamientoDesdeCheckin()
            }
        } catch {}
    }

    /// Check-in «si» desde push iPhone, Siri o comando del reloj: persiste plan,
    /// activa sesión en API y empuja tablero con `sesionActiva=true` al Watch.
    func accionCheckinConEntrenamiento(respuesta: String) async {
        guard respuesta == "si" else {
            await accionCheckin(respuesta)
            await sincronizar()
            return
        }
        asegurarCliente()
        await iniciarEntrenamientoDesdeCheckin()
    }

    private func iniciarEntrenamientoDesdeCheckin() async {
        guard let client else { return }
        do {
            let readiness = await health.readinessResumen()
            _ = try await client.gymCheckin(respuesta: "si", readiness: readiness)
            if let sesion = try await client.gymSesionActual() {
                switch sesion.status {
                case "planned":
                    _ = try await client.gymIniciarSesion(sessionId: sesion.id)
                case "paused":
                    _ = try await client.gymReanudarSesion(sessionId: sesion.id)
                default:
                    break
                }
            }
            iniciarHealthSiToca()
        } catch {}
        await sincronizar()
    }

    private func iniciarHealthSiToca() {
        if PhoneWatchBridge.compartido.relojAlcanzable {
            health.abandonarSeguimientoSinGuardar()
        } else {
            health.iniciarSeguimiento()
        }
    }

    private func terminarGym() async {
        descansoTask?.cancel()
        terminarDescanso()
        guard let client, let sesion = try? await client.gymSesionActual() else { return }
        _ = try? await client.gymTerminarSesion(sessionId: sesion.id)
        await health.detenerSeguimiento(fin: Date())
    }

    /// Siri y los botones de notificación pueden correr antes de que la UI
    /// inyecte el `APIClient`. Leemos URL y tokens del Keychain igual que
    /// el arranque normal.
    private func asegurarCliente() {
        if client != nil { return }
        let pairing = PairingStore()
        guard pairing.isPaired, let url = pairing.serverURL else { return }
        client = APIClient(baseURL: url)
    }

    func accionCheckin(_ respuesta: String) async {
        asegurarCliente()
        _ = try? await client?.gymCheckin(respuesta: respuesta)
    }

    func accionCompletarAviso(_ id: String) async {
        asegurarCliente()
        _ = try? await client?.completeReminder(id: id)
        await sincronizar()
    }

    func accionDecidirAprobacion(id: String, ok: Bool) async -> Bool {
        asegurarCliente()
        guard let client else { return false }
        do {
            if ok {
                try await client.approveApproval(id: id)
            } else {
                try await client.denyApproval(id: id)
            }
        } catch {
            return false
        }
        await sincronizar()
        return true
    }

    func pendienteParaAprobar() async -> AprobacionPendienteVista? {
        asegurarCliente()
        guard let client else { return nil }
        let approvals = (try? await client.listApprovals()) ?? []
        guard let primera = approvals.first(where: { $0.status == nil || $0.status == "pending" }) else {
            return nil
        }
        return AprobacionPendienteVista(
            id: primera.id,
            nombre: primera.name ?? "Herramienta",
            detalle: String(primera.argsPreview.prefix(120))
        )
    }

    func accionSerie() async -> Bool {
        asegurarCliente()
        let ok = await registrarSerie(reps: nil, peso: nil)
        await sincronizar()
        return ok
    }

    func accionAprobarPendiente(id: String) async -> Bool {
        asegurarCliente()
        guard let client else { return false }
        do {
            try await client.approveApproval(id: id)
        } catch {
            return false
        }
        await sincronizar()
        return true
    }

    @discardableResult
    func accionAgua(_ ml: Int) async -> Bool {
        if !health.autorizado {
            await health.solicitarAutorizacion()
        }
        let tragos = max(1, ml)
        let enSalud = await health.guardarAgua(ml: tragos)
        PhoneWatchBridge.compartido.enviarTablero([
            "sumarAgua": tragos,
            "aguaEnSalud": NSNumber(value: enSalud),
        ])
        return enSalud
    }

    private func sumarAgua(ml: Int) async {
        let enSalud = await health.guardarAgua(ml: ml)
        PhoneWatchBridge.compartido.enviarTablero([
            "sumarAgua": ml,
            "aguaEnSalud": NSNumber(value: enSalud),
        ])
    }

    @discardableResult
    private func registrarSerie(reps: Int?, peso: Double?) async -> Bool {
        guard let client, let sesion = try? await client.gymSesionActual() else { return false }
        guard sesion.status == "active" else { return false }
        let progreso = sesion.progress.exercises
        let indice = progreso.first(where: { $0.setsDone < $0.setsTotal })?.index
            ?? comandoIndiceActual(sesion)
        guard sesion.plan.exercises.indices.contains(indice) else { return false }
        let ejercicio = sesion.plan.exercises[indice]
        let reps = reps ?? repeticionesPorDefecto(sesion: sesion, indice: indice, ejercicio: ejercicio)
        let kilos = peso ?? pesoPorDefecto(sesion: sesion, indice: indice)
        do {
            _ = try await client.gymRegistrarSerie(
                sessionId: sesion.id,
                ejercicioIdx: indice,
                repeticiones: reps,
                pesoKg: kilos
            )
        } catch {
            return false
        }
        arrancarDescanso(segundos: ejercicio.restSeconds, nombre: ejercicio.name)
        return true
    }

    private func comandoIndiceActual(_ sesion: GymSession) -> Int {
        sesion.progress.exercises.first?.index ?? 0
    }

    private func repeticionesPorDefecto(sesion: GymSession, indice: Int, ejercicio: GymEjercicio) -> Int {
        if let meta = sesion.meta?.first(where: { $0.idx == indice })?.repeticionesObjetivo, meta > 0 {
            return meta
        }
        if let ultima = sesion.series.last(where: { $0.exerciseIndex == indice }), ultima.repetitions > 0 {
            return ultima.repetitions
        }
        let digitos = ejercicio.repetitions.components(separatedBy: CharacterSet.decimalDigits.inverted)
            .filter { !$0.isEmpty }
            .first
        return Int(digitos ?? "") ?? 0
    }

    private func pesoPorDefecto(sesion: GymSession, indice: Int) -> Double? {
        if let meta = sesion.meta?.first(where: { $0.idx == indice })?.pesoObjetivo, meta > 0 {
            return meta
        }
        if let ultima = sesion.series.last(where: { $0.exerciseIndex == indice })?.weightKg, ultima > 0 {
            return ultima
        }
        if let previo = sesion.previo?.first(where: { $0.idx == indice })?.weightKg, previo > 0 {
            return previo
        }
        return nil
    }

    private func arrancarDescanso(segundos: Int, nombre: String) {
        descansoTask?.cancel()
        guard segundos > 0 else {
            terminarDescanso()
            return
        }
        descansoTask = Task { [weak self] in
            for restantes in stride(from: segundos, through: 0, by: -1) {
                guard !Task.isCancelled else { return }
                PhoneWatchBridge.compartido.enviarDescanso(restante: restantes, ejercicio: nombre)
                if restantes == 0 { break }
                try? await Task.sleep(for: .seconds(1))
            }
            guard !Task.isCancelled else { return }
            self?.terminarDescanso()
            LocalNotificationScheduler.gymSerieLista(ejercicio: nombre)
        }
    }

    private func terminarDescanso() {
        descansoTask?.cancel()
        descansoTask = nil
        PhoneWatchBridge.compartido.enviarDescanso(restante: 0, ejercicio: nil)
    }

    private func crearAviso(mensaje: String?, minutos: Int?) async {
        guard let client else { return }
        let texto = (mensaje ?? "Aviso de Edecán").trimmingCharacters(in: .whitespacesAndNewlines)
        guard !texto.isEmpty else { return }
        let espera = max(1, minutos ?? 20)
        _ = try? await client.createReminder(
            texto: texto,
            fecha: Date().addingTimeInterval(TimeInterval(espera * 60)),
            canal: "mobile"
        )
    }

    private func hablar(_ texto: String?) async {
        guard let client else { return }
        let limpio = (texto ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        guard !limpio.isEmpty else { return }
        let vm: ChatViewModel
        if let chat {
            vm = chat
        } else {
            vm = ChatViewModel()
            chat = vm
        }
        if vm.conversacionId == nil, let principal = try? await client.conversacionPrincipal() {
            await vm.abrirConversacion(id: principal.id, client: client)
        }
        _ = await vm.enviar(texto: limpio, client: client)
        let respuesta = vm.mensajes.last(where: { $0.rol == .asistente })?.texto ?? ""
        if !respuesta.isEmpty {
            PhoneWatchBridge.compartido.enviarTablero([
                "respuesta": String(respuesta.prefix(280)),
            ])
        }
    }

    private func decidir(id: String, ok: Bool) async {
        _ = await accionDecidirAprobacion(id: id, ok: ok)
    }

    private func avisarAprobacionesNuevas(_ pendientes: [PendingApproval]) {
        let ids = Set(pendientes.map(\.id))
        defer { idsAprobacionesConocidas = ids }
        guard let previas = idsAprobacionesConocidas else { return }
        for item in pendientes where !previas.contains(item.id) {
            LocalNotificationScheduler.approval(
                id: item.id,
                nombre: item.name ?? "Herramienta",
                detalle: String(item.argsPreview.prefix(80))
            )
        }
    }
}
