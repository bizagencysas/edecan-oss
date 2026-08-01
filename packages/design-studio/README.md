# Edecán Design Studio

Design Studio es una capacidad detrás del chat, no una aplicación paralela.
Una persona puede decir “crea una landing para mi evento”, ver una previsualización
PNG en la conversación, pedir “haz el título más claro” y exportar la versión
elegida como HTML, PNG o PDF.

El paquete incluye doce tools descubiertas automáticamente mediante
`edecan.tools`. Seis forman la capa portable de lienzos:

- `crear_diseno_visual`: sanea HTML, crea el artefacto y su primera versión,
  sube HTML + preview PNG privada y muestra el PNG en chat.
- `crear_coleccion_visual`: crea hasta ocho lienzos de un carrusel, campaña o
  presentación en una sola petición; cada uno queda versionado por separado.
- `obtener_diseno_visual`: devuelve al agente el HTML actual antes de editar.
- `refinar_diseno_visual`: guarda una versión nueva con control optimista; no
  pisa la anterior.
- `historial_diseno_visual`: lista versiones, padres, fecha, resumen y SHA-256.
- `exportar_diseno_visual`: materializa HTML, PNG y/o PDF como archivos privados.

Las otras seis conectan el motor creativo completo sin obligar a la persona a
escoger módulos técnicos:

- `ver_estudio_creativo`: comprueba el runtime y descubre el catálogo vigente.
- `usar_estudio_creativo`: recibe una petición normal y selecciona internamente
  una capacidad de lectura, planeación o render local.
- `usar_estudio_creativo_premium`: hace lo mismo con generación externa de
  imagen, video, campaña, producto o personas, siempre con confirmación.
- `ver_proyectos_creativos`: consulta proyectos, historial, variantes,
  coherencia de marca, plantillas, sistemas de diseño y corpus local; también
  puede aprender patrones de un repositorio público `owner/repo` autorizado.
- `crear_editar_proyecto_creativo`: crea o edita desde una frase y referencias
  privadas; conserva revisiones y puede exportar o preparar un paquete privado.
- `administrar_proyecto_creativo`: ordena, archiva o restaura de forma explícita
  y reversible.

La capa de proyectos ejecuta 22 acciones internas: salud, listado, creación,
edición, lectura, render, historial, variantes, duplicación, diagnóstico de
marca, orden, archivo/restauración, exportación HTML/PNG/PDF, plantillas,
sistemas de diseño, importación/búsqueda de corpus y paquete de revisión. Son memoria y
herramientas detrás del chat; no constituyen otra aplicación que la persona
deba aprender.

Para importar repositorios públicos no hace falta una credencial. Si la persona
quiere más cuota, puede entregar un token GitHub por el chat: Edecán lo cifra en
su vault y solo lo inyecta al proceso local autorizado, sin copiarlo al corpus,
al proyecto ni al repositorio OSS.

No hay un proveedor obligatorio. La calidad del diseño viene del modelo que la
persona ya conectó a Edecán; este módulo se ocupa de seguridad, versionado,
render, archivos y de entregar al modelo los esquemas reales de cada capacidad.
El precio y disponibilidad de una generación externa dependen de esa conexión;
la ejecución local no se presenta como garantía de costo cero.

## Desarrollo

```bash
uv sync --all-packages --frozen
uv run --all-packages pytest packages/design-studio/tests -q
uv run ruff check packages/design-studio
```

La aplicación de escritorio empaqueta su propio Node, Chromium, ffmpeg, ffprobe
y yt-dlp. En desarrollo, Chrome/Chromium instalado en el sistema se usa
automáticamente para renders CSS de alta fidelidad, incluso sin dependencias
Python opcionales. Playwright sigue siendo una integración soportada:

```bash
uv sync --package edecan-design-studio --extra browser-render
uv run playwright install chromium
```

Sin ese extra, PNG y PDF siguen funcionando mediante el renderer portable. La
procedencia y la frontera de seguridad están en
[`SECURITY_PROVENANCE.md`](./SECURITY_PROVENANCE.md).
