# Edecán Design Studio

Design Studio es una capacidad detrás del chat, no una aplicación paralela.
Una persona puede decir “crea una landing para mi evento”, ver una previsualización
PNG en la conversación, pedir “haz el título más claro” y exportar la versión
elegida como HTML, PNG o PDF.

El vertical slice incluye cinco tools descubiertas automáticamente mediante
`edecan.tools`:

- `crear_diseno_visual`: sanea HTML, crea el artefacto y su primera versión,
  sube HTML + preview PNG privada y muestra el PNG en chat.
- `obtener_diseno_visual`: devuelve al agente el HTML actual antes de editar.
- `refinar_diseno_visual`: guarda una versión nueva con control optimista; no
  pisa la anterior.
- `historial_diseno_visual`: lista versiones, padres, fecha, resumen y SHA-256.
- `exportar_diseno_visual`: materializa HTML, PNG y/o PDF como archivos privados.

No hay SDK de modelos ni nombres de proveedores. La calidad del diseño viene del
modelo que la persona ya conectó a Edecán; este módulo se ocupa de seguridad,
versionado, render y archivos.

## Desarrollo

```bash
uv sync --all-packages --frozen
uv run --all-packages pytest packages/design-studio/tests -q
uv run ruff check packages/design-studio
```

Chromium es opcional. Para renders CSS de alta fidelidad:

```bash
uv sync --package edecan-design-studio --extra browser-render
uv run playwright install chromium
```

Sin ese extra, PNG y PDF siguen funcionando mediante el renderer portable. La
procedencia y la frontera de seguridad están en
[`SECURITY_PROVENANCE.md`](./SECURITY_PROVENANCE.md).
