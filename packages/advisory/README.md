# packages/advisory — `edecan_advisory`

Asesores informativos del agente (`ARCHITECTURE.md` §11 fase v2): legal, salud y
educación. Cada tool implementa el contrato `Tool` de `edecan_core`
(ARCHITECTURE.md §10.7): `name`, `description`, `input_schema`,
`requires_flags` (vacío en las 8), `dangerous` (`False` en las 8) y
`async run(ctx, args)`.

`get_all_tools() -> list[Tool]` (en `edecan_advisory/__init__.py`) es el
entry point que consume `edecan_core.ToolRegistry.load_entry_points(group="edecan.tools")`,
declarado en `pyproject.toml` como `[project.entry-points."edecan.tools"]`.

## GUARDRAIL no negociable (`ARCHITECTURE.md` §0)

Salud/legal = **informativo + disclaimer SIEMPRE**, jamás sustituto de un
profesional. El disclaimer va embebido en código (`_disclaimers.py`) y
**verificado por tests** — `tests/test_disclaimers.py::test_disclaimers_en_todas`
es el test más importante de este paquete: itera las 8 tools y comprueba que
`resultado.content` termina exactamente con el disclaimer correcto.

## Las 8 herramientas (nombres exactos, pinned en `ARCHITECTURE.md` §11)

| Módulo | Tool | Qué hace | Disclaimer |
|---|---|---|---|
| `legal.py` | `analizar_contrato` | Extrae partes/objeto/vigencia/obligaciones/riesgos de un contrato (`file_id` o `texto` pegado) vía LLM. | legal |
| `legal.py` | `comparar_contratos` | `difflib.unified_diff` línea a línea entre dos versiones + resumen de cambios materiales vía LLM. | legal |
| `legal.py` | `generar_borrador_legal` | Rellena una plantilla (`_plantillas.py`: NDA, carta formal, acuerdo simple), la pule con el LLM y la guarda como `.md` en `files`. | legal |
| `salud.py` | `registrar_salud` | Inserta un registro en `health_logs` (medicamento/ejercicio/sueño/agua/hábito/medida). | salud |
| `salud.py` | `resumen_salud` | Agregados por `kind` en una ventana de días: conteos, sumas de `valor.cantidad`, rachas de días consecutivos. | salud |
| `salud.py` | `analizar_laboratorio` | Detecta analitos (nombre/valor/unidad) por regex y explica QUÉ MIDEN cada uno — nunca si están normales/altos/bajos. | salud (reforzado) |
| `educacion.py` | `tutor_leccion` | Genera una lección (explicación + ejemplos + ejercicios) vía LLM, la persiste en `learning_progress` y muestra las preguntas SIN respuestas. | edu |
| `educacion.py` | `tutor_evaluar` | Corrige las respuestas contra la última lección del tema (tolerante a redacción) y guarda el resultado. | edu |

`_disclaimers.py`, `_texto.py`, `_plantillas.py` y `_util.py` son helpers
privados compartidos (no forman parte del contrato público del paquete).

## Decisiones de implementación

- **Acceso a datos**: igual patrón que `edecan_toolkit.documentos`/`finanzas`
  y `edecan_docanalysis._s3` — SQL parametrizado con `sqlalchemy.text()`
  sobre `ctx.session` (no se importa `edecan_db.models`, ARCHITECTURE.md
  §10.1), S3 con un cliente `aioboto3` construido al vuelo. `_texto.py` junta
  en un solo módulo la descarga de S3 y la extracción de texto (PDF vía
  `pypdf`, DOCX vía `python-docx`, TXT/MD por decodificación UTF-8), capada a
  `MAX_CHARS` = 100k caracteres antes de mandarla a cualquier LLM.
- **Disclaimers embebidos, no opcionales**: cada tool arma su `content` y
  SIEMPRE lo pasa por `with_disclaimer(kind, texto)` como último paso — nunca
  concatena el string a mano. `with_disclaimer` es idempotente (no duplica si
  el texto ya termina en el disclaimer).
- **JSON tolerante del LLM**: `_util.extraer_json_llm` parsea la respuesta
  del modelo aunque venga envuelta en una cerca de código markdown o con
  texto alrededor; si no logra extraer un objeto, el caller cae a un `dict`
  vacío y renderiza valores por defecto ("no especificado") en vez de
  reventar la herramienta.
- **`tutor_evaluar` nunca pierde el turno por un LLM que no respetó el
  formato**: si `_util.extraer_json_llm` no devuelve `correcciones` con la
  misma longitud que los pares pregunta/respuesta, cae a una comparación
  case-insensitive determinista en vez de fallar.
- **Downgrade de modelo por plan**: todas las llamadas a `ctx.llm.complete`
  pasan `_util.tenant_flags(ctx)` (lee `ctx.extras["flags"]`) — mismo patrón
  que `GenerarContenidoTool` de `edecan_toolkit.contenido`.

## Tests

`tests/` usa fakes locales por duck typing (`ctx` como `SimpleNamespace`,
`FakeSession`/`FakeLLM` — mismo patrón que `packages/toolkit/tests/conftest.py`
y `packages/docanalysis/tests/conftest.py`) y genera sus propios archivos de
prueba en memoria (PDF mínimo construido a mano con `pypdf`, DOCX real con
`python-docx`) — offline y determinista (ARCHITECTURE.md §10.15). `_texto.py`
se fakea monkeypatcheando `descargar_archivo`/`subir_resultado`. Ningún test
importa `edecan_db`/`edecan_llm` reales para construir sus dobles, ni hace
llamadas de red ni a paquetes hermanos.

```
cd packages/advisory && PYTHONPATH=. pytest
```
