import Foundation
import Observation
import EdecanKit

@MainActor
@Observable
final class IDEViewModel {
    private(set) var conectado = false
    private(set) var cargando = false
    private(set) var reconectando = false
    var errorMensaje: String?

    private(set) var workspaces: [IDEWorkspace] = []
    private(set) var workspaceActivo: IDEWorkspace?
    private(set) var cambiandoWorkspace = false

    private(set) var arbol: [IDEEntry] = []
    private(set) var truncado = false
    private(set) var rutaAbierta: String?
    private(set) var archivoAbierto: IDEFileOut?
    var contenidoEditable = ""
    private(set) var cargandoArchivo = false
    private(set) var guardandoArchivo = false
    var rutaActual = ""

    private(set) var terminales: [IDESession] = []
    private(set) var terminalActivo: IDESession?
    private(set) var eventosTerminal: [IDESessionEvent] = []
    private(set) var creandoTerminal = false
    var entradaTerminal = ""

    private(set) var agentes: [IDESession] = []
    private(set) var agenteActivo: IDESession?
    private(set) var eventosAgente: [IDESessionEvent] = []
    private(set) var creandoAgente = false
    var promptAgente = ""
    var proveedorAgente: IDEAgentProvider = .auto
    var modeloAgente = ""

    private(set) var gitStatus: IDEGitStatus?
    private(set) var gitDiff: IDEGitDiff?
    private(set) var gitLog: [IDEGitCommit] = []
    private(set) var cargandoGit = false
    private(set) var accionGitEnCurso = false
    var mensajeCommit = ""
    var nuevaRama = ""

    private let estadoLocal = IDELocalStateStore()
    private let intervaloPolling: Duration = .seconds(1)
    private var cursorTerminal = 0
    private var cursorAgente = 0
    private var tareaPolling: Task<Void, Never>?

    var salidaTerminal: String {
        eventosTerminal.map { evento in
            if evento.type == "output" { return evento.text }
            return "\n\(evento.text)\n"
        }.joined()
    }

    var workspaceId: String? { workspaceActivo?.id }

    func descartarError() {
        errorMensaje = nil
    }

    func cargar(client: APIClient?) async {
        guard let client else {
            errorMensaje = "No hay sesión activa."
            return
        }
        cargando = true
        errorMensaje = nil
        defer { cargando = false }
        do {
            let status = try await client.ideStatus()
            conectado = status.connected
            guard conectado else {
                limpiarContenidoRemoto()
                return
            }

            let disponibles = try await client.ideWorkspaces()
            workspaces = disponibles
            guard !disponibles.isEmpty else {
                workspaceActivo = nil
                arbol = []
                return
            }

            let preferido = estadoLocal.selectedWorkspaceId
            let elegido = disponibles.first(where: { $0.id == preferido })
                ?? disponibles.first(where: \.active)
                ?? disponibles.first!
            await activar(workspace: elegido, client: client, forceRemoteActivation: !elegido.active)
        } catch is CancellationError {
            return
        } catch {
            errorMensaje = error.localizedDescription
        }
    }

    func crearWorkspace(path: String, name: String?, client: APIClient?) async -> Bool {
        guard let client else { return false }
        let path = path.trimmingCharacters(in: .whitespacesAndNewlines)
        let name = name?.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !path.isEmpty, !cambiandoWorkspace else { return false }
        cambiandoWorkspace = true
        errorMensaje = nil
        defer { cambiandoWorkspace = false }
        do {
            let creado = try await client.ideCreateWorkspace(
                path: path,
                name: name?.isEmpty == true ? nil : name
            )
            workspaces.removeAll { $0.id == creado.id }
            workspaces.append(creado)
            await activar(workspace: creado, client: client, forceRemoteActivation: true)
            return true
        } catch {
            errorMensaje = error.localizedDescription
            return false
        }
    }

    func seleccionarWorkspace(id: String, client: APIClient?) async {
        guard let client,
              let workspace = workspaces.first(where: { $0.id == id }),
              workspace.id != workspaceActivo?.id,
              !cambiandoWorkspace
        else { return }
        cambiandoWorkspace = true
        errorMensaje = nil
        defer { cambiandoWorkspace = false }
        await activar(workspace: workspace, client: client, forceRemoteActivation: true)
    }

    private func activar(
        workspace: IDEWorkspace,
        client: APIClient,
        forceRemoteActivation: Bool
    ) async {
        do {
            let activo = forceRemoteActivation
                ? try await client.ideActivateWorkspace(id: workspace.id)
                : workspace
            workspaceActivo = activo
            estadoLocal.selectedWorkspaceId = activo.id
            workspaces = workspaces.map {
                IDEWorkspace(
                    id: $0.id,
                    name: $0.name,
                    path: $0.path,
                    active: $0.id == activo.id,
                    createdAt: $0.createdAt
                )
            }
            resetearWorkspace()
            async let cargarArbol: Void = refrescarArbol(client: client)
            async let cargarSesiones: Void = refrescarSesiones(client: client)
            async let cargarGit: Void = refrescarGit(client: client)
            _ = await (cargarArbol, cargarSesiones, cargarGit)
            iniciarPolling(client: client)
        } catch is CancellationError {
            return
        } catch {
            errorMensaje = error.localizedDescription
        }
    }

    func refrescarArbol(client: APIClient?) async {
        guard let client, let workspaceId else { return }
        do {
            let tree = try await client.ideTree(
                workspaceId: workspaceId,
                path: rutaActual.isEmpty ? nil : rutaActual
            )
            arbol = tree.entries
            rutaActual = tree.path == "." ? "" : tree.path
            truncado = tree.truncated
        } catch is CancellationError {
            return
        } catch {
            errorMensaje = error.localizedDescription
        }
    }

    func abrirRuta(client: APIClient?) async {
        await refrescarArbol(client: client)
    }

    func abrir(ruta: String, client: APIClient?) async {
        guard let client, let workspaceId else { return }
        rutaAbierta = ruta
        archivoAbierto = nil
        cargandoArchivo = true
        errorMensaje = nil
        defer { cargandoArchivo = false }
        do {
            let file = try await client.ideFile(workspaceId: workspaceId, path: ruta)
            archivoAbierto = file
            contenidoEditable = file.content
        } catch is CancellationError {
            return
        } catch {
            errorMensaje = error.localizedDescription
        }
    }

    func cerrarArchivo() {
        rutaAbierta = nil
        archivoAbierto = nil
        contenidoEditable = ""
    }

    func guardar(client: APIClient?) async {
        guard let client,
              let workspaceId,
              let rutaAbierta,
              archivoAbierto?.encoding == "utf-8",
              contenidoEditable != archivoAbierto?.content
        else { return }
        guardandoArchivo = true
        errorMensaje = nil
        defer { guardandoArchivo = false }
        do {
            try await client.ideWrite(
                workspaceId: workspaceId,
                path: rutaAbierta,
                content: contenidoEditable
            )
            archivoAbierto = try await client.ideFile(
                workspaceId: workspaceId,
                path: rutaAbierta
            )
            contenidoEditable = archivoAbierto?.content ?? contenidoEditable
            await refrescarGit(client: client)
        } catch {
            errorMensaje = error.localizedDescription
        }
    }

    // MARK: Terminal

    func crearTerminal(client: APIClient?) async {
        guard let client, let workspaceId, !creandoTerminal else { return }
        creandoTerminal = true
        errorMensaje = nil
        defer { creandoTerminal = false }
        do {
            let terminal = try await client.ideCreateTerminal(
                workspaceId: workspaceId,
                title: workspaceActivo?.name
            )
            terminales.removeAll { $0.id == terminal.id }
            terminales.insert(terminal, at: 0)
            seleccionarTerminal(terminal)
        } catch {
            errorMensaje = error.localizedDescription
        }
    }

    func seleccionarTerminal(id: String, client: APIClient?) async {
        guard let terminal = terminales.first(where: { $0.id == id }) else { return }
        seleccionarTerminal(terminal)
        await leerTerminal(client: client, mostrarError: true)
    }

    func enviarTerminal(client: APIClient?) async {
        let data = entradaTerminal.trimmingCharacters(in: .newlines)
        guard !data.isEmpty, let client else { return }
        if terminalActivo == nil || terminalActivo?.isActive == false {
            await crearTerminal(client: client)
        }
        guard let id = terminalActivo?.id else { return }
        entradaTerminal = ""
        do {
            try await client.ideTerminalInput(id: id, data: data + "\n")
            await leerTerminal(client: client, mostrarError: true)
        } catch {
            entradaTerminal = data
            errorMensaje = error.localizedDescription
        }
    }

    func interrumpirTerminal(client: APIClient?) async {
        guard let client, let id = terminalActivo?.id else { return }
        do {
            try await client.ideTerminalInput(id: id, data: "\u{3}")
        } catch {
            errorMensaje = error.localizedDescription
        }
    }

    func cerrarTerminal(client: APIClient?) async {
        guard let client, let terminal = terminalActivo else { return }
        do {
            try await client.ideDeleteTerminal(id: terminal.id)
            estadoLocal.setSelectedSessionId(nil, kind: "terminal", workspaceId: terminal.workspaceId)
            terminalActivo = nil
            eventosTerminal = []
            cursorTerminal = 0
            await refrescarSesiones(client: client)
        } catch {
            errorMensaje = error.localizedDescription
        }
    }

    // MARK: Agente

    func crearAgente(client: APIClient?) async {
        guard let client, let workspaceId, !creandoAgente else { return }
        let prompt = promptAgente.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !prompt.isEmpty else { return }
        creandoAgente = true
        errorMensaje = nil
        defer { creandoAgente = false }
        do {
            let agente = try await client.ideCreateAgent(
                workspaceId: workspaceId,
                prompt: prompt,
                provider: proveedorAgente,
                title: String(prompt.prefix(80)),
                model: modeloAgente.trimmingCharacters(in: .whitespacesAndNewlines).vacioANil
            )
            agentes.removeAll { $0.id == agente.id }
            agentes.insert(agente, at: 0)
            promptAgente = ""
            seleccionarAgente(agente)
        } catch {
            errorMensaje = error.localizedDescription
        }
    }

    func seleccionarAgente(id: String, client: APIClient?) async {
        guard let agente = agentes.first(where: { $0.id == id }) else { return }
        seleccionarAgente(agente)
        await leerAgente(client: client, mostrarError: true)
    }

    func cerrarAgente(client: APIClient?) async {
        guard let client, let agente = agenteActivo else { return }
        do {
            try await client.ideDeleteAgent(id: agente.id)
            estadoLocal.setSelectedSessionId(nil, kind: "agent", workspaceId: agente.workspaceId)
            agenteActivo = nil
            eventosAgente = []
            cursorAgente = 0
            await refrescarSesiones(client: client)
        } catch {
            errorMensaje = error.localizedDescription
        }
    }

    // MARK: Git

    func refrescarGit(client: APIClient?) async {
        guard let client, let workspaceId else { return }
        cargandoGit = gitStatus == nil
        defer { cargandoGit = false }
        do {
            async let status = client.ideGitStatus(workspaceId: workspaceId)
            async let diff = client.ideGitDiff(workspaceId: workspaceId)
            async let log = client.ideGitLog(workspaceId: workspaceId)
            gitStatus = try await status
            gitDiff = try await diff
            let historial = try await log
            gitLog = historial.commits
        } catch is CancellationError {
            return
        } catch {
            // Un proyecto sin Git conserva Archivos/Agente/Terminal funcionales.
            gitStatus = nil
            gitDiff = nil
            gitLog = []
        }
    }

    func stage(paths: [String], client: APIClient?) async {
        await ejecutarGit(client: client) { client, workspaceId in
            try await client.ideGitStage(workspaceId: workspaceId, paths: paths)
        }
    }

    func unstage(paths: [String], client: APIClient?) async {
        await ejecutarGit(client: client) { client, workspaceId in
            try await client.ideGitUnstage(workspaceId: workspaceId, paths: paths)
        }
    }

    func commit(client: APIClient?) async {
        let message = mensajeCommit.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !message.isEmpty else { return }
        await ejecutarGit(client: client) { client, workspaceId in
            try await client.ideGitCommit(workspaceId: workspaceId, message: message)
        }
        if errorMensaje == nil { mensajeCommit = "" }
    }

    func crearRama(client: APIClient?) async {
        let name = nuevaRama.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { return }
        await ejecutarGit(client: client) { client, workspaceId in
            try await client.ideGitCreateBranch(workspaceId: workspaceId, name: name, checkout: true)
        }
        if errorMensaje == nil { nuevaRama = "" }
    }

    func push(client: APIClient?) async {
        await ejecutarGit(client: client) { client, workspaceId in
            try await client.ideGitPush(workspaceId: workspaceId)
        }
    }

    // MARK: Ciclo de vida y polling

    func iniciarPolling(client: APIClient?) {
        detenerPolling()
        guard let client else { return }
        tareaPolling = Task { [weak self] in
            guard let self else { return }
            while !Task.isCancelled {
                await self.poll(client: client)
                try? await Task.sleep(for: self.intervaloPolling)
            }
        }
    }

    func detenerPolling() {
        tareaPolling?.cancel()
        tareaPolling = nil
    }

    func reanudar(client: APIClient?) async {
        guard let client else { return }
        reconectando = true
        do {
            let status = try await client.ideStatus()
            conectado = status.connected
            guard conectado else {
                detenerPolling()
                return
            }
            await refrescarSesiones(client: client)
            let terminalOk = await leerTerminal(client: client, mostrarError: false)
            let agenteOk = await leerAgente(client: client, mostrarError: false)
            await refrescarArbol(client: client)
            reconectando = !(terminalOk && agenteOk)
            iniciarPolling(client: client)
        } catch is CancellationError {
            return
        } catch {
            reconectando = true
        }
    }

    private func poll(client: APIClient) async {
        let terminalOk = await leerTerminal(client: client, mostrarError: false)
        let agenteOk = await leerAgente(client: client, mostrarError: false)
        if terminalActivo != nil || agenteActivo != nil {
            reconectando = !(terminalOk && agenteOk)
        }
    }

    private func refrescarSesiones(client: APIClient) async {
        guard let workspaceId else { return }
        do {
            async let terminalesRemotas = client.ideTerminals()
            async let agentesRemotos = client.ideAgents()
            let todasLasTerminales = try await terminalesRemotas
            let todosLosAgentes = try await agentesRemotos
            terminales = todasLasTerminales
                .filter { $0.workspaceId == workspaceId }
                .sorted { $0.startedAt > $1.startedAt }
            agentes = todosLosAgentes
                .filter { $0.workspaceId == workspaceId }
                .sorted { $0.startedAt > $1.startedAt }

            if terminalActivo == nil {
                let preferido = estadoLocal.selectedSessionId(kind: "terminal", workspaceId: workspaceId)
                if let terminal = terminales.first(where: { $0.id == preferido }) ?? terminales.first {
                    seleccionarTerminal(terminal)
                    _ = await leerTerminal(client: client, mostrarError: false)
                }
            }
            if agenteActivo == nil {
                let preferido = estadoLocal.selectedSessionId(kind: "agent", workspaceId: workspaceId)
                if let agente = agentes.first(where: { $0.id == preferido }) ?? agentes.first {
                    seleccionarAgente(agente)
                    _ = await leerAgente(client: client, mostrarError: false)
                }
            }
        } catch is CancellationError {
            return
        } catch {
            reconectando = true
        }
    }

    @discardableResult
    private func leerTerminal(client: APIClient?, mostrarError: Bool) async -> Bool {
        guard let client, let id = terminalActivo?.id else { return true }
        do {
            let out = try await client.ideReadTerminal(id: id, cursor: cursorTerminal)
            terminalActivo = out.session
            anexar(out.events, a: &eventosTerminal)
            cursorTerminal = max(cursorTerminal, out.nextCursor)
            return true
        } catch is CancellationError {
            return false
        } catch {
            if mostrarError { errorMensaje = error.localizedDescription }
            return false
        }
    }

    @discardableResult
    private func leerAgente(client: APIClient?, mostrarError: Bool) async -> Bool {
        guard let client, let id = agenteActivo?.id else { return true }
        do {
            let out = try await client.ideReadAgent(id: id, cursor: cursorAgente)
            agenteActivo = out.session
            anexar(out.events, a: &eventosAgente)
            cursorAgente = max(cursorAgente, out.nextCursor)
            return true
        } catch is CancellationError {
            return false
        } catch {
            if mostrarError { errorMensaje = error.localizedDescription }
            return false
        }
    }

    private func anexar(_ nuevos: [IDESessionEvent], a actuales: inout [IDESessionEvent]) {
        let existentes = Set(actuales.map(\.cursor))
        actuales.append(contentsOf: nuevos.filter { !existentes.contains($0.cursor) })
        actuales.sort { $0.cursor < $1.cursor }
    }

    private func seleccionarTerminal(_ terminal: IDESession) {
        terminalActivo = terminal
        eventosTerminal = []
        cursorTerminal = 0
        estadoLocal.setSelectedSessionId(terminal.id, kind: "terminal", workspaceId: terminal.workspaceId)
    }

    private func seleccionarAgente(_ agente: IDESession) {
        agenteActivo = agente
        eventosAgente = []
        cursorAgente = 0
        estadoLocal.setSelectedSessionId(agente.id, kind: "agent", workspaceId: agente.workspaceId)
    }

    private func ejecutarGit(
        client: APIClient?,
        action: (APIClient, String) async throws -> Void
    ) async {
        guard let client, let workspaceId, !accionGitEnCurso else { return }
        accionGitEnCurso = true
        errorMensaje = nil
        defer { accionGitEnCurso = false }
        do {
            try await action(client, workspaceId)
            await refrescarGit(client: client)
            await refrescarArbol(client: client)
        } catch {
            errorMensaje = error.localizedDescription
        }
    }

    private func resetearWorkspace() {
        detenerPolling()
        rutaActual = ""
        arbol = []
        truncado = false
        cerrarArchivo()
        terminales = []
        terminalActivo = nil
        eventosTerminal = []
        cursorTerminal = 0
        agentes = []
        agenteActivo = nil
        eventosAgente = []
        cursorAgente = 0
        gitStatus = nil
        gitDiff = nil
        gitLog = []
    }

    private func limpiarContenidoRemoto() {
        workspaces = []
        workspaceActivo = nil
        resetearWorkspace()
    }
}

private extension String {
    var vacioANil: String? { isEmpty ? nil : self }
}
