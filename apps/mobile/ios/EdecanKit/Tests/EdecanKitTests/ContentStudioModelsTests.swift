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
}
