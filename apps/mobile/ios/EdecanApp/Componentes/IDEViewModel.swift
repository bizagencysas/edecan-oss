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
    private(set) var cargandoArchivo = false

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
            let tree = try await client.ideTree()
            arbol = tree.entries
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
            archivoAbierto = try await client.ideFile(path: ruta)
        } catch {
            errorMensaje = error.localizedDescription
        }
    }

    func cerrarArchivo() {
        rutaAbierta = nil
        archivoAbierto = nil
    }
}
