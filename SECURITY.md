# Política de seguridad

Edecán es un proyecto en fase inicial (pre-1.0). No tenemos ninguna certificación de cumplimiento formal (no somos SOC 2, no somos ISO 27001, no hay una auditoría externa publicada) — lo que ofrecemos es un diseño documentado y honesto sobre sus riesgos (ver [`docs/seguridad-modelo-amenazas.md`](./docs/seguridad-modelo-amenazas.md) y `RIESGOS.md`), y un compromiso real de divulgación responsable. Si necesitas una certificación formal para tu proceso de compras, pregúntanos el estado actual antes de asumir que existe.

## Reporte de vulnerabilidades

Si encuentras una vulnerabilidad de seguridad en Edecán, por favor **no abras un issue público**. Repórtala de forma privada a:

**security@TU_DOMINIO_AQUI**

(placeholder — quien opere una instancia o distribución de Edecán debe reemplazar `TU_DOMINIO_AQUI` por su propio dominio de contacto real, idealmente una casilla dedicada a seguridad, no el correo personal de una sola persona.)

Incluye en tu reporte:

- Descripción de la vulnerabilidad y su impacto potencial.
- Pasos para reproducirla (idealmente con un caso mínimo).
- Versión, commit o rama afectada.
- Cualquier mitigación temporal que conozcas.

Si tu hallazgo es especialmente sensible (p. ej. involucra una fuga de datos ya explotable), dilo explícitamente en el asunto del correo para que se priorice de inmediato.

### Nuestro compromiso de respuesta

- **Confirmación de recepción en un máximo de 72 horas** desde que llega el reporte a `security@TU_DOMINIO_AQUI`.
- Tras confirmar recepción, te daremos una evaluación inicial de severidad y los siguientes pasos — el tiempo de investigación varía según la complejidad, pero te mantendremos informado en vez de dejarte sin respuesta.
- Coordinamos contigo una fecha de divulgación pública razonable **después** de que exista una corrección o mitigación desplegada — no publicamos detalles técnicos explotables antes de eso.
- Si quieres crédito público por el hallazgo una vez resuelto y divulgado de forma coordinada, dínoslo en tu reporte y te lo damos con gusto; si prefieres permanecer anónimo, también lo respetamos.

### Investigación de buena fe (*safe harbor*)

Si tu investigación se mantiene dentro del alcance de abajo, evita degradar el servicio para otros, no accede ni exfiltra más datos de los estrictamente necesarios para demostrar el hallazgo (nunca datos reales de un tenant ajeno a ti), y nos reportas de forma privada y oportuna en vez de explotarlo o divulgarlo primero — consideramos esa investigación como actividad de buena fe y no la perseguiremos legalmente por ello. Esto no es una promesa de inmunidad para actividad que exceda ese marco (p. ej. acceso masivo a datos de terceros, interrupción del servicio, ingeniería social contra el equipo).

## Alcance

**Dentro de alcance:**

- El núcleo open-core: `apps/` y `packages/`.
- La capa premium: `premium/` (licencia comercial, ver `NOTICE`).
- El diseño de infraestructura en `infra/` — se revisa como código.

**Fuera de alcance:**

- **Nunca se ejecuta `terraform apply`, `docker push` ni llamadas AWS con efectos reales desde este repositorio o sus agentes automatizados** — si tu reporte requeriría que nosotros (o tú) ejecutáramos algo así para reproducirlo, descríbelo en el reporte en vez de ejecutarlo.
- Vulnerabilidades que dependan exclusivamente de una configuración insegura que el propio operador de una instancia self-host eligió a propósito (p. ej. dejar `JWT_SECRET` con el valor placeholder de `.env.example` en una instancia expuesta a internet) — sí queremos saberlo igual porque nos ayuda a mejorar los defaults y las advertencias, pero lo tratamos como hallazgo de *hardening*, no como vulnerabilidad crítica del código.
- Ataques de ingeniería social contra el equipo o la comunidad del proyecto.

## Áreas de especial sensibilidad

- **Aislamiento multi-tenant**: el modelo usa un pool de base de datos compartido con Row-Level Security (`ARCHITECTURE.md` §2). Cualquier hallazgo que permita a un tenant leer o escribir datos de otro tenant (incluida una política RLS incompleta, un `SET LOCAL app.tenant_id` faltante, o un job de worker que no filtre por `tenant_id`) se trata como **crítico**.
- **TokenVault**: las credenciales de conectores (OAuth de Google/Microsoft/Meta/X/YouTube, Twilio por tenant) se cifran con AES-256-GCM mediante una data key por tenant, envuelta con KMS en producción o con `LOCAL_MASTER_KEY` (Fernet) en desarrollo. Estas credenciales nunca deben aparecer en texto claro en logs, backups, mensajes de error o trazas.
- **Sin credenciales compartidas ni scraping**: toda integración usa APIs oficiales; cada tenant conecta sus propias credenciales vía OAuth. Un hallazgo que involucre credenciales compartidas entre tenants, o scraping de una plataforma, se considera un defecto de diseño grave.
- **LinkedIn**: está excluido permanentemente del proyecto en cualquier forma (código, scopes, URLs, UI, documentación). Cualquier reintroducción se trata como una regresión de seguridad/cumplimiento, no solo de producto.
- **Telefonía y campañas** (`premium/`): toda llamada o SMS saliente exige consentimiento registrado (`consents`), ventana horaria del destinatario y mecanismo de opt-out. Un hallazgo que permita saltarse estos controles se considera crítico.

## Buenas prácticas para quien contribuye

- Nunca incluyas API keys, tokens, contraseñas o datos personales reales en código, tests, commits o issues — usa siempre placeholders `TU_X_AQUI` (ver `.env.example`).
- Los tests deben ser offline y deterministas (usa `respx`/fakes para HTTP); no deben realizar llamadas de red reales, y mucho menos a servicios de pago.
- No ejecutes `terraform apply`, comandos `aws` con efectos reales, ni `docker push` como parte de desarrollo, CI o revisión — el código de infraestructura en `infra/` se escribe y se revisa, nunca se aplica automáticamente.

## Versiones soportadas

Mientras el proyecto esté en fase inicial (pre-1.0), el soporte de seguridad cubre únicamente la rama `main`.

## Ver también

- [`docs/seguridad-modelo-amenazas.md`](./docs/seguridad-modelo-amenazas.md) — activos, actores, STRIDE resumido y las mitigaciones ya implementadas.
- [`docs/runbooks/`](./docs/runbooks/) — procedimientos operativos ante un incidente confirmado (fuga entre tenants, rotación de claves, restauración de base de datos, cola atascada).
- [`docs/cumplimiento/privacidad.md`](./docs/cumplimiento/privacidad.md) y [`docs/cumplimiento/tos-redes.md`](./docs/cumplimiento/tos-redes.md) — postura de privacidad y de cumplimiento con términos de servicio de terceros.
- [`RIESGOS.md`](./RIESGOS.md) — registro vivo de riesgos del proyecto, incluidos los de seguridad.
