# Roadmap

Dirección de producto a mediano plazo, más allá del alcance ya construido descrito en `ARCHITECTURE.md` y `PLAN.md`. Este documento es **intención**, no un compromiso de fechas — se prioriza contra la métrica norte descrita en el go-to-market de `PLAN.md` (conversión descarga→licencia de Edecán Escritorio, retención/renovación de actualizaciones, e instalaciones self-host activas de comunidad).

## De v2 a v3: qué se construyó y qué entra ahora

**v2 quedó completo**: ecosistema de agentes (misiones multi-paso, `Orchestrator` + 3 sub-agentes reales), automatizaciones (disparador→acción), IDE embebido sobre el companion, vista remota view-only, analista de documentos (Excel/CSV/PDF + visión), navegador de investigación, creatividad (imágenes + documentos de oficina), mensajería oficial (Telegram/Slack/Discord), presupuestos/cotizaciones/órdenes con dinero real siempre confirmado a mano, asesores legal/salud/educación con disclaimers, facturación + KPIs de negocio, y perfil vivo — detalle completo en `ARCHITECTURE.md` §11 y en cada documento de la sección "v2" de [`index.md`](./index.md).

**v3 entra con dos objetivos**: (1) corregir el único hueco de diseño real que quedaba — que algunas credenciales de terceros (voz web, y el LLM en despliegues hospedados) todavía se resolvían desde configuración de PLATAFORMA en vez de por tenant — y (2) lanzar la app de escritorio (Tauri) como el vehículo de venta principal, en vez del código-fuente-para-que-lo-armes-tú. Ver `DIRECCION_ACTUAL.md` para la dirección completa vigente; ese documento reemplaza en autoridad a cualquier decisión de negocio previa que lo contradiga.

## v3 — escritorio, bring-your-own completo y el resto de P1/P2 hacia código real

`ROADMAP_V2.md` §3 clasificó buena parte del wishlist de `REQUISITOS_V2.md` en P0 (se construyó real), P1 (parcial) y P2 (solo documentado, "queda para después"). **Esa barrera P1/P2 queda superada en v3 por decisión explícita del dueño del proyecto** (`DIRECCION_ACTUAL.md`, "Ambición: sin límite, salvo lo que no es código"): la instrucción es terminar el wishlist completo moviendo lo que estaba en P1/P2 hacia código real, salvo lo que depende de acciones irreversibles con dinero/cuentas reales del dueño del proyecto (aplicar Terraform contra su AWS real, conectar Stripe/Twilio de producción propios, publicar en tiendas — eso sigue sin ejecutarse, por diseño, no por falta de ambición).

Frentes de esta ola:

- **App de escritorio (Tauri)** — el paquete más importante de v3: empaqueta `apps/web/` como interfaz sobre un envoltorio Tauri (Rust), con el backend (hoy pensado para Docker Compose/ECS) corriendo LOCAL en la máquina del cliente al abrir la app (Postgres embebido o alternativa más liviana; Redis simplificado o removido en modo single-user local). Pantalla de "Configuración" pulida para todas las credenciales de terceros, sin `.env` a mano. Ver `docs/desktop.md` y `docs/desktop-local.md`.
- **Bring-your-own completo, por tenant (`apps/api` y `apps/worker` aterrizados)** — corrige el hueco documentado en `REQUISITOS_V2.md` ("Corrección de diseño"): voz web (Deepgram/ElevenLabs/Polly) y LLM dejan de depender de `Settings`/`.env` de plataforma y resuelven credenciales SIEMPRE desde el `TokenVault` del propio tenant, mismo patrón que ya usa Twilio — en `apps/api`, si el tenant no conecta nada, el LLM corta con `HTTPException(400)` y la voz cae a stub, NUNCA a la config de plataforma (ver [`credenciales.md`](./credenciales.md) "Orden de resolución"). El worker (`apps/worker/edecan_worker/deps.py::Deps.llm_router_for`, jobs asíncronos) ya tiene el mismo corte: lanza `TenantLLMNotConnectedError` en vez de degradar a plataforma, dejada propagar hasta el despachador del job (reintento/DLQ). Endpoint `/v1/credentials` (ver [`api.md`](./api.md)).
- **Nuevos proveedores LLM**: `ClaudeCLIProvider`/`CodexCLIProvider` (ejecutan `claude`/`codex` como subproceso local, usan la sesión/suscripción ya paga del cliente sin pedir una API key aparte — solo tiene sentido con el backend corriendo local, por eso encaja naturalmente con la app de escritorio), `VertexAIProvider` real (hoy stub — credenciales de GCP del propio cliente, con un camino simple de API key por defecto y el flujo completo de service account como opción avanzada), y `OllamaProvider` (modelos locales gratis, detectado automáticamente igual que los CLIs). Auto-detección de un clic para los tres (`GET /v1/setup/detect`, gated a que el backend corra en modo local — ver [`docs/proveedores-llm.md`](./proveedores-llm.md) y [`api.md`](./api.md)).
- **Marketplace abierto de Agent Skills** — en vez de construir un catálogo propio desde cero, Edecán se conecta al mismo estándar abierto que indexa skills.sh (soporta 20+ agentes, instalación vía `npx skills add <owner/repo>` o su API programática) para que el toolkit/IDE embebido/`Orchestrator` puedan instalar y usar skills de ese marketplace compartido. Ver `docs/skills.md` y [`api.md`](./api.md) `/v1/skills`.
- **Esqueletos móviles reales** — a diferencia de v2 (que dejó la arquitectura documentada en `REQUISITOS_V2.md` §6.1 sin generar proyecto), v3 arranca el proyecto Xcode real (Swift/SwiftUI, iOS 26.5, Liquid Glass) y el proyecto Kotlin Multiplatform real (Compose Multiplatform) — nunca React Native. Cada cliente compila/firma/instala con su propia cuenta de Apple Developer Program (resuelve el tope de 100 dispositivos/año usando el cupo de cada cliente, no uno compartido); nunca se publica en App Store/Play Store, instalación local vía USB en modo desarrollador. Ver `docs/movil-ios.md` y `docs/movil-android.md`.
- **Casa inteligente (Home Assistant)** — pasa de P2 documentado (`ROADMAP_V2.md` §6.3) a conector real: un solo conector (API oficial, self-host friendly) da luces/AC/cámaras/cerraduras/sensores. Ver [`docs/casa-inteligente.md`](./casa-inteligente.md) y [`api.md`](./api.md) `/v1/smarthome`.
- **WhatsApp Business Platform** — hasta v2 aparecía como "candidato futuro" en la sección "Más conectores" de este documento (ver más abajo); en v3 pasa a conector real de esta ola: API oficial de Meta, con su propio motor de cumplimiento (plantillas pre-aprobadas, ventanas de mensajería de 24h, opt-in explícito) — no es un copy-paste del checklist de telefonía. Ver [`api.md`](./api.md) `PUT /v1/connectors/whatsapp/credentials` y `mensajeria.md` (actualización pendiente para v3).
- **Video** — pasa de P2 documentado (`ROADMAP_V2.md` §6.3: "extracción de frames (ffmpeg en el worker) + visión por lotes") a capacidad real del analista: análisis de video por frames reutilizando el mismo bloque de visión del LLM que ya usa `analizar_imagen` (WP-V2-02), con resúmenes de reuniones sujetos a consentimiento de grabación (mismo principio que telefonía).

Lo que **no** cambia, porque no es un límite autoimpuesto sino una restricción real externa o un guardrail de seguridad no negociable: dinero real nunca se mueve solo (siempre confirmación explícita en la UI), control remoto con emparejamiento explícito (nunca backdoor silencioso), salud/legal/finanzas siempre informativo con disclaimers, cero LinkedIn, cero scraping o automatización que viole ToS, cero comandos git, cero infraestructura real aplicada por un agente, cero secretos/datos personales reales. Ver `DIRECCION_ACTUAL.md`, "Guardrails de seguridad: sin cambios, no negociables".

## v4 a v7: el resto del wishlist a código real, y luego consolidación (2026-07-09)

Esta sección quedó desactualizada desde que se escribió (se detenía en v3) — el proyecto siguió
cuatro olas más. Resumen breve, sin repetir el detalle que ya vive en `DIRECCION_ACTUAL.md`
(fuente de verdad de cada ola) y en `ARCHITECTURE.md` §13-§15 (contratos técnicos pinned):

- **v4**: ERP/inventario ligero, borradores de campañas de Ads (Meta, nunca gasta solo),
  vehículos (Smartcar — luego **eliminado del alcance de nueva inversión**, ver
  `DIRECCION_ACTUAL.md`), mensajería nativa push (APNs/FCM), control remoto "fase 2" (input de
  teclado/mouse, siempre con emparejamiento explícito).
- **v5**: RRHH/nómina (siempre borrador), viajes bring-your-own (Amadeus + AfterShip, nunca
  reserva sola), voz avanzada (clonación con consentimiento verificable), podcasts,
  notificaciones push nativas, barrido de seguridad dedicado (encontró y cerró una fuga de
  credencial real en Polly TTS).
- **v6**: reuniones (transcripción + minutas), pantalla de Analista, MCP bring-your-own
  (servidores de terceros como herramientas del agente, siempre con confirmación humana),
  control remoto en iOS/Android, interrupciones naturales en telefonía (Media Streams, beta
  opt-in) — y varios hallazgos reales de cumplimiento (evidencia legal perdida en rollback,
  esquema de `reuniones.py` desincronizado de la migración real).
- **v7**: sin verticales nuevas a propósito — 11 barridos de consolidación que releyeron todo
  lo anterior contra los patrones de bug ya conocidos (fuga BYO, plan-flag-bypass, evidencia en
  rollback, esquema asumido) y cerraron los que seguían abiertos: una carrera de datos en RRHH,
  cuota fail-open en 4 routers, evidencia/estado perdido en los handlers de misiones/
  automatizaciones del worker si el proceso muere a mitad de camino, un bug que rompía el
  arranque de Postgres embebido real en la app de escritorio, y `docs/api.md` re-sincronizado
  programáticamente contra el código real. Detalle completo: `DIRECCION_ACTUAL.md` ("v7
  completado"), `HOTFIXES_PENDIENTES.md` ("Barrido v7"), y los 11 informes listados en la
  sección "v7" de [`index.md`](./index.md).

Con esto, el wishlist original de `REQUISITOS_V2.md` (~30 categorías) está casi agotado en
código real — lo que queda deliberadamente fuera (vehículos como inversión nueva, tier
dedicado operado por el dueño del proyecto, algunos conectores de "Más conectores" abajo) es
por decisión de producto, no por falta de tiempo de construcción.

## Tier dedicado (Enterprise)

Ya anticipado en el diseño (`ARCHITECTURE.md` §2: *"Tier «dedicado» futuro: mismo código, stack Terraform separado por cliente"*). Con el pivote a bring-your-own de `PLAN.md`, la primera capa de esto ya existe hoy como **"Tu propia nube"**: el cliente despliega en SU PROPIA cuenta de AWS con el Terraform de `infra/terraform/`, como servicio de instalación/mantenimiento cotizado — no infraestructura compartida operada por el dueño del proyecto. Lo que queda como roadmap genuino más allá de eso es un tier donde el dueño del proyecto SÍ opera la infraestructura aislada por cliente (para quien no quiera ni siquiera administrar su propia cuenta de AWS): un proceso de aprovisionamiento repetible (Terraform parametrizado por cliente, no una copia manual), una política de versionado/actualización independiente por cliente, y un modelo de soporte y SLA propio. Al igual que el hosted multi-tenant compartido (`PLAN.md`), se prioriza solo si aparece demanda comercial concreta — no de forma especulativa.

## Más conectores

El patrón `Connector` (`ARCHITECTURE.md` §10.8) ya está diseñado para agregar proveedores sin tocar el contrato central — cada conector nuevo es un módulo más en `packages/connectors/`, registrado en `CONNECTORS`, con su propio `OAuthSpec` y scopes mínimos documentados como los de [`conectores.md`](./conectores.md). WhatsApp Business Platform ya pasó de "candidato" a construcción real de v3 (ver sección de arriba). Candidatos que siguen sin fecha comprometida:

- **Calendarios/tareas adicionales** (p. ej. proveedores de agenda o gestión de tareas de terceros) más allá de Google/Microsoft, para usuarios que no viven en ninguno de esos dos ecosistemas.
- **Open finance / agregadores bancarios con API oficial** en mercados donde exista un estándar regulado (relevante para profundizar el caso de uso "CFO personal" de [`personalizacion-nivel-dios.md`](./personalizacion-nivel-dios.md) con datos bancarios reales en vez de solo registro manual de transacciones) — solo si existe una vía 100% API oficial con consentimiento del usuario final, nunca *screen scraping* de banca en línea.
- **Notion/herramientas de productividad de equipo** (Slack ya está implementado — ver [`mensajeria.md`](./mensajeria.md), WP-V2-05), para el caso de uso de micro-negocios y agencias descrito en `PLAN.md`.
- **Vehículos** (Tesla Fleet API, Smartcar) y **control del teléfono** (Android `NotificationListenerService`, iOS solo vía App Intents/Shortcuts) — documentados en `ROADMAP_V2.md` §6.3, siguen sin entrar a esta ola.

Lo que **no** cambia con este roadmap: la regla de "solo APIs oficiales, cada tenant trae sus propias credenciales" (`ARCHITECTURE.md` §0.3) aplica igual a cualquier conector futuro, y **LinkedIn sigue excluido permanentemente** sin importar cuántos conectores nuevos se agreguen — ver [`conectores.md`](./conectores.md) sección "Integraciones excluidas" y [`cumplimiento/tos-redes.md`](./cumplimiento/tos-redes.md).

## Apps móviles

A diferencia de la versión anterior de este documento (que dejaba las apps móviles como "trabajo nuevo" sin arrancar), v3 arranca los proyectos reales — ver la sección "v3" de arriba y `REQUISITOS_V2.md` §6.1 para la arquitectura técnica exacta (Swift/SwiftUI nativo, Kotlin Multiplatform, nunca React Native, control remoto tipo TeamViewer/AnyDesk con emparejamiento explícito). Lo que sigue siendo trabajo incremental sobre esos proyectos, no bloqueante para la primera versión instalable:

- Manejo de push notifications nativas para recordatorios y respuestas asíncronas (hoy `reminders` se resuelve por canal `web`/`voice`/`phone`/`api`; un canal `mobile` con push nativo sería una extensión natural).
- Captura de voz nativa como alternativa (o complemento) al push-to-talk del navegador para `voice.web`.
- Sesión y almacenamiento seguro de tokens en el dispositivo (equivalente móvil de cómo el frontend web y la app de escritorio manejan el JWT hoy).
- Igual que el companion de escritorio (`apps/companion`) y la propia app de escritorio, son consumidores delgados de la misma API (`ARCHITECTURE.md` §10.12) — no un rediseño del backend.

## Marketplace de Agent Skills vs. marketplace de personas

Son dos cosas distintas, que conviene no confundir:

- **Marketplace de Agent Skills** (nuevo en v3, ver sección de arriba): capacidades/herramientas del agente (código, no configuración de personalidad), instalables desde el estándar abierto que indexa skills.sh. Ya entra en construcción esta ola.
- **Marketplace de personas** (sigue siendo roadmap futuro, sin fecha): `PLAN.md` anticipa una "galería de plantillas de personas (CFO personal, asistente ejecutivo, coach)" como parte del go-to-market comunitario. Llevar esa galería de contenido curado a un catálogo real implica:
  - Compartir una `PersonaConfig` completa (nombre, tono, formalidad, instrucciones, rasgos — ver [`personalizacion-nivel-dios.md`](./personalizacion-nivel-dios.md)) como plantilla exportable/importable, sin exponer nunca memoria ni datos del tenant que la creó.
  - Un catálogo navegable (comunidad, o curado) de plantillas por caso de uso, con posibilidad de que la comunidad contribuya las suyas.
  - Posible capa comercial encima (plantillas premium de creadores, revenue share) — a definir si se persigue, nunca como requisito para usar plantillas gratuitas en self-host o en la app de escritorio.
  - Requiere, antes que nada, una superficie de API dedicada para exportar/importar `PersonaConfig` de forma aislada (hoy `GET`/`PUT /v1/persona` operan sobre la persona activa del tenant, no sobre un catálogo) — trabajo de diseño de API pendiente, no solo de UI.

## Cómo se prioriza

Ninguno de los frentes que siguen sin fecha (tier dedicado operado por el dueño del proyecto, los conectores de "Más conectores", el marketplace de personas) tiene compromiso de fecha. La señal principal de priorización, según `PLAN.md`, es la métrica norte del negocio: conversión descarga→licencia de Edecán Escritorio, tasa de renovación de actualizaciones y attach rate de soporte prioritario — ya no "conversión self-host→hosted" (ese embudo quedó pospuesto, ver `PLAN.md`). Un conector nuevo o el marketplace de personas se priorizan si mueven esa conversión; el tier dedicado operado por el dueño del proyecto se prioriza cuando aparece demanda comercial concreta que lo exija, no de forma especulativa.
