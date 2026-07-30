"""`redact` — enmascara secretos evidentes antes de loguear texto.

No es un DLP completo: es una última red de seguridad para que una API key o
un header `Authorization` que se cuele en un mensaje de error, en el texto de
una herramienta, o en cualquier otro string que termine en un log, no quede
en texto plano (ARCHITECTURE.md §0.1 "Cero secretos reales"; SECURITY.md
"nunca deben aparecer en texto claro en logs, backups, mensajes de error o
trazas").
"""

from __future__ import annotations

import re

_MASK = "[REDACTED]"

_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Claves con prefijo "sk-"/"sk_" (Anthropic "sk-ant-…", OpenAI "sk-…",
    # Stripe secretas "sk_live_…"/"sk_test_…").
    re.compile(r"\bsk[-_][A-Za-z0-9_-]{8,}"),
    # Encabezado/valor "Bearer <token>" (Authorization header típico).
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    # Claves restringidas/de webhook de Stripe (rk_live_…, rk_test_…, whsec_…).
    re.compile(r"\b(?:rk_live|rk_test|whsec)_[A-Za-z0-9]{8,}"),
    # Access key id de AWS (p. ej. credenciales de LocalStack pegadas por error).
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
)


def redact(text: str) -> str:
    """Devuelve `text` con cualquier patrón de credencial reconocible enmascarado."""
    redacted = text
    for pattern in _PATTERNS:
        redacted = pattern.sub(_MASK, redacted)
    return redacted


_INTERNAL_ERROR_MARKERS = (
    "sqlalchemy.",
    "asyncpg.",
    "[sql:",
    "background on this error",
    "current transaction is aborted",
    "undefinedfileerror",
    "$libdir/",
)


def public_error_message(exc: BaseException) -> str:
    """Devuelve un error útil sin publicar infraestructura o sentencias SQL.

    Los errores normales del proveedor se conservan porque ayudan a corregir
    un modelo o credencial. Los errores internos de base de datos se registran
    completos en el servidor, pero el chat solo recibe una explicación breve.
    """

    detail = redact(str(exc))
    normalized = detail.casefold()
    if any(marker in normalized for marker in _INTERNAL_ERROR_MARKERS):
        return (
            "Edecán encontró un problema temporal con el almacenamiento local. "
            "El detalle quedó registrado; vuelve a intentarlo."
        )
    return detail
