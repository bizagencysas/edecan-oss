# Conectores

Edecán se integra con Google, Microsoft, Meta, X y YouTube **exclusivamente por sus APIs oficiales**, con OAuth 2.0. La plataforma (quien opera esta instancia — tú, si haces self-host) registra **una app OAuth por proveedor**; luego **cada tenant autoriza su propia cuenta** contra esa app. Los tokens resultantes nunca se comparten entre tenants y se guardan cifrados en el `TokenVault` (`ARCHITECTURE.md` §10.4). Ningún conector hace scraping ni usa endpoints no documentados — ver `packages/connectors/edecan_connectors/`.

Todos los conectores comparten el mismo patrón de callback:

```
{PUBLIC_BASE_URL}/v1/connectors/{key}/callback
```

Sustituye `{PUBLIC_BASE_URL}` por la URL pública real de tu API (en desarrollo, `http://localhost:8000`) y `{key}` por la clave del conector (`google`, `microsoft`, `meta`, `x`, `youtube`). Esa es exactamente la URL que debes registrar como "redirect URI" / "callback URL" autorizada en la consola de cada proveedor — si no coincide carácter por carácter (incluido el esquema `http`/`https` y el puerto), el proveedor rechazará el intercambio de código por token.

Las credenciales de la app (client id/secret) van en tu `.env` — ver [`configuracion.md`](./configuracion.md). Nunca son credenciales de un usuario final: identifican a la aplicación, no a una persona.

---

## Google (Gmail + Calendar)

**Clave del conector**: `google`. **Variables**: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`.

### Crear la app OAuth

1. Entra a [Google Cloud Console](https://console.cloud.google.com/) y crea (o reutiliza) un proyecto.
2. **APIs & Services → Library**: activa **Gmail API** y **Google Calendar API**.
3. **APIs & Services → OAuth consent screen**: tipo *External* (a menos que todos tus tenants sean de tu propio Google Workspace, en cuyo caso puede ser *Internal*). Completa nombre de la app, correo de soporte y dominios autorizados. Mientras la app esté en modo *Testing*, solo los correos que agregues como "test users" podrán autorizar — pasa a *In production* (requiere verificación de Google para scopes sensibles) cuando quieras abrirlo a cualquier tenant.
4. **APIs & Services → Credentials → Create Credentials → OAuth client ID**, tipo **Web application**.
5. En **Authorized redirect URIs** agrega exactamente `{PUBLIC_BASE_URL}/v1/connectors/google/callback`.
6. Copia el **Client ID** y el **Client secret** a `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` en tu `.env`.

### Scopes mínimos exactos usados

```
https://www.googleapis.com/auth/gmail.readonly
https://www.googleapis.com/auth/gmail.send
https://www.googleapis.com/auth/gmail.compose
https://www.googleapis.com/auth/calendar.events
```

Usa PKCE (`code_challenge`/`code_verifier`) y pide `access_type=offline&prompt=consent` para asegurar que Google emita `refresh_token` (Google solo lo entrega la primera vez que el usuario consiente, salvo que fuerces `prompt=consent`). `gmail.readonly`, `gmail.send` y `calendar.events` son scopes **sensibles**: para salir de modo *Testing* en Google Cloud necesitarás pasar la verificación de OAuth de Google (incluye un video de demostración y, en algunos casos, una revisión de seguridad de terceros — CASA).

### Límites de uso conocidos

Gmail API y Calendar API usan un sistema de **unidades de cuota** por proyecto, no un conteo simple de llamadas:

- **Gmail API**: cuota de proyecto por defecto (ampliable desde la consola), más un límite por usuario de aproximadamente 250 unidades/usuario/segundo. Leer o listar mensajes cuesta ~5 unidades por llamada; enviar un correo (`gmail.send`) cuesta ~100 unidades.
- **Calendar API**: cuota de proyecto por defecto de aproximadamente 1,000,000 de consultas/día, con límites adicionales por usuario en ventanas de 100 segundos.

Estos números son orientativos y **cambian con el tiempo** — confirma siempre el valor vigente en **Google Cloud Console → APIs & Services → [API] → Quotas** antes de dimensionar cuántos tenants puede soportar tu proyecto.

---

## Microsoft (Outlook Mail + Calendar)

**Clave del conector**: `microsoft`. **Variables**: `MS_CLIENT_ID`, `MS_CLIENT_SECRET`.

### Crear la app OAuth

1. Entra al [Azure Portal](https://portal.azure.com/) → **Microsoft Entra ID → App registrations → New registration**.
2. Nombre de la app; en **Supported account types** elige *Accounts in any organizational directory and personal Microsoft accounts* (necesario porque el conector usa el endpoint `common`, que admite cuentas laborales/escolares y personales).
3. En **Redirect URI** elige tipo **Web** y pon exactamente `{PUBLIC_BASE_URL}/v1/connectors/microsoft/callback`.
4. **API permissions → Add a permission → Microsoft Graph → Delegated permissions**, agrega los scopes de la siguiente sección. Si tu tenant de Azure lo exige, otorga *Admin consent*.
5. **Certificates & secrets → New client secret** — cópialo de inmediato (no se vuelve a mostrar).
6. Copia el **Application (client) ID** y el secreto a `MS_CLIENT_ID`/`MS_CLIENT_SECRET`.

### Scopes mínimos exactos usados

```
offline_access
User.Read
Mail.ReadWrite
Mail.Send
Calendars.ReadWrite
```

Usa PKCE. `offline_access` es lo que permite obtener `refresh_token`; Microsoft además **rota el refresh token en cada uso** (a diferencia de Google), por lo que el conector siempre guarda el más reciente que el proveedor devuelva.

### Límites de uso conocidos

Microsoft Graph aplica *throttling* dinámico por app y por buzón (no una cuota diaria fija): cuando se excede, responde `429 Too Many Requests` con un header `Retry-After` que indica cuánto esperar. Como referencia de orden de magnitud, Microsoft documenta límites cercanos a **10,000 solicitudes por 10 minutos por app por buzón** para las apps de Outlook Mail/Calendar, pero el valor exacto depende del tipo de recurso y puede cambiar. Respeta siempre el header `Retry-After` en vez de asumir un número fijo — consulta la documentación de [Throttling en Microsoft Graph](https://learn.microsoft.com/graph/throttling) para el detalle vigente.

---

## Meta (Facebook Pages e Instagram Business)

**Clave del conector**: `meta`. **Variables**: `META_APP_ID`, `META_APP_SECRET`. Requiere el flag de plan `connectors.social`.

### Crear la app OAuth

1. Entra a [Meta for Developers](https://developers.facebook.com/) → **My Apps → Create App**, tipo *Business*.
2. Agrega el producto **Facebook Login** (o *Facebook Login for Business* si administras varias Páginas desde un Business Manager).
3. En **Facebook Login → Settings → Valid OAuth Redirect URIs** agrega exactamente `{PUBLIC_BASE_URL}/v1/connectors/meta/callback`.
4. **App Settings → Basic**: copia el **App ID** y el **App Secret** a `META_APP_ID`/`META_APP_SECRET`.
5. Mientras la app esté en modo *Development*, solo los usuarios con un rol asignado en el Business Manager (admin, developer, tester) pueden autorizar. Para operar con Páginas de terceros necesitas pasar **App Review** de Meta para los permisos avanzados (`pages_manage_posts`, `instagram_content_publish`, etc.) y, normalmente, verificación del negocio (*Business Verification*).

### Scopes mínimos exactos usados

```
pages_manage_posts
pages_read_engagement
pages_show_list
instagram_basic
instagram_content_publish
```

El conector no usa `refresh_token`: el token de usuario de corta duración se canjea por uno de larga duración (~60 días) con `grant_type=fb_exchange_token` contra el mismo endpoint de token — hay que reautorizar (o refrescar programáticamente antes de que expire) periódicamente. Publicar en Instagram Business requiere que la cuenta de Instagram esté vinculada a la Página de Facebook correspondiente (`instagram_business_account`).

### Límites de uso conocidos

La Graph API de Meta no usa una cuota fija por hora, sino un puntaje de uso por app/Página que se reporta en los headers `X-App-Usage` y `X-Business-Use-Case-Usage` de cada respuesta (porcentaje consumido de *calls*, *CPU time* y *total time* en una ventana móvil). Cuando el porcentaje se acerca a 100%, Meta empieza a limitar o rechazar llamadas. En modo *Development* los límites son bastante más bajos que en producción con la app ya revisada. Revisa esos headers en tus propias respuestas y la documentación de [Rate Limiting de Graph API](https://developers.facebook.com/docs/graph-api/overview/rate-limiting/) para el comportamiento vigente.

---

## X (API v2)

**Clave del conector**: `x`. **Variables**: `X_CLIENT_ID`, `X_CLIENT_SECRET`. Requiere el flag de plan `connectors.social`.

### Crear la app OAuth

1. Entra al [X Developer Portal](https://developer.x.com/) → crea un **Project** y, dentro, una **App**.
2. En **User authentication settings**, actívalas y configura: **App permissions** = *Read and write* (necesario para `tweet.write`); **Type of App** = *Web App, Automated App or Bot*; **Callback URI / Redirect URL** = exactamente `{PUBLIC_BASE_URL}/v1/connectors/x/callback`; completa también *Website URL* (obligatorio).
3. En **Keys and tokens**, copia el **OAuth 2.0 Client ID** y el **Client Secret** a `X_CLIENT_ID`/`X_CLIENT_SECRET`.

### Scopes mínimos exactos usados

```
tweet.read
tweet.write
users.read
offline.access
```

`offline.access` es lo que habilita `refresh_token`. El conector usa PKCE con `code_challenge_method=S256`; el `code_verifier` se deriva de forma determinista a partir del `state` de la autorización (ver `derive_code_verifier` en `packages/connectors/edecan_connectors/social/x.py`) para no requerir almacenamiento adicional entre el paso de autorización y el callback.

### Límites de uso conocidos

La API v2 de X está sujeta a los **niveles de acceso de pago** de la plataforma (Free/Basic/Pro/Enterprise), cada uno con topes mensuales de *posts* de escritura y de lecturas muy distintos entre sí — el nivel *Free* en particular es predominantemente de solo-escritura con un tope mensual de publicaciones bajo, y prácticamente sin acceso de lectura útil. Además de esos topes mensuales por nivel, cada endpoint tiene su propio límite por ventana de 15 minutos (p. ej. históricamente `POST /2/tweets` ronda el orden de 200 solicitudes/15 min por usuario en niveles pagos). Estos números cambian con frecuencia según la política comercial de X — confirma siempre el nivel y los límites vigentes en el [Developer Portal](https://developer.x.com/en/portal/dashboard) de tu propia app antes de asumir capacidad.

---

## YouTube (Data API v3)

**Clave del conector**: `youtube`. **Variables**: reutiliza `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` — YouTube no tiene servidor de autorización propio, usa el mismo OAuth 2.0 de Google con scopes distintos.

### Crear la app OAuth

1. En el **mismo proyecto de Google Cloud** que usaste para el conector `google`, ve a **APIs & Services → Library** y activa además **YouTube Data API v3**.
2. No hace falta un client OAuth nuevo: el mismo **Client ID**/**Client Secret** web de la sección de Google sirve, siempre que `{PUBLIC_BASE_URL}/v1/connectors/youtube/callback` también esté en la lista de **Authorized redirect URIs** de ese client (agrégalo junto al de `google`).
3. `youtube.upload` es un scope **restringido** (no solo sensible): si vas a operar en producción con tenants ajenos a tu propia organización, Google exige una **verificación de API adicional** específica para YouTube (incluye una auditoría de seguridad), separada de la verificación general de OAuth.

### Scopes mínimos exactos usados

```
https://www.googleapis.com/auth/youtube.upload
https://www.googleapis.com/auth/youtube.readonly
```

Igual que el conector `google`, usa PKCE (`pkce=True` en su `OAuthSpec`) y pide `access_type=offline&prompt=consent` para garantizar `refresh_token` — en la práctica, el mismo flujo de autorización de Google, solo que con scopes distintos.

### Límites de uso conocidos

La Data API v3 usa cuota de proyecto en **unidades**, con un default de **10,000 unidades/día** (ampliable solicitando más cuota a Google). El costo varía muchísimo por operación: listar/leer estadísticas del canal cuesta pocas unidades (`channels.list` ≈ 1), mientras que **subir un video (`videos.insert`) cuesta 1,600 unidades** — es decir, con la cuota default alcanzan solo unas pocas decenas de subidas por día en todo el proyecto (compartido entre todos los tenants). Si vas a ofrecer publicación en YouTube a varios tenants activos, probablemente necesites solicitar aumento de cuota pronto. Confirma el costo exacto por endpoint en la [calculadora de cuota de YouTube](https://developers.google.com/youtube/v3/determine_quota_cost).

---

## Integraciones excluidas

**LinkedIn no forma parte de este producto, en ninguna forma, y esto es una decisión de cumplimiento permanente — no una omisión temporal.**

- No hay conector, scope, URL, texto de UI ni mención de LinkedIn en ningún lugar del código o la documentación de Edecán (`ARCHITECTURE.md` §0.2).
- Existe un test automatizado (`test_no_linkedin`, en `packages/connectors/tests/`) que **falla la suite** si la palabra "linkedin" aparece en `packages/connectors/` — no es una convención de estilo, es una barrera técnica.
- **No se aceptarán Pull Requests que agreguen soporte de LinkedIn** de ninguna forma (API oficial incluida). Si tienes un caso de uso que crees que lo justifica, la respuesta seguirá siendo no: es una decisión de producto y cumplimiento, no una limitación técnica que se pueda argumentar caso por caso.

Para el detalle de por qué (riesgo de scraping/automatización no oficial en ese ecosistema específico, términos de servicio particularmente restrictivos con herramientas de automatización de redes profesionales) ver [`cumplimiento/tos-redes.md`](./cumplimiento/tos-redes.md).
