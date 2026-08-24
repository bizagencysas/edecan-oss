"""`edecan_db` — capa de datos de Edecán: SQLAlchemy 2.0 async + Alembic +
Row-Level Security + `TokenVault` (`ARCHITECTURE.md` §10.3, §10.4).

Re-exporta el contrato público para que el resto del monorepo pueda hacer
`from edecan_db import get_session, TokenVault, Tenant, ...`.
"""

from __future__ import annotations

from edecan_db.engine import create_engine, get_engine, get_sessionmaker
from edecan_db.models import (
    ALL_MODELS,
    GLOBAL_TABLES,
    RLS_TABLES,
    AuditLog,
    Base,
    Campaign,
    CampaignTarget,
    ConnectorAccount,
    Consent,
    Contact,
    Conversation,
    File,
    FileChunk,
    Job,
    Membership,
    MemoryEdge,
    MemoryItem,
    Message,
    OAuthToken,
    Persona,
    Reminder,
    Subscription,
    Tenant,
    TenantKey,
    Transaction,
    UsageEvent,
    User,
)
from edecan_db.session import get_session
from edecan_db.settings import DbSettings, get_settings
from edecan_db.vault import (
    KeyProvider,
    KmsKeyProvider,
    LocalKeyProvider,
    TokenVault,
    VaultError,
    get_key_provider,
)

__all__ = [
    "ALL_MODELS",
    "GLOBAL_TABLES",
    "RLS_TABLES",
    "AuditLog",
    "Base",
    "Campaign",
    "CampaignTarget",
    "ConnectorAccount",
    "Consent",
    "Contact",
    "Conversation",
    "DbSettings",
    "File",
    "FileChunk",
    "Job",
    "KeyProvider",
    "KmsKeyProvider",
    "LocalKeyProvider",
    "Membership",
    "MemoryEdge",
    "MemoryItem",
    "Message",
    "OAuthToken",
    "Persona",
    "Reminder",
    "Subscription",
    "Tenant",
    "TenantKey",
    "TokenVault",
    "Transaction",
    "UsageEvent",
    "User",
    "VaultError",
    "create_engine",
    "get_engine",
    "get_key_provider",
    "get_session",
    "get_sessionmaker",
    "get_settings",
]
