import Testing
import Foundation
@testable import EdecanKit

/// Decodifica las MISMAS fixtures doradas del paso 3 del plan de SDUI
/// (`packages/schemas/tests/fixtures/chat_blocks/`, ver
/// `/private/tmp/sdui_plan_mvp.json`) -- si `edecan_schemas.chat` cambia de
/// forma en el backend sin actualizar este cliente a la vez, uno de estos
/// tests debería fallar. Copiadas byte a byte (incluyendo `_comentario`/
/// `_descripcion`, que `ChatBlock` ignora como cualquier campo desconocido).
struct CardGenericaTests {
    private func decodificarBloque(_ json: String) throws -> ChatBlock {
        try JSONDecoder().decode(ChatBlock.self, from: Data(json.utf8))
    }

    // MARK: - card_linkedin.json

    private static let cardLinkedin = #"""
    {
      "_comentario": "FIXTURE DORADA -- contrato congelado. Fuente única de verdad para el MVP de Server-Driven UI del chat (ver /private/tmp/sdui_plan_mvp.json). packages/schemas/tests/test_chat_card.py la valida contra Pydantic; EdecanKitTests/CardGenericaTests.swift (iOS), y los tests de web/Android, decodifican ESTOS MISMOS BYTES -- si una plataforma se desvía de este contrato, su test dorado truena. No editar sin actualizar las 4 plataformas a la vez.",
      "_descripcion": "La card real de LinkedIn (kicker con badge en vivo + imagen + copy + botones Copiar/Aprobar) expresada por completo con las 6 primitivas del vocabulario -- la prueba de que una card real cabe en el vocabulario mínimo.",
      "type": "card",
      "schema_version": 1,
      "card_id": "linkedin-draft-1",
      "fallback_text": "Post de LinkedIn listo para revisar. Aprobar o copiar el texto.",
      "raiz": {
        "nodo": "stack",
        "eje": "vertical",
        "espaciado": "m",
        "relleno": "tarjeta",
        "fondo": "acento_suave",
        "alineacion": "inicio",
        "hijos": [
          {
            "nodo": "stack",
            "eje": "horizontal",
            "espaciado": "m",
            "relleno": "ninguno",
            "fondo": "ninguno",
            "alineacion": "centro",
            "hijos": [
              {
                "nodo": "texto",
                "contenido": "Post de LinkedIn listo para revisar",
                "rol": "titulo",
                "color": "primario",
                "enfasis": "fuerte",
                "max_lineas": 2
              },
              {
                "nodo": "badge",
                "contenido": "En vivo",
                "tono": "exito"
              }
            ]
          },
          {
            "nodo": "imagen",
            "artifact": {
              "file_id": "6f6b7f9a-6b0e-4b1e-9f7f-8f9a6b7f9a6b",
              "filename": "post_organization.png",
              "mime": "image/png"
            },
            "alt": "Gráfico sobre crédito en Venezuela",
            "aspecto": "panoramica",
            "esquinas": "m"
          },
          {
            "nodo": "divisor"
          },
          {
            "nodo": "stack",
            "eje": "horizontal",
            "espaciado": "s",
            "relleno": "ninguno",
            "fondo": "ninguno",
            "alineacion": "inicio",
            "hijos": [
              {
                "nodo": "boton",
                "estilo": "secundario",
                "accion": {
                  "id": "post.copiar",
                  "label": "Copiar",
                  "action": "copy_text",
                  "text": "Un post real sobre crédito en Venezuela."
                }
              },
              {
                "nodo": "boton",
                "estilo": "primario",
                "accion": {
                  "id": "post.aprobar",
                  "label": "Aprobar",
                  "action": "approve_draft",
                  "draft_id": "draft-123"
                }
              }
            ]
          }
        ]
      }
    }
    """#

    @Test func cardLinkedinProduceElArbolEsperado() throws {
        guard case .card(let card) = try decodificarBloque(Self.cardLinkedin) else {
            Issue.record("se esperaba un bloque .card")
            return
        }
        #expect(card.cardId == "linkedin-draft-1")
        #expect(card.fallbackText == "Post de LinkedIn listo para revisar. Aprobar o copiar el texto.")
        #expect(card.raiz.eje == .vertical)
        #expect(card.raiz.fondo == .acentoSuave)
        #expect(card.raiz.hijos.count == 4)

        guard case .stack(let kicker) = card.raiz.hijos[0] else {
            Issue.record("se esperaba un stack horizontal de kicker")
            return
        }
        #expect(kicker.eje == .horizontal)
        #expect(kicker.hijos.count == 2)
        guard case .texto(let titulo) = kicker.hijos[0] else {
            Issue.record("se esperaba un nodo de texto")
            return
        }
        #expect(titulo.contenido == "Post de LinkedIn listo para revisar")
        #expect(titulo.rol == .titulo)
        #expect(titulo.enfasis == .fuerte)
        #expect(titulo.maxLineas == 2)
        guard case .badge(let badge) = kicker.hijos[1] else {
            Issue.record("se esperaba un badge")
            return
        }
        #expect(badge.contenido == "En vivo")
        #expect(badge.tono == .exito)

        guard case .imagen(let imagen) = card.raiz.hijos[1] else {
            Issue.record("se esperaba un nodo de imagen")
            return
        }
        #expect(imagen.artifact.fileId == "6f6b7f9a-6b0e-4b1e-9f7f-8f9a6b7f9a6b")
        #expect(imagen.aspecto == .panoramica)
        #expect(imagen.esquinas == .m)

        guard case .divisor = card.raiz.hijos[2] else {
            Issue.record("se esperaba un divisor")
            return
        }

        guard case .stack(let botones) = card.raiz.hijos[3] else {
            Issue.record("se esperaba un stack horizontal de botones")
            return
        }
        #expect(botones.hijos.count == 2)
        guard case .boton(let copiar) = botones.hijos[0] else {
            Issue.record("se esperaba el botón Copiar")
            return
        }
        #expect(copiar.estilo == .secundario)
        #expect(copiar.accion == .copyText(id: "post.copiar", label: "Copiar", text: "Un post real sobre crédito en Venezuela."))
        guard case .boton(let aprobar) = botones.hijos[1] else {
            Issue.record("se esperaba el botón Aprobar")
            return
        }
        #expect(aprobar.estilo == .primario)
        #expect(aprobar.accion == .approveDraft(id: "post.aprobar", label: "Aprobar", draftId: "draft-123"))
    }

    // MARK: - card_nodo_desconocido.json

    private static let cardNodoDesconocido = #"""
    {
      "_comentario": "FIXTURE DORADA -- contrato congelado. Fuente única de verdad para el MVP de Server-Driven UI del chat (ver /private/tmp/sdui_plan_mvp.json). packages/schemas/tests/test_chat_card.py la valida contra Pydantic; EdecanKitTests/CardGenericaTests.swift (iOS), y los tests de web/Android, decodifican ESTOS MISMOS BYTES -- si una plataforma se desvía de este contrato, su test dorado truena. No editar sin actualizar las 4 plataformas a la vez.",
      "_descripcion": "Card válida (nivel 1 de forward-compat) con un nodo 'holograma' -- que NO existe en el vocabulario de 6 primitivas -- intercalado entre hijos conocidos. Prueba que el cliente lo decodifica como el caso tolerante (.desconocido) y lo SALTA al pintar, sin tumbar la card ni a sus hermanos.",
      "type": "card",
      "schema_version": 1,
      "card_id": "card-nodo-desconocido-1",
      "fallback_text": "Contenido con un nodo futuro que este cliente no reconoce todavía.",
      "raiz": {
        "nodo": "stack",
        "eje": "vertical",
        "espaciado": "m",
        "hijos": [
          {
            "nodo": "texto",
            "contenido": "Antes del nodo futuro",
            "rol": "titulo",
            "enfasis": "fuerte"
          },
          {
            "nodo": "holograma",
            "algo_futuro": true,
            "proyeccion_3d": {
              "activa": true,
              "color": "#00ffea"
            }
          },
          {
            "nodo": "divisor"
          },
          {
            "nodo": "texto",
            "contenido": "Después del nodo futuro",
            "rol": "cuerpo"
          },
          {
            "nodo": "boton",
            "estilo": "primario",
            "accion": {
              "id": "boton.futuro",
              "label": "Lanzar",
              "action": "lanzar_cohete",
              "launch_pad": "39A"
            }
          }
        ]
      }
    }
    """#

    @Test func cardNodoDesconocidoConservaHermanosYSaltaElRaro() throws {
        guard case .card(let card) = try decodificarBloque(Self.cardNodoDesconocido) else {
            Issue.record("se esperaba un bloque .card")
            return
        }
        #expect(card.raiz.hijos.count == 5)

        guard case .texto(let antes) = card.raiz.hijos[0] else {
            Issue.record("se esperaba el texto anterior al nodo futuro")
            return
        }
        #expect(antes.contenido == "Antes del nodo futuro")

        guard case .desconocido(let nodo) = card.raiz.hijos[1] else {
            Issue.record("se esperaba el nodo desconocido 'holograma'")
            return
        }
        #expect(nodo == "holograma")

        guard case .divisor = card.raiz.hijos[2] else {
            Issue.record("se esperaba el divisor tras el nodo desconocido")
            return
        }

        guard case .texto(let despues) = card.raiz.hijos[3] else {
            Issue.record("se esperaba el texto posterior al nodo futuro")
            return
        }
        #expect(despues.contenido == "Después del nodo futuro")

        // El nodo `boton` en sí es conocido; lo que no reconoce es su
        // `accion` ("lanzar_cohete") -- por eso el BOTÓN sobrevive como
        // `.boton`, pero con una acción `.unsupported` que el renderer no
        // pinta (nivel 1 de forward-compat también dentro de la acción).
        guard case .boton(let botonFuturo) = card.raiz.hijos[4] else {
            Issue.record("se esperaba que el nodo boton sobreviviera")
            return
        }
        guard case .unsupported(let id, let label, let action) = botonFuturo.accion else {
            Issue.record("se esperaba una acción unsupported dentro del botón")
            return
        }
        #expect(id == "boton.futuro")
        #expect(label == "Lanzar")
        #expect(action == "lanzar_cohete")
    }

    // MARK: - card_version_futura.json

    private static let cardVersionFutura = #"""
    {
      "_comentario": "FIXTURE DORADA -- contrato congelado. Fuente única de verdad para el MVP de Server-Driven UI del chat (ver /private/tmp/sdui_plan_mvp.json). packages/schemas/tests/test_chat_card.py la valida contra Pydantic; EdecanKitTests/CardGenericaTests.swift (iOS), y los tests de web/Android, decodifican ESTOS MISMOS BYTES -- si una plataforma se desvía de este contrato, su test dorado truena. No editar sin actualizar las 4 plataformas a la vez.",
      "_descripcion": "Nivel 3 de forward-compat: un bloque con schema_version futuro (99) y campos extra en varios niveles (bloque, nodo raíz, nodo de texto). El backend actual (edecan_schemas.chat, que es el ÚNICO emisor real) fija schema_version en Literal[1] a propósito -- nunca emite otra cosa -- así que este payload representa lo que un SERVIDOR MÁS NUEVO enviaría a un CLIENTE MÁS VIEJO. El gate correspondiente vive en cada cliente (p.ej. iOS: `schemaVersion > versionMaximaSoportada -> unsupported`), NO en este paquete de esquemas; por eso `fallback_text` es obligatorio y debe ser útil por sí solo.",
      "type": "card",
      "schema_version": 99,
      "card_id": "card-version-futura-1",
      "fallback_text": "Esta tarjeta usa una versión más nueva del contrato; tu app no puede mostrarla todavía.",
      "capacidad_nueva_del_futuro": "algo_que_este_paquete_no_conoce",
      "raiz": {
        "nodo": "stack",
        "eje": "vertical",
        "espaciado": "m",
        "efecto_futuro": "particulas_3d",
        "hijos": [
          {
            "nodo": "texto",
            "contenido": "Texto compatible con el futuro",
            "rol": "cuerpo",
            "brillo_futuro": "neon"
          }
        ]
      }
    }
    """#

    @Test func cardVersionFuturaCaeAFallbackText() throws {
        guard case .unsupported(let type, let fallback) = try decodificarBloque(Self.cardVersionFutura) else {
            Issue.record("se esperaba un bloque .unsupported")
            return
        }
        #expect(type == "card")
        #expect(fallback == "Esta tarjeta usa una versión más nueva del contrato; tu app no puede mostrarla todavía.")
    }

    // MARK: - Defensa en el cliente (además de la validación server-side)

    /// `chat.py` ya rechaza en Pydantic cualquier card con más de
    /// `cardMaxNodos` nodos antes de emitirla -- este payload nunca debería
    /// llegar en producción. El cliente vuelve a aplicar el límite de todas
    /// formas (nunca confiar solo en el emisor): en vez de tumbar la card
    /// entera, poda en silencio los nodos que sobran del presupuesto.
    @Test func excesoDeNodosSePodaEnSilencio() throws {
        var hijosJSON = ""
        for indice in 0..<50 {
            if indice > 0 { hijosJSON += "," }
            hijosJSON += #"{"nodo":"texto","contenido":"nodo \#(indice)"}"#
        }
        let json = #"""
        {"type":"card","schema_version":1,"card_id":"c","fallback_text":"f",
         "raiz":{"nodo":"stack","hijos":[\#(hijosJSON)]}}
        """#
        guard case .card(let card) = try decodificarBloque(json) else {
            Issue.record("se esperaba un bloque .card")
            return
        }
        // Presupuesto total 40: 1 para la raíz, 39 para los hijos.
        #expect(card.raiz.hijos.count == 39)
    }

    /// Cinco stacks anidados exceden `cardMaxProfundidad` (4): el quinto
    /// nivel (y cualquier hoja dentro de él) se poda, los primeros cuatro
    /// niveles se conservan.
    @Test func excesoDeProfundidadSePoda() throws {
        let json = #"""
        {"type":"card","schema_version":1,"card_id":"c","fallback_text":"f",
         "raiz":{"nodo":"stack","hijos":[
           {"nodo":"stack","hijos":[
             {"nodo":"stack","hijos":[
               {"nodo":"stack","hijos":[
                 {"nodo":"texto","contenido":"demasiado profundo"}
               ]}
             ]}
           ]}
         ]}}
        """#
        guard case .card(let card) = try decodificarBloque(json) else {
            Issue.record("se esperaba un bloque .card")
            return
        }
        // raiz = nivel 1, hijos[0] = nivel 2, su hijo = nivel 3, su hijo =
        // nivel 4 (stack, sobrevive), pero el `texto` adentro sería nivel 5
        // y se poda.
        guard case .stack(let nivel2) = card.raiz.hijos[0],
              case .stack(let nivel3) = nivel2.hijos[0],
              case .stack(let nivel4) = nivel3.hijos[0]
        else {
            Issue.record("se esperaba conservar los primeros 4 niveles")
            return
        }
        #expect(nivel4.hijos.isEmpty)
    }
}
