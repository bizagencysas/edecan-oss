// swift-tools-version: 6.0
// EdecanKit — capa de red/datos de la app iOS de Edecán, sin UI.
//
// Todo lo que habla con `/v1/*` (ver ../../../docs/api.md y
// ARCHITECTURE.md §10.12) vive aquí: modelos Codable, cliente HTTP,
// cliente SSE y persistencia en Keychain. `EdecanApp` (la app SwiftUI) y
// `EdecanWidgets` (la extensión de widgets) importan este paquete en vez
// de reimplementar el cliente cada uno.
import PackageDescription

let package = Package(
    name: "EdecanKit",
    platforms: [
        // iOS 26 es la plataforma real de envío (ver project.yml). macOS se
        // agrega SOLO para que `swift build`/`swift test` corran en local
        // sin simulador — sin este mínimo, SwiftPM compila para macOS con un
        // deployment target implícito viejo (10.13) y APIs como
        // `AsyncSequence.lines`/`@Observable` no compilan. EdecanApp/
        // EdecanWidgets nunca se distribuyen para macOS.
        .iOS("26.0"),
        .macOS("26.0"),
    ],
    products: [
        .library(name: "EdecanKit", targets: ["EdecanKit"])
    ],
    targets: [
        .target(
            name: "EdecanKit",
            swiftSettings: [.swiftLanguageMode(.v6)]
        ),
        .testTarget(
            name: "EdecanKitTests",
            dependencies: ["EdecanKit"],
            swiftSettings: [.swiftLanguageMode(.v6)]
        ),
    ]
)
