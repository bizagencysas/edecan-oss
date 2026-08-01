# Perfil vivo

El perfil vivo es la respuesta de Edecán a "no solo recordar, sino *conocerte*"
(`ARCHITECTURE.md` §11): un resumen
**estructurado y acumulativo** del usuario — identidad declarada, gustos, proyectos, metas,
relaciones, empresas, hábitos — que se reconstruye solo, a partir de tus
conversaciones, y que influye en **cada respuesta** del asistente sin que
tengas que repetirte.

## 1. Qué es (y qué NO es)

Desde v1, Edecán ya tiene memoria de largo plazo: `memory_items`, hechos y
preferencias sueltos que `MemoryStore.search` recupera por relevancia
semántica en cada turno (ver `personalizacion-nivel-dios.md`). Eso sigue
existiendo tal cual — el perfil vivo NO lo reemplaza, se CONSTRUYE a partir de
él.

La diferencia es que `memory_items` es una bolsa de hechos sueltos ("vive en
Bogotá", "prefiere que le hablen de tú", "su aniversario es el 14 de
febrero"), mientras que el perfil vivo es una **síntesis** de esos hechos en
seis categorías con sentido de producto:

| Categoría | Qué guarda | Ejemplo |
|---|---|---|
| `identidad` | Nombre preferido, nombre completo, pronombres, ubicación, zona horaria, ocupación, idioma, forma de trato y biografía declarados por la persona | "Llámame Ana; háblame de tú y de forma directa" |
| `resumen` | 1-2 frases en 2ª persona, la "tarjeta de presentación" del usuario | "Prefieres respuestas breves y directas; trabajas en Acme y tu meta este año es correr una maratón." |
| `gustos` | Preferencias, cosas que le gustan | "Le gusta el café" |
| `proyectos` | En qué está trabajando | "Lanzamiento de Acme v2" |
| `metas` | Objetivos, a corto o largo plazo | "Correr una maratón este año" |
| `relaciones` | Personas relevantes y su rol | "Marta es su socia en el estudio" |
| `empresas` | Dónde trabaja, empresas relacionadas | "Trabaja en Acme" |
| `habitos` | Rutinas, costumbres | "Prefiere respuestas breves y directas" |

## 2. Cómo se construye

El perfil vivo se construye/actualiza como una **fase 3** del job
`memory_consolidate` (el mismo que ya corría en v1 tras cada turno de chat) —
ver el docstring completo en
`apps/worker/edecan_worker/handlers/memory_consolidate.py`:

```
turno de chat termina
        │
        ▼
POST /v1/conversations/{id}/messages encola memory_consolidate {user_id}
        │
        ▼
Fase 1 — extracción: el LLM saca hechos/preferencias nuevos del turno reciente
        │                                   y los inserta en memory_items
        ▼
Fase 2 — deduplicación: funde memory_items casi-duplicados (similitud coseno)
        │
        ▼
Fase 3 — PERFIL VIVO:
  1. reúne las 50 memorias más importantes del usuario (memory_items, ya con
     lo que insertó/depuró la fase 1+2)
  2. le pasa el perfil previo (user_profiles) + esas memorias a
     edecan_core.memory.build_profile — función PURA que arma el prompt,
     llama al LLM y hace el MERGE CONSERVADOR del resultado
  3. persiste el resultado en user_profiles, con version += 1
  4. ESPEJA el resumen como un memory_item nuevo: kind="fact",
     source="perfil_vivo", importance=1.0 (borrando el espejo anterior
     primero, para no acumular duplicados)
```

### 2.1 Merge conservador

`build_profile` (`packages/core/edecan_core/memory/profile.py`) nunca borra
una entrada del perfil previo solo porque el LLM "no la repitió" en su
respuesta — eso sería frágil (un modelo que se olvida de una entrada vieja no
debería borrarla). La regla es:

- El LLM propone entradas **nuevas** por categoría, y opcionalmente marca
  entradas viejas que una memoria reciente **contradice explícitamente**
  ("ya no trabajo en Acme" contradice la entrada "Trabaja en Acme").
- El código (no el LLM) hace el merge real: arranca del perfil previo, quita
  solo las entradas explícitamente contradichas, agrega las nuevas
  (deduplicadas sin importar mayúsculas/minúsculas), y recorta cada lista a
  20 entradas (priorizando lo antiguo sobre lo recién extraído si hay que
  recortar).
- El parseo de la respuesta del modelo es tolerante: si el LLM responde algo
  que no es JSON reconocible, o la llamada falla (proveedor no configurado,
  error de red...), el perfil **se conserva tal cual estaba** — nunca se
  corrompe ni se vacía por un fallo del LLM.

### 2.2 La tabla `user_profiles`

```
user_profiles(id, tenant_id, user_id, resumen text, datos jsonb, version int,
              created_at, updated_at)
UNIQUE(tenant_id, user_id)  +  Row-Level Security (tenant_isolation)
```

`datos` guarda `identidad` y las 6 categorías como JSON. `identidad` solo
cambia cuando la persona la edita; una reconstrucción con IA la conserva. Cada
consolidación exitosa incrementa `version` en 1 — es un contador simple, sin
historial de versiones anteriores (si necesitas auditar cambios, están en los
`memory_items` de origen, que sí conservan su propia fecha de creación).

## 3. Cómo influye en CADA respuesta (inyección garantizada)

Antes de cada turno, el endpoint de conversaciones lee `user_profiles`, crea
un contexto limpio con la identidad, el resumen y las categorías y lo coloca
en `ToolContext.extras["profile_context"]`. `Agent._recall_memories` lo
antepone siempre a las memorias semánticas, incluso si la memoria automática
está desactivada. El nombre y la forma de trato ya no dependen de que una
búsqueda vectorial considere el resumen "relevante" para ese mensaje.

El espejo `source="perfil_vivo"` se conserva para compatibilidad, búsqueda y
trabajos de fondo, pero dejó de ser la única vía de personalización.

## 4. API — `/v1/perfil`

Todas las rutas requieren `Authorization: Bearer <access_token>` (sin flag de
plan adicional: el perfil vivo está disponible en todos los planes, igual que
la memoria base). Ver `apps/api/edecan_api/routers/perfil.py`.

| Ruta | Qué hace |
|---|---|
| `GET /v1/perfil` | `{resumen, datos, version, updated_at}`. Si todavía no existe, devuelve identidad y listas vacías con `version: 0`. |
| `PUT /v1/perfil` | Patch parcial de resumen, identidad declarada y categorías. Los campos de identidad se pueden guardar por separado; las categorías se envían como listas completas. Incrementa `version`. |
| `DELETE /v1/perfil` | Derecho a reset: borra la fila de `user_profiles` **y** el espejo en `memory_items`. Si solo se borrara la fila, el espejo seguiría inyectándose en el próximo turno como si el perfil siguiera existiendo. |
| `POST /v1/perfil/rebuild` | Encola el mismo job `memory_consolidate` que corre tras cada turno (no un job especial "solo perfil") y responde `202` de inmediato — la reconstrucción real (extracción de memorias nuevas + fase 3) ocurre async en el worker; puede tardar unos segundos. |

## 5. Página web — `/app/perfil-vivo`

- Tarjeta **Quién eres** con la misma identidad editable de iOS y Android.
- Tarjeta de **resumen** editable (textarea + botón "Guardar
  resumen"), con la versión y fecha de última actualización.
- Una tarjeta por cada una de las 6 categorías, con chips agregables
  (input + botón "+") y eliminables (botón "×" en cada chip) — cada
  cambio dispara un `PUT /v1/perfil` inmediato con la lista nueva de esa
  categoría.
- Botón **"Reconstruir desde mis memorias"** (`POST /rebuild`), con aviso
  explícito de que la reconstrucción puede tardar unos segundos y no es
  instantánea.
- **"Borrar perfil"**, con confirmación de dos pasos (sin `window.confirm`,
  para mantener el mismo lenguaje visual del resto de la app).
- Nota de privacidad fija (ver §6).

Cliente HTTP en `apps/web/src/lib/api-perfil.ts` (duplica localmente el mismo
patrón de autenticación/refresh que `lib/api.ts`, ver el docstring de ese
archivo — `lib/api.ts` es compartido y este paquete de trabajo no lo toca).

## 6. Privacidad

`user_profiles` es una tabla tenant-scoped como cualquier otra
(ARCHITECTURE.md §2, §10.3): lleva `tenant_id` y Row-Level Security con la
política `tenant_isolation` — ningún otro tenant puede leerla ni escribirla,
sin importar qué rol de aplicación use. El espejo en `memory_items` hereda la
misma protección (es la misma tabla que usa la memoria de largo plazo desde
v1).

El usuario tiene control total: puede editar cualquier campo a mano (§4/§5),
y puede borrar su perfil por completo en cualquier momento — el borrado
elimina tanto la fila estructurada como su copia en memoria, así que deja de
influir en las respuestas de inmediato. Nada impide que el perfil se
reconstruya después a partir de nuevas conversaciones (es, después de todo,
"vivo").

## 7. Qué es real hoy vs. qué es diseño

| Pieza | Estado |
|---|---|
| `build_profile` (merge conservador, parseo tolerante) | **Real** — función pura, testeada con un LLM fake determinista (`packages/core/tests/test_profile_builder.py`) |
| Fase 3 de `memory_consolidate` (construye + persiste + espeja) | **Real** — contra el esquema real de `user_profiles`/`memory_items` de la migración `0003_v2_expansion` |
| `GET/PUT/DELETE/POST rebuild /v1/perfil` | **Real** — SQL parametrizado directo (mismo criterio que `edecan_api.routers.commerce` para tablas v2 nuevas) |
| Página web `/app/perfil-vivo` | **Real** — sin dependencias npm nuevas |
| Suite de evals `perfil_vivo.yaml` | **Real** — 4 casos multi-turno, formato idéntico a `memoria.yaml`/`persona_consistencia.yaml` |
| Garantía de que el perfil SIEMPRE se inyecta | **Real** — `profile_context` se carga desde `user_profiles` antes de cada turno |
| Historial de versiones anteriores del perfil (auditoría de cambios) | **Diseño** — hoy `version` es un contador simple sin snapshot; el rastro real está en los `memory_items` de origen |
| Reconstrucción "solo perfil" sin re-extraer memorias nuevas del turno reciente | **Diseño** — `POST /rebuild` hoy reencola el job completo (fases 1+2+3); un job más liviano que solo re-corra la fase 3 sobre memorias ya existentes queda como posible optimización futura |

## 8. Ver también

- `personalizacion-nivel-dios.md` — memoria de largo plazo base (`memory_items`) y `PersonaConfig`.
- `ARCHITECTURE.md` §9, §10.3, §10.7 — flujo de referencia de una conversación, esquema de datos, contrato de memoria.
- `apps/worker/edecan_worker/handlers/memory_consolidate.py` — docstring completo de las 3 fases.
- `packages/core/edecan_core/memory/profile.py` — docstring completo de `build_profile` (contrato, merge conservador, parseo tolerante).
