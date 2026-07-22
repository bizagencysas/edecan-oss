# Seguridad y procedencia

Studio tiene dos capas con procedencia distinta y explícita:

1. Este paquete Python contiene el lienzo local, saneamiento, versionado,
   exports y adaptadores humanos de Edecán. Esa capa fue escrita como una
   adaptación limpia de cinco contratos de producto fijados con SHA-256 en
   [`PORTING_MANIFEST.json`](./PORTING_MANIFEST.json).
2. [`packages/fydesign-engine`](../fydesign-engine) contiene el motor FyDesign
   autorizado expresamente por su propietario para ser incluido y relicenciado
   bajo Apache-2.0. Su manifiesto separado registra el working tree entregado,
   la revisión Git usada solo como base, los archivos modificados o no
   versionados que sí fueron revisados, los 125 módulos conservados en la misma
   ruta, adaptaciones OSS explícitas, seis exclusiones justificadas y el hash
   de cada archivo distribuido.

Ninguna de las dos capas importó `.env`, claves, bases de datos, archivos de
clientes, identidades de marcas privadas, historial Git, builds o dependencias
instaladas del repositorio fuente. Las seis exclusiones del motor sustituyen la
autenticación, persistencia y credenciales de la antigua SaaS por las fronteras
de identidad, tenant y `TokenVault` de Edecán.

## Frontera de ejecución

- La capa de lienzo Python no elige proveedor. El motor TypeScript solicita
  razonamiento por un puente efímero autenticado hacia el LLM que la persona ya
  conectó en Edecán. El proceso Node recibe la URL y un token aleatorio de corta
  vida, nunca las credenciales del modelo de texto.
- Las APIs especializadas de imagen, video, voz o almacenamiento solo reciben
  variables de una allowlist obtenida desde `TokenVault`. El proceso no hereda
  el entorno completo del backend ni lee `.env`.
- Los trabajos con costo externo se separan de las operaciones locales y
  requieren confirmación. Crear o editar una revisión local y reversible no la
  requiere.
- Todo HTML pasa por una allowlist de tags/atributos. Se eliminan scripts,
  handlers `on*`, formularios, frames, objetos, URLs externas, `@import` y CSS
  activo. Se inyecta CSP sin red, scripts, frames, formularios ni objetos.
- Playwright corre con JavaScript apagado y aborta toda solicitud externa. La
  app empaquetada incluye su propio Node 22 y Chromium verificados, por lo que
  no depende del Node o Chrome del usuario. Si un entorno de desarrollo no
  tiene navegador, se usa el renderer portable y se marca
  `renderer=portable`.
- Las URLs remotas cruzan dos controles. La tool Python hace el filtro inicial
  y el guard TypeScript valida cada destino y cada redirect, bloquea rangos
  privados/reservados y fija al abrir el socket la resolución DNS que ya fue
  autorizada. El análisis por `yt-dlp` solo acepta rutas canónicas de
  YouTube, TikTok, Vimeo o Instagram, o un adjunto local mediado por Edecán.
- Auto-brand no navega Chromium a la URL recibida: renderiza únicamente el HTML
  que ya descargó el fetch protegido y aborta todos sus subrecursos. Así, una
  redirección, subrecurso o cambio de DNS no crea un segundo camino hacia la
  red local o metadata cloud.
- El HTML entregado puede abrirse dentro de Edecán, pero la WebView/iframe lo
  aísla sin scripts ni origen compartido y vuelve a inyectar una CSP sin red.
  El PNG continúa siendo la vista previa visual primaria.
- Las versiones son inmutables y viven bajo
  `tenants/{tenant_id}/design-studio/...` en el mismo object store privado de
  Edecán. Los exports pasan por `subir_archivo`, que crea los archivos privados
  normales del tenant.
- Refinar exige `base_version_id`. Si ya existe otra versión, no sobrescribe:
  pide recuperar la actual y reintentar.

## Límites honestos

El fallback portable conserva dimensiones y contenido legible, pero no promete
fidelidad CSS pixel-perfect. La fidelidad completa requiere Chrome/Chromium
local o el extra `edecan-design-studio[browser-render]`. El historial devuelve hasta
200 versiones por artefacto en este incremento; las versiones anteriores siguen
inmutables en storage. La operación compare-and-save evita conflictos
secuenciales y nunca destruye versiones, pero un futuro incremento puede sumar
un índice transaccional para coordinación multi-región estricta.

Las capacidades que generan imagen o video necesitan la API o plan del
proveedor elegido por la persona. Si falta una dependencia o credencial, Studio
falla con un diagnóstico accionable y nunca presenta un placeholder como
resultado real.
