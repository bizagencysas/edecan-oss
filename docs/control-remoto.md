# Control remoto

Edecán puede, con aprobación explícita en ambos extremos, dejar que el dueño de un tenant vea la pantalla de su propio equipo desde el panel web. Este documento es el diseño completo de esa capacidad — a nivel de TeamViewer/AnyDesk y más allá — y también la referencia exacta de qué parte de ese diseño ya está construida hoy (fase v2, `docs/roadmap.md`) frente a qué queda documentada para después (P2, `docs/roadmap.md`).

> Esto NO es asesoría legal ni un compromiso de certificación (SOC2, ISO 27001, etc.). Es el diseño técnico y el modelo de amenazas tal como existen en este repositorio en el momento de escribirse. Antes de operar control remoto sobre equipos de terceros (no solo el propio) a cualquier escala, revisa los términos de uso que le presentas a tus usuarios y, si aplica, la normativa de privacidad/vigilancia de cada jurisdicción donde operes (ver también [`cumplimiento/privacidad.md`](./cumplimiento/privacidad.md)).

## 1. Qué hay hoy vs. qué es diseño — léelo primero

La regla de producto de `ARCHITECTURE.md` §0 y `docs/roadmap.md` es innegociable: **control remoto = emparejamiento + aprobación humana + cifrado, nunca un backdoor silencioso**. Todo lo de abajo — construido o solo diseñado — respeta esa regla; la diferencia entre columnas es únicamente "ya hay código" vs. "todavía es solo este documento".

| Pieza | Estado hoy | Dónde vive |
|---|---|---|
| Sesión de **solo vista** por *polling* de capturas de pantalla | **Real** (P1) | `apps/api/edecan_api/routers/remote.py`, `apps/web/src/app/(app)/app/remoto/` |
| Consentimiento explícito antes de crear la sesión | **Real** (P1) | `SessionCreateIn` en `routers/remote.py` — `consent: true` obligatorio, 422 si no |
| Auditoría de solicitud/inicio/denegación/fin | **Real** (P1) | `audit_log` vía `Repo.add_audit_log`, acciones `remote.session.*` |
| Banner permanente "sesión activa — solo lectura" en el panel web | **Real** (P1) | `components/remoto/RemoteViewer.tsx` |
| Captura de pantalla real del companion (acción `screenshot`) | **Real (v0.5)** — macOS nativo y Windows/Linux con el extra `remote-control`; entrega JPEG/PNG optimizado, dimensiones, MIME y origen multimonitor | `apps/companion/edecan_companion/actions.py`; `routers/remote.py` degrada de forma limpia ante companions desactualizados o deshabilitados |
| Tabla `remote_sessions` en Postgres | **Real** — las sesiones persisten ahí de verdad, vía `Repo.create_remote_session`/`list_remote_sessions`/`get_remote_session`/`record_remote_session_frame`/`mark_remote_session_denied`/`mark_remote_session_ended` (`edecan_api/repo.py`, consumidos desde `routers/remote.py`) | `docs/roadmap.md`; `packages/db/edecan_db/models.py::RemoteSession` |
| Tabla `devices` en Postgres (emparejamiento de apps móviles) | **Tabla creada, todavía sin ningún código que la use** — el emparejamiento QR + *fingerprint* de este documento (§4) y el visor móvil (§9) son P2; hasta que aterricen, `device_id` queda `NULL` en cada `remote_sessions` | `docs/roadmap.md`; `packages/db/edecan_db/models.py::Device` |
| Emparejamiento por QR + código de 6 dígitos con verificación de *fingerprint* | **Diseño de este documento** — el emparejamiento de companion que existe hoy (`POST /v1/companion/pair-code`) es un código alfanumérico de 8 caracteres sin verificación de fingerprint | §4 de este documento |
| Transporte WebRTC (video H.264, DataChannel, DTLS-SRTP, TURN propio) | **Diseño de este documento** — el prototipo de hoy es HTTP *polling* liso, sin cifrado de extremo a extremo adicional al TLS del propio `PUBLIC_BASE_URL` | §5 de este documento |
| Niveles de permiso (ver / ver+puntero / control total) | **Parcial** — `kind` pasó de fijo en `"view"` a `"view" \| "control"` (fase v4); `"control"` ya es input REAL de teclado/mouse (más que "ver+puntero", que sigue sin existir) — el nivel intermedio puramente informativo del diseño original de §6 no se construyó | §6 (diseño original) y §7bis (implementado) de este documento |
| Input injection (teclado/mouse remotos) | **Real (v0.5)** — macOS/Windows/Linux; mover, clic/doble/clic derecho, arrastrar, scroll, texto, teclas y atajos. Sigue requiriendo opt-in y aprobación local acotada a la sesión | §7bis; `apps/api/edecan_api/routers/remote.py`, `apps/companion/edecan_companion/actions.py`/`approval.py` |
| Indicador visible permanente **en el equipo controlado** (menú/bandeja del sistema) | **P2** — el companion de hoy es una CLI (`python -m edecan_companion`) sin presencia gráfica; solo hay líneas de log/consola por cada acción (incluidas las de input, fase v4 no cambió esto) | §8 de este documento |
| Botón de pánico local | **P2 — requiere protocolo companion→backend que no existe hoy** | §8 de este documento (gap documentado explícitamente) |
| Apps iOS y Android como visor/control | **Real (v0.5)** — pestaña Remoto, JPEG/PNG, multimonitor, gestos y barra de teclado | `apps/mobile/ios`, `apps/mobile/android` |

### 1.1 Por qué el prototipo empieza con *polling* HTTP y no con WebRTC

Podría parecer que construir directo sobre WebRTC habría sido "hacerlo bien desde el principio" — pero WebRTC trae consigo señalización, ICE/STUN/TURN, negociación SDP y un companion con capacidad de streaming continuo, ninguno de los cuales existe todavía en este repositorio. Meter todo eso en el primer WP de control remoto habría significado o (a) no entregar nada verificable esta ronda, o (b) entregar una integración de WebRTC a medio probar, sin los cimientos de producto (consentimiento, flag de plan, auditoría, degradación limpia cuando falta una pieza) que sí importan desde el primer día. El *polling* HTTP, en cambio, reutiliza infraestructura que ya existe y ya está probada (`ConnectionManager.send_command`, el mismo mecanismo que usa `usar_computadora` desde v1) y permite ejercitar de verdad — con tests reales, no aspiracionales — exactamente los mismos contratos de producto (consentimiento explícito, aprobación local, flag de plan, auditoría completa, límites de uso) que el diseño final también va a necesitar. Cuando WebRTC aterrice (§5, §11), esos contratos no cambian — solo cambia cómo viaja el video.

## 2. Principios de diseño (no negociables)

1. **Consentimiento en ambos extremos.** El dueño del tenant consiente en el panel (`consent: true` explícito). El dueño **del equipo** — que en el caso normal es la misma persona, pero arquitectónicamente son dos roles distintos — consiente por segunda vez, **localmente, en su propia máquina**, cuando el companion le pregunta antes de mandar el primer frame. Ninguna de las dos aprobaciones sustituye a la otra.
2. **Visibilidad permanente.** Mientras una sesión está activa, tiene que ser imposible no darse cuenta de que lo está: banner en el panel web (real hoy), e indicador visible en el equipo controlado (diseñado, §8). Una sesión de "vista" que pasa desapercibida para el dueño del equipo no es aceptable aunque técnicamente sea de solo lectura.
3. **Mínima retención.** Los frames no se persisten: `routers/remote.py` los reenvía en la respuesta HTTP y no los guarda en ningún lado (ni en `remote_sessions`, que solo guarda metadatos — conteo, estado, marcas de tiempo). El diseño de transporte objetivo (§5) mantiene esa misma propiedad: el backend jamás decodifica ni almacena el video, solo señaliza la conexión entre las dos puntas.
4. **Apoyarse en los gates del sistema operativo, nunca evadirlos.** macOS exige un clic humano explícito en Preferencias del Sistema para conceder permiso de Grabación de Pantalla (captura, real hoy) y de Accesibilidad (input injection, real hoy — fase v4, §7bis). Cada vez que este diseño depende de uno de esos permisos, la arquitectura **espera** a que el usuario lo conceda por la vía normal del sistema operativo — nunca intenta automatizarlo, ni hay ninguna bandera de configuración que lo salte.
5. **El backend nunca ve el contenido en claro en el transporte objetivo.** El *polling* HTTP de hoy sí pasa por el backend (es la propia API la que reenvía el frame que le pidió al companion) — es una limitación conocida del prototipo, no el diseño final. El transporte objetivo (WebRTC, §5) hace que el backend deje de estar en el camino de los datos: solo negocia la conexión (señalización), el video viaja cifrado directo entre las dos puntas (o a través de un TURN que solo repite paquetes cifrados, sin poder leerlos).

## 3. Modelo de amenazas

Mismo formato que [`seguridad-modelo-amenazas.md`](./seguridad-modelo-amenazas.md), aplicado específicamente a control remoto.

### 3.1 Activos a proteger

| Activo | Dónde vive (hoy / diseño) | Por qué importa |
|---|---|---|
| Frames de pantalla del equipo controlado | En tránsito únicamente — `GET .../frame` (hoy), canal WebRTC (diseño) | Puede mostrar literalmente cualquier cosa: correos abiertos, contraseñas en un gestor visible, documentos confidenciales. Es el activo más sensible de esta capacidad completa. |
| Código/QR de emparejamiento | Redis, TTL corto (`POST /v1/companion/pair-code` hoy; el QR+código de 6 dígitos es diseño, §4) | Su robo durante la ventana de validez permite que un tercero registre SU companion como si fuera el equipo del tenant. |
| *Fingerprint* de las claves de sesión | No existe todavía (diseño, §4) | Es la defensa contra MITM en el emparejamiento — comprometerlo (o nunca verificarlo) anula esa defensa. |
| Metadatos de sesión (`remote_sessions`: quién, cuándo, cuántos frames, duración) | Postgres, tabla `remote_sessions` (§7.4 `docs/roadmap.md`) | Es el rastro de auditoría — su pérdida o alteración impide demostrar después quién vio qué y cuándo. |
| `audit_log` de acciones `remote.session.*` | Postgres, tabla `audit_log` (ya existe desde v1) | Mismo motivo que en el modelo de amenazas general: sin esto, ningún incidente de control remoto es investigable después de los hechos. |
| Eventos de input (teclado/mouse) | En tránsito únicamente — `POST .../input` (fase v4, real hoy — §7bis), DataChannel de WebRTC (diseño, §5) | Es el activo de mayor impacto posible de todo el sistema: control total del equipo. Por eso es el que más candados en serie exige de todo este documento (4, ver §7bis) — nunca se persiste el contenido de `texto` en claro, ni en `audit_log` ni en la bitácora del companion. |

### 3.2 Escenarios de abuso y mitigaciones

#### Acceso no autorizado a una sesión

**Escenario**: alguien que no es el dueño del tenant, o un miembro del tenant sin la intención del dueño del equipo, intenta iniciar o espiar una sesión de vista remota.

**Mitigaciones**:
- Autenticación JWT normal (`Bearer`) más el flag de plan `companion.remote_view` (`edecan_schemas.plans.FLAG_COMPANION_REMOTE_VIEW`) — sin los dos, `403` antes de llegar a tocar el companion.
- El companion **siempre** pregunta localmente antes del primer frame (hoy: prompt en la terminal del companion, `edecan_companion.approval.default_approver`; el mismo mecanismo que ya usa `usar_computadora`) — un JWT robado no basta para ver nada si nadie frente al equipo lo aprueba.
- Aislamiento por `tenant_id` en cada método de `Repo`/`SqlRepo` que toca `remote_sessions` (`edecan_api/repo.py`: `WHERE tenant_id = ...` explícito en cada `SELECT`/`UPDATE`, además de la política RLS `tenant_isolation` de Postgres sobre la tabla) — un tenant nunca puede listar, leer ni pedir frames de una sesión de otro tenant (`404`, no `403`, para no confirmar siquiera que el `id` existe).

#### Replay de una sesión o de un comando

**Escenario**: alguien captura el tráfico de una sesión pasada (una URL de frame, un token, un comando de señalización) e intenta reproducirlo más tarde para reabrir acceso.

**Mitigaciones hoy**: cada frame es una respuesta HTTP nueva (no hay un token de sesión reutilizable distinto del JWT normal, que ya expira a los 30 minutos — `ARCHITECTURE.md` §10.12); una sesión `ended`/`denied` rechaza cualquier intento posterior de pedir un frame (`409`/`403` desde `get_frame`, ver `routers/remote.py`) sin volver a preguntarle nada al companion.

**Mitigación de diseño (transporte objetivo, §5)**: DTLS-SRTP usa números de secuencia y una ventana anti-replay por diseño del propio protocolo (RFC 3711) — un paquete de video capturado y reenviado más tarde se descarta en la capa de transporte, no depende de lógica de aplicación.

#### Man-in-the-middle en el transporte

**Escenario**: un atacante en la misma red (Wi-Fi pública, red corporativa comprometida) intenta interceptar o inyectar tráfico entre el visor y el equipo controlado.

**Mitigación hoy**: TLS de extremo del `PUBLIC_BASE_URL` (igual que el resto de la API) protege el *polling* HTTP en tránsito hacia/desde el backend — pero el backend sigue siendo un intermediario que ve los bytes del frame (limitación conocida, ver §10).

**Mitigación de diseño**: la verificación de *fingerprint* de claves durante el emparejamiento (§4) es la defensa específica contra MITM en el momento más crítico — el primer contacto entre visor y equipo, antes de que exista ninguna relación de confianza previa. DTLS-SRTP (§5) cifra el propio medio de extremo a extremo, así que un MITM en la red no puede ver ni alterar el video aunque logre interceptar los paquetes.

#### Insider (operador de la instancia hosted)

**Escenario**: alguien con acceso privilegiado a la infraestructura de una instancia hosted (base de datos como *owner*, logs, el propio proceso de `apps/api`) intenta ver sesiones de control remoto de un tenant sin su conocimiento.

**Mitigación hoy**: el backend nunca decodifica el frame — lo recibe en base64 de un lado (companion) y lo reenvía en base64 al otro (panel web) dentro de la misma respuesta HTTP; no queda guardado en ninguna tabla ni log (el *rate limit* en Redis solo guarda una marca de tiempo, `remote:frame:last:{tenant}:{session}`, nunca el contenido).

**Brecha conocida hoy**: aunque no se persiste, el proceso de `apps/api` sí ve los bytes del frame en memoria durante el reenvío — un operador con acceso al proceso en ejecución (no solo a la base de datos) podría, en teoría, instrumentar el proceso para capturarlos. Es exactamente la brecha que el transporte objetivo (§5) cierra: con WebRTC real, el backend deja de estar en el camino de los datos y pasa a ser puro servidor de señalización, así que ni siquiera un insider con acceso total al proceso de `apps/api` puede ver el video — solo vería metadatos de señalización (quién se conecta con quién, cuándo), igual que hoy ve metadatos de sesión.

#### Dispositivo robado o perdido

**Escenario**: el teléfono o la laptop desde donde alguien inició sesión como visor (o el propio equipo companion) se pierde o es robado.

**Mitigaciones**:
- Sesión de visor: el JWT expira a los 30 minutos (access) / 30 días (refresh) — el mismo mecanismo de toda la plataforma, sin nada especial para control remoto; revocar acceso del usuario (cambiar contraseña, o un futuro "cerrar todas las sesiones") corta el acceso.
- Equipo companion perdido/robado: **revocación inmediata** vía la tabla `devices` (diseño, §4) — marcar el dispositivo `revoked` debe bastar para que la próxima vez que ese companion intente conectarse (o esté conectado) se le cierre el WebSocket. Este flujo de revocación en caliente todavía no existe (el emparejamiento de hoy vive solo en Redis con TTL, sin un registro persistente de "dispositivos conocidos" que se pueda revocar) — es parte de lo que aporta la tabla `devices`.

## 4. Emparejamiento de dispositivos

El emparejamiento que existe **hoy** (`POST /v1/companion/pair-code` + `WS /v1/companion/ws?code=`, `routers/companion.py`) es deliberadamente simple: un código alfanumérico de 8 caracteres en Redis con TTL de 600 segundos, de un solo uso, más un límite de intentos por IP para acotar fuerza bruta contra el *handshake*. Sirve bien para las acciones de companion que existen hoy (IDE embebido, `usar_computadora`), donde el peor caso de un emparejamiento indebido es que alguien ejecute una acción ya sujeta a aprobación local.

Para control remoto — donde el activo en juego es literalmente la pantalla completa del equipo — el diseño de este documento sube el estándar:

1. **QR + código de 6 dígitos, mostrados en las DOS pantallas a la vez.** El companion (o su futura interfaz gráfica, ver §8) muestra un QR grande y, debajo, el mismo código en dígitos legibles (para el caso sin cámara a mano). El panel web, al iniciar el flujo de emparejamiento (no el de iniciar una sesión — esto es un registro de dispositivo que se hace una sola vez, o cuando se agrega un equipo nuevo), pide escanear ese QR o teclear el código de 6 dígitos.
2. **Verificación de *fingerprint* de claves en ambas pantallas.** Al emparejar, cada punta genera un par de claves y deriva un *fingerprint* corto (p. ej. un hash truncado, representado como 4-6 grupos de caracteres legibles, al estilo Signal/WhatsApp) de la clave pública de la otra punta. Ese *fingerprint* se muestra en **ambas** pantallas — panel web y companion — y el emparejamiento no se confirma hasta que la persona verifica visualmente que coinciden. Esto es lo que cierra la ventana de MITM del §3.2: un atacante que lograra insertarse en el intercambio de claves tendría un *fingerprint* distinto, visible para quien compara.
3. **Registro en la tabla `devices`** (`docs/roadmap.md`: `user_id, nombre, plataforma, kind: companion|mobile, status: active|revoked, last_seen_at, fingerprint`). Cada companion emparejado exitosamente queda como una fila — a diferencia del pairing de hoy, que no dejaba ningún registro persistente más allá de la conexión WebSocket en memoria de `ConnectionManager`.
4. **Revocación inmediata.** Marcar un `device` como `revoked` (desde el panel: "Dispositivos" → "Revocar") debe:
   - Rechazar cualquier intento de reconexión de ese `fingerprint`.
   - Si está conectado en ese momento, cerrar el WebSocket activo del lado del servidor.
   - Cancelar cualquier `remote_sessions` en curso asociada a ese `device_id`.
   Este es el control que hoy falta por completo (§3.2, "dispositivo robado o perdido") — el pairing actual no tiene concepto de "dispositivo conocido" que se pueda revocar, solo un código de un solo uso que ya se consumió.

El QR/código de 6 dígitos de este diseño es un paso **adicional y más ceremonioso** que el `pair-code` de 8 caracteres que ya existe — no lo reemplaza para las demás acciones del companion (IDE, `usar_computadora`), que pueden seguir usando el flujo simple. Control remoto exige el flujo con verificación de *fingerprint* porque el costo de un MITM exitoso ahí es mucho mayor.

## 5. Transporte objetivo: WebRTC de extremo a extremo

El prototipo P1 de hoy usa *polling* HTTP liso a propósito — es la forma más simple de entregar algo real y auditable rápido, y ya dejó el resto de la arquitectura (consentimiento, flag de plan, auditoría, degradación limpia) lista para no tener que rehacerse cuando el transporte cambie. El transporte objetivo, para llegar a "nivel TeamViewer", es **WebRTC**:

- **Captura**: en macOS, [ScreenCaptureKit](https://developer.apple.com/documentation/screencapturekit) (el mismo framework del sistema que ya usa la acción `screenshot` del companion, `docs/roadmap.md`, solo que en modo streaming continuo en vez de una captura suelta) produce frames que se codifican a **H.264** con el encoder de hardware del equipo (VideoToolbox) — bajo uso de CPU incluso en sesiones largas.
- **Video**: el stream H.264 va por un `RTCPeerConnection` estándar de WebRTC, como cualquier videollamada.
- **Eventos**: input remoto YA existe (fase v4, §7bis) pero hoy viaja por `POST` HTTP, un comando a la vez — el diseño objetivo lo mueve a un **DataChannel** separado del PeerConnection, para mensajes de baja latencia tipo `{type: "mousemove", x, y}`, nunca mezclado con el canal de video. Migrar de HTTP a DataChannel no cambia ninguno de los 4 candados de §7bis, solo el transporte del evento en sí.
- **Cifrado**: WebRTC exige **DTLS-SRTP** por especificación — no es una opción, es parte del protocolo. Cada sesión negocia sus propias claves efímeras (forward secrecy: comprometer una sesión pasada no compromete las futuras), y el *fingerprint* de esas claves DTLS es precisamente lo que se verifica en el emparejamiento (§4) para atar la conexión WebRTC a un dispositivo ya verificado, no a cualquiera que adivine la señalización.
- **TURN propio**: cuando el NAT/firewall de una o ambas puntas impide conexión directa (el caso común en redes corporativas o domésticas con NAT simétrico), WebRTC necesita un relay — [coturn](https://github.com/coturn/coturn) autoalojado (nunca un TURN de un tercero fuera del control de la plataforma, para no meter un cuarto actor con visibilidad del tráfico). El punto clave: **un TURN solo repite paquetes ya cifrados por DTLS-SRTP** — no los descifra, no los almacena más que en tránsito. Verlo pasar por el TURN no es distinto, en términos de confidencialidad, de verlo pasar por cualquier router intermedio de Internet.
- **El backend (`apps/api`) solo señaliza.** Su único rol en este diseño es intercambiar las ofertas/respuestas SDP y los candidatos ICE entre el visor y el companion — exactamente lo que ya hace `ConnectionManager.send_command`/`handle_incoming` para las acciones actuales del companion, solo que el "comando" en este caso es "aquí está mi oferta SDP, reenvíasela a la otra punta". Una vez que el `RTCPeerConnection` queda establecido, el video **no vuelve a pasar por el backend** — a diferencia del prototipo P1 de hoy, donde cada frame sí pasa por `apps/api` (ver la brecha de insider en §3.2). Esto es lo que hace que este diseño sea E2E **real**, no solo cifrado en tránsito hacia un intermediario de confianza.

## 6. Niveles de permiso de sesión

**Nota (fase v4): el prototipo ya tiene DOS niveles, no uno** — `kind="view"` (fijo hasta esta fase) y `kind="control"` (nuevo, §7bis). El diseño original de esta sección definía TRES niveles; en la implementación real se saltó directo de "ver" a "control total", sin construir el escalón intermedio puramente informativo ("ver + puntero") — se conserva la fila en la tabla como diseño no construido, no como error.

`Repo.create_remote_session` sigue insertando siempre `kind="view"` (firma sin tocar); `POST /v1/remote/sessions` promueve a `"control"` acto seguido si el cliente lo pidió y tiene el flag — ver §7bis. Cada nivel tiene su propia aprobación explícita **separada** en el equipo controlado — aprobar "ver" nunca implica haber aprobado "control total":

| Nivel | Qué permite | Aprobación requerida en el companion |
|---|---|---|
| **Ver** (`view`) | Solo recibir video de la pantalla. | Un prompt: "¿Permitir que vean tu pantalla?" |
| **Ver + puntero** (`view_pointer`) | Además de ver, se muestra la posición del puntero del VISOR superpuesta en la pantalla del equipo controlado (como un puntero remoto de presentación) — sigue sin poder mover NADA en el equipo real, es puramente informativo/cosmético para señalar algo durante una sesión de soporte guiado. **No se construyó** (fase v4 saltó directo a control total). | Un prompt distinto: "¿Permitir que además señalen con un puntero en tu pantalla?" |
| **Control total** (`full_control`, `kind="control"`) | Input real: mouse, drag, scroll, teclado y atajos. **Real en macOS/Windows/Linux desde v0.5** (§7bis), sobre `POST` HTTP, no sobre WebRTC. | Un prompt POR COMANDO (más granular que "un tercer prompt" único), permiso nativo del sistema, flag de plan y `remote_input_enabled` del companion: cuatro candados independientes. |

Ningún nivel se hereda del anterior automáticamente: pasar de "ver" a "control total" a mitad de una sesión no está soportado — hay que crear una sesión NUEVA con `kind="control"` desde el principio (no hay una ruta de "elevar" una sesión `view` ya en curso).

## 7. Input injection — diseño original (P2 en su momento; implementado como "fase 2" en fase v4)

**Nota (fase v4, ver §7bis más abajo): esto YA está construido**, sobre el *polling* HTTP de hoy — no sobre el transporte WebRTC que describe el resto de esta sección (`CGEvent`/DataChannel siguen siendo diseño para cuando WebRTC aterrice, §5). Esta sección §7 se conserva tal cual (histórica) porque su razonamiento de fondo — por qué es la capacidad de mayor impacto, por qué depende del gate de Accesibilidad de macOS, por qué la aprobación es una capa de producto adicional al gate del sistema operativo — se siguió al pie de la letra en la implementación real; §7bis documenta qué cambió al construirla ANTES de que existiera WebRTC, y por qué eso sigue respetando la regla "nunca un backdoor silencioso".

### Por qué era P2 y no P1 parcial (razonamiento original, sigue vigente)

Mover el mouse o escribir en el equipo de otra persona es, por un margen amplio, la capacidad de mayor impacto de todo este documento — el resto (ver la pantalla) es observación; esto es actuación. `ARCHITECTURE.md` §0 ya fija el principio general ("Control remoto = arquitectura tipo TeamViewer/AnyDesk: emparejamiento explícito + aprobación humana + canal cifrado. Nunca un backdoor silencioso") precisamente pensando en esta pieza. Construirla bien exige que TODO lo anterior (emparejamiento con *fingerprint*, transporte E2E, niveles de permiso) ya esté sólido primero — construirla sobre el *polling* HTTP de hoy, sin esas piezas, sería exactamente el backdoor silencioso que la regla prohíbe.

### Cómo se construiría (cuando le toque)

- **`CGEvent`** (el API de macOS para sintetizar eventos de teclado/mouse a nivel de sistema) es el mecanismo técnico correcto — pero macOS **exige** que el proceso que lo llama tenga permiso de **Accesibilidad**, concedido explícitamente en Preferencias del Sistema → Privacidad y Seguridad → Accesibilidad, con un clic humano en ese momento. No existe una forma de conceder ese permiso por API ni por configuración — es, por diseño del propio sistema operativo, un gate que solo un humano frente al equipo puede abrir.
- El companion **se apoya en ese gate, nunca lo evade.** Si el permiso no está concedido, la acción de input falla con un error claro (mismo patrón que ya usa `screenshot` para el permiso de Grabación de Pantalla, `docs/roadmap.md`) — nunca se intenta un mecanismo alternativo (inyección a nivel de kernel, herramientas de accesibilidad de terceros sin permiso, etc.) para saltárselo.
- Encima de ese gate del sistema operativo va el gate de **producto**: la aprobación local del nivel "control total" (§6) es una condición **adicional**, no un sustituto — un companion con permiso de Accesibilidad ya concedido de una sesión anterior igual debe volver a preguntar por cada sesión nueva de control total.
- Los eventos viajarían por el **DataChannel** de WebRTC (§5), nunca mezclados con el canal de video ni con la señalización HTTP del backend.
- **Auditoría de cada evento**, no solo de la sesión: a diferencia de "ver" (donde se audita inicio/fin/conteo de frames), control total necesitaría auditar cada acción de teclado/mouse de forma agregada y searchable (p. ej. "se hicieron 40 clics y se escribieron 2 líneas de texto entre las 10:03 y las 10:07"), sin necesariamente registrar cada pulsación de tecla en texto claro (evitar que el propio log de auditoría se vuelva un keylogger de facto — un balance a diseñar con cuidado cuando se construya).

## 7bis. Fase 2: input remoto (implementado, fase v4)

Completa el wishlist de "manejar el equipo, nivel TeamViewer": la vista remota (§1, fase v2) dejó el visor **solo lectura**; esta fase agrega **input real** de teclado/mouse — `POST /v1/remote/sessions {"kind": "control"}` en vez de (o además de) `"view"`, y el nuevo `POST /v1/remote/sessions/{id}/input`. Construida directo sobre el *polling* HTTP de hoy (§1.1: mismo motivo que la vista — entregar algo real y auditable ya, no una integración de WebRTC a medio terminar) en vez de esperar a que el transporte objetivo de §5 aterrice; cuando WebRTC llegue, el contrato de producto de abajo (los 4 candados, la auditoría por comando) no cambia — solo el transporte del propio evento deja de ser un `POST` HTTP.

### Arquitectura

1. **Panel web** (`/app/remoto`, `ConsentGate` → `kind="control"` si el usuario marca el checkbox — solo visible con el flag de plan): clic/doble clic/clic derecho sobre la imagen del frame se mapean de coordenadas de pantalla → resolución real (`components/remoto/coords.ts`, corrige el "letterbox" de `object-fit: contain`) y se mandan como `input_pointer`; el panel de teclado (`RemoteControlPanel.tsx`) manda texto o teclas especiales como `input_key`.
2. **API** (`edecan_api.routers.remote.send_input`) valida sesión activa + `kind="control"`, reenvía la acción al companion vía `companion_manager.ConnectionManager.send_command` (el mismo canal WebSocket que ya usa la vista, `usar_computadora`, y el IDE embebido — ningún transporte nuevo), y traduce la respuesta a HTTP (`_translate_input_companion_error`).
3. **Companion** (`edecan_companion.actions._input_pointer`/`_input_key`) ejecuta el gesto vía `InputBackend`: Quartz/`CGEvent*` en macOS y `pynput` en Windows/Linux. El extra `remote-control` instala el backend apropiado de forma perezosa para no afectar el resto del companion.

### Los 4 candados en serie (ninguno sustituye a los demás)

Mismo principio de "defensa en profundidad" del resto de este documento, aplicado a la capacidad de mayor impacto de todo el producto:

1. **Flag de plan `companion.remote_input`** (`edecan_schemas.plans.FLAG_COMPANION_REMOTE_INPUT`) — recalculado en CADA request desde `PLANES[plan]` (`edecan_api.routers.remote._require_remote_control`), nunca se confía en el flag que traía la sesión al crearse: un downgrade de plan a mitad de una sesión de control corta el acceso al siguiente `POST .../input`, aunque la sesión siga técnicamente `active`. `hosted_basic` no lo trae (igual que `companion.remote_view`); el resto de los planes sí.
2. **`remote_input_enabled: true`** en `~/.edecan/companion.yaml` del companion — **`false` por defecto**, a propósito distinto del resto de acciones del companion (`ide_enabled` sí nace en `true`): dejar que alguien mueva el mouse o escriba en tu equipo exige opt-in explícito del dueño de la máquina, no basta con aprobar una vez en la terminal (`apps/companion/edecan_companion/config.py`, ver el comentario junto al campo).
3. **Aprobación local por comando**, en el companion, con una regla más dura que el resto de acciones (`edecan_companion.approval._approve_input_action`): nunca pasa por `auto_approve` (SIEMPRE pregunta al menos una vez, sin excepción configurable), y su "recordado" (`remote_input_remember_minutes`, default 10) queda acotado a la sesión de control activa — la clave de memoria incluye el `session_id` que el router SIEMPRE manda en los `params`, así que una sesión de control nueva nunca hereda una aprobación recordada de una anterior, ni siquiera dentro de la ventana de minutos.
4. **Permiso nativo de entrada**: macOS exige Accesibilidad concedida por un clic humano y `_QuartzInputBackend` la verifica con `Quartz.AXIsProcessTrusted()`; Windows/Linux respetan los permisos y la sesión gráfica del equipo. Ningún backend los evade ni automatiza.

### Auditoría por comando

Cada `POST .../input` exitoso deja una fila nueva en `audit_log` (`remote.session.input`, `meta` con `tipo` y un resumen — nunca el contenido: `texto` se audita como `{"clave": "texto", "length": N}`, jamás el texto en claro, tanto en `audit_log` como en la bitácora del companion, `edecan_companion.audit._REDACTED_KEYS`, ahora también con `"texto"`). Una denegación del usuario en el companion marca la SESIÓN completa `denied` (no solo ese comando — mismo criterio conservador que ya usaba la vista para una captura denegada) y audita `remote.session.input_denied`, con el mismo patrón "commit de evidencia ANTES del raise" de `docs/seguridad-modelo-amenazas.md` punto 8 que ya usaba `get_frame`.

### Qué NO cambió (sigue P2)

- **Transporte**: sigue siendo `POST` HTTP por comando, no WebRTC/DataChannel — cada clic/tecla es una request-respuesta completa contra `apps/api`, con el mismo rate limit (`REMOTE_INPUT_MIN_INTERVAL_SECONDS`, default 0.05s) que ya usa el *polling* de frames como patrón. El backend sigue en el camino de cada evento (a diferencia del diseño de §5, donde solo señalizaría). Migrar a WebRTC (§5, §11) no cambia ninguno de los 4 candados de arriba — solo el "cómo viaja el evento".
- **Cifrado de extremo a extremo adicional**: TLS del propio `PUBLIC_BASE_URL`, igual que el resto de la API — sin el DTLS-SRTP con *forward secrecy* que traería WebRTC.
- **Nivel "ver+puntero"** (§6): sigue sin existir — se saltó directo de "ver" a "control total real", no se construyó el escalón intermedio puramente informativo.
- **Indicador visible permanente en el equipo controlado** y **botón de pánico** (§8): el companion sigue siendo una CLI sin presencia gráfica — cada acción de input queda en la bitácora de consola/log igual que cualquier otra, sin un ícono de bandeja que muestre "te están controlando ahora mismo" de forma imposible de ignorar.
- **Emparejamiento con verificación de *fingerprint*** (§4): sigue usando el `pair-code` simple de 8 caracteres — control remoto de input no exige todavía el emparejamiento reforzado que este documento diseña en §4.

### Solución de problemas: "ya activé el permiso y sigue sin funcionar" (concesión TCC zombi)

Síntoma: el teléfono conecta pero nunca llegan frames (HTTP 502 con
"Grabacion de pantalla esta desactivada/no esta autorizada..."), y en la Mac
el interruptor de Edecán en Configuración del Sistema **se ve encendido**.

Causa: macOS ancla cada concesión de Grabación de pantalla/Accesibilidad al
*code signing requirement* del binario que la pidió. Con una build firmada
ad-hoc ese requirement es el `cdhash` exacto de ESA build: reinstalar o
re-firmar Edecán (otra identidad, otro rebuild) deja la fila de TCC
"muerta" — el toggle sigue en ON pero `tccd` la ignora y
`CGPreflightScreenCaptureAccess()`/`AXIsProcessTrusted()` devuelven `false`.
La bitácora (`companion.log`, campo `error` de las líneas `ok: false`) y el
propio mensaje 502 lo delatan.

Remediación en la máquina afectada:

1. Salir de Edecán por completo (bandeja → "Salir completamente"; cerrar la
   ventana solo la oculta).
2. `tccutil reset ScreenCapture cc.edecan.desktop && tccutil reset Accessibility cc.edecan.desktop`
3. Abrir Edecán y re-conceder ambos permisos (Grabación en la lista
   superior, no en "Solo grabación de audio del sistema").
4. Salir por completo otra vez y reabrir (un proceso vivo no hereda la
   concesión nueva).

Prevención: firmar releases con una identidad real y estable
(`EDECAN_MACOS_CODESIGN_IDENTITY` en `build-app.sh`; ideal Developer ID) —
el requirement pasa a ser identificador+certificado y sobrevive rebuilds.
Además, desde esta fase el bridge nativo dispara UNA vez por ejecución la
solicitud real del sistema (`CGRequestScreenCaptureAccess` /
`AXIsProcessTrustedWithOptions` con prompt, `remote_bridge.rs`) cuando el
preflight falla, así el permiso sí "llega" a la Mac en el primer intento de
sesión en vez de fallar en silencio hacia el teléfono.

### Referencias de código

`apps/api/edecan_api/routers/remote.py` (`SessionCreateIn.kind`, `send_input`, `_require_remote_control`, `_translate_input_companion_error` — ver el docstring del módulo, sección "Fase 2: input remoto", para el detalle línea por línea) · `apps/api/edecan_api/repo.py` (`Repo.mark_remote_session_kind`, método nuevo y aditivo — `remote_sessions.kind` no tiene `CHECK constraint`, así que no hizo falta ninguna migración) · `apps/companion/edecan_companion/actions.py` (`InputBackend`, `_QuartzInputBackend`, `_input_pointer`, `_input_key`) · `apps/companion/edecan_companion/approval.py` (`_approve_input_action`) · `apps/companion/edecan_companion/config.py` (`remote_input_enabled`, `remote_input_remember_minutes`) · `apps/web/src/components/remoto/{RemoteViewer,RemoteControlPanel,coords,ConsentGate,SessionHistory}` · `apps/web/src/lib/api-remoto.ts` · tests: `apps/api/tests/test_remote_router.py`, `apps/companion/tests/{test_actions_input.py,test_approval.py,test_config.py}`.

## 8. Auditoría y controles operativos

### Lo que ya audita el prototipo P1 (hoy)

`routers/remote.py` escribe en `audit_log` (vía `Repo.add_audit_log`, la misma tabla de v1) en cada transición relevante:

| `action` | Cuándo | `meta` |
|---|---|---|
| `remote.session.requested` | `POST /v1/remote/sessions` exitoso (sesión creada `pending`) | `{"kind": "view"}` o `{"kind": "control"}` (fase v4) |
| `remote.session.started` | Primer `GET .../frame` exitoso (transición `pending` → `active`) | `{}` |
| `remote.session.denied` | El companion respondió "acción rechazada" al pedir un frame | `{"error": "..."}` |
| `remote.session.input` (fase v4) | `POST .../input` exitoso — una fila POR COMANDO, nunca agregada | `{"tipo": "pointer"\|"key", ...}` — nunca el `texto` en claro, ver §7bis |
| `remote.session.input_denied` (fase v4) | El companion respondió "acción rechazada" a un `input_pointer`/`input_key` — deniega la SESIÓN completa, no solo ese comando | `{..., "error": "..."}` |
| `remote.session.ended` | `POST .../end` (primera vez; es idempotente, no se duplica) | `{"frames_count": N, "duration_seconds": N o null}` |

Además, el conteo de frames y el estado de cada sesión son consultables en cualquier momento vía `GET /v1/remote/sessions` / `GET /v1/remote/sessions/{id}` — no hace falta ir al `audit_log` crudo para saber si hay una sesión activa ahora mismo.

### Indicador visible en el equipo controlado

**Brecha conocida, no resuelta hoy.** El companion actual (`apps/companion/edecan_companion/main.py`) es una **CLI**: corre en una terminal, y cada acción que ejecuta (incluida la futura `screenshot`) queda como una línea de log/consola en esa terminal (`edecan_companion.audit.log_action`) — visible solo si la persona tiene esa terminal a la vista. Eso cumple el principio de "nunca actúa en silencio" en un sentido literal (queda un rastro visible en algún lado), pero no cumple el estándar más alto de "visibilidad permanente" del §2: alguien que minimizó la terminal, o que ni siquiera sabe que el companion corre ahí, no tiene ninguna señal en pantalla de que una sesión de vista remota está activa.

El diseño correcto es una presencia gráfica persistente del companion — en macOS, un ícono en la barra de menú (`NSStatusItem`) que cambie de estado (color/animación) mientras haya una `remote_sessions` activa para ese equipo, con un clic que muestre "Sesión activa desde [fecha], N frames enviados" y un botón para terminarla. Esto implica migrar el companion de CLI a una app con interfaz gráfica mínima (o al menos un ícono de bandeja) — un cambio de superficie más grande que agregar una acción nueva, por eso queda fuera de este WP.

### Botón de pánico local

**Brecha arquitectónica explícita, no solo de UI.** Un botón de pánico necesita que el companion pueda avisarle al backend "termina TODAS mis sesiones activas, ya" **por iniciativa propia** — pero el protocolo WebSocket de hoy (`ConnectionManager`, `routers/companion.py`) es estrictamente petición/respuesta en una sola dirección: el backend manda `{request_id, action, params}` y el companion contesta `{request_id, ok, ...}`; `ConnectionManager.handle_incoming` descarta en silencio cualquier mensaje que no traiga un `request_id` de una petición pendiente (es el comportamiento correcto hoy — un heartbeat sin `request_id` no debe romper nada), lo que de paso significa que **no hay ningún tipo de mensaje espontáneo del companion hacia el backend**.

Cerrar esto requiere:
1. Un nuevo tipo de mensaje companion→backend sin `request_id` de respuesta (p. ej. `{"type": "panic"}`), que `handle_incoming` reconozca como un caso aparte en vez de descartarlo.
2. Que el backend, al recibirlo, llame el equivalente de `end_session` sobre cada `remote_sessions` activa de ese `tenant_id`/`device_id` — reutilizando la misma lógica de `routers/remote.py`, no una nueva.
3. Un atajo de teclado o botón físico/de UI en el companion gráfico (ver arriba) que dispare ese mensaje sin fricción — un botón de pánico que exige varios clics en un menú no cumple su propósito.

## 9. Roadmap móvil

La app iOS/Android (P2, `docs/roadmap.md`) no controla el Mac por su cuenta en ningún momento — es, literalmente, **el visor WebRTC** del diseño del §5 corriendo en un teléfono en vez de en el navegador:

- **iOS**: `EdecanKit` (SPM local) ya está pensado con un cliente tipado contra `/v1/*`; el visor remoto se sumaría con `RTCPeerConnection` de [WebRTC.framework](https://webrtc.github.io/webrtc-org/native-code/ios/) (el mismo protocolo, sin reimplementar nada del lado del transporte) renderizando el stream de video en una vista SwiftUI.
- **Android**: mismo patrón con **libwebrtc** sobre Kotlin/Compose Multiplatform.
- En ambos casos, la app móvil **nunca** iguala a un "control" del Mac sin que exista una sesión ya aprobada del lado del equipo — es un visor más, sujeto a exactamente las mismas reglas de consentimiento/aprobación de este documento, no una puerta trasera adicional.

## 10. El prototipo P1 de hoy — referencia rápida

Para el detalle línea por línea, la fuente de verdad es el propio código (`apps/api/edecan_api/routers/remote.py`, extensamente documentado en su docstring de módulo) y `apps/api/tests/test_remote_router.py`. Este apartado es solo el resumen operativo.

### Endpoints (`/v1/remote`, Bearer + flag `companion.remote_view`; `input` exige además `companion.remote_input`)

| Ruta | Qué hace | Casos de error propios |
|---|---|---|
| `POST /sessions` | Crea una sesión `pending`. Exige `consent: true` y companion conectado. `kind` (fase v4, default `"view"`) — `"control"` exige además el flag `companion.remote_input`. | `422` sin consentimiento explícito o `kind` inválido · `403` `kind="control"` sin el flag · `503` sin companion conectado |
| `GET /sessions` | Lista las sesiones del tenant (todas, cualquier estado), más recientes primero. | — |
| `GET /sessions/{id}` | Detalle de una sesión. | `404` si no existe o es de otro tenant |
| `GET /sessions/{id}/frame` | Pide un frame nuevo al companion (`send_command(tenant_id, "screenshot", {})`). El primer frame exitoso pasa la sesión a `active` y fija `started_at`. | `404` sesión inexistente · `403` sesión `denied` (o el companion acaba de denegarla) · `409` sesión ya `ended` · `429` pediste un frame antes de `REMOTE_FRAME_MIN_INTERVAL_SECONDS` · `501` el companion no soporta `screenshot` todavía · `502` el companion contestó algo inesperado · `503` companion no conectado o no respondió a tiempo |
| `POST /sessions/{id}/input` (fase v4) | Reenvía `{tipo: "pointer"\|"key", ...}` como `input_pointer`/`input_key` al companion — solo sesiones `kind="control"` ya `active`. Ver §7bis para el detalle de los 4 candados. | `404` sesión inexistente · `403` no es `kind="control"`, o el companion/sesión ya `denied` · `409` todavía no `active`, o ya `ended` · `422` payload inválido (`accion`/`tecla` fuera de vocabulario, o `texto`+`tecla` juntos/ninguno) · `429` `REMOTE_INPUT_MIN_INTERVAL_SECONDS` · `501` companion sin soporte/deshabilitado/plataforma sin soporte · `502` otra falla del companion · `503` companion no conectado o sin respuesta |
| `POST /sessions/{id}/end` | Termina la sesión (idempotente). | `404` sesión inexistente |

### Contrato de degradación con el companion

fase v2 aterrizó `screenshot` en `ACTIONS` (`apps/companion/edecan_companion/actions.py`) mientras este WP estaba en curso, así que un companion actualizado, con `ide_enabled: true` (el default) y el permiso de macOS "Grabación de pantalla" ya concedido, sirve capturas reales de punta a punta. El contrato de degradación sigue haciendo falta para los otros dos casos, reales ambos: un companion desactualizado (de antes de esa actualización, no reconoce la acción en absoluto) o uno con `ide_enabled: false` en su `companion.yaml` (`screenshot` vive en `_IDE_ACTIONS`, así que hereda ese gate). `routers/remote.py` reconoce los mensajes exactos de `actions.execute` para ambos casos y responde `501` con una explicación distinta para cada uno, en vez de un `502` genérico.

### Almacenamiento

Las sesiones persisten en la tabla `remote_sessions` real (§7.4 `docs/roadmap.md`, migración `0003_v2_expansion`, fase v2, ya aterrizada), vía `Repo.create_remote_session`/`list_remote_sessions`/`get_remote_session`/`record_remote_session_frame`/`mark_remote_session_denied`/`mark_remote_session_ended` (`edecan_api/repo.py`). Una fila sobrevive un reinicio del proceso API y es visible desde cualquier *worker* `uvicorn` que consulte Postgres. Lo que SÍ sigue siendo estado en memoria, por proceso — mismo *trade-off* que siempre, sin *fix* posible sin un backend compartido tipo Redis pub/sub — es el mapa de sockets conectados de `ConnectionManager` (`companion_manager.py`): es una conexión WebSocket viva, no una fila de tabla, así que `GET .../frame` solo funciona en el *worker* que tiene ese socket abierto.

### *Rate limit* de frames (y de input, fase v4)

Redis (`deps.get_redis`, mismo patrón que `deps.rate_limit`), una marca de tiempo por sesión (`remote:frame:last:{tenant_id}:{session_id}`), comparada contra `getattr(settings, "REMOTE_FRAME_MIN_INTERVAL_SECONDS", 1.0)` — nunca revienta si el campo todavía no existe en `Settings` (lo agrega fase v2 junto con el resto de `.env.example` de §7.5). Es un límite **por sesión**, separado del límite general de 60 solicitudes/min por tenant que ya aplica a toda la API (`deps.rate_limit`, aplicado a nivel de router). `POST .../input` (fase v4) usa el mismo mecanismo con su propia clave (`remote:input:last:{tenant_id}:{session_id}`) y su propio umbral, `getattr(settings, "REMOTE_INPUT_MIN_INTERVAL_SECONDS", 0.05)` — mucho más laxo que el de frames (50ms vs 1s) porque un clic/tecla es una acción puntual, no un *polling* continuo.

### Limitaciones conocidas del prototipo (explícitas, no implícitas)

| Limitación | Por qué existe | Qué la cierra |
|---|---|---|
| El backend ve los bytes del frame en tránsito | Es *polling* HTTP liso, no WebRTC | Transporte objetivo, §5 |
| Sin verificación de *fingerprint* en el emparejamiento | El pairing de hoy (`pair-code` de 8 caracteres) es el de v1, pensado para acciones ya aprobadas localmente una por una, no para control remoto | Emparejamiento con QR + *fingerprint*, §4 |
| El mapa de companions conectados es un solo *worker* `uvicorn` (estado en memoria, `ConnectionManager`) — `remote_sessions` en sí ya no tiene este límite, persiste en Postgres | Es un socket WebSocket vivo, no una fila de tabla | Backend compartido (p. ej. Redis pub/sub) para ese mapa si el despliegue usa varios *workers* — fuera de alcance de este documento |
| Sin indicador visible permanente en el equipo controlado | El companion es una CLI, no una app con interfaz gráfica | §8 |
| Sin botón de pánico | El protocolo companion→backend de hoy es solo petición/respuesta | §8 (gap de protocolo documentado) |
| Input remoto es `POST` por comando, no un canal continuo — cada clic/tecla es una request HTTP completa contra `apps/api`, no un DataChannel de baja latencia | Se construyó sobre el *polling* HTTP existente (fase v4), no se esperó a WebRTC | Transporte WebRTC, §5/§7bis |
| Sin nivel "ver+puntero" (el escalón intermedio puramente informativo de §6) | Se saltó directo de "ver" a "control total" real al construir input remoto (fase v4) | §6 (si algún día se prioriza) |

## 11. De P1 a "nivel TeamViewer" — checklist de lo que falta

En orden aproximado de qué desbloquea qué (cada fila típicamente depende de que la anterior ya exista):

1. ~~Migración `0003_v2_expansion`~~ (fase v2) — **hecho**: `remote_sessions` ya persiste de verdad (`Repo.create_remote_session/...`, `routers/remote.py`), deja de depender de estado en memoria de un solo proceso. La tabla `devices` existe pero sigue sin ningún código que la use — la puebla el punto 3 (emparejamiento) cuando aterrice.
2. ~~Acción `screenshot` del companion~~ (fase v2) — **hecho**: frames reales en vez de `501` constante.
3. **Emparejamiento con QR + código de 6 dígitos + verificación de *fingerprint*** (§4 de este documento): sube el estándar de confianza del pairing específicamente para control remoto — sigue pendiente, incluso para las sesiones `kind="control"` de input real (punto 8, ya hecho) que se construyeron sobre el `pair-code` simple de siempre.
4. **Companion con presencia gráfica** (ícono de bandeja/menú, §8): habilita tanto el indicador visible permanente como, más adelante, un botón de pánico utilizable de verdad. Más urgente ahora que existe input real (fase v4): un ícono de bandeja "te están controlando" importa más cuando alguien puede de verdad mover tu mouse, no solo verlo.
5. **Protocolo companion→backend con mensajes espontáneos** (§8): requisito técnico del botón de pánico.
6. **Transporte WebRTC** (§5): ScreenCaptureKit + H.264 + DTLS-SRTP + TURN propio — el cambio más grande de todos, saca al backend del camino de los datos (incluidos los eventos de input, que hoy siguen viajando por `POST` HTTP, ver §7bis "Qué NO cambió").
7. **Niveles de permiso "ver+puntero"** (§6): el escalón puramente informativo entre "ver" y "control total" — se saltó al construir input real directo (punto 8), sigue sin existir si algún día se prioriza.
8. ~~Input injection ("control total")~~ (§7, §7bis) — **hecho (fase v4)**, sobre `POST` HTTP en vez de WebRTC: `CGEvent` tras Accesibilidad ✅, aprobación local por comando acotada a la sesión ✅, auditoría **por comando** (más granular que la "agregada" que proponía el diseño original) ✅. Lo que sigue pendiente de este punto: el DataChannel de eventos de baja latencia (depende del punto 6) y la auditoría agregada/searchable tipo "40 clics entre las 10:03 y las 10:07" (hoy es una fila de `audit_log` por comando, ya suficientemente searchable vía SQL normal, así que puede que ni haga falta agregarla aparte).
9. **Visor móvil nativo** (§9, `docs/roadmap.md`): iOS/Android consumiendo el mismo WebRTC del punto 6, sin lógica de control remoto propia.

## 12. Flujo paso a paso

### 12.1 Prototipo P1 (hoy) — sesión de vista completa

1. El usuario abre `/app/remoto`. Si su plan no tiene `companion.remote_view`, ve un aviso claro y nada más (`RemotoPage`, `apps/web/src/app/(app)/app/remoto/page.tsx`) — no se intenta ninguna llamada que vaya a devolver `403`.
2. Con el flag activo y sin sesión en curso, ve `ConsentGate`: una explicación del doble consentimiento y un checkbox obligatorio antes de habilitar "Iniciar sesión de vista remota".
3. Al confirmar, el panel llama `POST /v1/remote/sessions {"consent": true}`. El backend valida el flag, valida que haya un companion conectado (`ConnectionManager.is_connected`) y crea la fila `pending` — responde `201` con la sesión.
4. El panel, de inmediato, pide el primer frame: `GET /v1/remote/sessions/{id}/frame`. Esto dispara `ConnectionManager.send_command(tenant_id, "screenshot", {})`, que le manda `{request_id, action: "screenshot", params: {}}` al companion por el WebSocket ya emparejado.
5. **Aquí es donde ocurre la segunda aprobación.** El companion (hoy, en su terminal; en el diseño objetivo, un prompt de su interfaz gráfica — §8) pregunta "¿Permitir que vean tu pantalla?". Si la persona dice que no, el companion contesta `{"ok": false, "error": "acción rechazada (sin aprobación del usuario)"}`; el backend mueve la sesión a `denied`, audita `remote.session.denied` y responde `403` — el panel muestra ese mensaje y no vuelve a intentarlo automáticamente para esa sesión.
6. Si la persona aprueba (y, hoy, si además el companion ya tiene la acción `screenshot` implementada — mientras no la tenga, ver el contrato de degradación de §10), el companion captura la pantalla y contesta `{"ok": true, "result": {"image_b64", "width", "height"}}`. El backend marca la sesión `active`, fija `started_at`, audita `remote.session.started` (solo la primera vez) e incrementa `frames_count`.
7. El panel muestra el frame en un `<img>` con `data:image/png;base64,...`, y ofrece "Actualizar" (un frame más), "Auto" (un frame cada 2 segundos, por encima del límite mínimo de 1 segundo del servidor así que no debería toparse con `429` en uso normal) y "Terminar sesión".
8. En cualquier momento — por el botón, cerrando la pestaña sin terminarla explícitamente (queda `active` hasta que alguien la cierre o hasta una limpieza futura, ver §13), o porque el companion se desconectó — la sesión puede terminar. `POST /v1/remote/sessions/{id}/end` la marca `ended`, calcula la duración si hubo al menos un frame, y audita `remote.session.ended`.

### 12.1bis Fase 2 (fase v4) — sesión de control completa

Mismos pasos 1-6 de 12.1, con estas diferencias:

1. En `ConsentGate` el usuario marca el checkbox "Además, habilitar control remoto de teclado y mouse" — solo visible si el plan trae `companion.remote_input` (si no, ni siquiera se ofrece la opción).
2. `POST /v1/remote/sessions {"consent": true, "kind": "control"}` — el backend valida `companion.remote_input` ADEMÁS de `companion.remote_view` (403 si falta), y la fila nace `kind="control"` (`create_remote_session` sigue insertando `"view"`; `Repo.mark_remote_session_kind` la promueve acto seguido).
3. Igual que 12.1 paso 4-6: el primer `GET .../frame` dispara la segunda aprobación (esta vez para VER la pantalla) y activa la sesión.
4. El panel muestra el banner ROJO "Sesión de control activa — se está controlando tu equipo" (en vez del ámbar de solo-vista) con un botón grande "Terminar sesión", y habilita clics sobre el frame + el panel de teclado (`RemoteControlPanel`).
5. Cada clic/tecla dispara `POST /v1/remote/sessions/{id}/input {tipo, ...}`. El backend valida sesión `active` + `kind="control"`, reenvía `input_pointer`/`input_key` al companion vía el mismo `ConnectionManager.send_command`.
6. **Aquí ocurre una TERCERA aprobación, por comando** (distinta de la del paso 3, que solo cubrió "ver"): el companion pregunta "¿Permitir CONTROL REMOTO «input_pointer»/«input_key» con {...}?" — si dice que sí y `remote_input_remember_minutes > 0`, esa aprobación se recuerda SOLO para esta sesión y solo por esos minutos; si dice que no, la SESIÓN entera pasa a `denied` (no solo ese comando) y el panel deja de poder mandar más input o pedir más frames.
7. Terminar la sesión (`POST .../end`) funciona exactamente igual que en 12.1 — no hay ningún paso adicional de "soltar el control" separado de terminar la sesión.

### 12.2 Diseño objetivo — emparejamiento + sesión WebRTC

1. **Emparejamiento** (una vez por equipo, no por sesión): el companion muestra QR + código de 6 dígitos (§4). El panel los lee/teclea, ambas puntas intercambian claves, ambas pantallas muestran el mismo *fingerprint* derivado — la persona confirma visualmente que coinciden en las dos. Se crea la fila en `devices`.
2. **Inicio de sesión**: igual que en 12.1 pasos 1-3 (consentimiento en el panel, `POST /sessions`), pero la sesión referencia el `device_id` verificado en vez de "cualquier companion conectado de este tenant".
3. **Señalización**: el panel crea un `RTCPeerConnection`, genera una oferta SDP y la manda al backend; el backend la reenvía al companion por el mismo canal de señalización (mismo rol que `send_command` hoy, pero para SDP/ICE en vez de para pedir un frame suelto). El companion contesta con su propia respuesta SDP y sus candidatos ICE; el backend los reenvía de vuelta. El backend nunca interpreta el contenido de esos mensajes, solo los repite entre las dos puntas correctas.
4. **Segunda aprobación local**: exactamente el mismo prompt del paso 5 de 12.1, pero disparado por la oferta de conexión WebRTC entrante en vez de por un comando `screenshot` suelto.
5. **Conexión directa (o vía TURN)**: una vez negociada, la conexión DTLS-SRTP se establece directo entre panel y companion (o a través de coturn si el NAT lo exige) — el backend queda fuera del camino de los datos a partir de este punto.
6. **Video en vivo**: el companion transmite el stream H.264 capturado con ScreenCaptureKit; el panel lo renderiza en un `<video>` en tiempo real, sin el *polling* de frames sueltos del prototipo P1.
7. **Fin de sesión**: cualquiera de las dos puntas puede cerrar el `RTCPeerConnection` localmente; el backend se entera por el canal de señalización (o por un `POST /end` explícito) y actualiza `remote_sessions` igual que hoy.

## 13. Modos de falla

Ninguno de estos es hipotético — son las preguntas que cualquier revisor de este diseño va a hacer primero, así que quedan contestadas aquí en vez de descubiertas en producción.

| Falla | Qué pasa hoy (P1) | Qué debería pasar en el diseño objetivo |
|---|---|---|
| El companion se desconecta a mitad de sesión (Wi-Fi, equipo se duerme, se cierra el proceso) | El siguiente `GET .../frame` ve `ConnectionManager.is_connected() == False` y responde `503` de inmediato; el panel deja de hacer *auto-refresh* (`setAuto(false)`) pero la sesión sigue técnicamente `active` en `remote_sessions` hasta que alguien la termine explícitamente | El `RTCPeerConnection` detecta la pérdida de conexión (evento `iceconnectionstatechange` → `disconnected`/`failed`) y ambas puntas la cierran solas, sin esperar una acción manual — el backend se entera por el canal de señalización y marca la sesión `ended` con una nota de "corte de conexión" en vez de "terminada por el usuario" |
| El usuario cierra la pestaña del panel sin pulsar "Terminar sesión" | La sesión queda `active` indefinidamente en `remote_sessions` (no hay *timeout* automático todavía) — el companion, si sigue emparejado, seguiría respondiendo a pedidos de frame de esa sesión si alguien retoma la pestaña más tarde | Con WebRTC, cerrar la pestaña cierra el `RTCPeerConnection` del lado del navegador automáticamente (comportamiento estándar del navegador al descargar la página), lo que dispara el mismo camino de "conexión perdida" de la fila de arriba — no depende de que el usuario recuerde pulsar un botón |
| Alguien pide frames mucho más rápido que `REMOTE_FRAME_MIN_INTERVAL_SECONDS` (bug de cliente, o abuso deliberado) | `429` inmediato desde Redis, sin llegar a molestar al companion — protege tanto el ancho de banda como la sensación de "vigilancia constante" de la persona frente al equipo | Con video en vivo no aplica un límite de "frames por segundo" del mismo tipo — el control equivalente pasa a ser la propia negociación de *bitrate* de WebRTC |
| El proceso de `apps/api` se reinicia (deploy, crash) mientras hay sesiones activas | Las filas de `remote_sessions` sobreviven al reinicio (Postgres, ya no memoria) — pero el mapa de companions conectados (`ConnectionManager`) sí se pierde, así que ninguna sesión que quedó `active` puede pedir más frames hasta que su companion se reempareje; nada la cierra proactivamente, sigue apareciendo `active` sin un `remote.session.ended` en el `audit_log` | Sigue haciendo falta un job de limpieza que cierre (con motivo "servidor reiniciado") cualquier sesión que quedó `active` sin una fila de conexión viva tras un arranque — no está resuelto ni en el diseño de este documento, queda como trabajo futuro explícito |
| El usuario revoca el flag de plan (baja de plan) con una sesión activa | El flag se recalcula en cada request desde `PLANES[plan_key]` (`ARCHITECTURE.md` §10.12) — la PRÓXIMA petición a cualquier ruta de `/v1/remote/*` devuelve `403` de inmediato, incluida `GET .../frame`, así que una sesión activa deja de poder pedir más frames en cuanto se procesa el cambio de plan, aunque no hay un mecanismo que la cierre proactivamente en ese instante | Igual, más un evento explícito de "tu plan ya no incluye esto" empujado al panel en vivo (fuera del alcance de este documento) |
| El usuario deniega UN comando de input (fase v4) — p. ej. un clic específico que no reconoce | La SESIÓN completa pasa a `denied` (no solo ese comando) — mismo criterio deliberadamente conservador que ya usaba la vista para una captura denegada; el panel deja de poder mandar más input O pedir más frames para esa sesión, tiene que iniciar una nueva | Igual — este criterio "todo o nada" no depende del transporte, seguiría aplicando con WebRTC |
| El companion tiene `remote_input_enabled: true` pero perdió el permiso de Accesibilidad de macOS (revocado a mano en Ajustes del Sistema después de concederlo) | `_QuartzInputBackend.__init__` lo detecta en cada intento (`AXIsProcessTrusted()`, nunca cacheado) y falla con `ActionError` claro; el router lo traduce a `502` (no `501`: no es "deshabilitado", es un permiso del sistema operativo que cambió) | Igual — este gate es del sistema operativo, no del transporte |

## Referencias cruzadas

- [`../ARCHITECTURE.md`](../ARCHITECTURE.md) §0 (regla dura de control remoto), §10.7 (`ToolContext.extras["companion"]`), §10.12 (`/v1/companion/*`).
- [`roadmap.md`](./roadmap.md) — estado público de los clientes móviles y del control remoto.
- [`../ARCHITECTURE.md`](../ARCHITECTURE.md) §13 (responsable de la fase v4, flag `companion.remote_input`) — fase v4 (control remoto fase 2, §7bis de este documento) es quien lo consume.
- [`seguridad-modelo-amenazas.md`](./seguridad-modelo-amenazas.md) — modelo de amenazas general de la plataforma; este documento es su extensión específica para control remoto.
- [`cumplimiento/privacidad.md`](./cumplimiento/privacidad.md) — retención de datos y derechos de los titulares, aplica también a metadatos de sesiones de control remoto.
- `apps/api/edecan_api/companion_manager.py` — `ConnectionManager`, el canal companion↔backend que este documento extiende (§8) y que el transporte WebRTC (§5) complementa con señalización.
- `apps/api/edecan_api/routers/remote.py` — implementación real de la vista (§10) y del input remoto (§7bis) — docstring de módulo con el mismo nivel de detalle que este documento, orientado a quien lea el código en vez de esta página.
- `apps/api/tests/test_remote_router.py` — cobertura de test de cada caso de error de §10 y §7bis.
- `apps/companion/edecan_companion/{actions,approval,config}.py` + `apps/companion/tests/{test_actions_input,test_approval,test_config}.py` — implementación y tests del lado companion de §7bis.
