"""Utilidades internas compartidas de `edecan_advisory`.

No forman parte del contrato público del paquete (por eso el prefijo `_`) —
mismo criterio que `edecan_toolkit._util`/`edecan_docanalysis._util`: cada
paquete lleva su propia copia de estos helpers pequeños en vez de importar a
un hermano (ARCHITECTURE.md §10.1: "los tests NO importan paquetes
hermanos" — mantener el helper local también evita que el código de
producción de este paquete dependa de la estructura interna de otro).
"""

from __future__ import annotations

import json
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
    y lo acota al rango cerrado [`minimo`, `maximo`]."""
    try:
        n = int(valor) if valor is not None else default
    except (TypeError, ValueError):
        n = default
    return max(minimo, min(maximo, n))


def slugify(texto: str, *, default: str = "archivo", max_len: int = 60) -> str:
    """Normaliza `texto` a un slug seguro para nombre de archivo (minúsculas,
    `[a-z0-9]` separados por `-`, recortado a `max_len`). Devuelve `default`
    si `texto` no deja ningún carácter alfanumérico."""
    normalizado = _SLUG_RE.sub("-", texto.strip().lower()).strip("-")
    if not normalizado:
        return default
    return normalizado[:max_len].strip("-") or default


def tenant_flags(ctx: ToolContext) -> dict[str, Any]:
    """Lee `ctx.extras["flags"]` (dict de flags del plan del tenant, mismo
    valor que recibe `Agent.run_turn(flags=...)`, ARCHITECTURE.md §10.7) para
    que una tool que llama a `ctx.llm.complete` por su cuenta respete el
    downgrade de modelo por plan. Mismo patrón que
    `edecan_toolkit.contenido._tenant_flags` / `edecan_docanalysis._util.tenant_flags`."""
    extras = ctx.extras if isinstance(ctx.extras, dict) else {}
    flags = extras.get("flags")
    return flags if isinstance(flags, dict) else {}


def extraer_json_llm(texto: str) -> dict[str, Any] | None:
    """Parsea `texto` (una respuesta de `ctx.llm.complete(...).text`) como un
    objeto JSON, tolerando que el modelo lo envuelva en una cerca de código
    markdown (` ```json ... ``` `) o le agregue una frase antes/después.

    Usado por `legal.py` (`analizar_contrato`) y `educacion.py`
    (`tutor_leccion`/`tutor_evaluar`), los tres prompts que piden al modelo
    "responde ÚNICAMENTE con un JSON con esta forma". Devuelve `None` si no
    logra extraer un objeto JSON válido — el caller cae a un `dict` vacío y
    renderiza valores por defecto en vez de reventar la herramienta.
    """
    limpio = texto.strip()
    if limpio.startswith("```"):
        limpio = limpio.strip("`").strip()
        if limpio[:4].lower() == "json":
            limpio = limpio[4:].strip()
    try:
        cargado = json.loads(limpio)
        return cargado if isinstance(cargado, dict) else None
    except (json.JSONDecodeError, ValueError):
        pass

    inicio, fin = texto.find("{"), texto.rfind("}")
    if inicio != -1 and fin != -1 and fin > inicio:
        try:
            cargado = json.loads(texto[inicio : fin + 1])
            return cargado if isinstance(cargado, dict) else None
        except (json.JSONDecodeError, ValueError):
            return None
    return None
