# Barrido v7 — routers restantes (WP-V7-08)

Este documento registra el barrido de los **17 routers de `apps/api/edecan_api/routers/`
que ningún barrido de evidencia anterior recorrió** (`missions`, `automations`, `ide`,
`smarthome`, `skills`, `setup`, `voice`, `companion`, `memory`, `files`, `contacts`,
`finance`, `reminders`, `me`, `persona`, `admin`, `usage`), más `packages/automations/`.
`docs/cumplimiento/barrido-evidencia-v6.md` (WP-V6-03) cubrió otro conjunto —
`auth`/`billing`/`connectors`/`credentials`/`consents`/`erp`/`rrhh`/`viajes`/`mensajes`/
`negocios`/`perfil` + `hooks.py` (fix propio) + `premium/` — y los fixes previos de
`remote.py`/`commerce.py`/`ads.py`/`voz_avanzada.py` ya están verificados ahí. Leído
completo antes de escribir una línea: `DIRECCION_ACTUAL.md`, `ARCHITECTURE.md` §0/§10/§13,
`HOTFIXES_PENDIENTES.md` completo (imprescindible: el fix de `delegar_mision`/
`_cupo_disponible` y el de `confirm_tool_call`), y `docs/cumplimiento/barrido-evidencia-v6.md`.

Cuatro barridos, igual criterio metodológico que v6: **A** (bring-your-own), **B** (flags de
plan finos), **C** (evidencia/estado escrito antes de un `raise`/enqueue alcanzable, sin
commit de por medio — regla de `HOTFIXES_PENDIENTES.md` puntos 8/9), **D** (SQL crudo contra
el esquema REAL, no uno asumido).

## Resumen ejecutivo

- **Hallazgo candidato del enunciado (paridad `delegar_mision` ↔ `gestionar_automatizacion`
  para `LIMIT_AUTOMATIONS_ACTIVE`): investigado y descartado — ya estaba resuelto desde
  WP-V6-02**, antes de que este WP empezara. Ver sección dedicada abajo con la evidencia.
- **2 hallazgos reales nuevos, corregidos**: `files.py::_check_storage_quota` y
  `voice.py::_check_voice_quota` defaulteaban a `UNLIMITED` (no a `0`) cuando
  `tenant.flags` no trae la clave de límite — exactamente lo que pasa con un `plan_key`
  huérfano (`edecan_api.deps.flags_for_plan` devuelve `{}`). `files.py` no tiene ningún
  flag booleano previo que lo cubra, así que era alcanzable de verdad (almacenamiento sin
  ningún límite); `voice.py` sí lo tiene (`voice.web`, `403` antes de llegar a la cuota) así
  que era defensa en profundidad. Ambos corregidos a `0` (fail-closed), mismo criterio que
  ya usa `missions.py::_check_missions_quota`. Detalle completo abajo.
- El resto de los 17 routers: **sin hallazgos nuevos** — BARRIDO A/B/C/D limpios, con
  evidencia (lectura de código + tests, incluidos 2 tests de integración contra Postgres
  real para BARRIDO D de `missions.py`/`automations.py`).
- 3 hallazgos **fuera de mi alcance**, documentados para WP-V7-12: `docs/api.md` §`/v1/setup`
  desactualizado, `companion.py` sin registro persistente de emparejamiento (gap YA
  documentado en `docs/control-remoto.md`, requiere `devices.py`), y el patrón fail-open de
  `UNLIMITED` en `conversations.py`/`connectors.py`/`usage.py` (mismo patrón que corregí en
  `files.py`/`voice.py`, pero esos 3 archivos no están en mis rutas).

---

## 1. Hallazgo candidato del enunciado — investigado y descartado

**Pregunta**: ¿la tool `gestionar_automatizacion` (`packages/automations/edecan_automations/
tools.py`) aplica `LIMIT_AUTOMATIONS_ACTIVE` igual que lo aplica el router
`automations.py`, análogo al bug real que tenía `delegar_mision` antes de su fix?

**Respuesta: NO es el mismo bug.** `GestionarAutomatizacionTool._bajo_limite` YA aplica
`LIMIT_AUTOMATIONS_ACTIVE` — y lo hace desde **WP-V6-02**, con la misma semántica
fail-closed que el router:

```python
# packages/automations/edecan_automations/tools.py
async def _bajo_limite(self, ctx: ToolContext) -> bool:
    limite = _tenant_flags(ctx).get(LIMIT_AUTOMATIONS_ACTIVE, 0)
    if limite == _UNLIMITED:
        return True
    resultado = await ctx.session.execute(
        text("SELECT COUNT(*) FROM automations WHERE tenant_id = :tenant_id AND enabled = true"),
        {"tenant_id": str(ctx.tenant_id)},
    )
    activas = resultado.scalar_one()
    return activas < limite
```

vs. el router (`apps/api/edecan_api/routers/automations.py::_check_limit`):

```python
async def _check_limit(session, *, tenant_id, flags) -> None:
    limite = flags.get(LIMIT_AUTOMATIONS_ACTIVE, 0)
    if limite == _UNLIMITED:
        return
    if await _count_enabled(session, tenant_id=tenant_id) >= limite:
        raise HTTPException(403, ...)
```

Mismo criterio exacto: `-1` (`UNLIMITED`) salta el `COUNT`; `0` (explícito o ausente)
deniega sin excepción (el router lo hace comparando `count >= 0`, siempre verdadero; la
tool comparando `activas < 0`, siempre falso — funcionalmente idéntico, la tool paga una
consulta de más que el router evita con un atajo explícito, diferencia de estilo sin
impacto de seguridad); cualquier límite positivo compara contra el mismo `SELECT COUNT(*)
FROM automations WHERE tenant_id = ... AND enabled = true`. El chequeo corre **antes** de
`INSERT`/`UPDATE` en ambos casos (`_crear`/`_set_enabled` en la tool, `create_automation`/
`update_automation` en el router). `requires_flags` de la tool también coincide
(`FLAG_AUTOMATIONS_RULES`, gate binario, separado del límite numérico).

**¿Por qué NO es el mismo bug que `delegar_mision`?** El bug real de `delegar_mision`
(`HOTFIXES_PENDIENTES.md`, "RESUELTO 2026-07-09") era que `DelegarMisionTool.run()` **no
tenía ningún chequeo de `LIMIT_MISSIONS_PER_DAY` en absoluto** — insertaba y encolaba sin
mirar la cuota, mientras que el router sí la revisaba. Acá, desde WP-V6-02,
`GestionarAutomatizacionTool` ya replica el chequeo — el paquete ya tenía su propio pin de
regresión (`packages/automations/tests/test_v6_paridad_flag_router.py`, que compara el
VALOR de la constante local contra `edecan_schemas.plans`) y 6+ tests de comportamiento en
`test_tools.py` (`test_crear_en_el_limite_del_plan_no_inserta`,
`test_activar_en_el_limite_no_actualiza`, `test_desactivar_exitoso_no_chequea_limite`,
etc.) que ya pasaban ANTES de que este WP tocara nada.

**Verificación adicional que sí faltaba** (agregada por este WP,
`packages/automations/tests/test_v7_verificacion_limite_automatizaciones.py`, 4 tests
nuevos): el caso límite exacto que si el bug existiera lo habría revelado —
`ctx.extras["flags"]` con la clave `LIMIT_AUTOMATIONS_ACTIVE` **ausente por completo**
(no `0` explícito, el estado real que deja un `plan_key` huérfano vía
`edecan_api.deps.flags_for_plan`) para `crear`/`activar` — ningún test previo de
`test_tools.py` ejercitaba exactamente esa combinación (solo `desactivar`, que a propósito
no chequea límite). Los 4 tests pasan contra el código tal cual está hoy, sin ningún fix
necesario, más un centinela estructural (`inspect.getsource`) que ancla el default `0` de
`_bajo_limite` para que una futura "simplificación" a `UNLIMITED` (exactamente la clase de
bug que sí encontré en `files.py`/`voice.py`, ver §3) no pase desapercibida.

**Revisado el resto de tools del paquete** (`edecan_automations.runner`/`engine`, único
paquete adicional que este WP podía tocar): `runner.py::_build_safe_registry` ya excluye
`gestionar_automatizacion`/`delegar_mision` por nombre de CUALQUIER run headless (además de
filtrar por `dangerous=True`, doble barrera deliberada contra que una automatización
dispare más trabajo autónomo en cadena, ver su docstring) — sin hallazgos.
`get_all_tools()` solo expone `GestionarAutomatizacionTool` (una sola tool en todo el
paquete).

---

## 2. Barrido D — esquema real vs. SQL crudo (`missions.py`/`automations.py`)

Los únicos dos de los 17 routers que hablan SQL parametrizado directo contra tablas
propias son `missions.py` (`agent_missions`/`agent_steps`) y `automations.py`
(`automations`/`automation_runs`) — el resto usa `Repo`/`edecan_skills.store`/el
companion. Mismo objetivo que la clase de bug crítico que v6 encontró en
`apps/api/edecan_api/routers/reuniones.py` (`HOTFIXES_PENDIENTES.md`): un router que
escribe SQL contra un esquema "asumido" en vez del real, invisible a un `FakeSession` que
mockea filas con el mismo esquema equivocado que el código.

**Leídos ambos contra la fuente real** (no contra ningún README propio): la migración
`packages/db/alembic/versions/0003_v2_expansion.py` y el modelo SQLAlchemy
`packages/db/edecan_db/models.py` (`AgentMission`/`AgentStep`/`Automation`/
`AutomationRun`). Coinciden exactamente:

| Columnas que usa el router | Columnas reales (migración + modelo) | Coincide |
|---|---|---|
| `missions.py::_MISSION_COLUMNS` (11) | `agent_missions` (11) | ✔ |
| `missions.py::_STEP_COLUMNS` (11) | `agent_steps` (11) | ✔ |
| `automations.py::create_automation` INSERT (9 de 12) | `automations` (12) | ✔ (subconjunto válido) |
| `automations.py::list_automation_runs` SELECT (5 de 9) | `automation_runs` (9) | ✔ (subconjunto válido) |

**Verificado empíricamente, no solo por lectura** — dos formas complementarias, ambas en
`apps/api/tests/test_v7_sweep_routers_restantes.py`:

1. **Estructural, sin Postgres** (corre siempre): 4 tests que comparan por introspección
   (`Table.columns.keys()`) las columnas literales de `_MISSION_COLUMNS`/`_STEP_COLUMNS`/el
   INSERT de `create_automation`/el SELECT de `list_automation_runs` contra
   `edecan_db.models` — detecta un desajuste incluso en una corrida sin `DATABASE_URL`.
2. **Empírico, `@pytest.mark.integration`** (Postgres desechable, `docker run pgvector/
   pgvector:pg16`, migrado a `head` con Alembic real): dos tests de ciclo de vida completo
   que llaman DIRECTO a las funciones reales del router (`missions.create_mission` →
   `list_missions` → `get_mission_detalle` → `confirm_mission` → `cancel_mission`;
   `automations.create_automation` → `list_automations` → `update_automation` →
   `probar_automation` → `list_automation_runs` → `delete_automation`) contra una base
   migrada de verdad. Si `_MISSION_COLUMNS`/el INSERT de `create_automation` tuvieran una
   columna que no existe, esto revienta con `UndefinedColumnError` en vez de pasar en
   silencio.

```
$ docker run -d --name edecan-v7-routers-pg -e POSTGRES_USER=edecan \
    -e POSTGRES_PASSWORD=edecan -e POSTGRES_DB=edecan -p 55480:5432 pgvector/pgvector:pg16
$ DATABASE_URL="postgresql+asyncpg://edecan:edecan@localhost:55480/edecan" \
    uv run --all-packages pytest -q apps/api/tests/test_v7_sweep_routers_restantes.py
14 passed in 0.99s
```

**Veredicto: Seguro.** Ninguno de los dos routers tiene el patrón de `reuniones.py`.

---

## 3. Los 2 hallazgos reales — fail-open en `files.py`/`voice.py`, corregidos

### Hallazgo

`edecan_api.deps.flags_for_plan(plan_key)` devuelve `{}` (dict vacío) si `plan_key` no
existe en `edecan_schemas.plans.PLANES` — su propio docstring lo documenta como un caso
real y anticipado: *"p. ej. quedó huérfano tras un cambio de catálogo de planes"*. Un
`plan_key` así llega a `TenantCtx.flags` de cualquier request autenticada (el JWT no
valida `plan` contra el catálogo al firmarlo — `create_access_token` acepta cualquier
string).

Grep de **todos** los sitios de `apps/api/edecan_api/routers/` que leen un `LIMIT_*` de
`tenant.flags` (no solo mis 17 archivos, para calibrar si esto era un patrón nuevo de v7 o
preexistente):

| archivo | default cuando la clave falta | ¿en mis rutas? |
|---|---|---|
| `conversations.py` (mensajes/día) | `UNLIMITED` | No — fuera de mi alcance |
| `connectors.py` (números de teléfono) | `UNLIMITED` | No — fuera de mi alcance |
| `usage.py` (los 5, solo DISPLAY) | `UNLIMITED` | Sí, pero solo lectura/dashboard — ver nota abajo |
| `missions.py` (misiones/día) | `0` | Sí — ya correcto |
| `automations.py` (automatizaciones activas) | `0` | Sí — ya correcto |
| **`files.py` (almacenamiento)** | **`UNLIMITED` → corregido a `0`** | **Sí — hallazgo real** |
| **`voice.py` (minutos de voz)** | **`UNLIMITED` → corregido a `0`** | **Sí — hallazgo real** |

`UNLIMITED` como default es, de hecho, el patrón **mayoritario y preexistente** en este
código base (5 de 7 sitios, incluido el gate de mensajes de `conversations.py` — el más
usado de toda la API) — no es una regresión introducida por v7. Pero es la clase exacta de
"fail-open en vez de fail-closed" que `ide.py::_require_companion_ide` documenta
explícitamente como el criterio correcto ("`tenant.flags.get(..., False)` es tolerante a
un plan huérfano... fail-closed, no fail-open") y que `missions.py::_check_missions_quota`
ya aplicaba para su propio límite. Con `files.py`/`voice.py` corregidos, la proporción
pasa de "5 fail-open / 2 fail-closed" a "3 fail-open / 4 fail-closed" — mejora neta sin
tocar ningún archivo fuera de mi alcance.

### Por qué `files.py` era explotable de verdad (no solo teórico)

`POST /v1/files` **no tiene ningún flag booleano previo** que bloquee un `plan_key`
huérfano — a diferencia de `voice.py` (`_require_voice_web` ya devuelve `403` antes de
llegar a la cuota, para flags `{}` la clave `FLAG_VOICE_WEB` también falta y por defecto
es `False` — ver `test_voice.py::test_transcribe_without_voice_web_flag_returns_403`, que
YA prueba `plan_key="plan_no_existe"` y confirma que ese primer candado corta ahí). Antes
del fix, un tenant con `plan_key` huérfano podía subir archivos a S3 **sin ningún límite
de almacenamiento** — `_check_storage_quota` devolvía inmediatamente al ver
`limit_mb == UNLIMITED`, sin siquiera consultar el uso acumulado.

Para `voice.py`, el mismo default incorrecto existía en `_check_voice_quota`, pero era
inalcanzable en la práctica por el primer gate — corregido igual, por defensa en
profundidad (si alguien reordena los dos chequeos en el futuro, o si `ctx.extras["flags"]`
llega a ser parcial en vez de completo-o-vacío como es hoy, el segundo candado ya está
bien).

### Fix

Un cambio de una palabra en cada archivo — el default del `.get()`, de `UNLIMITED` a `0`
— con comentario explicando el razonamiento y la referencia cruzada entre los dos:

```python
# apps/api/edecan_api/routers/files.py
limit_mb = tenant.flags.get(LIMIT_STORAGE_MB, 0)  # antes: UNLIMITED

# apps/api/edecan_api/routers/voice.py
limit_minutes = tenant.flags.get(LIMIT_VOICE_MINUTES_MONTH, 0)  # antes: UNLIMITED
```

**Por qué es seguro para los 4 planes reales**: `edecan_schemas.plans.PLANES` fija
`LIMIT_STORAGE_MB`/`LIMIT_VOICE_MINUTES_MONTH` explícitamente en las 4 entradas
(`free_selfhost`/`hosted_basic`/`hosted_pro`/`hosted_business`) — el nuevo default `0`
NUNCA se alcanza para un plan real, solo para el caso `flags == {}` (huérfano). Verificado
con un test dedicado por archivo que confirma que un plan real conserva su límite exacto
sin cambios (`test_check_storage_quota_plan_conocido_conserva_su_limite_real`,
`test_check_voice_quota_plan_conocido_conserva_su_limite_real`).

### Tests (`apps/api/tests/test_v7_sweep_routers_restantes.py`)

- `test_check_storage_quota_plan_huerfano_deniega_en_vez_de_ilimitado` / `test_check_voice_quota_plan_huerfano_deniega_en_vez_de_ilimitado` — llaman la función DIRECTO con `TenantCtx(flags={})`, confirman `429` en vez de "pasa sin más".
- `test_upload_file_plan_huerfano_devuelve_429_no_ilimitado` — regresión a NIVEL HTTP real (`POST /v1/files` con `plan_key="plan_no_existe"`, S3 mockeado): `429`, y `s3_calls == []` (nunca llega a subir nada).
- `test_check_storage_quota_plan_conocido_conserva_su_limite_real` / `test_check_voice_quota_plan_conocido_conserva_su_limite_real` — contraparte de no-regresión con un plan real (`hosted_basic`).

Verificado manualmente que `test_upload_file_plan_huerfano_devuelve_429_no_ilimitado`
FALLA si se revierte el fix (se restauró `UNLIMITED` temporalmente, se corrió el test,
falló con `assert 200 == 429` y `s3_calls` con 1 elemento — subió el archivo sin límite —
y se restauró el fix).

---

## 4. Tabla completa — los 17 routers

Veredicto por barrido, uno por archivo (Seguro / N-A / Hallazgo). "N-A" = el barrido no
aplica a ese archivo (p. ej. no hay credencial BYO que revisar, o no hay flag de plan por
diseño).

| Router | A — BYO | B — Flags | C — Evidencia | D — Esquema | Notas |
|---|---|---|---|---|---|
| `missions.py` | N-A (sin credenciales) | Seguro — `agents.missions` (`_require_agents_missions`) + `LIMIT_MISSIONS_PER_DAY` ya fail-closed (`0` default) desde antes de este WP | Seguro — `create_mission`/`confirm_mission` encolan DESPUÉS de escribir estado, pero no hay evidencia legal involucrada (no es audit_log/consent/SMS): un fallo de `enqueue` revierte la sesión completa junto con el `UPDATE`/`INSERT`, comportamiento atómico correcto, no el patrón de `hooks.py` (ver test dedicado `test_confirm_mission_si_enqueue_falla_la_sesion_revierte_el_status`) | Seguro — ver §2 | — |
| `automations.py` | N-A | Seguro — `automations.rules` (`_require_automations_flag`) + `LIMIT_AUTOMATIONS_ACTIVE` (`_check_limit`, `0` default) | Seguro — mismo razonamiento que `missions.py`; `probar_automation` encola tras un SELECT puro (nada que perder); `create`/`update` no encolan | Seguro — ver §2 | — |
| `ide.py` | N-A (proxy al companion, sin credenciales propias) | Seguro — las 7 rutas (`status`/`tree`/`file` GET/PUT/`edit`/`run`/`search`) exigen `companion.ide` vía `_require_companion_ide` (precedente `_ACCIONES_IDE`, ya verificado intacto) — cubierto exhaustivamente por `test_ide_router.py::test_all_endpoints_are_reachable_with_a_known_plan`/`test_status_without_the_companion_ide_flag_is_forbidden`/`test_companion_ide_flag_is_true_for_every_real_plan` | N-A — este router nunca escribe en la base de datos, todo pasa por el companion vía WebSocket | N-A — sin SQL crudo | — |
| `smarthome.py` | Seguro — token del propio tenant (`vault.put`/`vault.get`), sin ningún fallback a config de plataforma; ping (`GET {base_url}/api/`) SIEMPRE antes de persistir | Seguro (por diseño) — **sin flag de plan**, instrucción explícita del work package original (`edecan_smarthome.tools` docstring: "no toques edecan_schemas") — verificado con `test_smarthome_status_no_exige_ningun_flag_de_plan` (200 con `plan_key="plan_no_existe"`) | Seguro — `put_credentials`: ping antes de `vault.put`+`add_audit_log`; `add_audit_log` es la ÚLTIMA operación en `put_credentials`/`delete_credentials`, nada después que pueda lanzar (mismo patrón ya "Seguro" que `credentials.py` en barrido-evidencia-v6.md) | N-A — usa `Repo`, sin SQL crudo | — |
| `skills.py` | N-A — instalación sin ninguna credencial (fetch anónimo a GitHub raw / búsqueda pública en skills.sh) | Seguro (por diseño) — **sin flag de plan**, `edecan_skills.tools` docstring lo documenta igual que `smarthome` — verificado con `test_skills_list_no_exige_ningun_flag_de_plan` | Seguro — **verificado el punto explícito del enunciado**: `edecan_skills.store.insert_skill` corre `escanear_inyeccion` (scan) ANTES de decidir `enabled` y de persistir la fila (`enabled = not escanear_inyeccion(contenido)`, calculado antes del `INSERT`) — nunca una skill con hallazgos queda `enabled=true` por un instante. Ya cubierto por `test_skills_router.py::test_install_con_hallazgos_queda_desactivada_y_los_expone` | N-A — usa `edecan_skills.store`, sin SQL crudo en el router mismo | `capacidades_peligrosas` (dangerous capabilities declaradas) NO gatea el auto-enable al instalar (solo lo hace `escanear_inyeccion`) — investigado y es DISEÑO deliberado, no bug: declarar una capacidad "no le da a la skill ningún poder real" (`security.py` docstring, `usar_skill` nunca ejecuta código), y el clic humano en "Instalar" ya es la confirmación (ver docstring de `routers/skills.py`) |
| `setup.py` | N-A | N-A — solo requiere autenticación, sin flag de plan (es un endpoint de diagnóstico/detección, no una capacidad) | N-A — solo lectura, sin escrituras | N-A — sin SQL crudo | Agregada sección faltante a `docs/configuracion.md` con la forma REAL de `/status`/`/detect` (ver §5) |
| `voice.py` | Seguro — `_stt_para_tenant`/`_tts_para_tenant` fail-closed intactos (tenant → stub, nunca plataforma); gate de Polly (`EDECAN_LOCAL_MODE` + `allow_ambient_credentials=True` solo ahí) intacto — verificados leyendo el código línea por línea contra el fix de v5 (`HOTFIXES_PENDIENTES.md`) | Seguro — `voice.web` (`_require_voice_web`) + `LIMIT_VOICE_MINUTES_MONTH` — **hallazgo real corregido, ver §3** | Seguro — `add_usage_event` es la última operación de `transcribe`/`speak`, nada después que pueda revertirla, y no es evidencia legal | N-A — usa `Repo`, sin SQL crudo | **Hallazgo corregido, ver §3** |
| `companion.py` | N-A | N-A — `companion` es `True` en los 4 planes (`ARCHITECTURE.md` §10.13), sin flag fino documentado en el contrato v1 pinned para `pair-code`/`ws` | **N-A con nota**: este router NUNCA escribe evidencia de emparejamiento en ninguna tabla — `ConnectionManager.connect`/`.disconnect` solo `logger.info`, nada en `audit_log` ni en `devices`. NO es un bug de rollback (no hay escritura que perder), es una AUSENCIA total de evidencia persistente — y es un gap YA DOCUMENTADO, no descubierto por este WP: `docs/control-remoto.md` línea ~106 ya lo señala explícitamente ("a diferencia del pairing de hoy, que no dejaba ningún registro persistente") como trabajo pendiente que requiere integrar con la tabla `devices` — `devices.py` está fuera de mi alcance (regla dura del WP). Reportado para WP-V7-12/un WP de control remoto de seguimiento, no corregido acá | N-A — sin SQL crudo (usa Redis) | Ver nota arriba |
| `memory.py` | N-A | N-A — CRUD core, sin flag (memoria activada/desactivada es config de `PersonaConfig`, no un flag de plan) | N-A — sin escrituras de evidencia | N-A — usa `Repo` | — |
| `files.py` | N-A (S3 de plataforma, no BYO — mismo criterio que siempre, `S3_BUCKET` es infraestructura de la plataforma, no una credencial de tenant) | Seguro — **hallazgo real corregido, ver §3** (sin flag booleano, solo el límite numérico) | Seguro con nota: `upload_file` sube a S3 (efecto real) ANTES de `repo.create_file`/`add_usage_event`/`enqueue` — si `enqueue` fallara, la sesión revierte el `INSERT`/`usage_event` pero el objeto YA subido a S3 queda huérfano (sin fila que lo referencie). No es el patrón de `hooks.py` (no hay evidencia LEGAL que se pierda — nadie puede alegar "yo subí esto" si no hay fila) y es el patrón preexistente desde v1, no algo que este WP introdujo; arreglarlo de raíz (comitear la fila antes de subir a S3) es un cambio de arquitectura mayor, fuera de alcance de un fix puntual — reportado como nota, no corregido | N-A — usa `Repo` | **Hallazgo corregido, ver §3**; nota de S3 huérfano reportada, no corregida (ver arriba) |
| `contacts.py` | N-A | N-A — CRUD core sin flag | N-A — sin escrituras de evidencia | N-A — usa `Repo` | — |
| `finance.py` | N-A | N-A — CRUD core sin flag | N-A — sin escrituras de evidencia | N-A — usa `Repo` | — |
| `reminders.py` | N-A | N-A — CRUD core sin flag | N-A — sin escrituras de evidencia | N-A — usa `Repo` | Deuda YA documentada (v5, no de este WP): `channel="mobile"` válido en este router HTTP pero `edecan_toolkit.recordatorios._CANALES_VALIDOS` (fuera de mi alcance) todavía no lo incluye — sin cambios |
| `me.py` | N-A | N-A — solo lectura de identidad | N-A — sin escrituras | N-A — usa `Repo` | — |
| `persona.py` | N-A | N-A — CRUD core sin flag | N-A — sin escrituras de evidencia | N-A — usa `Repo` | — |
| `admin.py` | N-A | N-A — gate de rol (`require_superadmin`), no de plan | N-A — solo lectura | N-A — usa `Repo` | — |
| `usage.py` | N-A | N-A — solo lectura/dashboard | N-A — solo lectura | N-A — usa `Repo` | Comparte el mismo default `UNLIMITED` que `files.py`/`voice.py` tenían, pero es **puramente de DISPLAY** (no enforcement — la cuota real la aplican `files.py`/`voice.py`/`conversations.py`/`connectors.py`, cada uno con su propio chequeo): deliberadamente NO se tocó — cambiar solo `usage.py` desincronizaría el número mostrado del que de verdad se aplica en `conversations.py`/`connectors.py` (fuera de mi alcance), sería más engañoso, no menos. Reportado para WP-V7-12 junto con esos dos archivos |

---

## 5. Documentación sincronizada

- `docs/automatizaciones.md`, `docs/ide.md`, `docs/casa-inteligente.md`, `docs/skills.md`
  — leídos completos contra el código real de sus routers/paquetes correspondientes: **ya
  estaban precisos**, sin ninguna discrepancia encontrada. No se tocaron (editar un doc ya
  correcto solo para "tocarlo" sería ruido, no una mejora).
- `docs/configuracion.md` — **agregada** la sección faltante "El wizard de primer arranque
  — `/v1/setup/*`" (no existía ninguna mención a estas dos rutas en este documento, pese a
  que el enunciado pedía que reflejara "el wizard/setup real"). Las formas de respuesta
  documentadas se verificaron línea por línea contra `apps/api/edecan_api/routers/setup.py`
  y contra las aserciones reales de `apps/api/tests/test_setup_router.py` (no inventadas):
  `GET /status` → `{"local_mode", "llm_configured", "version"}`; `GET /detect` →
  `{"local_mode": bool, "claude_cli", "codex_cli", "ollama"}`.
- **Hallazgo fuera de mi alcance, reportado**: `docs/api.md` §`/v1/setup` (líneas ~642-665)
  documenta una forma de respuesta DISTINTA y desactualizada —
  `{"llm_connected", "voice_connected", "onboarding_complete"}` para `/status` (los campos
  reales son `local_mode`/`llm_configured`/`version`) y un campo `"mode": "local"|"hosted"`
  para `/detect` (el campo real es `local_mode: bool`, no `mode: str`). `docs/api.md` no
  está en mis rutas editables — la sección nueva de `docs/configuracion.md` documenta la
  forma CORRECTA mientras tanto; recomendado corregir `docs/api.md` en un WP de
  documentación de seguimiento (WP-V7-12 o similar).

---

## 6. Hallazgos fuera de alcance — reportados para WP-V7-12

1. **`docs/api.md` §`/v1/setup` desactualizado** — ver §5 arriba, detalle completo ahí.
2. **`companion.py` sin evidencia persistente de emparejamiento** — ver la fila de la
   tabla arriba. Gap ya documentado en `docs/control-remoto.md` (no un descubrimiento de
   este WP), requiere integrar con la tabla `devices` (`devices.py`, fuera de mi alcance).
3. **Patrón fail-open `UNLIMITED` en `conversations.py`/`connectors.py`** (mensajes/día,
   números de teléfono) — mismo patrón exacto que corregí en `files.py`/`voice.py` (§3),
   pero esos dos archivos son explícitamente de otros WPs (`conversations.py` está en la
   lista de "nunca editar" de este WP). Recomendado un fix puntual (cambiar el default del
   `.get()` de `UNLIMITED` a `0`) en un WP de seguimiento, junto con alinear el display de
   `usage.py` una vez que la fuente de verdad esté alineada.

---

## 7. Verificación final

```
$ uv run --all-packages pytest -q apps/api/tests/ -m "not integration"
... (ver salida completa más abajo)

$ DATABASE_URL="postgresql+asyncpg://edecan:edecan@localhost:55480/edecan" \
    uv run --all-packages pytest -q apps/api/tests/test_v7_sweep_routers_restantes.py
14 passed in 0.99s

$ uv run --all-packages pytest -q packages/automations/
50 passed in 0.84s

$ uv run --all-packages ruff check apps/api/edecan_api/routers/files.py \
    apps/api/edecan_api/routers/voice.py apps/api/tests/test_v7_sweep_routers_restantes.py \
    packages/automations/tests/test_v7_verificacion_limite_automatizaciones.py
All checks passed!

$ uv run --all-packages ruff format --check <mismos 4 archivos>
4 files already formatted
```

Postgres desechable (`edecan-v7-routers-pg`) bajado al terminar (`docker rm -f`) — sin
contenedores huérfanos, verificado con `docker ps -a`.

Archivos escritos/tocados por este WP: `apps/api/edecan_api/routers/files.py` (fix),
`apps/api/edecan_api/routers/voice.py` (fix), `apps/api/tests/
test_v7_sweep_routers_restantes.py` (nuevo), `packages/automations/tests/
test_v7_verificacion_limite_automatizaciones.py` (nuevo), `docs/configuracion.md`
(sección nueva), `docs/cumplimiento/barrido-v7-routers-restantes.md` (este documento).
Ningún otro archivo de la lista de 17 routers ni de `packages/automations/` necesitó
cambios de código — solo verificación (BARRIDO A/B/C/D limpios, con evidencia documentada
arriba).
