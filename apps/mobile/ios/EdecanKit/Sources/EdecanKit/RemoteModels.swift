import Foundation

// MARK: - `/v1/remote/*` — control remoto tipo TeamViewer del Mac/PC
// companion (`ARCHITECTURE.md` §13.c/§14, `apps/api/edecan_api/routers/
// remote.py`, `docs/control-remoto.md`). `kind="view"` (solo lectura) y
// `kind="control"` (WP-V4-10: además input real de teclado/mouse) del mismo
// prototipo de *polling* HTTP — NUNCA WebRTC/streaming todavía (ver §1.1 de
// ese documento), así que este cliente no abre ningún socket propio: cada
// frame/comando es una petición HTTP suelta, igual que
// `apps/web/src/lib/api-remoto.ts`.

/// Fila de `remote_sessions`, espejo EXACTO de lo que devuelven
/// `POST /v1/remote/sessions`, `GET /v1/remote/sessions[/{id}]` y
/// `POST /v1/remote/sessions/{id}/end` (`Repo._REMOTE_SESSION_COLUMNS`,
/// verificado contra `apps/api/edecan_api/repo.py` y
/// `apps/api/tests/test_remote_router.py`: `id, tenant_id, user_id,
/// device_id, kind, status, started_at, ended_at, frames_count, created_at,
/// updated_at`).
public struct RemoteSession: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let tenantId: String
    public let userId: String
    /// Siempre `nil` hoy: el emparejamiento de `remote_sessions` con la tabla
    /// `devices` sigue sin ningún código que lo escriba
    /// (`docs/control-remoto.md` §1, fila "Tabla `devices` en Postgres").
    public let deviceId: String?
    /// `"view"` (default, solo lectura) o `"control"` (WP-V4-10, input real
    /// de teclado/mouse) — `String` crudo, no un enum Swift cerrado, mismo
    /// criterio que `Conversation.channel`/`MissionOut.status`: si el
    /// backend suma un `kind` nuevo, decodificar no debe romperse.
    public let kind: String
    /// `"pending" | "active" | "ended" | "denied"`.
    public let status: String
    public let startedAt: Date?
    public let endedAt: Date?
    public let framesCount: Int
    public let createdAt: Date
    public let updatedAt: Date

    enum CodingKeys: String, CodingKey {
        case id, kind, status
        case tenantId = "tenant_id"
        case userId = "user_id"
        case deviceId = "device_id"
        case startedAt = "started_at"
        case endedAt = "ended_at"
        case framesCount = "frames_count"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }

    public init(
        id: String, tenantId: String, userId: String, deviceId: String?, kind: String, status: String,
        startedAt: Date?, endedAt: Date?, framesCount: Int, createdAt: Date, updatedAt: Date
    ) {
        self.id = id
        self.tenantId = tenantId
        self.userId = userId
        self.deviceId = deviceId
        self.kind = kind
        self.status = status
        self.startedAt = startedAt
        self.endedAt = endedAt
        self.framesCount = framesCount
        self.createdAt = createdAt
        self.updatedAt = updatedAt
    }

    /// `true` si esta sesión pidió `kind="control"` (input real de
    /// teclado/mouse), no solo vista.
    public var esControl: Bool { kind == "control" }

    /// La sesión sigue viva del lado del cliente (todavía no terminó ni fue
    /// denegada) — mismo criterio que `session.status !== "ended" &&
    /// status !== "denied"` en `apps/web/src/components/remoto/RemoteViewer.tsx`.
    public var sigueViva: Bool { status == "pending" || status == "active" }

    /// `true` si ya no se puede pedir más frames/mandar más input.
    public var esTerminal: Bool { status == "ended" || status == "denied" }

    /// Copia con `status` reemplazado — usada por ``RemotoViewModel`` para
    /// reflejar localmente una transición que el servidor ya aplicó (p. ej.
    /// `403`/`409` de `GET .../frame` o `POST .../input`) sin tener que
    /// volver a pedir la sesión completa.
    public func conEstado(_ nuevoStatus: String) -> RemoteSession {
        RemoteSession(
            id: id, tenantId: tenantId, userId: userId, deviceId: deviceId, kind: kind,
            status: nuevoStatus, startedAt: startedAt, endedAt: endedAt, framesCount: framesCount,
            createdAt: createdAt, updatedAt: updatedAt
        )
    }

    /// Copia reflejando un frame recién recibido: pasa a `"active"` (nunca
    /// vuelve a `"pending"`), fija `startedAt` si todavía no estaba fijado, y
    /// actualiza `framesCount` a `frame.seq` — mismo contrato que
    /// `record_remote_session_frame` en el servidor (`apps/api/edecan_api/repo.py`).
    public func conFrame(_ frame: RemoteFrame) -> RemoteSession {
        RemoteSession(
            id: id, tenantId: tenantId, userId: userId, deviceId: deviceId, kind: kind,
            status: "active", startedAt: startedAt ?? Date(), endedAt: endedAt,
            framesCount: frame.seq, createdAt: createdAt, updatedAt: Date()
        )
    }
}

/// `GET /v1/remote/sessions/{id}/frame` — un PNG suelto en base64 + el
/// tamaño real de la captura (necesario para mapear gestos → coordenadas,
/// ver ``RemoteCoordinateMapper``). NUNCA se persiste en este cliente
/// (`docs/control-remoto.md` §2, "mínima retención") — vive solo en memoria
/// de ``RemotoViewModel`` mientras la sesión está en pantalla.
public struct RemoteFrame: Codable, Sendable, Equatable {
    public let imageB64: String
    public let width: Int
    public let height: Int
    /// Copia de `RemoteSession.framesCount` al momento de este frame.
    public let seq: Int

    enum CodingKeys: String, CodingKey {
        case width, height, seq
        case imageB64 = "image_b64"
    }

    public init(imageB64: String, width: Int, height: Int, seq: Int) {
        self.imageB64 = imageB64
        self.width = width
        self.height = height
        self.seq = seq
    }
}

// MARK: - Input remoto (WP-V4-10) — mismo vocabulario EXACTO que
// `edecan_api.routers.remote.PointerAccion`/`MouseButton`/`SpecialKey` y
// `edecan_companion.actions._POINTER_ACTIONS`/`_MOUSE_BUTTONS`/`_SPECIAL_KEYS`
// (verificado contra el código fuente de ambos).

public enum RemotePointerAccion: String, Codable, Sendable, Equatable {
    case move, click
    case doubleClick = "double_click"
    case rightClick = "right_click"
}

public enum RemoteMouseButton: String, Codable, Sendable, Equatable {
    case left, right, middle
}

/// Las 8 teclas especiales EXACTAS que soporta
/// `edecan_companion.actions._input_key` (`_SPECIAL_KEYS`) — cualquier otro
/// valor lo rechaza el backend con `422`.
public enum RemoteSpecialKey: String, Codable, Sendable, Equatable, CaseIterable {
    case enter, tab, escape, backspace
    case arrowUp = "arrow_up"
    case arrowDown = "arrow_down"
    case arrowLeft = "arrow_left"
    case arrowRight = "arrow_right"

    /// Etiqueta corta en español para el teclado accesorio de ``RemotoView``.
    public var etiqueta: String {
        switch self {
        case .enter: return "Enter"
        case .tab: return "Tab"
        case .escape: return "Esc"
        case .backspace: return "⌫"
        case .arrowUp: return "↑"
        case .arrowDown: return "↓"
        case .arrowLeft: return "←"
        case .arrowRight: return "→"
        }
    }
}

/// `POST /v1/remote/sessions/{id}/input {tipo: "pointer", ...}` — espejo
/// EXACTO de `edecan_api.routers.remote.PointerInputIn`. `button` se omite
/// del JSON si es `nil` (Codable sintetizado usa `encodeIfPresent` para
/// propiedades opcionales) — el companion ya trata "sin `button`" como
/// `"left"` por defecto (`edecan_companion.actions._input_pointer`), así que
/// omitir la clave y mandar `null` explícito son equivalentes.
public struct RemotePointerInput: Encodable, Sendable, Equatable {
    public let tipo = "pointer"
    public let x: Int
    public let y: Int
    public let accion: RemotePointerAccion
    public let button: RemoteMouseButton?

    public init(x: Int, y: Int, accion: RemotePointerAccion, button: RemoteMouseButton? = nil) {
        self.x = x
        self.y = y
        self.accion = accion
        self.button = button
    }
}

/// `POST /v1/remote/sessions/{id}/input {tipo: "key", ...}` — espejo EXACTO
/// de `edecan_api.routers.remote.KeyInputIn`: exactamente uno de
/// `texto`/`tecla`, nunca ambos ni ninguno. El `init` privado + los dos
/// constructores estáticos hacen ese estado inválido irrepresentable desde
/// fuera de este archivo (a diferencia del backend, que lo valida en
/// runtime con un `model_validator`).
public struct RemoteKeyInput: Encodable, Sendable, Equatable {
    public let tipo = "key"
    public let texto: String?
    public let tecla: RemoteSpecialKey?

    public static func texto(_ texto: String) -> RemoteKeyInput {
        RemoteKeyInput(texto: texto, tecla: nil)
    }

    public static func tecla(_ tecla: RemoteSpecialKey) -> RemoteKeyInput {
        RemoteKeyInput(texto: nil, tecla: tecla)
    }

    private init(texto: String?, tecla: RemoteSpecialKey?) {
        self.texto = texto
        self.tecla = tecla
    }
}

/// Cuerpo de `POST /v1/remote/sessions/{id}/input` — unión discriminada por
/// `tipo`, igual que `SessionInputIn` en el backend
/// (`Annotated[PointerInputIn | KeyInputIn, Field(discriminator="tipo")]`).
/// Solo `Encodable` (nunca se decodifica, es exclusivamente un cuerpo de
/// petición): reenvía a `encode(to:)` del caso concreto sobre el MISMO
/// `Encoder`, así el JSON queda IDÉNTICO al de codificar
/// `RemotePointerInput`/`RemoteKeyInput` sueltos — sin ningún nivel de
/// anidación extra.
public enum RemoteInput: Encodable, Sendable, Equatable {
    case pointer(RemotePointerInput)
    case key(RemoteKeyInput)

    public func encode(to encoder: Encoder) throws {
        switch self {
        case .pointer(let input): try input.encode(to: encoder)
        case .key(let input): try input.encode(to: encoder)
        }
    }
}

/// `POST /v1/remote/sessions/{id}/input` — `{"ok": true, "result": {...}|null}`
/// (`edecan_api.routers.remote.send_input`, último `return`). `result` es de
/// forma libre según la acción (`{x,y,accion,button}` para pointer,
/// `{tipo,length}`/`{tipo,tecla}` para key) — se reutiliza ``JSONValue``, el
/// mismo tipo genérico que ya usa el resto de `EdecanKit` para JSON de forma
/// libre (`ChatEvent`, `MissionStepOut.usage`).
public struct RemoteInputResult: Decodable, Sendable, Equatable {
    public let ok: Bool
    public let result: [String: JSONValue]?
}

// MARK: - Mapeo de coordenadas (réplica de
// `apps/web/src/components/remoto/coords.ts`)

/// Mapea un gesto sobre la `Image` del visor (``RemotoView`` la muestra con
/// `.aspectRatio(contentMode: .fit)`, así que puede tener franjas vacías tipo
/// "letterbox" si la proporción del frame no coincide con la del elemento) a
/// coordenadas REALES del frame remoto (`RemoteFrame.width`/`height`) — mismo
/// algoritmo que `apps/web/src/components/remoto/coords.ts`
/// (`containedImageRect`/`mapClientPointToRemoteCoords`), adaptado a SwiftUI:
/// un gesto atado directo a la `Image` ya reporta su `location` en el
/// espacio LOCAL de ese view (equivalente a `clientX - elementRect.left` en
/// el DOM), así que no hace falta restar ningún origen aparte — por eso las
/// funciones de abajo reciben `Double` sueltos en vez de `CGPoint`/`CGRect`
/// (mantiene `EdecanKit` sin importar `CoreGraphics`/`UIKit`, ver el
/// docstring de este paquete en `Package.swift`).
public enum RemoteCoordinateMapper {
    public struct Rect: Equatable, Sendable {
        public let left: Double
        public let top: Double
        public let width: Double
        public let height: Double

        public init(left: Double, top: Double, width: Double, height: Double) {
            self.left = left
            self.top = top
            self.width = width
            self.height = height
        }
    }

    /// El rectángulo que de verdad ocupa la imagen dentro de su elemento con
    /// `.aspectRatio(contentMode: .fit)` — puede ser más chico que el
    /// elemento si las proporciones no coinciden (de ahí las franjas vacías
    /// arriba/abajo o a los lados).
    public static func rectanguloContenido(
        anchoElemento: Double, altoElemento: Double, anchoNatural: Double, altoNatural: Double
    ) -> Rect {
        guard anchoElemento > 0, altoElemento > 0, anchoNatural > 0, altoNatural > 0 else {
            return Rect(left: 0, top: 0, width: anchoElemento, height: altoElemento)
        }

        let aspectoElemento = anchoElemento / altoElemento
        let aspectoNatural = anchoNatural / altoNatural

        if aspectoNatural > aspectoElemento {
            // La imagen llena el ancho completo; franjas vacías arriba/abajo.
            let width = anchoElemento
            let height = width / aspectoNatural
            return Rect(left: 0, top: (altoElemento - height) / 2, width: width, height: height)
        }

        // La imagen llena el alto completo; franjas vacías a los lados.
        let height = altoElemento
        let width = height * aspectoNatural
        return Rect(left: (anchoElemento - width) / 2, top: 0, width: width, height: height)
    }

    /// Punto de gesto (en coordenadas LOCALES del elemento que muestra la
    /// imagen, p. ej. `value.location` de un `SpatialTapGesture` atado
    /// directo a la `Image`) → coordenadas enteras `[0, frame.width) x
    /// [0, frame.height)`, o `nil` si cayó en la franja vacía del letterbox
    /// — el llamador (``RemotoView``) debe ignorarlo, nunca mandar un
    /// `input_pointer` con coordenadas inventadas (mismo contrato que la
    /// versión web).
    public static func mapear(
        puntoLocalX: Double, puntoLocalY: Double,
        anchoElemento: Double, altoElemento: Double,
        frame: RemoteFrame
    ) -> (x: Int, y: Int)? {
        guard frame.width > 0, frame.height > 0 else { return nil }

        let contenido = rectanguloContenido(
            anchoElemento: anchoElemento, altoElemento: altoElemento,
            anchoNatural: Double(frame.width), altoNatural: Double(frame.height)
        )
        guard contenido.width > 0, contenido.height > 0 else { return nil }

        let relX = puntoLocalX - contenido.left
        let relY = puntoLocalY - contenido.top
        guard relX >= 0, relY >= 0, relX <= contenido.width, relY <= contenido.height else {
            return nil // cayó en la franja vacía del letterbox, no en la imagen real
        }

        let x = Int((relX / contenido.width * Double(frame.width)).rounded())
        let y = Int((relY / contenido.height * Double(frame.height)).rounded())
        return (
            min(max(x, 0), frame.width - 1),
            min(max(y, 0), frame.height - 1)
        )
    }
}
