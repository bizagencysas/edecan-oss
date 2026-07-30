# Creador universal desde chat

`crear_artefactos` convierte una sola frase en uno o varios entregables privados reales. Es la
ruta preferida para solicitudes compuestas como:

> Crea un post, un documento Word, un PDF, una presentación, una página web y una app para el
> lanzamiento de Café Norte.

No añade pantallas ni módulos al producto: se usa desde el mismo chat o entrada de voz. El
router de capacidades detecta la intención de creación y ofrece un único contrato al agente,
evitando mezclar generadores independientes sin un manifest común.

## Formatos reales

| Formato | Artefacto | Validación antes de afirmarlo como creado |
|---|---|---|
| `post` | Markdown `.md` | UTF-8 válido y contenido no vacío |
| `docx` | Word `.docx` | ZIP OOXML con `[Content_Types].xml` y `word/document.xml` |
| `pdf` | PDF `.pdf` | cabecera `%PDF-` y marcador final `%%EOF` |
| `pptx` | PowerPoint `.pptx` | ZIP OOXML con `[Content_Types].xml` y `ppt/presentation.xml` |
| `website` | proyecto estático `.zip` | HTML, CSS, README y `edecan-project.json` presentes y no vacíos |
| `app` | scaffold full-stack `.zip` | frontend, servidor Node, API `/api/health`, test y README presentes |

Word, PDF y PowerPoint reutilizan los renderizadores de `edecan_creative`. El uploader se
envuelve para capturar los bytes exactos que se guardan: así el creador puede calcular SHA-256,
validar la estructura y escribir la misma copia en el workspace local antes de subirla como
archivo privado del tenant.

El scaffold `app` es ejecutable y no tiene dependencias de terceros:

```bash
npm test
npm start
```

Incluye frontend, servidor HTTP y una prueba del endpoint de salud. Es un punto de partida
completo y verificable, no una promesa de que requisitos de negocio no descritos ya estén
implementados. El creador tampoco despliega el proyecto.

## Planner determinista

`edecan_core.creator_planner.plan_creation`:

1. acepta `formatos` explícitos o detecta todos los formatos mencionados;
2. conserva el orden de la solicitud y elimina duplicados;
3. distingue “documento PDF” (solo PDF) de “documento y PDF” (Word + PDF);
4. rechaza cualquier formato desconocido antes de crear un workspace o subir archivos;
5. cae a un post Markdown privado cuando la petición es de redacción sin formato explícito.

El planner no llama al LLM y siempre produce el mismo plan para la misma entrada. La redacción
base sí puede usar el modelo principal ya conectado. Si no está disponible, el manifest marca
`content_source="deterministic_fallback"` y genera un borrador mínimo que conserva literalmente
la solicitud; nunca inventa que el texto vino del modelo.

## Workspace y manifest

Cada ejecución usa un UUID nuevo y escribe únicamente dentro de:

```text
$CREATOR_WORKSPACE_DIR/{tenant_id}/{creation_id}/
```

Si `CREATOR_WORKSPACE_DIR` no está configurado, deriva a `$DATA_DIR/creator`. Las rutas internas
de proyectos rechazan absolutos y `..`; los ZIP se construyen con nombres fijos y orden estable.
En hosted la ruta física nunca se devuelve al cliente. En modo local sí aparece en
`ToolResult.data.workspace_path` para abrir el proyecto directamente.

`manifest.json` incluye:

- solicitud y plan exactos;
- fuente del contenido (`provided`, `llm` o `deterministic_fallback`);
- estado global (`completed`, `partial`, `failed`);
- por artefacto: estado, nombre, MIME, tamaño, SHA-256, validación y `file_id` privado;
- error acotado por formato cuando una salida falló.

El manifest también se sube como archivo privado. Una falla parcial no cancela los formatos
independientes que sí se materializaron, y la respuesta enumera como creados exclusivamente los
que tienen `status="created"`.

## Efectos externos

Crear es una operación privada y reversible, por lo que `crear_artefactos.dangerous` es `False`.
La tool nunca publica, despliega, envía correos ni actúa sobre cuentas conectadas. Si la frase
dice “crea un post y publícalo”, el router selecciona dos capacidades separadas:

1. `crear_artefactos` produce el borrador y su evidencia;
2. `publicar_social` conserva `dangerous=True` y el gate oficial de confirmación.

El preflight de lotes del agente impide ejecutar acciones del mismo lote antes de que estén
confirmados todos los efectos peligrosos.
