# Capacidades de FyDesign Studio

Este catálogo es el contrato público del motor distribuido. La capa MCP expone
36 operaciones ejecutables y el adaptador de Edecán agrega
`fydesign_health`, para un total exacto de 37.

## Niveles y confirmación

- **safe**: lectura, planeación, análisis o render local acotado. Se puede
  ejecutar sin la confirmación reforzada de una herramienta peligrosa.
- **premium**: puede consumir un proveedor de pago, generar varios artefactos
  o cambiar estado persistente. Edecán la presenta como acción peligrosa y
  exige confirmación antes de ejecutarla.

"Local" significa que esa acción no llama por sí sola a un proveedor externo.
Cuando interviene un modelo o una API, disponibilidad y costo dependen de la
conexión elegida por la persona; el motor no promete precios ni gratuidad.

## Matriz exacta

| Capacidad | Nivel | Qué hace | Proveedor o dependencia | Costo de proveedor | Confirmación |
|---|---|---|---|---|---|
| `fydesign_ad_engine` | premium | Planea nichos y, opcionalmente, genera un video por nicho | Proveedor de texto; Muapi si `generate=true` | Plan/API; variable por videos | Sí |
| `fydesign_ambassador` | premium | Genera una serie con una persona persistida | Proveedores de texto e imagen | Plan/API más API por imagen | Sí |
| `fydesign_analyze_video` | safe | Extrae fotogramas y deconstruye un video | ffmpeg, yt-dlp para URL y proveedor con visión | Plan/API por análisis | No |
| `fydesign_angles` | premium | Crea otros ángulos de una imagen | Muapi | API por variante | Sí |
| `fydesign_animate` | premium | Anima, recastea o interpola referencias | Muapi | API por video | Sí |
| `fydesign_autoroute` | safe | Recomienda el motor apropiado sin generar | Proveedor de texto configurado | Plan/API por análisis | No |
| `fydesign_batch` | premium | Genera variaciones en paralelo | Proveedores de texto e imagen | Plan/API más API por variante | Sí |
| `fydesign_brands` | safe | Lista las marcas del tenant | Almacén JSON local privado | Local | No |
| `fydesign_campaign` | premium | Produce una campaña coherente de varias piezas | Proveedores configurados y Chromium | Plan/API más API por pieza | Sí |
| `fydesign_clipper` | safe | Corta un video largo en clips verticales | ffmpeg, yt-dlp y proveedor de texto cuando hay transcript | Plan/API opcional | No |
| `fydesign_edit` | premium | Edita una imagen mediante image-to-image | Muapi | API por variante | Sí |
| `fydesign_generate` | safe | Renderiza anuncio o carrusel on-brand en PNG | Proveedor de texto y Chromium local | Plan/API; sin API de imagen | No |
| `fydesign_health` | safe | Verifica instalación, catálogo y runtime | Adaptador local, Node y MCP | Local | No |
| `fydesign_image` | premium | Genera imágenes reales on-brand | Vertex, Muapi, OpenAI o fal.ai | API por imagen | Sí |
| `fydesign_influencer` | premium | Registra, lista o usa una persona reutilizable | Estado local; Muapi al generar | Local al registrar/listar; API al generar | Sí |
| `fydesign_instadump` | premium | Crea variaciones de tendencia desde un retrato | Muapi | API por variante | Sí |
| `fydesign_instant` | premium | Planea o genera una suite desde URL/referencias | Proveedores configurados; Chromium para sitio | Plan/API; variable al renderizar | Sí |
| `fydesign_marketplace_card` | premium | Compone fichas de marketplace sin inventar datos | Proveedor de imagen y Chromium | API por composición | Sí |
| `fydesign_moodboard` | premium | Destila y guarda un estilo desde referencias | Proveedor con visión y estado local | Plan/API por análisis | Sí |
| `fydesign_photo_dump` | premium | Genera una tanda consistente sin guardar persona | Muapi | API por imagen | Sí |
| `fydesign_photodump` | premium | Produce hasta 26 fotos de una persona persistida | Muapi | API por imagen | Sí |
| `fydesign_post` | premium | Entrega un post final con imagen, texto y marca | Proveedores configurados y Chromium | Plan/API más API por imagen | Sí |
| `fydesign_product_ad` | premium | Genera un anuncio de video de producto | Proveedores configurados, ffmpeg y almacenamiento mediado | Plan/API más API por video/audio | Sí |
| `fydesign_product_photoshoot` | premium | Produce una sesión curada de producto | Proveedor de imagen y Chromium | API por imagen | Sí |
| `fydesign_product_shots` | premium | Compone tomas hero conservando el producto | Proveedor de imagen | API por imagen | Sí |
| `fydesign_refine` | safe | Aclara y reescribe un brief | Proveedor de texto configurado | Plan/API por análisis | No |
| `fydesign_register_brand` | premium | Crea o actualiza una marca del tenant | Almacén JSON local privado | Local | Sí |
| `fydesign_storyboard` | premium | Genera frames coherentes de una secuencia | Proveedores de texto e imagen | Plan/API más API por frame | Sí |
| `fydesign_strategy` | premium | Investiga y genera una campaña completa | Proveedores configurados y Chromium | Plan/API más API por pieza | Sí |
| `fydesign_studio` | premium | Ejecuta edición especializada de imagen | Muapi | API por edición | Sí |
| `fydesign_svg` | safe | Genera un vector SVG on-brand | Proveedor de texto configurado | Plan/API; sin API de imagen | No |
| `fydesign_talking_head` | premium | Produce voz y lipsync de una persona | Muapi y GCS para intercambio de medios | API por audio y video | Sí |
| `fydesign_train_face` | premium | Entrena una identidad LoRA desde referencias | Muapi y GCS, o URL de ZIP explícita | API por entrenamiento | Sí |
| `fydesign_upscale` | premium | Escala una imagen o video | Muapi | API por archivo | Sí |
| `fydesign_video` | premium | Genera un clip texto-a-video | Proveedores de texto y video | Plan/API más API por video | Sí |
| `fydesign_video_ad` | premium | Produce un anuncio con tomas, overlays y audio | Proveedores configurados, ffmpeg; TTS/música opcionales | Plan/API más API por toma/audio | Sí |
| `fydesign_virality` | safe | Puntúa y mejora un concepto antes de renderizar | Proveedor de texto configurado | Plan/API por análisis | No |

## Capa B: motor creativo de proyectos locales

La matriz anterior describe la superficie conversacional de medios. El comando
local `scripts/fydesign-project.ts`, usado por las herramientas conversacionales
de proyectos de Edecán, mantiene detrás del chat un flujo local con 22 acciones:
`health`, `list`, `create`, `edit`, `read`, `render`, `history`, `variants`,
`duplicate`, `brand-health`, `tidy`, `archive`, `restore`, `export`,
`template-list`, `template-save`, `template-create`, `design-system-list`,
`design-system-generate`, `corpus-ingest`, `corpus-search` y `share-package`. La persona no tiene
que conocer estos nombres: pide el resultado en lenguaje normal y Edecán elige
la operación interna. En la ruta probada participan realmente:

- recuperación de patrones, corpus semilla y memoria de diseño persistente;
- dirección artística, watchdog, generación, validación y self-healing;
- crítica visual y una revisión opcional cuando la crítica vale la pena;
- entre una y cuatro variantes, revisiones inmutables, duplicación y tablero
  estático sin JS;
- recreación de pantallas y composición de mockups cuando el brief incluye
  `screenBriefs`;
- referencias visuales privadas mediadas por Edecán, sin exponer rutas del host;
- diagnóstico de coherencia de marca, orden reversible, plantillas locales,
  sistemas de diseño versionados, importación explícita de repositorios públicos
  `owner/repo` y búsqueda acotada en el corpus;
- export HTML/PNG/PDF y paquete privado de revisión, sin publicar por cuenta
  propia, mediante Chromium con la red bloqueada.

El resto del inventario conservado bajo `src/lib` sigue disponible para una
integración futura, pero su mera presencia no se declara como una ruta de
producto. El flujo distribuido reemplaza la persistencia SQL y los orquestadores
de la interfaz web original por estado local privado, revisiones acotadas y
operaciones mediadas por Edecán. `screen-simulator.ts` se conserva, pero la ruta
probada de recreación usa `screen-recreator.ts` y `mockup-html-generator.ts`.

El inventario auditado del working tree entregado por el propietario, basado en
la revisión `4e5d6a8`, contiene 131 módulos TypeScript. El manifiesto distingue
expresamente los archivos modificados o todavía no versionados en esa revisión,
por lo que el commit se usa como base y no como una afirmación falsa de árbol
limpio. Esta distribución incorpora 125 módulos en la misma ruta,
reemplaza las fronteras de base de datos/registro con implementaciones OSS y
agrega módulos pequeños para registro local, seguridad de artefactos, runtime,
red y composición ejecutable del pipeline. El manifiesto enumera esos módulos
y sus hashes exactos. Solo se excluyen los seis módulos siguientes:

| Módulo upstream no distribuido | Clasificación | Motivo |
|---|---|---|
| `src/lib/auth.ts` | SaaS / autenticación | Acopla sesiones y usuarios de la app web; Edecán aplica su propia identidad y tenant. |
| `src/lib/brands/society-creators.ts` | Datos privados | Contiene identidad de una marca propietaria; el registro OSS comienza vacío. |
| `src/lib/browser-db.ts` | SaaS / persistencia | Expone almacenamiento del navegador de la app; Edecán usa estado privado por tenant. |
| `src/lib/design-system/utils.ts` | Solo UI | Utilidad de la interfaz Next; no participa en el runtime creativo distribuido. |
| `src/lib/github-token.ts` | Credenciales | Gestiona token web directamente; Edecán media GitHub mediante su vault. |
| `src/lib/web-engine.ts` | Superficie web no mediada | Orquestador ligado a la app web y su red; la ejecución OSS pasa por el broker local acotado. |

La cobertura de fuente no equivale a cobertura funcional. Las rutas ejecutables
y sus límites se enumeran arriba; el manifiesto machine-readable fija el
conjunto y sus hashes para impedir que una exclusión futura quede oculta.

## Límites del contrato

- `premium` describe el límite de autorización de Edecán, no garantiza que
  toda variante vaya a facturar. Por ejemplo, `fydesign_ad_engine` puede
  devolver solo el plan y `fydesign_instant` admite `dryRun`.
- Una dependencia ausente produce un error accionable. Nunca se presenta un
  placeholder como un artefacto generado.
- Las claves se entregan por la allowlist del adaptador; este documento no
  contiene ni prescribe valores de credenciales.
- La importación de corpus funciona con el límite público de GitHub. Un token
  opcional puede guardarse cifrado desde el chat de Edecán y solo se entrega al
  proceso acotado para esa operación; nunca se persiste en el corpus ni en el repo.
- Los archivos del usuario se median por el almacenamiento privado de Edecán.
  El MCP es una frontera de motor, no una autorización para leer rutas
  arbitrarias del host.
- Las descargas remotas validan destino, redirects y la IP usada por el socket;
  bloquean IPs privadas/reservadas. Auto-brand renderiza HTML ya descargado sin
  navegación ni subrecursos, y `yt-dlp` solo recibe plataformas/rutas canónicas
  admitidas por el contrato.
