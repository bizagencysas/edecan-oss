import Foundation

// MARK: - `GET /v1/negocios/kpis` (pantalla 4 del mockup, `docs/negocios.md`,
// `packages/business/edecan_business/kpis.py::kpis_mes`)

/// Un canal (`transactions.cuenta`) de la dona de ventas — top 6 + `"otros"`
/// (`kpis.py::_top_n_mas_otros`).
public struct CanalKPI: Codable, Sendable, Equatable, Identifiable {
    public let canal: String
    public let total: Double

    public var id: String { canal }
}

/// Una fila de la actividad reciente del mes (`kpis.py::_actividad_reciente`):
/// mezcla facturas y transacciones, ya ordenadas por fecha descendente.
/// `status` solo viene presente para `tipo == "factura"` — `nil` para
/// `"transaccion"`.
public struct ActividadKPI: Codable, Sendable, Equatable, Identifiable {
    public let tipo: String
    public let id: String
    public let fecha: String
    public let descripcion: String
    public let monto: Double
    public let moneda: String
    public let status: String?
}

/// `GET /v1/negocios/kpis?mes=YYYY-MM` — KPIs de negocio del mes
/// (`edecan_business.kpis.kpis_mes`). `ingresos`/`gastos`/`beneficio` salen
/// ÚNICAMENTE de `transactions` (nunca de facturas — ver el docstring de
/// `kpis_mes` para el criterio anti-doble-conteo); `facturado`/`cobrado` son
/// la cartera de facturas, informativa y separada.
public struct NegocioKPIs: Codable, Sendable, Equatable {
    public let mes: String
    public let ingresos: Double
    public let gastos: Double
    public let beneficio: Double
    public let nuevosClientes: Int
    public let facturado: Double
    public let cobrado: Double
    public let porCanal: [CanalKPI]
    public let actividad: [ActividadKPI]

    enum CodingKeys: String, CodingKey {
        case mes, ingresos, gastos, beneficio, facturado, cobrado
        case nuevosClientes = "nuevos_clientes"
        case porCanal = "por_canal"
        case actividad
    }
}

// MARK: - `/v1/negocios/facturas` (`edecan_business.invoices`)

/// Una línea de una factura (`invoice_items`) — solo viene anidada dentro de
/// ``Factura/items`` cuando la factura se pide por `id`
/// (`GET /v1/negocios/facturas/{id}`); `GET /v1/negocios/facturas` (lista) no
/// las incluye, así que ``Factura/items`` es opcional.
public struct FacturaItem: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let descripcion: String
    public let cantidad: Double
    public let precioUnitario: Double
    public let total: Double

    enum CodingKeys: String, CodingKey {
        case id, descripcion, cantidad, total
        case precioUnitario = "precio_unitario"
    }
}

/// `draft → sent → paid`; `void` desde cualquier estado no-`void`
/// (`edecan_business.invoices._TRANSICIONES_VALIDAS`). `String` crudo (no un
/// enum cerrado) para no romper la decodificación si el backend agrega un
/// estado nuevo antes que este cliente lo conozca — mismo criterio que
/// `Conversation.channel`.
public struct Factura: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let numero: String
    public let clienteNombre: String
    public let clienteEmail: String?
    public let moneda: String
    public let subtotal: Double
    public let impuestos: Double
    public let total: Double
    public let status: String
    public let dueDate: String?
    public let notas: String?
    public let createdAt: Date
    public let items: [FacturaItem]?

    enum CodingKeys: String, CodingKey {
        case id, numero, moneda, subtotal, impuestos, total, status, notas, items
        case clienteNombre = "cliente_nombre"
        case clienteEmail = "cliente_email"
        case dueDate = "due_date"
        case createdAt = "created_at"
    }
}
