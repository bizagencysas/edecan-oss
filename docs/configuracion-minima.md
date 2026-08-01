# Configuración mínima: Workers AI, móvil e integraciones

Edecán administra su inteligencia desde la instalación. La persona que usa la
app no conecta un LLM ni elige modelos.

## Obligatorio para el operador

```dotenv
CLOUDFLARE_ACCOUNT_ID=
CLOUDFLARE_API_TOKEN=
WORKERS_AI_CHAT_MODEL=@cf/zai-org/glm-4.7-flash
```

Con esas dos credenciales, chat, voz, llamadas, herramientas ligeras y jobs
fuera del IDE usan Workers AI. El token permanece en el host y nunca se entrega
a un tenant o móvil.

## Credenciales opcionales

| Resultado | Qué conectar |
|---|---|
| Voz nativa en iOS/Android | Nada; usa capacidades del sistema |
| Búsqueda web | Nada para el fallback público; Brave/Tavily son opcionales |
| Imágenes generadas | Un proveedor compatible de imágenes |
| Voz cloud | Deepgram para STT y ElevenLabs para TTS |
| Llamadas | Twilio Account SID, Auth Token y un número |
| Gmail, Calendar y YouTube | Una app OAuth de Google |
| Outlook | Una app OAuth de Microsoft |
| Redes sociales | La app OAuth oficial de cada plataforma |

No existe un total único para “todo”: depende de las capacidades que active
cada instalación. Las credenciales opcionales se cifran y nunca se comparten
entre tenants.

## Conectar iOS o Android

Abre **Configuración → Conectar mi teléfono** en la aplicación de escritorio y
escanea el QR. El QR es de un solo uso; el teléfono recibe después una identidad
durable guardada en Keychain o Android Keystore.

### Android por USB, solo desarrollo

```bash
cd apps/mobile/android
./gradlew :androidApp:assembleDebug
adb reverse tcp:8765 tcp:8765
adb install -r androidApp/build/outputs/apk/debug/androidApp-debug.apk
```

### iOS

Abre `apps/mobile/ios/Edecan.xcodeproj`, selecciona tu equipo de desarrollo y
firma con tu propia cuenta de Apple. Después usa el mismo QR.

## Seguridad

- No pegues el token de Cloudflare en el chat.
- No lo guardes en Git, fixtures, logs ni capturas.
- Otorga al token solo los permisos necesarios para Workers AI.
- Rota el token si alguna vez se expone.

La arquitectura y las pruebas están en
[`workers-ai.md`](./workers-ai.md).
