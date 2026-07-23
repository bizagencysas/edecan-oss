# Design Studio

Design Studio convierte una instrucción normal en un artefacto visual privado
sin sacar a la persona del chat. No agrega una sección técnica ni obliga a
elegir motor: usa el proveedor/modelo que Edecán ya tenga conectado.

Ejemplos de uso:

- “Crea una landing sencilla para anunciar mi taller del viernes.”
- “Haz el título de la última versión más grande y cambia el fondo a crema.”
- “Muéstrame el historial y exporta la versión dos en HTML, PNG y PDF.”

En creación y refinamiento, el modelo genera un documento HTML completo y llama
a la herramienta correspondiente. El módulo extrae el HTML aunque llegue dentro
de fences Markdown, lo sanea, guarda una versión inmutable tenant-scoped, genera
una vista previa PNG y la entrega como bloque visual del Mega Chat. HTML/PDF/PNG
se guardan mediante la infraestructura normal de archivos privados de Edecán.

El HTML exportado contiene una CSP estricta y no conserva scripts, formularios,
iframes, objetos, eventos inline ni accesos de red. La vista previa del chat es
raster, por lo que nunca ejecuta el documento generado. Ver la procedencia y los
límites exactos en
[`packages/design-studio/SECURITY_PROVENANCE.md`](../packages/design-studio/SECURITY_PROVENANCE.md).

## Incrementos posteriores

- Editor visual dedicado opcional, sin convertirlo en navegación principal.
- Índice transaccional de proyectos para búsquedas y coordinación multi-región.
- Diff visual y restauración de una versión con un clic.
- Más formatos (SVG editable, decks y paquetes web multipágina) una vez que el
  slice HTML tenga validación real de uso.
