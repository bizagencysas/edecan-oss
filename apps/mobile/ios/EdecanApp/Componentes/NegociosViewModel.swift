import Foundation
import Observation
import EdecanKit

/// Estado y carga de datos de ``NegociosView`` — `GET /v1/negocios/kpis` +
/// `GET /v1/negocios/facturas` en paralelo (`docs/negocios.md`,
/// `ROADMAP_V2.md` §7.4/§7.7 WP-V2-12).
@MainActor
@Observable
final class NegociosViewModel {
    private(set) var kpis: NegocioKPIs?
    private(set) var facturas: [Factura] = []
    private(set) var cargando = false
    var errorMensaje: String?

    /// `true` la primera vez que se pide cargar y ninguna de las dos
    /// llamadas terminó todavía — distingue "cargando de cero" (muestra un
    /// spinner de pantalla completa) de "refrescando con datos ya en
    /// pantalla" (`.refreshable`, no debe tapar lo que ya se ve).
    var sinDatosTodavia: Bool { kpis == nil && facturas.isEmpty }

    func cargar(client: APIClient?) async {
        guard let client else {
            errorMensaje = "No hay sesión activa."
            return
        }
        cargando = true
        errorMensaje = nil
        defer { cargando = false }

        async let kpisTarea = client.negociosKPIs()
        async let facturasTarea = client.listarFacturas()
        do {
            let (kpisResultado, facturasResultado) = try await (kpisTarea, facturasTarea)
            kpis = kpisResultado
            facturas = facturasResultado
        } catch {
            errorMensaje = error.localizedDescription
        }
    }
}
