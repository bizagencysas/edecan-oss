fastlane documentation
----

# Installation

Make sure you have the latest version of the Xcode command line tools installed:

```sh
xcode-select --install
```

For _fastlane_ installation instructions, see [Installing _fastlane_](https://docs.fastlane.tools/#installing-fastlane)

# Available Actions

## iOS

### ios generate

```sh
[bundle exec] fastlane ios generate
```

Regenera Edecan.xcodeproj desde project.yml (xcodegen) — nunca se edita el .xcodeproj a mano

### ios bump

```sh
[bundle exec] fastlane ios bump
```

Sube CURRENT_PROJECT_VERSION en project.yml en 1 (build number) — mismo paso `sed` del pipeline conocido

### ios adhoc

```sh
[bundle exec] fastlane ios adhoc
```

Build ad-hoc firmado, listo para instalar por USB.

Requisitos ANTES de correr esta lane (docs/movil-ios.md tiene el
detalle completo, paso a paso):
  1. Tu propia cuenta Apple Developer Program ($99/año) — NUNCA la del
     dueño de Edecán (`REQUISITOS_V2.md`, "Decisión de negocio").
  2. El UDID de cada iPhone/iPad donde vas a instalar, registrado en
     TU cuenta (developer.apple.com → Devices) — tope de ~100
     dispositivos/año, es tuyo, no compartido con otros clientes.
  3. Un perfil de aprovisionamiento tipo "Ad Hoc" para
     `cc.edecan.app` (o el bundle id que hayas elegido — ver la nota
     en project.yml) que incluya esos dispositivos, con el
     certificado de distribución correspondiente instalado en tu
     Keychain.
  4. `DEVELOPMENT_TEAM` con TU Team ID — se configura en Xcode
     (target EdecanApp → Signing & Capabilities) tras iniciar sesión
     con tu Apple ID en Xcode → Settings → Accounts.
     `CODE_SIGN_STYLE: Automatic` (project.yml) deja que Xcode
     resuelva perfil/certificado solo a partir de ahí.

Este repo NUNCA trae un Team ID, certificado ni perfil reales —
cero secretos (`ARCHITECTURE.md` §0).


----

This README.md is auto-generated and will be re-generated every time [_fastlane_](https://fastlane.tools) is run.

More information about _fastlane_ can be found on [fastlane.tools](https://fastlane.tools).

The documentation of _fastlane_ can be found on [docs.fastlane.tools](https://docs.fastlane.tools).
