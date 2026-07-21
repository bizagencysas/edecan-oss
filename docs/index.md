# Documentación de Edecán — mapa

Esta carpeta es la documentación extendida del proyecto. La experiencia visible la gobierna primero el [`contrato assistant-first`](./producto-assistant-first.md): una conversación por texto o voz, tres espacios y las capacidades técnicas detrás del asistente. Para el contrato vinculante entre paquetes usa [`../ARCHITECTURE.md`](../ARCHITECTURE.md); para la evolución pública, [`roadmap.md`](./roadmap.md).

## Producto

| Documento | Para qué sirve |
|---|---|
| [`producto-assistant-first.md`](./producto-assistant-first.md) | Contrato de producto: Edecan, Actividad y Ajustes; órdenes compuestas; recuperación conversacional; skills locales y autorreparación reversible. |
| [`estilos-de-acompanamiento.md`](./estilos-de-acompanamiento.md) | Estilos profesional, coach, amigo y romántico; transparencia de IA, consentimiento adulto y salida inmediata. |
| [`autorreparacion-local.md`](./autorreparacion-local.md) | Cómo Edecan diagnostica un límite, crea una skill local o prepara una reparación Git aislada, probada y reversible. |
| [`primeros-pasos.md`](./primeros-pasos.md) | Guía corta para pasar de cero a una primera tarea real. |
| [`configuracion-minima.md`](./configuracion-minima.md) | Cero API keys con Codex CLI, matriz exacta de credenciales opcionales e instalación móvil local. |
| [`roadmap.md`](./roadmap.md) | Estado público actual, prioridades, criterios de salida y límites deliberados. |

## Empezar

El camino recomendado hoy es la app de escritorio (en construcción; ver [`roadmap.md`](./roadmap.md)); [`self-hosting.md`](./self-hosting.md) sigue siendo la referencia completa para quien prefiere correr Edecán desde el código fuente.

| Documento | Para qué sirve |
|---|---|
| [`self-hosting.md`](./self-hosting.md) | Levantar Edecán en tu propia infraestructura: requisitos, modo desarrollo, modo "producción ligera" con `infra/docker/compose.selfhost.yml`, y cómo traer tus propias API keys. |
| [`configuracion.md`](./configuracion.md) | Tabla completa de variables de entorno: cuáles son obligatorias, cuál es su valor por defecto y qué controla cada una. |

## Referencia técnica

| Documento | Para qué sirve |
|---|---|
| [`api.md`](./api.md) | Referencia de todas las rutas HTTP: método, autenticación, cuerpo de la petición y ejemplo de respuesta, incluido el formato de streaming SSE del chat. |
| [`personalizacion-nivel-dios.md`](./personalizacion-nivel-dios.md) | Qué controla cada campo de `PersonaConfig`, tres personas de ejemplo completas, y cómo funciona (y se borra) la memoria de largo plazo. |

## Integraciones

| Documento | Para qué sirve |
|---|---|
| [`conectores.md`](./conectores.md) | Cómo registrar tu propia app OAuth en Google, Microsoft, Meta, X y YouTube, los scopes mínimos exactos que usa Edecán, la URL de callback y los límites de uso conocidos de cada API. Incluye por qué LinkedIn está excluido permanentemente. |
| [`voz-telefonia.md`](./voz-telefonia.md) | Diferencia entre voz web (núcleo) y telefonía (premium), y el checklist legal obligatorio para operar llamadas y SMS salientes. |

## Cumplimiento y seguridad

| Documento | Para qué sirve |
|---|---|
| [`cumplimiento/privacidad.md`](./cumplimiento/privacidad.md) | Posición frente a GDPR/CCPA/LFPDPPP/Ley 1581: derechos de los titulares, retención de datos, subencargados y plantilla de DPA. |
| [`cumplimiento/tos-redes.md`](./cumplimiento/tos-redes.md) | Matriz por red social: qué permite cada API oficial, qué está prohibido, y el compromiso del producto. |
| [`seguridad-modelo-amenazas.md`](./seguridad-modelo-amenazas.md) | Modelo de amenazas: activos, actores, STRIDE resumido, los tres riesgos principales y las mitigaciones ya implementadas. |
| [`../SECURITY.md`](../SECURITY.md) | Política de divulgación responsable de vulnerabilidades. |

## Operación (runbooks)

| Documento | Para qué sirve |
|---|---|
| [`runbooks/incidente-fuga-tenant.md`](./runbooks/incidente-fuga-tenant.md) | Qué hacer si se sospecha que un tenant vio datos de otro. |
| [`runbooks/rotacion-claves.md`](./runbooks/rotacion-claves.md) | Rotar la data key del `TokenVault` y el `KMS_KEY_ID`/`LOCAL_MASTER_KEY`. |
| [`runbooks/restore-rds.md`](./runbooks/restore-rds.md) | Restaurar PostgreSQL (RDS en prod, volumen local en self-host) desde backup. |
| [`runbooks/cola-atascada.md`](./runbooks/cola-atascada.md) | Redrive de la Dead Letter Queue (`edecan-jobs-dlq`) cuando hay jobs atascados o fallando en bucle. |

## Ecosistema de agentes, automatizaciones y herramientas

La tabla enlaza documentación pública existente; `ARCHITECTURE.md` §11 define el contrato
de montaje defensivo para capacidades opcionales.

| Documento | Para qué sirve | Origen |
|---|---|---|
| [`analista.md`](./analista.md) | Analista total: XLSX/CSV/PDF/DOCX/PPTX, estadística, gráficos, visión (OCR/descripción de imágenes), predicción de series y detección de anomalías; desde v6 también como pantalla propia (`/v1/analista`, sin LLM). | fase v2 |
| [`navegador.md`](./navegador.md) | Navegador de investigación: fetch headless, extracción legible, comparación de precios. Jamás compra ni llena formularios. | fase v2 |
| [`creatividad.md`](./creatividad.md) | Generación de imágenes y documentos de oficina (DOCX/PPTX/PDF), más podcasts y efectos de sonido con el TTS bring-your-own del tenant. | fase v2 |
| [`creador-universal.md`](./creador-universal.md) | Una frase produce posts, Word, PDF, PowerPoint, páginas y scaffolds de apps reales con workspace, manifest y SHA-256. | v0.4 |
| [`mensajeria.md`](./mensajeria.md) | Telegram, Slack y Discord oficiales por tenant; por qué WhatsApp/Signal quedan fuera por ahora. | fase v2 |
| [`agentes.md`](./agentes.md) | Orchestrator + misiones: los 3 sub-agentes reales y los 13 perfiles declarados del ecosistema. | fase v2 |
| [`automatizaciones.md`](./automatizaciones.md) | Reglas disparador→acción (agenda/webhook → instrucción de agente). | fase v2 |
| [`ide.md`](./ide.md) | IDE embebido sobre el companion: árbol, editor, ediciones quirúrgicas, terminal allowlisted. | fase v2 |
| [`control-remoto.md`](./control-remoto.md) | Arquitectura completa de control remoto (nivel TeamViewer) + qué entrega hoy el prototipo solo-vista. | fase v2 |
| [`dinero-real.md`](./dinero-real.md) | Presupuestos, cotizaciones y órdenes: por qué toda orden nace borrador y nunca se auto-ejecuta. | fase v2 |
| [`asesores.md`](./asesores.md) | Legal, salud y educación: siempre informativo, disclaimers obligatorios. | fase v2 |
| [`negocios.md`](./negocios.md) | Facturación y dashboard de KPIs. | fase v2 |
| [`perfil-vivo.md`](./perfil-vivo.md) | Cómo se construye el perfil estructurado del usuario y dónde se usa. | fase v2 |

## Escritorio, bring-your-own y marketplace de skills

[`roadmap.md`](./roadmap.md) publica el estado y las prioridades vigentes. Los documentos
siguientes describen las capacidades y limitaciones actuales de cada superficie.

| Documento | Para qué sirve |
|---|---|
| [`desktop.md`](./desktop.md) | La app de escritorio (Tauri): instalación, wizard de primer arranque, empaquetado macOS/Windows. |
| [`desktop-local.md`](./desktop-local.md) | Cómo corre el backend empaquetado y local en la máquina del cliente (Postgres embebido, Redis simplificado) al abrir la app. |
| [`primeros-pasos.md`](./primeros-pasos.md) | Guía corta "de cero a mayordomo funcionando": los 2–3 pasos del wizard, sin configuración completa por delante. |
| [`credenciales.md`](./credenciales.md) | Pantalla de "Configuración": cómo conectar cada credencial (LLM, voz, conectores) con el flujo de pegar-y-validar, `/v1/credentials` (ver [`api.md`](./api.md)). |
| [`proveedores-llm.md`](./proveedores-llm.md) | Los proveedores LLM nuevos de v3 — Claude CLI, Codex CLI, Ollama y Vertex AI real — y cómo funciona la auto-detección de un clic (`/v1/setup/detect`). |
| [`skills.md`](./skills.md) | Marketplace abierto de Agent Skills (mismo estándar que indexa skills.sh): buscar, instalar y gestionar skills desde el toolkit de Edecán. |
| [`movil-ios.md`](./movil-ios.md) | App iOS nativa (Swift/SwiftUI, Liquid Glass): proyecto Xcode, compilación con tu propia cuenta de Apple Developer, instalación local vía USB. |
| [`movil-android.md`](./movil-android.md) | App Android nativa (Kotlin, Compose Multiplatform): mismo criterio que iOS, sin Play Store. |
| [`casa-inteligente.md`](./casa-inteligente.md) | Conector de Home Assistant: qué dispositivos controla y cómo conectar tu propia instancia. |

## v4-v6 — negocio, viajes, voz avanzada, reuniones y MCP

Documentos de las olas v4-v6 (`ARCHITECTURE.md` §13-§15), reunidos aquí para
que el estado de cada integración y sus límites operativos sean descubribles.

| Documento | Para qué sirve | Versión |
|---|---|---|
| [`ads.md`](./ads.md) | Borradores de campañas publicitarias con tu propia cuenta de Meta Ads — nunca activa gasto por su cuenta. | v4 |
| [`vehiculos.md`](./vehiculos.md) | Conector Smartcar (multi-marca): estado y control de cerraduras de tu vehículo vía el router HTTP. Fuera de alcance para nueva inversión (`docs/roadmap.md`), el router sigue activo. | v4 |
| [`notificaciones-push.md`](./notificaciones-push.md) | Recordatorios como notificación push nativa (APNs/FCM) con tus propias credenciales. | v5 |
| [`rrhh.md`](./rrhh.md) | Empleados, ausencias y nómina — toda corrida de nómina nace en borrador, nunca se paga sola. | v5 |
| [`viajes.md`](./viajes.md) | Buscar vuelos/hoteles (Amadeus) y rastrear paquetes (AfterShip) con tus propias cuentas — nunca reserva ni paga nada. | v5 |
| [`voz-telefonia.md`](./voz-telefonia.md) | Voz web vs. telefonía premium, clonación de voz con consentimiento verificable, y el checklist legal obligatorio para llamadas/SMS salientes. | v1/v5 |
| [`reuniones.md`](./reuniones.md) | Resumen y minutas de reuniones a partir de un audio/video que subas, con el STT/LLM de tu propio tenant. Requiere el consentimiento de los participantes. | v6 |
| [`mcp.md`](./mcp.md) | Conecta servidores MCP de terceros (o propios) como herramientas del agente — bring-your-own, siempre con confirmación por ser código no auditado. | v6 |

## v7 — consolidación y endurecimiento (2026-07-09)

v7 no agregó verticales nuevas — releyó las ya construidas (v2-v6) contra los patrones de bug
que fue encontrando cada ola anterior (fuga de credencial, plan-flag-bypass, evidencia perdida
en rollback, esquema SQL asumido vs. real) y cerró lo que seguía abierto. Resumen ejecutivo y
lista completa de bugs reales encontrados/corregidos en
`docs/seguridad-modelo-amenazas.md`; los 11 informes de barrido completos, con
metodología y tabla de veredicto archivo-por-archivo, en `docs/cumplimiento/`:

| Informe | Dominio |
|---|---|
| [`cumplimiento/barrido-v7-rrhh.md`](./cumplimiento/barrido-v7-rrhh.md) | RRHH/nómina — carrera en `resolver_ausencia` corregida. |
| [`cumplimiento/barrido-v7-viajes.md`](./cumplimiento/barrido-v7-viajes.md) | Viajes (Amadeus/AfterShip) — limpio, sin hallazgos. |
| [`cumplimiento/barrido-v7-voz.md`](./cumplimiento/barrido-v7-voz.md) | Voz avanzada + podcasts — audio huérfano en S3 corregido. |
| [`cumplimiento/barrido-v7-reuniones-analista.md`](./cumplimiento/barrido-v7-reuniones-analista.md) | Reuniones/Analista — 2 bugs de `process_meeting.py` corregidos. |
| [`cumplimiento/barrido-v7-mcp.md`](./cumplimiento/barrido-v7-mcp.md) | MCP bring-your-own — sin hallazgos de seguridad, escaneo de prompt-injection nuevo. |
| [`cumplimiento/barrido-v7-mediastreams-worker.md`](./cumplimiento/barrido-v7-mediastreams-worker.md) | Media Streams + 4 handlers de worker — evidencia/estado perdido en `run_mission.py`/`run_automation.py` corregido. |
| [`cumplimiento/barrido-v7-v4residual.md`](./cumplimiento/barrido-v7-v4residual.md) | Residual v4/v5 (devices/push, ERP, ads, mensajes) — limpio, sin hallazgos. |
| [`cumplimiento/barrido-v7-routers-restantes.md`](./cumplimiento/barrido-v7-routers-restantes.md) | 17 routers restantes — cuota fail-open en `files.py`/`voice.py` corregida. |
| [`cumplimiento/barrido-v7-ux.md`](./cumplimiento/barrido-v7-ux.md) | UX/navegación de `apps/web` — página de Ads construida. |
| [`cumplimiento/barrido-v7-apimd.md`](./cumplimiento/barrido-v7-apimd.md) | `docs/api.md` re-sincronizado programáticamente contra el código real. |
| [`cumplimiento/barrido-v7-desktop.md`](./cumplimiento/barrido-v7-desktop.md) | E2E real de `apps/local` — bug de `pgserver.get_uri()` corregido, flujo completo verificado de punta a punta. |

## Proyecto abierto

| Documento | Para qué sirve |
|---|---|
| [`../CONTRIBUTING.md`](../CONTRIBUTING.md) | Entorno de desarrollo, convenciones, validación y proceso de contribución. |
| [`../GOVERNANCE.md`](../GOVERNANCE.md) | Cómo se toman decisiones y cómo puede crecer el equipo mantenedor. |
| [`adr/README.md`](./adr/README.md) | Registro de decisiones arquitectónicas públicas. |
| [`claude-for-oss.md`](./claude-for-oss.md) | Borrador verificable y checklist de elegibilidad para Claude for Open Source; no es una afirmación de aceptación. |

## Otros documentos de referencia (fuera de `docs/`)

- [`../ARCHITECTURE.md`](../ARCHITECTURE.md) — arquitectura técnica y contratos obligatorios entre paquetes (§10).
- [`roadmap.md`](./roadmap.md) — capacidades implementadas y próximas prioridades públicas.
- [`seguridad-modelo-amenazas.md`](./seguridad-modelo-amenazas.md) — riesgos técnicos, límites de confianza y mitigaciones.
- [`../SECURITY.md`](../SECURITY.md) — política de seguridad y reporte de vulnerabilidades.
- [`../CONTRIBUTING.md`](../CONTRIBUTING.md) — convenciones de código y flujo de contribución.
