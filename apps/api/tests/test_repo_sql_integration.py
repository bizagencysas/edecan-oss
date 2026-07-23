"""Test de integración: `SqlRepo` (`edecan_api.repo`) contra Postgres real.

Hallazgo de auditoría (consistencia-arquitectura, `apps/api/edecan_api/repo.py`):
`SqlRepo` es la única implementación de `Repo` que habla con Postgres real y
de la que depende cada router de `apps/api` en producción, pero
`apps/api/tests/*.py` ejercita siempre `FakeRepo` (`tests/api_fakes.py`, en
memoria) vía `dependency_overrides` — tal como exige ARCHITECTURE.md §10.1
("Los tests NO importan paquetes hermanos: usa stubs/fakes"). Eso deja las
~50 sentencias SQL parametrizadas de `SqlRepo` sin verificar contra una base
de datos real en ningún lugar del repo: `packages/db/tests` sí prueba contra
Postgres real (`test_rls.py`, `test_connector_accounts_unique.py`), pero
ejercita el ORM de `edecan_db` (`edecan_db.models`), nunca el SQL crudo
escrito a mano de `SqlRepo`.

Este módulo es la excepción deliberada y acotada a la regla de §10.1: SÍ
importa `edecan_db` (paquete hermano) porque es la única forma de obtener una
`AsyncSession` real contra la que ejercitar `SqlRepo` — mismo criterio que ya
usa `packages/db/tests/test_rls.py` para probar contra Postgres real en vez
de hacerlo con fakes. Mismo patrón de skip/fixture que esos módulos: marcado
`@pytest.mark.integration`, se salta automáticamente si `DATABASE_URL` no
está configurada o Postgres no es alcanzable ahí (la lógica se duplica a
propósito — ver el docstring de `test_connector_accounts_unique.py`: cada
módulo de test de integración es autocontenido). El resto de
`apps/api/tests/*.py` (tests de routers HTTP) sigue sin tocar esto: usa
`FakeRepo` como siempre.

Para correrlo: `docker compose up -d postgres` y luego, con
`DATABASE_URL=postgresql+asyncpg://edecan:edecan@localhost:5432/edecan`
exportada, `uv run pytest apps/api/tests/test_repo_sql_integration.py` (o
simplemente correr toda la suite con esa variable exportada: sin ella, este
módulo entero se salta con un `skip`, nunca falla).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from edecan_api.repo import SqlRepo

pytestmark = pytest.mark.integration


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL")


async def _es_alcanzable(url: str) -> bool:
    import asyncpg

    # asyncpg no entiende el sufijo `+asyncpg` que usa SQLAlchemy en la URL.
    dsn = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    try:
        conn = await asyncpg.connect(dsn, timeout=2)
    except Exception:
        return False
    await conn.close()
    return True


def _skip_reason() -> str | None:
    url = _database_url()
    if not url:
        return "DATABASE_URL no está configurada"
    try:
        alcanzable = asyncio.run(_es_alcanzable(url))
    except Exception as exc:  # pragma: no cover - solo diagnóstico del skip
        return f"No se pudo probar la conexión a DATABASE_URL: {exc}"
    if not alcanzable:
        return f"Postgres no está alcanzable en DATABASE_URL={url!r}"
    return None


_SKIP_REASON = _skip_reason()

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or ""),
]


async def _aplicar_migraciones(database_url: str) -> None:
    """Aplica hasta `head` (idempotente: no-op si ya están aplicadas)."""
    from alembic.command import upgrade
    from alembic.config import Config

    db_dir = Path(__file__).resolve().parents[3] / "packages" / "db"
    cfg = Config(str(db_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(db_dir / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    # `command.upgrade` es sync pero `env.py` llama `asyncio.run(...)` por
    # dentro -- correrlo en un hilo aparte evita pisar el loop de
    # pytest-asyncio de este mismo test.
    await asyncio.to_thread(upgrade, cfg, "head")


@pytest.fixture
async def db(monkeypatch: pytest.MonkeyPatch):
    """Prepara `edecan_db.settings`/`engine` para apuntar a `DATABASE_URL`,
    aplica migraciones, y limpia sus cachés `lru_cache` al terminar."""
    from edecan_db import engine as engine_module
    from edecan_db import settings as settings_module

    database_url = _database_url()
    assert database_url  # el skipif del módulo ya garantizó que hay una

    monkeypatch.setenv("DATABASE_URL", database_url)
    settings_module.get_settings.cache_clear()
    engine_module.get_engine.cache_clear()

    await _aplicar_migraciones(database_url)

    yield

    settings_module.get_settings.cache_clear()
    engine_module.get_engine.cache_clear()


# ---------------------------------------------------------------------------
# Helpers de setup/cleanup — cada test crea su propio tenant+usuario (sufijo
# aleatorio) vía el propio `SqlRepo` sobre la sesión "plataforma" (igual que
# el registro real, `/v1/auth/register`) y los borra al terminar: `tenants`
# se borra por `ON DELETE CASCADE` (migración 0001) arrastra todas las filas
# tenant-scoped que el test haya insertado; `users` es global y se borra
# aparte.
# ---------------------------------------------------------------------------


async def _seed_tenant_y_usuario(sufijo: str) -> tuple[uuid.UUID, uuid.UUID]:
    from edecan_db.session import get_session

    async with get_session(None) as session:
        repo = SqlRepo(session)
        tenant = await repo.create_tenant(
            name=f"SqlRepo test {sufijo}", slug=f"sqlrepo-test-{sufijo}", plan_key="hosted_pro"
        )
        user = await repo.create_user(
            email=f"sqlrepo-test-{sufijo}@example.com", password_hash="x" * 20
        )
        await repo.create_membership(user_id=user["id"], tenant_id=tenant["id"], role="owner")
    return tenant["id"], user["id"]


async def _delete_user(user_id: uuid.UUID) -> None:
    from edecan_db.models import User
    from edecan_db.session import get_session
    from sqlalchemy import delete

    async with get_session(None) as session:
        await session.execute(delete(User).where(User.id == user_id))


async def _cleanup(tenant_id: uuid.UUID, user_id: uuid.UUID) -> None:
    from edecan_db.models import Tenant
    from edecan_db.session import get_session
    from sqlalchemy import delete

    async with get_session(None) as session:
        # El tenant primero: `ON DELETE CASCADE` se lleva todas las tablas
        # tenant-scoped que el test haya poblado.
        await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
    await _delete_user(user_id)


# ---------------------------------------------------------------------------
# tenants / users / memberships
# ---------------------------------------------------------------------------


async def test_tenants_users_memberships(db) -> None:
    from edecan_db.session import get_session

    sufijo = uuid4().hex[:8]
    tenant_id, user_id = await _seed_tenant_y_usuario(sufijo)
    try:
        async with get_session(None) as session:
            repo = SqlRepo(session)

            tenant = await repo.get_tenant(tenant_id)
            assert tenant is not None
            assert tenant["slug"] == f"sqlrepo-test-{sufijo}"
            assert tenant["plan_key"] == "hosted_pro"
            assert tenant["status"] == "active"

            by_slug = await repo.get_tenant_by_slug(f"sqlrepo-test-{sufijo}")
            assert by_slug is not None
            assert by_slug["id"] == tenant_id
            assert await repo.get_tenant_by_slug("no-existe-" + sufijo) is None

            await repo.update_tenant_plan(tenant_id, "hosted_business")
            refreshed = await repo.get_tenant(tenant_id)
            assert refreshed is not None
            assert refreshed["plan_key"] == "hosted_business"

            listado = await repo.list_tenants(limit=1000)
            assert tenant_id in {row["id"] for row in listado}

            user = await repo.get_user(user_id)
            assert user is not None
            assert user["email"] == f"sqlrepo-test-{sufijo}@example.com"

            # `create_user`/`get_user_by_email` normalizan el email a
            # minúsculas (`repo.py` línea ~357/370).
            by_email = await repo.get_user_by_email(f"SQLREPO-TEST-{sufijo}@EXAMPLE.COM")
            assert by_email is not None
            assert by_email["id"] == user_id

            await repo.set_user_totp_secret(user_id, "JBSWY3DPEHPK3PXP")
            with_totp = await repo.get_user(user_id)
            assert with_totp is not None
            assert with_totp["totp_secret"] == "JBSWY3DPEHPK3PXP"
            await repo.set_user_totp_secret(user_id, None)
            without_totp = await repo.get_user(user_id)
            assert without_totp is not None
            assert without_totp["totp_secret"] is None

            await repo.update_user_password_hash(user_id, "nuevo-hash")
            rehashed = await repo.get_user(user_id)
            assert rehashed is not None
            assert rehashed["password_hash"] == "nuevo-hash"

            membership = await repo.get_membership(user_id=user_id, tenant_id=tenant_id)
            assert membership is not None
            assert membership["role"] == "owner"

            first_membership = await repo.get_first_membership_for_user(user_id)
            assert first_membership is not None
            assert first_membership["id"] == membership["id"]
    finally:
        await _cleanup(tenant_id, user_id)


# ---------------------------------------------------------------------------
# personas
# ---------------------------------------------------------------------------


async def test_personas_create_get_upsert(db) -> None:
    from edecan_db.session import get_session

    sufijo = uuid4().hex[:8]
    tenant_id, user_id = await _seed_tenant_y_usuario(sufijo)
    otro_user_id: uuid.UUID | None = None
    try:
        # Segundo usuario del mismo tenant, para ejercitar la rama
        # `existing is None` de `upsert_persona` (crea el default por dentro
        # antes de aplicar los cambios) — se crea vía sesión "plataforma",
        # igual que el registro real (`/v1/auth/register`).
        async with get_session(None) as session:
            repo_platform = SqlRepo(session)
            otro_user = await repo_platform.create_user(
                email=f"sqlrepo-test-otro-{sufijo}@example.com", password_hash="x" * 20
            )
            otro_user_id = otro_user["id"]
            await repo_platform.create_membership(
                user_id=otro_user_id, tenant_id=tenant_id, role="member"
            )

        async with get_session(tenant_id) as session:
            repo = SqlRepo(session)

            created = await repo.create_persona_default(tenant_id=tenant_id, user_id=user_id)
            assert created["nombre_asistente"] == "Edecán"
            assert created["idioma"] == "es"
            assert created["tono"] == "cálido y profesional"
            assert created["formalidad"] == 1
            assert created["rasgos"] == []

            fetched = await repo.get_persona(tenant_id=tenant_id, user_id=user_id)
            assert fetched is not None
            assert fetched["id"] == created["id"]

            upserted_nuevo = await repo.upsert_persona(
                tenant_id=tenant_id,
                user_id=otro_user_id,
                fields={"nombre_asistente": "Ada", "formalidad": 2},
            )
            assert upserted_nuevo["nombre_asistente"] == "Ada"
            assert upserted_nuevo["formalidad"] == 2
            assert upserted_nuevo["id"] != created["id"]

            # `fields` sin ninguna clave permitida, sobre persona YA
            # existente: rama `not clean` — devuelve la fila tal cual.
            unchanged = await repo.upsert_persona(
                tenant_id=tenant_id, user_id=user_id, fields={"ignorado_no_es_columna": "x"}
            )
            assert unchanged["id"] == created["id"]
            assert unchanged["nombre_asistente"] == "Edecán"

            # UPDATE dinámico real, incluido el cast `rasgos::jsonb`.
            updated = await repo.upsert_persona(
                tenant_id=tenant_id,
                user_id=user_id,
                fields={
                    "nombre_asistente": "Edecán Pro",
                    "instrucciones": "Sé breve.",
                    "rasgos": ["directo", "puntual"],
                    "emojis": True,
                    "voice_id": "voice-123",
                },
            )
            assert updated["id"] == created["id"]
            assert updated["nombre_asistente"] == "Edecán Pro"
            assert updated["instrucciones"] == "Sé breve."
            assert updated["rasgos"] == ["directo", "puntual"]
            assert updated["emojis"] is True
            assert updated["voice_id"] == "voice-123"
    finally:
        if otro_user_id is not None:
            await _delete_user(otro_user_id)
        await _cleanup(tenant_id, user_id)


# ---------------------------------------------------------------------------
# conversaciones / mensajes
# ---------------------------------------------------------------------------


async def test_conversations_and_messages(db) -> None:
    from edecan_db.session import get_session

    sufijo = uuid4().hex[:8]
    tenant_id, user_id = await _seed_tenant_y_usuario(sufijo)
    try:
        async with get_session(tenant_id) as session:
            repo = SqlRepo(session)

            sin_titulo = await repo.create_conversation(
                tenant_id=tenant_id, user_id=user_id, title=None, channel="web"
            )
            # `title` es NOT NULL sin default aplicable aquí (columna sí va en
            # el INSERT): un `None` debe sustituirse por "" (ver repo.py).
            assert sin_titulo["title"] == ""
            assert sin_titulo["title_source"] == "auto_pending"
            assert sin_titulo["channel"] == "web"

            con_titulo = await repo.create_conversation(
                tenant_id=tenant_id, user_id=user_id, title="Plan de viaje", channel="voice"
            )
            assert con_titulo["title"] == "Plan de viaje"
            assert con_titulo["title_source"] == "manual"

            listado = await repo.list_conversations(tenant_id=tenant_id, user_id=user_id)
            assert {c["id"] for c in listado} == {sin_titulo["id"], con_titulo["id"]}

            fetched = await repo.get_conversation(
                tenant_id=tenant_id,
                user_id=user_id,
                conversation_id=con_titulo["id"],
            )
            assert fetched is not None
            assert fetched["id"] == con_titulo["id"]

            m1 = await repo.add_message(
                tenant_id=tenant_id, conversation_id=con_titulo["id"], role="user", content="hola"
            )
            m2 = await repo.add_message(
                tenant_id=tenant_id,
                conversation_id=con_titulo["id"],
                role="assistant",
                content={"text": "¡hola! ¿en qué ayudo?"},
                tool_calls=[{"id": "call_1", "name": "hora_actual", "arguments": {}}],
                tokens_in=12,
                tokens_out=8,
            )
            m3 = await repo.add_message(
                tenant_id=tenant_id,
                conversation_id=con_titulo["id"],
                role="user",
                content="gracias",
            )
            assert m1["content"] == "hola"
            assert m2["tool_calls"] == [{"id": "call_1", "name": "hora_actual", "arguments": {}}]
            assert m2["tokens_in"] == 12
            assert m2["tokens_out"] == 8
            assert m3["tool_calls"] is None

            # `add_message` también refresca `conversations.updated_at`.
            conv_after = await repo.get_conversation(
                tenant_id=tenant_id,
                user_id=user_id,
                conversation_id=con_titulo["id"],
            )
            assert conv_after is not None
            assert conv_after["updated_at"] >= con_titulo["updated_at"]

            # `list_messages(limit=2)` debe traer los 2 MÁS RECIENTES (m2, m3)
            # reordenados ascendente — nunca los 2 más viejos (m1, m2).
            ultimos_dos = await repo.list_messages(
                tenant_id=tenant_id, conversation_id=con_titulo["id"], limit=2
            )
            assert [row["id"] for row in ultimos_dos] == [m2["id"], m3["id"]]

            todos = await repo.list_messages(
                tenant_id=tenant_id, conversation_id=con_titulo["id"], limit=50
            )
            assert [row["id"] for row in todos] == [m1["id"], m2["id"], m3["id"]]

            assert await repo.delete_conversation(
                tenant_id=tenant_id,
                user_id=user_id,
                conversation_id=sin_titulo["id"],
            )
            assert (
                await repo.get_conversation(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    conversation_id=sin_titulo["id"],
                )
                is None
            )
            assert not await repo.delete_conversation(
                tenant_id=tenant_id,
                user_id=user_id,
                conversation_id=sin_titulo["id"],
            )
    finally:
        await _cleanup(tenant_id, user_id)


# ---------------------------------------------------------------------------
# agentes y snapshots de llamadas
# ---------------------------------------------------------------------------


async def test_phone_agent_templates_and_call_snapshot(db) -> None:
    from edecan_db.session import get_session

    sufijo = uuid4().hex[:8]
    tenant_id, user_id = await _seed_tenant_y_usuario(sufijo)
    try:
        async with get_session(tenant_id) as session:
            repo = SqlRepo(session)
            conversation = await repo.create_conversation(
                tenant_id=tenant_id,
                user_id=user_id,
                title="Llamada de seguimiento",
                channel="phone",
            )
            template = await repo.create_phone_agent_template(
                tenant_id=tenant_id,
                user_id=user_id,
                name="Seguimiento",
                agent_name="Sara",
                persona_prompt="Habla con calma y confirma los datos.",
                default_goal="Confirmar la cita.",
                opening_message="Te llamo para confirmar tu próxima cita.",
                is_default=True,
            )

            assert (
                await repo.get_default_phone_agent_template(
                    tenant_id=tenant_id, user_id=user_id
                )
            )["id"] == template["id"]
            call = await repo.create_phone_call(
                tenant_id=tenant_id,
                user_id=user_id,
                conversation_id=conversation["id"],
                direction="outgoing",
                from_e164="+573001111111",
                to_e164="+573002222222",
                goal=template["default_goal"],
                agent_template_id=template["id"],
                agent_template_name=template["name"],
                agent_name=template["agent_name"],
                agent_prompt=template["persona_prompt"],
                opening_message=template["opening_message"],
            )

            await repo.update_phone_agent_template(
                tenant_id=tenant_id,
                template_id=template["id"],
                fields={"agent_name": "Nombre nuevo", "persona_prompt": "Prompt nuevo"},
            )
            persisted = await repo.get_phone_call(tenant_id=tenant_id, call_id=call["id"])
            assert persisted is not None
            assert persisted["agent_name"] == "Sara"
            assert persisted["agent_prompt"] == "Habla con calma y confirma los datos."

            structured_summary = {
                "status": "completed",
                "participants": [],
                "duration_seconds": 12,
                "key_points": ["Cita confirmada"],
                "commitments": [],
                "next_steps": ["Asistir a la cita"],
                "transcript": {"available": True, "turn_count": 2},
            }
            summarized = await repo.set_phone_call_summary_if_absent(
                tenant_id=tenant_id,
                call_id=call["id"],
                summary=structured_summary,
            )
            assert summarized is not None
            assert summarized["summary"] == structured_summary
            assert (
                await repo.set_phone_call_summary_if_absent(
                    tenant_id=tenant_id,
                    call_id=call["id"],
                    summary={"status": "failed"},
                )
                is None
            )

            assert await repo.delete_phone_agent_template(
                tenant_id=tenant_id, template_id=template["id"]
            )
            after_delete = await repo.get_phone_call(
                tenant_id=tenant_id, call_id=call["id"]
            )
            assert after_delete is not None
            assert after_delete["agent_template_id"] is None
            assert after_delete["agent_template_name"] == "Seguimiento"
            assert after_delete["opening_message"] == template["opening_message"]
    finally:
        await _cleanup(tenant_id, user_id)


# ---------------------------------------------------------------------------
# uso / cuotas
# ---------------------------------------------------------------------------


async def test_usage_events(db) -> None:
    from edecan_db.session import get_session

    sufijo = uuid4().hex[:8]
    tenant_id, user_id = await _seed_tenant_y_usuario(sufijo)
    try:
        desde = datetime.now(UTC) - timedelta(minutes=1)
        async with get_session(tenant_id) as session:
            repo = SqlRepo(session)
            await repo.add_usage_event(tenant_id=tenant_id, kind="llm_tokens", quantity=10)
            await repo.add_usage_event(
                tenant_id=tenant_id, kind="llm_tokens", quantity=5, meta={"model": "haiku"}
            )
            await repo.add_usage_event(tenant_id=tenant_id, kind="voice_seconds", quantity=30)

            total_llm = await repo.sum_usage_since(
                tenant_id=tenant_id, kind="llm_tokens", since=desde
            )
            assert total_llm == 15.0

            total_futuro = await repo.sum_usage_since(
                tenant_id=tenant_id,
                kind="voice_seconds",
                since=datetime.now(UTC) + timedelta(hours=1),
            )
            assert total_futuro == 0.0

            por_kind = await repo.sum_usage_by_kind_since(tenant_id=tenant_id, since=desde)
            assert por_kind == {"llm_tokens": 15.0, "voice_seconds": 30.0}

            todos_tenants = await repo.sum_usage_all_tenants_since(since=desde)
            propios = {
                row["kind"]: float(row["total"])
                for row in todos_tenants
                if row["tenant_id"] == tenant_id
            }
            assert propios == {"llm_tokens": 15.0, "voice_seconds": 30.0}
    finally:
        await _cleanup(tenant_id, user_id)


# ---------------------------------------------------------------------------
# memoria
# ---------------------------------------------------------------------------


async def test_memory_items(db) -> None:
    from edecan_db.session import get_session

    sufijo = uuid4().hex[:8]
    tenant_id, user_id = await _seed_tenant_y_usuario(sufijo)
    try:
        async with get_session(tenant_id) as session:
            repo = SqlRepo(session)

            alto = await repo.add_memory(
                tenant_id=tenant_id,
                user_id=user_id,
                kind="preference",
                content="el usuario ama el café por la mañana",
                importance=0.9,
                source="test",
            )
            bajo = await repo.add_memory(
                tenant_id=tenant_id,
                user_id=user_id,
                kind="fact",
                content="el usuario vive en Bogotá",
                importance=0.1,
                source="test",
            )
            # Hallazgo que motivó calcular el embedding aquí (ver docstring de
            # `add_memory`): nunca debe quedar NULL.
            assert alto["embedding"] is not None

            listado = await repo.list_memory(tenant_id=tenant_id, user_id=user_id, q=None, k=10)
            assert [row["id"] for row in listado] == [alto["id"], bajo["id"]]  # importance DESC

            solo_cafe = await repo.list_memory(tenant_id=tenant_id, user_id=user_id, q="café", k=10)
            assert [row["id"] for row in solo_cafe] == [alto["id"]]

            assert await repo.delete_memory(tenant_id=tenant_id, memory_id=bajo["id"])
            assert not await repo.delete_memory(tenant_id=tenant_id, memory_id=bajo["id"])
            quedan = await repo.list_memory(tenant_id=tenant_id, user_id=user_id, q=None, k=10)
            assert [row["id"] for row in quedan] == [alto["id"]]
    finally:
        await _cleanup(tenant_id, user_id)


# ---------------------------------------------------------------------------
# conectores
# ---------------------------------------------------------------------------


async def test_connector_accounts(db) -> None:
    from edecan_db.session import get_session

    sufijo = uuid4().hex[:8]
    tenant_a, user_a = await _seed_tenant_y_usuario(f"{sufijo}a")
    tenant_b, user_b = await _seed_tenant_y_usuario(f"{sufijo}b")
    try:
        async with get_session(tenant_a) as session:
            repo_a = SqlRepo(session)
            cuenta = await repo_a.create_connector_account(
                tenant_id=tenant_a,
                connector_key="google",
                external_account_id=f"google-acc-{sufijo}",
                display_name="Google (Gmail + Calendar)",
                scopes=["gmail.readonly", "calendar.readonly"],
            )
            assert cuenta["scopes"] == ["gmail.readonly", "calendar.readonly"]
            assert cuenta["status"] == "active"

            listado = await repo_a.list_connector_accounts(tenant_id=tenant_a)
            assert [row["id"] for row in listado] == [cuenta["id"]]

            fetched = await repo_a.get_connector_account(
                tenant_id=tenant_a, account_id=cuenta["id"]
            )
            assert fetched is not None
            assert fetched["id"] == cuenta["id"]

            # Aislamiento: filtrar por el tenant_id equivocado no debe
            # devolver la cuenta de otro tenant.
            assert (
                await repo_a.get_connector_account(tenant_id=tenant_b, account_id=cuenta["id"])
                is None
            )

        # `get_connector_account_by_external_id` exige sesión "plataforma"
        # (ver su docstring): cruza tenants a propósito.
        async with get_session(None) as session:
            repo_platform = SqlRepo(session)
            cross = await repo_platform.get_connector_account_by_external_id(
                connector_key="google", external_account_id=f"google-acc-{sufijo}"
            )
            assert cross is not None
            assert cross["id"] == cuenta["id"]
            assert (
                await repo_platform.get_connector_account_by_external_id(
                    connector_key="google", external_account_id="no-existe-" + sufijo
                )
                is None
            )

        async with get_session(tenant_a) as session:
            repo_a = SqlRepo(session)
            assert await repo_a.delete_connector_account(
                tenant_id=tenant_a, account_id=cuenta["id"]
            )
            assert not await repo_a.delete_connector_account(
                tenant_id=tenant_a, account_id=cuenta["id"]
            )
    finally:
        await _cleanup(tenant_a, user_a)
        await _cleanup(tenant_b, user_b)


# ---------------------------------------------------------------------------
# archivos
# ---------------------------------------------------------------------------


async def test_files(db) -> None:
    from edecan_db.session import get_session

    sufijo = uuid4().hex[:8]
    tenant_id, user_id = await _seed_tenant_y_usuario(sufijo)
    try:
        async with get_session(tenant_id) as session:
            repo = SqlRepo(session)

            auto = await repo.create_file(
                tenant_id=tenant_id,
                user_id=user_id,
                s3_key=f"tenants/{tenant_id}/files/auto/informe.pdf",
                filename="informe.pdf",
                mime="application/pdf",
                size_bytes=2048,
                status="uploaded",
            )
            assert auto["id"] is not None

            file_id = uuid4()
            explicito = await repo.create_file(
                tenant_id=tenant_id,
                user_id=user_id,
                s3_key=f"tenants/{tenant_id}/files/{file_id}/foto.png",
                filename="foto.png",
                mime="image/png",
                size_bytes=1024,
                status="ready",
                file_id=file_id,
            )
            assert explicito["id"] == file_id

            fetched = await repo.get_file(tenant_id=tenant_id, file_id=file_id)
            assert fetched is not None
            assert fetched["filename"] == "foto.png"

            listado = await repo.list_files(tenant_id=tenant_id)
            assert {row["id"] for row in listado} == {auto["id"], file_id}
    finally:
        await _cleanup(tenant_id, user_id)


# ---------------------------------------------------------------------------
# recordatorios
# ---------------------------------------------------------------------------


async def test_reminders(db) -> None:
    from edecan_db.session import get_session

    sufijo = uuid4().hex[:8]
    tenant_id, user_id = await _seed_tenant_y_usuario(sufijo)
    try:
        async with get_session(tenant_id) as session:
            repo = SqlRepo(session)

            due = datetime.now(UTC) + timedelta(days=1)
            creado = await repo.create_reminder(
                tenant_id=tenant_id,
                user_id=user_id,
                fields={"due_at": due, "message": "Llamar al cliente"},
            )
            assert creado["channel"] == "web"
            assert creado["status"] == "pending"
            assert creado["rrule"] is None

            listado = await repo.list_reminders(tenant_id=tenant_id, user_id=user_id)
            assert [row["id"] for row in listado] == [creado["id"]]

            fetched = await repo.get_reminder(tenant_id=tenant_id, reminder_id=creado["id"])
            assert fetched is not None

            actualizado = await repo.update_reminder(
                tenant_id=tenant_id,
                reminder_id=creado["id"],
                fields={"status": "sent", "message": "Cliente contactado", "no_es_columna": "x"},
            )
            assert actualizado is not None
            assert actualizado["status"] == "sent"
            assert actualizado["message"] == "Cliente contactado"

            # `fields` sin ninguna clave permitida (tras filtrar): rama
            # `not clean` — devuelve la fila sin tocarla.
            sin_cambios = await repo.update_reminder(
                tenant_id=tenant_id, reminder_id=creado["id"], fields={"no_es_columna": "x"}
            )
            assert sin_cambios is not None
            assert sin_cambios["status"] == "sent"

            assert await repo.delete_reminder(tenant_id=tenant_id, reminder_id=creado["id"])
            assert not await repo.delete_reminder(tenant_id=tenant_id, reminder_id=creado["id"])
    finally:
        await _cleanup(tenant_id, user_id)


# ---------------------------------------------------------------------------
# contactos
# ---------------------------------------------------------------------------


async def test_contacts(db) -> None:
    from edecan_db.session import get_session

    sufijo = uuid4().hex[:8]
    tenant_id, user_id = await _seed_tenant_y_usuario(sufijo)
    try:
        async with get_session(tenant_id) as session:
            repo = SqlRepo(session)

            creado = await repo.create_contact(
                tenant_id=tenant_id,
                user_id=user_id,
                fields={
                    "nombre": f"Ana Pérez {sufijo}",
                    "emails": ["ana@example.com"],
                    "phones": ["+525512345678"],
                    "empresa": "Acme",
                    "tags": ["vip"],
                },
            )
            assert creado["emails"] == ["ana@example.com"]
            assert creado["notas"] == ""

            otro = await repo.create_contact(
                tenant_id=tenant_id, user_id=user_id, fields={"nombre": f"Beto Ruiz {sufijo}"}
            )
            assert otro["emails"] == []

            listado = await repo.list_contacts(tenant_id=tenant_id, user_id=user_id, q=None)
            assert {row["id"] for row in listado} == {creado["id"], otro["id"]}

            filtrado = await repo.list_contacts(tenant_id=tenant_id, user_id=user_id, q="ana")
            assert [row["id"] for row in filtrado] == [creado["id"]]

            fetched = await repo.get_contact(tenant_id=tenant_id, contact_id=creado["id"])
            assert fetched is not None

            actualizado = await repo.update_contact(
                tenant_id=tenant_id,
                contact_id=creado["id"],
                fields={
                    "empresa": "Beta Corp",
                    "tags": ["vip", "prioritario"],
                    "notas": "Cliente clave",
                },
            )
            assert actualizado is not None
            assert actualizado["empresa"] == "Beta Corp"
            assert actualizado["tags"] == ["vip", "prioritario"]
            assert actualizado["notas"] == "Cliente clave"

            # `fields` sin ninguna clave permitida (tras filtrar): rama
            # `not clean` — devuelve la fila sin tocarla, sin llegar al UPDATE.
            sin_cambios = await repo.update_contact(
                tenant_id=tenant_id, contact_id=creado["id"], fields={"no_es_columna": "x"}
            )
            assert sin_cambios is not None
            assert sin_cambios["empresa"] == "Beta Corp"

            assert await repo.delete_contact(tenant_id=tenant_id, contact_id=otro["id"])
            assert not await repo.delete_contact(tenant_id=tenant_id, contact_id=otro["id"])
    finally:
        await _cleanup(tenant_id, user_id)


# ---------------------------------------------------------------------------
# finanzas
# ---------------------------------------------------------------------------


async def test_transactions_and_finance_summary(db) -> None:
    from edecan_db.session import get_session

    sufijo = uuid4().hex[:8]
    tenant_id, user_id = await _seed_tenant_y_usuario(sufijo)
    mes = "2031-03"  # mes lejano, improbable de colisionar con otra corrida
    try:
        async with get_session(tenant_id) as session:
            repo = SqlRepo(session)

            ingreso = await repo.create_transaction(
                tenant_id=tenant_id,
                user_id=user_id,
                fields={
                    "fecha": date(2031, 3, 5),
                    "monto": Decimal("1500.00"),
                    "categoria": "salario",
                },
            )
            assert ingreso["moneda"] == "USD"
            assert ingreso["categoria"] == "salario"

            gasto = await repo.create_transaction(
                tenant_id=tenant_id,
                user_id=user_id,
                fields={
                    "fecha": date(2031, 3, 10),
                    "monto": Decimal("-200.50"),
                    "categoria": "renta",
                    "descripcion": "Renta de marzo",
                    "cuenta": "banco-x",
                },
            )
            otro_mes = await repo.create_transaction(
                tenant_id=tenant_id,
                user_id=user_id,
                fields={"fecha": date(2031, 4, 1), "monto": Decimal("-10.00")},
            )
            assert otro_mes["categoria"] == "sin_categoria"

            del_mes = await repo.list_transactions(tenant_id=tenant_id, user_id=user_id, mes=mes)
            assert {row["id"] for row in del_mes} == {ingreso["id"], gasto["id"]}

            todas = await repo.list_transactions(tenant_id=tenant_id, user_id=user_id, mes=None)
            assert {row["id"] for row in todas} == {ingreso["id"], gasto["id"], otro_mes["id"]}

            fetched = await repo.get_transaction(tenant_id=tenant_id, transaction_id=gasto["id"])
            assert fetched is not None
            assert Decimal(str(fetched["monto"])) == Decimal("-200.50")

            actualizado = await repo.update_transaction(
                tenant_id=tenant_id,
                transaction_id=gasto["id"],
                fields={"monto": Decimal("-250.00"), "fecha": date(2031, 3, 11)},
            )
            assert actualizado is not None
            assert Decimal(str(actualizado["monto"])) == Decimal("-250.00")

            # `fields` sin ninguna clave permitida (tras filtrar): rama
            # `not clean` — devuelve la fila sin tocarla, sin llegar al UPDATE.
            sin_cambios = await repo.update_transaction(
                tenant_id=tenant_id, transaction_id=gasto["id"], fields={"no_es_columna": "x"}
            )
            assert sin_cambios is not None
            assert Decimal(str(sin_cambios["monto"])) == Decimal("-250.00")

            resumen = await repo.finance_summary(tenant_id=tenant_id, user_id=user_id, mes=mes)
            assert Decimal(str(resumen["ingresos"])) == Decimal("1500.00")
            assert Decimal(str(resumen["gastos"])) == Decimal("-250.00")
            assert Decimal(str(resumen["neto"])) == Decimal("1250.00")
            assert int(resumen["num_transacciones"]) == 2
            por_categoria = {
                row["categoria"]: Decimal(str(row["total"])) for row in resumen["por_categoria"]
            }
            assert por_categoria == {"salario": Decimal("1500.00"), "renta": Decimal("-250.00")}
            assert resumen["mes"] == mes

            assert await repo.delete_transaction(tenant_id=tenant_id, transaction_id=otro_mes["id"])
            assert not await repo.delete_transaction(
                tenant_id=tenant_id, transaction_id=otro_mes["id"]
            )
    finally:
        await _cleanup(tenant_id, user_id)


# ---------------------------------------------------------------------------
# billing
# ---------------------------------------------------------------------------


async def test_subscriptions(db) -> None:
    from edecan_db.session import get_session

    sufijo = uuid4().hex[:8]
    tenant_id, user_id = await _seed_tenant_y_usuario(sufijo)
    try:
        # Igual que `billing.py`: siempre por la sesión "plataforma"
        # (`get_platform_repo`), el webhook de Stripe no tiene JWT de tenant.
        async with get_session(None) as session:
            repo = SqlRepo(session)

            periodo = datetime.now(UTC) + timedelta(days=30)
            creada = await repo.upsert_subscription(
                tenant_id=tenant_id,
                fields={
                    "stripe_customer_id": f"cus_{sufijo}",
                    "stripe_subscription_id": f"sub_{sufijo}",
                    "plan_key": "hosted_pro",
                    "status": "active",
                    "current_period_end": periodo,
                },
            )
            assert creada["stripe_customer_id"] == f"cus_{sufijo}"
            assert creada["status"] == "active"

            por_customer = await repo.get_subscription_by_stripe_customer(f"cus_{sufijo}")
            assert por_customer is not None
            assert por_customer["id"] == creada["id"]

            por_subscription = await repo.get_subscription_by_stripe_subscription(f"sub_{sufijo}")
            assert por_subscription is not None
            assert por_subscription["id"] == creada["id"]

            assert await repo.get_subscription_by_stripe_customer("no-existe-" + sufijo) is None

            # Segunda llamada con el MISMO tenant_id: rama UPDATE (parcial —
            # solo status/current_period_end, igual que
            # `_handle_subscription_updated` en billing.py).
            nuevo_periodo = periodo + timedelta(days=30)
            actualizada = await repo.upsert_subscription(
                tenant_id=tenant_id,
                fields={"status": "past_due", "current_period_end": nuevo_periodo},
            )
            assert actualizada["id"] == creada["id"]
            assert actualizada["status"] == "past_due"
            assert actualizada["stripe_customer_id"] == f"cus_{sufijo}"  # no tocado

            # `fields` sin ninguna clave permitida "de verdad" (valor `None`
            # se filtra): rama `not clean` — devuelve la fila sin tocarla.
            sin_cambios = await repo.upsert_subscription(
                tenant_id=tenant_id, fields={"status": None}
            )
            assert sin_cambios["status"] == "past_due"
    finally:
        await _cleanup(tenant_id, user_id)


# ---------------------------------------------------------------------------
# auditoría
# ---------------------------------------------------------------------------


async def test_audit_log(db) -> None:
    from edecan_db.session import get_session
    from sqlalchemy import text

    sufijo = uuid4().hex[:8]
    tenant_id, user_id = await _seed_tenant_y_usuario(sufijo)
    try:
        async with get_session(tenant_id) as session:
            repo = SqlRepo(session)
            await repo.add_audit_log(
                tenant_id=tenant_id,
                actor_user_id=user_id,
                action="connectors.connected",
                target=f"google:{sufijo}",
                meta={"scopes": ["gmail.readonly"]},
            )
            rows = (
                (
                    await session.execute(
                        text("SELECT action, meta FROM audit_log WHERE target = :t"),
                        {"t": f"google:{sufijo}"},
                    )
                )
                .mappings()
                .all()
            )
            assert len(rows) == 1
            assert rows[0]["action"] == "connectors.connected"
            assert rows[0]["meta"] == {"scopes": ["gmail.readonly"]}

        # Evento de plataforma (p. ej. webhook de Stripe): tenant_id/
        # actor_user_id NULL son válidos (§10.3) — pero solo insertables desde
        # la sesión "dueño" (bypassa RLS): la política `tenant_isolation` de
        # `audit_log` (`tenant_id = current_setting('app.tenant_id')::uuid`)
        # rechazaría un `tenant_id` NULL bajo una sesión de tenant con RLS
        # activa (NULL = uuid nunca es TRUE).
        async with get_session(None) as session:
            repo_platform = SqlRepo(session)
            await repo_platform.add_audit_log(
                tenant_id=None,
                actor_user_id=None,
                action="billing.webhook_sin_tenant",
                target=f"evt_{sufijo}",
            )
            rows = (
                (
                    await session.execute(
                        text("SELECT action, tenant_id FROM audit_log WHERE target = :t"),
                        {"t": f"evt_{sufijo}"},
                    )
                )
                .mappings()
                .all()
            )
            assert len(rows) == 1
            assert rows[0]["action"] == "billing.webhook_sin_tenant"
            assert rows[0]["tenant_id"] is None
    finally:
        await _cleanup(tenant_id, user_id)


# ---------------------------------------------------------------------------
# vista remota (remote_sessions, WP-V2-09 — hallazgo de auditoría
# consistencia-arquitectura: `_RemoteSessionStore` en memoria sustituía a
# esta tabla pese a que la migración `0003_v2_expansion` ya la crea; ahora
# `routers.remote` persiste de verdad vía estos métodos de `Repo`)
# ---------------------------------------------------------------------------


async def test_remote_sessions(db) -> None:
    from edecan_db.session import get_session

    sufijo = uuid4().hex[:8]
    tenant_id, user_id = await _seed_tenant_y_usuario(sufijo)
    try:
        async with get_session(tenant_id) as session:
            repo = SqlRepo(session)

            creada = await repo.create_remote_session(tenant_id=tenant_id, user_id=user_id)
            assert creada["tenant_id"] == tenant_id
            assert creada["user_id"] == user_id
            assert creada["device_id"] is None
            assert creada["kind"] == "view"
            assert creada["status"] == "pending"
            assert creada["started_at"] is None
            assert creada["ended_at"] is None
            assert creada["frames_count"] == 0
            session_id = creada["id"]

            fetched = await repo.get_remote_session(tenant_id=tenant_id, session_id=session_id)
            assert fetched is not None
            assert fetched["id"] == session_id
            assert await repo.get_remote_session(tenant_id=tenant_id, session_id=uuid4()) is None

            listado = await repo.list_remote_sessions(tenant_id=tenant_id)
            assert [row["id"] for row in listado] == [session_id]

            # Primer frame: pending -> active, fija started_at, frames_count=1.
            primer_frame = await repo.record_remote_session_frame(
                tenant_id=tenant_id, session_id=session_id
            )
            assert primer_frame["status"] == "active"
            assert primer_frame["started_at"] is not None
            assert primer_frame["frames_count"] == 1

            # Segundo frame: sigue active, NO reinicia started_at, incrementa.
            segundo_frame = await repo.record_remote_session_frame(
                tenant_id=tenant_id, session_id=session_id
            )
            assert segundo_frame["status"] == "active"
            assert segundo_frame["started_at"] == primer_frame["started_at"]
            assert segundo_frame["frames_count"] == 2

            terminada = await repo.mark_remote_session_ended(
                tenant_id=tenant_id, session_id=session_id
            )
            assert terminada["status"] == "ended"
            assert terminada["ended_at"] is not None
            assert terminada["frames_count"] == 2  # no lo toca

            # Idempotente: terminar de nuevo no mueve `ended_at`.
            terminada_otra_vez = await repo.mark_remote_session_ended(
                tenant_id=tenant_id, session_id=session_id
            )
            assert terminada_otra_vez["ended_at"] == terminada["ended_at"]

            # Sesión aparte para el camino "denegada" (pending -> denied).
            para_denegar = await repo.create_remote_session(tenant_id=tenant_id, user_id=user_id)
            denegada = await repo.mark_remote_session_denied(
                tenant_id=tenant_id, session_id=para_denegar["id"]
            )
            assert denegada["status"] == "denied"

            listado_final = await repo.list_remote_sessions(tenant_id=tenant_id)
            assert {row["id"] for row in listado_final} == {session_id, para_denegar["id"]}
    finally:
        await _cleanup(tenant_id, user_id)
