# Roadmap público

Este roadmap comunica prioridades, no fechas ni compromisos comerciales. Una
capacidad se considera terminada solo cuando tiene implementación, pruebas,
documentación y un flujo verificable; el tamaño del código o un mockup no son
evidencia de disponibilidad.

## Estado actual: hacer confiable la base pre-1.0

Prioridades de la serie 0.x:

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

### 1. Release reproducible de escritorio

- pipeline de build para macOS y Windows;
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

1. Seguridad, privacidad y pérdida de datos antes que amplitud.
2. Un flujo completo y verificable antes que otra superficie parcial.
3. Compatibilidad hacia atrás salvo razón de seguridad documentada.
4. Provider abstractions y configuración remota antes que hardcode.
5. Integraciones oficiales y credenciales del usuario; nunca scraping ni
   credenciales compartidas.
6. Acciones con dinero, mensajería o control de dispositivos conservan
   aprobación humana y gates server-side.

## Fuera de compromiso

No hay fecha prometida para una versión 1.0, un servicio hosted, tiendas
móviles, integraciones concretas nuevas ni capacidades comerciales externas.
Las propuestas se discuten en issues públicos y se aceptan según impacto,
riesgo, mantenibilidad y disponibilidad real de contribuidores.
