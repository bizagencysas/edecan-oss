# packages/creative — `edecan_creative`

Creatividad: generación de imágenes y documentos de oficina (Word/PowerPoint/PDF),
guardados como archivos del tenant en S3 + tabla `files` (`ARCHITECTURE.md` §10.14,
`ARCHITECTURE.md` §11 — fase v2). Cada herramienta implementa el contrato `Tool` de
`edecan_core` (§10.7): `name`, `description`, `input_schema`, `requires_flags`,
`dangerous` y `async run(ctx, args)`.

`get_all_tools() -> list[Tool]` (en `edecan_creative/__init__.py`) es el entry point que
consume `edecan_core.ToolRegistry.load_entry_points(group="edecan.tools")`, declarado en
`pyproject.toml` como `[project.entry-points."edecan.tools"]`.

## Las 4 herramientas (nombres exactos, pinned en `ARCHITECTURE.md` §11)

| Tool | Módulo | Flag | `dangerous` | Genera |
|---|---|---|---|---|
| `generar_imagen` | `tools.py` | `tools.images` | No | PNG vía `ImageProvider` intercambiable |
| `crear_documento` | `tools.py` | — | No | `.docx` (python-docx) |
| `crear_presentacion` | `tools.py` | — | No | `.pptx` (python-pptx) |
| `crear_pdf` | `tools.py` | — | No | `.pdf` (fpdf2) |

Ninguna es `dangerous`: no publican nada ni actúan sobre servicios externos del
usuario — solo generan un archivo y lo guardan como privado del tenant (prefijo S3
`tenants/{tenant_id}/...`, `ARCHITECTURE.md` §2). Todas devuelven
`data={"file_id", "filename"}` y un `content` en español listo para el chat.

## Módulos

- **`providers.py`** — `ImageProvider` (protocolo `Protocol`, `async generate(prompt,
  size="1024x1024") -> bytes` PNG). Implementaciones:
  - `StubImageProvider` (default, `IMAGES_PROVIDER=stub`): determinista y 100% offline.
    El color de fondo sale de `sha256(prompt)` (mismo prompt → mismos bytes siempre) y el
    prompt (truncado) se dibuja como texto envuelto, con la fuente bitmap por defecto de
    Pillow — sin descargar ninguna fuente.
  - `OpenAICompatImagesProvider` (`IMAGES_PROVIDER=openai_compat`): `POST
    {IMAGES_BASE_URL}/images/generations` con `Bearer IMAGES_API_KEY`, cuerpo
    `{model: IMAGES_MODEL, prompt, size, response_format: "b64_json"}`; decodifica el
    `b64_json` de la respuesta.
  - `get_image_provider(settings)` resuelve el proveedor activo leyendo `settings` de
    forma defensiva (`getattr(settings, "CAMPO", default)`) — nunca revienta si falta un
    campo, y cae a `StubImageProvider` con `logging.warning` si falta configuración,
    mismo patrón que `edecan_voice.registry.get_stt/get_tts`.
- **`_files.py`** *(privado)* — `subir_archivo(ctx, *, data, filename, mime) ->
  (file_id, filename)`: sube `data` a `s3://$S3_BUCKET/tenants/{tenant_id}/files/{file_id}/{filename}`
  (aioboto3, mismo layout que `apps/api/edecan_api/routers/files.py`) e inserta la fila en
  `files` con `status="ready"` directamente (un archivo generado ya está completo — a
  diferencia de una subida del usuario, no pasa por el job async `ingest_file`).
- **`tools.py`** — las 4 `Tool`. Cada una recibe su `uploader` (y `GenerarImagenTool`
  además su `image_provider`) por parámetro de constructor con el default real
  (`subir_archivo` / `get_image_provider(ctx.settings)`) — patrón inyectable para que los
  tests sustituyan S3/Postgres/el proveedor de imágenes por un doble en memoria sin tocar
  red ni base de datos.

## Límites de tamaño

Para no generar archivos absurdos: máximo 100 elementos en cualquier lista de nivel
superior (`secciones`, `diapositivas`, `parrafos` del PDF), máximo 200 elementos en listas
anidadas (párrafos por sección, bullets por diapositiva), títulos/encabezados acotados a
300 caracteres y cualquier otro string a 4000 caracteres. Un argumento fuera de rango se
recorta silenciosamente en vez de fallar (mismo criterio que `edecan_toolkit._util.clamp_int`).

## Decisiones de implementación

- **`crear_pdf` y Unicode**: fpdf2 con fuente core (Helvetica) solo soporta el charset
  latin-1 (ISO-8859-1). Para evitar tener que descargar y empaquetar una fuente TrueType
  (regla dura: nada de red al generar un archivo), el texto se sanea con
  `texto.encode("latin-1", errors="replace").decode("latin-1")` antes de escribirlo: los
  acentos/eñe de español pasan intactos (están en latin-1), pero emojis y símbolos
  tipográficos fuera de ese charset (comillas curvas, guiones largos, etc.) se sustituyen
  por `'?'`. Ver la limitación completa en `docs/creatividad.md`.
- **`crear_presentacion`**: la portada usa el layout 0 (`"Title Slide"`) del template por
  defecto de python-pptx; cada diapositiva de contenido usa el layout 1 (`"Title and
  Content"`, placeholder de cuerpo en el índice 1). El conteo que reporta `content` es el
  de diapositivas de *contenido* (sin contar la portada).
- **Nombres de archivo**: `_slug(titulo)` deriva un nombre de archivo seguro (ASCII,
  minúsculas, `-` como separador) normalizando acentos con NFKD en vez de descartar la
  palabra completa (`"Métricas Q3"` → `metricas-q3.docx`).
- **Sin `edecan_db.models`**: igual que `edecan_toolkit` (ver su README), `_files.py`
  habla SQL parametrizado directo (`sqlalchemy.text()`) contra el esquema pinneado en
  `ARCHITECTURE.md` §10.3 (tabla `files`) sobre `ctx.session`, en vez de acoplarse a una
  forma de modelos ORM que el contrato no fija.
- **Sin `usage_events` propio**: igual que el resto de `edecan_toolkit`, esta capa no
  registra su propio evento de medición — es responsabilidad de la capa que orquesta el
  turno (`edecan_api`), consistente con que ninguna otra `Tool` del repo lo hace desde
  dentro de `run()`.

## Tests

```
uv run pytest packages/creative
```

`tests/conftest.py` define fakes locales por duck typing (`FakeSession`, `FakeUploader`,
`ctx` como `SimpleNamespace`) — no importa `edecan_db` ni `edecan_api` (`ARCHITECTURE.md`
§10.1).

| Archivo | Cubre |
|---|---|
| `test_providers.py` | `StubImageProvider` (determinismo mismo prompt/tamaño, cabecera PNG válida, tamaño respetado, clamp de `_parse_size`) y `OpenAICompatImagesProvider` (con `respx`, sin red real: request/headers/body, decodificación de `b64_json`, error si falta, error HTTP) más `get_image_provider` (fallback a stub). |
| `test_files.py` | `_files.subir_archivo` con `aioboto3.Session` sustituido por `monkeypatch` (mismo patrón que `apps/api/tests/test_files.py`): verifica el `Bucket`/`Key`/`Body`/`ContentType` del `put_object` y los parámetros del `INSERT INTO files ... status='ready'`, más el fallback a `S3_BUCKET`/`AWS_REGION` por defecto cuando `settings` no los trae. |
| `test_tools.py` | Las 4 tools con un `FakeUploader` inyectado por constructor: genera cada tipo de archivo y lo vuelve a abrir con la misma librería (`python-docx`/`python-pptx`) para confirmar estructura real (títulos, párrafos, bullets, número de diapositivas), o valida la cabecera `%PDF`/PNG. Cubre además validación de argumentos faltantes/vacíos y el tope de 100 elementos en listas de nivel superior. |
| `test_catalogo.py` | `get_all_tools()` devuelve los 4 nombres pinned sin duplicados, ninguna tool es `dangerous`, solo `generar_imagen` declara `requires_flags = {"tools.images"}`, y cada `input_schema` es válido. |
