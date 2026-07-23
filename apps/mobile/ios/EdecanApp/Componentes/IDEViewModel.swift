import Foundation
import Observation
import EdecanKit

/// Estado de ``IDEView`` — IDE embebido de solo lectura sobre el companion
/// de escritorio emparejado (`GET /v1/ide/status|tree|file`,
/// `apps/api/edecan_api/routers/ide.py`, `docs/ide.md`).
@MainActor
@Observable
final class IDEViewModel {
    private(set) var conectado = false
    private(set) var arbol: [IDEEntry] = []
    private(set) var truncado = false
    private(set) var cargando = false
    var errorMensaje: String?

    private(set) var rutaAbierta: String?
    private(set) var archivoAbierto: IDEFileOut?
    var contenidoEditable = ""
    private(set) var cargandoArchivo = false
    private(set) var guardandoArchivo = false
    var rutaActual = ""
    var comando = ""
    private(set) var salidaTerminal = ""
    private(set) var ejecutandoComando = false

    /// `GET /v1/ide/status`, y si hay companion conectado, `GET /v1/ide/tree`
    /// de una — la app móvil pide el árbol completo (hasta los topes por
    /// defecto del companion) de una sola vez y lo navega en memoria con
    /// `OutlineGroup`, en vez de pedir carpeta por carpeta.
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
                arbol = []
                return
            }
            let tree = try await client.ideTree(path: rutaActual.isEmpty ? nil : rutaActual)
            arbol = tree.entries
            rutaActual = tree.path == "." ? "" : tree.path
            truncado = tree.truncated
        } catch {
            errorMensaje = error.localizedDescription
        }
    }

    /// `GET /v1/ide/file?path=` — abre un archivo del árbol.
    func abrir(ruta: String, client: APIClient?) async {
        guard let client else {
            errorMensaje = "No hay sesión activa."
            return
        }
        rutaAbierta = ruta
        archivoAbierto = nil
        cargandoArchivo = true
        errorMensaje = nil
        defer { cargandoArchivo = false }
        do {
            let file = try await client.ideFile(path: ruta)
            archivoAbierto = file
            contenidoEditable = file.content
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
        guard let client, let rutaAbierta, archivoAbierto?.encoding == "utf-8" else { return }
        guard contenidoEditable != archivoAbierto?.content else { return }
        guardandoArchivo = true
        errorMensaje = nil
        defer { guardandoArchivo = false }
        do {
            try await client.ideWrite(path: rutaAbierta, content: contenidoEditable)
            archivoAbierto = try await client.ideFile(path: rutaAbierta)
            contenidoEditable = archivoAbierto?.content ?? contenidoEditable
        } catch {
            errorMensaje = error.localizedDescription
        }
    }

    func abrirRuta(client: APIClient?) async {
        await cargar(client: client)
    }

    func ejecutar(client: APIClient?) async {
        let value = comando.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty, let client else { return }
        ejecutandoComando = true
        errorMensaje = nil
        salidaTerminal += "\n$ \(value)\n"
        comando = ""
        defer { ejecutandoComando = false }
        do {
            let result = try await client.ideRun(command: value)
            salidaTerminal += result.stdout
            if !result.stderr.isEmpty { salidaTerminal += result.stderr }
            salidaTerminal += "\n[exit \(result.exitCode)]\n"
        } catch {
            errorMensaje = error.localizedDescription
            salidaTerminal += "\n\(error.localizedDescription)\n"
        }
    }
}
