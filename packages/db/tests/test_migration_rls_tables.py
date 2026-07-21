"""Guardarraíl: `RLS_TABLES` de las migraciones vs `edecan_db.models.RLS_TABLES`.

`packages/db/alembic/versions/0001_initial.py`, `0003_v2_expansion.py`,
`0004_v3_expansion.py`, `0006_v4_expansion.py`, `0007_v5_expansion.py` y
`0008_v6_expansion.py` mantienen cada una su propia tupla `RLS_TABLES` a
mano — a propósito no importan `edecan_db.models` (para quedar como una foto
fija de cada migración, independiente de cómo evolucionen los modelos del
ORM más adelante; ver el docstring de esos archivos). Eso deja siete listas
(seis de migración + la de `edecan_db.models`) que deben coincidir por
convención, sin que nada las ate en tiempo de import.

Este test es el cross-check que le falta a ese mantenimiento manual: si
`edecan_db.models.RLS_TABLES` gana una tabla tenant-scoped nueva (una clase
con `TenantScopedMixin`) y NINGUNA migración la incluye en su tupla
`RLS_TABLES`, esta prueba falla en vez de dejar pasar en silencio una tabla
sin política `tenant_isolation` — exactamente el riesgo "más crítico del
modelo multi-tenant" que señala `RIESGOS.md`.

No abre conexión a base de datos: `ScriptDirectory.get_revision(...).module`
solo importa el archivo `.py` de la migración desde disco (no ejecuta
`upgrade()`/`downgrade()` ni `alembic/env.py`), igual de liviano que los
tests de `test_db_models.py`.

`0002_twilio_number_global_unique` no aparece aquí: no crea tablas nuevas
(solo un índice sobre `connector_accounts`, ya cubierta por `0001_initial`),
así que no tiene su propia tupla `RLS_TABLES`. `0005_jobs_type_check_v2_types`
tampoco: solo altera un `CHECK` existente en `jobs` (ver su propio docstring).
`0007_v5_expansion` SÍ crea 5 tablas tenant-scoped nuevas (además de alterar
`devices`/`skills` y el `CHECK` de `jobs.type` — esas alteraciones no suman
tablas nuevas, así que no afectan esta lista), por eso SÍ trae su propia
`RLS_TABLES` y aparece abajo. `0008_v6_expansion` SÍ crea 2 tablas
tenant-scoped nuevas (`meetings`/`podcasts`; también altera el `CHECK` de
`jobs.type` otra vez, mismo criterio sin efecto sobre esta lista), por eso
también trae su propia `RLS_TABLES`.

Nota para la PRÓXIMA migración que agregue tablas tenant-scoped (`0009_*`):
siguiendo el patrón que dejó `0001_initial`/`0003_v2_expansion`/
`0004_v3_expansion`/`0006_v4_expansion`/`0007_v5_expansion` para
`0008_v6_expansion`, esa migración nueva NO debe tocar las tuplas
`RLS_TABLES` de las migraciones ya listadas en `_MIGRACIONES_CON_RLS_TABLES`
(cada una debe seguir representando solo lo que ESA migración crea) — en su
lugar, este test debe extenderse para sumar la `RLS_TABLES` de la migración
nueva a `_todas_las_rls_tables_de_migraciones()`.
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

from alembic.config import Config
from alembic.script import ScriptDirectory
from edecan_db.models import RLS_TABLES as MODELS_RLS_TABLES

_DB_PKG_ROOT = Path(__file__).resolve().parents[1]  # packages/db/

# Migraciones que declaran su propia tupla `RLS_TABLES` (crean >=1 tabla
# tenant-scoped). `0002_twilio_number_global_unique` queda fuera a propósito
# (ver docstring del módulo).
_MIGRACIONES_CON_RLS_TABLES: tuple[str, ...] = (
    "0001_initial",
    "0003_v2_expansion",
    "0004_v3_expansion",
    "0006_v4_expansion",
    "0007_v5_expansion",
    "0008_v6_expansion",
    "0011_phone_calls",
)


def _cargar_modulo_migracion(revision: str) -> ModuleType:
    cfg = Config(str(_DB_PKG_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_DB_PKG_ROOT / "alembic"))
    script_dir = ScriptDirectory.from_config(cfg)
    return script_dir.get_revision(revision).module


def _todas_las_rls_tables_de_migraciones() -> list[str]:
    todas: list[str] = []
    for revision in _MIGRACIONES_CON_RLS_TABLES:
        todas.extend(_cargar_modulo_migracion(revision).RLS_TABLES)
    return todas


def test_migraciones_rls_tables_sin_duplicados_dentro_de_cada_una():
    # Un duplicado DENTRO de una migración no rompería `upgrade()`/
    # `downgrade()` (el `ALTER TABLE`/`DROP POLICY IF EXISTS` repetido es
    # inofensivo) pero delataría un tuple armado a mano con un error de
    # copy-paste.
    for revision in _MIGRACIONES_CON_RLS_TABLES:
        migracion_rls_tables = _cargar_modulo_migracion(revision).RLS_TABLES
        assert len(migracion_rls_tables) == len(set(migracion_rls_tables)), revision


def test_ninguna_tabla_rls_esta_declarada_en_mas_de_una_migracion():
    # Cada tabla tenant-scoped debe tener su política `tenant_isolation`
    # creada UNA sola vez — si dos migraciones la declararan, `upgrade()`
    # fallaría de verdad (`CREATE POLICY` sobre una tabla que ya la tiene).
    todas = _todas_las_rls_tables_de_migraciones()
    assert len(todas) == len(set(todas))


def test_union_de_rls_tables_de_migraciones_coincide_con_edecan_db_models():
    assert set(_todas_las_rls_tables_de_migraciones()) == set(MODELS_RLS_TABLES)
