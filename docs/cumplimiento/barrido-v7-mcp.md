# Barrido v7 — MCP bring-your-own: aislamiento, SSRF, flag `tools.mcp` (fase v7)

Este documento registra el barrido dedicado del conector MCP (Model Context Protocol,
`ARCHITECTURE.md` §15.g/§15.h, `docs/mcp.md`) — `apps/api/edecan_api/routers/mcp.py` y
`packages/mcp/edecan_mcp/` (`client.py`, `transport.py`, `protocol.py`, `provider_config.py`,
`seguridad.py`, `tool_adapter.py`), construido en v6 (fase v6) y **nunca cubierto por ningún
barrido v6** (a diferencia de campaigns.py/twilio_router.py/hooks.py/reuniones.py, que sí
tuvieron su propia ronda dedicada — ver `docs/seguridad-modelo-amenazas.md`).

Referencias canónicas leídas completas antes de escribir una línea de este WP:
`docs/roadmap.md`, `docs/seguridad-modelo-amenazas.md` completo (en especial el gate
`EDECAN_LOCAL_MODE` de `claude_cli`/`codex_cli`/`ollama`/`polly`, el fix de `confirm_tool_call`
que revalida `requires_flags` incluso para "extra_tools MCP recalculadas", y el punto 7 SSRF
del fetcher Playwright), `ARCHITECTURE.md` §15.g/§15.h, y `docs/mcp.md`.

**Resumen ejecutivo**: los cuatro barridos con nombre (A: bring-your-own/aislamiento, B:
plan-flag, C: evidencia, D: esquema) confirmaron que `mcp.py`/`packages/mcp` ya estaban
construidos correctamente en los puntos centrales que este paquete pidió verificar —
aislamiento de credenciales por tenant, allowlist de entorno del subproceso `stdio`, SSRF
incondicional con revalidación en la ejecución real (defensa contra DNS rebinding), gate de
plan en los 4 endpoints HTTP y en el camino completo chat normal + `confirm`, orden
handshake-antes-de-persistir, y ausencia total de SQL crudo en las rutas de este WP. No hizo
falta ningún fix de seguridad. Sí se encontraron y cerraron **tres huecos de cobertura reales**
(ninguno un bug explotable, los tres "correcto pero no anclado con test" o "correcto pero
documentado de más"): el aislamiento de entorno del subprocess `stdio` nunca se había
verificado inspeccionando el env REAL recibido (solo se había leído el código); la protección
contra redirect en `HTTPTransport` nunca se había probado explícitamente; y el camino
`confirm_tool_call` con una tool `mcp_*` y el flag `tools.mcp` apagado no tenía ningún test
(a diferencia del caso genérico ya pinneado en `test_conversations.py`). Además se agregó
**un refuerzo nuevo, no bloqueante**: escaneo heurístico de nombre/descripción de tools
remotas contra *prompt injection*, que hasta ahora no existía para MCP pese a existir ya para
Skills (`packages/skills`).

---

## BARRIDO A — bring-your-own + aislamiento

### A.1 — Resolución de servidores por tenant, fail-closed, sin fallback de plataforma

Verificado leyendo `apps/api/edecan_api/deps.py::get_mcp_tools_for_tenant`/
`_build_mcp_tools_for_tenant` y `apps/worker/edecan_worker/deps.py::Deps.mcp_tools_para`/
`_build_mcp_tools`: ambos resuelven servidores MCP EXCLUSIVAMENTE desde
`connector_accounts` filtradas por `tenant_id` + `connector_key="mcp"`, descifran la config
completa (`{nombre, transporte, url, comando, headers}`) del `TokenVault` del propio tenant
(`edecan_mcp.provider_config.deserializar_config_mcp`), y NUNCA leen ningún campo de
`Settings`/`.env` de plataforma para la config de un servidor en sí — el único valor que sale
de `Settings` es `EDECAN_LOCAL_MODE` (un booleano de modo de despliegue, no una credencial).
Sin fila en `connector_accounts`, la lista de tools MCP es `[]` (nunca un fallback silencioso a
nada). Confirmado también en `apps/api/edecan_api/routers/mcp.py`: los 3 métodos que tocan el
vault (`list_servers`, `put_server`, `get_server_tools`) siempre pasan `current_user.tenant_id`
explícito. No se encontró ningún patrón `config.campo or getattr(self._settings, "X", None)`
(el patrón exacto del hallazgo crítico de v4 en `edecan_llm.router`) en ningún archivo de este
paquete. **Correcto — documentado, sin test nuevo dedicado** (ya lo ejercitan
`packages/mcp/tests/*`, `apps/api/tests/test_mcp_router.py`, `apps/worker/tests/
test_mcp_en_worker.py` con `FakeSession`/`FakeVault` que solo entienden queries filtradas por
`tenant_id`, ver el docstring de `_FakeMCPSession` en este último).

### A.2 — Transporte `stdio`: entorno del subproceso

**Ya estaba bien construido** — `edecan_mcp.transport.StdioTransport._ensure_started` arma un
`env` explícito con un **allowlist** de solo 2 claves (`_STDIO_ENV_ALLOWLIST = ("PATH",
"HOME")`) y lo pasa a `asyncio.create_subprocess_exec(..., env=env)`, en vez de heredar
`os.environ` completo del proceso backend (que en un despliegue hospedado real contendría
`ANTHROPIC_API_KEY`, `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`, `DATABASE_URL`,
`JWT_SECRET`, etc. — la misma clase de fuga que tenía `packages/voice/edecan_voice/polly.py`
en v5, pero por herencia de entorno en vez de cadena de credenciales AWS ambiente). Además,
`stdio` exige `EDECAN_LOCAL_MODE=True` (`seguridad.validar_comando_mcp`, fail-closed sin ese
flag) — mismo criterio que `claude_cli`/`codex_cli`/`ollama`/`polly` — verificado en 3 capas
independientes: el router (`PUT /v1/mcp/servers` rechaza con `400` ANTES de intentar nada),
`tool_adapter._tools_de_un_servidor` (omite con warning si `local_mode=False`, defensa en
profundidad), y `seguridad.validar_comando_mcp` en sí (la fuente de verdad, con su propio test).

**Hueco de cobertura cerrado**: no existía NINGÚN test que inspeccionara el env REAL recibido
por el subprocess — solo se verificaba por lectura de código. Se agregó
`packages/mcp/tests/test_transport.py::
test_stdio_transport_el_subproceso_solo_hereda_path_y_home`: un subprocess real (`sys.
executable -c "..."`) que reporta su propio `os.environ` de vuelta por el protocolo MCP, con 6
variables tipo-secreto de plataforma sembradas en el proceso de test (`ANTHROPIC_API_KEY`,
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `DATABASE_URL`, `JWT_SECRET`, una variable
genérica) — confirma que NINGUNA cruza.

**Hallazgo secundario, benigno, documentado (no una fuga)**: el test reveló que en macOS el
subprocess recibe, además de `PATH`/`HOME`, dos variables NO secretas que ni siquiera
`_STDIO_ENV_ALLOWLIST` puede evitar: `__CF_USER_TEXT_ENCODING` (Apple CoreFoundation, deriva
del UID del proceso) y `LC_CTYPE` (coerción de locale de CPython, PEP 538). Verificado
empíricamente que **persisten incluso pasando `env={}` totalmente vacío** a
`create_subprocess_exec` — es decir, no llegan copiando nada de `os.environ` del padre, las
sintetiza el propio runtime del sistema operativo/CPython en el proceso HIJO, fuera del
alcance de cualquier allowlist que reciba `create_subprocess_exec`. Confirmado con una prueba
directa (`ANTHROPIC_API_KEY` sembrada en el padre, `env={}` explícito → el hijo nunca la ve).
Documentado en el comentario de `_STDIO_ENV_ALLOWLIST` (`transport.py`) y en el test mismo; la
aserción del test tolera exactamente esas dos claves conocidas y sigue siendo estricta contra
cualquier otra.

### A.3 — Transporte `http`/SSE remoto: SSRF y redirects

**Ya estaba bien construido** — `edecan_mcp.seguridad.validar_url_mcp` bloquea IP privada/
loopback/link-local/reservada/multicast/no-especificada (literal o resuelta por DNS),
hostnames de metadata de nube (`metadata.google.internal`, `metadata.goog`) y `localhost`/
`*.localhost`, exige `https://` salvo `local_mode=True`, y falla CERRADO si el DNS está caído
o no resuelve — mismo criterio que `edecan_browser.policy` (duplicado a propósito, sin importar
ese paquete, ver el docstring de `seguridad.py`). Se revalida en DOS momentos: al conectar
(`PUT /v1/mcp/servers`) Y otra vez, con el mismo `local_mode`, justo antes de CADA ejecución
real de una tool (`_MCPRemoteTool.run`, ya existía en v6) — defensa contra DNS rebinding en la
ventana entre el discovery (con caché de hasta 60s) y la confirmación humana (hasta 15 min).
Cobertura ya exhaustiva en `packages/mcp/tests/test_seguridad.py` (11 casos) y
`test_tool_adapter.py` (revalidación en `run()`).

**Hueco de cobertura cerrado — redirects**: nunca se había probado explícitamente que un
servidor MCP no pudiera redirigir la conexión real a otro host por su cuenta (mismo vector que
`docs/seguridad-modelo-amenazas.md` punto 7, "el fetcher Playwright no revalida redirects", pero para el
transporte MCP). Verificado por lectura + prueba empírica: `HTTPTransport` construye su
`httpx.AsyncClient` sin `follow_redirects=True` (default de httpx 0.28: `False`), y
`httpx.Response.raise_for_status()` trata CUALQUIER `3xx` como error (no solo 4xx/5xx) — un
redirect nunca llega ni a intentarse parsear como JSON-RPC, se traduce directo a
`MCPTransportError` sin que el transporte toque el host de destino. Confirmado con
`respx` simulando un servidor que responde `307` apuntando a otro host: la ruta del host
redirigido nunca recibe ninguna request. Test nuevo:
`packages/mcp/tests/test_transport.py::test_http_transport_no_sigue_redirects_automaticamente`.
Documentado en `edecan_mcp.seguridad` (nueva sección "Redirects HTTP" en el docstring del
módulo) y en `docs/mcp.md`.

---

## BARRIDO B — plan-flag end-to-end

### B.1 — Los 4 endpoints HTTP exigen `FLAG_TOOLS_MCP`

Verificado por introspección REAL del grafo de dependencias de FastAPI (no solo lectura): los
4 endpoints de `mcp.py` (`GET`/`PUT /v1/mcp/servers`, `DELETE /v1/mcp/servers/{nombre}`,
`GET /v1/mcp/servers/{nombre}/tools`) tienen `_require_tools_mcp` en su cadena de
dependencias. Ya estaba correcto (`Depends(_require_tools_mcp)` en la firma de los 4
handlers) — se agregó el ancla ejecutable
`apps/api/tests/test_v7_sweep_mcp.py::test_los_4_endpoints_de_mcp_exigen_require_tools_mcp`,
que además congela la lista exacta de endpoints conocidos (un endpoint nuevo sin el gate, o
agregado sin actualizar la lista, rompe el test a propósito).

### B.2 — `requires_flags` de las tools MCP gatea Agent/specs Y `confirm_tool_call`

Verificado de punta a punta:

- **Descubrimiento/oferta al modelo**: `edecan_mcp.tool_adapter._MCPRemoteTool.requires_flags
  = frozenset({"tools.mcp"})` en cada tool construida — `packages/core/edecan_core/agent.py::
  _extra_tools_disponibles` filtra `extra_tools` por `requires_flags` con el MISMO criterio que
  `ToolRegistry._flags_satisfechos` antes de ofrecérselas al LLM.
- **Turno de chat normal**: `apps/api/edecan_api/routers/conversations.py::post_message` pasa
  `extra_tools=await _extra_mcp_tools_or_empty(...)` a `Agent.run_turn`, que a su vez re-exige
  el flag al EJECUTAR (no solo al ofrecer) vía `_con_flags_satisfechos` — el fix del "Hallazgo
  1" de `docs/seguridad-modelo-amenazas.md`/`test_v6_sweep_flags.py`, verificado intacto,
  aplica igual a las tools MCP (no hay ninguna ruta que las trate distinto).
- **Confirmación (`POST .../confirm`)**: `confirm_tool_call` resuelve la tool pendiente contra
  el `ToolRegistry` compartido primero (las tools MCP NUNCA están ahí) y, si no la encuentra,
  recalcula `extra_tools` vía `_extra_mcp_tools_or_empty` y busca por nombre — el fix CRITICAL
  ya documentado en `docs/seguridad-modelo-amenazas.md` ("`POST /v1/conversations/{id}/confirm` ejecutaba
  una tool `dangerous` sin revisar su flag de plan") cubre EXPLÍCITAMENTE este camino
  (`_tool_requires_flags_satisfechos(tool, tenant.flags)`, con `getattr` defensivo "por si...
  es una tool MCP bring-your-own", cita literal del propio fix).

**Hueco de cobertura cerrado**: `test_conversations_mcp.py` ya cubre flag on/off en el
descubrimiento (`extra_tools` capturadas) y el camino feliz + 409 de `confirm_tool_call` para
una tool `mcp_*` — pero NINGÚN test existente ejercitaba una tool `mcp_*` pendiente de
confirmar con el flag `tools.mcp` apagado. Se agregaron 3 tests en
`apps/api/tests/test_v7_sweep_mcp.py` (siguiendo la instrucción explícita del paquete de
trabajo de extender ahí, no en `test_conversations_mcp.py`):

1. `test_confirm_mcp_con_flag_apagado_hoy_da_409_via_extra_tools_vacias` — el comportamiento
   REAL de hoy: como `get_mcp_tools_for_tenant` corta en seco ANTES de construir tools cuando
   el flag está apagado, `extra_tools` recalculadas en `confirm_tool_call` son `[]` → la tool
   nunca se encuentra → `409` (no `403`). Fija cuál de los dos códigos ocurre hoy.
2. `test_confirm_mcp_con_flag_apagado_defensa_en_profundidad_403` — escenario adversarial:
   simula que `get_mcp_tools_for_tenant` devolviera la tool PESE al flag apagado (como si su
   propio corte temprano tuviera un bug futuro), y confirma que `_tool_requires_flags_
   satisfechos` la bloquea igual de forma independiente (`403`, la tool nunca llega a
   `run()`). Es el equivalente, para el camino `extra_tools`, del test que motivó el fix
   original (probado ahí con una tool genérica del registry compartido, `preparar_pago`).
3. `test_confirm_mcp_con_flag_prendido_si_ejecuta` — contraparte positiva: con el flag
   encendido de verdad (`hosted_pro`), la misma tool sí se ejecuta, confirmando que los dos
   tests anteriores bloquean por el flag específicamente.

Se agregó también `test_flag_hardcodeado_en_tool_adapter_coincide_con_el_flag_pinned_del_plan`/
`test_flag_hardcodeado_en_router_mcp_coincide_con_el_flag_pinned_del_plan`: `edecan_mcp.
tool_adapter.REQUIRES_FLAG_MCP` y `edecan_api.routers.mcp.FLAG_TOOLS_MCP` son literales `"tools.
mcp"` independientes (no una única fuente de verdad — `edecan_mcp` no depende de
`edecan_schemas.plans` a propósito, ver `packages/mcp/pyproject.toml`) que hoy coinciden por
convención; el test los ancla para que un drift futuro entre los dos se note de inmediato.

### B.3 — Límite de servidores MCP por tenant

**No existe ningún límite documentado** — ni `ARCHITECTURE.md` §15.g ni
`edecan_schemas.plans.PLANES` definen un `limits.mcp_servers` (a diferencia de
`limits.phone_numbers`/`limits.seats`, que sí son límites de plan pinned). Instrucción
explícita del paquete de trabajo: "si hay límite documentado, que se aplique en TODAS las
superficies" — como no existe ninguno, **no se inventó uno** (consistente con "no inventar
cambios"). Documentado como observación operativa, no como hallazgo de seguridad: un tenant
que conecte muchos servidores paga un costo de latencia real en cada recálculo de
`extra_tools` sin caché vigente, porque `construir_tools_mcp` hace un handshake + `tools/list`
POR SERVIDOR **en serie** (`apps/api/edecan_api/deps.py::_build_mcp_tools_for_tenant`) — con
decenas de servidores lentos/caídos, un turno de chat cuya caché de 60s expiró podría notar
esa latencia acumulada. Anotado en `docs/mcp.md` ("Limitaciones de esta primera versión") y
como candidato de una ola futura (no de este WP: es una mejora de rendimiento potencial, no un
bug ni un hallazgo de seguridad — no bloquea nada, `get_mcp_tools_for_tenant` es fail-open y
tiene timeout de 15s por handshake vía `_VALIDATE_TIMEOUT_SECONDS` en el router, aunque el
descubrimiento en `deps.py` en sí no impone timeout explícito por servidor más allá del que ya
trae cada `httpx.AsyncClient`/`StdioTransport` — otra observación menor para la misma ola
futura).

---

## BARRIDO C — evidencia de auditoría

`mcp.py` escribe evidencia en 2 sitios: `repo.create_connector_account` + `vault.put` (`PUT`) y
`repo.add_audit_log` (`PUT` y `DELETE`, acción `"mcp.server.connected"`/`"mcp.server.
disconnected"`). Verificado contra la regla de `docs/seguridad-modelo-amenazas.md` puntos 8/9 (commit de
evidencia ANTES de cualquier `raise` alcanzable en el mismo camino) y el criterio de
"handshake antes de persistir" que ya aplica `credentials.py`:

- **Handshake antes de persistir**: `put_server` llama `_handshake_real` (si
  `validate=true`, el default) ANTES de tocar el repo/vault — si el handshake falla, la
  función sale con `HTTPException(400)` sin haber escrito nada. Ya estaba correcto, pero
  **ningún test existente lo verificaba explícitamente** (`test_validate_falla_400_con_
  detalle`/`test_validate_timeout_400` solo comprobaban el código `400`, no la ausencia de
  escritura). Test nuevo: `apps/api/tests/test_mcp_router.py::
  test_validate_falla_nunca_persiste_nada` — confirma que ni la `connector_account` ni la fila
  del vault llegan a existir cuando el handshake falla.
- **`audit_log` es siempre la ÚLTIMA operación**: en `put_server` y `delete_server`, `repo.
  add_audit_log(...)` es literalmente la última línea de la función (sin ningún `raise`
  después en el mismo camino) — la regla de commit-explícito-antes-de-raise de los puntos 8/9
  NO aplica aquí (ese guardrail es para cuando SÍ hay una excepción alcanzable DESPUÉS de
  escribir evidencia en la misma transacción; acá no la hay). Confirmado además que toda la
  request corre en una única transacción que solo comitea al salir limpio del handler
  (`edecan_db.session.get_session`, "commit al salir normalmente del bloque `async with`") —
  si `vault.put`/`add_audit_log` fallaran DESPUÉS de `create_connector_account`, la transacción
  completa (incluida esa fila) se revierte junto, sin dejar ningún estado a medias. Ya
  documentado con precisión en el docstring del propio router — no hizo falta corregir nada,
  solo verificar que la afirmación es literalmente cierta (lo es).
- **`GET /v1/mcp/servers/{nombre}/tools`**: de solo lectura, no escribe evidencia.

---

## BARRIDO D — esquema SQL

`apps/api/edecan_api/routers/mcp.py` y TODO `packages/mcp/edecan_mcp/` **no ejecutan SQL
crudo en ningún punto** — el router usa exclusivamente `Repo.list_connector_accounts`/
`create_connector_account`/`delete_connector_account` (interfaz ya pinned, implementada en
`apps/api/edecan_api/repo.py::SqlRepo`, fuera del alcance de este WP) y `TokenVault.get`/`put`;
`packages/mcp` no importa `edecan_db` en absoluto (no tiene ninguna noción de sesión/SQL, ver
`README.md` del paquete: "No decide flags de plan ni persiste nada"). **Nada que verificar
contra una migración — documentado y listo**, consistente con la instrucción del paquete de
trabajo ("Si solo usan TokenVault/ORM, documéntalo y listo").

**Nota fuera de mi alcance de escritura** (solo lectura, reportado aquí): `apps/worker/
edecan_worker/deps.py::Deps._build_mcp_tools` SÍ ejecuta SQL crudo parametrizado contra
`connector_accounts` (`SELECT id, external_account_id FROM connector_accounts WHERE tenant_id
= :tenant_id AND connector_key = :connector_key ORDER BY created_at ASC`) — verificado columna
por columna contra `packages/db/edecan_db/models.py::ConnectorAccount`
(`id`/`tenant_id`/`connector_key`/`external_account_id`/`created_at` existen todas, mixins
`IDMixin`/`TenantScopedMixin`/`TimestampMixin`) y contra `apps/api/edecan_api/repo.py::
SqlRepo.create_connector_account` (mismas columnas, sin discrepancia). **Sin bug** — se
documenta acá porque `apps/worker/edecan_worker/deps.py` no está entre las rutas que este WP
puede escribir, no porque haya algo que corregir.

---

## SEGURIDAD ADICIONAL — colisión de nombres y prompt injection en tools remotas

### Colisión de nombres con tools nativas

**Ya estaba resuelto por construcción, no solo por convención**: toda tool MCP se nombra
`mcp_{slug_servidor}_{slug_tool}` — el prefijo `mcp_` lo antepone SIEMPRE
`edecan_mcp.tool_adapter._nombre_tool`, sin que el servidor remoto pueda influir en eso (solo
controla lo que va DESPUÉS del prefijo). Como ninguna tool nativa del repo empieza con `mcp_`
(namespace reservado de facto), la colisión es estructuralmente imposible sin importar qué
nombre elija un servidor de terceros para su tool. Ya estaba anclado con un test real:
`apps/api/tests/test_mcp_router.py::
test_prefijo_mcp_nunca_colisiona_con_nombres_de_tools_reales` construye un `ToolRegistry` real
con `load_entry_points` (~40+ tools reales cargadas) y confirma que ninguna empieza con
`"mcp_"`. **Correcto — ya anclado, sin cambios.**

### Prompt injection vía nombre/descripción de tool remota

**Mitigación primaria ya existente, verificada de nuevo**: toda tool MCP es `dangerous=True`
SIN excepción (`_MCPRemoteTool.dangerous = True`, `edecan_mcp.tool_adapter`) — cualquier ACCIÓN
real que el modelo intente tras leer una descripción manipulada por un servidor hostil sigue
exigiendo confirmación humana explícita antes de ejecutarse. A diferencia de una Skill
(`packages/skills`, que inserta un `SKILL.md` completo, potencialmente largo, en el contexto
del modelo SIN ningún gate de ejecución — de ahí que amerite escanear ANTES de instalar), el
texto de una tool MCP expuesto al modelo es corto (nombre + una o dos frases de descripción) y
la ejecución real nunca es alcanzable sin el gate de confirmación.

**Hueco real, cerrado con un refuerzo no bloqueante**: a diferencia de Skills
(`packages/skills/edecan_skills/security.py::escanear_inyeccion`, con trust tiers +
escaneo heurístico portado de OpenJarvis), MCP no tenía NINGÚN escaneo del `name`/
`description` que reporta el servidor remoto — texto que se inserta tal cual en el `ToolSpec`
enviado al LLM (superficie clásica de "tool poisoning"). Se agregó
`edecan_mcp.seguridad.escanear_descripcion_tool_mcp` — subconjunto reducido, A PROPÓSITO, de
los mismos 3 patrones de `edecan_skills.security` que sí aplican bien a texto corto (anulación
imperativa tipo "ignore previous instructions"/"olvida tus instrucciones", suplantación de
sistema tipo "you are now"/"system prompt"/"jailbreak", caracteres de ancho cero) — se omiten
los 2 pensados para un documento largo (`base64_sospechoso`, `exfiltracion` con plantilla de
URL), que no tienen sentido en una descripción de una frase. Duplicado a propósito en vez de
importar `edecan_skills` directo (mismo criterio de "cada paquete trae su propia copia mínima"
que ya aplica este módulo para `edecan_browser`, `ARCHITECTURE.md` §10.1).

**Diseño deliberadamente NO bloqueante**: `edecan_mcp.tool_adapter._tools_de_un_servidor`
corre el escaneo sobre cada tool descubierta y, si encuentra algo, deja un `logger.warning`
con servidor/tool/patrón — la tool sigue disponible siempre (nunca se oculta ni se rechaza por
un hallazgo heurístico, que puede ser falso positivo; la garantía real sigue siendo
`dangerous=True`). Mismo espíritu que Skills ("Edecán nunca bloquea una instalación por esto,
solo la marca"), adaptado a que acá no hay ningún flujo de instalación/`acknowledge` que
construir (fuera de alcance: tocar `apps/web` no está entre las rutas de este WP). Tests
nuevos: `packages/mcp/tests/test_seguridad.py` (7 casos: detección de cada patrón, recorte de
fragmento a 80 caracteres, texto limpio sin hallazgos, contrato "nunca lanza") y
`packages/mcp/tests/test_tool_adapter.py` (2 casos: la tool sospechosa sigue disponible +
queda el `logger.warning`; una tool limpia no genera ningún warning de escaneo).

---

## Verificación final

```
uv run --all-packages pytest packages/mcp -q
  → 94 passed

uv run --all-packages pytest apps/api/tests/test_mcp_router.py apps/api/tests/test_conversations_mcp.py \
    apps/api/tests/test_v7_sweep_mcp.py apps/api/tests/test_conversations.py apps/api/tests/test_v6_sweep_flags.py -q
  → 100 passed

uv run --all-packages pytest apps/api -q
  → 1105 passed, 22 skipped (1 fallo AJENO, ver nota abajo)

uv run --all-packages ruff check packages/mcp apps/api/tests/test_mcp_router.py \
    apps/api/tests/test_conversations_mcp.py apps/api/tests/test_v7_sweep_mcp.py
  → All checks passed
```

**Nota sobre el único fallo de la suite completa de `apps/api`**: al correr la suite completa
(no un subconjunto) apareció 1 fallo en `apps/api/tests/test_v7_sweep_routers_restantes.py::
test_skills_list_no_exige_ningun_flag_de_plan` (`AttributeError: 'NoneType' object has no
attribute 'execute'` en `packages/skills/edecan_skills/store.py::list_skills`) — **archivo y
paquete completamente fuera del alcance de este WP** (no MCP, no una ruta que este paquete
pueda tocar). Verificado que **no lo causó ningún cambio de este WP**: el test pasa limpio en
aislamiento (`pytest apps/api/tests/test_v7_sweep_routers_restantes.py::
test_skills_list_no_exige_ningun_flag_de_plan` → `1 passed`), consistente con una
dependencia de orden/estado compartido entre archivos de test (u otro paquete de trabajo v7
tocando ese mismo archivo en paralelo durante esta sesión — el propio entorno de ejecución de
este WP confirmó otros procesos `pytest`/agentes activos concurrentemente sobre el mismo
checkout). Se reporta acá para quien tenga `apps/api/edecan_api/routers/skills.py`/
`packages/skills/` en su alcance (candidato natural: el mismo WP que ya tiene `packages/
skills` en sus rutas, o fase v7 en su verificación cruzada) — no se investigó más a fondo ni
se tocó ningún archivo de esa área, por estar fuera de las rutas permitidas de este paquete de
trabajo.

## Deliverables de este WP

- `apps/api/edecan_api/routers/mcp.py` — sin cambios de comportamiento (ya estaba correcto en
  los 4 barridos); confirmado por lectura + tests nuevos, no reescrito.
- `packages/mcp/edecan_mcp/seguridad.py` — nuevo `escanear_descripcion_tool_mcp`/
  `HallazgoDescripcionTool` (heurístico de prompt-injection, no bloqueante) + documentación
  nueva de redirects/SSRF/allowlist de entorno en el docstring del módulo.
  `packages/mcp/edecan_mcp/tool_adapter.py` — cableado del escaneo (solo `logger.warning`, cero
  cambio de comportamiento observable salvo el log). `packages/mcp/edecan_mcp/transport.py` —
  solo documentación (nota sobre `__CF_USER_TEXT_ENCODING`/`LC_CTYPE`), sin cambio de código.
  `packages/mcp/edecan_mcp/__init__.py` — exporta los 2 símbolos nuevos.
- Tests nuevos: `packages/mcp/tests/test_transport.py` (+2: env real del subprocess, no-sigue-
  redirects), `packages/mcp/tests/test_seguridad.py` (+7: escaneo heurístico),
  `packages/mcp/tests/test_tool_adapter.py` (+2: escaneo no bloqueante),
  `apps/api/tests/test_mcp_router.py` (+1: handshake-antes-de-persistir, + corrección de un
  comentario desactualizado sobre `edecan-mcp` como dependencia declarada),
  `apps/api/tests/test_v7_sweep_mcp.py` (nuevo archivo, 6 tests: 2 de consistencia
  cross-package, 1 estructural de flag en los 4 endpoints, 3 del camino `confirm` con flag
  apagado/adversarial/prendido). `apps/api/tests/test_conversations_mcp.py`: **sin cambios**
  (el caso de flag apagado se agregó en `test_v7_sweep_mcp.py` por instrucción explícita del
  paquete de trabajo).
- `docs/mcp.md` — sincronizado: redirects nunca seguidos, colisión de nombres estructuralmente
  imposible, escaneo heurístico de descripciones, sin límite de servidores por tenant
  (+ costo de latencia si se conectan muchos), referencia técnica actualizada.
- Este informe.
