# Personalización "nivel Dios"

El diferenciador central de Edecán frente a un chatbot genérico: cada usuario define **quién es** su asistente — nombre, tono, formalidad, instrucciones permanentes, rasgos de personalidad — y el asistente acumula **memoria de largo plazo** sobre esa persona concreta. Todo vive en dos piezas: `PersonaConfig` (tabla `personas`, `ARCHITECTURE.md` §10.3 y §10.5) y el subsistema de memoria (`memory_items`/`memory_edges`, §10.7).

Se edita desde `GET`/`PUT /v1/persona` (ver [`api.md`](./api.md)) y se puede previsualizar el resultado exacto con `GET /v1/persona/preview`, que devuelve el `system_prompt` real que usará el agente sin gastar un turno de conversación.

## Qué controla cada campo de `PersonaConfig`

```python
class PersonaConfig(BaseModel):
    nombre_asistente: str = "Edecán"
    idioma: str = "es"
    tono: str = "cálido y profesional"
    formalidad: int = Field(default=1, ge=0, le=3)
    emojis: bool = False
    instrucciones: str = ""
    rasgos: list[str] = Field(default_factory=list)
    memoria_activada: bool = True
    voice_id: str | None = None
```

| Campo | Tipo | Qué controla |
|---|---|---|
| `nombre_asistente` | texto | Cómo se presenta el asistente y cómo se refiere a sí mismo en el `system_prompt` (`edecan_core.persona.build_system_prompt`). Es lo primero que el usuario nota — "Edecán" es solo el default. |
| `idioma` | código de idioma (`es`, `en`, ...) | En qué idioma responde por defecto. Español (`es`) es first-class en todo el producto; otros idiomas funcionan porque el LLM los soporta, pero la UI y la documentación siguen en español. |
| `tono` | texto libre | Descripción en lenguaje natural del carácter de las respuestas (p. ej. `"cálido y profesional"`, `"directo y sin rodeos"`, `"analítico y minucioso"`). Se inyecta literalmente en el `system_prompt`. |
| `formalidad` | entero `0`–`3` | Nivel de formalidad del trato en español: **0** = tú, muy informal; **1** = tú, profesional (default); **2** = usted, cordial; **3** = usted, muy formal/protocolar. `build_system_prompt` traduce este número a instrucciones concretas de tratamiento (tú↔usted, registro léxico). |
| `emojis` | booleano | Si el asistente puede usar emojis en sus respuestas de texto. `False` por defecto — pensado para personas como un CFO o un asistente ejecutivo formal, donde los emojis restan seriedad. |
| `instrucciones` | texto libre (multilínea) | Instrucciones permanentes del usuario: reglas de negocio, cosas que el asistente siempre debe o nunca debe hacer, contexto fijo ("mi empresa se llama...", "nunca agendes reuniones los viernes"). Se inyecta en una **sección delimitada** del `system_prompt` — y por diseño **nunca puede anular las reglas de seguridad** del propio sistema (p. ej. no puede desactivar la confirmación humana de herramientas `dangerous=True`, ni instruir al asistente a ignorar el aislamiento entre tenants). Es personalización de comportamiento, no un mecanismo de escalar privilegios. |
| `rasgos` | lista de textos | Rasgos de personalidad cortos y puntuales (p. ej. `["directo", "con humor seco", "impaciente con la ambigüedad"]`) que matizan el tono sin repetir toda una instrucción larga. |
| `memoria_activada` | booleano | Si el asistente consulta (`MemoryStore.search`) y guarda (`MemoryStore.add`) memoria de largo plazo para este usuario. En `False`, el turno de conversación no recupera memorias previas para construir el `system_prompt` ni dispara el job `memory_consolidate` — pero **no borra** lo que ya existía en `memory_items` (ver más abajo cómo borrarlo explícitamente). |
| `voice_id` | texto o `null` | Voz específica para TTS de este usuario/persona, si el proveedor la soporta (ElevenLabs, Polly). Si es `null`, se usa el default de la plataforma (`ELEVENLABS_VOICE_ID` o `POLLY_VOICE`, ver [`configuracion.md`](./configuracion.md)). |

## Cómo se arma el `system_prompt`

`edecan_core.persona.build_system_prompt(persona, memories, extra_context=None)` construye el prompt final combinando, en este orden conceptual:

1. Identidad del asistente (`nombre_asistente`, `idioma`, `tono`, `rasgos`, `formalidad` → tú/usted, `emojis`).
2. Reglas de seguridad del sistema (fijas, no personalizables) — incluyen el comportamiento de confirmación de herramientas peligrosas y los límites de lo que el agente puede hacer en nombre del usuario.
3. Sección delimitada con las `instrucciones` del usuario — se respetan como preferencias de comportamiento, pero **no** pueden pisar el punto 2.
4. Memorias relevantes recuperadas para el turno actual (si `memoria_activada`), como contexto, no como instrucciones.
5. `extra_context` opcional (p. ej. contexto inyectado por el flujo de telefonía o el companion).

`GET /v1/persona/preview` devuelve exactamente este texto renderizado, para que la UI de configuración pueda mostrarlo mientras el usuario ajusta los campos.

## Tres personas de ejemplo

Estos son valores completos de `PersonaConfig` — pueden pegarse tal cual en el body de `PUT /v1/persona`.

### 1. «CFO personal»

Pensado para alguien que quiere control financiero estricto y respuestas basadas en números, sin adornos.

```json
{
  "nombre_asistente": "Valeria",
  "idioma": "es",
  "tono": "analítico, preciso y basado en datos; nunca optimista sin evidencia",
  "formalidad": 2,
  "emojis": false,
  "instrucciones": "Antes de responder cualquier pregunta de finanzas, consulta resumen_finanzas o registrar_transaccion en vez de estimar de memoria. Si un gasto supera 500000 COP y no tiene categoría, pregúntame la categoría antes de registrarlo. Al inicio de cada semana (lunes), si te lo pido, dame un resumen de flujo de caja del mes en curso comparado con el mes anterior. Nunca redondees cifras de forma que oculten un déficit.",
  "rasgos": ["conservadora con el gasto", "detallista", "cuestiona supuestos optimistas"],
  "memoria_activada": true,
  "voice_id": null
}
```

### 2. «Asistente ejecutivo formal»

Para gestión de agenda, correo y contactos con un registro protocolar, tipo jefe de gabinete.

```json
{
  "nombre_asistente": "Ricardo",
  "idioma": "es",
  "tono": "formal, discreto y eficiente; va directo al punto sin perder cortesía",
  "formalidad": 3,
  "emojis": false,
  "instrucciones": "Trátame siempre de usted. Antes de agendar una reunión, confirma que no choque con nada existente usando agenda_eventos. Los correos que redactes en mi nombre deben llevar cierre formal y nunca se envían sin mi confirmación explícita. Si un contacto no está en la agenda, créalo con gestionar_contacto antes de continuar. Prioriza siempre lo que yo marque como urgente sobre lo recurrente.",
  "rasgos": ["protocolar", "meticuloso con la agenda", "nunca informal con desconocidos"],
  "memoria_activada": true,
  "voice_id": "TU_VOICE_ID_ELEVENLABS_AQUI"
}
```

### 3. «Coach cercano»

Para acompañamiento personal diario, tono cálido y cercano, con memoria activa de objetivos y estado de ánimo.

```json
{
  "nombre_asistente": "Sole",
  "idioma": "es",
  "tono": "cercano, cálido y motivador, como una amiga que también te exige",
  "formalidad": 0,
  "emojis": true,
  "instrucciones": "Trátame de tú. Cada vez que hablemos, si no lo hemos hecho hoy, pregúntame brevemente cómo estoy antes de entrar en tareas. Recuerda mis metas activas y celebra el progreso, pero también sé honesta si llevo días sin avanzar en algo que dije que era importante. No me sermonees, solo nómbralo una vez.",
  "rasgos": ["empática", "motivadora", "honesta aunque incómodo"],
  "memoria_activada": true,
  "voice_id": null
}
```

## Cómo funciona la memoria

La memoria de largo plazo vive en dos tablas (`ARCHITECTURE.md` §10.3):

- **`memory_items`** — hechos, preferencias, eventos o entidades individuales: `kind` (`fact|preference|event|entity`), `content` (texto), `embedding` (`vector(1536)`, opcional), `importance` (float) y `source` (de dónde salió: una conversación, un archivo, una entrada manual vía `POST /v1/memory`).
- **`memory_edges`** — relaciones entre dos `memory_items` (`src_id`, `dst_id`, `relation`) para modelar el grafo de memoria (p. ej. "Mateo" —`hijo_de`→ el usuario).

### Cómo se llena

1. **Automáticamente**: al final de cada turno, el worker corre el job `memory_consolidate`, que revisa la conversación reciente y extrae hechos/preferencias nuevos o actualiza la importancia de los existentes, generando su `embedding` con el `Embedder` configurado (`HashEmbedder`, determinista y offline por defecto, o `OpenAICompatEmbedder` si configuraste `EMBEDDINGS_MODEL`).
2. **Manualmente**: `POST /v1/memory` para que el propio usuario (o una integración) agregue un hecho explícito.

### Cómo se usa

En cada turno, si `persona.memoria_activada` es `true`, el agente llama a `MemoryStore.search(tenant_id, user_id, query, k=8)` (`PgMemoryStore`, similitud coseno sobre `pgvector`) para traer las memorias más relevantes al mensaje actual, y las pasa como contexto — no como instrucciones — a `build_system_prompt`. El grafo (`memory_edges`) permite además navegar relaciones (`neighbors`) cuando una memoria trae otras conectadas.

### Cómo se borra

- **Un recuerdo puntual**: `DELETE /v1/memory/{id}` (ver [`api.md`](./api.md)) — irreversible, `204 No Content`.
- **Detener la memoria sin borrar el historial**: poner `memoria_activada: false` en `PersonaConfig` vía `PUT /v1/persona`. El asistente deja de leer y de escribir memoria nueva, pero **lo que ya existe permanece almacenado** hasta que se borre explícitamente.
- **Borrar toda la memoria de un usuario**: hoy la API expone borrado por elemento (`DELETE /v1/memory/{id}`), no un endpoint de "vaciar todo" en un solo llamado — la forma soportada es iterar `GET /v1/memory` y borrar cada resultado. Para self-hosters con acceso directo a la base de datos, un `DELETE FROM memory_items WHERE tenant_id = '<uuid>' AND user_id = '<uuid>'` (respetando RLS, es decir conectado como `app_user` con `app.tenant_id` fijado, o como owner filtrando explícitamente) logra lo mismo en una sola operación. Un endpoint dedicado de borrado masivo (equivalente a un derecho de supresión de datos personales) está anotado como trabajo pendiente en [`cumplimiento/privacidad.md`](./cumplimiento/privacidad.md).
- **Borrar todo el tenant**: no hay un botón único documentado aquí; implica borrar en cascada `memory_items`, `memory_edges`, `messages`, `conversations`, `oauth_tokens`, `connector_accounts`, `files`/`file_chunks` y finalmente el propio tenant — ver [`cumplimiento/privacidad.md`](./cumplimiento/privacidad.md) para la postura de retención/borrado a nivel de cuenta.

## Relación con la voz

Si `voice_id` está definido, se usa para TTS (`POST /v1/voice/speak` y, en `premium/`, las respuestas TwiML de telefonía) en vez del default de la plataforma. Esto permite que cada persona (no solo cada tenant) tenga una voz propia coherente con su carácter — por ejemplo, la "Sole" cercana del ejemplo 3 puede sonar distinta al "Ricardo" formal del ejemplo 2, aunque compartan el mismo tenant y el mismo proveedor TTS.
