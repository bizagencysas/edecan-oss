# Runbook: sospecha de fuga de datos entre tenants

**Severidad**: Crítica (Sev1) siempre — sin excepciones, incluso si el alcance parece pequeño al principio. Ver [`../seguridad-modelo-amenazas.md`](../seguridad-modelo-amenazas.md) riesgo #1.

## Cuándo se activa

- Un usuario reporta ver datos que no le pertenecen (conversaciones, contactos, recordatorios, archivos de otro tenant).
- Un reporte de seguridad vía el proceso de [`../../SECURITY.md`](../../SECURITY.md) describe un bypass de aislamiento multi-tenant.
- Una alerta interna detecta una consulta o un job que devolvió filas con `tenant_id` distinto al esperado.
- Una revisión de código o un hallazgo de pentest identifica una tabla sin `ENABLE ROW LEVEL SECURITY`, sin política `tenant_isolation`, o un handler del worker sin filtro explícito de `tenant_id`.

## Prerrequisitos

Acceso de administrador a la base de datos (rol *owner*, no `app_user`), acceso a los logs de `apps/api` y `apps/worker`, y acceso a `audit_log`.

## Pasos

### 1. Contención inmediata (objetivo: primeros 15 minutos)

- Si el vector es una ruta HTTP específica y activamente explotable, desactívala (feature flag, o retira temporalmente el despliegue de esa ruta) antes de investigar a fondo — no esperes a tener el diagnóstico completo para detener el sangrado.
- Si el vector involucra sesiones/JWT (p. ej. un bug que emite tokens con el `tenant_id` equivocado), considera invalidar sesiones activas de forma masiva (rotar `JWT_SECRET` fuerza a todos a reautenticar — es una medida drástica pero reversible).
- **No reinicies servicios asumiendo que "se arregla solo"** — un bug de RLS no se cura reiniciando el proceso; solo pierdes la oportunidad de diagnosticarlo en caliente.

### 2. Confirmar alcance (objetivo: primera hora)

Conéctate a Postgres como *owner* (nunca como `app_user`, que ya tiene RLS activo y ocultaría exactamente lo que necesitas inspeccionar) y verifica el estado real de las políticas:

```sql
-- ¿Qué tablas tienen RLS activado y forzado?
SELECT relname, relrowsecurity, relforcerowsecurity
FROM pg_class
WHERE relkind = 'r' AND relnamespace = 'public'::regnamespace
ORDER BY relname;

-- ¿Qué políticas existen y qué condición aplican?
SELECT schemaname, tablename, policyname, qual
FROM pg_policies
WHERE schemaname = 'public'
ORDER BY tablename;

-- ¿El rol de aplicación tiene BYPASSRLS por error?
SELECT rolname, rolbypassrls, rolsuper
FROM pg_roles
WHERE rolname IN ('app_user');
```

Si `rolbypassrls` es `true` para `app_user`, **esa es la causa raíz** — nunca debería serlo (`ARCHITECTURE.md` §2 y §10.3).

Con la causa aún sin confirmar, delimita el alcance real:

- ¿Qué tabla(s) específicas se vieron afectadas?
- ¿Qué tenant(s) fueron expuestos y cuáles fueron los que vieron datos ajenos (pueden ser listas distintas)?
- ¿Desde cuándo? Cruza `audit_log` y los timestamps de despliegue/migraciones recientes para acotar la ventana temporal.
- ¿El vector fue la API (bajo `app_user`, esperaría estar protegido por RLS) o el worker (que bypassa RLS por diseño y depende de filtrar `tenant_id` a mano en cada handler)?

### 3. Erradicar la causa raíz

- Si falta una política: agrégala y **fuerza** su aplicación (`ENABLE ROW LEVEL SECURITY` + política `tenant_isolation` con la misma forma que las demás tablas).
- Si el problema es un handler del worker sin filtro: corrige la consulta para incluir `WHERE tenant_id = :tenant_id` explícito y añade una prueba de regresión específica para ese handler.
- Si el problema es `app_user` con `BYPASSRLS`: revócalo (`ALTER ROLE app_user NOBYPASSRLS;`) y audita cómo llegó a tenerlo (¿una migración manual fuera de Alembic? ¿un cambio de rol accidental?).
- Cualquier fix a este código pasa por el proceso normal de PR — pero dado que toca un contrato de `ARCHITECTURE.md` §10.3, **debe** actualizar ese documento en el mismo cambio (`CONTRIBUTING.md`).

### 4. Verificar la remediación

Antes de cerrar el incidente, demuéstralo con datos, no solo con lectura de código:

1. Crea (o usa) dos tenants de prueba, A y B, con datos distintivos en la tabla afectada.
2. Abre una sesión como `app_user` con `app.tenant_id` fijado al tenant A.
3. Intenta leer/escribir explícitamente filas del tenant B — debe devolver cero filas (lectura) o fallar (escritura), nunca tener éxito.
4. Repite para cada tabla que estuvo en el alcance del incidente.
5. Si el vector fue el worker, ejecuta el `job_type` afectado con un `JobEnvelope` de un tenant de prueba y confirma que solo toca datos de ese `tenant_id`.

### 5. Notificación

- Documenta el incidente completo en `audit_log` (o el sistema de tracking de incidentes que uses) con línea de tiempo, alcance exacto (qué tenants, qué tablas, qué ventana temporal) y causa raíz.
- Si hay tenants con datos personales de terceros (sus propios contactos/clientes) potencialmente expuestos, la notificación a los afectados sigue la postura de [`../cumplimiento/privacidad.md`](../cumplimiento/privacidad.md) — bajo GDPR, por ejemplo, una fuga con riesgo para los titulares puede requerir notificar a la autoridad de control dentro de 72 horas desde que se tuvo conocimiento; no asumas que aplica o no aplica sin confirmarlo para cada jurisdicción involucrada.
- Notifica a los tenants afectados de forma directa, clara y sin minimizar — qué pasó, qué datos, qué se hizo para contenerlo y corregirlo, qué pueden hacer ellos (p. ej. rotar credenciales de conectores si esos tokens pudieron haber quedado expuestos).

### 6. Postmortem

- Postmortem sin culpar personas (*blameless*): qué falló, por qué las capas de defensa existentes (RLS + revisión de PR) no lo atraparon antes, qué prueba de regresión se agregó.
- Actualiza `../../docs/seguridad-modelo-amenazas.md` si el incidente reveló un riesgo no listado o cambió la probabilidad/impacto de uno existente.
- Si el incidente reveló que falta cobertura de pruebas automatizadas de aislamiento (p. ej. una suite que cree tenants de prueba y verifique sistemáticamente que ninguna tabla tenant-scoped es legible entre ellos), priorízala — es la forma de que este runbook se necesite cada vez menos.
