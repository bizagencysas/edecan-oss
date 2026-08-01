# Roadmap público

Este roadmap comunica prioridades, no fechas ni compromisos comerciales. Una
capacidad se considera terminada solo cuando tiene implementación, pruebas,
documentación y un flujo verificable; el tamaño del código o un mockup no son
evidencia de disponibilidad.

## Estado actual: hacer confiable la base pre-1.0

Prioridades de la serie 0.x:

- demostrar el contrato assistant-first de punta a punta: una petición por
  texto o voz, una conversación, varias acciones y un resultado verificable;
- mantener solo Edecan, Actividad y Ajustes como navegación primaria, con las
  capacidades especializadas detrás del asistente;
- conservar y reanudar de forma segura las órdenes compuestas que necesitan
  una confirmación humana;
- diagnosticar límites desde la conversación y recuperar por configuración,
  skill local o reparación reversible del núcleo;
- instalación reproducible desde un clon limpio en macOS, Linux y WSL2;
- CI público para Python, web, Rust, iOS y Android;
- releases de escritorio firmados, con checksums, SBOM y procedencia;
- migraciones y upgrades seguros para self-host;
- observabilidad sin PII y runbooks probados;
- eliminar referencias internas y mantener documentación pública autocontenida;
- pruebas end-to-end de los flujos críticos, además de las suites unitarias.

## Capacidades implementadas que necesitan endurecimiento

Estas superficies tienen código real, pero siguen sujetas al estatus pre-1.0:

- agente con tools tipadas, memoria y perfil vivo;
- automations, recordatorios y misiones multi-agente;
- documentos, análisis tabular, imágenes, video y reuniones;
- conectores oficiales y credenciales bring-your-own por tenant;
- voz web, mensajería, browser de investigación, MCP y skills;
- negocios, RRHH, comercio simulado, viajes, vehículos y Home Assistant;
- web app, companion local, shell Tauri y clientes nativos iOS/Android.

El trabajo transversal incluye pruebas E2E, accesibilidad, rendimiento,
recuperación ante fallos, aislamiento multi-tenant y paridad entre clientes.

## Próximos hitos

### 0. Asistente completo, no catálogo de módulos

- recorrido E2E de la frase de referencia: pendientes + correo + documento +
  recordatorio en una sola conversación;
- misma semántica, confirmaciones y evidencia desde texto y voz;
- Actividad como resumen único de trabajo, aprobaciones y fallos;
- recuperación conversacional después de “haz que se pueda”;
- pruebas de reinicio durante una confirmación y durante una reparación local;
- lenguaje cotidiano en toda la interfaz; nombres de herramientas, planes y
  arquitectura solo en el modo avanzado o la documentación técnica.

El criterio de salida está en
[`producto-assistant-first.md`](./producto-assistant-first.md). Añadir otra
pantalla primaria no cuenta como progreso de producto.

### 1. Release reproducible de escritorio

- pipeline de build para macOS, Windows y Linux x64;
- firma/notarización y publicación de checksums;
- actualización segura y rollback;
- smoke test del instalador en máquina limpia;
- documentación separada para usuario final y desarrollador.

No se publicará un botón de descarga hasta que existan artefactos firmados y
probados. Mientras tanto, el desktop se construye desde source.

### 2. Self-host verificable

- smoke test del Compose completo;
- configuración con defaults de mínimo privilegio;
- reverse proxy/TLS documentado sin imponer proveedor;
- backup y restore ensayados;
- compatibilidad de migraciones entre releases;
- health/readiness útiles y guía de actualización.

### 3. Seguridad y supply chain

- private vulnerability reporting y advisories coordinados;
- CodeQL, dependency review, Dependabot y secret scanning;
- threat model actualizado por superficie;
- SBOM y artifact attestations para releases;
- revisión de permisos Tauri, Android, iOS, MCP, skills y companion;
- auditoría externa cuando el proyecto y adopción la justifiquen.

### 4. Experiencia de contribución

- issues reproducibles con etiquetas `good first issue` y `help wanted`;
- ADRs para decisiones que cruzan paquetes;
- changelog mantenido por release;
- documentación de contratos sin cronología interna;
- ejemplos y fakes pequeños para crear tools/proveedores nuevos;
- primera contribución externa revisada y publicada de forma transparente.

### 5. Paridad y calidad del producto

- matriz de flujos compartidos web/iOS/Android;
- manejo uniforme de estados vacíos, errores y reintentos;
- accesibilidad de teclado, lector de pantalla y contraste;
- presupuestos de latencia, memoria y tamaño de bundle;
- telemetría opt-in, privada y separada por superficie;
- demo local reproducible sin servicios pagados.

## Principios de priorización

1. Una intención completa en la conversación antes que otra pantalla o módulo.
2. Seguridad, privacidad y pérdida de datos antes que amplitud.
3. Un flujo completo y verificable antes que otra superficie parcial.
4. Compatibilidad hacia atrás salvo razón de seguridad documentada.
5. Provider abstractions y configuración remota antes que hardcode.
6. Integraciones oficiales y credenciales del usuario; nunca scraping ni
   credenciales compartidas.
7. Acciones con dinero, mensajería o control de dispositivos conservan
   aprobación humana y gates server-side.

## Fuera de compromiso

No hay fecha prometida para una versión 1.0, un servicio hosted, tiendas
móviles, integraciones concretas nuevas ni capacidades comerciales externas.
Las propuestas se discuten en issues públicos y se aceptan según impacto,
riesgo, mantenibilidad y disponibilidad real de contribuidores.
