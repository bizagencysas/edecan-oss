import Testing
import Foundation
@testable import EdecanKit

/// Decodifica los mismos shapes que `packages/business/edecan_business/kpis.py::kpis_mes`
/// y `edecan_business/invoices.py` devuelven de verdad — si esos módulos
/// cambian de forma sin actualizar este cliente, uno de estos tests debería
/// fallar antes de que lo note un usuario real (mismo criterio que
/// `ChatEventTests`).
struct NegociosModelsTests {
    @Test func decodificaKPIsCompletos() throws {
        let json = """
        {
          "mes": "2026-07",
          "ingresos": 8500000.0,
          "gastos": 3120450.0,
          "beneficio": 5379550.0,
          "nuevos_clientes": 3,
          "facturado": 500.0,
          "cobrado": 300.0,
          "por_canal": [
            {"canal": "tarjeta-empresa", "total": 6000000.0},
            {"canal": "otros", "total": 2500000.0}
          ],
          "actividad": [
            {
              "tipo": "factura", "id": "f001", "fecha": "2026-07-08T10:00:00Z",
              "descripcion": "Factura F-2026-0001 — Acme", "monto": 500.0,
              "moneda": "USD", "status": "sent"
            },
            {
              "tipo": "transaccion", "id": "tx01", "fecha": "2026-07-05",
              "descripcion": "Suscripción anual", "monto": 152300.0,
              "moneda": "COP", "status": null
            }
          ]
        }
        """
        let kpis = try JSONDecoder().decode(NegocioKPIs.self, from: Data(json.utf8))
        #expect(kpis.mes == "2026-07")
        #expect(kpis.ingresos == 8500000.0)
        #expect(kpis.nuevosClientes == 3)
        #expect(kpis.porCanal.count == 2)
        #expect(kpis.porCanal[0].canal == "tarjeta-empresa")
        #expect(kpis.actividad.count == 2)
        #expect(kpis.actividad[0].tipo == "factura")
        #expect(kpis.actividad[0].status == "sent")
        #expect(kpis.actividad[1].status == nil)
    }

    @Test func decodificaKPIsSinActividadNiCanales() throws {
        let json = """
        {"mes": "2026-07", "ingresos": 0, "gastos": 0, "beneficio": 0, "nuevos_clientes": 0,
         "facturado": 0, "cobrado": 0, "por_canal": [], "actividad": []}
        """
        let kpis = try JSONDecoder().decode(NegocioKPIs.self, from: Data(json.utf8))
        #expect(kpis.porCanal.isEmpty)
        #expect(kpis.actividad.isEmpty)
    }

    @Test func decodificaFacturaDeListaSinItems() throws {
        // `GET /v1/negocios/facturas` (lista) no anida `items` — ver
        // `edecan_business.invoices.listar_facturas`.
        let json = """
        {
          "id": "inv01", "numero": "F-2026-0001", "cliente_nombre": "Acme",
          "cliente_email": null, "moneda": "USD", "subtotal": 100.0,
          "impuestos": 0.0, "total": 100.0, "status": "draft", "due_date": null,
          "notas": "", "created_at": "2026-07-08T10:00:00Z"
        }
        """
        let factura = try APIClient.crearDecoder().decode(Factura.self, from: Data(json.utf8))
        #expect(factura.numero == "F-2026-0001")
        #expect(factura.status == "draft")
        #expect(factura.items == nil)
    }

    @Test func decodificaFacturaDeDetalleConItems() throws {
        // `GET /v1/negocios/facturas/{id}` — ver `edecan_business.invoices.obtener_factura`.
        let json = """
        {
          "id": "inv01", "numero": "F-2026-0001", "cliente_nombre": "Acme",
          "cliente_email": "ana@acme.com", "moneda": "USD", "subtotal": 100.0,
          "impuestos": 19.0, "total": 119.0, "status": "sent", "due_date": "2026-08-01",
          "notas": "Neto 30", "created_at": "2026-07-08T10:00:00Z",
          "items": [
            {"id": "it01", "descripcion": "Consultoría", "cantidad": 2.0, "precio_unitario": 50.0, "total": 100.0}
          ]
        }
        """
        let factura = try APIClient.crearDecoder().decode(Factura.self, from: Data(json.utf8))
        #expect(factura.items?.count == 1)
        #expect(factura.items?.first?.descripcion == "Consultoría")
        #expect(factura.clienteEmail == "ana@acme.com")
        #expect(factura.dueDate == "2026-08-01")
    }

    @Test func decodificaListaDeFacturas() throws {
        let json = """
        [
          {"id": "inv01", "numero": "F-2026-0001", "cliente_nombre": "Acme", "cliente_email": null,
           "moneda": "USD", "subtotal": 100.0, "impuestos": 0.0, "total": 100.0, "status": "paid",
           "due_date": null, "notas": "", "created_at": "2026-07-08T10:00:00Z"}
        ]
        """
        let facturas = try APIClient.crearDecoder().decode([Factura].self, from: Data(json.utf8))
        #expect(facturas.count == 1)
        #expect(facturas[0].status == "paid")
    }
}
