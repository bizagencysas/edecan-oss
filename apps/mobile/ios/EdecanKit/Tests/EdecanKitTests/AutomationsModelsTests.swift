import Testing
import Foundation
@testable import EdecanKit

/// Decodifica el mismo shape que devuelve
/// `automations.py::_public_automation` de verdad (sin `BaseModel` de
/// Pydantic pinned — el router arma un `dict` a mano, verificado contra el
/// código fuente) — mismo criterio que `ModelsTests`.
struct AutomationsModelsTests {
    @Test func decodificaAutomatizacionDeAgendaActiva() throws {
        let json = """
        {
          "id": "a1", "nombre": "Reporte de ventas diario", "descripcion": "",
          "trigger": {"kind": "schedule", "rrule": "FREQ=DAILY;BYHOUR=9;BYMINUTE=0"},
          "accion": {"kind": "agent_instruction", "instruccion": "Resume las ventas de hoy", "agente": null},
          "enabled": true, "next_run_at": "2026-07-09T09:00:00Z", "last_run_at": null,
          "created_at": "2026-07-08T10:00:00Z", "updated_at": "2026-07-08T10:00:00Z"
        }
        """
        let automatizacion = try APIClient.crearDecoder().decode(AutomationOut.self, from: Data(json.utf8))
        #expect(automatizacion.nombre == "Reporte de ventas diario")
        #expect(automatizacion.enabled == true)
        #expect(automatizacion.hookSecret == nil)
        #expect(automatizacion.lastRunAt == nil)
        guard case .schedule(let rrule) = automatizacion.trigger else {
            Issue.record("se esperaba trigger .schedule")
            return
        }
        #expect(rrule == "FREQ=DAILY;BYHOUR=9;BYMINUTE=0")
        #expect(automatizacion.accion.instruccion == "Resume las ventas de hoy")
        #expect(automatizacion.accion.agente == nil)
    }

    @Test func decodificaAutomatizacionWebhookRedactada() throws {
        // `GET`/`list` nunca traen `hook_secret` propio — solo la forma
        // redactada `{"kind": "webhook", "has_secret": true, "hook_url": ...}`
        // (ver docstring de `automations.py::_public_automation`).
        let json = """
        {
          "id": "a2", "nombre": "Disparador entrante", "descripcion": "Desde un CRM externo",
          "trigger": {"kind": "webhook", "has_secret": true, "hook_url": "https://api.edecan.local/v1/hooks/a2"},
          "accion": {"kind": "agent_instruction", "instruccion": "Registra el lead", "agente": "operar"},
          "enabled": false, "next_run_at": null, "last_run_at": "2026-07-07T08:00:00Z",
          "created_at": "2026-07-01T10:00:00Z", "updated_at": "2026-07-07T08:00:00Z"
        }
        """
        let automatizacion = try APIClient.crearDecoder().decode(AutomationOut.self, from: Data(json.utf8))
        #expect(automatizacion.enabled == false)
        guard case .webhook(let hasSecret, let hookUrl) = automatizacion.trigger else {
            Issue.record("se esperaba trigger .webhook")
            return
        }
        #expect(hasSecret == true)
        #expect(hookUrl == "https://api.edecan.local/v1/hooks/a2")
        #expect(automatizacion.accion.agente == "operar")
    }

    @Test func decodificaHookSecretSoloEnLaRespuestaQueLoGenera() throws {
        let json = """
        {
          "id": "a3", "nombre": "n", "descripcion": "",
          "trigger": {"kind": "webhook", "has_secret": true, "hook_url": "https://x/y"},
          "accion": {"kind": "agent_instruction", "instruccion": "i", "agente": null},
          "enabled": true, "next_run_at": null, "last_run_at": null,
          "created_at": "2026-07-08T10:00:00Z", "updated_at": "2026-07-08T10:00:00Z",
          "hook_secret": "un-secreto-de-un-solo-uso"
        }
        """
        let automatizacion = try APIClient.crearDecoder().decode(AutomationOut.self, from: Data(json.utf8))
        #expect(automatizacion.hookSecret == "un-secreto-de-un-solo-uso")
    }

    @Test func triggerDeKindDesconocidoLanzaError() {
        #expect(throws: (any Error).self) {
            _ = try APIClient.crearDecoder().decode(
                AutomationTrigger.self, from: Data(#"{"kind": "algo_nuevo"}"#.utf8)
            )
        }
    }

    @Test func decodificaListaDeAutomatizaciones() throws {
        let json = """
        [{"id": "a1", "nombre": "n", "descripcion": "",
          "trigger": {"kind": "schedule", "rrule": "FREQ=WEEKLY;BYDAY=MO"},
          "accion": {"kind": "agent_instruction", "instruccion": "i", "agente": null},
          "enabled": true, "next_run_at": null, "last_run_at": null,
          "created_at": "2026-07-08T10:00:00Z", "updated_at": "2026-07-08T10:00:00Z"}]
        """
        let automatizaciones = try APIClient.crearDecoder().decode([AutomationOut].self, from: Data(json.utf8))
        #expect(automatizaciones.count == 1)
    }

    @Test func decodificaCorridaConDetalleDeResultado() throws {
        let json = """
        {"id": "r1", "status": "done", "detalle": {"resultado": "Resumen enviado"},
         "started_at": "2026-07-08T09:00:00Z", "finished_at": "2026-07-08T09:00:05Z"}
        """
        let corrida = try APIClient.crearDecoder().decode(AutomationRunOut.self, from: Data(json.utf8))
        #expect(corrida.status == "done")
        #expect(corrida.detalle["resultado"] == .string("Resumen enviado"))
        #expect(corrida.finishedAt != nil)
    }

    @Test func decodificaCorridaSinTerminar() throws {
        let json = """
        {"id": "r2", "status": "running", "detalle": {}, "started_at": "2026-07-08T09:00:00Z", "finished_at": null}
        """
        let corrida = try APIClient.crearDecoder().decode(AutomationRunOut.self, from: Data(json.utf8))
        #expect(corrida.finishedAt == nil)
        #expect(corrida.detalle.isEmpty)
    }
}
