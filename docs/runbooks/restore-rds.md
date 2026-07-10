# Runbook: restaurar PostgreSQL desde backup

Cubre dos escenarios muy distintos: restaurar **RDS en producción** (hosted) y restaurar **Postgres self-host** (el contenedor `postgres` de `docker-compose.yml` o de `infra/docker/compose.selfhost.yml`).

## Cuándo se activa

- Corrupción o pérdida de datos detectada (borrado accidental, migración fallida a mitad de camino, bug que sobrescribió datos).
- Necesidad de restaurar a un punto anterior a un incidente de seguridad (p. ej. antes de la ventana de una fuga confirmada, para preservar evidencia o revertir escritura maliciosa).
- Fallo catastrófico de la instancia de base de datos.

## RPO honesto según el entorno

- **RDS en producción**: con *automated backups* habilitados (recomendado siempre), RDS hace snapshot diario más *transaction log shipping* continuo, lo que permite *Point-in-Time Recovery* (PITR) con granularidad de minutos dentro de la ventana de retención configurada.
- **Self-host**: **no hay backup automático por defecto**. `docker-compose.yml` y `compose.selfhost.yml` persisten `postgres_data` como volumen Docker normal — sobrevive a reinicios de contenedor, pero no protege contra borrado accidental de datos, corrupción del propio volumen, o pérdida del disco/host. El RPO real de una instancia self-host es exactamente el que el propio operador se haya configurado con un cron de `pg_dump` (o un snapshot de volumen a nivel de infraestructura) — si no configuraste ninguno, el RPO es "todo lo escrito desde el último respaldo manual que hayas hecho", que puede ser "nunca". Configura un `pg_dump` programado desde el primer día si vas a operar una instancia con datos reales.

## A. Restaurar RDS (producción)

> Los comandos de esta sección son de **referencia para uso manual por un operador humano**, fuera del flujo de este repositorio. `ARCHITECTURE.md` §0.4 prohíbe ejecutar comandos `aws` con efectos reales desde este repo o desde cualquier agente automatizado que opere sobre él — nadie debe copiar-pegar esto en un pipeline de CI ni dejar que un agente de IA los ejecute por su cuenta.

1. **Identifica el punto de restauración objetivo** — el timestamp más reciente **anterior** al problema (p. ej. "2 minutos antes del `DELETE` accidental", o "justo antes de la ventana del incidente de fuga").
2. **Restaura a una instancia nueva** (RDS PITR siempre crea una instancia nueva, nunca sobrescribe la existente in situ):

   ```bash
   aws rds restore-db-instance-to-point-in-time \
     --source-db-instance-identifier edecan-prod \
     --target-db-instance-identifier edecan-prod-restore-YYYYMMDD \
     --restore-time 2026-07-07T14:32:00Z \
     --db-subnet-group-name <subnet-group-privado> \
     --vpc-security-group-ids <sg-id>
   ```

3. **Espera a que la instancia nueva esté disponible** (`aws rds describe-db-instances --db-instance-identifier edecan-prod-restore-YYYYMMDD`, estado `available`) — típicamente varios minutos, no segundos.
4. **Valida los datos en la instancia restaurada antes de cortar tráfico hacia ella** (ver sección "Verificación" abajo) — conéctate directamente con `DATABASE_URL` apuntando a su endpoint, sin tocar todavía la instancia de producción activa.
5. **Corta el tráfico a la instancia nueva** solo cuando la validación pase:
   - Actualiza el secreto de `DATABASE_URL` en Secrets Manager con el nuevo endpoint.
   - Redespliega las task definitions de ECS (`api` y `worker`) para que tomen el secreto actualizado.
   - Verifica en logs que ambos servicios levantaron conectados a la instancia restaurada.
6. **No borres la instancia vieja de inmediato** — consérvala (puedes detenerla para ahorrar costo, RDS permite *stop* hasta 7 días) hasta confirmar que la restauración fue estable en producción real por al menos un par de días.

## B. Restaurar Postgres self-host

### Si tienes un dump de `pg_dump`

```bash
# Detén las apps (no la base de datos todavía) para evitar escrituras a medio camino.
make down   # o docker compose -f infra/docker/compose.selfhost.yml stop api worker web

# Restaura sobre una base de datos vacía (ajusta si tu backup es de otro nombre de DB):
docker compose exec -T postgres psql -U edecan -d postgres -c "DROP DATABASE IF EXISTS edecan;"
docker compose exec -T postgres psql -U edecan -d postgres -c "CREATE DATABASE edecan OWNER edecan;"
cat backup.dump | docker compose exec -T postgres pg_restore -U edecan -d edecan --no-owner
```

### Si restauras el volumen Docker completo

```bash
make down
docker volume rm asistente_postgres_data   # ajusta el nombre real del volumen (docker volume ls)
# Restaura tu snapshot del volumen (depende de cómo lo respaldaste: tar del punto de montaje, snapshot de disco, etc.)
make deps
```

### Cuidado con los roles: no viven "dentro" de la base de datos

Los roles de PostgreSQL (incluido `app_user`, el rol `NOLOGIN` sin `BYPASSRLS` del que depende todo el aislamiento RLS, `ARCHITECTURE.md` §2) son **objetos a nivel de clúster**, no de una base de datos individual. Un `pg_dump` normal (a nivel de base de datos, sin `--role` ni `pg_dumpall -g`) **no incluye la creación de roles**. Después de cualquier restauración:

```sql
-- Verifica que el rol siga existiendo y con los atributos correctos:
SELECT rolname, rolcanlogin, rolbypassrls FROM pg_roles WHERE rolname = 'app_user';
```

Si no existe, vuelve a correr la migración `0001_initial` (`make db-migrate`) — está escrita para crear el rol y sus grants de forma idempotente — o créalo manualmente con los mismos atributos (`NOLOGIN`, sin `BYPASSRLS`, con los grants DML sobre las tablas tenant-scoped) antes de apuntar la API hacia esa base restaurada.

## Verificación (aplica a ambos escenarios)

Antes de dar por buena una restauración y cortar tráfico de producción hacia ella:

1. **Extensión `pgvector` presente**: `SELECT * FROM pg_extension WHERE extname = 'vector';`
2. **Rol `app_user` presente y sin `BYPASSRLS`** (ver arriba).
3. **RLS activo en todas las tablas tenant-scoped**: reutiliza las consultas de [`incidente-fuga-tenant.md`](./incidente-fuga-tenant.md) sección 2 — una restauración es tan buen momento como cualquiera para que una política se haya perdido silenciosamente.
4. **Versión de esquema esperada**: `SELECT version_num FROM alembic_version;` debe coincidir con la cabeza (`head`) de `packages/db/alembic/` en el commit que corresponde al momento restaurado — si no coincide, corre `make db-migrate` para ponerla al día antes de servir tráfico.
5. **Conteo de filas de sanity check** en un par de tablas clave (`tenants`, `messages`) comparado contra lo esperado (o al menos, contra "no es cero cuando no debería serlo").
6. **Smoke test funcional**: login de un usuario de prueba, listar sus conversaciones, confirmar que ve exactamente sus propios datos y no los de otro tenant.

## Comunicación

Si la restauración implica pérdida de datos escritos después del punto de restauración (inevitable en cualquier PITR: todo lo escrito entre el punto restaurado y "ahora" se pierde), comunica a los tenants afectados qué ventana de tiempo se perdió, en términos concretos ("cualquier mensaje, recordatorio o archivo creado entre las 14:30 y las 15:10 UTC del 7 de julio no se recuperó").

## Prevención

- Confirma periódicamente que las restauraciones **funcionan de verdad** — un backup nunca probado no es un backup, es una esperanza. Programa un simulacro de restauración (a un entorno aislado, nunca sobre producción) con cierta cadencia.
- Para self-host: automatiza `pg_dump` con cron desde el primer día si los datos importan; no dependas solo del volumen Docker.
