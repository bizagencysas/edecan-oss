"""Ayuda interna compartida por `pg.py`/`graph.py`: envolver SQL textual con
`sqlalchemy.text()` cuando está disponible.

`edecan_core` no declara `sqlalchemy` como dependencia dura (ver
`pyproject.toml`): el loop principal del agente no la necesita. Pero
`PgMemoryStore`/`add_edge`/`neighbors` sí hablan con una `AsyncSession` real
de SQLAlchemy 2.0 (la que entrega `edecan_db.session.get_session`), que
exige que el SQL textual pase por `text()` antes de `session.execute(...)` —
pasar un `str` a secas le lanza `ObjectNotExecutableError`.

En el proceso real (`apps/api`, `apps/worker`) `sqlalchemy` SIEMPRE está
instalada, porque `edecan_db`/`apps/api` la declaran como dependencia dura;
así que este import diferido simplemente la usa. Si por lo que sea no está
disponible (p. ej. alguien usa `edecan_core` de forma standalone, con un
`session` propio que ya acepta `str`), se degrada a pasar el SQL tal cual.
"""

from __future__ import annotations

from typing import Any

try:
    from sqlalchemy import text as _sqlalchemy_text
except ImportError:  # pragma: no cover - sqlalchemy no instalada
    _sqlalchemy_text = None


def sql(statement: str) -> Any:
    """Envuelve `statement` con `sqlalchemy.text()` si sqlalchemy está disponible."""
    return _sqlalchemy_text(statement) if _sqlalchemy_text is not None else statement
