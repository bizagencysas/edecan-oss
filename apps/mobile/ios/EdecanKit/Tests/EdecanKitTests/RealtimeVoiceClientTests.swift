import Foundation
import Testing
@testable import EdecanKit

struct RealtimeVoiceClientTests {
    @Test func decodificaAudioYTranscript() {
        let evento = RealtimeVoiceEvent(json: [
            "type": "audio",
            "turn_id": 3,
            "sequence": 2,
            "mime": "audio/wav",
            "data": Data([1, 2, 3]).base64EncodedString(),
            "state": "speaking"
        ])

        #expect(evento.type == "audio")
        #expect(evento.turnId == 3)
        #expect(evento.sequence == 2)
        #expect(evento.audio == Data([1, 2, 3]))
        #expect(evento.state == "speaking")
    }

    @Test func eventoSinAudioNoInventaBytes() {
        let evento = RealtimeVoiceEvent(json: [
            "type": "transcript",
            "text": "hola",
            "language": "es"
        ])

        #expect(evento.type == "transcript")
        #expect(evento.text == "hola")
        #expect(evento.audio == nil)
    }
}
