# Guía completa de Edecán

Esta es la puerta de entrada canónica al proyecto. Explica qué es Edecán, cómo
se instala, dónde vive cada componente, cómo se conecta un teléfono, cómo se
configuran modelos y llamadas, cómo funciona el IDE, cómo puede reparar su
propio código y cómo se publica un nodo online.

La guía está escrita para dos públicos:

- una persona que quiere usar Edecán sin convertirse en desarrolladora;
- una persona técnica que quiere compilarlo, autohospedarlo o contribuir.

Para detalles de implementación y cuerpos HTTP exactos, sigue los enlaces de
cada sección. El contrato técnico vinculante sigue siendo
[`ARCHITECTURE.md`](../ARCHITECTURE.md).

## 1. Qué es Edecán

Edecán es un asistente personal local-first. La conversación es la interfaz,
pero detrás existen memoria, herramientas, tareas durables, creación de
archivos, voz, telefonía, conectores, un estudio visual, un IDE y control de la
computadora.

La arquitectura separa tres superficies:

1. **App maestra de escritorio:** corre en macOS, Windows o Linux. Aloja el
   backend local y habilita archivos, terminal, IDE, modelos CLI y control de
   esa computadora.
2. **Apps móviles:** iOS y Android son controles personales de una instalación
   maestra. Se emparejan por QR y conservan una identidad revocable.
3. **Nodo online opcional:** mantiene chat básico, memoria, tareas, llamadas,
   recordatorios y notificaciones cuando la computadora está apagada. No puede
   inventar acceso al disco o pantalla de una computadora desconectada.

Cada instalación OSS pertenece a quien la ejecuta. No comparte las cuentas,
datos, credenciales, AWS, Cloudflare ni Apple Developer de los mantenedores.

## 2. Cómo leer el estado del proyecto

Estas palabras no significan lo mismo:

| Estado | Significado |
|---|---|
| Implementado | Existe código y pruebas para la capacidad. |
| Compilado | Una plataforma produjo un artefacto sin errores. |
| Instalado | El artefacto se colocó en un dispositivo concreto. |
| Configurado | Esa instalación recibió sus propias credenciales y permisos. |
| Verificado | Se ejerció el flujo real y se observó el resultado. |
| Producción | Además está firmado, monitorizado, respaldado y operado con su checklist completo. |

Edecán está en **developer preview v0.7**. El código es público y existen
empaquetadores nativos, pero los instaladores públicos no están firmados por
defecto. Telefonía, notificaciones, conectores y nube necesitan cuentas propias
antes de funcionar en una instalación nueva.

## 3. Mapa del repositorio

Todas las rutas siguientes son relativas a la raíz del clon:

```text
edecan/
├── Abrir Edecán.command       Lanzador de dos clics para macOS
├── README.md                  Presentación pública y quickstart
├── ARCHITECTURE.md            Contrato técnico completo
├── .env.example               Plantilla de configuración, sin secretos
├── apps/
│   ├── api/                   API FastAPI y routers /v1
│   ├── worker/                Trabajo asíncrono, misiones y notificaciones
│   ├── local/                 Runtime local empaquetable
│   ├── companion/             Acciones autorizadas sobre la computadora
│   ├── desktop/               Shell Tauri para macOS, Windows y Linux
│   ├── web/                   Interfaz Next.js
│   └── mobile/
│       ├── ios/               App SwiftUI nativa
│       └── android/           App Kotlin y Compose nativa
├── packages/                  Motores y capacidades reutilizables
│   ├── core/                  Agente, routing y composición cognitiva
│   ├── llm/                   Proveedores y selector de modelos
│   ├── toolkit/               Herramientas, código local y autorreparación
│   ├── voice/                 Voz y agentes telefónicos
│   ├── fydesign-engine/       Motor del estudio visual
│   └── ...                    Memoria, conectores, archivos, viajes, etc.
├── infra/
│   ├── docker/                Self-hosting con Compose
│   ├── aws/edge/              Continuidad económica en AWS
│   └── cloudflare/edge/       Borde privado en Cloudflare
├── scripts/                   Instalación, operación y validación
└── docs/                      Documentación especializada
```

No guardes claves en ninguna de esas rutas. Los archivos `.env` locales, los
llaveros del sistema y los secretos de nube son datos de cada instalación y
están fuera del código público.

## 4. La forma más rápida de probarlo

### macOS, sin aprender comandos

1. Clona o descarga el repositorio.
2. Abre `Abrir Edecán.command` con doble clic.
3. Completa el asistente inicial.
4. Comprueba en **Ajustes → Cómo piensa Edecán** que Workers AI figure como
   administrado y disponible.
5. En **Ajustes → Conectar teléfono**, muestra el QR si usarás iOS o Android.

El lanzador prepara dependencias y abre la app. Para entender el runtime
empaquetado consulta [`desktop-local.md`](./desktop-local.md).

### Desarrollo desde código

Requisitos comunes:

- Git;
- Python 3.12 o superior;
- [`uv`](https://docs.astral.sh/uv/);
- Node.js 22 y npm 10;
- Docker Compose v2 para el stack completo.

```bash
git clone https://github.com/bizagencysas/edecan-oss.git
cd edecan-oss
uv sync --all-packages --frozen
make check
```

Para ejecutar el stack de desarrollo:

```bash
cp .env.example .env
make deps
make db-migrate
make api
make worker
make web
```

No ejecutes `uv sync` sin `--all-packages`: este es un workspace y una
sincronización parcial puede quitar dependencias de otras apps.

## 5. Instalar la app de escritorio

### macOS

Para construir el instalador:

```bash
cd apps/desktop
./scripts/build-app.sh
```

Los artefactos quedan en:

```text
apps/desktop/src-tauri/target/release/bundle/dmg/
apps/desktop/src-tauri/target/release/bundle/macos/
```

Abre el DMG, arrastra `Edecán.app` a **Aplicaciones** y ejecuta esa copia. Un
build OSS sin firma puede requerir Control-clic → **Abrir** la primera vez.

La app queda residente en la barra de menús. Cerrar la ventana no apaga el
backend; **Salir completamente** sí lo hace. Ajustes permite iniciar Edecán
oculto al iniciar sesión.

### Windows

Requiere Rust estable y Visual Studio Build Tools con C++:

```powershell
cd apps\desktop
.\scripts\build-app.ps1
.\scripts\verify-windows-bundles.ps1
```

Salidas:

```text
apps\desktop\src-tauri\target\release\bundle\nsis\   Instalador recomendado
apps\desktop\src-tauri\target\release\bundle\msi\    MSI empresarial
```

Cerrar la ventana conserva Edecán en la bandeja. **Salir completamente**
termina también el backend local.

### Linux

En Debian o Ubuntu instala primero las dependencias listadas en
[`desktop.md`](./desktop.md), luego:

```bash
cd apps/desktop
./scripts/build-app.sh
```

Tauri produce AppImage, Debian y RPM en:

```text
apps/desktop/src-tauri/target/release/bundle/
```

Wayland, X11, PipeWire y los portales de cada distribución no exponen siempre
las mismas capacidades. La app comprueba captura y control al utilizarlos, no
simula un permiso global inexistente.

### Permisos de la computadora

Abre **Ajustes → Permisos de esta computadora**:

- macOS: Accesibilidad, Grabación de pantalla, Micrófono, Notificaciones y,
  solo cuando haga falta, Automatización o Acceso total al disco;
- Windows: Micrófono, Notificaciones y UAC para acciones administrativas;
- Linux: permisos del compositor, PipeWire y sandbox de Flatpak o Snap.

Concede permisos a la aplicación instalada, no a una copia de respaldo ni a un
Python de otro proyecto. En macOS, si reemplazas el binario por otro build sin
la misma firma, TCC puede tratarlo como una app distinta. Consulta
[`control-remoto.md`](./control-remoto.md) para diagnóstico.

## 6. Instalar iOS

La app es SwiftUI nativa y cada persona la firma con su propia cuenta. No
depende del Apple Developer ID de los mantenedores.

Requisitos:

- macOS con Xcode;
- XcodeGen;
- un iPhone conectado y en modo desarrollador;
- una cuenta Apple. Una cuenta gratuita sirve para pruebas temporales; una
  cuenta Developer permite una firma y distribución más estables.

```bash
brew install xcodegen
cd apps/mobile/ios
xcodegen generate
open Edecan.xcodeproj
```

En Xcode:

1. selecciona el proyecto y el target Edecan;
2. en **Signing & Capabilities**, elige tu equipo;
3. cambia `cc.edecan.app` por un bundle identifier que controles si Xcode lo
   solicita;
4. selecciona tu iPhone;
5. pulsa Run.

Para verificar el código sin firmar:

```bash
cd apps/mobile/ios/EdecanKit
swift build
swift test
cd ..
xcodegen generate
xcodebuild -project Edecan.xcodeproj -scheme EdecanApp \
  -sdk iphonesimulator CODE_SIGNING_ALLOWED=NO build
```

Detalles: [`movil-ios.md`](./movil-ios.md).

## 7. Instalar Android

Requisitos:

- JDK 17 o superior;
- Android SDK con la plataforma indicada por el proyecto;
- un dispositivo con depuración USB o un emulador.

```bash
cd apps/mobile/android
./gradlew :androidApp:assembleDebug
adb install -r androidApp/build/outputs/apk/debug/androidApp-debug.apk
```

Durante desarrollo por USB, este comando dirige el puerto local del teléfono a
la Mac o PC:

```bash
adb reverse tcp:8765 tcp:8765
```

Cada persona crea su propio keystore para builds release. No necesita la cuenta
de Google Play de los mantenedores. Detalles:
[`movil-android.md`](./movil-android.md).

## 8. Emparejar el teléfono por QR

1. Mantén la app maestra abierta o residente.
2. En escritorio abre **Ajustes → Conectar teléfono**.
3. En iOS o Android toca **Escanear QR**.
4. Acepta la cámara la primera vez.
5. Escanea el código antes de que expire.

El QR sirve una vez y expira. Después, el teléfono conserva una identidad
durable en Keychain o Android Keystore y renueva tokens sin pedir correo y
contraseña. Desde escritorio puedes revocar cualquier dispositivo.

Rutas centrales:

```text
POST /v1/devices/pairing
POST /v1/devices/pairing/claim
POST /v1/devices/pairing/refresh
GET  /v1/devices
POST /v1/devices/{device_id}/revoke
```

Minimizar el móvil no debe cancelar una tarea que sigue en el worker o en la
computadora. Si la app pierde el socket, recupera el estado al volver. Si la
computadora está apagada y no existe nodo online, IDE, archivos y control
remoto quedan naturalmente fuera de línea.

## 9. Inteligencia administrada y Task Router

La persona no elige proveedor ni modelo. Edecán recibe una tarea semántica y
su `TaskRouter` aplica la política del host:

| Superficie | Modelo | Política |
|---|---|---|
| Chat, voz, llamadas y herramientas ligeras | `@cf/zai-org/glm-4.7-flash` | mínima latencia y razonamiento desactivado |
| IDE e ingeniería | runtime y router de ingeniería separados | proveedor administrado, herramientas y contexto propios |

El adaptador de Workers AI implementa el contrato genérico `LLMProvider`.
Cambiar el proveedor de infraestructura en el futuro no requiere modificar
chat, llamadas, agentes ni workers. Las credenciales de Cloudflare pertenecen
al host y viven únicamente en variables de entorno; no se aceptan por chat ni
se exponen como configuración por usuario.

Referencia completa: [`proveedores-llm.md`](./proveedores-llm.md) y
[`workers-ai.md`](./workers-ai.md).

## 10. Voz

Hay tres capas distintas:

1. **Dictado del teléfono o sistema:** convierte voz a texto localmente.
2. **STT/TTS conectado:** Deepgram, ElevenLabs u otro proveedor mejora
   transcripción y síntesis.
3. **Telefonía:** usa un número Twilio y agentes configurables.

En **Ajustes → Voz** se eligen proveedor, voz, escucha y palabra de activación.
Las voces de ElevenLabs solo se pueden previsualizar después de validar una
clave propia. Los tags expresivos se generan justo antes de TTS y no contaminan
el texto visible del chat.

Nunca clones la voz de otra persona sin consentimiento verificable.

## 11. Configurar llamadas

Edecán soporta varios agentes telefónicos independientes. No son solo nombres:
cada plantilla guarda identidad, voz, objetivo, instrucciones, asuntos
permitidos, asuntos prohibidos, información que debe recopilar y criterios de
escalamiento.

### Conectar Twilio

Necesitas:

- Account SID;
- Auth Token;
- un número de Twilio con capacidad de voz;
- una URL HTTPS pública para webhooks.

En **Ajustes → Conexiones → Twilio** pega y valida esas credenciales. Edecán
las cifra y muestra solo valores enmascarados.

### Crear agentes

Abre **Llamadas → Agentes de llamadas** o pide en el chat:

```text
Configura un agente de llamadas llamado Asistente Ventas.
Debe explicar el producto, calificar al prospecto y agendar una reunión.
No puede prometer descuentos ni hablar de asuntos legales.
Usa la voz X y transfiere a una persona si preguntan por contratos.
```

Edecán debe pedir los campos que falten. Puedes crear cinco, diez o más agentes
con identidades diferentes, elegir uno predeterminado para llamadas entrantes y
otro para salientes, y editarlos después.

### Realizar una llamada

```text
Llama a Andrea al +57... con el agente de negocios.
El objetivo es presentar X y confirmar si quiere una demostración.
```

Antes de llamar se requieren:

- nombre de la persona;
- número internacional;
- agente exacto;
- objetivo;
- contexto mínimo;
- confirmación humana final.

Si falta algo, Edecán pregunta. No elige un agente al azar. Al terminar, guarda
estado, transcripción cuando esté habilitada, resumen, resultado y un intento de
notificación.

### Recibir llamadas

Asigna un agente entrante predeterminado y configura el webhook de voz del
número al nodo HTTPS. Si la Mac puede apagarse, ese webhook debe apuntar al nodo
online, no a `localhost`.

La conversación actual por Twilio es por turnos. No debe confundirse con audio
full-duplex de latencia ultrabaja. Consulta
[`agentes-llamadas.md`](./agentes-llamadas.md) y
[`voz-telefonia.md`](./voz-telefonia.md).

## 12. Chat, visión y archivos

El chat acepta texto, imágenes y archivos. Las apps muestran previews y
descargan el contenido autenticado. Para visión rápida, el archivo se adjunta al
turno del modelo capaz de visión; si el proveedor elegido no acepta imágenes,
Edecán usa su herramienta de análisis.

Edecán puede crear:

- imágenes;
- PDF;
- DOCX;
- PPTX;
- hojas de cálculo;
- páginas web;
- proyectos de aplicación;
- posts y paquetes de contenido.

Generar imágenes requiere un proveedor de imágenes configurado. Crear un
archivo no significa que su diseño sea perfecto: los generadores deben
renderizar, inspeccionar y corregir el resultado antes de presentarlo como
terminado.

Documentos relacionados:

- [`creador-universal.md`](./creador-universal.md)
- [`creatividad.md`](./creatividad.md)
- [`design-studio.md`](./design-studio.md)
- [`analista.md`](./analista.md)

## 13. Studio visual y contenido

`packages/fydesign-engine/` es el motor creativo integrado. La pantalla Studio
permite crear y refinar piezas, no solo recibir una imagen final.

El perfil editorial social es por persona. Configura tono, territorios,
formatos, objetivos, audiencia, frecuencia, CTA, temas prohibidos y estilo
visual. No existe un plan de LinkedIn fijo para todo el mundo.

Flujo recomendado:

1. configurar el perfil editorial;
2. investigar el tema;
3. generar texto y concepto visual relacionados;
4. editar ambos;
5. aprobar;
6. publicar mediante la API oficial conectada o exportar manualmente.

LinkedIn, X, Meta y YouTube requieren apps OAuth propias y permisos aprobados
por cada plataforma. Edecán no debe publicar ni gastar dinero sin aprobación.
Consulta [`conectores.md`](./conectores.md), [`ads.md`](./ads.md) y
[`design-studio.md`](./design-studio.md).

## 14. Usar el IDE desde el teléfono

El IDE móvil convierte la app de escritorio en un estudio portátil:

- **Archivos:** árbol, lectura, búsqueda y edición;
- **Agente:** runtime de ingeniería administrado con progreso y herramientas visibles;
- **Terminal:** sesiones persistentes;
- **Git:** estado, diff, stage, commit, ramas y push tipados.

### Autorizar una ruta

La primera vez, agrega desde la computadora una ruta absoluta que controles,
por ejemplo:

```text
/Users/tuusuario/Projects/mi-app
C:\Users\tuusuario\Projects\mi-app
/home/tuusuario/projects/mi-app
```

Edecán registra un identificador de workspace. El teléfono usa ese identificador
y no puede escapar a otras rutas. Home completo, raíz, llaveros, `.ssh`,
credenciales y carpetas del sistema deben seguir bloqueados.

### Pedir trabajo

```text
Abre el proyecto Acme, investiga por qué falla el login, corrígelo,
ejecuta las pruebas y muéstrame el progreso.
```

La sesión del agente y la terminal viven en la app maestra. Continúan aunque el
teléfono se minimice. Se detienen si la computadora se apaga, salvo que el
proyecto y el agente vivan explícitamente en un nodo online preparado para
ejecutar código.

Referencia y rutas: [`ide.md`](./ide.md).

## 15. Cómo Edecán puede arreglar su propio código

La autorreparación es local, explícita, reversible y desactivada por defecto.
Solo debe habilitarse sobre un clon Git de Edecán que controle su dueño.

Configuración mínima:

```dotenv
EDECAN_LOCAL_MODE=true
EDECAN_LOCAL_REPO_PATH=/ruta/absoluta/al/clon/edecan
EDECAN_SELF_REPAIR_ENABLED=true
EDECAN_SELF_REPAIR_TEST_COMMANDS_JSON=[["uv","run","--all-packages","--frozen","pytest","packages/toolkit/tests"]]
EDECAN_SELF_REPAIR_INSTALL_COMMANDS_JSON=[]
EDECAN_SELF_REPAIR_COMMAND_TIMEOUT_SECONDS=300
```

Comandos conversacionales:

| Comando | Comportamiento |
|---|---|
| `/fix descripción` | Diagnostica y repara el defecto descrito. |
| `/oss descripción` | Limita el trabajo al checkout OSS configurado. |
| `/changes` | Solo lectura: resume estado, diff y commits recientes. |

`/fix` sin descripción pide contexto, no inicia un trabajo indefinido.

Flujo de reparación:

1. reproduce y diagnostica;
2. intenta reutilizar una capacidad o skill existente;
3. valida que el repo esté limpio y dentro de la ruta autorizada;
4. crea un worktree aislado;
5. modifica solo el alcance necesario;
6. ejecuta los comandos exactos de la allowlist;
7. crea un commit local y aplica fast-forward;
8. reintenta la intención original;
9. revierte si la validación falla.

Edecán **no hace push automáticamente**. Publicar sigue siendo una decisión
separada. Una instalación binaria sin el código fuente no puede editar mágicamente
el repositorio.

Referencia: [`autorreparacion-local.md`](./autorreparacion-local.md).

## 16. Poner Edecán en internet

Hay dos opciones principales.

### Nodo completo con Docker Compose

Necesitas un servidor Linux, Docker Compose v2 y un subdominio:

```bash
git clone https://github.com/bizagencysas/edecan-oss.git
cd edecan-oss
scripts/instalar-online.sh \
  --dominio edecan.tudominio.com \
  --email tu@email.com
```

El instalador genera `.env.online` con permisos restrictivos, levanta
PostgreSQL con pgvector, Redis, almacenamiento, colas, API, worker, web y Caddy,
ejecuta migraciones y comprueba `/healthz`.

Para actualizar:

```bash
git pull --ff-only
scripts/instalar-online.sh \
  --dominio edecan.tudominio.com \
  --email tu@email.com
```

Consulta [`online-node.md`](./online-node.md) y
[`self-hosting.md`](./self-hosting.md).

### Continuidad económica con AWS y Cloudflare

`infra/aws/edge/` y `infra/cloudflare/edge/` contienen una arquitectura
Mac-first:

- Cloudflare recibe el tráfico autenticado y protege el origen;
- AWS API Gateway y Lambda atienden continuidad básica;
- DynamoDB conserva estado liviano;
- SQS desacopla trabajo;
- S3 guarda artefactos;
- Secrets Manager conserva secretos;
- un relay HTTP con heartbeat en DynamoDB (no AWS IoT Core) permite localizar la computadora cuando está disponible.

No clones nombres, dominios ni cuentas de un despliegue ajeno. Cada operador:

1. inicia sesión en su propia cuenta AWS;
2. inicia Wrangler en su propia cuenta Cloudflare;
3. elige un dominio propio;
4. despliega su propio stack;
5. coloca secretos con los mecanismos de nube;
6. configura presupuestos y alertas;
7. prueba autenticación válida e inválida.

Publicar ese código en Git no concede acceso al AWS o Cloudflare de quien lo
creó. Las credenciales no están en el repositorio.

La capa online mantiene chat básico y una cola de trabajos. El runtime local
envía heartbeat, reclama jobs y confirma resultados; Cloudflare adapta el
fallback al SSE móvil sin que iOS/Android cambien de endpoint. El control de
pantalla, los adjuntos, los archivos privados y el IDE requieren que la app
maestra esté conectada.

Las llamadas no continúan todavía con la computadora apagada: los webhooks y
agentes Twilio actuales viven en la API principal. Resolverlo correctamente
requiere un plano telefónico cloud con validación Twilio, identidades de agente,
voz, LLM, consentimiento y secretos propios. La cola genérica no se presenta
como sustituto de una llamada activa.

Configuración local, estado, pruebas y límites:
[`continuidad-hibrida.md`](./continuidad-hibrida.md).

## 17. Control remoto

El teléfono puede solicitar una sesión inmersiva de la computadora. Ver,
controlar mouse, escribir, hacer scroll, usar portapapeles o transferir archivos
son capacidades diferentes y pueden tener permisos diferentes.

En macOS:

1. instala una sola copia estable de `Edecán.app` en `/Applications`;
2. concede Grabación de pantalla a esa app;
3. concede Accesibilidad para entrada;
4. cierra Edecán completamente;
5. vuelve a abrir la misma copia;
6. actualiza estados desde el centro de permisos.

Apple no permite que la app se auto conceda estos permisos. Si la identidad
del binario cambia entre builds, puede ser necesario retirar la entrada vieja
y concederla a la nueva.

El modo solo vista no demuestra que mouse y teclado estén autorizados. Consulta
[`control-remoto.md`](./control-remoto.md) para el protocolo, permisos y
solución de TCC zombi.

## 18. Memoria, perfil y conversaciones

El perfil de **Tú** identifica a la persona que usa esa instalación. La memoria
guarda hechos, preferencias, eventos y entidades. Cuando una afirmación nueva
reemplaza una anterior, la anterior se marca como superada, no se presenta como
vigente.

Las conversaciones tienen identificador, título resumido y contexto propio.
Los recuerdos relevantes pueden cruzar conversaciones, pero no todo el
historial bruto se inyecta siempre. La persona puede inspeccionar, editar,
importar o borrar memoria. Si ya tenía otra instalación privada, el importador
local puede traer perfil, recuerdos, conversaciones, agentes de llamadas y
preferencias editoriales con un `dry-run` previo. No mueve datos al repositorio
ni activa horarios.

Consulta [`perfil-vivo.md`](./perfil-vivo.md) y
[`personalizacion-nivel-dios.md`](./personalizacion-nivel-dios.md). Para una
migración completa, consulta
[`migracion-asistente-privada.md`](./migracion-asistente-privada.md).

## 19. Conectores, MCP y credenciales

Edecán integra cuentas externas de tres formas:

- OAuth oficial: Google, Microsoft, Meta, X, YouTube y redes compatibles;
- credenciales propias: Twilio, voz, imágenes, búsqueda y servicios verticales;
- MCP: herramientas externas configuradas por la persona.

Los enlaces de configuración abren el portal oficial de cada proveedor. Una
app OAuth necesita Client ID, a veces Client Secret, callback exacto y scopes
mínimos. Los permisos de publicación dependen de la revisión de cada red.

MCP ejecuta código o servicios no auditados por Edecán. Instala solo servidores
de confianza, limita sus credenciales y revisa sus herramientas.

Referencias:

- [`credenciales.md`](./credenciales.md)
- [`conectores.md`](./conectores.md)
- [`mcp.md`](./mcp.md)
- [`skills.md`](./skills.md)

## 20. Notificaciones y actualizaciones

iOS usa APNs y Android usa FCM con credenciales del dueño de la instalación.
Edecán puede notificar llamadas, trabajos terminados, recordatorios y otros
eventos según preferencias. Sin credenciales push, la actividad sigue visible
en la app, pero no existe entrega remota garantizada.

La app de escritorio admite actualizaciones firmadas. Un canal público serio
necesita:

- artefactos firmados por plataforma;
- manifiesto de actualización firmado;
- canal estable o preview;
- checksum;
- estrategia de rollback hacia adelante.

Quien compila iOS o Android desde el código actualiza con `git pull --ff-only`,
recompila y reinstala. Git no puede reemplazar automáticamente una app firmada
en un teléfono.

Consulta [`notificaciones-push.md`](./notificaciones-push.md) y
[`actualizaciones.md`](./actualizaciones.md).

## 21. Mapa de rutas HTTP

La API usa el prefijo `/v1`. Las familias principales actuales son:

| Prefijo | Responsabilidad |
|---|---|
| `/v1/auth`, `/v1/me`, `/v1/setup` | sesión, dueño y onboarding |
| `/v1/conversations` | conversaciones, mensajes, streaming y confirmaciones |
| `/v1/memory`, `/v1/perfil`, `/v1/persona` | memoria y personalización |
| `/v1/files` | adjuntos, descargas y artefactos privados |
| `/v1/devices` | pairing, tokens, revocación y push |
| `/v1/credentials` | LLM, voz, imágenes y búsqueda |
| `/v1/connectors`, `/v1/mcp`, `/v1/skills` | OAuth, MCP y skills |
| `/v1/missions`, `/v1/automations`, `/v1/reminders` | trabajo durable |
| `/v1/ide`, `/v1/companion`, `/v1/remote` | computadora, IDE y remoto |
| `/v1/phone`, `/v1/voice`, `/v1/voz` | llamadas, STT, TTS y voz avanzada |
| `/v1/content`, `/v1/analista`, `/v1/reuniones` | creación, análisis y reuniones |
| `/v1/viajes`, `/v1/commerce`, `/v1/finance` | viajes y finanzas |
| `/v1/mensajes`, `/v1/contacts` | mensajería y contactos |
| `/v1/ads`, `/v1/negocios`, `/v1/erp`, `/v1/rrhh` | capacidades empresariales |
| `/v1/smarthome`, `/v1/vehiculos` | hogar y vehículos |
| `/v1/hooks`, `/v1/consents`, `/v1/usage`, `/v1/billing` | webhooks, consentimiento y operación |
| `/v1/admin` | administración restringida |

La referencia exhaustiva de métodos, autenticación, cuerpos, respuestas y SSE
está en [`api.md`](./api.md). En una instalación en ejecución, OpenAPI es la
fuente verificable del build concreto.

## 22. Datos locales y secretos

Nunca copies datos personales al repositorio. Usa:

- llavero del sistema para credenciales de la app;
- vault cifrado de Edecán para credenciales de proveedores;
- `.env` o `.env.online` ignorados por Git para configuración de runtime;
- Secrets Manager o el almacén de secretos de Cloudflare para nube.

No publiques:

- API keys;
- tokens OAuth;
- Twilio Auth Token;
- certificados APNs;
- claves FCM;
- JWT secrets;
- bases de datos;
- archivos de usuario;
- perfiles o memorias.

El menú de escritorio ofrece **Ver carpeta de datos** para resolver la ruta
correcta de esa plataforma sin adivinarla.

## 23. Comandos de validación

Validación base del repositorio:

```bash
uv sync --all-packages --frozen
make check
```

Pruebas Python:

```bash
uv run --all-packages --frozen pytest
```

Web:

```bash
cd apps/web
npm ci
npm test
npm run build
```

iOS y Android usan los comandos de las secciones 6 y 7. Los instaladores
nativos tienen verificadores adicionales en `apps/desktop/scripts/`.

Ningún typecheck sustituye una prueba funcional. Antes de afirmar que una
feature está terminada, verifica al menos:

1. código y pruebas;
2. build;
3. migraciones;
4. arranque;
5. permisos;
6. flujo real;
7. recuperación tras cerrar, minimizar o perder red;
8. ausencia de secretos en Git.

## 24. Solución rápida de problemas

### El backend no arranca

Revisa el detalle técnico y las migraciones. Comprueba:

```bash
uv sync --all-packages --frozen
make db-migrate
```

Si PostgreSQL no tiene pgvector, instala la extensión correspondiente o usa el
stack documentado. No muestres un traceback SQL completo al móvil.

### El teléfono pierde la conexión al minimizar

No reenvíes inmediatamente el mensaje. Vuelve a abrir la conversación y deja
que el cliente consulte el estado por ID. Si la tarea es durable, el worker
seguirá ejecutándola. Revisa la URL del nodo, el token renovado y el relay.

### El QR conecta en casa pero no fuera

Una IP local solo funciona en la misma red. Configura un dominio HTTPS y un
nodo online o túnel autenticado. No publiques el backend sin autenticación.

### Una voz muestra error

Valida la credencial del proveedor, el ID de voz, la cuota y el formato de
audio. Las voces de ejemplo no garantizan acceso a una cuenta de ElevenLabs.

### Edecán dice que configuró algo pero falla al usarlo

“Guardado” no significa “validado”. La pantalla debe distinguir credencial
guardada, validación del proveedor y operación real. Revisa el error HTTP
sanitizado y el modelo o endpoint exacto.

### El IDE no puede abrir una ruta

Autoriza el workspace desde la app maestra. Una ruta escrita en el chat no
anula el sandbox.

### `/fix` queda esperando

Incluye el defecto concreto:

```text
/fix al adjuntar una foto no se abre el selector
```

Comprueba que el clon, modo local, comandos de prueba y repo limpio estén
configurados.

## 25. Lecturas siguientes

- Primer uso: [`primeros-pasos.md`](./primeros-pasos.md)
- Mapa documental: [`index.md`](./index.md)
- Arquitectura: [`../ARCHITECTURE.md`](../ARCHITECTURE.md)
- Estado público: [`roadmap.md`](./roadmap.md)
- Seguridad: [`seguridad-modelo-amenazas.md`](./seguridad-modelo-amenazas.md)
- Contribuir: [`../CONTRIBUTING.md`](../CONTRIBUTING.md)
- Reportar vulnerabilidades: [`../SECURITY.md`](../SECURITY.md)

Si esta guía y el código difieren, el código del commit instalado y su OpenAPI
son la evidencia operativa. Abre un issue o pull request para volver a
sincronizar la documentación.
