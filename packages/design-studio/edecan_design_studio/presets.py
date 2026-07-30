"""Formatos humanos para que el chat no obligue a conocer píxeles."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CanvasPreset:
    label: str
    width: int
    height: int
    guidance: str


CANVAS_PRESETS: dict[str, CanvasPreset] = {
    "libre": CanvasPreset("Lienzo libre", 1200, 800, "composición visual de propósito general"),
    "post_cuadrado": CanvasPreset(
        "Post cuadrado", 1080, 1080, "lectura móvil, jerarquía breve y área segura"
    ),
    "historia_vertical": CanvasPreset(
        "Historia vertical", 1080, 1920, "impacto vertical y contenido lejos de los bordes"
    ),
    "anuncio_horizontal": CanvasPreset(
        "Anuncio horizontal", 1200, 628, "mensaje inmediato y CTA legible"
    ),
    "landing": CanvasPreset(
        "Landing", 1440, 2000, "flujo vertical completo y secciones claramente diferenciadas"
    ),
    "email": CanvasPreset(
        "Email", 800, 1200, "columna estrecha, tipografía robusta y CTA inequívoco"
    ),
    "mockup_movil": CanvasPreset(
        "Mockup móvil", 1080, 1920, "interfaz detallada y legible sin datos inventados"
    ),
    "presentacion": CanvasPreset(
        "Presentación", 1600, 900, "una idea principal por diapositiva"
    ),
}


def canvas_dimensions(
    format_name: str,
    *,
    width: int | None,
    height: int | None,
) -> tuple[int, int]:
    preset = CANVAS_PRESETS.get(format_name) or CANVAS_PRESETS["libre"]
    return width or preset.width, height or preset.height


__all__ = ["CANVAS_PRESETS", "CanvasPreset", "canvas_dimensions"]
