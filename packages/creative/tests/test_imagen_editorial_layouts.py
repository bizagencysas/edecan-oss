"""Variedad de layout en `imagen_editorial.componer_titular`.

Hasta aquí el compositor producía SIEMPRE el mismo layout (full-bleed, scrim inferior,
kicker+headline+apoyo abajo) — el dueño: "mismo titular, mismo subtitulo, todo eso
aburre". Estos tests clavan el contrato de la variedad: 3 layouts distintos, cada uno
devuelve PNG no vacío, dos layouts distintos sobre el mismo input producen bytes
DISTINTOS (si no, no hay variedad), el clásico no regresa (idéntico por nombre y por
índice), y el default por hash del headline es reproducible.

Offline y determinista: la foto se sintetiza con PIL (sin red, sin proveedor), y se
compone a `escala=1` con un lienzo chico para que el test sea rápido.
"""

from __future__ import annotations

import io

from edecan_creative.imagen_editorial import LAYOUTS, componer_titular
from PIL import Image

# Entrada `visual` representativa (mismo shape que `redaccion.normalizar_visual` le pasa al
# compositor desde `social.py`).
_VISUAL: dict[str, str] = {
    "kicker": "banca · tasas",
    "headline": "El crédito se encarece",
    "accent": "encarece",
    "support": "Suben las tasas de interés",
}


def _foto_sintetica() -> bytes:
    """PNG chico y determinista armado con PIL — un degradado vertical sobre el que el
    compositor pueda trabajar (no hace falta una foto real para probar layout variety)."""
    im = Image.linear_gradient("L").convert("RGB").resize((300, 375))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


_FIRMA_PNG = b"\x89PNG\r\n\x1a\n"


def test_cada_layout_devuelve_png_no_vacio():
    foto = _foto_sintetica()
    for nombre in LAYOUTS:
        out = componer_titular(
            foto, layout=nombre, width=300, height=375, escala=1, **_VISUAL
        )
        assert out[:8] == _FIRMA_PNG, f"{nombre} no devolvió PNG válido"
        assert len(out) > 500, f"{nombre} devolvió un PNG sospechosamente chico"


def test_layouts_distintos_producen_bytes_distintos():
    """El punto central del cambio: si dos layouts dan bytes iguales, no hay variedad."""
    foto = _foto_sintetica()
    salidas: dict[str, bytes] = {
        nombre: componer_titular(foto, layout=nombre, width=300, height=375, escala=1, **_VISUAL)
        for nombre in LAYOUTS
    }
    vistos: set[bytes] = set()
    for nombre, out in salidas.items():
        assert out not in vistos, f"el layout '{nombre}' colisiona byte a byte con otro"
        vistos.add(out)


def test_clasico_por_nombre_es_igual_que_por_indice():
    """Sin regresión: 'clasico' y 0 son el mismo camino y producen bytes idénticos."""
    foto = _foto_sintetica()
    por_nombre = componer_titular(
        foto, layout="clasico", width=300, height=375, escala=1, **_VISUAL
    )
    por_indice = componer_titular(foto, layout=0, width=300, height=375, escala=1, **_VISUAL)
    assert por_nombre == por_indice


def test_layout_none_es_reproducible():
    """El default (hash del headline) es determinista: misma entrada, misma salida, siempre."""
    foto = _foto_sintetica()
    a = componer_titular(foto, layout=None, width=300, height=375, escala=1, **_VISUAL)
    b = componer_titular(foto, layout=None, width=300, height=375, escala=1, **_VISUAL)
    assert a == b


def test_layout_none_coincide_con_algun_layout_con_nombre():
    """El hash del headline mapea a un índice concreto: el default debe ser idéntico a ese
    layout nombrado (prueba que la rotación por hash aterriza en una variante real, no en un
    camino distinto)."""
    foto = _foto_sintetica()
    default = componer_titular(foto, layout=None, width=300, height=375, escala=1, **_VISUAL)
    alguno = {
        componer_titular(foto, layout=nombre, width=300, height=375, escala=1, **_VISUAL)
        for nombre in LAYOUTS
    }
    assert default in alguno
