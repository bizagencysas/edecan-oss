# fastlane — Edecán iOS

Automatiza el pipeline de build/firma. Ninguna lane sube nada a App Store
Connect ni TestFlight — Edecán nunca se distribuye por tienda (ver
`docs/movil-ios.md`).

```bash
brew install fastlane xcodegen   # una sola vez
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 fastlane bump      # sube el build number
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 fastlane adhoc     # genera + build ad-hoc firmado
```

| Lane | Qué hace |
|---|---|
| `generate` | Corre `xcodegen generate` desde `project.yml`. La usan las demás lanes; rara vez hace falta llamarla suelta. |
| `bump` | Sube `CURRENT_PROJECT_VERSION` en `project.yml` en 1 (build number). Corré esto antes de cada build que vayas a instalar, para que iOS reconozca la nueva versión. |
| `adhoc` | `xcodegen generate` + build Release firmado `ad-hoc` (`gym`). Requiere tu propia cuenta Apple Developer Program configurada en Xcode — ver requisitos completos en la descripción de la lane (`fastlane action adhoc` o el propio `Fastfile`) y en `docs/movil-ios.md`. |

El prefijo `LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8` evita un error de
codificación de Ruby que puede aparecer al leer el proyecto según el locale
del sistema — mismo pipeline conocido que el resto de proyectos iOS del
dueño de Edecán.
