import Foundation
import Testing
@testable import EdecanKit

struct IOSUpdatesTests {
    @Test func semverOrdenaVersionesFinalesYPreliminares() throws {
        let ordered = [
            "1.0.0-alpha",
            "1.0.0-alpha.1",
            "1.0.0-alpha.beta",
            "1.0.0-beta",
            "1.0.0-beta.2",
            "1.0.0-beta.11",
            "1.0.0-rc.1",
            "1.0.0",
            "1.0.1",
            "1.1.0",
            "2.0.0",
        ]
        let parsed = try ordered.map { try #require(SemanticVersion($0)) }

        #expect(parsed == parsed.sorted())
        #expect(try #require(SemanticVersion("1.0.0+build.1"))
            == #require(SemanticVersion("1.0.0+build.99")))
    }

    @Test func semverRechazaFormatosAmbiguos() {
        for invalid in [
            "", "1", "1.2", "1.2.3.4", "01.2.3", "1.02.3", "1.2.03",
            "1.2.3-", "1.2.3+", "1.2.3-alpha..1", "1.2.3-01",
            "1.2.3-ñ", "1.2.3+meta_1",
        ] {
            #expect(SemanticVersion(invalid) == nil)
        }
        #expect(SemanticVersion("v1.2.3-preview.4+sha.abcdef") != nil)
    }

    @Test func decodificaManifiestoValidoYLimitaNotas() throws {
        let notes = String(repeating: "a", count: 20_100)
        let data = try #require(
            """
            {
              "schema_version": 1,
              "channel": "stable",
              "version": "v0.8.0",
              "build_number": 25,
              "published_at": "2026-07-23T12:00:00Z",
              "release_notes": "\(notes)",
              "install_url": "https://apps.apple.com/app/id123456789"
            }
            """.data(using: .utf8)
        )

        let manifest = try IOSUpdateManifestParser.parse(data)

        #expect(manifest.channel == .stable)
        #expect(manifest.versionText == "0.8.0")
        #expect(manifest.buildNumber == 25)
        #expect(manifest.releaseNotes.count == 20_000)
        #expect(manifest.installKind == .appStore)
        #expect(manifest.presentationID == "0.8.0-25")
    }

    @Test func manifiestoRechazaEsquemaVersionBuildYTamanoInvalidos() throws {
        let badSchema = try manifestData(schema: 2)
        #expect(throws: IOSUpdateManifestError.unsupportedSchema) {
            try IOSUpdateManifestParser.parse(badSchema)
        }

        let badVersion = try manifestData(version: "0.08.0")
        #expect(throws: IOSUpdateManifestError.invalidVersion) {
            try IOSUpdateManifestParser.parse(badVersion)
        }

        let badBuild = try manifestData(build: 0)
        #expect(throws: IOSUpdateManifestError.invalidBuildNumber) {
            try IOSUpdateManifestParser.parse(badBuild)
        }

        let oversized = Data(repeating: 0x20, count: IOSUpdateManifestParser.maximumBytes + 1)
        #expect(throws: IOSUpdateManifestError.tooLarge) {
            try IOSUpdateManifestParser.parse(oversized)
        }
    }

    @Test func soloAceptaCanalDeManifiestoPorHTTPS() throws {
        #expect(
            try IOSUpdateManifestParser.validatedManifestURL(
                "https://updates.edecan.cc/ios/stable.json"
            ).host == "updates.edecan.cc"
        )
        for invalid in [
            "http://updates.edecan.cc/ios.json",
            "file:///tmp/ios.json",
            "https://user:secret@updates.edecan.cc/ios.json",
            "https://updates.edecan.cc/ios.json#otro",
        ] {
            #expect(throws: IOSUpdateManifestError.insecureManifestURL) {
                try IOSUpdateManifestParser.validatedManifestURL(invalid)
            }
        }
    }

    @Test func clasificaMecanismosOficialesYRechazaEsquemasPeligrosos() throws {
        let cases: [(String, IOSUpdateInstallKind)] = [
            ("https://apps.apple.com/app/id123", .appStore),
            ("https://testflight.apple.com/join/ABCDEF", .testFlight),
            ("https://downloads.example.com/edecan", .web),
            ("itms-apps://itunes.apple.com/app/id123", .appStore),
            ("itms-beta://testflight.apple.com/join/ABCDEF", .testFlight),
            ("altstore://source?url=https%3A%2F%2Fexample.com%2Fsource.json", .altStore),
            ("sidestore://source?url=https%3A%2F%2Fexample.com%2Fsource.json", .sideStore),
        ]
        for (url, expectedKind) in cases {
            #expect(try IOSUpdateManifestParser.validatedInstallURL(url).1 == expectedKind)
        }
        for invalid in [
            "javascript:alert(1)",
            "file:///private/var/mobile/secret",
            "https://user:secret@example.com/install",
            "https://example.com/install#fragment",
            "altstore:",
            "sidestore:",
        ] {
            #expect(throws: IOSUpdateManifestError.invalidInstallURL) {
                try IOSUpdateManifestParser.validatedInstallURL(invalid)
            }
        }
    }

    @Test func politicaImpideDowngradeCanalCruzadoYPreviewEnStable() throws {
        let final = try manifest(channel: .stable, version: "0.8.0", build: 20)
        let preview = try manifest(channel: .preview, version: "0.8.0-beta.1", build: 20)

        #expect(IOSUpdatePolicy.isAvailable(
            final,
            currentVersion: "0.7.3",
            currentBuild: 18,
            configuredChannel: .stable
        ))
        #expect(!IOSUpdatePolicy.isAvailable(
            final,
            currentVersion: "0.8.1",
            currentBuild: 18,
            configuredChannel: .stable
        ))
        #expect(!IOSUpdatePolicy.isAvailable(
            preview,
            currentVersion: "0.7.3",
            currentBuild: 18,
            configuredChannel: .stable
        ))
        #expect(!IOSUpdatePolicy.isAvailable(
            final,
            currentVersion: "0.7.3",
            currentBuild: 18,
            configuredChannel: .preview
        ))
    }

    @Test func politicaAceptaBuildMayorDeLaMismaVersion() throws {
        let higherBuild = try manifest(channel: .preview, version: "0.8.0+sha.new", build: 22)

        #expect(IOSUpdatePolicy.isAvailable(
            higherBuild,
            currentVersion: "0.8.0+sha.old",
            currentBuild: 21,
            configuredChannel: .preview
        ))
        #expect(!IOSUpdatePolicy.isAvailable(
            higherBuild,
            currentVersion: "0.8.0",
            currentBuild: 22,
            configuredChannel: .preview
        ))
    }

    private func manifest(
        channel: IOSUpdateChannel,
        version: String,
        build: Int
    ) throws -> IOSUpdateManifest {
        try IOSUpdateManifestParser.parse(
            manifestData(channel: channel.rawValue, version: version, build: build)
        )
    }

    private func manifestData(
        schema: Int = 1,
        channel: String = "stable",
        version: String = "0.8.0",
        build: Int = 20
    ) throws -> Data {
        try JSONSerialization.data(withJSONObject: [
            "schema_version": schema,
            "channel": channel,
            "version": version,
            "build_number": build,
            "release_notes": "Mejoras de estabilidad.",
            "install_url": "https://apps.apple.com/app/id123456789",
        ])
    }
}
