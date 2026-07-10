import Testing
import Foundation
@testable import EdecanKit

/// Decodifica el mismo *shape* que devuelven de verdad
/// `POST /v1/remote/sessions`, `GET /v1/remote/sessions[/{id}]` y
/// `POST /v1/remote/sessions/{id}/end` — `Repo._REMOTE_SESSION_COLUMNS`,
/// verificado contra `apps/api/edecan_api/repo.py` y
/// `apps/api/tests/test_remote_router.py` (mismo criterio que
/// `MissionsModelsTests`).
struct RemoteSessionModelsTests {
    @Test func decodificaSesionPendienteReciénCreada() throws {
        let json = """
        {
          "id": "s1", "tenant_id": "t1", "user_id": "u1", "device_id": null,
          "kind": "view", "status": "pending", "started_at": null, "ended_at": null,
          "frames_count": 0, "created_at": "2026-07-09T10:00:00Z", "updated_at": "2026-07-09T10:00:00Z"
        }
        """
        let sesion = try APIClient.crearDecoder().decode(RemoteSession.self, from: Data(json.utf8))
        #expect(sesion.id == "s1")
        #expect(sesion.deviceId == nil)
        #expect(sesion.kind == "view")
        #expect(sesion.status == "pending")
        #expect(sesion.startedAt == nil)
        #expect(sesion.framesCount == 0)
        #expect(sesion.esControl == false)
        #expect(sesion.sigueViva == true)
        #expect(sesion.esTerminal == false)
    }

    @Test func decodificaSesionDeControlActiva() throws {
        let json = """
        {
          "id": "s2", "tenant_id": "t1", "user_id": "u1", "device_id": null,
          "kind": "control", "status": "active", "started_at": "2026-07-09T10:00:05Z", "ended_at": null,
          "frames_count": 4, "created_at": "2026-07-09T10:00:00Z", "updated_at": "2026-07-09T10:00:05Z"
        }
        """
        let sesion = try APIClient.crearDecoder().decode(RemoteSession.self, from: Data(json.utf8))
        #expect(sesion.esControl == true)
        #expect(sesion.status == "active")
        #expect(sesion.startedAt != nil)
        #expect(sesion.framesCount == 4)
        #expect(sesion.sigueViva == true)
        #expect(sesion.esTerminal == false)
    }

    @Test func decodificaSesionDenegadaYTerminada() throws {
        let denegadaJSON = """
        {"id": "s3", "tenant_id": "t1", "user_id": "u1", "device_id": null, "kind": "view",
         "status": "denied", "started_at": null, "ended_at": null, "frames_count": 0,
         "created_at": "2026-07-09T10:00:00Z", "updated_at": "2026-07-09T10:00:01Z"}
        """
        let denegada = try APIClient.crearDecoder().decode(RemoteSession.self, from: Data(denegadaJSON.utf8))
        #expect(denegada.esTerminal == true)
        #expect(denegada.sigueViva == false)

        let terminadaJSON = """
        {"id": "s4", "tenant_id": "t1", "user_id": "u1", "device_id": null, "kind": "view",
         "status": "ended", "started_at": "2026-07-09T10:00:00Z", "ended_at": "2026-07-09T10:05:00Z",
         "frames_count": 12, "created_at": "2026-07-09T10:00:00Z", "updated_at": "2026-07-09T10:05:00Z"}
        """
        let terminada = try APIClient.crearDecoder().decode(RemoteSession.self, from: Data(terminadaJSON.utf8))
        #expect(terminada.esTerminal == true)
        #expect(terminada.endedAt != nil)
    }

    @Test func decodificaListaDeSesiones() throws {
        let json = """
        [{"id": "s1", "tenant_id": "t1", "user_id": "u1", "device_id": null, "kind": "view",
          "status": "ended", "started_at": null, "ended_at": null, "frames_count": 0,
          "created_at": "2026-07-09T10:00:00Z", "updated_at": "2026-07-09T10:00:00Z"}]
        """
        let sesiones = try APIClient.crearDecoder().decode([RemoteSession].self, from: Data(json.utf8))
        #expect(sesiones.count == 1)
        #expect(sesiones[0].id == "s1")
    }

    // MARK: - conEstado / conFrame

    private func sesionDePrueba(status: String = "pending", framesCount: Int = 0, startedAt: Date? = nil) -> RemoteSession {
        RemoteSession(
            id: "s1", tenantId: "t1", userId: "u1", deviceId: nil, kind: "control", status: status,
            startedAt: startedAt, endedAt: nil, framesCount: framesCount,
            createdAt: Date(timeIntervalSince1970: 0), updatedAt: Date(timeIntervalSince1970: 0)
        )
    }

    @Test func conEstadoReemplazaSoloElStatus() {
        let original = sesionDePrueba(status: "active", framesCount: 3)
        let denegada = original.conEstado("denied")
        #expect(denegada.status == "denied")
        #expect(denegada.id == original.id)
        #expect(denegada.framesCount == original.framesCount)
        #expect(denegada.kind == original.kind)
    }

    @Test func conFramePasaAActivaYActualizaConteoYFechaDeInicio() {
        let pendiente = sesionDePrueba(status: "pending", framesCount: 0, startedAt: nil)
        let frame = RemoteFrame(imageB64: "aGVsbG8=", width: 100, height: 100, seq: 1)
        let activa = pendiente.conFrame(frame)
        #expect(activa.status == "active")
        #expect(activa.framesCount == 1)
        #expect(activa.startedAt != nil)
    }

    @Test func conFrameNoPisaUnaFechaDeInicioYaFijada() {
        let inicio = Date(timeIntervalSince1970: 500)
        let activa = sesionDePrueba(status: "active", framesCount: 1, startedAt: inicio)
        let conSegundoFrame = activa.conFrame(RemoteFrame(imageB64: "x", width: 10, height: 10, seq: 2))
        #expect(conSegundoFrame.startedAt == inicio)
        #expect(conSegundoFrame.framesCount == 2)
    }
}

struct RemoteFrameModelsTests {
    @Test func decodificaFrame() throws {
        let json = #"{"image_b64": "aGVsbG8=", "width": 1440, "height": 900, "seq": 3}"#
        let frame = try JSONDecoder().decode(RemoteFrame.self, from: Data(json.utf8))
        #expect(frame.imageB64 == "aGVsbG8=")
        #expect(frame.width == 1440)
        #expect(frame.height == 900)
        #expect(frame.seq == 3)
    }
}

/// Codifica exactamente el *shape* que espera
/// `edecan_api.routers.remote.PointerInputIn`/`KeyInputIn` (verificado
/// contra el código fuente del router) — decodifica de vuelta a
/// ``JSONValue`` (ya `Codable` en `EdecanKit`) para inspeccionar claves y
/// valores sin depender de un tipo de respuesta que el backend nunca manda.
struct RemoteInputModelsTests {
    private func codificarComoDiccionario<T: Encodable>(_ valor: T) throws -> [String: JSONValue] {
        let data = try JSONEncoder().encode(valor)
        return try JSONDecoder().decode([String: JSONValue].self, from: data)
    }

    @Test func codificaPointerInputConBotonOmiteBotonSiEsNil() throws {
        let dict = try codificarComoDiccionario(RemotePointerInput(x: 10, y: 20, accion: .click))
        #expect(dict["tipo"] == .string("pointer"))
        #expect(dict["x"] == .number(10))
        #expect(dict["y"] == .number(20))
        #expect(dict["accion"] == .string("click"))
        #expect(dict["button"] == nil)
    }

    @Test func codificaPointerInputConBotonExplicito() throws {
        let dict = try codificarComoDiccionario(
            RemotePointerInput(x: 5, y: 6, accion: .rightClick, button: .right)
        )
        #expect(dict["accion"] == .string("right_click"))
        #expect(dict["button"] == .string("right"))
    }

    @Test func codificaKeyInputDeTextoOmiteTecla() throws {
        let dict = try codificarComoDiccionario(RemoteKeyInput.texto("hola"))
        #expect(dict["tipo"] == .string("key"))
        #expect(dict["texto"] == .string("hola"))
        #expect(dict["tecla"] == nil)
    }

    @Test func codificaKeyInputDeTeclaEspecialOmiteTexto() throws {
        let dict = try codificarComoDiccionario(RemoteKeyInput.tecla(.enter))
        #expect(dict["tipo"] == .string("key"))
        #expect(dict["tecla"] == .string("enter"))
        #expect(dict["texto"] == nil)
    }

    @Test func todasLasTeclasEspecialesCodificanAlVocabularioExactoDelBackend() throws {
        // `edecan_companion.actions._SPECIAL_KEYS` — cualquier valor fuera de
        // este conjunto lo rechaza el backend con 422.
        let esperadas: Set<String> = [
            "enter", "tab", "escape", "backspace",
            "arrow_up", "arrow_down", "arrow_left", "arrow_right",
        ]
        let codificadas = Set(RemoteSpecialKey.allCases.map(\.rawValue))
        #expect(codificadas == esperadas)
    }

    /// Compara CONTENIDO decodificado, no bytes crudos: `JSONEncoder` no
    /// garantiza un orden de claves estable entre dos llamadas a `encode`
    /// distintas (sin `.sortedKeys`, el orden es un detalle de
    /// implementación) — lo que de verdad importa acá es que envolver en
    /// ``RemoteInput`` no agregue ni pierda ningún campo ni nivel de
    /// anidación, no que los bytes coincidan letra por letra.
    @Test func remoteInputEnvuelveSinAnidarProduceElMismoContenidoQueElTipoConcreto() throws {
        func comoDiccionario<T: Encodable>(_ valor: T) throws -> [String: JSONValue] {
            try JSONDecoder().decode([String: JSONValue].self, from: JSONEncoder().encode(valor))
        }

        let pointerDirecto = try comoDiccionario(RemotePointerInput(x: 1, y: 2, accion: .move))
        let pointerEnvuelto = try comoDiccionario(RemoteInput.pointer(RemotePointerInput(x: 1, y: 2, accion: .move)))
        #expect(pointerDirecto == pointerEnvuelto)

        let keyDirecto = try comoDiccionario(RemoteKeyInput.texto("hola"))
        let keyEnvuelto = try comoDiccionario(RemoteInput.key(.texto("hola")))
        #expect(keyDirecto == keyEnvuelto)
    }

    @Test func decodificaResultadoDeInputConDatos() throws {
        let json = #"{"ok": true, "result": {"x": 10, "y": 20, "accion": "click", "button": "left"}}"#
        let resultado = try JSONDecoder().decode(RemoteInputResult.self, from: Data(json.utf8))
        #expect(resultado.ok == true)
        #expect(resultado.result?["accion"] == .string("click"))
    }

    @Test func decodificaResultadoDeInputSinDatos() throws {
        let json = #"{"ok": true, "result": null}"#
        let resultado = try JSONDecoder().decode(RemoteInputResult.self, from: Data(json.utf8))
        #expect(resultado.ok == true)
        #expect(resultado.result == nil)
    }
}

/// Réplica de `apps/web/src/components/remoto/coords.ts` — mismos tres casos
/// que importan: mapeo 1:1 cuando las proporciones coinciden, `nil` cuando
/// el punto cae en la franja vacía del letterbox, y recorte a los bordes del
/// frame en el punto límite exacto.
struct RemoteCoordinateMapperTests {
    @Test func mapeaUnoAUnoCuandoLasProporcionesCoinciden() {
        let frame = RemoteFrame(imageB64: "x", width: 200, height: 100, seq: 1)
        let punto = RemoteCoordinateMapper.mapear(
            puntoLocalX: 100, puntoLocalY: 50,
            anchoElemento: 200, altoElemento: 100,
            frame: frame
        )
        #expect(punto?.x == 100)
        #expect(punto?.y == 50)
    }

    @Test func devuelveNilFueraDelRectanguloAunSinLetterbox() {
        let frame = RemoteFrame(imageB64: "x", width: 200, height: 100, seq: 1)
        let punto = RemoteCoordinateMapper.mapear(
            puntoLocalX: -5, puntoLocalY: 10,
            anchoElemento: 200, altoElemento: 100,
            frame: frame
        )
        #expect(punto == nil)
    }

    @Test func devuelveNilEnLaFranjaVaciaDelLetterboxVertical() {
        // Elemento cuadrado (200x200), frame ancho (400x100, aspecto 4:1) —
        // la imagen llena el ancho completo y deja franjas arriba/abajo.
        let frame = RemoteFrame(imageB64: "x", width: 400, height: 100, seq: 1)
        let enLaFranja = RemoteCoordinateMapper.mapear(
            puntoLocalX: 100, puntoLocalY: 10, // top=75 con este tamaño, 10 < 75
            anchoElemento: 200, altoElemento: 200,
            frame: frame
        )
        #expect(enLaFranja == nil)

        let dentroDeLaImagen = RemoteCoordinateMapper.mapear(
            puntoLocalX: 100, puntoLocalY: 100, // dentro del rango [75, 125]
            anchoElemento: 200, altoElemento: 200,
            frame: frame
        )
        #expect(dentroDeLaImagen?.x == 200) // centro horizontal del elemento -> centro del frame (400/2)
        #expect(dentroDeLaImagen?.y == 50)
    }

    @Test func recortaAlLimiteMaximoDelFrameEnElPuntoLimiteExacto() {
        let frame = RemoteFrame(imageB64: "x", width: 200, height: 100, seq: 1)
        let punto = RemoteCoordinateMapper.mapear(
            puntoLocalX: 200, puntoLocalY: 100, // exactamente el borde del elemento
            anchoElemento: 200, altoElemento: 100,
            frame: frame
        )
        #expect(punto?.x == 199) // frame.width - 1, nunca frame.width (fuera de rango)
        #expect(punto?.y == 99)
    }

    @Test func devuelveNilConFrameSinDimensiones() {
        let frame = RemoteFrame(imageB64: "x", width: 0, height: 0, seq: 1)
        let punto = RemoteCoordinateMapper.mapear(
            puntoLocalX: 10, puntoLocalY: 10, anchoElemento: 100, altoElemento: 100, frame: frame
        )
        #expect(punto == nil)
    }

    @Test func rectanguloContenidoDegeneradoDevuelveElElementoCompleto() {
        let rect = RemoteCoordinateMapper.rectanguloContenido(
            anchoElemento: 0, altoElemento: 100, anchoNatural: 50, altoNatural: 50
        )
        #expect(rect.width == 0)
        #expect(rect.height == 100)
        #expect(rect.left == 0)
        #expect(rect.top == 0)
    }
}
