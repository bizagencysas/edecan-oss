import Testing
import Foundation
@testable import EdecanKit

/// `IDEEntry`/`IDETree`/`IDEFileOut` decodifican EXACTAMENTE lo que devuelven
/// `edecan_companion.actions._list_tree`/`_read_file` (verificado contra el
/// código fuente del companion, no una suposición) tal como las reenvía
/// `apps/api/edecan_api/routers/ide.py`.
struct IDEModelsTests {
    @Test func decodificaEstadoConectado() throws {
        let status = try JSONDecoder().decode(IDEStatusOut.self, from: Data(#"{"connected": true}"#.utf8))
        #expect(status.connected)
    }

    @Test func decodificaArbolAnidado() throws {
        let json = """
        {
          "path": ".",
          "truncated": false,
          "entries": [
            {"name": "README.md", "is_dir": false, "size_bytes": 120},
            {"name": "src", "is_dir": true, "children": [
              {"name": "main.py", "is_dir": false, "size_bytes": 450}
            ]},
            {"name": "vacia", "is_dir": true, "children": null}
          ]
        }
        """
        let tree = try JSONDecoder().decode(IDETree.self, from: Data(json.utf8))
        #expect(tree.entries.count == 3)
        #expect(tree.entries[0].isDir == false)
        #expect(tree.entries[0].sizeBytes == 120)
        #expect(tree.entries[1].children?.count == 1)
        #expect(tree.entries[1].children?.first?.name == "main.py")
        #expect(tree.entries[2].isDir == true)
        #expect(tree.entries[2].children == nil)
        #expect(tree.truncated == false)
    }

    @Test func decodificaArchivoUTF8() throws {
        let json = #"{"path": "src/main.py", "content": "print('hola')", "encoding": "utf-8", "size_bytes": 14}"#
        let file = try JSONDecoder().decode(IDEFileOut.self, from: Data(json.utf8))
        #expect(file.content == "print('hola')")
        #expect(file.encoding == "utf-8")
    }
}

/// `DeviceOut` solo exige `id` — el resto queda opcional a propósito porque
/// `POST /v1/devices` es un contrato en paralelo (WP-V4-01) que puede no
/// existir todavía del lado del servidor (ver su docstring).
struct DeviceModelsTests {
    @Test func decodificaDispositivoCompleto() throws {
        let json = """
        {"id": "d001", "nombre": "iPhone de Ana", "plataforma": "ios", "kind": "mobile",
         "status": "active", "fingerprint": "ABCD-1234"}
        """
        let device = try JSONDecoder().decode(DeviceOut.self, from: Data(json.utf8))
        #expect(device.id == "d001")
        #expect(device.plataforma == "ios")
        #expect(device.kind == "mobile")
    }

    @Test func decodificaDispositivoSoloConId() throws {
        let device = try JSONDecoder().decode(DeviceOut.self, from: Data(#"{"id": "d002"}"#.utf8))
        #expect(device.id == "d002")
        #expect(device.nombre == nil)
        #expect(device.status == nil)
    }
}

struct VoiceModelsTests {
    @Test func decodificaTranscripcion() throws {
        let json = #"{"text": "Recuérdame llamar al contador mañana a las tres"}"#
        let out = try JSONDecoder().decode(TranscribeOut.self, from: Data(json.utf8))
        #expect(out.text == "Recuérdame llamar al contador mañana a las tres")
    }
}
