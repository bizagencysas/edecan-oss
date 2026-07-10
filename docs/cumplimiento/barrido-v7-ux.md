# Auditoría UX/navegación end-to-end de `apps/web` (v7, WP-V7-09)

Alcance de este paquete: **solo `apps/web/**` + este informe**. Nunca se tocó
backend — donde un valor de backend parecía discutible, la UI se alinea al
backend y el punto queda anotado abajo como nota, no como fix. Contexto
leído antes de escribir código (`DIRECCION_ACTUAL.md` "Principio de UX no
negociable: configuración de pocos clicks"; `HOTFIXES_PENDIENTES.md`,
en particular el precedente `ReunionStatus` — v6 encontró que
`apps/web/src/lib/api-reuniones.ts` tenía `"queued"` donde el backend real
usaba `"pending"`, invisible hasta que alguien comparó el enum literal
contra la migración real — y el patrón `tryRefresh`/`TOTP_REQUIRED_DETAIL`/
`tryRefreshWithTotpPrompt` duplicado a propósito en cada `api-*.ts`).

---

## 1. Hallazgos confirmados por el arquitecto — resueltos

### 1.1 Ads no tenía UI — construida

`/v1/ads` (borradores + confirmación, `apps/api/edecan_api/routers/ads.py`,
`ARCHITECTURE.md` §13, WP-V4-07) estaba montado desde v4 sin ninguna página
que lo consumiera: no existía `/app/ads`, ni entrada en `nav-items.ts`, ni
`components/ads/`. Construido:

- **`apps/web/src/lib/api-ads.ts`** — cliente HTTP nuevo, replica **exacto**
  el patrón `tryRefresh(totpCode?)` / `TOTP_REQUIRED_DETAIL` /
  `tryRefreshWithTotpPrompt()` / `refreshInFlight` / `totpPromptInFlight`
  documentado en `HOTFIXES_PENDIENTES.md` punto 2 y ya duplicado en
  `lib/api-misiones.ts` (plantilla explícita pedida por el enunciado) y
  `lib/api-reuniones.ts`/`lib/api-mcp.ts`. Tipos:
  - `AdDraftStatus = "draft" | "confirmed" | "pushed" | "error" | "cancelled"`
    — verificado literal por literal contra el `CheckConstraint` real de la
    migración `packages/db/alembic/versions/0006_v4_expansion.py:193-195`
    (`"status IN ('draft', 'confirmed', 'pushed', 'error', 'cancelled')"`),
    no contra un README ni un docstring — mismo criterio que exige la regla
    dura de esquema SQL de este proyecto.
  - `ESTADOS_CANCELABLES = ["draft", "confirmed", "error"]` — copiado literal
    de `_ESTADOS_CANCELABLES` en
    `apps/api/edecan_api/routers/ads.py:117`.
  - `ESTADO_CONFIRMABLE = "draft"` — copiado del `if draft["status"] !=
    "draft": raise 409` en `ads.py::confirmar_borrador`.
  - `AdsStatus`/`AdCampana`/`AdsMetricas`/`AdDraft` — espejan
    `AdsStatusOut`/`AdCampana`(`dict[str, Any]` sin `response_model` pinned,
    tipado deliberadamente abierto con `[key: string]: unknown` en vez de
    fingir precisión que el backend no garantiza)/fila real de `ad_drafts`.
  - `FLAG_TOOLS_ADS = "tools.ads"` — copiado de
    `packages/schemas/edecan_schemas/plans.py:40`.
- **`apps/web/src/components/ads/ConectarMetaAds.tsx`** — "pegar y validar"
  (access token + id de cuenta), mismo patrón que
  `components/viajes/ConectarAmadeus.tsx`. Vive EN la página de Ads, no en
  Configuración — mismo criterio que Amadeus/AfterShip en `/app/viajes`: es
  una credencial propia de esta vertical, no una de las tarjetas centrales
  de `/app/configuracion`.
- **`apps/web/src/components/ads/ResumenCampanas.tsx`** — campañas + métricas
  del período (`GET /v1/ads/resumen`), selector de período, banner "datos de
  ejemplo" cuando `!status.configured` (el endpoint cae a `StubAdsProvider`
  sin bloquear nunca), formatea `spend`/`cpc` con la moneda real de la cuenta
  cuando se conoce.
- **`apps/web/src/components/ads/BorradoresAds.tsx`** — lista de
  `ad_drafts` con badge de estado + modal de confirmación explícita
  (**segundo gate humano**, calca el patrón de `/app/ordenes`) antes de
  `POST /borradores/{id}/confirmar` — el modal aclara que la campaña se crea
  **siempre en pausa** y que activarla es decisión del usuario en el Ads
  Manager de Meta (`DIRECCION_ACTUAL.md`: "dinero real nunca se mueve
  solo"). Cancelar respeta `ESTADOS_CANCELABLES` (nunca ofrece cancelar un
  borrador `pushed`).
- **`apps/web/src/app/(app)/app/ads/page.tsx`** — orquesta las tres piezas.
  Pocos clicks: sin conectar Meta, la página es 100% usable (resumen con
  datos de ejemplo, borradores listables/confirmables/cancelables).
- **`nav-items.ts`**: entrada `{ href: "/app/ads", label: "Ads", icon:
  SendIcon }`, junto a Órdenes. Ícono **reusado, no creado** (`SendIcon` ya
  existía en `components/icons.tsx`, usado hoy en el composer de chat y en
  Configuración/Mensajería) — el enunciado pedía explícitamente "icono
  existente"; no se tocó `icons.tsx`.

Verificado: `npx tsc --noEmit` limpio, `next lint` limpio sobre los 5
archivos nuevos + `nav-items.ts`, `next build` genera `/app/ads` como página
estática sin errores (ver §4).

### 1.2 Casa inteligente — decisión: la tarjeta de Configuración basta, no se construye página nueva

Leído `docs/casa-inteligente.md` completo antes de decidir. Razones:

1. **La vertical es chat-first por diseño, no por omisión.** `edecan_smarthome`
   expone exactamente 3 tools (`casa_dispositivos`, `casa_estado`,
   `casa_controlar`, ver `docs/casa-inteligente.md` "Qué puede hacer el
   agente") pensadas para usarse **desde la conversación** — no hay un
   modelo de datos propio que justifique una pantalla (a diferencia de
   Reuniones/Podcasts/Ads, que sí tienen tablas con historial que revisar:
   `meetings`, `podcasts`, `ad_drafts`). Una página dedicada terminaría
   reconstruyendo el propio dashboard de Home Assistant (listado de
   entidades, control), que ya existe y es responsabilidad de Home
   Assistant, no de Edecán.
2. **La tarjeta de conexión ya es "pegar y validar" completa**:
   `SelectorCasaInteligente.tsx` (base_url + token) +
   `CardCredencial`/`FilaCredencialConectada` en
   `app/(app)/app/configuracion/page.tsx` cubren conectar (`PUT
   /v1/smarthome/credentials`, con `validate: true` por defecto — probado
   contra `smarthome.py:110-113` `SmarthomeStatusOut{configured, base_url,
   reachable}`), ver estado (`GET /status`, con `reachable` en `null` si la
   red falla, nunca error), y desconectar (`DELETE
   /v1/smarthome/credentials`, botón "Quitar" ya wireado vía
   `handleQuitarCasa`) — ciclo completo, sin fricción extra.
3. Precedente explícito en `DIRECCION_ACTUAL.md` ("Vehículos... eliminado
   del alcance") de que no toda vertical necesita una superficie de UI
   propia — acá el criterio es más fuerte todavía porque la vertical SÍ
   está completamente servida (a diferencia de vehículos, que se cerró por
   prioridad de producto).

**Ninguna acción de código** en esta sección — se verificó que la tarjeta ya
cumple "pegar y validar completa" y se documenta la decisión de no construir
una página nueva, tal como pedía el enunciado.

### 1.3 Comentarios stale en `nav-items.ts` — limpiados

Cada entrada llevaba una nota `"...enlace puede dar 404 hasta entonces
(esperado)"` documentando aterrizajes parciales en paralelo durante v2-v6.
**Verificado que las 27 rutas no-root de `NAV_ITEMS` tienen hoy una carpeta
real bajo `apps/web/src/app/(app)/app/`** (listado completo cotejado 1 a 1:
`ads, ajustes, analista, archivos, automatizaciones, conectores, configuracion,
contactos, facturacion, finanzas, ide, inventario, memoria, mensajes,
misiones, negocios, ordenes, panel, perfil-vivo, persona, recordatorios,
remoto, reuniones, rrhh, skills, viajes, voz`) — **ninguna referencia
"puede dar 404" seguía siendo cierta**. Se quitaron todas (v2, v3, v4×2, v5×2,
v6×2 — 8 notas en total) y se dejó solo la atribución de versión/dueño (útil
como historial de qué WP sumó cada entrada), más una nota nueva en el
docstring del archivo aclarando que ya no aplican. Se sumó la nota de v7
para la entrada de Ads nueva.

### 1.4 MCP — flujo agregar/validar/quitar verificado completo

`CardServidoresMcp.tsx` (dentro de `/app/configuracion`, nunca en la nav
principal — **por diseño**, `ARCHITECTURE.md` §15.i: "MCP NO va en la
navegación principal: vive dentro de `/app/configuracion`... no como una
sección propia", así que su ausencia de `nav-items.ts` NO es un hallazgo)
cubre el ciclo completo contra `lib/api-mcp.ts`:

| Acción | Función | Endpoint |
|---|---|---|
| Agregar + validar | `putMcpServer` (`FormularioAlta`) | `PUT /v1/mcp/servers` (`validate: true` default — handshake MCP real antes de guardar) |
| Listar | `getMcpServers` | `GET /v1/mcp/servers` (tolerante a 404 mientras el router no esté montado) |
| Introspeccionar | `getMcpServerTools` (`FilaServidor` → "Ver herramientas") | `GET /v1/mcp/servers/{nombre}/tools` |
| Quitar | `deleteMcpServer` (`handleEliminar`, con `window.confirm`) | `DELETE /v1/mcp/servers/{nombre}` |

`transporte="stdio"` se deshabilita en el `<Select>` cuando `localMode` es
`false` (con tooltip explicando por qué), en vez de dejar que el usuario
llene el formulario para toparse con un 400 — coherente con "pocos clicks".
`lib/api-mcp.ts` ya sigue el mismo patrón `tryRefresh`/TOTP que el resto.
**Ninguna acción de código** — flujo verificado completo, sin hallazgos.

---

## 2. Matriz de auditoría por vertical

Una fila por vertical pedida en el enunciado. `(a)` página real + nav
wireada · `(b)` pocos clicks (sin configuración bloqueante) · `(c)` badges/
estados de la UI vs. valores REALES del backend (archivo:línea citado a
ambos lados) · `(d)` polling/refresh funciona con esos valores.

| Vertical | (a) Página + nav | (b) Pocos clicks | (c) Enums UI vs. backend (fuente citada) | (d) Polling/refresh |
|---|---|---|---|---|
| **RRHH** | `app/(app)/app/rrhh/page.tsx` (tabs Empleados/Ausencias/Nómina) · nav ya wireada | Sí — sin credenciales externas, datos internos del tenant; tabs sin bloqueo | `EmpleadoStatus`(`api-rrhh.ts:22`)=`active│inactive` vs. `Literal["active","inactive"]` en `rrhh.py:151,163` (**nota**: la columna `employees.status` en sí es `sa.Text()` SIN `CheckConstraint` — `0007_v5_expansion.py:189` — vocabulario abierto a nivel DB a propósito, `ARCHITECTURE.md` §14.b; el único escritor real es este router, así que el tipo TS sigue siendo preciso en la práctica). `AusenciaStatus`(`api-rrhh.ts:24`)=`pending│approved│rejected│cancelled` vs. CHECK real `0007_v5_expansion.py:213-215` y filtro `rrhh.py:274-276` — **coincide exacto**. `NominaStatus`(`api-rrhh.ts:25`)=`draft│approved│paid│cancelled` vs. CHECK real `0007_v5_expansion.py:231` y filtro `rrhh.py:344-346` — **coincide exacto** | No aplica (sin estados transitorios que requieran poll; las tablas se recargan on-demand tras cada acción) |
| **Viajes** | `app/(app)/app/viajes/page.tsx` · nav ya wireada | Sí — `ConectarAmadeus`/`ConectarAfterShip` opcionales, búsqueda funciona con datos de ejemplo sin credenciales | `ViajesEnvironment`(`api-viajes.ts:21`)=`test│production` vs. `_ENVIRONMENTS_VALIDOS` validado en `viajes.py:316-328` — **coincide exacto** | No aplica (sin estados transitorios propios; borradores de reserva viven en `orders`, ya cubiertos por `/app/ordenes`) |
| **Voz avanzada** (clones) | `app/(app)/app/voz/page.tsx` (`VocesTab`) · nav ya wireada | Sí — attestation de consentimiento es el único paso obligatorio antes de clonar, sin bloquear el resto de la página | `VozClonStatus`(`api-voz.ts:48`)=`attested│revoked` vs. CHECK real `0007_v5_expansion.py:280` (`voice_consents`) — **coincide exacto** | No aplica (clones no tienen estado transitorio: attested→revoked es una acción directa del usuario, no un job de fondo) |
| **Podcasts** | `app/(app)/app/voz/page.tsx` (`PodcastsTab`) · nav ya wireada (comparte ruta con Voz avanzada) | Sí — comentario explícito en `PodcastsTab.tsx:103` confirma que sin TTS conectado igual se puede crear un podcast (cae a un proveedor por defecto) | `PodcastStatus`(`api-voz.ts:76`)=`pending│running│done│error` vs. CHECK real `0008_v6_expansion.py:196` (`podcasts`) — **coincide exacto** | Sí — mismo patrón que Reuniones (ver abajo); `PodcastsTab` hace poll mientras haya alguno en `pending`/`running` |
| **Reuniones** | `app/(app)/app/reuniones/page.tsx` · nav ya wireada | Sí — el formulario solo pide elegir un archivo ya subido; sin eso, `EmptyState` con CTA implícito a `/app/archivos` | `ReunionStatus`(`api-reuniones.ts:24`)=`pending│running│done│error` vs. CHECK real `0008_v6_expansion.py:176` (`meetings`) — **coincide exacto** (este es el fix de v6 ya aplicado — re-verificado aquí, sin regresión: sigue en `"pending"`, no volvió a `"queued"`) | Sí — `page.tsx:63-78`: `setInterval` cada `POLL_INTERVAL_MS=5000` mientras `hayTrabajoPendiente` (`status==="pending"││"running"`), se limpia solo al llegar todas a estado terminal |
| **MCP** | Sin página propia — vive en `CardServidoresMcp` dentro de `/app/configuracion`, **por diseño** (`ARCHITECTURE.md` §15.i) | Sí — ver §1.4 | `MCPServerOut.estado`(`api-mcp.ts:35`)=`string` (abierto) vs. `estado: str`(`mcp.py:157`, poblado con `cuenta.get("status","active")` en `mcp.py:211`) — ambos lados deliberadamente sin vocabulario cerrado, **coincide** (ninguno promete más precisión de la que el otro garantiza) | No aplica (sin job de fondo; conectar es síncrono con handshake en el propio `PUT`) |
| **Analista** | `app/(app)/app/analista/page.tsx` · nav ya wireada | Sí — opera sobre archivos ya subidos, sin credenciales; `EmptyState` cuando no hay archivo elegido, nunca bloquea | Sin flag de plan, sin `dangerous`, **sin ningún campo de estado/enum** en `analista.py` (funciones puras `resumen`/`forecast`/`grafico`/`anomalias`, confirmado por grep — cero `Literal[`/`status` de dominio) — nada que comparar, consistente con `ARCHITECTURE.md` §14.e ("son un punto de partida útil, no un reemplazo de una revisión humana") | No aplica (sin estados transitorios) |
| **Ads** | `app/(app)/app/ads/page.tsx` · nav ya wireada — **construida en este WP, ver §1.1** | Sí — ver §1.1 | `AdDraftStatus`(`api-ads.ts`, nuevo)=`draft│confirmed│pushed│error│cancelled` vs. CHECK real `0006_v4_expansion.py:193-195` — **coincide exacto** (construido ya verificado, no una regresión post-hoc) | No aplica (confirmar es síncrono en una sola request HTTP, sin job de fondo que poll-ear; ver nota en `BorradoresAds.tsx` sobre por qué `"confirmed"` en reposo es un caso borde, no un estado "en progreso") |
| **Casa inteligente** | Sin página propia — tarjeta en `/app/configuracion`, **decisión documentada en §1.2** | Sí — ver §1.2 | Sin tabla propia con `status` (`connector_accounts` genérico); `SmarthomeStatus`(`api-configuracion.ts:210`)=`{configured, base_url, reachable}` vs. `SmarthomeStatusOut`(`smarthome.py:110-113`) — **coincide exacto**, incluido `reachable: bool│null` en ambos lados | No aplica (sin job de fondo; `reachable` se sondea live en cada `GET /status`, sin caché) |

### Verificación adicional pedida por el enunciado

- **Wizard `/app/bienvenida`**: releído completo. Sigue siendo exactamente 3
  pasos (`paso: 1 | 2 | 3` en `page.tsx:45`) — Paso 1 LLM
  (`SelectorLLM`, con "seguir sin conectar"), Paso 2 Voz (opcional,
  "Saltar"), Paso 3 confirmación + botón "Empezar a chatear" — coincide al
  pie de la letra con `DIRECCION_ACTUAL.md` ("Wizard de primer arranque:
  pantalla corta de bienvenida con 2-3 pasos como máximo"). Sin hallazgos.
- **Mensajes** (v4): `CanalEstado`(`api-mensajes.ts:28-33`)=`{canal,
  conectado, puede_leer}` vs. `CanalEstadoOut`(`mensajes.py:126-129`) —
  **coincide exacto**, incluido el comentario sobre por qué `puede_leer` es
  `false` solo para WhatsApp. `CanalMensajeria` (`CANALES_MENSAJERIA =
  ["telegram","discord","slack","whatsapp"]`, `api-mensajes.ts:25`) vs. el
  vocabulario documentado en `mensajes.py:332` (`Query(...,
  description="telegram | discord | slack | whatsapp")`) — **coincide**.
- **Inventario** (v4, ERP): `MovimientoMotivo`(`api-inventario.ts:21`)=
  `compra│venta│ajuste│merma│devolucion` vs. `Literal[...]` real en
  `erp.py:150` — **coincide exacto**. `Producto.activo: boolean` vs.
  `activo: bool | None` (`erp.py:145,169`) — **coincide**; el botón
  "Desactivar" en `ProductosTable.tsx` deliberadamente no usa
  `disabled={!p.activo}` (comentario propio explica por qué), revisado y es
  intencional, no un bug.

**Conclusión de la matriz**: no se encontró ningún caso nuevo del patrón
`ReunionStatus` (un enum de UI desalineado del backend real). Los siete
verticals v5/v6 nombrados explícitamente en el enunciado
(RRHH/viajes/voz/podcasts/reuniones/analista/mensajes/inventario) más MCP y
Ads — nueve superficies en total — verifican limpio contra su fuente real
(migración Alembic donde existe `CheckConstraint`, `Literal[]` del router
donde no).

---

## 3. Qué NO se hizo, y por qué

- **No se construyó una página `/app/casa-inteligente`** — decisión
  documentada en §1.2, no una omisión.
- **No se movió MCP a la navegación principal** — es una decisión de
  arquitectura pinned (`ARCHITECTURE.md` §15.i), no un hallazgo de esta
  auditoría.
- **No se agregó ningún ícono nuevo a `icons.tsx`** — el enunciado pedía
  explícitamente reusar uno existente para Ads; se usó `SendIcon` (ya
  usado en el composer de chat y en Configuración/Mensajería — mismo
  criterio de reuso que dejó v5 con `MicIcon` para "Voz").
- **No se tocó ningún archivo fuera de `apps/web/**`** — en particular, la
  columna `employees.status` sin `CheckConstraint` (§2, fila RRHH) se dejó
  tal cual: es una decisión de diseño ya documentada en
  `ARCHITECTURE.md` §14.b ("texto abierto a propósito"), no un bug — se
  anota en la matriz solo como transparencia de fuente, no como hallazgo a
  corregir.
- **No se re-auditaron las verticales v2-v4 "vintage"** (Misiones,
  Automatizaciones, IDE, Remoto, Órdenes, Negocios, Perfil vivo, Skills,
  Ajustes, Facturación, Panel, Persona, Memoria, Conectores, Archivos,
  Recordatorios, Contactos, Finanzas) — fuera de la lista de verticals que
  el enunciado pidió explícitamente revisar contra su backend, y ya
  pasaron por 3+ rondas de auditoría convergente en v2-v4
  (`DIRECCION_ACTUAL.md`). Si se quiere ese barrido, es un WP aparte.
- **No se tocó el patrón `tryRefresh` de `lib/api-ordenes.ts`** — a
  diferencia de `api-misiones.ts`/`api-reuniones.ts`/`api-mcp.ts`/etc.
  (que sí implementan el reintento con gate de TOTP), `api-ordenes.ts`
  usa un patrón deliberadamente más simple (redirect directo a `/login` en
  401, documentado así en su propio docstring: "mismo patrón mínimo de
  auth... sin el refresh-on-401"). Esto significa que un usuario con 2FA
  activo en `/app/ordenes` pierde sesión sin el prompt de reingreso de
  código que sí tienen las páginas más nuevas — **posible inconsistencia
  de UX real** (no confirmé si es una decisión deliberada de ese WP o una
  omisión), pero está fuera del alcance de este paquete (no es nav/badges/
  pocos-clicks de una vertical nueva) y tocar `lib/api-ordenes.ts` no
  estaba en las rutas asignadas a esta auditoría. Queda anotado aquí para
  quien audite `lib/api-*.ts` de forma dedicada.

---

## 4. Verificación obligatoria — resultados

```
cd apps/web && npx tsc --noEmit
```
→ limpio, exit code 0 (dos corridas, antes y después del pulido de
`ResumenCampanas.tsx`/`BorradoresAds.tsx`).

```
cd apps/web && npx next lint --file <cada archivo tocado>
```
→ `✔ No ESLint warnings or errors` sobre los 6 archivos (5 nuevos de Ads +
`nav-items.ts`).

```
cd apps/web && npm run build
```
→ `next build` completo, compila y pre-renderiza las 35 páginas como
estático, incluida `/app/ads` (`7.79 kB`, `First Load JS 97.8 kB`) — sin
errores de tipo ni de build. `next build` es un comando de un solo disparo
(no un servidor) — no quedó ningún proceso huérfano; verificado con `ps
aux` tras cada corrida (el único `next dev`/`next-server` visible pertenece
a `/Users/hennsolutionsllc/Documents/edecan/`, un proyecto **distinto**,
ya corriendo antes de esta sesión — no se tocó, fuera del alcance de este
repo).

Sin framework de tests de frontend configurado (confirmado, `package.json`
solo tiene `dev`/`build`/`start`/`lint`) — no se agregó ninguno, tal como
pedía el enunciado.

---

## 5. Archivos tocados

Nuevos:
- `apps/web/src/lib/api-ads.ts`
- `apps/web/src/app/(app)/app/ads/page.tsx`
- `apps/web/src/components/ads/ConectarMetaAds.tsx`
- `apps/web/src/components/ads/ResumenCampanas.tsx`
- `apps/web/src/components/ads/BorradoresAds.tsx`
- `docs/cumplimiento/barrido-v7-ux.md` (este archivo)

Modificados:
- `apps/web/src/components/layout/nav-items.ts` (entrada de Ads + limpieza
  de comentarios stale "puede dar 404")

Ningún otro archivo de `apps/web/**` fue modificado. Cero archivos fuera de
`apps/web/**` tocados salvo este informe. Cero LinkedIn en cualquier texto.
Cero secretos reales (los placeholders de ejemplo en `ConectarMetaAds.tsx`
son solo `placeholder=` de formulario, nunca un valor real). Cero `git`.
