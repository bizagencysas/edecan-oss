# Vehículos (Smartcar)

Edecán se conecta a tus vehículos a través de **Smartcar** (https://smartcar.com) — un agregador con **API OAuth oficial y documentada** que cubre decenas de marcas (Tesla, GM/Chevrolet, Ford, Toyota, BMW, Hyundai/Kia, Nissan, Volvo, Jaguar/Land Rover y más) con una única integración (`ARCHITECTURE.md` §13). Como con el resto de conectores (ver [`conectores.md`](./conectores.md)), es **bring-your-own** al pie de la letra: creas TU PROPIA app en el dashboard de Smartcar (gratis) y autorizas TU PROPIO vehículo — Edecán nunca opera una app de Smartcar compartida ni guarda una credencial de plataforma.

## Por qué Smartcar y no el API propietario de cada fabricante

Cada fabricante (Tesla, Ford, GM...) expone su propio API, con su propio flujo de autenticación y su propia forma de nombrar batería/combustible/ubicación. Smartcar normaliza todo eso detrás de un único API REST con OAuth estándar: Edecán integra Smartcar UNA vez y automáticamente puede leer/controlar cualquier vehículo compatible, sin importar la marca. El costo: solo funciona con vehículos que Smartcar soporta (la lista crece constantemente, ver https://smartcar.com/compatible-vehicles) y algunas capacidades (batería, ubicación...) dependen de lo que cada fabricante decida exponer — de ahí que `vehiculo_estado` trate cada campo como opcional.

## 1. Crea tu app en el dashboard de Smartcar

1. Entra a https://dashboard.smartcar.com y crea una cuenta (gratis).
2. Crea una nueva "aplicación". Smartcar te da un **Client ID** y un **Client Secret** — cópialos, los necesitas más abajo.
3. En **Redirect URIs**, agrega cualquier URI de tu elección (p. ej. `https://smartcar.com/test`, que Smartcar ofrece como página de redirección de prueba lista para usar) — solo hace falta para completar el flujo Connect del paso 2, Edecán no corre un servidor OAuth propio para esto (ver "Mejora futura" más abajo).
4. Activa el **modo de prueba (test mode)** de tu app: Smartcar simula vehículos falsos (puedes elegir marca/modelo) sin necesitar un auto real ni una cuenta de fabricante — perfecto para probar Edecán de punta a punta antes de conectar un vehículo de verdad. Cuando quieras vehículos reales, tu app pasa a modo "live" (Smartcar puede pedir verificación adicional para eso, ver su propia documentación).

## 2. Obtén tu `refresh_token` inicial (flujo Connect)

Smartcar autoriza el acceso a un vehículo con un flujo OAuth estándar ("Smartcar Connect"): el usuario final (tú, el dueño del vehículo) inicia sesión con la cuenta de su fabricante (o, en modo de prueba, elige un vehículo simulado) y autoriza tu app. Al final del flujo, Smartcar te da un `refresh_token` — la credencial de larga duración que Edecán usa de ahí en adelante para pedir `access_token`s nuevos sin que tengas que repetir el login.

Hoy este paso se hace **a mano**, con la propia herramienta de autenticación de Smartcar:

1. Instala el CLI de Smartcar (`npm install -g @smartcar/cli`) o usa su [Postman collection](https://smartcar.com/docs/api-reference) / el explorador interactivo de su documentación — cualquiera de las tres construye la URL de autorización de Smartcar Connect por ti (necesita tu `client_id`, la(s) `scope`(s) que quieras — p. ej. `read_vehicle_info read_battery read_fuel read_odometer read_location control_security` — y el `redirect_uri` que registraste en el paso 1).
2. Abre esa URL, inicia sesión (o elige un vehículo simulado en modo de prueba) y autoriza tu app.
3. Smartcar te redirige con un `code` en la URL — canjéalo por un `refresh_token` con `POST https://auth.smartcar.com/oauth/token` (`grant_type=authorization_code`), Basic auth con tu `client_id`/`client_secret` — el CLI/Postman collection lo hacen por ti y te muestran el `refresh_token` directo.
4. Copia ese `refresh_token` — lo pegas en Edecán en el paso 3.

**Mejora futura**: un flujo "Conectar con Smartcar" dentro de la propia app de Edecán (Configuración → Vehículos → un botón que abre Smartcar Connect y captura el `code` automáticamente, como ya existe para Google/Microsoft/Meta en [`conectores.md`](./conectores.md)) eliminaría este paso manual. Hoy no está implementado — el camino manual de arriba es la vía real y funcional mientras tanto.

## 3. Conectarlo en Edecán

En el panel de Edecán, ve a **Configuración → Vehículos** y pega:

- **Client ID** y **Client Secret** de tu app de Smartcar (paso 1).
- **Refresh token** que obtuviste en el paso 2.

Internamente esto llama a `PUT /v1/vehiculos/credentials {client_id, client_secret, refresh_token}` (`apps/api/edecan_api/routers/vehiculos.py`). Por defecto (`validate: true`) Edecán hace una comprobación real —refresca el token contra Smartcar y confirma con un `GET /vehicles`— **antes de guardar nada**: si Smartcar rechaza cualquier parte, la pantalla te muestra el error exacto (mismo principio de "pegar y validar" de [`credenciales.md`](./credenciales.md)). Solo si la comprobación pasa se guarda, cifrado en tu `TokenVault` (`ARCHITECTURE.md` §10.4) — nunca en texto plano, nunca en logs.

`GET /v1/vehiculos/status` te deja consultar en cualquier momento si está configurado y si Smartcar responde ahora mismo (`{"configured", "reachable"}` — `reachable` queda en `null`, nunca en error, si la red falla al comprobar). `DELETE /v1/vehiculos/credentials` desconecta.

### Por qué Smartcar rota tu `refresh_token` — y por qué no tienes que hacer nada al respecto

Smartcar puede emitir un `refresh_token` **nuevo** cada vez que Edecán lo canjea por un `access_token` (los access tokens duran ~2 horas). Edecán lo maneja solo: cada vez que refresca el token —al conectar, al listar vehículos, al consultar un estado, al bloquear/desbloquear— si Smartcar devolvió un `refresh_token` distinto al que tenía guardado, Edecán lo persiste de vuelta en tu `TokenVault` automáticamente, ANTES de usarlo. No necesitas volver a pegar nada; si algún día ves que tu conexión dejó de funcionar (`reachable: false` en `GET /v1/vehiculos/status`), lo más probable es que hayas revocado el acceso desde el propio dashboard de Smartcar o desde la cuenta de tu fabricante — reconéctala repitiendo el paso 2.

## Qué puede hacer el agente (fuera de alcance por ahora)

`edecan_vehicles` (`packages/vehicles/`) tiene escrito el código de 2 herramientas de agente —
`vehiculo_estado` (solo lectura) y `vehiculo_controlar` (bloquear/desbloquear, `dangerous=True`,
requeriría el flag `tools.vehicles`) — pero **el dueño del proyecto decidió sacar vehículos del
alcance del producto** (`ARCHITECTURE.md` §13.e): no vale la pena el esfuerzo de mantener esta integración cuando la app oficial de cada
fabricante ya resuelve lo mismo mejor, y no es el foco del producto.

Por eso `apps/api/pyproject.toml` **a propósito** no declara `edecan-vehicles` como dependencia: el
descubrimiento de herramientas del agente (`edecan_core.ToolRegistry`, entry point `edecan.tools`)
solo encuentra paquetes de verdad instalados en el entorno donde corre la API, y en cualquier build
real del producto (imagen Docker de `apps/api`, la app de escritorio empaquetada) `edecan_vehicles`
no lo está. **El chat del agente no puede usar `vehiculo_estado` ni `vehiculo_controlar` en el
producto tal como se distribuye** — el código en `packages/vehicles/edecan_vehicles/tools.py` (incluido
su modo demo con `StubVehiclesProvider`) queda como base para un trabajo futuro si algún día se
revierte esta decisión, no como una función activa hoy. (En un checkout de desarrollo con `uv sync
--all-packages`, el paquete SÍ queda instalado por accidente de cómo funciona `uv` en modo dev — pero
no es el comportamiento soportado ni el que tendrá el producto real, no construyas nada asumiendo que
va a seguir así.)

Lo que SÍ sigue funcionando de verdad (secciones 1-3 arriba): conectar tus credenciales de Smartcar, y
los endpoints HTTP directos descritos en "Referencia técnica" abajo (`GET /v1/vehiculos`, `GET
/v1/vehiculos/{id}/estado`, `POST /v1/vehiculos/{id}/puertas`) — no pasan por el agente ni por
`edecan_vehicles`, son `httpx` puro dentro del propio router, así que si tu propio frontend/cliente los
llama directamente, funcionan igual que siempre.

## Disclaimer de seguridad: bloquear/desbloquear es una acción física

**Desbloquear un auto de forma remota es una acción física real, con implicaciones de seguridad reales** (acceso al vehículo, a lo que haya dentro). El endpoint `POST /v1/vehiculos/{id}/puertas` (la única forma real de hacerlo hoy — ver nota de alcance arriba sobre por qué esto no pasa por el chat del agente) espera que quien lo llama ya haya mostrado su propia confirmación explícita en la UI antes de la llamada, el mismo criterio que el resto de acciones sensibles del producto (dinero real, correos salientes, control remoto de tu computadora — ver [`dinero-real.md`](./dinero-real.md), [`control-remoto.md`](./control-remoto.md)). Si algo sale mal (Smartcar no responde, la acción falla), Edecán registra el intento en tu bitácora de auditoría (`audit_log`) de todos modos — tanto si la acción tuvo éxito como si falló — para que siempre quede constancia de qué se intentó hacer con tu vehículo y cuándo.

## Filosofía bring-your-own — sin excepción

Igual que Home Assistant ([`casa-inteligente.md`](./casa-inteligente.md)) o cualquier otro conector: Edecán nunca opera una app de Smartcar compartida entre clientes, nunca guarda una credencial de vehículos de "plataforma", y nunca hay un modo "vehículos incluidos" que dependa de que el dueño del proyecto pague o mantenga una integración de fabricante por su cuenta. Cada tenant trae su propia app de Smartcar (gratis de crear) y autoriza sus propios vehículos — el costo (si algún día Smartcar deja de ser gratis para tu volumen de uso) y la relación son enteramente tuyos con Smartcar, nunca con Edecán.

## Qué NO hace (todavía)

- **Arrancar el motor / climatización remota / bocina y luces**: Smartcar expone estas capacidades (`START`/`STOP` en algunos fabricantes, `climate`, `panic`...) pero este work package solo implementa lectura de estado + bloqueo/desbloqueo de puertas — el resto queda para una ampliación futura del mismo proveedor (`edecan_vehicles.providers.SmartcarProvider` ya tiene la estructura para agregarlas).
- **Flujo Connect in-app**: ver "Mejora futura" en la sección 2 — hoy el `refresh_token` inicial se obtiene con la propia herramienta de Smartcar, a mano.
- **Tesla Fleet API directo**: se evaluó como alternativa pero se descartó a favor de Smartcar por ser multi-marca — una integración Tesla-específica queda fuera de alcance mientras Smartcar cubra Tesla igual (lo hace).

## Referencia técnica

- Proveedor (usado por las tools del agente): `packages/vehicles/edecan_vehicles/providers.py` — `VehicleProvider` (protocolo), `StubVehiclesProvider` (demo), `SmartcarProvider` (real).
- Herramientas del agente (código presente, **fuera de alcance del producto, nunca registradas en un build real** — ver nota arriba): `packages/vehicles/edecan_vehicles/tools.py` (`vehiculo_estado`, `vehiculo_controlar`).
- Router HTTP: `apps/api/edecan_api/routers/vehiculos.py` — `PUT`/`DELETE /v1/vehiculos/credentials`, `GET /v1/vehiculos/status`, `GET /v1/vehiculos` (lista), `GET /v1/vehiculos/{id}/estado`, `POST /v1/vehiculos/{id}/puertas`.
- Vault: `connector_key = "vehicles"`, singleton por tenant, `TokenBundle.access_token` = JSON `{"client_id", "client_secret", "refresh_token"}` (`token_type="config"`, mismo criterio que `"llm"`/`"images"` — ver [`credenciales.md`](./credenciales.md)).
- API de Smartcar: https://smartcar.com/docs/api-reference — auth en `https://auth.smartcar.com/oauth/token`, datos/control en `https://api.smartcar.com/v2.0`.

Ver también: [`conectores.md`](./conectores.md) para el resto de integraciones, y [`configuracion.md`](./configuracion.md) para la pantalla de Configuración en general.
