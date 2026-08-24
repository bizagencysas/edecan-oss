import XCTest
@testable import EdecanKit

/// TEMPORAL — diagnóstico del bug "pantalla en blanco con hilo largo".
/// Mide el decodificador real contra payloads reales del servidor y contra
/// filas deformes. Borrar tras el diagnóstico.
final class ZZDiagnosticoHiloLargoTests: XCTestCase {
    private let scratch = "/private/tmp/claude-501/-Users-example-org-referencia/e07fac40-251e-4018-a258-a643a9fd3338/scratchpad"

    private func decoder() -> JSONDecoder { APIClient.crearDecoder() }

    func testPayloadRealPesado() throws {
        guard FileManager.default.fileExists(atPath: "\(scratch)/pesado.json") else {
            throw XCTSkip("Archivo pesado.json temporal no existe en este entorno")
        }
        let data = try Data(contentsOf: URL(fileURLWithPath: "\(scratch)/pesado.json"))
        let t0 = Date()
        let detail = try decoder().decode(ConversationDetail.self, from: data)
        let dt = Date().timeIntervalSince(t0)
        print("DIAG pesado: bytes=\(data.count) mensajes=\(detail.messages.count) segundos=\(String(format: "%.3f", dt))")
        XCTAssertEqual(detail.messages.count, 50)
    }

    func testPayloadRealLargo() throws {
        guard FileManager.default.fileExists(atPath: "\(scratch)/largo.json") else {
            throw XCTSkip("Archivo largo.json temporal no existe en este entorno")
        }
        let data = try Data(contentsOf: URL(fileURLWithPath: "\(scratch)/largo.json"))
        let detail = try decoder().decode(ConversationDetail.self, from: data)
        print("DIAG largo: mensajes=\(detail.messages.count)")
    }

    // MARK: - Filas deformes: ¿se saltan o revientan el hilo entero?

    private func sobre(_ mensajes: String) -> Data {
        """
        {"id":"11111111-1111-1111-1111-111111111111","title":"t","channel":"web",
         "model":null,"effort":null,
         "created_at":"2026-08-02T12:00:00Z","updated_at":"2026-08-02T12:00:00Z",
         "pending_confirmation":null,
         "messages":[\(mensajes)]}
        """.data(using: .utf8)!
    }

    private func msg(id: String = "aaaa", role: String = "user", createdAt: String = "\"2026-08-02T12:00:00.123456Z\"", content: String = "\"hola\"") -> String {
        """
        {"id":"\(id)","role":"\(role)","content":\(content),"tool_calls":null,
         "tokens_in":0,"tokens_out":0,"created_at":\(createdAt)}
        """
    }

    func testUnaFilaDeformeMataElHiloEntero() {
        let casos: [(String, String)] = [
            ("created_at sin fraccion", msg(createdAt: "\"2026-08-02T12:00:00Z\"")),
            ("created_at 9 decimales", msg(createdAt: "\"2026-08-02T12:00:00.123456789Z\"")),
            ("created_at offset +00:00", msg(createdAt: "\"2026-08-02T12:00:00.123456+00:00\"")),
            ("created_at offset -05:00", msg(createdAt: "\"2026-08-02T12:00:00.123456-05:00\"")),
            ("created_at sin zona", msg(createdAt: "\"2026-08-02T12:00:00.123456\"")),
            ("created_at null", msg(createdAt: "null")),
            ("created_at ausente", "{\"id\":\"x\",\"role\":\"user\",\"content\":\"hola\",\"tool_calls\":null}"),
            ("role desconocido", msg(role: "tool")),
            ("role null", "{\"id\":\"x\",\"role\":null,\"content\":\"hola\",\"created_at\":\"2026-08-02T12:00:00Z\"}"),
            ("content objeto", msg(content: "{\"text\":\"hola\",\"attachments\":[]}")),
            ("content lista de bloques", msg(content: "[{\"type\":\"text\",\"text\":\"hola\"}]")),
            ("content null", msg(content: "null")),
        ]
        for (nombre, fila) in casos {
            // Fila sola
            let sola: String
            do {
                let d = try decoder().decode(ConversationDetail.self, from: sobre(fila))
                sola = "OK mensajes=\(d.messages.count) texto=\"\(d.messages.first?.text ?? "-")\" rol=\(d.messages.first?.role ?? "-")"
            } catch {
                sola = "LANZA \(error)"
            }
            // La misma fila metida entre 3 buenas: ¿se salta o mata el hilo?
            let mezcla = [msg(id: "b1"), fila, msg(id: "b2"), msg(id: "b3")].joined(separator: ",")
            let entre: String
            do {
                let d = try decoder().decode(ConversationDetail.self, from: sobre(mezcla))
                entre = "OK mensajes=\(d.messages.count)/4"
            } catch {
                entre = "LANZA (pierde las 4)"
            }
            print("DIAG fila [\(nombre)] -> sola: \(sola) | entre buenas: \(entre)")
        }
    }

    func testToolCallsDesconocidoBorraTodoElTrabajo() {
        let bueno = """
        {"type":"tool_start","tool_call_id":"c1","name":"buscar_web","args":{}}
        """
        let raro = """
        {"type":"un_evento_del_futuro","tool_call_id":"c2"}
        """
        for (nombre, eventos) in [("solo conocidos", "[\(bueno)]"), ("uno desconocido al final", "[\(bueno),\(raro)]")] {
            let fila = """
            {"id":"z","role":"assistant","content":"texto","tool_calls":\(eventos),
             "tokens_in":0,"tokens_out":0,"created_at":"2026-08-02T12:00:00Z"}
            """
            do {
                let d = try decoder().decode(ConversationDetail.self, from: sobre(fila))
                print("DIAG tool_calls [\(nombre)] -> mensajes=\(d.messages.count) eventos=\(d.messages.first?.toolCalls.count ?? -1)")
            } catch {
                print("DIAG tool_calls [\(nombre)] -> LANZA \(error)")
            }
        }
    }
}
