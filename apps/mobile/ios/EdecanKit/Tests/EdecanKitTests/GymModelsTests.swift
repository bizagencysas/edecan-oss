import Foundation
import Testing
@testable import EdecanKit

/// Round-trip Codable de los modelos del contrato `/v1/gym`: claves
/// snake_case exactas y campos opcionales ausentes que caen a un default
/// seguro (nunca fabricar éxito).
struct GymModelsTests {
    @Test func decodificaSesionCompletaConPlanSeriesYProgreso() throws {
        let json = """
        {
          "id": "gym-s1",
          "estado": "active",
          "plan": {
            "titulo": "Empuje", "objetivo": "Fuerza", "duracion_min": 45, "imagen_url": null,
            "ejercicios": [
              {"nombre": "Press banca", "musculo": "Pecho", "series": 4,
               "repeticiones": "8-10", "descanso_seg": 90, "notas": "Controla la bajada"}
            ]
          },
          "started_at": "2026-08-16T10:00:00Z",
          "series": [
            {"ejercicio_idx": 0, "repeticiones": 10, "peso_kg": 60.5, "en": "2026-08-16T10:05:00Z"}
          ],
          "progreso": {"ejercicios": [{"idx": 0, "series_hechas": 1, "series_total": 4}]}
        }
        """
        let session = try APIClient.crearDecoder().decode(GymSession.self, from: Data(json.utf8))
        #expect(session.id == "gym-s1")
        #expect(session.status == "active")
        #expect(session.startedAt == "2026-08-16T10:00:00Z")
        #expect(session.plan.title == "Empuje")
        #expect(session.plan.objective == "Fuerza")
        #expect(session.plan.durationMinutes == 45)
        #expect(session.plan.imageURL == nil)
        #expect(session.plan.exercises.count == 1)
        #expect(session.plan.exercises[0].name == "Press banca")
        #expect(session.plan.exercises[0].muscle == "Pecho")
        #expect(session.plan.exercises[0].sets == 4)
        #expect(session.plan.exercises[0].repetitions == "8-10")
        #expect(session.plan.exercises[0].restSeconds == 90)
        #expect(session.plan.exercises[0].notes == "Controla la bajada")
        #expect(session.series.count == 1)
        #expect(session.series[0].exerciseIndex == 0)
        #expect(session.series[0].repetitions == 10)
        #expect(session.series[0].weightKg == 60.5)
        #expect(session.series[0].en == "2026-08-16T10:05:00Z")
        #expect(session.progress.exercises.count == 1)
        #expect(session.progress.exercises[0].index == 0)
        #expect(session.progress.exercises[0].setsDone == 1)
        #expect(session.progress.exercises[0].setsTotal == 4)
    }

    @Test func camposOpcionalesAusentesDecodificanANil() throws {
        // `imagen_url` ausente, `peso_kg` ausente y `started_at` ausente.
        let json = """
        {
          "id": "gym-s2",
          "estado": "paused",
          "plan": {
            "titulo": "Tirón", "objetivo": "Hipertrofia", "duracion_min": 30,
            "ejercicios": [{"nombre": "Dominadas", "musculo": "Espalda", "series": 3,
                            "repeticiones": "6-8", "descanso_seg": 120, "notas": ""}]
          },
          "series": [{"ejercicio_idx": 0, "repeticiones": 8, "en": "2026-08-16T10:05:00Z"}],
          "progreso": {"ejercicios": [{"idx": 0, "series_hechas": 0, "series_total": 3}]}
        }
        """
        let session = try APIClient.crearDecoder().decode(GymSession.self, from: Data(json.utf8))
        #expect(session.plan.imageURL == nil)
        #expect(session.startedAt == nil)
        #expect(session.series[0].weightKg == nil)
    }

    @Test func decodificaCheckinAceptadoConPlanYSesion() throws {
        let json = """
        {
          "ok": true,
          "plan": {"titulo": "Pierna", "objetivo": "Fuerza", "duracion_min": 50, "imagen_url": null,
                   "ejercicios": []},
          "session": {
            "id": "gym-s1", "estado": "active",
            "plan": {"titulo": "Pierna", "objetivo": "Fuerza", "duracion_min": 50, "imagen_url": null,
                     "ejercicios": []},
            "started_at": null,
            "series": [],
            "progreso": {"ejercicios": []}
          },
          "mensaje": "¡A entrenar!"
        }
        """
        let out = try APIClient.crearDecoder().decode(GymCheckinOut.self, from: Data(json.utf8))
        #expect(out.ok == true)
        #expect(out.message == "¡A entrenar!")
        #expect(out.plan?.title == "Pierna")
        #expect(out.session?.id == "gym-s1")
    }

    @Test func decodificaCheckinRechazadoSinPlanNiSesion() throws {
        let json = #"{"ok": false, "plan": null, "session": null, "mensaje": "Hoy no toca"}"#
        let out = try APIClient.crearDecoder().decode(GymCheckinOut.self, from: Data(json.utf8))
        #expect(out.ok == false)
        #expect(out.plan == nil)
        #expect(out.session == nil)
        #expect(out.message == "Hoy no toca")
    }

    @Test func checkinSinCampoOkNoFabricaExito() throws {
        let json = #"{"plan": null, "session": null, "mensaje": "sin señal"}"#
        let out = try APIClient.crearDecoder().decode(GymCheckinOut.self, from: Data(json.utf8))
        #expect(out.ok == false)
    }

    @Test func codificaCheckinInConClaveRespuesta() throws {
        let object = try #require(
            JSONSerialization.jsonObject(with: JSONEncoder().encode(GymCheckinIn(answer: "si")))
                as? [String: Any]
        )
        #expect(object["respuesta"] as? String == "si")
        #expect(object["answer"] == nil)
    }

    @Test func codificaSetLogInConClavesSnakeYPesoNulo() throws {
        let sinPeso = try #require(
            JSONSerialization.jsonObject(
                with: JSONEncoder().encode(GymSetLogIn(exerciseIndex: 2, repetitions: 12))
            ) as? [String: Any]
        )
        #expect(sinPeso["ejercicio_idx"] as? Int == 2)
        #expect(sinPeso["repeticiones"] as? Int == 12)
        // `peso_kg` viaja SIEMPRE (como NSNull), nunca se omite.
        #expect(sinPeso["peso_kg"] is NSNull)
        #expect(sinPeso["exerciseIndex"] == nil)

        let conPeso = try #require(
            JSONSerialization.jsonObject(
                with: JSONEncoder().encode(GymSetLogIn(exerciseIndex: 1, repetitions: 5, weightKg: 82.5))
            ) as? [String: Any]
        )
        #expect(conPeso["peso_kg"] as? Double == 82.5)
    }

    @Test func roundTripDeSesionConservaClavesSnakeCase() throws {
        let json = """
        {
          "id": "gym-s1", "estado": "active",
          "plan": {"titulo": "Empuje", "objetivo": "Fuerza", "duracion_min": 45, "imagen_url": null,
                   "ejercicios": [{"nombre": "Press banca", "musculo": "Pecho", "series": 4,
                                   "repeticiones": "8-10", "descanso_seg": 90, "notas": ""}]},
          "started_at": "2026-08-16T10:00:00Z",
          "series": [{"ejercicio_idx": 0, "repeticiones": 10, "peso_kg": 75.0, "en": "x"}],
          "progreso": {"ejercicios": [{"idx": 0, "series_hechas": 1, "series_total": 4}]}
        }
        """
        let decoder = APIClient.crearDecoder()
        let session = try decoder.decode(GymSession.self, from: Data(json.utf8))
        let object = try #require(
            JSONSerialization.jsonObject(with: JSONEncoder().encode(session)) as? [String: Any]
        )

        // Top-level: claves snake_case, nunca camelCase.
        #expect(object["estado"] as? String == "active")
        #expect(object["started_at"] as? String == "2026-08-16T10:00:00Z")
        #expect(object["status"] == nil)
        #expect(object["startedAt"] == nil)

        let plan = try #require(object["plan"] as? [String: Any])
        #expect(plan["titulo"] as? String == "Empuje")
        #expect(plan["duracion_min"] as? Int == 45)
        #expect(plan["title"] == nil)

        let ejercicio = try #require((plan["ejercicios"] as? [[String: Any]])?.first)
        #expect(ejercicio["descanso_seg"] as? Int == 90)
        #expect(ejercicio["restSeconds"] == nil)

        let serie = try #require((object["series"] as? [[String: Any]])?.first)
        #expect(serie["ejercicio_idx"] as? Int == 0)
        #expect(serie["peso_kg"] as? Double == 75.0)
        #expect(serie["exerciseIndex"] == nil)

        let progreso = try #require(object["progreso"] as? [String: Any])
        let progresoEjercicio = try #require((progreso["ejercicios"] as? [[String: Any]])?.first)
        #expect(progresoEjercicio["series_hechas"] as? Int == 1)
        #expect(progresoEjercicio["series_total"] as? Int == 4)
        #expect(progresoEjercicio["setsDone"] == nil)
    }

    @Test func decodificaImagenFileIdEnPlan() throws {
        let json = """
        {
          "titulo": "Empuje", "objetivo": "Fuerza", "duracion_min": 45,
          "imagen_url": "https://cdn.test/gym.png",
          "imagen_file_id": "file-collage-001",
          "ejercicios": []
        }
        """
        let plan = try APIClient.crearDecoder().decode(GymPlan.self, from: Data(json.utf8))
        #expect(plan.imageFileID == "file-collage-001")
        #expect(plan.imageURL == "https://cdn.test/gym.png")
    }
}