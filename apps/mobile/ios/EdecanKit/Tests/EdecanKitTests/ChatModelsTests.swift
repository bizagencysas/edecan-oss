import Foundation
import Testing
@testable import EdecanKit

/// Contrato del selector de modelos del chat: `GET /v1/models/chat`,
/// `PUT /v1/conversations/{id}/model` y los campos nuevos de la conversación.
/// El JSON de abajo es el del backend real (`apps/api/edecan_api/routers/
/// models.py` + `config/modelos.yml`), copiado tal cual para que un cambio de
/// forma rompa aquí y no en el simulador.
struct ChatModelsTests {
    /// Respuesta literal de `GET /v1/models/chat`: 7 modelos, los 4
    /// principales con `orden` 1..4 primero.
    private static let catalogoJSON = """
    {
      "default": "@cf/meta/llama-4-scout-17b-16e-instruct",
      "esfuerzos": ["bajo", "medio", "alto"],
      "esfuerzo_default": "medio",
      "modelos": [
        {"id": "@cf/meta/llama-4-scout-17b-16e-instruct", "nombre": "Copla", "descripcion": "Ligero y al instante · ve imágenes", "orden": 1, "principal": true, "ve_imagenes": true, "soporta_esfuerzo": false, "contexto_ventana": 131072},
        {"id": "@cf/moonshotai/kimi-k2.7-code", "nombre": "Silva", "descripcion": "Contexto enorme, fuerte en código", "orden": 2, "principal": true, "ve_imagenes": true, "soporta_esfuerzo": true, "contexto_ventana": 262144},
        {"id": "@cf/google/gemma-4-26b-a4b-it", "nombre": "Soneto", "descripcion": "Equilibrado y con criterio", "orden": 3, "principal": true, "ve_imagenes": true, "soporta_esfuerzo": true, "contexto_ventana": 256000},
        {"id": "@cf/moonshotai/kimi-k2.6", "nombre": "Oda", "descripcion": "El más profundo, para lo difícil", "orden": 4, "principal": true, "ve_imagenes": true, "soporta_esfuerzo": true, "contexto_ventana": 262144},
        {"id": "@cf/openai/gpt-oss-20b", "nombre": "GPT-OSS 20B", "descripcion": "El más veloz del catálogo · No ve imágenes", "orden": 5, "principal": false, "ve_imagenes": false, "soporta_esfuerzo": false, "contexto_ventana": 131072},
        {"id": "@cf/openai/gpt-oss-120b", "nombre": "GPT-OSS 120B", "descripcion": "Grande y rápido para análisis · No ve imágenes", "orden": 6, "principal": false, "ve_imagenes": false, "soporta_esfuerzo": false, "contexto_ventana": 131072},
        {"id": "@cf/nvidia/nemotron-3-120b-a12b", "nombre": "Nemotron 3 120B", "descripcion": "Análisis pesado, contexto largo · No ve imágenes", "orden": 7, "principal": false, "ve_imagenes": false, "soporta_esfuerzo": false, "contexto_ventana": 131072}
      ]
    }
    """

    private static func catalogo() throws -> ChatModelCatalog {
        try JSONDecoder().decode(ChatModelCatalog.self, from: Data(catalogoJSON.utf8))
    }

    // MARK: - GET /v1/models/chat

    @Test func decodificaCatalogoCompleto() throws {
        let catalogo = try Self.catalogo()
        #expect(catalogo.porDefecto == "@cf/meta/llama-4-scout-17b-16e-instruct")
        #expect(catalogo.esfuerzos == [.bajo, .medio, .alto])
        #expect(catalogo.esfuerzoPorDefecto == .medio)
        #expect(catalogo.modelos.count == 7)
    }

    @Test func separaPortadaDeMasModelos() throws {
        let catalogo = try Self.catalogo()
        #expect(catalogo.principales.map(\.nombre) == ["Copla", "Silva", "Soneto", "Oda"])
        #expect(catalogo.secundarios.count == 3)
        #expect(catalogo.secundarios.allSatisfy { !$0.veImagenes })
    }

    @Test func leeLasBanderasQueDecidenLaUI() throws {
        let catalogo = try Self.catalogo()
        let copla = catalogo.modelo(id: "@cf/meta/llama-4-scout-17b-16e-instruct")
        // Copla ve imágenes pero NO razona: la fila "Esfuerzo" no debe salir.
        #expect(copla?.veImagenes == true)
        #expect(copla?.soportaEsfuerzo == false)
        #expect(copla?.contextoVentana == 131_072)

        let oda = catalogo.modelo(id: "@cf/moonshotai/kimi-k2.6")
        #expect(oda?.soportaEsfuerzo == true)
        #expect(oda?.veImagenes == true)

        let nemotron = catalogo.modelo(id: "@cf/nvidia/nemotron-3-120b-a12b")
        #expect(nemotron?.veImagenes == false)
        #expect(catalogo.modelo(id: "@cf/no/existe") == nil)
        #expect(catalogo.modelo(id: nil) == nil)
        #expect(nemotron?.principal == false)
    }

    /// Un nivel de Esfuerzo que el cliente todavía no conoce no puede dejar la
    /// hoja vacía: se ignora y el resto del catálogo sigue sirviendo.
    @Test func toleraEsfuerzosDesconocidos() throws {
        let json = """
        {
          "default": "@cf/meta/llama-4-scout-17b-16e-instruct",
          "esfuerzos": ["bajo", "extremo"],
          "esfuerzo_default": "extremo",
          "modelos": []
        }
        """
        let catalogo = try JSONDecoder().decode(ChatModelCatalog.self, from: Data(json.utf8))
        #expect(catalogo.esfuerzos == [.bajo])
        #expect(catalogo.esfuerzoPorDefecto == .medio)
    }

    @Test func ignoraClavesExtraDelModelo() throws {
        let json = """
        {"id": "x", "nombre": "X", "descripcion": "d", "orden": 1, "principal": true,
         "ve_imagenes": true, "soporta_esfuerzo": true, "contexto_ventana": 10,
         "insignia": "algo que el cliente no conoce"}
        """
        let modelo = try JSONDecoder().decode(ChatModelInfo.self, from: Data(json.utf8))
        #expect(modelo.nombre == "X")
    }

    // MARK: - Conversación con y sin selección (retro-compatibilidad)

    @Test func decodificaConversacionConModeloYEsfuerzo() throws {
        let json = """
        {"id": "c1", "title": "Hola", "channel": "web", "is_main": false,
         "model": "@cf/moonshotai/kimi-k2.6", "effort": "alto",
         "created_at": "2026-07-29T10:00:00Z", "updated_at": null}
        """
        let conversation = try APIClient.crearDecoder()
            .decode(Conversation.self, from: Data(json.utf8))
        #expect(conversation.model == "@cf/moonshotai/kimi-k2.6")
        #expect(conversation.effort == .alto)
    }

    /// Un servidor anterior al selector no manda `model`/`effort`. El
    /// historial tiene que seguir cargando igual.
    @Test func decodificaConversacionDeServidorViejo() throws {
        let json = """
        {"id": "c1", "title": null, "channel": "web",
         "created_at": "2026-07-29T10:00:00Z", "updated_at": null}
        """
        let conversation = try APIClient.crearDecoder()
            .decode(Conversation.self, from: Data(json.utf8))
        #expect(conversation.model == nil)
        #expect(conversation.effort == nil)
        #expect(conversation.isMain == false)
    }

    @Test func conversacionEnAutomaticoLlegaConNullExplicito() throws {
        let json = """
        {"id": "c1", "title": null, "channel": "web", "is_main": true,
         "model": null, "effort": null,
         "created_at": "2026-07-29T10:00:00Z", "updated_at": "2026-07-29T11:00:00Z"}
        """
        let conversation = try APIClient.crearDecoder()
            .decode(Conversation.self, from: Data(json.utf8))
        #expect(conversation.model == nil)
        #expect(conversation.effort == nil)
    }

    /// Un Esfuerzo desconocido en la fila degrada a "sin nivel", no impide
    /// abrir la conversación.
    @Test func conversacionConEsfuerzoDesconocidoNoRompe() throws {
        let json = """
        {"id": "c1", "title": null, "channel": "web", "model": "m", "effort": "extremo",
         "created_at": "2026-07-29T10:00:00Z", "updated_at": null}
        """
        let conversation = try APIClient.crearDecoder()
            .decode(Conversation.self, from: Data(json.utf8))
        #expect(conversation.model == "m")
        #expect(conversation.effort == nil)
    }

    @Test func decodificaDetalleConSeleccion() throws {
        let json = """
        {"id": "c1", "title": "T", "channel": "web", "is_main": false,
         "model": "@cf/google/gemma-4-26b-a4b-it", "effort": "bajo",
         "created_at": "2026-07-29T10:00:00Z", "updated_at": null,
         "messages": [], "pending_confirmation": null}
        """
        let detalle = try APIClient.crearDecoder()
            .decode(ConversationDetail.self, from: Data(json.utf8))
        #expect(detalle.model == "@cf/google/gemma-4-26b-a4b-it")
        #expect(detalle.effort == .bajo)
        #expect(detalle.messages.isEmpty)
        #expect(detalle.pendingConfirmation == nil)
    }

    @Test func decodificaDetalleDeServidorViejo() throws {
        let json = """
        {"id": "c1", "title": null, "channel": "web",
         "created_at": "2026-07-29T10:00:00Z", "updated_at": null, "messages": []}
        """
        let detalle = try APIClient.crearDecoder()
            .decode(ConversationDetail.self, from: Data(json.utf8))
        #expect(detalle.model == nil)
        #expect(detalle.effort == nil)
    }

    // MARK: - PUT /v1/conversations/{id}/model

    /// Las dos claves van SIEMPRE: para ese endpoint `null` significa "volver
    /// a automático", no "no cambiar". Con `encodeIfPresent` la clave
    /// desaparecería y volver atrás sería imposible.
    @Test func codificaCuerpoConAmbasClavesSiempre() throws {
        let data = try JSONEncoder().encode(SeleccionModeloChatIn(model: nil, effort: nil))
        let objeto = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        #expect(objeto?.keys.sorted() == ["effort", "model"])
        #expect(objeto?["model"] is NSNull)
        #expect(objeto?["effort"] is NSNull)
    }

    @Test func codificaCuerpoConModeloYEsfuerzo() throws {
        let data = try JSONEncoder().encode(
            SeleccionModeloChatIn(model: "@cf/moonshotai/kimi-k2.6", effort: .alto)
        )
        let objeto = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        #expect(objeto?["model"] as? String == "@cf/moonshotai/kimi-k2.6")
        #expect(objeto?["effort"] as? String == "alto")
    }

    @Test func decodificaRespuestaDelPut() throws {
        let json = #"{"model": "@cf/moonshotai/kimi-k2.7-code", "effort": "medio"}"#
        let out = try JSONDecoder().decode(SeleccionModeloChatOut.self, from: Data(json.utf8))
        #expect(out.model == "@cf/moonshotai/kimi-k2.7-code")
        #expect(out.effort == .medio)

        let automatico = try JSONDecoder()
            .decode(SeleccionModeloChatOut.self, from: Data(#"{"model": null, "effort": null}"#.utf8))
        #expect(automatico.model == nil)
        #expect(automatico.effort == nil)
    }

    // MARK: - Etiqueta de la pastilla del composer

    @Test func etiquetaConModeloQueSoportaEsfuerzo() throws {
        let catalogo = try Self.catalogo()
        let seleccion = SeleccionDeModeloChat(
            modeloId: "@cf/moonshotai/kimi-k2.6",
            info: catalogo.modelo(id: "@cf/moonshotai/kimi-k2.6"),
            esfuerzo: .alto
        )
        #expect(seleccion.etiqueta == "Oda · Alto")
        #expect(seleccion.muestraEsfuerzo)
    }

    /// Copla no razona: la pastilla NO debe insinuar un nivel que el turno no
    /// va a aplicar, ni siquiera si quedó guardado de un modelo anterior.
    @Test func etiquetaOcultaEsfuerzoEnModeloQueNoLoSoporta() throws {
        let catalogo = try Self.catalogo()
        let seleccion = SeleccionDeModeloChat(
            modeloId: "@cf/meta/llama-4-scout-17b-16e-instruct",
            info: catalogo.modelo(id: "@cf/meta/llama-4-scout-17b-16e-instruct"),
            esfuerzo: .alto
        )
        #expect(seleccion.etiqueta == "Copla")
        #expect(seleccion.muestraEsfuerzo == false)
    }

    @Test func etiquetaEnAutomatico() {
        let seleccion = SeleccionDeModeloChat(modeloId: nil, info: nil, esfuerzo: .medio)
        #expect(seleccion.etiqueta == "Automático")
        #expect(seleccion.nombreVisible == nil)
        #expect(seleccion.muestraEsfuerzo == false)
    }

    /// Si la conversación ya trae un id pero el catálogo no llegó, mentir con
    /// "Automático" sería peor que mostrar el tramo final del id.
    @Test func etiquetaSinCatalogoUsaElIdCorto() {
        let seleccion = SeleccionDeModeloChat(
            modeloId: "@cf/moonshotai/kimi-k2.6", info: nil, esfuerzo: .alto
        )
        #expect(seleccion.etiqueta == "kimi-k2.6")
    }

    // MARK: - Aviso de modelo ciego

    @Test func avisaCuandoElModeloElegidoNoVeYHayImagen() throws {
        let catalogo = try Self.catalogo()
        let seleccion = SeleccionDeModeloChat(
            modeloId: "@cf/openai/gpt-oss-20b",
            info: catalogo.modelo(id: "@cf/openai/gpt-oss-20b"),
            esfuerzo: nil
        )
        let aviso = seleccion.avisoDeCeguera(hayImagenEnElTurno: true)
        #expect(aviso?.contains("GPT-OSS 20B") == true)
        #expect(aviso?.contains("no ve imágenes") == true)
        // Sin imagen en el turno no hay nada que avisar.
        #expect(seleccion.avisoDeCeguera(hayImagenEnElTurno: false) == nil)
    }

    @Test func noAvisaConModeloQueVe() throws {
        let catalogo = try Self.catalogo()
        let seleccion = SeleccionDeModeloChat(
            modeloId: "@cf/google/gemma-4-26b-a4b-it",
            info: catalogo.modelo(id: "@cf/google/gemma-4-26b-a4b-it"),
            esfuerzo: .medio
        )
        #expect(seleccion.avisoDeCeguera(hayImagenEnElTurno: true) == nil)
    }

    /// En automático decide el backend, que ya usa un modelo con visión
    /// cuando el turno trae imagen: avisar ahí sería alarmar sin motivo.
    @Test func noAvisaEnAutomatico() {
        let seleccion = SeleccionDeModeloChat(modeloId: nil, info: nil, esfuerzo: nil)
        #expect(seleccion.avisoDeCeguera(hayImagenEnElTurno: true) == nil)
    }

    // MARK: - MIMEs de visión directa

    @Test func reconoceLosMimesQueElBackendInsertaComoImagen() {
        #expect(MimesConVisionDirecta.esImagen("image/png"))
        #expect(MimesConVisionDirecta.esImagen("image/jpeg"))
        #expect(MimesConVisionDirecta.esImagen("image/gif"))
        #expect(MimesConVisionDirecta.esImagen("image/webp"))
        // Con parámetros y en mayúsculas sigue siendo la misma imagen.
        #expect(MimesConVisionDirecta.esImagen("IMAGE/PNG; charset=binary"))
        #expect(MimesConVisionDirecta.esImagen("image/heic") == false)
        #expect(MimesConVisionDirecta.esImagen("application/pdf") == false)
        #expect(MimesConVisionDirecta.esImagen(nil) == false)
    }

    // MARK: - Nombres de los niveles

    @Test func nombresLegiblesDelEsfuerzo() {
        #expect(EsfuerzoChat.bajo.nombreLegible == "Bajo")
        #expect(EsfuerzoChat.medio.nombreLegible == "Medio")
        #expect(EsfuerzoChat.alto.nombreLegible == "Alto")
        #expect(EsfuerzoChat.allCases.map(\.rawValue) == ["bajo", "medio", "alto"])
    }
}
