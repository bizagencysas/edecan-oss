import Foundation
import Testing
@testable import EdecanKit

@Suite("Content Studio")
struct ContentStudioModelsTests {
    @Test("decodifica borrador, partes e imagen privada")
    func decodeDraft() throws {
        let draft = try JSONDecoder().decode(
            SocialContentDraft.self,
            from: Data(
                """
                {
                  "status":"ready",
                  "platform":"x",
                  "copy":"Una idea",
                  "parts":["Una idea 1/2", "Otra parte 2/2"],
                  "alt_text":"Una mesa de trabajo.",
                  "offline_visual":true,
                  "artifacts":[
                    {"file_id":"md-1","filename":"post.md","mime":"text/markdown"},
                    {"file_id":"img-1","filename":"post.png","mime":"image/png"}
                  ],
                  "requires_human_confirmation":true
                }
                """.utf8
            )
        )

        #expect(draft.platform == .x)
        #expect(draft.parts.count == 2)
        #expect(draft.imageArtifact?.fileId == "img-1")
        #expect(draft.requiresHumanConfirmation)
    }

    @Test("codifica exactamente el contrato de creación")
    func encodeRequest() throws {
        let request = SocialContentRequest(
            platform: .linkedin,
            topic: "Una idea útil",
            objective: "Enseñar",
            tone: "Humano",
            withImage: false
        )
        let object = try #require(
            JSONSerialization.jsonObject(with: JSONEncoder().encode(request)) as? [String: Any]
        )

        #expect(object["platform"] as? String == "linkedin")
        #expect(object["topic"] as? String == "Una idea útil")
        #expect(object["with_image"] as? Bool == false)
        #expect(object["withImage"] == nil)
        #expect(SocialContentPlatform.x.characterLimit == 280)
        #expect(SocialContentPlatform.linkedin.characterLimit == 3_000)
    }

    @Test("decodifica proyectos, historial y artefactos privados de Studio")
    func decodeStudioWorkspace() throws {
        let response = try JSONDecoder().decode(
            StudioActionResponse.self,
            from: Data(
                """
                {
                  "status":"ready",
                  "action":"history",
                  "message":"Listo",
                  "result":{
                    "project":{
                      "id":"proj_1","name":"Café Norte","mode":"landing",
                      "brandName":"Norte","updatedAt":"2026-07-22T10:00:00Z"
                    },
                    "revisions":[
                      {"id":"rev_1","label":"Principal","width":1440,"height":1000,
                       "instruction":"Primera versión","createdAt":"2026-07-22T09:00:00Z"},
                      {"id":"rev_2","label":"Revisión","width":1440,"height":1000,
                       "instruction":"Más humana","createdAt":"2026-07-22T10:00:00Z"}
                    ]
                  },
                  "artifacts":[{"file_id":"png-1","filename":"preview.png","mime":"image/png"}],
                  "presentation":[]
                }
                """.utf8
            )
        )

        #expect(response.project?.id == "proj_1")
        #expect(response.project?.revisionCount == 2)
        #expect(response.revisions.last?.instruction == "Más humana")
        #expect(response.artifacts.first?.fileId == "png-1")
    }

    @Test("codifica la acción estable sin rutas ni secretos del motor")
    func encodeStudioAction() throws {
        let input = StudioActionRequest(
            action: "create",
            prompt: "Una landing humana",
            projectName: "Demo",
            mode: .landing,
            count: 3,
            quality: .max,
            files: ["file-1", "file-2"]
        )
        let object = try #require(
            JSONSerialization.jsonObject(with: JSONEncoder().encode(input)) as? [String: Any]
        )

        #expect(object["action"] as? String == "create")
        #expect(object["projectName"] as? String == "Demo")
        #expect(object["mode"] as? String == "landing")
        #expect(object["quality"] as? String == "max")
        #expect(object["files"] as? [String] == ["file-1", "file-2"])
        #expect(object["brandTokens"] == nil)
    }
}
