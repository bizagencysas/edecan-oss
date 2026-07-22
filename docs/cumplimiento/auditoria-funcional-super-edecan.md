# Auditoría funcional de Super Edecán

Fecha de corte: 2026-07-22.

Este documento no considera que una función esté terminada solo porque exista una
pantalla, una ruta HTTP o una clase. Distingue cuatro niveles de evidencia:

1. **Prueba automatizada**: contrato cubierto sin depender de una cuenta externa.
2. **Build nativo**: el artefacto compila para la plataforma indicada.
3. **Integración externa**: necesita la cuenta y credenciales de la persona.
4. **Prueba física**: flujo ejecutado en una app instalada y un dispositivo real.

Estados usados:

- **Funcional**: el recorrido principal tiene implementación y pruebas.
- **Funcional con conexión**: el producto está listo, pero el resultado real depende de
  conectar una cuenta del usuario.
- **Parcial**: existe un recorrido útil, pero todavía no cumple toda la experiencia
  prometida.
- **No validado en esta plataforma**: no se afirma soporte de producción sin un build o
  dispositivo de esa plataforma.

## Matriz de capacidades

| Capacidad | Estado actual | Evidencia y límite real |
| --- | --- | --- |
| Chat, conversaciones y SSE | Funcional en API, web, bundle macOS y builds móviles | El servidor emite deltas SSE y conserva un evento final. Un turno real en el bundle macOS envió, limpió el compositor, generó título y terminó con la respuesta esperada. iOS y Android aplican cada delta, rechazan streams truncados, ignoran eventos posteriores al cierre y evitan duplicar la respuesta final. Faltan dispositivos físicos móviles para la última prueba de experiencia. |
| Modelos intercambiables | Funcional con conexión | El orquestador y las herramientas son independientes del modelo. Existen adaptadores para proveedores HTTP, Ollama y CLIs locales. Cambiar de modelo cambia inteligencia, coste y soporte de tool calling, no el catálogo de capacidades de Edecán. Una integración externa puede degradarse si el proveedor no respeta el protocolo. |
| Historial, títulos y memoria compartida | Funcional | Los chats tienen ID y título editable/autogenerado. La memoria vive fuera de una conversación y se inyecta en las siguientes. La consolidación reemplaza recuerdos obsoletos en vez de conservar dos verdades activas. |
| Perfil personal y Persona Engine | Funcional | Web, iOS y Android consumen el mismo perfil persistente. El Core Identity se conserva separado de memoria, planificación, ejecución, herramientas y estilo de relación. |
| Imágenes, PDF y archivos adjuntos | Funcional en app macOS empaquetada | Carga, almacenamiento, extracción, visión y entrega al modelo usan un camino neutral al proveedor. En el bundle de release se adjuntó un PNG sintético, cambió de `Subiendo` a `Listo`, se envió en un turno real y el modelo describió correctamente la forma y confirmó que no contenía texto. iOS y Android físicos siguen pendientes. |
| Design Studio y motor FyDesign | Funcional en app macOS instalada y builds móviles | Edecán conserva el producto creativo completo: 36 capacidades MCP, motor persistente de proyectos, canvas interactivo, referencias, selección y anotaciones, inspector, ajustes, historial, ramas, variantes, plantillas, sistemas de diseño, corpus, análisis de marca y exportación HTML, PNG y PDF. En la app macOS reinstalada se creó un proyecto real con Claude CLI, se persistió su revisión, se volvió a renderizar desde el motor y el resultado se comprobó dentro del lienzo y en pantalla completa. Web, iOS y Android usan un único contrato autenticado y aislado por tenant. No se copiaron usuarios, secretos ni configuración privada del repositorio original. Falta únicamente la prueba física móvil de esta superficie. |
| Content Studio: LinkedIn y X | Funcional con conexión | iOS y Android pueden crear borradores con el mismo contrato. Generar texto no requiere conectar la red; publicar o leer cuentas sí requiere el conector oficial y confirmación. |
| Documentos y PDF profesionales | Funcional y validado visualmente | El generador ya no imprime HTML o CSS como texto: interpreta la estructura, aplica la composición aurora y produce un PDF A4 real. La regresión está cubierta por pruebas y el resultado corregido se renderizó página por página para verificar tipografía, tarjetas, color y legibilidad. Cada nueva plantilla seguirá requiriendo validación visual, no solo bytes o texto extraído. |
| Generación y edición de imágenes | Funcional con conexión | El chat y el estudio pueden invocar el proveedor de imágenes configurado. Sin una clave o modelo local compatible, Edecán debe explicar qué falta y no fingir una imagen. |
| Previews de web, PDF e imágenes | Funcional | Hay previews seguros en web, iOS y Android. URLs no confiables se validan antes de abrirse; los archivos conservan descarga como alternativa. |
| Voz STT/TTS | Funcional con conexión | Dictado nativo funciona en móvil. Deepgram y ElevenLabs son configurables por usuario. Eleven v3 recibe etiquetas expresivas solo en el payload de audio; nunca aparecen en el mensaje visible. Proveedores sin etiquetas reciben texto limpio. |
| Escucha continua y palabra de activación | Parcial | La sesión puede quedar abierta después de la primera activación, pero las restricciones de audio en segundo plano dependen del sistema operativo. No se afirma escucha ilimitada en iOS cuando la app está suspendida. |
| Agentes de llamadas | Funcional con conexión | Se pueden guardar varios perfiles, iniciar/recibir llamadas, persistir eventos y generar un resumen posterior completo con duración, disponibilidad de transcripción, puntos clave, compromisos y próximos pasos. Web, iOS y Android exponen el resumen sin romper la navegación móvil de tres pestañas. Telefonía real exige Twilio y un proveedor de voz/modelo; las pruebas actuales no sustituyen una llamada real. Cada llamada sensible conserva confirmación y trazabilidad. |
| Notificaciones locales y push | Funcional con conexión | Existe cola universal para trabajos, llamadas, publicaciones y automatizaciones. Envío remoto requiere APNs/FCM y credenciales propias; sin ellas el evento persiste, pero no se afirma entrega push. |
| Búsqueda web | Funcional con conexión y fallback local | Brave/Tavily pueden configurarse por chat. El navegador local y MCP amplían la capacidad del modelo. Sin proveedor, el sistema no debe afirmar acceso a resultados recientes. |
| Vuelos, hoteles y viajes | Parcial | La búsqueda nativa ya no depende exclusivamente de Amadeus y puede usar la web/MCP. Las tarjetas deben mostrarse solo cuando aportan datos concretos. Reservas y compras siguen siendo preparatorias, no transacciones automáticas. |
| Conectores OAuth y MCP | Funcional con conexión | Google, Microsoft, Meta, X y otros tienen guías y estados verificables. Los secretos MCP se cifran. Meta Ads recomienda el endpoint oficial OAuth; un servidor comunitario queda marcado como tercero. |
| Skills | Funcional en el instalador; prueba visual pendiente | El instalador soporta repositorios raíz, `skills/<slug>`, `.claude/skills`, `.agents/skills` y colecciones curadas, con límites, anti-SSRF y revisión de capacidades. Se verificó que `anthropics/skills/pdf` resuelve su `SKILL.md`; falta smoke visual autenticado. |
| IDE, archivos y terminal del master | Funcional en app instalada; smoke visual pendiente | `list_tree`, lectura, escritura, edición, búsqueda y comandos pasan por el companion local y su sandbox. Un comando no autorizado sigue bloqueado. El bridge ya quedó dentro de la app reinstalada; todavía falta recorrer visualmente un proyecto y una terminal desde esa superficie antes de declarar la experiencia física cerrada. |
| Control remoto | Parcial | El teléfono se considera emparejado al completar el QR, pero el master debe estar disponible. Hay vista, ratón y teclado con aprobación. Todavía no es un reemplazo de AnyDesk con vídeo adaptativo, baja latencia y recuperación total de red. |
| Configuración de API keys por chat | Funcional para proveedores reconocidos | Un detector determinista extrae y redacta Anthropic, OpenAI, Gemini, DeepSeek, Groq, OpenRouter, xAI, Mistral, Kimi, ElevenLabs, Deepgram, Brave, Tavily y el par de Alpaca Paper antes de invocar al LLM. La clave no entra al historial, título ni stream. Cada conexión se valida antes de guardarse; proveedores desconocidos no se guardan por intuición. |
| Alpaca Trading | Funcional en Paper con conexión | Consulta cuenta, posiciones y órdenes; una orden paper usa doble confirmación e idempotencia. La URL está fijada a `paper-api.alpaca.markets`. Edecán no activa trading live ni dinero real. |
| Seguridad y PentestGPT | Parcial | Existe un flujo de auditoría autorizada, aislamiento y corrección asistida. PentestGPT y sus herramientas externas deben instalarse y ejecutarse solo contra objetivos que la persona autorice. No es un escáner masivo autónomo sin alcance. |
| Autorreparación local | Parcial | Edecán puede diagnosticar, editar, probar y reintentar dentro de un workspace autorizado mediante CLI/companion. Las acciones destructivas, expansión de permisos y publicación continúan requiriendo límites explícitos. |
| Bandeja, autoinicio y permisos del master | Funcional en app macOS instalada | El master tiene modo residente, icono propio, una sola instancia y centro de permisos. El bundle conservó el sidecar al cerrar la ventana, reabrió la misma instancia y mostró los seis permisos con la ruta del binario. La build final de este ciclo quedó instalada con firma ad-hoc verificada y las versiones anteriores se conservaron como respaldos recuperables. |
| Acceso remoto fuera de casa | Funcional con infraestructura | El móvil usa identidad del dispositivo, token y relay/túnel saliente. El QR no debe contener credenciales del túnel. La permanencia depende de que master y túnel estén activos; no se usa una URL LAN aleatoria como contrato permanente. |
| macOS | App instalada y validada | La build arm64, firma ad-hoc, DMG, sidecar, chat, IDE, companion, permisos, autoinicio, residencia y salida explícita pasaron smoke sobre el bundle de release. La aplicación final quedó instalada en las carpetas de aplicaciones del usuario y del sistema; salud, cabeceras defensivas, Studio, render y pantalla completa se comprobaron sobre el binario instalado. La distribución pública aún necesita firma Developer ID y notarización propias. |
| iOS | Build nativo aprobado; dispositivo pendiente | SwiftUI y EdecanKit implementan chat, actividad, perfil, previews, Studio, voz, IDE y resumen completo de llamadas. El teclado puede cerrarse con “Listo” y el copiado del chat sale como texto plano. `swift test` aprobó 142 pruebas, `xcodebuild` terminó correctamente para simulador y la app actualizada se instaló y abrió allí. El iPhone físico no está conectado durante esta auditoría. |
| Android | Build nativo aprobado; dispositivo pendiente | Compose y el módulo compartido implementan los mismos contratos móviles, incluido historial y resumen de llamadas como pantalla secundaria sin añadir otra pestaña. Enviar cierra el teclado y copiar elimina formato enriquecido. Pruebas shared/app, compilación Kotlin, `lintDebug` y `assembleDebug` terminaron correctamente y generaron el APK debug. Falta la sesión física autenticada. |
| Windows y Linux | No validados en esta plataforma | Tauri y el companion tienen código multiplataforma, pero un build hecho en macOS no prueba instaladores, permisos ni autoinicio de Windows/Linux. Se requieren CI runners y smoke tests nativos. |

## Criterios de cierre

Una fila no pasa a “Funcional” por tener una pantalla bonita. Para cerrarla se
necesita, según aplique:

- prueba del contrato y de sus errores;
- build de producción de la superficie;
- un artefacto instalable;
- prueba con la cuenta externa real sin imprimir secretos;
- prueba física de envío, recepción o preview;
- resultado visible para la persona y un error accionable cuando falte algo.

Los servicios externos que no estén conectados deben verse como **No conectado** o
**Requiere atención**. Los datos demo nunca se presentan como si fueran datos reales.
