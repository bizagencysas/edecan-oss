# Por qué `Edecan.entitlements` tiene lo que tiene

El archivo está **sin comentarios a propósito**: `plutil` valida comentarios XML pero el
parser de entitlements de la firma (AMFI) NO los acepta, y falla con
`AMFIUnserializeXML: syntax error near line N` apuntando a la línea del comentario. Por eso
la explicación vive acá y no ahí.

## Por qué existe el archivo

Firmar con un Developer ID activa el **hardened runtime**; con la firma ad-hoc anterior
estaba apagado. Bajo hardened runtime, el permiso de TCC (lo que la persona marca en Ajustes)
y el entitlement son **dos candados distintos**: se puede tener Automatización y Micrófono
concedidos y que la capacidad siga bloqueada.

Es la contracara de haber pasado a Developer ID. Ese cambio se hizo porque con la firma
ad-hoc la identidad de la app es el hash del ejecutable, que cambia en cada compilación, así
que macOS la veía como una app nueva cada vez y borraba los permisos. Con Developer ID la
identidad pasa a ser `identifier "cc.edecan.desktop" + Team ID`, que es estable entre builds.

## Las tres claves

| clave | para qué | quién la usa |
|---|---|---|
| `automation.apple-events` | manejar otras apps del Mac | `usar_computadora`, `personal_apps` (osascript) |
| `device.audio-input` | abrir la entrada de audio | palabra clave y comandos de voz |
| `cs.disable-library-validation` | cargar `.so` de terceros | sidecar de Python del backend |

`NSMicrophoneUsageDescription` en `Info.plist` **no** reemplaza a `device.audio-input`: ese
texto solo explica el permiso cuando el sistema lo pide.

## Mantenimiento

Si alguna capacidad deja de usarse, quitar su clave: un entitlement de más es superficie de
ataque regalada. La tercera es la más cara —baja una defensa real de la plataforma— y se va
el día que el sidecar no traiga binarios de terceros.

Para comprobar qué quedó de verdad en la firma, sin fiarse del build:

```bash
codesign -d --entitlements :- /Applications/Edecán.app
```
