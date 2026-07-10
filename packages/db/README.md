# packages/db — `edecan_db`

Capa de datos: **SQLAlchemy 2.0 async** (asyncpg) + **Alembic** (`packages/db/alembic/`), con la migración inicial `0001_initial` escrita a mano (`ARCHITECTURE.md` §10.3): extensión `vector`, tablas con `tenant_id`, políticas `ENABLE ROW LEVEL SECURITY` + `tenant_isolation`, y el rol `app_user` (NOLOGIN, sin BYPASSRLS).

`edecan_db.session.get_session(tenant_id)` expone el context manager async que abre cada transacción con `SET LOCAL ROLE app_user` + `SET LOCAL app.tenant_id` cuando corresponde.

También vive aquí el **TokenVault** (`edecan_db.vault`): cifrado envolvente AES-256-GCM de credenciales por tenant, con `KeyProvider` intercambiable — `LocalKeyProvider` (Fernet + `LOCAL_MASTER_KEY`) para dev/self-host o `KmsKeyProvider` (boto3 KMS) para producción (§10.4). `edecan_db.vault.get_key_provider(settings)` elige entre ambos según `KMS_KEY_ID`/`LOCAL_MASTER_KEY`.

Todas las tablas de `ARCHITECTURE.md` §10.3 viven en `edecan_db.models` (SQLAlchemy 2.0 estilo `Mapped`); `edecan_db.models.ALL_MODELS`/`GLOBAL_TABLES`/`RLS_TABLES` sirven para introspección. `edecan_db.seed` crea el tenant `demo` / usuario `demo@example.com` de desarrollo (idempotente).

## Uso local

```bash
make deps              # levanta Postgres+pgvector (docker-compose, ver ARCHITECTURE.md §8)
make db-migrate         # alembic upgrade head
uv run python -m edecan_db.seed   # tenant/usuario/persona de demo
```

## Tests

`packages/db/tests/`: roundtrip del `TokenVault` con `LocalKeyProvider` (`test_vault.py`, sin red), estructura de `edecan_db.models` (`test_db_models.py`, sin red), y aislamiento por Row-Level Security contra Postgres real (`test_rls.py`, `@pytest.mark.integration`, se salta solo si `DATABASE_URL` no está configurada o no hay Postgres alcanzable ahí).
