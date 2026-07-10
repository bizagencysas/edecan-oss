# Analista de documentos

`edecan_docanalysis` (ROADMAP_V2.md §4 WP-V2-02; `analizar_video` sumado en v3 por WP-V3-14; `predecir_serie`/`detectar_anomalias` sumadas en v5 por WP-V5-12) le da al agente ocho herramientas para leer y analizar de verdad los archivos y series numéricas que el usuario trae: estadística sobre tablas (CSV/XLSX), extracción heurística de tablas de PDF, visión sobre imágenes (OCR/descripción), análisis de video por frames, gráficos SVG, exportación de reportes XLSX, predicción de series (forecast) y detección de anomalías. Todas viven en `packages/docanalysis/edecan_docanalysis/`, se registran vía el entry point `edecan.tools` (ARCHITECTURE.md §10.7) y ninguna es `dangerous` ni requiere un flag de plan — están disponibles siempre, en todos los planes.

> **Los análisis son informativos, no una auditoría.** La estadística, la detección de outliers, la lectura de tablas de PDF, las descripciones de imágenes/video y las predicciones de series las genera código determinista o un modelo de lenguaje: son un punto de partida útil, no un reemplazo de una revisión humana (mucho menos de un(a) contador(a), auditor(a) o analista profesional) antes de tomar una decisión importante con esos datos. En particular, la detección de outliers (IQR o z-score) y de rachas es una heurística estadística general, no un modelo de detección de fraude con aprendizaje automático (eso queda fuera del alcance de este paquete — ver ROADMAP_V2.md §3, fila 5: "Detección de fraude ML = P2", "outliers básicos sí en P0"), y la predicción de series es una proyección estadística a partir de los datos dados, nunca asesoría financiera ni garantía de resultados futuros (ver sección ["Predicción y anomalías"](#predicción-y-anomalías) más abajo).

## Las 8 herramientas

| Tool | Qué hace | Entrada mínima |
|---|---|---|
| `analizar_tabla` | Estadística descriptiva de un CSV/XLSX: tipo de columna (numérica/texto), media/mediana/min/max/desviación estándar/nulos por columna numérica, top-5 categorías por columna de texto, outliers por rango intercuartílico (IQR). Puede responder una `pregunta` en lenguaje natural sobre esas estadísticas (nunca sobre las filas crudas). | `file_id` de un CSV o XLSX ya subido (`POST /v1/files`) |
| `extraer_tablas_pdf` | Extrae el texto de un PDF por página (`pypdf`) y detecta tablas por alineación de columnas (espacios múltiples o tabs), devueltas como CSV. | `file_id` de un PDF ya subido; `paginas` opcional |
| `analizar_imagen` | Describe una imagen y transcribe el texto visible (OCR), o responde una pregunta puntual sobre ella. | `file_id` de una imagen ya subida; `pregunta` opcional |
| `analizar_video` | Extrae una muestra de frames del video con ffmpeg y los envía en una sola tanda a un modelo con visión para resumir los eventos clave, o responder una pregunta puntual. NO transcribe audio. Ver sección ["Video"](#video) más abajo. | `archivo` (id UUID) de un video ya subido; `pregunta` y `max_frames` opcionales |
| `generar_grafico` | Genera un gráfico SVG determinista (barras, líneas o dona) a partir de etiquetas y valores, y lo guarda como archivo nuevo. | `tipo`, `titulo`, `etiquetas`, `valores` (o `series` para líneas con más de una serie) |
| `exportar_analisis` | Arma un reporte XLSX: hoja "Resumen" con texto por sección + una hoja adicional por cada sección que traiga una tabla, y lo guarda como archivo nuevo. | `titulo`, `secciones` |
| `predecir_serie` | Predice los próximos valores de una serie numérica (ventas, métricas, cualquier serie temporal) probando media móvil, regresión lineal, suavizado exponencial simple y de Holt, y usa el que mejor predijo un backtest interno. Devuelve un intervalo aproximado por predicción. Ver sección ["Predicción y anomalías"](#predicción-y-anomalías). | `valores` (3 a 500 números); `periodos` y `etiquetas` opcionales |
| `detectar_anomalias` | Detecta valores atípicos (IQR o z-score) y rachas de al menos 5 valores seguidos por el mismo lado de la mediana. Ver sección ["Predicción y anomalías"](#predicción-y-anomalías). | `valores` (4 a 1000 números); `metodo`, `umbral` y `etiquetas` opcionales |

`generar_grafico`, `exportar_analisis`, `predecir_serie` y `detectar_anomalias` no leen ningún archivo de entrada: reciben los datos ya calculados (por ejemplo, por `analizar_tabla`) directo en los argumentos y, en el caso de `generar_grafico`/`exportar_analisis`, producen un archivo nuevo (`predecir_serie`/`detectar_anomalias` no tocan archivos ni S3 en absoluto — todo el cálculo es puro Python en memoria). `analizar_video` sí lee un archivo ya subido, pero con la propiedad `archivo` en vez de `file_id` (mismo mecanismo de resolución/descarga S3 que las demás, nombre de propiedad distinto).

## Video

`analizar_video` (WP-V3-14) mueve a código real el punto P2 de `ROADMAP_V2.md` §6.3 ("Video: extracción de frames (ffmpeg en el worker) + visión por lotes"), promovido por la ambición "sin límite" de `DIRECCION_ACTUAL.md`. No existe ningún modelo de video-a-texto en el flujo: la técnica es tomar una **muestra de frames** (imágenes fijas) con `ffmpeg` y mandarlos, en una sola tanda con etiquetas `"Frame i de N"` (y un timestamp aproximado `~MM:SS` cuando se puede estimar la duración con `ffprobe`), al mismo proveedor de visión que ya usa `analizar_imagen` — mismo requisito de proveedor Anthropic (ver arriba) y mismo error explícito si el proveedor configurado no procesa imágenes.

**ffmpeg es una dependencia del SISTEMA, no de Python.** A diferencia del resto del paquete (que solo usa librerías Python: `openpyxl`, `pypdf`, `edecan-llm`, `aioboto3`), `analizar_video` invoca el binario `ffmpeg` como subproceso (`asyncio.create_subprocess_exec`, nunca `shell=True`) — no se agregó ninguna dependencia Python nueva (nada de `moviepy`/`opencv`) a propósito, para no inflar el empaquetado de la app de escritorio Tauri (`DIRECCION_ACTUAL.md`) con librerías pesadas de procesamiento de video cuando el binario del sistema alcanza.

- **Detección**: `ffmpeg_disponible()` busca el binario con `shutil.which("ffmpeg")`, salvo que la variable de entorno `FFMPEG_PATH` fije una ruta explícita (mismo criterio que `CLAUDE_CLI_PATH`/`CODEX_CLI_PATH` de `edecan_llm.detect`, ARCHITECTURE.md §12.d) — un override explícito gana sobre la autodetección. `ffprobe` (para estimar la duración y así calcular timestamps aproximados) se detecta igual pero sin override de entorno; si falta, `analizar_video` sigue funcionando (cae a un muestreo de fps fijo y a `duracion_estimada_s: null` en el resultado), solo pierde los timestamps aproximados.
- **Instalación por el cliente**: en la app de escritorio, `ffmpeg` NO viene incluido — el cliente lo instala aparte en su máquina (`brew install ffmpeg` en macOS, `apt install ffmpeg` en Linux/Debian/Ubuntu, o el binario oficial en Windows). Si no está instalado, la tool nunca revienta con un traceback: devuelve un `ToolResult` con instrucciones claras de instalación en español.
- **Límites**: video de hasta **80 MB** (rechazado con un mensaje claro ANTES de escribir nada a disco si lo excede), hasta **16 frames** por análisis (`max_frames`, 8 por defecto), timeout duro de **120 segundos** para la extracción. Si algún límite se alcanza de forma que impide el análisis (video demasiado grande, timeout, ffmpeg terminó con error, o no generó ningún frame), la tool lo dice explícitamente en vez de fallar en silencio o con un stacktrace.
- **No transcribe audio.** `analizar_video` es puramente visual (frames fijos, sin pista de audio). Si el usuario necesita lo que se dice en el video, la alternativa hoy es extraer/subir el audio por separado a la transcripción de voz del asistente (`POST /v1/voice/transcribe`, ARCHITECTURE.md §10.12) — una transcripción de audio integrada directo en `analizar_video` queda como posible mejora futura, no construida en este WP.
- **Privacidad**: los frames extraídos viajan al proveedor LLM que tenga **configurado el tenant** (bring-your-own, `DIRECCION_ACTUAL.md`/ARCHITECTURE.md §12.b) — hoy, en la práctica, Anthropic (es el único proveedor que entiende bloques de visión, ver arriba). Ningún frame se guarda en el S3 del tenant ni en ninguna tabla: se extraen a un directorio temporal que se borra al terminar la llamada (`tempfile.TemporaryDirectory`) y se descartan de memoria apenas vuelve la respuesta del modelo.

## Predicción y anomalías

`predecir_serie` y `detectar_anomalias` (WP-V5-12) cubren la parte de "predice ventas, calcula riesgos, detecta fraude" del wishlist de Analista (`REQUISITOS_V2.md`) que `ROADMAP_V2.md` §3 fila 5 dejó explícitamente como P0 ("outliers básicos sí"), dejando un modelo de fraude con aprendizaje automático de verdad como P2 (fuera de alcance). Viven en `edecan_docanalysis/forecast.py`, **puro Python** (solo `math`/`statistics` de la librería estándar — cero dependencias nuevas, nada de numpy/pandas/scipy), deterministas y offline: a diferencia de `tablas.py`/`vision.py`/`video.py`, ninguna de las dos toca S3 ni el LLM — los datos llegan completos en los argumentos y el resultado es directo, sin archivo de por medio.

### Predicción de series (`predecir_serie`)

Prueba cuatro métodos clásicos de series de tiempo y usa el que mejor prediga:

| Método | Cómo pronostica | Piso de puntos |
|---|---|---|
| Media móvil | Promedio de los últimos 3 valores, proyectado **plano** hacia adelante (sin tendencia). | 3 |
| Suavizado exponencial simple (SES) | Promedio ponderado exponencialmente decreciente, también proyectado plano — reacciona más rápido que la media móvil a cambios recientes, pero tampoco modela tendencia. | 3 |
| Regresión lineal | Mínimos cuadrados ordinarios (OLS) de la serie contra el tiempo — capta tendencia lineal e informa `r²` (qué tan lineal es en realidad la serie). | 4 |
| Holt (suavizado exponencial doble) | Como SES pero con una segunda ecuación que sigue la tendencia — el único que proyecta una pendiente sin asumir que toda la serie es perfectamente lineal. | 8 |

**Cómo elige el método**: aparta el último tramo de la propia serie (~20%, con un mínimo de 3 puntos — recortado si hace falta para dejar suficiente entrenamiento a cada método, nunca 0), pronostica sobre ese tramo apartado con cada método aplicable (según el piso de la tabla) y mide el error absoluto medio (MAE) contra los valores reales que sí conoce. Usa el método que mejor predijo ese tramo para el pronóstico real hacia adelante (que sí se recalcula sobre la serie completa, no solo sobre el tramo de entrenamiento del backtest). Si dos o más métodos empatan en MAE exacto (típico en una serie perfectamente constante o perfectamente lineal), gana el más simple de la tabla de arriba.

**Intervalo**: cada predicción trae un intervalo aproximado `predicción ± 1.96 · desviación_estándar(residuos_del_backtest)` — la heurística de la "regla del ~95%" asumiendo que los errores del backtest se distribuyen aproximadamente normal. **No es un intervalo de confianza estadísticamente riguroso** (eso exigiría supuestos más fuertes sobre la serie que esta herramienta deliberadamente no asume) — es una aproximación honesta a partir del único dato disponible: qué tan bien predijo ese método el tramo que sí se pudo verificar. Una serie constante tiene residuos todos en cero, así que su intervalo tiene ancho cero (no es un error, es correcto: no hay incertidumbre que reportar si el patrón nunca varió).

**Toda predicción termina con el disclaimer exacto**: *"Proyección estadística informativa basada solo en los datos dados; no es asesoría financiera ni garantía."* — no se parafrasea ni se recorta, mismo criterio de disclaimers-en-código verificado por test que usa `edecan_advisory` (`docs/asesores.md`), aunque este paquete no depende de `edecan_advisory` (cada paquete lleva su propia copia, ARCHITECTURE.md §10.1).

Los períodos futuros siempre se etiquetan `t+1`…`t+n` en el texto de respuesta — si vienen `etiquetas` para la serie histórica (p. ej. nombres de mes), se usan solo para dar contexto ("serie histórica hasta «ago»"), nunca para inferir qué etiqueta sigue después (no hay una forma general de saber qué viene después de una etiqueta arbitraria de texto).

**Ejemplo de uso desde el chat**: *"acá están mis ventas de los últimos 10 meses: 50, 55, 53, 60, 65, 63, 70, 75, 73, 80 — predice los próximos 3 meses"* → el agente llama `predecir_serie` con esos `valores` y `periodos=3`; con una tendencia alcista con bajones cortos como esa, el backtest interno normalmente prefiere Holt (sigue la pendiente) sobre la media móvil (que se queda plana y se atrasa respecto a la tendencia real).

### Detección de anomalías (`detectar_anomalias`)

Dos métodos de outlier, elegidos por `metodo`:

- **`iqr`** (por defecto, `umbral=1.5`): mismos cuartiles "a la Tukey" que `analizar_tabla` (mediana de cada mitad de la lista ordenada, excluyendo la mediana global cuando `n` es impar) — un valor es atípico si cae fuera de `[Q1 − umbral·IQR, Q3 + umbral·IQR]`. Robusto a outliers extremos (los cuartiles no se mueven por un solo valor descabellado).
- **`zscore`** (`umbral=3.0` por defecto): un valor es atípico si `|valor − media| / desviación_estándar > umbral`. Ojo con series muy chicas: un único outlier entre pocos puntos "infla" su propia desviación estándar y puede quedar por debajo del umbral por defecto (efecto de enmascaramiento conocido de z-score) — si `detectar_anomalias` no encuentra nada con `zscore` en una serie corta, vale la pena probar con `iqr` o un `umbral` más bajo.

Cada atípico devuelve su posición (`indice`, o la `etiqueta` correspondiente si vinieron), el `valor` y un `score` (unidades de IQR o desviaciones estándar, según el método) — la respuesta en texto incluye una lectura en español del tipo *"3 de 40 puntos se salen del patrón"*.

**Rachas**: además de outliers puntuales, `detectar_anomalias` siempre busca corridas de **al menos 5 valores consecutivos** todos por encima, o todos por debajo, de la mediana de la serie completa (un valor exactamente igual a la mediana corta la racha en curso) — una señal simple de que el proceso cambió de régimen de forma sostenida, no solo un pico aislado. Es la lectura honesta y básica de "detecta fraude" que sí entra en el alcance P0 de `ROADMAP_V2.md`: una heurística de racha estadística, **no** un modelo de fraude con aprendizaje automático.

**Ejemplo de uso desde el chat**: *"estos son los montos de mis últimas 30 transacciones, ¿hay algo raro?"* → el agente llama `detectar_anomalias` con esos `valores`; si un monto se dispara muy por encima del resto lo marca como outlier IQR, y si además hay una racha de 5+ transacciones seguidas por encima de lo normal lo señala aparte como posible cambio de patrón.

### Límites honestos de ambas

- **No son modelos de series de tiempo "de caja negra"**: los cuatro métodos de `predecir_serie` son técnicas estadísticas clásicas de un siglo de antigüedad (mínimos cuadrados, suavizado exponencial) — no hay redes neuronales, no captan estacionalidad explícita (un patrón que se repite cada N períodos), solo tendencia y nivel. Para datos con estacionalidad fuerte (ventas de temporada, por ejemplo) el pronóstico puede quedarse corto; el backtest al menos avisa con qué error lo hizo en el propio historial.
- **`detectar_anomalias` no es un detector de fraude**: IQR/z-score/rachas son heurísticas estadísticas generales sobre UNA serie de números — no cruzan con reglas de negocio, patrones conocidos de fraude, ni aprendizaje automático entrenado sobre casos reales. Son un primer filtro razonable ("¿algo se ve raro aquí?"), no una herramienta de cumplimiento.
- **Todo el cálculo es en memoria, sin persistencia**: ninguna de las dos tools guarda nada en `files`/S3 ni en ninguna tabla — el resultado va directo en la respuesta del turno. Si el usuario quiere un gráfico o un reporte de la predicción, hay que encadenar con `generar_grafico`/`exportar_analisis` por separado.

## Cómo se conecta con el resto del sistema

- **Archivos de entrada**: los tres tools que leen un `file_id` (`analizar_tabla`, `extraer_tablas_pdf`, `analizar_imagen`), más `analizar_video` (que usa la misma resolución/descarga pero con la propiedad `archivo` en su schema), descargan el contenido de `s3://$S3_BUCKET/tenants/{tenant_id}/files/{file_id}/{filename}` (ARCHITECTURE.md §10.14) — el mismo archivo que subió el usuario con `POST /v1/files`.
- **Archivos de salida**: `generar_grafico` y `exportar_analisis` suben su resultado al mismo layout S3 y crean una fila nueva en `files` con `status='ready'` directo (a diferencia de una subida de usuario, que nace `status='uploaded'` y el worker la promueve a `ready` tras el job `ingest_file` — aquí no hace falta: el archivo generado ya está completo en el momento de subirlo). El resultado queda visible en `GET /v1/files` como cualquier otro archivo del tenant, y se puede volver a pasar a `consultar_documentos`/`analizar_tabla`/etc. como cualquier `file_id`.
- **Visión requiere el proveedor Anthropic**: `analizar_imagen` (y la auto-descripción de imágenes en la ingesta, ver abajo) le mandan al modelo un bloque de contenido `{"type": "image", ...}` (formato común de `edecan_llm`, que sigue la convención de Anthropic). Esto solo lo entiende el proveedor Anthropic — si el tenant tiene configurado únicamente un proveedor OpenAI-compatible (`OPENAI_COMPAT_BASE_URL`), la tool lo detecta ANTES de llamar (`ctx.llm.resolve(...)`) y devuelve un mensaje de error explícito en vez de mandar la imagen a un proveedor que la ignoraría en silencio. Configura `ANTHROPIC_API_KEY` (ver `configuracion.md`) para usar visión.
- **Auto-descripción de imágenes en la ingesta**: `apps/worker/edecan_worker/handlers/ingest_file.py` (el job que se encola en cada `POST /v1/files`) ahora reconoce imágenes (`png`/`jpeg`/`webp`/`gif`, hasta 5&nbsp;MB): si hay un proveedor de visión configurado, genera una descripción breve (1-2 frases, incluye OCR del texto visible) y la guarda como `file_chunks` `seq=0` — así `consultar_documentos` (`edecan_toolkit.documentos`) también encuentra imágenes por texto o por similitud semántica, no solo tablas/PDFs/Word. Usa el alias de modelo `"rapido"` a propósito (es un job automático que corre en cada imagen subida, así que se prioriza costo/latencia sobre la profundidad que sí tiene la tool interactiva `analizar_imagen`). Si NO hay proveedor de visión configurado, el comportamiento es idéntico al de cualquier archivo de un tipo no soportado: el archivo queda `status='error'` sin chunks — ninguna imagen se pierde ni rompe el job, simplemente no queda indexada por texto hasta que se configure Anthropic.

## Límites (tamaños y formatos)

Todos son límites duros en código, pensados para que un archivo grande o adversarial no tumbe el worker/API ni genere una respuesta inmanejable para el modelo. Si se alcanza un límite, la tool no falla: recorta y lo dice explícitamente en su respuesta.

| Herramienta | Límite | Valor |
|---|---|---|
| `analizar_tabla` | Formatos soportados | CSV (delimitado por comas, UTF-8/UTF-8 con BOM) y XLSX (primera hoja/hoja activa) |
| | Filas de datos leídas | 50.000 |
| | Columnas consideradas | 200 |
| | Columnas detalladas en el texto de respuesta | 30 (el resultado estructurado siempre trae todas, hasta el límite de columnas) |
| | Categorías por columna de texto (`top`) | 5 |
| | Outliers listados por columna | 20 |
| | Mínimo de valores numéricos para calcular outliers (IQR) | 4 |
| `extraer_tablas_pdf` | Formato soportado | PDF con texto extraíble (no escaneado como imagen — para eso usa `analizar_imagen`, que sí hace OCR) |
| | Páginas procesadas por llamada | 30 |
| | Tablas detectadas por página | 10 |
| | Tablas totales devueltas | 50 |
| | Filas por tabla detectada | 200 |
| `analizar_imagen` | Formatos soportados | PNG, JPEG, WEBP, GIF |
| | Tamaño máximo | 5&nbsp;MB |
| | Proveedor requerido | Anthropic (`ANTHROPIC_API_KEY`) — con otro proveedor, error explícito |
| `analizar_video` | Requisito de sistema | binario `ffmpeg` instalado — si falta, mensaje instructivo de instalación (ver sección ["Video"](#video)) |
| | Tamaño máximo | 80&nbsp;MB |
| | Frames extraídos | 1 a 16 (por defecto 8) |
| | Timeout de extracción | 120&nbsp;s |
| | Proveedor requerido | Anthropic (`ANTHROPIC_API_KEY`) — con otro proveedor, error explícito |
| | Audio | Nunca se transcribe |
| `generar_grafico` | Tipos soportados | `barras`, `lineas`, `dona` |
| | Valores | Solo no negativos (decisión de alcance: barras/líneas bidireccionales alrededor de cero quedan fuera) |
| | Categorías/etiquetas | 20 |
| | Series (solo `lineas`) | 8 |
| | Dona | La suma de los valores debe ser mayor que 0 |
| `exportar_analisis` | Secciones por reporte | 100 |
| | Filas por tabla de sección | 20.000 |
| | Columnas por tabla de sección | 200 |
| `predecir_serie` | Valores de entrada | 3 a 500 números |
| | Períodos a predecir | 1 a 24 (por defecto 6) |
| | Piso de puntos por método | media móvil/SES: 3, regresión lineal: 4, Holt: 8 |
| `detectar_anomalias` | Valores de entrada | 4 a 1000 números |
| | Umbral por defecto | 1.5 (`iqr`), 3.0 (`zscore`) |
| | Mínimo de valores consecutivos para contar como racha | 5 |
| Ingesta automática de imágenes (`ingest_file`) | Tamaño máximo | 5&nbsp;MB (igual que `analizar_imagen`) |

## Determinismo de los gráficos SVG

`generar_grafico` construye el SVG a mano, en Python puro (nunca matplotlib ni ninguna librería de render): el mismo `tipo`/`titulo`/`etiquetas`/`valores` siempre produce el mismo archivo byte a byte — sin timestamps, sin ids aleatorios, con formateo de número fijo. Usa la paleta **Okabe–Ito** (Okabe & Ito, 2008), el estándar de facto de paleta accesible para las formas más comunes de daltonismo.

## Pantalla Analista (WP-V6-06)

Hasta v5, todo lo de arriba (estadística, forecast, anomalías, gráficos) solo era alcanzable **por chat** — el agente decidía cuándo llamar cada tool. v6 le suma una **pantalla web dedicada** (`/app/analista`) que expone la MISMA lógica de análisis por REST de solo lectura, sin pasar por ningún turno de agente — principio de "configuración/uso de pocos clics" de `DIRECCION_ACTUAL.md` aplicado a una feature de dashboard: analizar un archivo ya subido no debería exigir escribirle una frase al asistente.

### Flujo de la UI

`/app/analista` (`apps/web/src/app/(app)/app/analista/page.tsx`) es un layout de dos paneles:

1. **Selector de archivo** (`components/analista/ArchivoSelector.tsx`): un dropdown poblado con `GET /v1/analista/archivos` (solo CSV/XLSX del tenant actual — el mismo listado que `GET /v1/files`, filtrado a mimes tabulares). Si el tenant no tiene ningún archivo tabular todavía, un `EmptyState` con link directo a `/app/archivos` (ahí es donde se sube un CSV/XLSX, `apps/api/edecan_api/routers/files.py`) — esta pantalla nunca duplica esa subida. Con al menos un archivo, la pantalla auto-selecciona el primero (menos clics).
2. **Pestañas** (mismo patrón tab-bar que `/app/rrhh`, sin un componente `Tabs` compartido): **Resumen**, **Pronóstico**, **Gráfico** — cada una es un componente independiente que hace su propia carga de datos al montar (mismo criterio que las pestañas de RRHH), con loading/error state propio.
   - **Resumen** (`ResumenTab.tsx`): tabla con una fila por columna (tipo, media/mediana/min/max/desviación estándar/nulos para numéricas, top de categorías para texto) + un chip de "N atípicos" donde `detectar_anomalias`/`analizar_tabla` encontró outliers.
   - **Pronóstico** (`PronosticoTab.tsx`): un formulario con columna de valor (numérica, obligatoria) y columna de fecha/etiqueta (opcional) — ambas **autodetectadas** apenas carga el resumen (primera columna numérica / primera de texto, mismo criterio que el propio backend usa como respaldo si el formulario las manda vacías) y editables antes de enviar — más un slider de horizonte (1 a 24 períodos). Al enviar, muestra la tabla de proyección (`t+1…t+n` con intervalo aproximado) y, debajo, la lista de anomalías de la misma serie con un chip de severidad ("moderada"/"alta", heurística simple sobre `|score|` — no un modelo de severidad riguroso).
   - **Gráfico** (`GraficoTab.tsx`): formulario de tipo (barras/líneas/dona) + columna X/Y (también autodetectadas) → el SVG que devuelve el backend se renderiza inline (`dangerouslySetInnerHTML`, justificado en un comentario en el propio componente: el SVG lo genera un backend determinista propio, con `xml.sax.saxutils.escape`/`quoteattr` sobre cualquier texto interpolado — nunca HTML/JS de un tercero ni marcado sin escapar de un dato arbitrario) + un botón "Copiar SVG" (`navigator.clipboard`).

### Endpoints (`apps/api/edecan_api/routers/analista.py`, prefix `/v1/analista`)

Los 4 son de **solo lectura** (`GET`/`POST` que nunca escriben nada — ni siquiera un archivo nuevo, a diferencia de `generar_grafico`/`exportar_analisis` por chat, que sí crean una fila en `files`), exigen `get_current_user` + `rate_limit` estándar, y **ninguno declara un flag de plan** — paridad deliberada con las 8 tools de este mismo paquete, que tampoco requieren uno (ver "Las 8 herramientas" arriba; `ARCHITECTURE.md` §15 pinnea el prefix con este mismo criterio).

| Endpoint | Body | Qué hace |
|---|---|---|
| `GET /archivos` | — | Lista los archivos del tenant filtrados a CSV/XLSX. |
| `POST /{file_id}/resumen` | `{hoja?}` | Descarga el archivo y corre la misma estadística/outliers que `analizar_tabla` (sin la parte de `pregunta`, que exige LLM). `hoja` selecciona una hoja de un XLSX por nombre — solo disponible acá; por chat `analizar_tabla` siempre usa la hoja activa/primera. |
| `POST /{file_id}/forecast` | `{columna_fecha?, columna_valor?, horizonte?<=24}` | Extrae una columna numérica (autodetectada si falta `columna_valor`) y corre `predecir` + `detectar_anomalias` sobre la misma serie. Si la serie alcanza para `predecir` (mínimo 3 valores) pero no para `detectar_anomalias` (mínimo 4), el pronóstico se devuelve completo igual, con `anomalias: null` + `anomalias_error` explicando el motivo — nunca falla todo el endpoint por eso. |
| `POST /{file_id}/grafico` | `{tipo: barras\|lineas\|dona, columna_x?, columna_y?}` | Extrae dos columnas (autodetectadas si faltan) y llama `generar_svg` → `{"svg": "..."}`. Capa a los primeros 20 puntos (mismo límite que `generar_grafico` acepta de `etiquetas`) si la tabla trae más filas, con `truncado: true` en la respuesta. |

Errores: `404` si el archivo no existe o es de otro tenant. `400` con el mensaje **exacto** de la función pura de `edecan_docanalysis` si el archivo no parsea o una columna pedida no existe (nunca se reformula ese texto). Límite de tamaño: `edecan_docanalysis.tablas` no trae un tope en bytes propio (solo de filas/columnas ya leídas) — este router agrega uno de 10&nbsp;MB.

### Superficie pública nueva de `edecan_docanalysis` (para este router, no para el chat)

Este WP no cambió ninguna de las 8 tools de chat — les agregó, al lado, un puñado de funciones **puras** (sin `ToolContext`, sin LLM) que el router consume directo, documentadas en el docstring de `edecan_docanalysis/__init__.py`:

- `descargar_archivo_de_tenant(session, settings, tenant_id, file_id)` (`_s3.py`) — misma descarga S3 tenant-scoped que usa `analizar_tabla`/`generar_grafico`, con `session`/`settings` explícitos en vez de un `ToolContext` (un router HTTP no arma uno — eso solo existe dentro del loop de `Agent.run_turn`).
- `extraer_columnas_bytes` / `analizar_tabla_bytes` (`tablas.py`) — filas crudas y estadística/outliers de un CSV/XLSX ya descargado.
- `generar_svg` (`graficos.py`) — mismo render determinista que `generar_grafico`, sin subir el resultado a S3.
- `predecir` / `detectar_anomalias` / `DISCLAIMER_FORECAST` (`forecast.py`) — ya eran funciones públicas que sostienen `predecir_serie`/`detectar_anomalias`; solo se re-exportan a nivel de paquete.

### El chat sigue siendo la vía con LLM

Esta pantalla es **puro Python determinista y offline** — ninguno de sus 4 endpoints llama jamás a `ctx.llm`/`edecan_llm` (verificado con un test de regresión que recorre el AST del router buscando cualquier `import` de `edecan_llm`). La única forma de **preguntar en lenguaje natural** sobre una tabla ("¿cuál mes vendió más?", "resume esto") sigue siendo exclusivamente el chat, vía `analizar_tabla(pregunta=...)` (que sí usa `ctx.llm.complete`, ver "Las 8 herramientas" arriba) — la pantalla Analista no tiene (ni tendrá) un cuadro de texto libre; es un dashboard determinista, no un chat alternativo.

## Ver también

- [`../ARCHITECTURE.md`](../ARCHITECTURE.md) §10.7 — contrato `Tool`/`ToolContext`/`ToolResult` que implementan las 8 herramientas.
- [`../ROADMAP_V2.md`](../ROADMAP_V2.md) §3 fila 5, §4 (WP-V2-02), §6.3 y §7.7 — alcance y nombres pinned de las 5 herramientas originales, y la decisión "outliers básicos sí en P0, fraude ML = P2" que delimita `detectar_anomalias`; `analizar_video` es una extensión de v3 (`DIRECCION_ACTUAL.md` "Ambición: sin límite", WP-V3-14) y `predecir_serie`/`detectar_anomalias` son de v5 (WP-V5-12) — ninguna reabre el pinned v2.
- [`conectores.md`](./conectores.md) y [`configuracion.md`](./configuracion.md) — cómo configurar `ANTHROPIC_API_KEY` (requerido por `analizar_imagen`/`analizar_video` y por la auto-descripción de imágenes en la ingesta; `predecir_serie`/`detectar_anomalias` no requieren ningún proveedor externo, son puro Python).
- [`personalizacion-nivel-dios.md`](./personalizacion-nivel-dios.md) — cómo `consultar_documentos` usa los `file_chunks` que genera tanto la ingesta normal como la auto-descripción de imágenes.
- [`asesores.md`](./asesores.md) — el paquete `edecan_advisory`, cuyo criterio de "disclaimer exacto verificado por test" replica `predecir_serie` (cada paquete con su propia copia del disclaimer, ARCHITECTURE.md §10.1, en vez de importar `edecan_advisory`).
- `apps/api/edecan_api/routers/analista.py` y `apps/api/tests/test_analista_router.py` — los 4 endpoints REST de la Pantalla Analista (WP-V6-06) y sus tests (`FakeRepo` + un `aioboto3` falso en `sys.modules`, mismo criterio que `packages/docanalysis/tests/test_s3.py`).
- `apps/web/src/app/(app)/app/analista/page.tsx`, `apps/web/src/components/analista/` y `apps/web/src/lib/api-analista.ts` — la UI de dos paneles + pestañas descrita arriba.
