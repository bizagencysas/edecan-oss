import Testing
import Foundation
@testable import EdecanKit

/// Decodifica el mismo shape que devuelve
/// `apps/api/edecan_api/routers/missions.py` de verdad (`MissionOut`/
/// `MissionStepOut`/`MissionDetailOut` de `edecan_schemas.missions`,
/// verificado contra el código fuente) — mismo criterio que `ModelsTests`.
struct MissionsModelsTests {
    @Test func decodificaMisionRecienCreada() throws {
        let json = """
        {
          "id": "m1", "tenant_id": "t1", "user_id": "u1",
          "objetivo": "Investiga a mis 3 competidores", "status": "planning",
          "plan": null, "resultado": null, "presupuesto": {"max_steps": 8},
          "error": null, "created_at": "2026-07-08T10:00:00Z", "updated_at": "2026-07-08T10:00:00Z"
        }
        """
        let mission = try APIClient.crearDecoder().decode(MissionOut.self, from: Data(json.utf8))
        #expect(mission.status == "planning")
        #expect(mission.plan == nil)
        #expect(mission.resultado == nil)
        #expect(mission.maxSteps == 8)
        #expect(mission.estaActiva == true)
        #expect(mission.esTerminal == false)
    }

    @Test func decodificaMisionEnCursoConPlan() throws {
        let json = """
        {
          "id": "m1", "tenant_id": "t1", "user_id": "u1",
          "objetivo": "Resume las ventas de hoy", "status": "running",
          "plan": [
            {"seq": 1, "agente": "research", "instruccion": "Busca datos de ventas"},
            {"seq": 2, "agente": "analysis", "instruccion": "Resume los datos"}
          ],
          "resultado": null, "presupuesto": {"max_steps": 8}, "error": null,
          "created_at": "2026-07-08T10:00:00Z", "updated_at": "2026-07-08T10:05:00Z"
        }
        """
        let mission = try APIClient.crearDecoder().decode(MissionOut.self, from: Data(json.utf8))
        #expect(mission.plan?.count == 2)
        #expect(mission.plan?[0].agente == "research")
        #expect(mission.plan?[1].instruccion == "Resume los datos")
        #expect(mission.estaActiva == true)
    }

    @Test func decodificaMisionTerminadaConResultado() throws {
        let json = """
        {
          "id": "m1", "tenant_id": "t1", "user_id": "u1",
          "objetivo": "obj", "status": "done", "plan": [], "resultado": "Listo, 3 competidores resumidos.",
          "presupuesto": {"max_steps": 8}, "error": null,
          "created_at": "2026-07-08T10:00:00Z", "updated_at": "2026-07-08T10:10:00Z"
        }
        """
        let mission = try APIClient.crearDecoder().decode(MissionOut.self, from: Data(json.utf8))
        #expect(mission.status == "done")
        #expect(mission.resultado == "Listo, 3 competidores resumidos.")
        #expect(mission.estaActiva == false)
        #expect(mission.esTerminal == true)
    }

    @Test func decodificaMisionConError() throws {
        let json = """
        {
          "id": "m1", "tenant_id": "t1", "user_id": "u1",
          "objetivo": "obj", "status": "error", "plan": null, "resultado": null,
          "presupuesto": {"max_steps": 8}, "error": "El proveedor LLM no respondió a tiempo",
          "created_at": "2026-07-08T10:00:00Z", "updated_at": "2026-07-08T10:01:00Z"
        }
        """
        let mission = try APIClient.crearDecoder().decode(MissionOut.self, from: Data(json.utf8))
        #expect(mission.error == "El proveedor LLM no respondió a tiempo")
        #expect(mission.esTerminal == true)
    }

    @Test func decodificaListaDeMisiones() throws {
        let json = """
        [{"id": "m1", "tenant_id": "t1", "user_id": "u1", "objetivo": "a", "status": "cancelled",
          "plan": null, "resultado": null, "presupuesto": {}, "error": null,
          "created_at": "2026-07-08T10:00:00Z", "updated_at": "2026-07-08T10:00:00Z"}]
        """
        let misiones = try APIClient.crearDecoder().decode([MissionOut].self, from: Data(json.utf8))
        #expect(misiones.count == 1)
        #expect(misiones[0].maxSteps == nil)
        #expect(misiones[0].estaActiva == false)
    }

    @Test func decodificaPasoTerminadoConUsageDeTokens() throws {
        let json = """
        {
          "id": "s1", "tenant_id": "t1", "mission_id": "m1", "seq": 1, "agente": "research",
          "instruccion": "Busca datos", "status": "done", "resultado": "3 competidores encontrados",
          "usage": {"input_tokens": 812, "output_tokens": 143},
          "created_at": "2026-07-08T10:00:00Z", "updated_at": "2026-07-08T10:02:00Z"
        }
        """
        let paso = try APIClient.crearDecoder().decode(MissionStepOut.self, from: Data(json.utf8))
        #expect(paso.status == "done")
        #expect(paso.resultado == "3 competidores encontrados")
        #expect(paso.pendingToolCall == nil)
    }

    @Test func decodificaPasoEsperandoConfirmacionConToolPendiente() throws {
        let json = """
        {
          "id": "s2", "tenant_id": "t1", "mission_id": "m1", "seq": 2, "agente": "operar",
          "instruccion": "Envía el resumen por correo", "status": "waiting_confirmation", "resultado": null,
          "usage": {"pending_tool_call": {"id": "call_abc", "name": "enviar_correo", "args": {"para": "ana@acme.com"}}},
          "created_at": "2026-07-08T10:00:00Z", "updated_at": "2026-07-08T10:03:00Z"
        }
        """
        let paso = try APIClient.crearDecoder().decode(MissionStepOut.self, from: Data(json.utf8))
        #expect(paso.status == "waiting_confirmation")
        guard let pendiente = paso.pendingToolCall else {
            Issue.record("se esperaba pendingToolCall")
            return
        }
        #expect(pendiente.id == "call_abc")
        #expect(pendiente.name == "enviar_correo")
        #expect(pendiente.args["para"] == .string("ana@acme.com"))
    }

    @Test func decodificaPasoPendienteSinUsage() throws {
        let json = """
        {
          "id": "s3", "tenant_id": "t1", "mission_id": "m1", "seq": 3, "agente": "research",
          "instruccion": "pendiente", "status": "pending", "resultado": null, "usage": null,
          "created_at": "2026-07-08T10:00:00Z", "updated_at": "2026-07-08T10:00:00Z"
        }
        """
        let paso = try APIClient.crearDecoder().decode(MissionStepOut.self, from: Data(json.utf8))
        #expect(paso.usage == nil)
        #expect(paso.pendingToolCall == nil)
    }

    @Test func decodificaDetalleDeMisionConPasos() throws {
        let json = """
        {
          "mission": {
            "id": "m1", "tenant_id": "t1", "user_id": "u1", "objetivo": "obj", "status": "running",
            "plan": [{"seq": 1, "agente": "research", "instruccion": "x"}], "resultado": null,
            "presupuesto": {"max_steps": 8}, "error": null,
            "created_at": "2026-07-08T10:00:00Z", "updated_at": "2026-07-08T10:00:00Z"
          },
          "steps": [
            {"id": "s1", "tenant_id": "t1", "mission_id": "m1", "seq": 1, "agente": "research",
             "instruccion": "x", "status": "running", "resultado": null, "usage": null,
             "created_at": "2026-07-08T10:00:00Z", "updated_at": "2026-07-08T10:00:00Z"}
          ]
        }
        """
        let detalle = try APIClient.crearDecoder().decode(MissionDetailOut.self, from: Data(json.utf8))
        #expect(detalle.mission.id == "m1")
        #expect(detalle.steps.count == 1)
        #expect(detalle.steps[0].status == "running")
    }
}
