"""Utilidades internas compartidas entre las herramientas de `edecan_docanalysis`.

No forma parte del contrato público del paquete (por eso el prefijo `_`), mismo
criterio que `edecan_toolkit._util`.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from edecan_core import ToolContext

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def parse_uuid(valor: Any) -> uuid.UUID | None:
    """Convierte `valor` a `UUID`, o `None` si no es un UUID válido (nunca lanza)."""
    if not valor:
        return None
    try:
        return uuid.UUID(str(valor))
    except (ValueError, AttributeError, TypeError):
        return None


def clamp_int(valor: Any, *, default: int, minimo: int, maximo: int) -> int:
    """Convierte `valor` a `int` (usando `default` si falta o no es convertible)
    y lo acota al rango cerrado [`minimo`, `maximo`]. Mismo comportamiento que
    `edecan_toolkit._util.clamp_int` (paquete hermano, no se importa — cada
    paquete lleva su propia copia por ARCHITECTURE.md §10.1)."""
    try:
        n = int(valor) if valor is not None else default
    except (TypeError, ValueError):
        n = default
    return max(minimo, min(maximo, n))


def slugify(texto: str, *, default: str = "archivo", max_len: int = 60) -> str:
    """Normaliza `texto` a un slug seguro para nombre de archivo/hoja
    (minúsculas, `[a-z0-9]` separados por `-`, recortado a `max_len`).
    Devuelve `default` si `texto` no deja ningún carácter alfanumérico.
    """
    normalizado = _SLUG_RE.sub("-", texto.strip().lower()).strip("-")
    if not normalizado:
        return default
    return normalizado[:max_len].strip("-") or default


def tenant_flags(ctx: ToolContext) -> dict[str, Any]:
    """Lee `ctx.extras["flags"]` (dict de flags del plan del tenant, mismo
    valor que recibe `Agent.run_turn(flags=...)`, ARCHITECTURE.md §10.7) para
    que una tool que llama a `ctx.llm.complete`/`ctx.llm.resolve` por su
    cuenta respete el downgrade de modelo por plan. Mismo patrón que
    `edecan_toolkit.contenido._tenant_flags` (paquete hermano, no se importa
    — cada paquete lleva su propia copia por ARCHITECTURE.md §10.1).
    Compartida por `tablas.py` y `vision.py` (los dos módulos de este
    paquete que invocan al LLM directo), por eso vive en este helper interno
    en vez de duplicarse en cada uno."""
    extras = ctx.extras if isinstance(ctx.extras, dict) else {}
    flags = extras.get("flags")
    return flags if isinstance(flags, dict) else {}
