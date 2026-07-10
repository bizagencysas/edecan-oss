# Privacidad y protección de datos personales

Edecán procesa datos personales por naturaleza: correos, calendarios, contactos, transacciones financieras, contenido de conversaciones y, en la capa premium, números de teléfono y grabaciones potenciales de llamadas. Este documento describe nuestra **postura** frente a los principales marcos de protección de datos relevantes para el mercado objetivo del producto (LATAM + EE. UU. hispanohablante + una postura alineada con GDPR como referencia general), qué controles existen hoy, y qué queda como trabajo pendiente declarado.

> Este documento no es asesoría legal ni una certificación de cumplimiento. Es un compromiso de diseño y una hoja de ruta honesta. Si operas una instancia hosted comercial (no self-host) con usuarios en la Unión Europea, California, México, Colombia u otra jurisdicción con régimen propio de protección de datos, valida esta postura con asesoría legal local antes de lanzar.

## Dos roles distintos: controlador vs. encargado

Esta distinción importa porque cambia quién es responsable de qué:

- **Self-host**: quien opera la instancia es el **único** responsable del tratamiento de principio a fin — usa sus propias credenciales de LLM/voz/OAuth, su propia base de datos, su propia infraestructura. El Proyecto Edecán (como origen del software) no procesa ni ve ningún dato de esa instancia. No hay "subencargados de la plataforma" en este modo: cada proveedor que el self-hoster conecta (Anthropic, Google, etc.) es un encargado **directo** del self-hoster, con su propio contrato/términos.
- **Hosted (`hosted_basic`/`hosted_pro`/`hosted_business`)**: quien opera la capa hospedada actúa como **encargado del tratamiento** (*data processor*) respecto de los datos que cada tenant introduce sobre sus propios contactos/clientes/terceros (p. ej. el contenido de los correos que el tenant conecta, los contactos de su CRM), y como **responsable del tratamiento** (*data controller*) respecto de los datos de la propia cuenta del tenant y sus usuarios (registro, facturación, autenticación, uso de la plataforma).

## Derechos de los titulares (DSR)

Los siguientes derechos aplican, con matices, bajo GDPR (UE), CCPA/CPRA (California), la LFPDPPP (México) y la Ley 1581 de 2012 (Colombia, régimen de habeas data con los llamados derechos ARCO — Acceso, Rectificación, Cancelación, Oposición — mismo concepto que la LFPDPPP mexicana bajo ese acrónimo):

| Derecho | GDPR | CCPA/CPRA | LFPDPPP (México) | Ley 1581 (Colombia) |
|---|---|---|---|---|
| Acceso a tus datos | Sí | "Right to know" | Acceso (la "A" de ARCO) | Conocer/acceder |
| Rectificación | Sí | Corrección | Rectificación (la "R") | Actualizar/rectificar |
| Supresión / "derecho al olvido" | Sí | Eliminación | Cancelación (la "C") | Revocar autorización / suprimir |
| Oposición al tratamiento | Sí | Opt-out de venta/uso | Oposición (la "O") | Revocar consentimiento |
| Portabilidad | Sí | Parcial (formato portable) | No explícito | No explícito |
| No discriminación por ejercer derechos | Implícito | Explícito | Implícito | Implícito |

### Estado actual (honesto) vs. roadmap

**Hoy** los siguientes mecanismos existen en la API pinned (`ARCHITECTURE.md` §10.12):

- Acceso y rectificación de persona/perfil: `GET`/`PUT /v1/persona`, `GET /v1/me`.
- Supresión puntual de memoria: `DELETE /v1/memory/{id}` (ver [`../personalizacion-nivel-dios.md`](../personalizacion-nivel-dios.md) para el detalle de cómo borrar memoria, incluida la limitación actual de no tener un endpoint de "borrar toda la memoria de una vez").
- Revocación de conectores: `DELETE /v1/connectors/{key}/{account_id}` borra la cuenta conectada y su `TokenBundle` del vault.
- Borrado de recursos individuales: `DELETE /v1/conversations/{id}`, y CRUD completo (incluido `DELETE`) sobre recordatorios, contactos y transacciones.

**No existe todavía** (declarado como roadmap, no como algo ya construido — ver también [`../roadmap.md`](../roadmap.md)):

- Un endpoint único de **exportación completa** de los datos de un usuario/tenant en formato portable (p. ej. `GET /v1/privacy/export` → un archivo descargable con todo lo que `ARCHITECTURE.md` §10.3 modela como datos de ese usuario: `messages`, `memory_items`, `contacts`, `transactions`, `files`, `reminders`).
- Un endpoint único de **borrado completo de cuenta** que dispare la cascada de eliminación (o anonimización, donde el borrado físico no sea posible por obligación legal — p. ej. registros contables) sobre todas las tablas relevantes, en vez de tener que borrar recurso por recurso.
- Un flujo de **verificación de identidad** del solicitante antes de ejecutar una solicitud de acceso/borrado a gran escala (relevante sobre todo para la capa hosted, donde el operador de la plataforma debe evitar que alguien suplante a un usuario para exfiltrar o borrar datos ajenos).

Hasta que estos endpoints existan, una solicitud de acceso o borrado completo se atiende de forma manual: quien opera la instancia hosted ejecuta las eliminaciones necesarias directamente en base de datos (respetando RLS, filtrando siempre por `tenant_id`/`user_id`), y debe dejar constancia en `audit_log`. Un self-hoster tiene acceso directo a su propia base de datos en todo momento, así que el "endpoint" para él siempre ha sido el acceso a su propio Postgres.

## Retención de datos

Postura por defecto (ajustable por cada operador de una instancia hosted, dentro de lo que exija su jurisdicción):

| Categoría de dato | Retención por defecto | Nota |
|---|---|---|
| Cuenta, perfil, persona (`users`, `tenants`, `personas`) | Mientras la cuenta esté activa | Se borra/anonimiza al cancelar la cuenta, salvo obligación legal de conservar registros de facturación. |
| Conversaciones y mensajes (`conversations`, `messages`) | Mientras la cuenta esté activa; el usuario puede borrar conversaciones individuales en cualquier momento | Contienen el contenido más sensible — es la primera categoría a cubrir con un borrado masivo cuando exista el endpoint de la sección anterior. |
| Memoria de largo plazo (`memory_items`, `memory_edges`) | Mientras `memoria_activada` esté activa y el usuario no la borre explícitamente | Ver [`../personalizacion-nivel-dios.md`](../personalizacion-nivel-dios.md). |
| Tokens OAuth (`oauth_tokens`, `tenant_keys`) | Hasta que el tenant desconecte el conector o cancele la cuenta | Cifrados en todo momento (`ARCHITECTURE.md` §10.4); nunca en texto claro ni en logs. |
| Consentimientos de voz/SMS (`consents`) | Se conservan **incluso tras una revocación** (`revoked_at` se marca, el registro no se borra) | Necesario para poder demostrar, ante una auditoría o reclamo, que se respetó un opt-out — borrar la prueba del opt-out sería contraproducente para el propio tenant. |
| Auditoría (`audit_log`) | Retención operativa/de seguridad de varios meses como mínimo (a definir por el operador) | Es evidencia forense ante incidentes (ver [`../seguridad-modelo-amenazas.md`](../seguridad-modelo-amenazas.md) y los runbooks). |
| Eventos de uso (`usage_events`) | Retención suficiente para ciclos de facturación y disputas (típicamente 12–24 meses) | Base de la medición de cuotas y de cualquier disputa de cobro. |
| Registros de facturación (Stripe) | Según obligaciones fiscales/contables locales (con frecuencia 5 años o más) | Viven principalmente en Stripe, no en la base de datos de Edecán. |

## Subencargados (subprocessors) — solo capa hosted

En una instancia **hosted**, dependiendo de qué proveedores tenga configurados el operador de la plataforma, los siguientes terceros pueden procesar datos como subencargados:

| Proveedor | Rol | Qué datos toca |
|---|---|---|
| Anthropic (u otro proveedor LLM configurado) | Inferencia del modelo | El contenido de los mensajes/turnos enviados al LLM en cada conversación. |
| AWS (RDS, S3, SQS, KMS, SES) | Infraestructura | Todo lo que vive en la base de datos, archivos subidos, colas de trabajo y correo transaccional. |
| Deepgram / ElevenLabs / AWS Polly | Voz (STT/TTS) | Audio de voz web y, si aplica, de telefonía. |
| Twilio | Telefonía y SMS (premium) | Contenido de llamadas/SMS — pero la cuenta de Twilio es **del tenant**, no de la plataforma, así que en sentido estricto el tenant es quien tiene la relación de encargado con Twilio; la plataforma actúa como intermediaria técnica. |
| Stripe | Facturación (capa hosted) | Datos de pago y facturación del tenant — nunca datos de los usuarios finales de ese tenant. |
| Brave Search / Tavily | Búsqueda web (herramienta `buscar_web`) | Solo el texto de la consulta de búsqueda, si el tenant usa esa herramienta. |
| Sentry | Observabilidad de errores (opcional, `SENTRY_DSN`) | Metadatos técnicos de errores — nunca debería incluir secretos ni contenido de conversación; es responsabilidad de la configuración de logging evitarlo. |

En **self-host**, esta tabla no aplica como "subencargados de la plataforma": cada proveedor que el self-hoster active es un encargado directo suyo, bajo sus propios términos con ese proveedor.

## Plantilla de Acuerdo de Encargo de Tratamiento (DPA) — placeholder

Lo siguiente es un **esqueleto** de referencia para negociar un DPA con clientes de la capa hosted, no un documento firmado ni jurídicamente completo. Debe revisarlo un abogado antes de usarse en un contrato real.

```
ACUERDO DE ENCARGO DE TRATAMIENTO DE DATOS (DPA) — PLANTILLA

Entre:
  El Responsable del Tratamiento: [NOMBRE_DEL_TENANT_AQUI]
  El Encargado del Tratamiento:   [NOMBRE_LEGAL_DEL_OPERADOR_AQUI]

1. Objeto y duración
   Este DPA rige el tratamiento de datos personales que el Encargado realiza
   por cuenta del Responsable al operar la instancia hospedada de Edecán
   contratada, durante la vigencia del contrato de servicio principal.

2. Naturaleza y finalidad del tratamiento
   Alojamiento, procesamiento y disponibilización de los datos que el
   Responsable introduce en la plataforma (correo, calendario, contactos,
   finanzas, documentos, contenido de conversaciones) con el único fin de
   prestar el servicio de asistente de IA contratado.

3. Categorías de titulares y de datos
   Titulares: usuarios del Responsable y, en su caso, los contactos/clientes
   de ese Responsable que aparezcan en sus datos (contactos, correos).
   Categorías de datos: identificación básica, contacto, contenido de
   comunicaciones, datos financieros que el Responsable decida registrar.

4. Obligaciones del Encargado
   - Tratar los datos únicamente según instrucciones documentadas del
     Responsable (incluidas las de este DPA).
   - Confidencialidad del personal con acceso.
   - Medidas técnicas y organizativas descritas en
     `docs/seguridad-modelo-amenazas.md` (aislamiento multi-tenant con RLS,
     cifrado envolvente de credenciales, confirmación humana en acciones
     sensibles).
   - Lista de subencargados: ver la tabla de este documento; notificación
     de cambios con [PLAZO_DIAS_AQUI] días de antelación.
   - Asistencia razonable ante solicitudes de derechos de los titulares.
   - Notificación de incidentes de seguridad en un plazo máximo de
     [PLAZO_HORAS_AQUI] horas desde su detección.
   - Borrado o devolución de todos los datos al finalizar el contrato,
     salvo obligación legal de conservación.

5. Auditoría
   El Responsable puede solicitar evidencia razonable de cumplimiento de
   este DPA con una frecuencia de [FRECUENCIA_AQUI].

6. Transferencias internacionales
   [DESCRIBIR_MECANISMO_DE_TRANSFERENCIA_AQUI — p. ej. cláusulas
   contractuales tipo, según la región de los subencargados listados.]

Firmas: [FIRMA_RESPONSABLE_AQUI]   [FIRMA_ENCARGADO_AQUI]
Fecha:  [FECHA_AQUI]
```

## Contacto

Para ejercer un derecho de acceso, rectificación, cancelación/supresión u oposición, o para consultas de privacidad en general, usa el mismo canal de contacto descrito en [`../../SECURITY.md`](../../SECURITY.md) (`security@TU_DOMINIO_AQUI` en esta plantilla del repo — cada operador debe reemplazarlo por su contacto real, idealmente uno dedicado a privacidad, p. ej. `privacidad@tu-dominio`).
