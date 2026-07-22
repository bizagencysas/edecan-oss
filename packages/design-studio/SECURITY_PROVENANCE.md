# Seguridad y procedencia

Este paquete es una adaptación limpia de patrones genéricos observados en un
repositorio local proporcionado por el mantenedor. El repositorio fuente no
declaraba una licencia en su raíz; por eso no se copiaron archivos, plantillas,
prompts, estilos ni assets de forma literal. La implementación nueva está
escrita en Python contra los contratos públicos Apache-2.0 de Edecán.

La única superficie inspeccionada está fijada, con SHA-256, en
[`PORTING_MANIFEST.json`](./PORTING_MANIFEST.json). Todo lo demás quedó fuera
por defecto. La denylist excluye expresamente `.env*`, autenticación, bases de
datos, storage privado, tokens, secretos, credenciales, clientes/adaptadores de
proveedor, APIs, MCP, scripts, despliegue y SQL. No se importaron configuraciones
ni valores desde el source.

## Frontera de ejecución

- El paquete no elige ni invoca un proveedor o modelo. El agente de Edecán ya
  conectado produce el HTML como argumento de una tool.
- Todo HTML pasa por una allowlist de tags/atributos. Se eliminan scripts,
  handlers `on*`, formularios, frames, objetos, URLs externas, `@import` y CSS
  activo. Se inyecta CSP sin red, scripts, frames, formularios ni objetos.
- La vista previa que aparece en chat es PNG, no HTML ejecutable. Chromium, si
  existe, corre con JavaScript apagado y aborta toda solicitud externa; si no,
  el renderer portable produce un PNG/PDF real y marca `renderer=portable`.
- Las versiones son inmutables y viven bajo
  `tenants/{tenant_id}/design-studio/...` en el mismo object store privado de
  Edecán. Los exports pasan por `subir_archivo`, que crea los archivos privados
  normales del tenant.
- Refinar exige `base_version_id`. Si ya existe otra versión, no sobrescribe:
  pide recuperar la actual y reintentar.

## Límites actuales

El fallback portable conserva dimensiones y contenido legible, pero no promete
fidelidad CSS pixel-perfect. La fidelidad completa requiere el extra
`edecan-design-studio[browser-render]` y Chromium. El historial devuelve hasta
200 versiones por artefacto en este incremento; las versiones anteriores siguen
inmutables en storage. La operación compare-and-save evita conflictos
secuenciales y nunca destruye versiones, pero un futuro incremento puede sumar
un índice transaccional para coordinación multi-región estricta.
