# Casa inteligente (Home Assistant)

Edecán controla tu casa inteligente a través de **Home Assistant** — la plataforma open-source de casa inteligente self-hosted más usada, con una **API REST oficial y documentada** (`ARCHITECTURE.md` §12, §12.b; `ROADMAP_V2.md` §6.3; WP-V3-12). Como con el resto de conectores (ver [`conectores.md`](./conectores.md)), es **bring-your-own** al pie de la letra: conectas TU PROPIA instancia de Home Assistant (self-hosted en tu casa, en un Raspberry Pi, un NAS, una VM...) con un token que generas tú mismo — Edecán nunca opera una instancia compartida ni guarda una credencial de plataforma.

## Por qué un solo conector alcanza para "toda" la casa

Home Assistant ya integra **miles** de dispositivos y servicios de terceros (Philips Hue, TP-Link Kasa, Google Nest, Sonos, cámaras ONVIF, TVs, robots aspiradora, sensores Zigbee/Z-Wave...) bajo un modelo de datos único: cada dispositivo se expone como una o más **entidades** (`entity_id` con la forma `dominio.nombre`, p. ej. `light.sala`, `climate.termostato`, `lock.puerta_principal`). Conectando Edecán a tu Home Assistant una sola vez, el agente puede listar, consultar y controlar **cualquier cosa que ya tengas integrada ahí** — luces, enchufes, clima, sensores, persianas, TVs, cámaras (su estado, no el streaming de video) — sin que Edecán tenga que integrar cada fabricante por separado. Matter (el estándar unificado de casa inteligente) queda para una fase posterior; Home Assistant ya lo soporta como puente, así que en la práctica ya llega indirectamente.

## Generar el Long-Lived Access Token en Home Assistant

1. Abre tu Home Assistant en el navegador y entra a tu **perfil** (clic en tu nombre de usuario, abajo a la izquierda del menú lateral).
2. Baja hasta la pestaña/sección **Seguridad**.
3. En **Long-Lived Access Tokens**, clic en **Crear token**.
4. Ponle un nombre reconocible (p. ej. "Edecán") y confirma.
5. Home Assistant te muestra el token **una sola vez** — cópialo ahora. Si lo pierdes, tendrás que crear uno nuevo (y revocar el anterior desde la misma pantalla).

Este token actúa como tu usuario dentro de la API REST de Home Assistant: cualquier acción que Edecán ejecute con él queda sujeta a los mismos permisos que tenga tu usuario en Home Assistant.

## Conectarlo en Edecán

En el panel de Edecán, ve a **Configuración → Casa inteligente** y pega:

- **URL de tu Home Assistant** (`base_url`): p. ej. `http://homeassistant.local:8123` o la IP local `http://192.168.1.50:8123`. También puede ser `https://` si tienes un certificado configurado (Home Assistant Cloud/Nabu Casa, un proxy inverso propio, etc.).
- **Long-Lived Access Token** (el que generaste arriba).

Internamente esto llama a `PUT /v1/smarthome/credentials {base_url, token}` (`apps/api/edecan_api/routers/smarthome.py`). Por defecto (`validate: true`) Edecán hace una comprobación real —`GET {base_url}/api/` con tu token— **antes de guardar nada**: si Home Assistant no responde o rechaza el token, la pantalla te muestra el error exacto (mismo principio de "pegar y validar" que el resto de credenciales, ver `DIRECCION_ACTUAL.md`). Solo si la comprobación pasa se guarda, cifrado en tu `TokenVault` (`ARCHITECTURE.md` §10.4) — nunca en texto plano, nunca en logs.

`GET /v1/smarthome/status` te deja consultar en cualquier momento si está configurado y si Home Assistant responde ahora mismo (`{"configured", "base_url", "reachable"}` — `reachable` queda en `null`, nunca en error, si la red falla al comprobar). `DELETE /v1/smarthome/credentials` desconecta.

### Nota de red: dónde debe correr Edecán para alcanzar tu Home Assistant

Home Assistant normalmente vive en tu red local (LAN) — no está pensado para exponerse directo a internet salvo que tú mismo configures un proxy inverso o Home Assistant Cloud. Esto significa:

- **App de escritorio de Edecán** (el vehículo principal del producto, `DIRECCION_ACTUAL.md` — corre en tu propia Mac/PC): encaja natural, porque corre en la MISMA LAN que tu Home Assistant. `http://homeassistant.local:8123` o la IP local funcionan tal cual.
- **Edecán hospedado en la nube** (opción secundaria): solo puede alcanzar tu Home Assistant si tú lo expones de alguna forma alcanzable desde internet (Home Assistant Cloud/Nabu Casa, un túnel propio, un proxy inverso con HTTPS) — la URL que pegues en `base_url` debe ser una que ese Edecán en la nube pueda resolver y alcanzar. Sin eso, `GET /v1/smarthome/status` reportará `"reachable": false` o `null`.

### Por qué la validación de la URL es "al revés" que la del navegador web

`edecan_browser` (la tool que navega páginas públicas) bloquea por seguridad cualquier URL que apunte a una IP privada, `localhost` o metadata de nube (protección SSRF, ver [`navegador.md`](./navegador.md)) — tiene sentido ahí porque esa tool sigue enlaces arbitrarios que te da un sitio web o el propio modelo. Para Home Assistant es **exactamente lo contrario**: una IP privada (`192.168.x.x`, `10.x.x.x`) o un hostname `.local` (mDNS) es **el caso normal y esperado**, porque tu Home Assistant vive en tu LAN por diseño. `edecan_smarthome.client`/`apps/api/edecan_api/routers/smarthome.py` solo rechazan lo que sí es inválido en cualquier caso: un esquema que no sea `http`/`https`, o credenciales pegadas dentro de la URL (`usuario:contraseña@host` — el token siempre va aparte, nunca ahí).

## Qué puede hacer el agente

`edecan_smarthome` (`packages/smarthome/`) expone 3 herramientas al agente, sin flag de plan adicional (disponibles en cualquier plan; lo que sí determina si funcionan es si TÚ conectaste tu Home Assistant):

| Tool | `dangerous` | Argumentos | Qué hace |
|---|---|---|---|
| `casa_dispositivos` | no | `dominio` (opcional: `'light'`, `'switch'`, `'climate'`, `'sensor'`, `'lock'`...) | Lista tus dispositivos con nombre y estado actual. Solo lectura, hasta 200 entidades. |
| `casa_estado` | no | `entity_id` | Estado y atributos (brillo, temperatura, batería...) de UN dispositivo. Solo lectura. |
| `casa_controlar` | **sí** | `entity_id`, `accion` (`'encender'`\|`'apagar'`\|`'alternar'`\|`'dominio.servicio'` explícito), `parametros` (opcional) | Ejecuta una acción real: enciende/apaga/alterna, o cualquier servicio de Home Assistant más específico (p. ej. `'cover.open_cover'` para subir una persiana, `'climate.set_temperature'` con `parametros: {"temperature": 22}`). |

`casa_controlar` es una acción física en tu hogar, así que está marcada `dangerous=True`: el mismo gate de confirmación humana que usa el resto de herramientas peligrosas del agente (`enviar_correo`, `preparar_pago`, `llamar_contacto`...) — el turno se detiene y te pide confirmar explícitamente antes de ejecutarla (`ARCHITECTURE.md` §10.7, `Agent.run_turn`).

Si todavía no conectaste tu Home Assistant, cualquiera de las 3 tools responde con un mensaje pidiéndote que lo conectes en **Configuración → Casa inteligente**, en vez de fallar con un error críptico.

### Acciones mapeadas vs. explícitas

`accion` acepta tres verbos comunes en español (`encender`, `apagar`, `alternar`), que se traducen a los servicios genéricos de Home Assistant `homeassistant.turn_on`/`turn_off`/`toggle` — funcionan sobre casi cualquier dominio (luces, enchufes, ventiladores...). Para acciones que esos tres verbos no cubren, `accion` acepta un `'dominio.servicio'` explícito de Home Assistant tal cual (los mismos que verías en **Herramientas para desarrolladores → Acciones** dentro de Home Assistant), con `parametros` como los datos adicionales del servicio.

## Política de cerraduras: bloqueadas, siempre

**Edecán nunca ejecuta ninguna acción sobre el dominio `lock` desde el chat, aunque se lo pidas explícitamente.** Esto incluye no solo `lock.unlock`/`lock.open` (desbloquear/abrir) sino el dominio `lock` completo — incluso `lock.lock` (bloquear, en sí inofensivo) queda fuera, y también los verbos mapeados (`encender`/`apagar`/`alternar`) cuando apuntan a una entidad `lock.*`, porque Home Assistant los traduce internamente a acciones reales sobre la cerradura (por ejemplo, `apagar` sobre una cerradura se traduce a `lock.unlock`).

Se bloquea el dominio entero, no una lista de "servicios peligrosos", a propósito: es la postura más simple y más robusta — no hay que mantener en código una tabla de qué combinación de verbo mapeado + dominio de entidad es "segura" dentro de Home Assistant, ni arriesgarse a que un cambio futuro en esa traducción interna abra un hueco. Si quieres bloquear o desbloquear una puerta, hazlo tú mismo — en persona o desde la app oficial de Home Assistant. La implementación vive en `edecan_smarthome.tools.DOMINIOS_BLOQUEADOS` (`frozenset({"lock"})`) y está cubierta por tests que verifican que ninguna combinación de entrada logra sortear el bloqueo (`packages/smarthome/tests/test_tools.py`).

Esta es la misma familia de guardrails permanentes del producto que el resto de acciones sensibles: dinero real nunca se mueve solo ([`dinero-real.md`](./dinero-real.md)), control remoto exige emparejamiento explícito ([`control-remoto.md`](./control-remoto.md)) — controlar el acceso físico a tu hogar por voz/chat es exactamente ese mismo tipo de riesgo, y la respuesta es la misma: está deshabilitado, no solo "gateado".

## Qué NO hace (todavía)

- **Streaming de cámaras**: `casa_dispositivos`/`casa_estado` muestran el *estado* de una entidad `camera.*` (p. ej. si está grabando, su último snapshot como atributo si Home Assistant lo expone), pero Edecán no reproduce video en vivo — eso queda para una integración de video dedicada.
- **Matter directo**: no hay un conector Matter propio; los dispositivos Matter que ya tengas puenteados dentro de Home Assistant sí son alcanzables (como cualquier otra entidad), pero un vínculo Matter nativo (sin pasar por Home Assistant) no está en el alcance de este work package.

## Referencia técnica

- Cliente REST puro: `packages/smarthome/edecan_smarthome/client.py` (`HomeAssistantClient`).
- Herramientas del agente: `packages/smarthome/edecan_smarthome/tools.py`.
- Router HTTP: `apps/api/edecan_api/routers/smarthome.py` (`PUT`/`DELETE /v1/smarthome/credentials`, `GET /v1/smarthome/status`).
- Vault: `connector_key = "homeassistant"`, singleton por tenant, `TokenBundle.access_token` = el Long-Lived Access Token tal cual (sin envolver en JSON), `TokenBundle.scopes[0]` = tu `base_url` (`ARCHITECTURE.md` §12.b).
- Timeout configurable por `HOMEASSISTANT_TIMEOUT_SECONDS` (`.env`, default 15s).

Ver también: [`conectores.md`](./conectores.md) para el resto de integraciones, y [`configuracion.md`](./configuracion.md) para la pantalla de Configuración en general.
