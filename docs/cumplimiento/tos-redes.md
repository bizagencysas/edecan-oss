# Términos de servicio de cada red — qué permite, qué prohíbe, qué prometemos

Cada plataforma con la que Edecán se integra tiene sus propios Términos de Servicio para desarrolladores, y todas comparten un núcleo de reglas parecido: acceso solo vía **API oficial y autenticada** (nunca scraping), **consentimiento explícito** del usuario final por OAuth, prohibición de **automatización engañosa** (hacerse pasar por una persona, inflar métricas de forma artificial, contactar en masa sin relación previa) y límites sobre qué se puede hacer con los datos obtenidos más allá de servir al propio usuario que autorizó el acceso.

Este documento resume, plataforma por plataforma, qué permite el uso oficial de la API y qué está prohibido — en el nivel de detalle relevante para entender por qué Edecán está diseñado como está —, y cuál es el compromiso concreto del producto frente a esas reglas. **No es un resumen legal exhaustivo de cada Términos de Servicio**: esos documentos cambian con el tiempo y cada operador de una instancia de Edecán es responsable de revisar la versión vigente antes de operar a escala.

## Principios comunes (aplican a las cinco integraciones soportadas)

1. **Solo API oficial, nunca scraping.** Todo conector en `packages/connectors/` habla exclusivamente con endpoints documentados de cada proveedor vía `httpx` (ver `ARCHITECTURE.md` §5 y §10.8). No hay ni ha habido código que simule un navegador, inicie sesión con credenciales de usuario/contraseña ajenas a OAuth, o extraiga datos de páginas HTML renderizadas.
2. **Cada tenant autoriza su propia cuenta.** Nunca se comparten credenciales entre tenants ni se usan credenciales de la plataforma para actuar "como si fuera" un usuario — cada acción queda atada al `TokenBundle` de la cuenta que el propio dueño de esos datos conectó.
3. **Sin automatización que finja ser humana.** El agente actúa **a petición explícita del usuario** en un turno de conversación (o de una campaña configurada con destinatarios y consentimiento verificado, en telefonía) — no ejecuta comportamiento continuo de imitación humana (like-bots, follow-bots, engagement artificial) en ninguna red.
4. **Nada de contacto masivo no solicitado.** Las herramientas del agente envían correos, publican contenido o hacen llamadas porque el usuario lo pidió en esa conversación (o porque configuró una campaña con consentimiento registrado, ver [`../voz-telefonia.md`](../voz-telefonia.md)) — no hay un modo de "difusión masiva a desconocidos" en ninguna integración.

## Matriz por red

### Google (Gmail + Calendar)

| | Detalle |
|---|---|
| **Qué permite la API oficial** | Leer, enviar y componer correo (`gmail.readonly`/`gmail.send`/`gmail.compose`) y gestionar eventos de calendario (`calendar.events`) **en nombre del usuario que autorizó explícitamente esos scopes vía OAuth**, dentro de una app que use esos datos para funciones visibles al propio usuario. |
| **Qué está prohibido** | Usar los datos de Gmail para publicidad o perfiles de anuncios; vender o transferir datos de usuario a terceros; permitir que personas humanas (fuera del propio usuario) lean el contenido del correo salvo excepciones muy estrechas y con consentimiento adicional; retener datos más allá de lo necesario para la función ofrecida; enviar correo no solicitado en nombre del usuario sin que él lo haya pedido en esa interacción. Los scopes usados son "sensibles"/"restringidos" y están sujetos a la política de Uso de Datos de Usuario de las APIs de Google, incluida (a partir de cierto volumen) una revisión de seguridad externa. |
| **Compromiso de Edecán** | El agente solo lee/envía correo cuando el propio usuario lo pide en el turno de conversación; `enviar_correo` es una herramienta `dangerous=True` que exige confirmación humana explícita antes de ejecutarse (`ARCHITECTURE.md` §10.7). No hay reenvío de contenido de correo a ningún tercero ni uso para publicidad. |

### Microsoft (Outlook Mail + Calendar)

| | Detalle |
|---|---|
| **Qué permite la API oficial** | Leer/escribir correo (`Mail.ReadWrite`, `Mail.Send`) y calendario (`Calendars.ReadWrite`) vía Microsoft Graph, con consentimiento OAuth del usuario, para funciones que benefician directamente a ese usuario. |
| **Qué está prohibido** | Minería de datos más allá de lo necesario para la función de la app; usar los datos de Graph para entrenar modelos de IA de propósito general sin consentimiento adicional explícito; scraping fuera de la API; ignorar el *throttling* documentado; sortear o almacenar el consentimiento de forma que el usuario no pueda revocarlo efectivamente. |
| **Compromiso de Edecán** | Mismo patrón que Google: acción a petición del usuario, confirmación humana antes de enviar correo, revocación real vía `DELETE /v1/connectors/microsoft/{account_id}` que borra el token del vault. |

### Meta (Facebook Pages + Instagram Business)

| | Detalle |
|---|---|
| **Qué permite la API oficial** | Publicar en una Página de Facebook o cuenta de Instagram Business que el usuario administra y autorizó explícitamente (`pages_manage_posts`, `instagram_content_publish`), leer métricas básicas de esa Página (`pages_read_engagement`, `instagram_basic`) y listar las Páginas administradas (`pages_show_list`) — siempre en nombre de quien autorizó, nunca de terceros. |
| **Qué está prohibido** | Scraping de cualquier tipo (política agresivamente perseguida por Meta, incluida vía litigio); automatizar comportamiento que imite interacción humana para inflar métricas (likes/comentarios/seguidores artificiales); usar datos obtenidos vía la Graph API para construir un perfil de usuario ajeno a la función de la app o para venderlos a terceros; publicar contenido sin que el usuario que administra la Página lo haya autorizado para esa publicación puntual; incumplir los límites de uso reportados en `X-App-Usage`. |
| **Compromiso de Edecán** | `publicar_social` es una herramienta `dangerous=True`: exige confirmación humana antes de publicar cualquier contenido. No hay generación ni publicación de engagement artificial (likes, comentarios, seguidores) en ningún punto del código — el conector solo expone publicar contenido, leer páginas propias y leer insights básicos, todo documentado en [`../conectores.md`](../conectores.md). |

### X (API v2)

| | Detalle |
|---|---|
| **Qué permite la API oficial** | Publicar contenido (`tweet.write`), leer perfil y menciones (`tweet.read`, `users.read`) en nombre de la cuenta que autorizó vía OAuth 2.0 + PKCE, dentro de los límites del nivel de acceso contratado (Free/Basic/Pro). |
| **Qué está prohibido** | Automatización "spammy" (según las reglas de automatización de X: contenido idéntico masivo, *follow/unfollow* agresivo, comportamiento coordinado no auténtico); scraping fuera de la API; redistribuir o almacenar contenido de X más allá de las reglas de cacheo permitidas (en particular, hay que respetar el borrado: si un tweet se borra o una cuenta se vuelve protegida, el contenido cacheado debe eliminarse también); usar datos de la API para entrenar modelos de lenguaje de propósito general sin permiso expreso de X. |
| **Compromiso de Edecán** | `publicar_social` con confirmación humana igual que en Meta. El conector no cachea contenido de terceros más allá de lo necesario para la función solicitada en el turno (p. ej. mostrar menciones recientes), y no hay ningún flujo de *follow/unfollow* automatizado ni de generación de engagement. |

### YouTube (Data API v3)

| | Detalle |
|---|---|
| **Qué permite la API oficial** | Subir video (`youtube.upload`) y leer estadísticas del propio canal (`youtube.readonly`) del usuario que autorizó, sujeto a los Términos de Servicio de las API de YouTube (un documento separado, más estricto, de los Términos generales de las API de Google). |
| **Qué está prohibido** | Descargar video de YouTube por fuera de los mecanismos oficiales; scraping; cachear datos de la API más allá de los períodos de refresco permitidos (por defecto, los datos deben refrescarse o purgarse periódicamente, no almacenarse indefinidamente); generar métricas de interacción sintéticas o incentivadas; no mostrar la atribución/branding de YouTube requerida en apps que exhiban contenido de YouTube. |
| **Compromiso de Edecán** | El conector solo sube contenido que el propio usuario pide subir en ese turno y solo lee estadísticas del canal propio del usuario — no hay descarga de video de terceros ni generación de engagement artificial. |

### LinkedIn — excluido permanentemente

| | Detalle |
|---|---|
| **Qué permite la API oficial** | El acceso programático de LinkedIn es, por diseño de la propia plataforma, mucho más restringido que el de las demás redes de esta lista: los productos de su API (Marketing Developer Platform, Talent Solutions, "Sign In with LinkedIn") son mayormente de acceso restringido/por invitación y no ofrecen un equivalente general a "publicar en nombre del usuario" o "gestionar su red de contactos" abierto a cualquier desarrollador de la forma en que sí lo hacen Meta, X o Google. |
| **Qué está prohibido** | El *User Agreement* de LinkedIn prohíbe explícitamente el uso de bots o métodos automatizados para acceder al servicio, conectar o comunicarse con otros usuarios, o extraer ("scrape") perfiles e información — independientemente de si técnicamente fuera posible automatizarlo. LinkedIn históricamente ha perseguido de forma activa, incluso por vía judicial, a herramientas de automatización de terceros dirigidas a su plataforma, sin importar la intención declarada de esas herramientas. |
| **Compromiso de Edecán** | **No se implementa ninguna integración con LinkedIn, bajo ninguna forma.** No es una limitación técnica temporal: es una decisión permanente de cumplimiento (`ARCHITECTURE.md` §0.2), reforzada por un test automatizado (`test_no_linkedin`) que falla la suite si la palabra aparece en `packages/connectors/`, y no se aceptan Pull Requests que la reintroduzcan. El motivo es precisamente que no existe, hoy, un camino de automatización de networking personal en LinkedIn que sea a la vez útil para el producto y compatible con sus términos — la alternativa honesta es no ofrecerlo, no ofrecerlo "por scraping" ni "con cuidado". Ver [`../conectores.md`](../conectores.md) sección "Integraciones excluidas". |

## Compromiso del producto (resumen)

- Cada integración de Edecán se limita estrictamente a lo que la API oficial del proveedor documenta y permite.
- Ninguna integración se implementa si su única vía técnica viable fuera scraping, credenciales compartidas o automatización que imite comportamiento humano sin autorización caso por caso del propio usuario.
- Las acciones que publican o envían algo en nombre del usuario (correo, redes sociales, llamadas/SMS) siempre requieren esa autorización en el turno concreto donde ocurren — nunca autonomía total y silenciosa del agente sobre canales que afectan a terceros.
- Si en el futuro los Términos de Servicio de alguna de estas cinco plataformas cambian de forma que una funcionalidad existente deje de cumplir esta matriz, esa funcionalidad se ajusta o se retira — no se mantiene por inercia. Este documento debe revisarse cuando eso ocurra.
