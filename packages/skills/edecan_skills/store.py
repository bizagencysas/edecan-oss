"""Acceso a la tabla `skills` (migración `0004_v3_expansion`, ampliada por `0007` con
`trust_tier`/`capabilities` — `ARCHITECTURE.md` §12e, WP-V5-01/WP-V5-04) con SQL
parametrizado directo (`sqlalchemy.text`) — MISMO criterio que v2 (`ROADMAP_V2.md` §7.4):
jamás importar `edecan_db.models` en código de producción; en tests, fakes (ver
`tests/conftest.py`).

Esquema pinned (columnas exactas): `id, tenant_id, user_id, nombre, slug, source,
descripcion, version, contenido, recursos jsonb, enabled, trust_tier text default
'sin_revisar', capabilities jsonb default '[]', created_at, updated_at`, con
`UNIQUE(tenant_id, slug)` (constraint `uq_skills_tenant_id_slug`) y RLS por `tenant_id`.

**`trust_tier`/`capabilities` (WP-V5-04, `edecan_skills.security`)**: `trust_tier` es uno de
`security.TRUST_TIERS` (`"indexada"`/`"sin_revisar"`, ver ese módulo para qué significa
cada uno); `capabilities` es la lista de nombres de tool que la skill declaró necesitar
(`allowed-tools` de su frontmatter, `installer.parse_capabilities`) — ambos los decide el
llamador (`tools.InstalarSkillTool`/`routers.skills`, que saben de dónde vino la fuente),
`insert_skill` solo los persiste.

**Escaneo anti-inyección al instalar**: `insert_skill` corre `security.escanear_inyeccion`
sobre `contenido` y, si hay hallazgos, la fila queda `enabled=false` sin importar lo que el
llamador hubiera pedido — una skill con posibles intentos de inyección de instrucciones
NUNCA queda activa por defecto (el humano puede reactivarla de todos modos tras revisar el
contenido, ver el gate `acknowledge` de `apps/api/edecan_api/routers/skills.py`). En un
reinstalo (`ON CONFLICT`), si la fuente nueva SÍ tiene hallazgos la fila se fuerza a
`enabled=false` aunque ya estuviera activa (una skill limpia no puede volverse maliciosa en
silencio vía reinstalo sin que esto la desactive); si la fuente nueva está limpia, se
preserva el `enabled` existente sin tocarlo — mismo criterio que ya aplicaba acá el resto de
campos (nunca reactivar en silencio algo que el usuario desactivó a propósito).

**Alcance de `user_id` en las funciones de este módulo — decisión de diseño documentada**:
la restricción `UNIQUE` real de la tabla es `(tenant_id, slug)`, NO `(tenant_id, user_id,
slug)` — es decir, un `slug` es único para todo el tenant, sin importar qué usuario lo
instaló (coherente con que la mayoría de los planes tienen `limits.seats=1`; en un tenant
multi-seat, dos usuarios instalando algo que "slugifica" al mismo nombre terminan
compartiendo la misma fila). Por eso:

- `list_skills` SÍ filtra por `(tenant_id, user_id)` — es la vista "lo que YO instalé".
- `get_by_slug`/`get_by_id`/`set_enabled`/`delete_skill` filtran SOLO por `tenant_id` —
  una vez instalada, cualquier miembro del tenant puede usar/gestionar la skill (mismo
  criterio que `edecan_business.invoices.next_numero`: "un negocio es del tenant", aunque
  la fila también lleve `user_id` para trazabilidad de quién la instaló). `insert_skill`
  resuelve la colisión con un único `INSERT ... ON CONFLICT (tenant_id, slug) DO UPDATE`
  (upsert atómico, ver su docstring) — así reinstalar nunca choca con el
  `UNIQUE(tenant_id, slug)` real de Postgres, ni siquiera bajo instalaciones concurrentes
  del mismo slug (doble clic en "Instalar", dos turnos de chat casi simultáneos).
"""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

# `edecan_skills` NO declara `sqlalchemy` como dependencia dura (ver `pyproject.toml`) —
# mismo criterio y misma técnica que `edecan_core.memory._sql` (ver ese docstring): import
# diferido/opcional, degrada a pasar el SQL como `str` si no está instalada. En el proceso
# real (`apps/api`) siempre está instalada, porque `apps/api`/`edecan_db` la declaran.
try:
    from sqlalchemy import text as _sqlalchemy_text
except ImportError:  # pragma: no cover - sqlalchemy no instalada
    _sqlalchemy_text = None

from .security import escanear_inyeccion

_SLUG_INVALID_RE = re.compile(r"[^a-z0-9]+")

_LIST_COLUMNS = (
    "id, tenant_id, user_id, nombre, slug, source, descripcion, version, enabled, "
    "trust_tier, capabilities, created_at, updated_at"
)  # a propósito SIN `contenido`/`recursos` — `GET /v1/skills` los omite (§10.14, WP-V3-04).
# `trust_tier`/`capabilities` SÍ viajan en la lista (WP-V5-04): son livianos (un string
# corto + un array chico de nombres de tool), a diferencia de `contenido` (~hasta 200KB).


def _sql(statement: str) -> Any:
    """Envuelve `statement` con `sqlalchemy.text()` si sqlalchemy está disponible (ver
    docstring del módulo)."""
    return _sqlalchemy_text(statement) if _sqlalchemy_text is not None else statement


def slugify(nombre: str) -> str:
    """Slug URL-safe y determinista de `nombre`: minúsculas, todo lo que no sea
    `[a-z0-9]` colapsa a un único `-`, sin guiones al inicio/final. `"skill"` si `nombre`
    queda vacío tras normalizar (nunca un slug vacío, que rompería el `UNIQUE`)."""
    base = _SLUG_INVALID_RE.sub("-", (nombre or "").strip().lower()).strip("-")
    return base or "skill"


async def list_skills(
    session: Any, tenant_id: UUID, user_id: UUID, solo_enabled: bool = False
) -> list[dict[str, Any]]:
    """Skills instaladas por `user_id` dentro de `tenant_id` (ver docstring del módulo),
    más recientes primero, SIN `contenido`/`recursos`. `solo_enabled=True` filtra a solo
    las activas."""
    stmt = (
        f"SELECT {_LIST_COLUMNS} FROM skills "
        "WHERE tenant_id = :tenant_id ::uuid AND user_id = :user_id ::uuid"
    )
    params: dict[str, Any] = {"tenant_id": str(tenant_id), "user_id": str(user_id)}
    if solo_enabled:
        stmt += " AND enabled = true"
    stmt += " ORDER BY created_at DESC"

    result = await session.execute(_sql(stmt), params)
    return [dict(row) for row in result.mappings().all()]


async def get_by_slug(session: Any, tenant_id: UUID, slug: str) -> dict[str, Any] | None:
    """Una skill por `slug` exacto, en todo el tenant (ver docstring del módulo) — fila
    completa (incluye `contenido`). `None` si no existe."""
    row = (
        await session.execute(
            _sql("SELECT * FROM skills WHERE tenant_id = :tenant_id ::uuid AND slug = :slug"),
            {"tenant_id": str(tenant_id), "slug": slug},
        )
    ).mappings().first()
    return dict(row) if row is not None else None


async def get_by_id(session: Any, tenant_id: UUID, skill_id: UUID) -> dict[str, Any] | None:
    """Una skill por `id`, en todo el tenant (ver docstring del módulo) — fila completa
    (incluye `contenido`). `None` si no existe (o es de otro tenant)."""
    row = (
        await session.execute(
            _sql("SELECT * FROM skills WHERE tenant_id = :tenant_id ::uuid AND id = :id ::uuid"),
            {"tenant_id": str(tenant_id), "id": str(skill_id)},
        )
    ).mappings().first()
    return dict(row) if row is not None else None


async def insert_skill(
    session: Any,
    *,
    tenant_id: UUID,
    user_id: UUID,
    nombre: str,
    source: str,
    contenido: str,
    descripcion: str = "",
    version: str | None = None,
    recursos: dict[str, Any] | None = None,
    capabilities: list[str] | None = None,
    trust_tier: str = "sin_revisar",
) -> dict[str, Any]:
    """Instala (o reinstala) una skill: `slug = slugify(nombre)`; si ya existe una fila con
    ese `slug` en el tenant (`UNIQUE(tenant_id, slug)`), actualiza `contenido`/`version`/
    `descripcion`/`source`/`trust_tier`/`capabilities`/`updated_at` de la fila existente EN
    VEZ DE insertar una duplicada — "instalar" algo que ya estaba instalado es, en efecto,
    reinstalarlo/actualizarlo con el contenido más reciente.

    `enabled` se decide SIEMPRE acá, nunca lo elige el llamador (ver
    `security.escanear_inyeccion` en el docstring del módulo): si `contenido` tiene
    hallazgos de una posible inyección de instrucciones, la fila queda (o se fuerza a)
    `enabled=false`; si no, un alta nueva queda `enabled=true` y un reinstalo PRESERVA el
    `enabled` existente sin tocarlo — reinstalar con contenido limpio nunca reactiva en
    silencio una skill que el usuario desactivó a propósito, mismo criterio que ya aplicaba
    acá el resto de campos.

    Implementado como un único `INSERT ... ON CONFLICT (tenant_id, slug) DO UPDATE`
    (upsert atómico) — NO como "`SELECT` para chequear existencia + `INSERT` o `UPDATE`".
    Ese patrón check-then-act tendría una ventana de carrera entre el `SELECT` y el
    `INSERT`: dos instalaciones concurrentes del mismo `slug` (doble clic en "Instalar" en
    `/app/skills`, o dos turnos de chat casi simultáneos llamando a `instalar_skill`)
    podrían ver ambas `existente is None` antes de que cualquiera hiciera commit, y ambas
    intentar el `INSERT` — la segunda violaría `UNIQUE(tenant_id, slug)`
    (`uq_skills_tenant_id_slug`) con un `IntegrityError` sin capturar. El `ON CONFLICT`
    delega esa resolución a Postgres, que la arbitra de forma atómica sin necesitar un
    `SELECT` previo ni manejar la excepción acá.
    """
    slug = slugify(nombre)
    enabled = not escanear_inyeccion(contenido)

    row = (
        await session.execute(
            _sql(
                "INSERT INTO skills "
                "(tenant_id, user_id, nombre, slug, source, descripcion, version, contenido, "
                "recursos, trust_tier, capabilities, enabled) "
                "VALUES (:tenant_id ::uuid, :user_id ::uuid, :nombre, :slug, :source, "
                ":descripcion, :version, :contenido, CAST(:recursos AS jsonb), :trust_tier, "
                "CAST(:capabilities AS jsonb), :enabled) "
                "ON CONFLICT (tenant_id, slug) DO UPDATE SET "
                "contenido = EXCLUDED.contenido, version = EXCLUDED.version, "
                "descripcion = EXCLUDED.descripcion, source = EXCLUDED.source, "
                "trust_tier = EXCLUDED.trust_tier, capabilities = EXCLUDED.capabilities, "
                "enabled = CASE WHEN EXCLUDED.enabled = false THEN false ELSE skills.enabled END, "
                "updated_at = now() "
                "RETURNING *"
            ),
            {
                "tenant_id": str(tenant_id),
                "user_id": str(user_id),
                "nombre": nombre,
                "slug": slug,
                "source": source,
                "descripcion": descripcion,
                "version": version,
                "contenido": contenido,
                "recursos": json.dumps(recursos or {}),
                "trust_tier": trust_tier,
                "capabilities": json.dumps(list(capabilities or [])),
                "enabled": enabled,
            },
        )
    ).mappings().first()
    await session.flush()
    if row is None:  # defensivo: no debería pasar nunca (INSERT/UPDATE con RETURNING).
        raise RuntimeError("No se pudo instalar la skill (fila no devuelta por Postgres).")
    return dict(row)


async def set_enabled(
    session: Any, tenant_id: UUID, skill_id: UUID, enabled: bool
) -> dict[str, Any] | None:
    """Activa/desactiva una skill instalada. `None` si no existe (o es de otro tenant)."""
    row = (
        await session.execute(
            _sql(
                "UPDATE skills SET enabled = :enabled, updated_at = now() "
                "WHERE tenant_id = :tenant_id ::uuid AND id = :id ::uuid RETURNING *"
            ),
            {"enabled": enabled, "tenant_id": str(tenant_id), "id": str(skill_id)},
        )
    ).mappings().first()
    await session.flush()
    return dict(row) if row is not None else None


async def delete_skill(session: Any, tenant_id: UUID, skill_id: UUID) -> bool:
    """Desinstala (borra) una skill. `True` si había una fila y se borró; `False` si no
    existía (o era de otro tenant) — el llamador HTTP lo traduce a 404 en ese caso."""
    row = (
        await session.execute(
            _sql(
                "DELETE FROM skills WHERE tenant_id = :tenant_id ::uuid AND id = :id ::uuid "
                "RETURNING id"
            ),
            {"tenant_id": str(tenant_id), "id": str(skill_id)},
        )
    ).mappings().first()
    await session.flush()
    return row is not None
