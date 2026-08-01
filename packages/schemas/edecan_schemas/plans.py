"""Planes y flags de producto (pinned, ver ARCHITECTURE.md §10.13).

## Modelo de precio de pago único (2026-07-09) — reemplaza el de suscripción

Edecán dejó de venderse por suscripción mensual con tiers de capacidades
distintas: ahora es pago único, dos tiers ($99 "código completo" / $199
"código + actualizaciones de por vida", `apps/api/edecan_api/routers/
billing.py`), y NINGÚN tier restringe capacidades del producto — quien
compró tiene acceso a todo. La única diferencia real entre los dos tiers es
`tenants.lifetime_updates_purchased_at` (migración 0010), no un flag de acá.

Las 4 entradas de `PLANES` de abajo (`free_selfhost`/`hosted_basic`/
`hosted_pro`/`hosted_business`) se MANTIENEN a propósito — muchísimos tests
y datos ya sembrados usan esos `plan_key` literales como fixture — pero su
CONTENIDO ahora es idéntico entre las 4: todos los flags booleanos en
`True`, todos los límites en `UNLIMITED`. Es decir, el sistema de flags
sigue existiendo mecánicamente (nada dejó de leer `flags.get(FLAG_X,
False)` en 43+ lugares de `apps/api`) pero ya nunca gatea nada — cualquier
`plan_key` válido concede todo. Si en el futuro se quiere borrar el
mecanismo entero (los 43+ call-sites), es un cambio mecánico aparte, no
haría falta tocar este archivo de nuevo.

Los flags booleanos gatean capacidades del producto; los límites enteros
gatean cuotas de uso (`-1` significa "ilimitado"). Ambos viven en el mismo
dict `PlanDef.flags` usando claves con notación de punto, para que el resto
del código pueda hacer `flags.get(FLAG_VOICE_WEB, False)` o
`flags.get(LIMIT_MESSAGES_PER_DAY, 0)` sin distinguir su "forma" de origen.

Los flags del plan del tenant se recalculan SIEMPRE server-side desde
`PLANES[plan_key]` — nunca se confía en el valor embebido en un JWT.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

# ---------------------------------------------------------------------------
# Claves de flags booleanos (§10.13)
# ---------------------------------------------------------------------------

FLAG_VOICE_WEB = "voice.web"
FLAG_VOICE_TELEPHONY = "voice.telephony"
FLAG_CONNECTORS_SOCIAL = "connectors.social"
FLAG_CAMPAIGNS = "campaigns"
FLAG_COMPANION = "companion"
FLAG_MODELS_PREMIUM = "models.premium"

# --- v2 (ROADMAP_V2.md §7.2, dueño WP-V2-01) --------------------------------
FLAG_AGENTS_MISSIONS = "agents.missions"
FLAG_AUTOMATIONS_RULES = "automations.rules"
FLAG_TOOLS_BROWSER = "tools.browser"
FLAG_TOOLS_IMAGES = "tools.images"
FLAG_COMPANION_IDE = "companion.ide"
FLAG_COMPANION_REMOTE_VIEW = "companion.remote_view"
FLAG_COMMERCE_ORDERS = "commerce.orders"
FLAG_CONNECTORS_MESSAGING = "connectors.messaging"

# --- v4 (ARCHITECTURE.md §13, dueño WP-V4-01) -------------------------------
FLAG_ERP_INVENTORY = "erp.inventory"
FLAG_TOOLS_ADS = "tools.ads"
FLAG_TOOLS_VEHICLES = "tools.vehicles"
FLAG_COMPANION_REMOTE_INPUT = "companion.remote_input"

# --- v5 (ARCHITECTURE.md §14, dueño WP-V5-01) -------------------------------
FLAG_ERP_HR = "erp.hr"
FLAG_TOOLS_TRAVEL = "tools.travel"
FLAG_VOICE_CLONING = "voice.cloning"
FLAG_TOOLS_PODCAST = "tools.podcast"
FLAG_NOTIFICATIONS_PUSH = "notifications.push"

# --- v6 (ARCHITECTURE.md §15, dueño WP-V6-01) -------------------------------
FLAG_TOOLS_MEETINGS = "tools.meetings"
FLAG_TOOLS_MCP = "tools.mcp"

BOOL_FLAGS: tuple[str, ...] = (
    FLAG_VOICE_WEB,
    FLAG_VOICE_TELEPHONY,
    FLAG_CONNECTORS_SOCIAL,
    FLAG_CAMPAIGNS,
    FLAG_COMPANION,
    FLAG_MODELS_PREMIUM,
    FLAG_AGENTS_MISSIONS,
    FLAG_AUTOMATIONS_RULES,
    FLAG_TOOLS_BROWSER,
    FLAG_TOOLS_IMAGES,
    FLAG_COMPANION_IDE,
    FLAG_COMPANION_REMOTE_VIEW,
    FLAG_COMMERCE_ORDERS,
    FLAG_CONNECTORS_MESSAGING,
    FLAG_ERP_INVENTORY,
    FLAG_TOOLS_ADS,
    FLAG_TOOLS_VEHICLES,
    FLAG_COMPANION_REMOTE_INPUT,
    FLAG_ERP_HR,
    FLAG_TOOLS_TRAVEL,
    FLAG_VOICE_CLONING,
    FLAG_TOOLS_PODCAST,
    FLAG_NOTIFICATIONS_PUSH,
    FLAG_TOOLS_MEETINGS,
    FLAG_TOOLS_MCP,
)

# ---------------------------------------------------------------------------
# Claves de límites enteros (§10.13). -1 = ilimitado.
# ---------------------------------------------------------------------------

LIMIT_MESSAGES_PER_DAY = "limits.messages_per_day"
LIMIT_VOICE_MINUTES_MONTH = "limits.voice_minutes_month"
LIMIT_STORAGE_MB = "limits.storage_mb"
LIMIT_PHONE_NUMBERS = "limits.phone_numbers"
LIMIT_SEATS = "limits.seats"

# --- v2 (ROADMAP_V2.md §7.2, dueño WP-V2-01) --------------------------------
LIMIT_MISSIONS_PER_DAY = "limits.missions_per_day"
LIMIT_AUTOMATIONS_ACTIVE = "limits.automations_active"

INT_LIMITS: tuple[str, ...] = (
    LIMIT_MESSAGES_PER_DAY,
    LIMIT_VOICE_MINUTES_MONTH,
    LIMIT_STORAGE_MB,
    LIMIT_PHONE_NUMBERS,
    LIMIT_SEATS,
    LIMIT_MISSIONS_PER_DAY,
    LIMIT_AUTOMATIONS_ACTIVE,
)

UNLIMITED = -1


class PlanDef(BaseModel):
    """Definición de un plan comercial: precio + flags/límites."""

    model_config = ConfigDict(frozen=True)

    key: str
    nombre: str
    precio_usd_mes: int
    flags: dict[str, bool | int]


# Contenido único y compartido por las 4 entradas de `PLANES` — ver docstring
# del módulo, "Modelo de precio de pago único": ya no hay tiers de
# capacidades, comprar Edecán da acceso a todo.
_TODO_INCLUIDO: dict[str, bool | int] = {
    **dict.fromkeys(BOOL_FLAGS, True),
    **dict.fromkeys(INT_LIMITS, UNLIMITED),
}

PLANES: dict[str, PlanDef] = {
    "free_selfhost": PlanDef(
        key="free_selfhost", nombre="Edecán", precio_usd_mes=0, flags=dict(_TODO_INCLUIDO)
    ),
    "hosted_basic": PlanDef(
        key="hosted_basic", nombre="Edecán", precio_usd_mes=0, flags=dict(_TODO_INCLUIDO)
    ),
    "hosted_pro": PlanDef(
        key="hosted_pro", nombre="Edecán", precio_usd_mes=0, flags=dict(_TODO_INCLUIDO)
    ),
    "hosted_business": PlanDef(
        key="hosted_business", nombre="Edecán", precio_usd_mes=0, flags=dict(_TODO_INCLUIDO)
    ),
}
