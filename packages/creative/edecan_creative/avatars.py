"""Avatares de agente deterministas (product design).

Un avatar de agente es un *descriptor* JSON — nunca una imagen — que se guarda
en `persistent_agents.avatar` (JSONB). A partir de un `seed` estable (el nombre o
identificador del agente) se deriva, sin aleatoriedad, forma geométrica, color
sólido, ojos inclinados y variantes de estilo Grok Bot.

El descriptor es deliberadamente honesto: no promete una URL ni un PNG que no se
haya producido. Los renderers (iOS, web, desktop) dibujan la cara a partir de
`shape`, `fill` y `eyes`. Los estilos legacy `geometric`/`professional` (iniciales
sobre degradado) siguen soportados para avatares ya guardados.
"""

from __future__ import annotations

import hashlib
import re

# Paletas fijas y ordenadas: se indexan por hash del seed, así que el resultado
# es estable entre procesos y entre versiones de Python (nunca se itera un set).
_ACENTOS: tuple[str, ...] = (
    "#6366f1", "#8b5cf6", "#d946ef", "#ec4899", "#f43f5e",
    "#f59e0b", "#f97316", "#ef4444", "#10b981", "#14b8a6",
    "#06b6d4", "#0ea5e9", "#3b82f6", "#1d4ed8", "#84cc16", "#a855f7",
)

_GROK_FILLS: tuple[str, ...] = (
    "#3b82f6",  # blue
    "#f97316",  # orange
    "#14b8a6",  # teal
    "#eab308",  # yellow
    "#8b5cf6",  # violet
    "#ec4899",  # pink
    "#06b6d4",  # cyan
    "#ef4444",  # red
    "#10b981",  # emerald
    "#6366f1",  # indigo
)

_GROK_SHAPES: tuple[str, ...] = (
    "circle",
    "rounded_square",
    "oval",
    "hexagon",
    "squircle",
)

_GRADIENTES: tuple[tuple[str, str], ...] = (
    ("#6366f1", "#8b5cf6"),
    ("#06b6d4", "#3b82f6"),
    ("#f43f5e", "#fb7185"),
    ("#f59e0b", "#f97316"),
    ("#10b981", "#14b8a6"),
    ("#8b5cf6", "#d946ef"),
    ("#0ea5e9", "#22d3ee"),
    ("#ef4444", "#f97316"),
    ("#14b8a6", "#84cc16"),
    ("#6366f1", "#22d3ee"),
    ("#a855f7", "#6366f1"),
    ("#e11d48", "#f43f5e"),
)

_BASES_PROFESIONALES: tuple[str, ...] = (
    "#1e293b", "#334155", "#475569", "#3f3f46",
    "#1c1917", "#292524", "#1e3a5f", "#164e63",
    "#1f2937", "#2d3142", "#3b3b4f", "#4a4e69",
)

_HEX_CHARS = "0123456789abcdefABCDEF"

# Posiciones normalizadas (0–1) de los ojos inclinados; la rotación varía por seed.
_EYE_ROTATIONS: tuple[int, ...] = (-28, -22, -18, -32, -25, -20)


def _indice(seed: str, tamaño: int, sal: str) -> int:
    """Índice estable en `[0, tamaño)` derivado de `seed` + `sal`."""
    digesto = hashlib.sha256(f"{sal}:{seed}".encode()).digest()
    return int.from_bytes(digesto[:8], "big") % tamaño


def _iniciales(seed: str) -> str:
    """Hasta dos iniciales (primera letra de las dos primeras palabras) de `seed`."""
    palabras = [p for p in re.split(r"[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+", (seed or "").strip()) if p]
    if not palabras:
        return "AG"
    letras = "".join(palabra[0].upper() for palabra in palabras[:2])
    return letras or "AG"


def _normalizar_hex(valor: str) -> str:
    """Normaliza un color hex a `#rrggbb` en minúsculas, o lanza `ValueError`."""
    texto = (valor or "").strip().lstrip("#")
    if len(texto) == 3 and all(c in _HEX_CHARS for c in texto):
        texto = "".join(c * 2 for c in texto)
    if len(texto) != 6 or any(c not in _HEX_CHARS for c in texto):
        raise ValueError(f"acento inválido: {valor!r} (se espera un color hex como '#RRGGBB')")
    return "#" + texto.lower()


def _resolver_accento(seed: str, acento: str | None) -> str:
    if acento is not None:
        return _normalizar_hex(acento)
    return _ACENTOS[_indice(seed, len(_ACENTOS), "accento")]


def _ojo(seed: str, lado: str) -> dict[str, float | int]:
    """Un ojo inclinado (punto elíptico blanco) en coordenadas normalizadas."""
    rotacion = _EYE_ROTATIONS[_indice(seed, len(_EYE_ROTATIONS), f"ojo-rot-{lado}")]
    if lado == "left":
        x, y = 0.34, 0.40
    else:
        x, y = 0.66, 0.40
    # Ligera variación vertical por seed para que no se vean clonados.
    y += (_indice(seed, 5, f"ojo-y-{lado}") - 2) * 0.015
    rx = 0.055 + (_indice(seed, 3, f"ojo-rx-{lado}") * 0.008)
    ry = 0.075 + (_indice(seed, 3, f"ojo-ry-{lado}") * 0.006)
    return {"x": x, "y": y, "rx": rx, "ry": ry, "rotation": rotacion}


def generar_avatar_grok_face(seed: str, acento: str | None = None) -> dict:
    """Descriptor de cara estilo Grok Bot: forma + relleno sólido + ojos inclinados.

    Devuelve `{style, seed, shape, fill, eyes}` listo para que iOS/web lo dibujen.
    Determinista para un mismo `seed`; `acento` fuerza el color de relleno si viene.
    """
    fill = _normalizar_hex(acento) if acento is not None else _GROK_FILLS[_indice(seed, len(_GROK_FILLS), "grok-fill")]
    shape = _GROK_SHAPES[_indice(seed, len(_GROK_SHAPES), "grok-shape")]
    return {
        "style": "grok_face",
        "seed": seed,
        "shape": shape,
        "fill": fill,
        "eyes": {
            "style": "slanted_dots",
            "color": "#ffffff",
            "left": _ojo(seed, "left"),
            "right": _ojo(seed, "right"),
        },
    }


def generar_avatar_geometrico(seed: str, acento: str | None = None) -> dict:
    """Descriptor legacy: iniciales sobre degradado."""
    inicio, fin = _GRADIENTES[_indice(seed, len(_GRADIENTES), "degradado")]
    return {
        "style": "geometric",
        "accent": _resolver_accento(seed, acento),
        "gradient": [inicio, fin],
        "seed": seed,
        "initials": _iniciales(seed),
    }


def generar_avatar_profesional(seed: str, acento: str | None = None) -> dict:
    """Descriptor legacy: iniciales sobre base sobria."""
    return {
        "style": "professional",
        "accent": _resolver_accento(seed, acento),
        "base": _BASES_PROFESIONALES[_indice(seed, len(_BASES_PROFESIONALES), "base")],
        "seed": seed,
        "initials": _iniciales(seed),
    }


_ESTILOS = {
    "grok_face": generar_avatar_grok_face,
    "geometric": generar_avatar_geometrico,
    "professional": generar_avatar_profesional,
}


def avatar_para_agente(seed: str, style: str = "grok_face", acento: str | None = None) -> dict:
    """Descriptor listo para guardar en `persistent_agents.avatar`.

    Por defecto usa `grok_face` (cara geométrica estilo Grok Bot). Estilos
    legacy `geometric`/`professional` siguen disponibles.
    """
    try:
        generar = _ESTILOS[style]
    except KeyError:
        raise ValueError(
            f"estilo de avatar desconocido: {style!r} "
            f"(se espera 'grok_face', 'geometric' o 'professional')"
        ) from None
    return generar(seed, acento)
