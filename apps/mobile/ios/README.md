# Edecán — app iOS nativa

Esqueleto real y compilable: Swift 6 + SwiftUI puro, diseño Liquid Glass
(iOS 26.5), sin React Native (mandato permanente — ver `docs/roadmap.md`
y `ARCHITECTURE.md` §11). Nunca se distribuye por App Store/TestFlight:
instalación local por USB, cada cliente compila y firma con su propia
cuenta Apple Developer Program. Guía completa para el cliente final en
[`../../../docs/movil-ios.md`](../../../docs/movil-ios.md) — este README es
solo el arranque rápido para quien va a tocar el código.

## Estructura

```
apps/mobile/ios/
├── project.yml          # fuente de verdad del proyecto Xcode (xcodegen)
├── EdecanKit/            # Swift Package: red/datos, sin UI (SPM local)
│   ├── Sources/EdecanKit/{Models,NegociosModels,CredentialsModels,IDEModels,
│   │                       DeviceModels,VoiceModels,RemoteModels,ConfirmacionFormato,
│   │                       APIClient,MultipartFormData,SSEClient,Keychain,
│   │                       PairingStore}.swift
│   └── Tests/EdecanKitTests/     # 85 tests (`swift test`)
├── EdecanApp/             # target de la app (SwiftUI)
│   ├── EdecanApp.swift, RootTabView.swift, Theme.swift, SessionStore.swift
│   ├── Onboarding/OnboardingView.swift        # login + registro
│   ├── Screens/{Inicio,Chat,IDE,Negocios,Voz,Perfil,Remoto}View.swift
│   ├── Componentes/       # ViewModels + vistas compartidas (Chat, Negocios, Voz,
│   │                       # Credenciales, IDE, Remoto, TarjetaConfirmacion, VozRecorder, ...)
│   └── Resources/Assets.xcassets/
├── EdecanWidgets/         # extensión de widgets (placeholder mínimo)
└── fastlane/              # lanes generate/bump/adhoc — ver fastlane/README.md
```

`Edecan.xcodeproj` **no existe en el repo** — lo genera `xcodegen` a partir
de `project.yml`. Nunca lo edites a mano ni lo commitees (`.gitignore`).

## Build en 5 pasos

```bash
brew install xcodegen           # 1. una sola vez
cd apps/mobile/ios
xcodegen generate                # 2. genera Edecan.xcodeproj
open Edecan.xcodeproj            # 3. ábrelo en Xcode 26+
#    4. Signing & Capabilities → elige TU equipo (Apple Developer Program)
#    5. Conecta tu iPhone por USB → Run (▶︎), o `fastlane adhoc` para un .ipa instalable
```

Bundle id de partida: `cc.edecan.app` (placeholder de desarrollo, ver la
nota en `project.yml`) — cámbialo por uno propio antes de firmar con tu
cuenta, un bundle id no se puede repetir entre cuentas Developer distintas.

## Pipeline de versión conocido

Mismo patrón que el dueño de Edecán ya usa en sus otros proyectos iOS:

1. `fastlane bump` — sube `CURRENT_PROJECT_VERSION` en `project.yml`.
2. `xcodegen generate` — regenera el proyecto con la versión nueva.
3. `LC_ALL=en_US.UTF-8 fastlane adhoc` — build + firma ad-hoc.

## Verificar en local sin Xcode abierto

```bash
cd EdecanKit && swift build && swift test    # capa de red/datos, 85 tests
cd ..
xcodegen generate
xcodebuild -project Edecan.xcodeproj -scheme EdecanApp -configuration Debug \
  -destination 'generic/platform=iOS Simulator' \
  CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO build
```

## Qué es real hoy vs. qué falta

Ver la tabla completa en
[`../../../docs/movil-ios.md`](../../../docs/movil-ios.md) — resumen: auth
(login/registro/refresh/Keychain), chat con streaming SSE real +
confirmación de herramientas peligrosas in-app (con advertencia específica
por herramienta, `ConfirmacionFormato`), voz nativa (*push-to-talk* con
`AVAudioEngine`), Negocios (KPIs + dona + facturas), Perfil con estado de
credenciales y "Conectar LLM", IDE de solo lectura, Remoto — visor de
control remoto tipo TeamViewer sobre `/v1/remote` (*polling* de frames,
vista y control real de teclado/mouse, doble aprobación, indicador de
sesión activa e input tipeado; `RemotoView`/`RemotoViewModel`) — y
`EdecanKit` completo con tests SÍ están construidos; push APNs,
emparejamiento por dispositivo completo (gestionar/revocar otros
dispositivos), historial de llamadas de telefonía premium, IDE con
escritura, el transporte WebRTC de control remoto (el prototipo de hoy sigue
siendo *polling* HTTP, ver `docs/control-remoto.md` §1.1) y el widget con
datos reales quedan documentados como siguiente iteración.
