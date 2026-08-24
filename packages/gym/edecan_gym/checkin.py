"""Decisión de check-in a partir de la respuesta del usuario (sí/no)."""

from __future__ import annotations

_AFIRMATIVAS = ("si", "sí", "yes")
_NEGATIVAS = ("no", "nope")


def decidir(respuesta: str) -> bool:
    """`True` para "si"/"sí"/"yes"; `False` para "no"/"nope"; `ValueError` en otro caso."""
    normalizada = respuesta.strip().lower()
    if normalizada in _AFIRMATIVAS:
        return True
    if normalizada in _NEGATIVAS:
        return False
    raise ValueError(f"respuesta no reconocida: {respuesta!r}")