import Testing

@testable import EdecanKit

struct SharePayloadStoreTests {
    @Test func encolaCuentaYConsumePayloads() {
        _ = SharePayloadStore.consume()
        let payload = SharedSharePayload(
            kind: "file",
            value: "/tmp/informe.pdf",
            filename: "informe.pdf",
            mimeType: "application/pdf"
        )

        SharePayloadStore.enqueue([payload])

        #expect(SharePayloadStore.pendingCount() == 1)
        #expect(SharePayloadStore.consume() == [payload])
        #expect(SharePayloadStore.pendingCount() == 0)
    }
}
