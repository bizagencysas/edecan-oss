# packages/docanalysis — `edecan_docanalysis`

Analista total de documentos (`ARCHITECTURE.md` §11 fase v2; `analizar_video`
sumado en v3 por fase v3; `predecir_serie`/`detectar_anomalias` sumadas en v5
por fase v5): estadística sobre tablas, extracción heurística de tablas de
PDF, visión sobre imágenes, análisis de video por frames, gráficos SVG
deterministas, exportación de reportes XLSX, predicción de series (forecast)
y detección de anomalías. Cada tool implementa el contrato `Tool` de
`edecan_core` (ARCHITECTURE.md §10.7): `name`, `description`, `input_schema`,
`requires_flags` (vacío en las 8), `dangerous` (`False` en las 8) y
`async run(ctx, args)`.

`get_all_tools() -> list[Tool]` (en `edecan_docanalysis/__init__.py`) es el
entry point que consume `edecan_core.ToolRegistry.load_entry_points(group="edecan.tools")`,
declarado en `pyproject.toml` como `[project.entry-points."edecan.tools"]`.

## Las 8 herramientas (5 nombres exactos pinned en `ARCHITECTURE.md` §11 + `analizar_video` de v3 + `predecir_serie`/`detectar_anomalias` de v5)

| Módulo | Tool | Qué hace |
|---|---|---|
| `tablas.py` | `analizar_tabla` | Estadística descriptiva de un CSV/XLSX: tipo de columna, media/mediana/min/max/std/nulos, top-5 categorías, outliers por IQR. Responde `pregunta` opcional vía LLM sobre esas estadísticas. |
| `pdf.py` | `extraer_tablas_pdf` | Extrae texto de un PDF por página (`pypdf`) y detecta tablas por alineación de columnas (heurística de espacios/tabs), devueltas como CSV. |
| `vision.py` | `analizar_imagen` | Describe/transcribe (OCR) una imagen, o responde una pregunta puntual sobre ella, vía bloques de visión del proveedor LLM. Requiere Anthropic (ver más abajo). |
| `video.py` | `analizar_video` | Extrae hasta 16 frames de un video con `ffmpeg` (subproceso, binario del sistema) y los envía en una sola tanda a los mismos bloques de visión que `analizar_imagen` (reutiliza `vision._bloque_imagen`). No transcribe audio; requiere Anthropic y `ffmpeg` instalado (ver `docs/analista.md` sección "Video"). |
| `graficos.py` | `generar_grafico` | Genera un SVG determinista (barras/líneas/dona, paleta accesible Okabe-Ito) puro Python, sin matplotlib, y lo guarda como archivo. |
| `reportes.py` | `exportar_analisis` | Arma un XLSX (`openpyxl`) con hoja "Resumen" + una hoja por sección con tabla, y lo guarda como archivo. |
| `forecast.py` | `predecir_serie` | Predice `periodos` puntos futuros de una serie numérica probando media móvil/regresión lineal/SES/Holt vía un backtest interno (menor MAE gana), con intervalo aproximado. Puro Python, sin S3 ni LLM. |
| `forecast.py` | `detectar_anomalias` | Detecta outliers (IQR o z-score) y rachas (≥5 valores seguidos por el mismo lado de la mediana) en una serie numérica. Puro Python, sin S3 ni LLM. |

`_s3.py` y `_util.py` son helpers privados compartidos (no forman parte del
contrato público del paquete).

## Decisiones de implementación

- **`edecan-llm` y `aioboto3` como dependencias añadidas**: no estaban en la
  lista original de dependencias del WP, pero se agregaron a `pyproject.toml`
  porque sin ellas las tools no podrían hacer lo que anuncian — mismo
  criterio que `packages/toolkit/pyproject.toml` documenta para su propio
  añadido de `edecan-llm`. `edecan-llm` la necesitan `tablas.py`/`vision.py`
  para `ChatMessage`/`CompletionRequest` (`ctx.llm.complete`/`.resolve` de
  verdad); `aioboto3` la necesita `_s3.py` (ver el punto siguiente).
- **Acceso a datos**: `_s3.py` lee/escribe la tabla `files` con
  `sqlalchemy.text()` sobre `ctx.session` (mismo patrón que
  `edecan_toolkit.documentos`/`finanzas` — no se importa `edecan_db.models`,
  ARCHITECTURE.md §10.1) y sube/baja bytes con un cliente `aioboto3`
  construido al vuelo (mismo patrón que
  `apps/api/edecan_api/routers/files.py`/`apps/worker/edecan_worker/deps.py`).
  Un archivo generado por una tool (gráfico, reporte) nace `status='ready'`
  directo — a diferencia de una subida de usuario, no hay nada que extraer
  después, así que no se encola `ingest_file` para él.
- **Visión requiere Anthropic**: `edecan_llm.anthropic._to_anthropic_messages`
  reenvía bloques de contenido tipo lista tal cual al wire de
  `/v1/messages` (incluido `{"type": "image", ...}`), pero
  `edecan_llm.openai_compat._to_openai_messages` solo extrae los bloques
  `type="text"` y descarta el resto en silencio. Para no mandar una imagen a
  un proveedor que la va a ignorar sin avisar, `analizar_imagen` resuelve el
  proveedor con `ctx.llm.resolve("principal", flags)` ANTES de llamar y
  devuelve un error explícito si `provider.name != "anthropic"`.
- **`generar_contenido`-style downgrade de modelo**: `tablas.py` (si viene
  `pregunta`), `vision.py` y `video.py` llaman a `ctx.llm.complete`/
  `ctx.llm.resolve` directo, así que las tres leen `ctx.extras["flags"]`
  (helper compartido `_util.tenant_flags`) para no perder el downgrade a
  modelo `"rapido"` por plan — mismo patrón que `GenerarContenidoTool` en
  `edecan_toolkit.contenido`.
- **SVG 100% determinista**: `graficos.py` no usa matplotlib ni ninguna
  librería de render — construye el XML a mano con formateo de número fijo
  (`_fmt`, siempre `.2f`) y sin timestamps/ids aleatorios, para que el mismo
  input produzca el mismo archivo byte a byte (`tests/test_graficos.py`
  compara contra snapshots en `tests/fixtures/`). Solo soporta valores no
  negativos (ver docstring del módulo para la razón).
- **Límites duros** en cada tool (filas/páginas/columnas/celdas leídas o
  devueltas) para que un archivo grande o adversarial no tumbe el proceso ni
  genere una respuesta inmanejable — el detalle exacto de cada límite está en
  `docs/analista.md`.
- **`forecast.py` (predicción/anomalías, fase v5) no toca S3 ni el LLM**: a
  diferencia del resto del paquete, `predecir_serie`/`detectar_anomalias`
  reciben los datos completos en los argumentos (`valores`) y devuelven el
  resultado directo — sin `ctx.session`/`ctx.llm`, así que no necesitan
  `_s3.py` ni `edecan-llm`. Cero dependencias nuevas: solo `math`/`statistics`
  de la librería estándar (nada de numpy/pandas/scipy), determinista y
  offline. El intervalo de `predecir_serie` es una heurística
  (`±1.96·desviación_estándar` de los residuos de un backtest interno), no un
  intervalo de confianza riguroso — por eso toda predicción termina con el
  disclaimer exacto `forecast.DISCLAIMER_FORECAST` (mismo criterio que
  `edecan_advisory`, ver `docs/analista.md` sección "Predicción y
  anomalías"). `detectar_anomalias` (IQR/z-score/rachas) es una heurística
  estadística general, explícitamente NO un modelo de detección de fraude
  (ARCHITECTURE.md §3 fila 5: "outliers básicos sí en P0", fraude ML = P2).
- **`ffmpeg` es la única dependencia no-Python del paquete**: `video.py`
  (fase v3) invoca el binario `ffmpeg` del sistema vía
  `asyncio.create_subprocess_exec` (JAMÁS `shell=True`) para extraer frames —
  a propósito, sin agregar `moviepy`/`opencv`/etc. a `pyproject.toml`, para no
  cargar la app de escritorio Tauri con librerías pesadas de video cuando el
  binario del sistema alcanza. `ffmpeg_disponible()` detecta el binario
  (`shutil.which`, o `FFMPEG_PATH` si el entorno lo fija) y `analizar_video`
  devuelve un `ToolResult` con instrucciones de instalación si falta —
  mismo criterio de "revisar el proveedor de visión ANTES de hacer trabajo
  caro" que `analizar_imagen`, aplicado también a "revisar ffmpeg/proveedor
  ANTES de extraer frames" (evita gastar hasta 120s de extracción cuando la
  llamada iba a fallar de todos modos). Detalle completo en `docs/analista.md`
  sección "Video".

## Tests

`tests/` usa fakes locales por duck typing (`ctx` como `SimpleNamespace`,
`FakeSession`/`FakeLLM` — mismo patrón que `packages/toolkit/tests/conftest.py`)
y genera sus propios archivos de prueba en memoria (XLSX con `openpyxl`, CSV
literal, PDF mínimo con `pypdf`) — offline y determinista (ARCHITECTURE.md
§10.15). `_s3.py` se fakea monkeypatcheando `descargar_archivo`/
`subir_resultado` en cada módulo bajo prueba. No importa `edecan_db` real ni
`edecan_llm` para construir sus dobles, ni hace llamadas de red.
`tests/test_video.py` sigue el mismo espíritu para `ffmpeg`: nunca invoca al
binario real, sino un script `#!/bin/sh` fake escrito en `tmp_path` en cada
test (fixture `fake_ffmpeg`) que imita su contrato de entrada/salida —
también offline y determinista, sin importar si el host que corre la suite
tiene `ffmpeg`/`ffprobe` instalados o no.
`tests/test_forecast.py` no necesita ninguno de esos fakes de infraestructura
(`forecast.py` no toca `_s3`/`ctx.llm`): usa solo `make_ctx` para las dos
tools, y para las funciones puras compara contra valores calculados a mano
y verificados de forma independiente (script aparte, sin importar el propio
módulo) antes de fijarlos como esperados en el test.
