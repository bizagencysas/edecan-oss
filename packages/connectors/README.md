# packages/connectors — `edecan_connectors`

Conectores OAuth 2.0 a APIs oficiales. La plataforma registra su propia app OAuth por proveedor;
**cada tenant autoriza su propia cuenta** y los tokens resultantes (`TokenBundle`) se guardan
cifrados en el TokenVault (`packages/db`) — **este paquete nunca almacena tokens**
(`ARCHITECTURE.md` §10.8).

## Contrato

- `OAuthSpec` (dataclass): `auth_url`, `token_url`, `scopes`, `pkce`, `extra_params`.
- `Connector` (ABC): `key`, `display_name`, `oauth`, `auth_url(redirect_uri, state)`,
  `async exchange_code(code, redirect_uri, http, code_verifier=None)`,
  `async refresh(bundle, http)`.
- `edecan_connectors.registry.CONNECTORS: dict[str, Connector]` — incluye siempre las keys
  `"google"` y `"microsoft"`, más las que agregue (si está instalado) el submódulo opcional
  `edecan_connectors.social`, importado con `try/except ImportError` — este paquete funciona
  igual de bien sin él.

Todo el tráfico es REST directo con `httpx` (cliente compartido en `edecan_connectors.http`:
`build_http_client()`, timeout de 30s, sin redirects automáticos), testeable con `respx` — sin
red real en los tests.

### PKCE

Cuando `oauth.pkce` es `True`, `auth_url()` deriva el `code_challenge` (método `S256`, RFC 7636)
directamente del propio `state` recibido. Quien llama (la capa API) debe generar `state` como un
token aleatorio con entropía y alfabeto suficientes (p. ej. `secrets.token_urlsafe(32)`, válido
también como `code_verifier` de RFC 7636) y reenviar ese **mismo valor** como `code_verifier` al
invocar `exchange_code(...)` tras el callback — así no hace falta guardar el `code_verifier` por
separado del `state`.

### Configuración perezosa

El client id/secret de cada proveedor se leen de variables de entorno **al invocar**
`auth_url`/`exchange_code`/`refresh` (nunca al importar el módulo), y lanzan `ConnectorError` con
un mensaje claro si faltan. Así, una instancia self-hosteada que solo configuró alguno de los
proveedores puede seguir arrancando sin error.

## Conectores incluidos

- **Google** (`edecan_connectors.google`) — Gmail (lectura, envío, borradores) + Calendar.
  Módulos `gmail.py` (`search_messages`, `send_message`, `create_draft`) y `gcal.py`
  (`list_events`, `create_event`).
- **Microsoft** (`edecan_connectors.microsoft`) — Outlook Mail + Calendar vía Microsoft Graph.
  Módulo `graph.py` (`search_mail`, `send_mail`, `list_events`, `create_event`).

Todas las funciones de API reciben `(http, bundle: TokenBundle, ...)` y usan
`Authorization: Bearer {bundle.access_token}` — **nunca** persisten ni cachean credenciales.

## Alcance de las integraciones

Este paquete integra **exclusivamente APIs oficiales** que cada proveedor pone a disposición de
terceros para este uso (correo, calendario y publicación en cuentas propias vía el submódulo
social). No hay scraping ni credenciales compartidas: cada tenant conecta su propia cuenta por
OAuth. LinkedIn usa OpenID Connect, Share on LinkedIn, Posts API e Images API oficiales; no
automatiza contactos, mensajes masivos, scraping ni engagement.

## Cómo crear tus propias apps OAuth (self-host)

Cada instancia self-hosteada usa **sus propias** apps OAuth registradas ante cada proveedor —
nunca credenciales compartidas de la plataforma. Copia los client id/secret resultantes a tu
`.env` (ver `.env.example`); nunca los subas a control de versiones.

### Google (Gmail + Calendar)

1. Crea (o reutiliza) un proyecto en Google Cloud Console.
2. Habilita las APIs "Gmail API" y "Google Calendar API" para ese proyecto.
3. Configura la pantalla de consentimiento OAuth: tipo "Externo" si vas a dar acceso a cuentas
   fuera de tu organización, y agrega los scopes que usa este paquete (`gmail.readonly`,
   `gmail.send`, `gmail.compose`, `calendar.events`). Mientras la app esté en modo "Prueba" solo
   los usuarios de prueba que agregues podrán autorizarla; publícala (con la verificación de
   Google si corresponde) cuando vayas a producción con terceros.
4. Crea credenciales de tipo "ID de cliente de OAuth", tipo de aplicación "Aplicación web".
5. En "URIs de redirección autorizados" agrega la URL de callback de tu instancia, con tu
   `PUBLIC_BASE_URL` real: `{PUBLIC_BASE_URL}/v1/connectors/google/callback` (en desarrollo local,
   `http://localhost:8000/v1/connectors/google/callback`).
6. Copia el "ID de cliente" y el "Secreto del cliente" a `GOOGLE_CLIENT_ID` y
   `GOOGLE_CLIENT_SECRET` en tu `.env`.

### Microsoft (Outlook + Calendar)

1. Entra al centro de administración de Microsoft Entra (Azure AD) y crea un nuevo "Registro de
   aplicación". Como tipo de cuenta admitida usa "Cuentas en cualquier organización y cuentas
   Microsoft personales" si quieres aceptar tanto cuentas laborales/escolares como personales
   (Outlook.com) — coincide con el endpoint `common` que usa este conector.
2. En "Autenticación", agrega una plataforma "Web" con URI de redirección:
   `{PUBLIC_BASE_URL}/v1/connectors/microsoft/callback`.
3. En "Certificados y secretos", crea un "Secreto de cliente" nuevo y cópialo de inmediato (no
   se vuelve a mostrar).
4. En "Permisos de API", agrega permisos delegados de Microsoft Graph: `offline_access`,
   `User.Read`, `Mail.ReadWrite`, `Mail.Send`, `Calendars.ReadWrite`. No hace falta "Conceder
   consentimiento de administrador": cada tenant autoriza su propia cuenta al conectar.
5. Copia el "Id. de aplicación (cliente)" y el secreto del paso 3 a `MS_CLIENT_ID` y
   `MS_CLIENT_SECRET` en tu `.env`.

## Tests

```bash
uv run pytest packages/connectors
```

Offline y deterministas: usan `respx` para interceptar `httpx` (sin red real, sin credenciales
reales). Los tests de este paquete **no importan `edecan_schemas`** (paquete hermano) — usan un
fake local que replica la forma de `TokenBundle`, ver `tests/conftest.py` (`ARCHITECTURE.md`
§10.1).
