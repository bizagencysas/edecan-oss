# Asesores — legal, salud y educación (informativo)

`edecan_advisory` (`packages/advisory/`) le da al agente ocho herramientas de asesoría
**informativa** en tres dominios: legal, salud y educación. Corresponde al work package
**fase v2** de `docs/roadmap.md`, con el contrato de nombres/tools pinned en
`docs/roadmap.md`.

## Alcance: informativo, no profesional

**Ninguna herramienta de este paquete sustituye a un profesional.** Ni el análisis de un
contrato reemplaza a un abogado, ni el tracking de salud o la lectura de un laboratorio
reemplazan a un médico, ni una lección o evaluación del tutor reemplaza una evaluación
académica oficial. Esto no es una política de producto que viva solo en este documento:
es un **guardrail de código** (`docs/roadmap.md`, regla no negociable del proyecto) —
cada respuesta del camino feliz de las ocho tools termina, siempre, con el disclaimer
exacto de su categoría, y `packages/advisory/tests/test_disclaimers.py::test_disclaimers_en_todas`
verifica ese comportamiento para las ocho.

## Los tres disclaimers (texto literal)

Viven como constantes en `edecan_advisory._disclaimers` y se agregan al final de
`content` vía el helper `with_disclaimer(kind, texto)` — ningún módulo concatena el
string a mano.

| Constante | Texto exacto |
|---|---|
| `DISCLAIMER_LEGAL` | ⚖️ Este análisis es informativo y no constituye asesoría legal. Consulta a un abogado antes de tomar decisiones. |
| `DISCLAIMER_SALUD` | 🩺 Esta información es orientativa y no reemplaza a un profesional de la salud. No es un diagnóstico. |
| `DISCLAIMER_EDU` | 🧑‍🏫 Contenido educativo generado por IA; verifica con fuentes oficiales para evaluaciones formales. |

`analizar_laboratorio` antepone además una advertencia **reforzada** explícita (deja
claro que la detección de analitos es automática/por texto, no una interpretación
clínica, y que los rangos de referencia varían) — esa advertencia va ANTES del
disclaimer estándar en el `content`, nunca lo reemplaza: la respuesta sigue terminando
en el texto exacto de `DISCLAIMER_SALUD`.

## Las ocho herramientas

| Módulo | Herramienta | Qué hace | Categoría |
|---|---|---|---|
| `legal.py` | `analizar_contrato` | Extrae partes, objeto, vigencia, obligaciones clave y riesgos por cláusula de un contrato (`file_id` ya subido, o `texto` pegado directo) vía LLM. | legal |
| `legal.py` | `comparar_contratos` | `difflib.unified_diff` línea a línea entre dos versiones de un contrato (primeras 200 líneas) + resumen de cambios materiales vía LLM. | legal |
| `legal.py` | `generar_borrador_legal` | Rellena una plantilla interna en español (NDA, carta formal o acuerdo simple), la pule con el LLM y la guarda como archivo `.md` marcado explícitamente como BORRADOR. | legal |
| `salud.py` | `registrar_salud` | Inserta un registro en `health_logs` (medicamento, ejercicio, sueño, agua, hábito o medida corporal) con la hora del registro. | salud |
| `salud.py` | `resumen_salud` | Agrega los registros de una ventana de días por tipo: conteos, sumas de `valor.cantidad` (cuando es numérica) y rachas de días consecutivos. | salud |
| `salud.py` | `analizar_laboratorio` | Detecta analitos (nombre, valor, unidad) por expresión regular en el texto de un resultado de laboratorio y explica QUÉ MIDE cada uno, en general. | salud (reforzado) |
| `educacion.py` | `tutor_leccion` | Genera una lección (explicación + ejemplos + ejercicios) sobre un tema y nivel, la persiste en `learning_progress`, y muestra las preguntas SIN las respuestas correctas. | edu |
| `educacion.py` | `tutor_evaluar` | Corrige las respuestas del estudiante contra la última lección de ese tema, de forma tolerante a la redacción, y guarda el resultado. | edu |

Ninguna es `dangerous` ni requiere un flag de plan (`docs/roadmap.md` no lista
ningún flag para `edecan_advisory`): todas son de lectura/generación informativa, nunca
actúan sobre una cuenta externa del usuario ni mueven datos fuera del propio tenant.

## Límites explícitos (lo que estas herramientas NUNCA hacen)

- **No diagnóstico.** `analizar_laboratorio` detecta y explica QUÉ MIDE cada analito en
  términos generales — nunca dice si un valor está "normal", "alto" o "bajo" respecto a
  un rango clínico, ni sugiere qué hacer al respecto. El prompt que arma la explicación
  se lo prohíbe explícitamente al modelo (ver `salud._SYSTEM_PROMPT_LABORATORIO`), y
  tampoco recomienda medicamentos ni tratamientos.
- **No asesoría legal real.** `analizar_contrato`/`comparar_contratos` describen lo que
  el documento dice y señalan riesgos POTENCIALES en tono informativo — nunca dan una
  conclusión legal definitiva. `generar_borrador_legal` produce un **borrador**, marcado
  como tal en el propio texto de la respuesta ("⚠️ BORRADOR — revísalo con un abogado
  antes de firmarlo o enviarlo"), nunca un documento listo para firmar sin revisión.
- **No evaluación oficial.** `tutor_leccion`/`tutor_evaluar` generan contenido educativo
  con un LLM: útil para practicar, pero no equivale a una evaluación de una institución
  educativa real ni certifica ningún nivel de conocimiento.

## Privacidad de `health_logs`

`health_logs` (como toda tabla tenant-scoped del esquema, `ARCHITECTURE.md` §10.3) lleva
`tenant_id UUID NOT NULL` y Row-Level Security (política `tenant_isolation`,
`ARCHITECTURE.md` §2): bajo una sesión de API con `SET LOCAL app.tenant_id` fijado, solo
son visibles las filas del tenant actual — un tenant nunca puede leer, agregar ni
comparar los registros de salud de otro tenant a nivel de base de datos, sin importar lo
que pida el modelo. `registrar_salud`/`resumen_salud` además filtran explícitamente por
`user_id` (no solo por tenant): dentro de un mismo tenant multi-usuario, cada usuario ve
únicamente sus propios registros de salud. Lo mismo aplica a `learning_progress`
(progreso del tutor): tenant-scoped + filtrado por `user_id`.

No hay hoy un mecanismo de retención/purga específico para `health_logs` más allá del
genérico descrito en [`cumplimiento/privacidad.md`](./cumplimiento/privacidad.md) — un
registro de salud persiste mientras la cuenta esté activa, y se borra/anonimiza junto
con el resto de los datos del usuario al cancelar la cuenta.

## Decisiones de implementación

- **Extracción de texto compartida** (`edecan_advisory._texto`): un solo módulo junta la
  descarga de S3 (mismo patrón que `edecan_docanalysis._s3`/`edecan_creative._files`:
  SQL parametrizado sobre `ctx.session` + cliente `aioboto3` al vuelo) y la extracción de
  texto plano — PDF vía `pypdf`, DOCX vía `python-docx`, TXT/MD por decodificación UTF-8
  — capada a 100.000 caracteres antes de mandarla a cualquier LLM
  (`analizar_contrato`/`comparar_contratos`/`analizar_laboratorio` la reutilizan).
- **JSON tolerante del LLM**: los prompts de `analizar_contrato`, `tutor_leccion` y
  `tutor_evaluar` piden al modelo "responde ÚNICAMENTE con un JSON con esta forma". Como
  ningún proveedor garantiza 100% de cumplimiento de formato, `_util.extraer_json_llm`
  tolera que la respuesta venga envuelta en una cerca de código markdown o con texto
  alrededor; si de todas formas no logra extraer un objeto válido, la herramienta cae a
  valores por defecto ("no especificado") en vez de reventar el turno.
- **`tutor_evaluar` nunca pierde el turno por un LLM que no respetó el formato**: si la
  respuesta no trae `correcciones` con la misma longitud que los pares
  pregunta/respuesta, cae a una comparación case-insensitive determinista.
- **Plantillas legales con placeholders seguros** (`legal._plantillas`): un campo que el
  modelo no mandó se renderiza como `[campo]` en vez de reventar `generar_borrador_legal`
  — el propio "BORRADOR" en el texto de respuesta ya le avisa al usuario que debe
  revisarlo de todas formas.

## Tests

`packages/advisory/tests/` usa fakes locales por duck typing (`ctx` como
`SimpleNamespace`, `FakeSession`/`FakeLLM`) y genera sus propios archivos de prueba en
memoria (PDF mínimo construido a mano con `pypdf`, DOCX real con `python-docx`) — offline
y determinista, sin importar paquetes hermanos (`ARCHITECTURE.md` §10.1/§10.15). El test
más importante es `test_disclaimers.py::test_disclaimers_en_todas`, que corre las ocho
herramientas y verifica que cada una termina su respuesta con el disclaimer correcto.

```
cd packages/advisory && PYTHONPATH=. pytest
```
