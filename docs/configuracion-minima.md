# Configuración mínima: Codex CLI, móvil e integraciones

Edecan no necesita una colección de claves para empezar. El perfil recomendado
para una persona que ya usa Codex CLI tiene **cero API keys obligatorias**:

1. instala y autentica Codex CLI con `codex login`;
2. abre Edecan en modo local/escritorio;
3. en el primer arranque pulsa **Usar mi Codex CLI**;
4. escribe o habla en el mismo chat.

Edecan ejecuta Codex en un directorio efímero y de solo lectura. Codex decide;
las herramientas, permisos, confirmaciones y archivos siguen bajo el control de
Edecan. La suscripción y los límites aplicables son los de la cuenta con la que
se autenticó el CLI.

## Cuántas credenciales necesitas

| Resultado deseado | Credenciales nuevas | Qué conectar |
|---|---:|---|
| Chat, memoria, archivos, creación local y herramientas con Codex CLI | **0** | Solo `codex login` |
| Voz nativa en iOS/Android | **0** | Reconocimiento y lectura del sistema |
| Búsqueda real en Internet | **1** | Brave **o** Tavily |
| Imágenes generadas por IA | **1** | Un proveedor compatible de imágenes |
| Internet + imágenes, perfil recomendado | **2** | Una búsqueda + una imagen |
| Voz cloud de mayor calidad | **2 opcionales** | Deepgram (STT) + ElevenLabs (TTS) |
| Vuelos y hoteles reales | **2 credenciales** | Amadeus API key + secret |
| Rastreo de paquetes | **1 opcional** | AfterShip |
| Llamadas telefónicas | **2 credenciales + 1 número** | Twilio Account SID, Auth Token y número |
| Gmail, Calendar y YouTube | **1 app OAuth** | Client ID + client secret de Google |
| Outlook | **1 app OAuth** | Client ID + client secret de Microsoft |

Las parejas OAuth, el SID de Twilio y los secretos de proveedor son
credenciales, pero no todas se llaman “API key”. Por eso no existe un total
honesto para “todo”: depende de qué cuentas y servicios quieras conectar.
Edecan funciona primero y degrada de forma visible a demostración cuando una
capacidad opcional no tiene proveedor; nunca presenta datos de ejemplo como
resultados en vivo.

## Codex CLI en el computador

Para una persona normal no hay comandos: abre `Edecán.app` o haz doble clic
en `Abrir Edecán.command`, entra a **Configuración → Conexiones** y elige
Codex CLI. Lo siguiente es únicamente el flujo de desarrollo del repo.

```bash
codex login
codex login status
uv run --all-packages edecan --no-web --mobile-access
```

Cuando aparezca `EDECAN_LOCAL_READY port=8765`, Edecan está disponible solo en
`127.0.0.1`, por diseño. El asistente detecta el binario y su versión mediante
`GET /v1/setup/detect`; no hay que copiar tokens de Codex dentro de Edecan.

## Conectar iOS o Android

Abre **Configuración → Conectar mi teléfono** en Edecán y escanea el QR. El
QR es de un solo uso y vence en diez minutos; el teléfono recibe después una
identidad durable, cifrada en Keychain/Android Keystore y revocable desde la
lista de dispositivos. Si el JWT normal caduca o el backend local se reinicia,
la app recupera la sesión con esa identidad sin pedir otra URL ni contraseña.

## Android conectado por USB (solo desarrollo)

Para un build de desarrollo y un teléfono autorizado por ADB:

```bash
cd apps/mobile/android
./gradlew :androidApp:assembleDebug
adb reverse tcp:8765 tcp:8765
adb install -r androidApp/build/outputs/apk/debug/androidApp-debug.apk
```

El deep link del QR sigue siendo el flujo principal. `adb reverse` lleva
`127.0.0.1:8765` hasta el backend local del computador y solo es un camino de
desarrollo por USB, no un sustituto de un relay autenticado o de HTTPS para uso
remoto diario.

## iOS

Abre `apps/mobile/ios/Edecan.xcodeproj`, selecciona tu equipo y firma con tu
cuenta de Apple. Después usa el mismo QR de Configuración; la app de escritorio
anuncia su dirección de red local automáticamente.

## Regla de seguridad

Conecta credenciales desde Ajustes o los endpoints BYO del tenant. No las
guardes en el repositorio, no las envíes en el chat y no reutilices una llave de
plataforma entre usuarios. Los archivos y medios del chat se descargan por una
ruta autenticada que vuelve a comprobar el dueño.
