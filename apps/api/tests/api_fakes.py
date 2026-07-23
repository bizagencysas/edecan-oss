"""Fakes en memoria para los tests de `edecan_api` (ARCHITECTURE.md §10.1: "los
tests no importan paquetes hermanos: usan stubs/fakes que implementen los
contratos de esta sección").

`FakeRepo` implementa el `Protocol` `edecan_api.repo.Repo` (propio de este
paquete, no un hermano) enteramente con diccionarios en memoria — nunca toca
Postgres. `FakeRedis` implementa el puñado de comandos de `redis.asyncio.Redis`
que usa `edecan_api` (`incr`, `expire`, `set`, `get`, `delete`).
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

Row = dict[str, Any]


def _now() -> datetime:
    return datetime.now(UTC)


class FakeRepo:
    """Implementación en memoria de `edecan_api.repo.Repo`."""

    def __init__(self) -> None:
        self.tenants: dict[uuid.UUID, Row] = {}
        self.users: dict[uuid.UUID, Row] = {}
        self.memberships: list[Row] = []
        self.local_owner: tuple[uuid.UUID, uuid.UUID] | None = None
        self.personas: dict[tuple[uuid.UUID, uuid.UUID], Row] = {}
        self.conversations: dict[uuid.UUID, Row] = {}
        self.messages: dict[uuid.UUID, list[Row]] = {}
        self.phone_agent_templates: dict[uuid.UUID, Row] = {}
        self.phone_calls: dict[uuid.UUID, Row] = {}
        self.phone_call_events: dict[uuid.UUID, list[Row]] = {}
        self.phone_consents: list[Row] = []
        self.usage_events: list[Row] = []
        self.memory_items: dict[uuid.UUID, Row] = {}
        self.connector_accounts: dict[uuid.UUID, Row] = {}
        self.files: dict[uuid.UUID, Row] = {}
        self.reminders: dict[uuid.UUID, Row] = {}
        self.contacts: dict[uuid.UUID, Row] = {}
        self.transactions: dict[uuid.UUID, Row] = {}
        self.subscriptions: dict[uuid.UUID, Row] = {}
        self.audit_log: list[Row] = []
        self.remote_sessions: dict[uuid.UUID, Row] = {}

    # -- tenants / users / memberships ------------------------------------

    async def create_tenant(self, *, name: str, slug: str, plan_key: str) -> Row:
        row: Row = {
            "id": uuid.uuid4(),
            "name": name,
            "slug": slug,
            "plan_key": plan_key,
            "status": "active",
            "onboarding_completed_at": None,
            "lifetime_updates_purchased_at": None,
            "created_at": _now(),
            "updated_at": _now(),
        }
        self.tenants[row["id"]] = row
        return dict(row)

    async def get_tenant(self, tenant_id: uuid.UUID) -> Row | None:
        row = self.tenants.get(tenant_id)
        return dict(row) if row else None

    async def get_tenant_by_slug(self, slug: str) -> Row | None:
        for row in self.tenants.values():
            if row["slug"] == slug:
                return dict(row)
        return None

    async def update_tenant_plan(self, tenant_id: uuid.UUID, plan_key: str) -> None:
        if tenant_id in self.tenants:
            self.tenants[tenant_id]["plan_key"] = plan_key
            self.tenants[tenant_id]["updated_at"] = _now()

    async def update_tenant_onboarding_completed(self, tenant_id: uuid.UUID) -> None:
        if tenant_id in self.tenants:
            self.tenants[tenant_id]["onboarding_completed_at"] = _now()
            self.tenants[tenant_id]["updated_at"] = _now()

    async def update_tenant_lifetime_updates(self, tenant_id: uuid.UUID) -> None:
        if tenant_id in self.tenants:
            self.tenants[tenant_id]["lifetime_updates_purchased_at"] = _now()
            self.tenants[tenant_id]["updated_at"] = _now()

    async def list_tenants(self, *, limit: int = 200) -> list[Row]:
        return [dict(r) for r in list(self.tenants.values())[:limit]]

    async def create_user(self, *, email: str, password_hash: str) -> Row:
        row: Row = {
            "id": uuid.uuid4(),
            "email": email.strip().lower(),
            "password_hash": password_hash,
            "totp_secret": None,
            "is_superadmin": False,
            "created_at": _now(),
            "updated_at": _now(),
        }
        self.users[row["id"]] = row
        return dict(row)

    async def get_user(self, user_id: uuid.UUID) -> Row | None:
        row = self.users.get(user_id)
        return dict(row) if row else None

    async def get_user_by_email(self, email: str) -> Row | None:
        target = email.strip().lower()
        for row in self.users.values():
            if row["email"] == target:
                return dict(row)
        return None

    async def set_user_totp_secret(self, user_id: uuid.UUID, secret: str | None) -> None:
        if user_id in self.users:
            self.users[user_id]["totp_secret"] = secret

    async def update_user_password_hash(self, user_id: uuid.UUID, password_hash: str) -> None:
        if user_id in self.users:
            self.users[user_id]["password_hash"] = password_hash

    async def create_membership(
        self, *, user_id: uuid.UUID, tenant_id: uuid.UUID, role: str
    ) -> Row:
        row: Row = {
            "id": uuid.uuid4(),
            "user_id": user_id,
            "tenant_id": tenant_id,
            "role": role,
            "created_at": _now(),
            "updated_at": _now(),
        }
        self.memberships.append(row)
        return dict(row)

    async def get_membership(self, *, user_id: uuid.UUID, tenant_id: uuid.UUID) -> Row | None:
        for row in self.memberships:
            if row["user_id"] == user_id and row["tenant_id"] == tenant_id:
                return dict(row)
        return None

    async def get_first_membership_for_user(self, user_id: uuid.UUID) -> Row | None:
        matches = sorted(
            (r for r in self.memberships if r["user_id"] == user_id), key=lambda r: r["created_at"]
        )
        return dict(matches[0]) if matches else None

    async def get_first_user_id_for_tenant(self, tenant_id: uuid.UUID) -> uuid.UUID | None:
        matches = sorted(
            (row for row in self.memberships if row["tenant_id"] == tenant_id),
            key=lambda row: row["created_at"],
        )
        return matches[0]["user_id"] if matches else None

    async def get_first_active_owner(self) -> Row | None:
        matches = sorted(
            (
                membership
                for membership in self.memberships
                if membership["role"] == "owner"
                and self.tenants.get(membership["tenant_id"], {}).get("status") == "active"
            ),
            key=lambda row: row["created_at"],
        )
        if not matches:
            return None
        membership = matches[0]
        user = self.users[membership["user_id"]]
        tenant = self.tenants[membership["tenant_id"]]
        return {
            "user_id": user["id"],
            "email": user["email"],
            "tenant_id": tenant["id"],
            "plan_key": tenant["plan_key"],
            "owner_count": len(matches),
        }

    async def get_local_owner(self) -> Row | None:
        if self.local_owner is None:
            return None
        user_id, tenant_id = self.local_owner
        user = self.users.get(user_id)
        tenant = self.tenants.get(tenant_id)
        membership = next(
            (
                row
                for row in self.memberships
                if row["user_id"] == user_id
                and row["tenant_id"] == tenant_id
                and row["role"] == "owner"
            ),
            None,
        )
        if user is None or tenant is None or membership is None or tenant["status"] != "active":
            return None
        return {
            "user_id": user_id,
            "email": user["email"],
            "tenant_id": tenant_id,
            "plan_key": tenant["plan_key"],
        }

    async def set_local_owner(self, *, user_id: uuid.UUID, tenant_id: uuid.UUID) -> Row:
        if self.local_owner is None:
            self.local_owner = (user_id, tenant_id)
        owner = await self.get_local_owner()
        assert owner is not None
        return owner

    # -- personas -----------------------------------------------------------

    async def create_persona_default(self, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> Row:
        row: Row = {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "user_id": user_id,
            "nombre_asistente": "Edecán",
            "idioma": "es",
            "tono": "cálido y profesional",
            "formalidad": 1,
            "emojis": False,
            "instrucciones": "",
            "rasgos": [],
            "memoria_activada": True,
            "voice_id": None,
            "estilo_relacion": "profesional",
            "adulto_confirmado": False,
            "consentimiento_romantico": False,
            "created_at": _now(),
            "updated_at": _now(),
        }
        self.personas[(tenant_id, user_id)] = row
        return dict(row)

    async def get_persona(self, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> Row | None:
        row = self.personas.get((tenant_id, user_id))
        return dict(row) if row else None

    async def upsert_persona(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, fields: dict[str, Any]
    ) -> Row:
        key = (tenant_id, user_id)
        if key not in self.personas:
            await self.create_persona_default(tenant_id=tenant_id, user_id=user_id)
        self.personas[key].update(fields)
        self.personas[key]["updated_at"] = _now()
        return dict(self.personas[key])

    # -- conversaciones / mensajes -------------------------------------------

    async def create_conversation(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, title: str | None, channel: str
    ) -> Row:
        row: Row = {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "user_id": user_id,
            "title": title,
            "title_source": "manual" if title and title.strip() else "auto_pending",
            "channel": channel,
            "created_at": _now(),
            "updated_at": _now(),
        }
        self.conversations[row["id"]] = row
        self.messages[row["id"]] = []
        return dict(row)

    async def list_conversations(self, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> list[Row]:
        rows = [
            r
            for r in self.conversations.values()
            if r["tenant_id"] == tenant_id and r["user_id"] == user_id
        ]
        rows.sort(key=lambda r: r["updated_at"], reverse=True)
        return [dict(r) for r in rows]

    async def list_conversation_title_refresh_candidates(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[Row]:
        candidates: list[Row] = []
        for row in self.conversations.values():
            if row["tenant_id"] != tenant_id or row["user_id"] != user_id:
                continue
            if row.get("title_source", "legacy") not in {"legacy", "auto"}:
                continue
            first_user = next(
                (
                    message.get("content")
                    for message in self.messages.get(row["id"], [])
                    if message.get("role") == "user"
                ),
                None,
            )
            candidates.append(
                {
                    "id": row["id"],
                    "title": row.get("title", ""),
                    "title_source": row.get("title_source", "legacy"),
                    "first_user_content": first_user,
                }
            )
        return candidates

    # -- llamadas como canal conversacional -----------------------------------

    async def create_phone_agent_template(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        name: str,
        agent_name: str,
        persona_prompt: str,
        default_goal: str,
        opening_message: str,
        is_default: bool,
        knowledge_context: str = "",
        required_information: str = "",
    ) -> Row:
        row: Row = {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "user_id": user_id,
            "name": name,
            "agent_name": agent_name,
            "persona_prompt": persona_prompt,
            "default_goal": default_goal,
            "opening_message": opening_message,
            "knowledge_context": knowledge_context,
            "required_information": required_information,
            "is_default": is_default,
            "created_at": _now(),
            "updated_at": _now(),
        }
        self.phone_agent_templates[row["id"]] = row
        return dict(row)

    async def list_phone_agent_templates(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[Row]:
        rows = [
            row
            for row in self.phone_agent_templates.values()
            if row["tenant_id"] == tenant_id and row["user_id"] == user_id
        ]
        rows.sort(key=lambda row: (not row["is_default"], row["created_at"], row["id"]))
        return [dict(row) for row in rows]

    async def get_phone_agent_template(
        self, *, tenant_id: uuid.UUID, template_id: uuid.UUID
    ) -> Row | None:
        row = self.phone_agent_templates.get(template_id)
        return dict(row) if row is not None and row["tenant_id"] == tenant_id else None

    async def get_default_phone_agent_template(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID
    ) -> Row | None:
        rows = await self.list_phone_agent_templates(tenant_id=tenant_id, user_id=user_id)
        return next((row for row in rows if row["is_default"]), None)

    async def update_phone_agent_template(
        self,
        *,
        tenant_id: uuid.UUID,
        template_id: uuid.UUID,
        fields: dict[str, Any],
    ) -> Row | None:
        row = self.phone_agent_templates.get(template_id)
        if row is None or row["tenant_id"] != tenant_id:
            return None
        row.update(fields)
        row["updated_at"] = _now()
        return dict(row)

    async def clear_default_phone_agent_template(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, except_id: uuid.UUID | None = None
    ) -> None:
        for row in self.phone_agent_templates.values():
            if (
                row["tenant_id"] == tenant_id
                and row["user_id"] == user_id
                and row["id"] != except_id
            ):
                row["is_default"] = False
                row["updated_at"] = _now()

    async def delete_phone_agent_template(
        self, *, tenant_id: uuid.UUID, template_id: uuid.UUID
    ) -> bool:
        row = self.phone_agent_templates.get(template_id)
        if row is None or row["tenant_id"] != tenant_id:
            return False
        del self.phone_agent_templates[template_id]
        for call in self.phone_calls.values():
            if call.get("agent_template_id") == template_id:
                call["agent_template_id"] = None
        return True

    async def create_phone_call(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        direction: str,
        from_e164: str,
        to_e164: str,
        goal: str,
        status: str = "draft",
        provider_call_sid: str | None = None,
        agent_template_id: uuid.UUID | None = None,
        agent_template_name: str | None = None,
        agent_name: str | None = None,
        agent_prompt: str | None = None,
        opening_message: str | None = None,
    ) -> Row:
        row: Row = {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "user_id": user_id,
            "conversation_id": conversation_id,
            "direction": direction,
            "from_e164": from_e164,
            "to_e164": to_e164,
            "goal": goal,
            "agent_template_id": agent_template_id,
            "agent_template_name": agent_template_name,
            "agent_name": agent_name,
            "agent_prompt": agent_prompt,
            "opening_message": opening_message,
            "status": status,
            "provider": "twilio",
            "provider_call_sid": provider_call_sid,
            "confirmed_at": None,
            "started_at": None,
            "ended_at": None,
            "duration_seconds": None,
            "error": None,
            "summary": None,
            "summary_generated_at": None,
            "summary_push_attempted_at": None,
            "created_at": _now(),
            "updated_at": _now(),
        }
        self.phone_calls[row["id"]] = row
        self.phone_call_events[row["id"]] = []
        return dict(row)

    async def list_phone_calls(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, limit: int = 50
    ) -> list[Row]:
        rows = [
            row
            for row in self.phone_calls.values()
            if row["tenant_id"] == tenant_id and row["user_id"] == user_id
        ]
        rows.sort(key=lambda row: row["created_at"], reverse=True)
        return [dict(row) for row in rows[:limit]]

    async def get_phone_call(self, *, tenant_id: uuid.UUID, call_id: uuid.UUID) -> Row | None:
        row = self.phone_calls.get(call_id)
        return dict(row) if row is not None and row["tenant_id"] == tenant_id else None

    async def get_phone_call_by_provider_sid(self, *, provider_call_sid: str) -> Row | None:
        for row in self.phone_calls.values():
            if row.get("provider_call_sid") == provider_call_sid:
                return dict(row)
        return None

    async def get_phone_call_global(self, *, call_id: uuid.UUID) -> Row | None:
        row = self.phone_calls.get(call_id)
        return dict(row) if row is not None else None

    async def update_phone_call(
        self, *, tenant_id: uuid.UUID, call_id: uuid.UUID, fields: dict[str, Any]
    ) -> Row | None:
        row = self.phone_calls.get(call_id)
        if row is None or row["tenant_id"] != tenant_id:
            return None
        row.update(fields)
        row["updated_at"] = _now()
        return dict(row)

    async def set_phone_call_summary_if_absent(
        self,
        *,
        tenant_id: uuid.UUID,
        call_id: uuid.UUID,
        summary: dict[str, Any],
    ) -> Row | None:
        row = self.phone_calls.get(call_id)
        if row is None or row["tenant_id"] != tenant_id or row.get("summary") is not None:
            return None
        now = _now()
        row["summary"] = summary
        row["summary_generated_at"] = now
        row["updated_at"] = now
        return dict(row)

    async def add_phone_call_event(
        self,
        *,
        tenant_id: uuid.UUID,
        call_id: uuid.UUID,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> Row:
        row: Row = {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "call_id": call_id,
            "event_type": event_type,
            "payload": payload or {},
            "occurred_at": _now(),
            "created_at": _now(),
            "updated_at": _now(),
        }
        self.phone_call_events.setdefault(call_id, []).append(row)
        return dict(row)

    async def list_phone_call_events(
        self, *, tenant_id: uuid.UUID, call_id: uuid.UUID
    ) -> list[Row]:
        return [
            dict(row)
            for row in self.phone_call_events.get(call_id, [])
            if row["tenant_id"] == tenant_id
        ]

    async def has_phone_consent(self, *, tenant_id: uuid.UUID, phone_e164: str, kind: str) -> bool:
        return any(
            row["tenant_id"] == tenant_id
            and row["phone_e164"] == phone_e164
            and row["kind"] == kind
            and row.get("revoked_at") is None
            for row in self.phone_consents
        )

    async def grant_phone_consent(
        self, *, tenant_id: uuid.UUID, phone_e164: str, kind: str, source: str
    ) -> Row:
        row: Row = {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "phone_e164": phone_e164,
            "kind": kind,
            "source": source,
            "granted_at": _now(),
            "revoked_at": None,
            "created_at": _now(),
            "updated_at": _now(),
        }
        self.phone_consents.append(row)
        return dict(row)

    async def get_conversation(
        self, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> Row | None:
        row = self.conversations.get(conversation_id)
        if row is not None and row["tenant_id"] == tenant_id:
            return dict(row)
        return None

    async def update_conversation_title(
        self,
        *,
        tenant_id: uuid.UUID,
        conversation_id: uuid.UUID,
        title: str,
        only_if_empty: bool = False,
        source: str | None = None,
    ) -> Row | None:
        row = self.conversations.get(conversation_id)
        if row is None or row["tenant_id"] != tenant_id:
            return None
        if (
            only_if_empty
            and str(row.get("title") or "").strip()
            and row.get("title_source") != "auto_pending"
        ):
            return None
        row["title"] = title
        if source is not None:
            row["title_source"] = source
        row["updated_at"] = _now()
        return dict(row)

    async def delete_conversation(
        self, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> bool:
        row = self.conversations.get(conversation_id)
        if row is not None and row["tenant_id"] == tenant_id:
            del self.conversations[conversation_id]
            self.messages.pop(conversation_id, None)
            return True
        return False

    async def add_message(
        self,
        *,
        tenant_id: uuid.UUID,
        conversation_id: uuid.UUID,
        role: str,
        content: Any,
        tool_calls: Any = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
    ) -> Row:
        row: Row = {
            "id": uuid.uuid4(),
            "conversation_id": conversation_id,
            "tenant_id": tenant_id,
            "role": role,
            "content": content,
            "tool_calls": tool_calls,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "created_at": _now(),
            "updated_at": _now(),
        }
        self.messages.setdefault(conversation_id, []).append(row)
        return dict(row)

    async def list_messages(
        self, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID, limit: int = 50
    ) -> list[Row]:
        rows = [r for r in self.messages.get(conversation_id, []) if r["tenant_id"] == tenant_id]
        return [dict(r) for r in rows[-limit:]]

    # -- uso / cuotas ---------------------------------------------------------

    async def add_usage_event(
        self,
        *,
        tenant_id: uuid.UUID,
        kind: str,
        quantity: float,
        meta: dict[str, Any] | None = None,
    ) -> None:
        self.usage_events.append(
            {
                "id": uuid.uuid4(),
                "tenant_id": tenant_id,
                "kind": kind,
                "quantity": quantity,
                "meta": meta or {},
                "created_at": _now(),
            }
        )

    async def sum_usage_since(self, *, tenant_id: uuid.UUID, kind: str, since: datetime) -> float:
        return sum(
            e["quantity"]
            for e in self.usage_events
            if e["tenant_id"] == tenant_id and e["kind"] == kind and e["created_at"] >= since
        )

    async def sum_usage_by_kind_since(
        self, *, tenant_id: uuid.UUID, since: datetime
    ) -> dict[str, float]:
        totals: dict[str, float] = {}
        for e in self.usage_events:
            if e["tenant_id"] == tenant_id and e["created_at"] >= since:
                totals[e["kind"]] = totals.get(e["kind"], 0.0) + e["quantity"]
        return totals

    async def sum_usage_all_tenants_since(self, *, since: datetime) -> list[Row]:
        totals: dict[tuple[uuid.UUID, str], float] = {}
        for e in self.usage_events:
            if e["created_at"] >= since:
                key = (e["tenant_id"], e["kind"])
                totals[key] = totals.get(key, 0.0) + e["quantity"]
        return [{"tenant_id": t, "kind": k, "total": v} for (t, k), v in totals.items()]

    # -- memoria --------------------------------------------------------------

    async def list_memory(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, q: str | None, k: int
    ) -> list[Row]:
        rows = [
            r
            for r in self.memory_items.values()
            if r["tenant_id"] == tenant_id
            and r["user_id"] == user_id
            and r.get("superseded_at") is None
        ]
        if q:
            rows = [r for r in rows if q.lower() in r["content"].lower()]
        rows.sort(key=lambda r: (r["importance"], r["created_at"]), reverse=True)
        return [dict(r) for r in rows[:k]]

    async def add_memory(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        kind: str,
        content: str,
        importance: float,
        source: str,
    ) -> Row:
        row: Row = {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "user_id": user_id,
            "kind": kind,
            "content": content,
            "importance": importance,
            "source": source,
            "created_at": _now(),
            "updated_at": _now(),
        }
        self.memory_items[row["id"]] = row
        return dict(row)

    async def delete_memory(self, *, tenant_id: uuid.UUID, memory_id: uuid.UUID) -> bool:
        row = self.memory_items.get(memory_id)
        if row is not None and row["tenant_id"] == tenant_id:
            del self.memory_items[memory_id]
            return True
        return False

    # -- conectores -------------------------------------------------------------

    async def list_connector_accounts(self, *, tenant_id: uuid.UUID) -> list[Row]:
        return [dict(r) for r in self.connector_accounts.values() if r["tenant_id"] == tenant_id]

    async def get_connector_account(
        self, *, tenant_id: uuid.UUID, account_id: uuid.UUID
    ) -> Row | None:
        row = self.connector_accounts.get(account_id)
        if row is not None and row["tenant_id"] == tenant_id:
            return dict(row)
        return None

    async def create_connector_account(
        self,
        *,
        tenant_id: uuid.UUID,
        connector_key: str,
        external_account_id: str,
        display_name: str,
        scopes: list[str],
    ) -> Row:
        row: Row = {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "connector_key": connector_key,
            "external_account_id": external_account_id,
            "display_name": display_name,
            "status": "active",
            "scopes": scopes,
            "created_at": _now(),
            "updated_at": _now(),
        }
        self.connector_accounts[row["id"]] = row
        return dict(row)

    async def get_connector_account_by_external_id(
        self, *, connector_key: str, external_account_id: str
    ) -> Row | None:
        # Sin filtro de tenant a propósito — espeja `SqlRepo` (que además
        # espera ejecutarse contra una sesión "plataforma", sin RLS, ver su
        # docstring): usado por `connect_twilio` para detectar si un número
        # de Twilio ya lo tiene conectado OTRO tenant.
        matches = [
            r
            for r in self.connector_accounts.values()
            if r["connector_key"] == connector_key
            and r["external_account_id"] == external_account_id
        ]
        if not matches:
            return None
        return dict(min(matches, key=lambda r: r["created_at"]))

    async def delete_connector_account(
        self, *, tenant_id: uuid.UUID, account_id: uuid.UUID
    ) -> bool:
        row = self.connector_accounts.get(account_id)
        if row is not None and row["tenant_id"] == tenant_id:
            del self.connector_accounts[account_id]
            return True
        return False

    # -- archivos -----------------------------------------------------------------

    async def create_file(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        s3_key: str,
        filename: str,
        mime: str,
        size_bytes: int,
        status: str,
        file_id: uuid.UUID | None = None,
    ) -> Row:
        row: Row = {
            "id": file_id or uuid.uuid4(),
            "tenant_id": tenant_id,
            "user_id": user_id,
            "s3_key": s3_key,
            "filename": filename,
            "mime": mime,
            "size_bytes": size_bytes,
            "status": status,
            "created_at": _now(),
            "updated_at": _now(),
        }
        self.files[row["id"]] = row
        return dict(row)

    async def get_file(self, *, tenant_id: uuid.UUID, file_id: uuid.UUID) -> Row | None:
        row = self.files.get(file_id)
        if row is not None and row["tenant_id"] == tenant_id:
            return dict(row)
        return None

    async def list_files(self, *, tenant_id: uuid.UUID) -> list[Row]:
        return [dict(r) for r in self.files.values() if r["tenant_id"] == tenant_id]

    # -- recordatorios --------------------------------------------------------------

    async def create_reminder(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, fields: dict[str, Any]
    ) -> Row:
        row: Row = {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "user_id": user_id,
            "due_at": fields.get("due_at"),
            "rrule": fields.get("rrule"),
            "message": fields.get("message"),
            "channel": fields.get("channel", "web"),
            "status": fields.get("status", "pending"),
            "created_at": _now(),
            "updated_at": _now(),
        }
        self.reminders[row["id"]] = row
        return dict(row)

    async def list_reminders(self, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> list[Row]:
        rows = [
            r
            for r in self.reminders.values()
            if r["tenant_id"] == tenant_id and r["user_id"] == user_id
        ]
        rows.sort(key=lambda r: r["due_at"])
        return [dict(r) for r in rows]

    async def get_reminder(self, *, tenant_id: uuid.UUID, reminder_id: uuid.UUID) -> Row | None:
        row = self.reminders.get(reminder_id)
        if row is not None and row["tenant_id"] == tenant_id:
            return dict(row)
        return None

    async def update_reminder(
        self, *, tenant_id: uuid.UUID, reminder_id: uuid.UUID, fields: dict[str, Any]
    ) -> Row | None:
        row = self.reminders.get(reminder_id)
        if row is None or row["tenant_id"] != tenant_id:
            return None
        row.update(fields)
        row["updated_at"] = _now()
        return dict(row)

    async def delete_reminder(self, *, tenant_id: uuid.UUID, reminder_id: uuid.UUID) -> bool:
        row = self.reminders.get(reminder_id)
        if row is not None and row["tenant_id"] == tenant_id:
            del self.reminders[reminder_id]
            return True
        return False

    # -- contactos --------------------------------------------------------------------

    async def create_contact(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, fields: dict[str, Any]
    ) -> Row:
        row: Row = {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "user_id": user_id,
            "nombre": fields.get("nombre"),
            "emails": fields.get("emails", []),
            "phones": fields.get("phones", []),
            "empresa": fields.get("empresa"),
            "notas": fields.get("notas"),
            "tags": fields.get("tags", []),
            "created_at": _now(),
            "updated_at": _now(),
        }
        self.contacts[row["id"]] = row
        return dict(row)

    async def list_contacts(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, q: str | None
    ) -> list[Row]:
        rows = [
            r
            for r in self.contacts.values()
            if r["tenant_id"] == tenant_id and r["user_id"] == user_id
        ]
        if q:
            rows = [r for r in rows if q.lower() in (r["nombre"] or "").lower()]
        rows.sort(key=lambda r: r["nombre"] or "")
        return [dict(r) for r in rows]

    async def get_contact(self, *, tenant_id: uuid.UUID, contact_id: uuid.UUID) -> Row | None:
        row = self.contacts.get(contact_id)
        if row is not None and row["tenant_id"] == tenant_id:
            return dict(row)
        return None

    async def update_contact(
        self, *, tenant_id: uuid.UUID, contact_id: uuid.UUID, fields: dict[str, Any]
    ) -> Row | None:
        row = self.contacts.get(contact_id)
        if row is None or row["tenant_id"] != tenant_id:
            return None
        row.update(fields)
        row["updated_at"] = _now()
        return dict(row)

    async def delete_contact(self, *, tenant_id: uuid.UUID, contact_id: uuid.UUID) -> bool:
        row = self.contacts.get(contact_id)
        if row is not None and row["tenant_id"] == tenant_id:
            del self.contacts[contact_id]
            return True
        return False

    # -- finanzas -----------------------------------------------------------------------

    async def create_transaction(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, fields: dict[str, Any]
    ) -> Row:
        row: Row = {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "user_id": user_id,
            "fecha": fields.get("fecha"),
            "monto": fields.get("monto"),
            "moneda": fields.get("moneda", "USD"),
            "categoria": fields.get("categoria"),
            "descripcion": fields.get("descripcion"),
            "cuenta": fields.get("cuenta"),
            "created_at": _now(),
            "updated_at": _now(),
        }
        self.transactions[row["id"]] = row
        return dict(row)

    async def list_transactions(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, mes: str | None
    ) -> list[Row]:
        rows = [
            r
            for r in self.transactions.values()
            if r["tenant_id"] == tenant_id and r["user_id"] == user_id
        ]
        if mes:
            rows = [
                r for r in rows if r["fecha"] is not None and r["fecha"].strftime("%Y-%m") == mes
            ]
        rows.sort(key=lambda r: r["fecha"] or _now().date(), reverse=True)
        return [dict(r) for r in rows]

    async def get_transaction(
        self, *, tenant_id: uuid.UUID, transaction_id: uuid.UUID
    ) -> Row | None:
        row = self.transactions.get(transaction_id)
        if row is not None and row["tenant_id"] == tenant_id:
            return dict(row)
        return None

    async def update_transaction(
        self, *, tenant_id: uuid.UUID, transaction_id: uuid.UUID, fields: dict[str, Any]
    ) -> Row | None:
        row = self.transactions.get(transaction_id)
        if row is None or row["tenant_id"] != tenant_id:
            return None
        row.update(fields)
        row["updated_at"] = _now()
        return dict(row)

    async def delete_transaction(self, *, tenant_id: uuid.UUID, transaction_id: uuid.UUID) -> bool:
        row = self.transactions.get(transaction_id)
        if row is not None and row["tenant_id"] == tenant_id:
            del self.transactions[transaction_id]
            return True
        return False

    async def finance_summary(self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, mes: str) -> Row:
        rows = [
            r
            for r in self.transactions.values()
            if r["tenant_id"] == tenant_id
            and r["user_id"] == user_id
            and r["fecha"] is not None
            and r["fecha"].strftime("%Y-%m") == mes
        ]
        ingresos = float(sum(r["monto"] for r in rows if r["monto"] > 0))
        gastos = float(sum(r["monto"] for r in rows if r["monto"] < 0))
        por_categoria: dict[str, float] = {}
        for r in rows:
            cat = r.get("categoria") or "sin_categoria"
            por_categoria[cat] = por_categoria.get(cat, 0.0) + float(r["monto"])
        return {
            "ingresos": ingresos,
            "gastos": gastos,
            "neto": ingresos + gastos,
            "num_transacciones": len(rows),
            "mes": mes,
            "por_categoria": [{"categoria": k, "total": v} for k, v in por_categoria.items()],
        }

    # -- billing -----------------------------------------------------------------------

    async def upsert_subscription(self, *, tenant_id: uuid.UUID, fields: dict[str, Any]) -> Row:
        row = self.subscriptions.get(tenant_id)
        if row is None:
            row = {
                "id": uuid.uuid4(),
                "tenant_id": tenant_id,
                "stripe_customer_id": None,
                "stripe_subscription_id": None,
                "plan_key": None,
                "status": "active",
                "current_period_end": None,
                "created_at": _now(),
                "updated_at": _now(),
            }
            self.subscriptions[tenant_id] = row
        row.update({k: v for k, v in fields.items() if v is not None})
        row["updated_at"] = _now()
        return dict(row)

    async def get_subscription_by_stripe_customer(self, stripe_customer_id: str) -> Row | None:
        for row in self.subscriptions.values():
            if row.get("stripe_customer_id") == stripe_customer_id:
                return dict(row)
        return None

    async def get_subscription_by_stripe_subscription(
        self, stripe_subscription_id: str
    ) -> Row | None:
        for row in self.subscriptions.values():
            if row.get("stripe_subscription_id") == stripe_subscription_id:
                return dict(row)
        return None

    # -- auditoría ------------------------------------------------------------------------

    async def add_audit_log(
        self,
        *,
        tenant_id: uuid.UUID | None,
        actor_user_id: uuid.UUID | None,
        action: str,
        target: str,
        meta: dict[str, Any] | None = None,
    ) -> None:
        self.audit_log.append(
            {
                "id": uuid.uuid4(),
                "tenant_id": tenant_id,
                "actor_user_id": actor_user_id,
                "action": action,
                "target": target,
                "meta": meta or {},
                "created_at": _now(),
            }
        )

    # -- vista remota (control remoto, WP-V2-09) -------------------------------------------

    async def create_remote_session(self, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> Row:
        row: Row = {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "user_id": user_id,
            "device_id": None,
            "kind": "view",
            "status": "pending",
            "started_at": None,
            "ended_at": None,
            "frames_count": 0,
            "created_at": _now(),
            "updated_at": _now(),
        }
        self.remote_sessions[row["id"]] = row
        return dict(row)

    async def list_remote_sessions(self, *, tenant_id: uuid.UUID) -> list[Row]:
        rows = [r for r in self.remote_sessions.values() if r["tenant_id"] == tenant_id]
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        return [dict(r) for r in rows]

    async def get_remote_session(
        self, *, tenant_id: uuid.UUID, session_id: uuid.UUID
    ) -> Row | None:
        row = self.remote_sessions.get(session_id)
        if row is not None and row["tenant_id"] == tenant_id:
            return dict(row)
        return None

    async def record_remote_session_frame(
        self, *, tenant_id: uuid.UUID, session_id: uuid.UUID
    ) -> Row:
        row = self.remote_sessions.get(session_id)
        assert row is not None and row["tenant_id"] == tenant_id
        if row["status"] == "pending":
            row["status"] = "active"
            row["started_at"] = _now()
        row["frames_count"] += 1
        row["updated_at"] = _now()
        return dict(row)

    async def mark_remote_session_denied(
        self, *, tenant_id: uuid.UUID, session_id: uuid.UUID
    ) -> Row:
        row = self.remote_sessions.get(session_id)
        assert row is not None and row["tenant_id"] == tenant_id
        row["status"] = "denied"
        row["updated_at"] = _now()
        return dict(row)

    async def mark_remote_session_ended(
        self, *, tenant_id: uuid.UUID, session_id: uuid.UUID
    ) -> Row:
        row = self.remote_sessions.get(session_id)
        assert row is not None and row["tenant_id"] == tenant_id
        if row["status"] != "ended":
            row["status"] = "ended"
            row["ended_at"] = _now()
            row["updated_at"] = _now()
        return dict(row)


class FakeRedis:
    """Réplica mínima, en memoria, de los comandos de `redis.asyncio.Redis` que
    usa `edecan_api`: `incr`, `expire`, `set` (con `ex=`/`nx=`), `get`, `getdel`,
    `delete`."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._expiry: dict[str, float] = {}

    def _expire_if_needed(self, key: str) -> None:
        exp = self._expiry.get(key)
        if exp is not None and time.time() > exp:
            self._store.pop(key, None)
            self._expiry.pop(key, None)

    async def incr(self, key: str) -> int:
        self._expire_if_needed(key)
        value = int(self._store.get(key, "0")) + 1
        self._store[key] = str(value)
        return value

    async def ping(self) -> bool:
        return True

    async def expire(self, key: str, seconds: int) -> bool:
        self._expiry[key] = time.time() + seconds
        return True

    async def set(
        self,
        key: str,
        value: Any,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool:
        self._expire_if_needed(key)
        if nx and key in self._store:
            return False
        self._store[key] = str(value)
        if ex is not None:
            self._expiry[key] = time.time() + ex
        else:
            self._expiry.pop(key, None)
        return True

    async def get(self, key: str) -> str | None:
        self._expire_if_needed(key)
        return self._store.get(key)

    async def getdel(self, key: str) -> str | None:
        self._expire_if_needed(key)
        value = self._store.pop(key, None)
        self._expiry.pop(key, None)
        return value

    async def delete(self, key: str) -> int:
        self._expire_if_needed(key)
        existed = key in self._store
        self._store.pop(key, None)
        self._expiry.pop(key, None)
        return 1 if existed else 0
