"""Gráficos SVG deterministas (`generar_grafico`, ROADMAP_V2.md §7.7, §5, §10).

SVG construido a mano, **puro Python** (nunca matplotlib ni ninguna librería
de render): el mismo input siempre produce el mismo string byte a byte — sin
timestamps, sin ids aleatorios, sin iterar sobre `dict`/`set` cuando el orden
importa, formateo de números fijo vía `_fmt` (siempre `.2f`, nunca notación
científica ni locale). Necesario para que `tests/test_graficos.py` compare
contra un snapshot exacto (ver `tests/fixtures/`).

Tres tipos (`tipo`), todos con **valores no negativos únicamente** (decisión
de alcance deliberada: soportar barras/líneas bidireccionales alrededor de
cero exige repartir el alto del gráfico entre semiplano positivo y negativo,
bastante más lógica para un caso de uso poco común aquí — con datos negativos
la tool devuelve un `ToolResult` de error claro en vez de dibujar algo
incorrecto a medias):

- `"barras"`: una barra por etiqueta, eje Y autoescalado ("nice numbers":
  `_nice_step` redondea el paso de la cuadrícula a 1/2/5·10^n) con el valor
  encima de cada barra.
- `"lineas"`: una o más `series` (cada una `{nombre, valores}}`; si solo viene
  `valores` se trata como una única serie "Serie 1"), escala Y COMPARTIDA
  entre todas las series para que sean comparables, con leyenda.
- `"dona"`: anillo construido con un `<circle stroke-dasharray>` por
  categoría — la técnica estándar de "donut con círculos apilados" en vez de
  un `<path>` de arco: evita la trigonometría de construir el `d=` de un arco
  (y sus redondeos en los puntos de inicio/fin), es más fácil de razonar y de
  testear. Requiere que la suma de valores sea > 0.

Paleta fija **Okabe–Ito** (Okabe & Ito, 2008) — el estándar de facto de
paleta accesible para las formas más comunes de daltonismo — ciclada si hay
más categorías/series que colores.

El SVG resultante se sube a S3 vía `_s3.subir_resultado` (nunca a disco
local: ninguna tool de este paquete asume filesystem persistente entre
llamadas).
"""

from __future__ import annotations

import math
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from edecan_core import Tool, ToolContext, ToolResult

from . import _s3
from ._util import slugify

_ANCHO = 640
_ALTO = 400
_MARGEN_IZQ = 60
_MARGEN_DER = 30
_MARGEN_SUP = 55
_MARGEN_INF = 70

_TIPOS = ("barras", "lineas", "dona")
_MAX_CATEGORIAS = 20
_MAX_SERIES = 8

# Paleta Okabe-Ito (Okabe & Ito, 2008) — accesible para las formas más
# comunes de daltonismo (protanopia/deuteranopia/tritanopia).
_PALETA = (
    "#0072B2",  # azul
    "#E69F00",  # naranja
    "#009E73",  # verde azulado
    "#CC79A7",  # rosa/púrpura
    "#D55E00",  # rojo ladrillo
    "#56B4E9",  # celeste
    "#F0E442",  # amarillo
    "#000000",  # negro
)

_FUENTE = "Helvetica, Arial, sans-serif"

_DONA_CX = 200.0
_DONA_CY = 220.0
_DONA_RADIO = 110.0
_DONA_GROSOR = 46.0


class GenerarGraficoTool(Tool):
    name = "generar_grafico"
    description = (
        "Genera un gráfico SVG (barras, líneas o dona) a partir de etiquetas y "
        "valores numéricos NO negativos, y lo guarda como archivo. Para 'lineas' "
        "con más de una serie, usa 'series' en vez de 'valores'."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "tipo": {
                "type": "string",
                "enum": list(_TIPOS),
                "description": "Tipo de gráfico: 'barras', 'lineas' o 'dona'.",
            },
            "titulo": {"type": "string", "description": "Título del gráfico."},
            "etiquetas": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Etiquetas del eje X (barras/líneas) o de cada porción (dona).",
            },
            "valores": {
                "type": "array",
                "items": {"type": "number"},
                "description": (
                    "Valores numéricos no negativos, uno por etiqueta. Usado por "
                    "'barras' y 'dona'; para 'lineas' con una sola serie equivale a "
                    "series=[{nombre:'Serie 1', valores}]."
                ),
            },
            "series": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "nombre": {"type": "string"},
                        "valores": {"type": "array", "items": {"type": "number"}},
                    },
                    "required": ["nombre", "valores"],
                },
                "description": "Solo para 'tipo'='lineas' con más de una serie.",
            },
        },
        "required": ["tipo", "titulo", "etiquetas"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        tipo = str(args.get("tipo") or "").strip().lower()
        if tipo not in _TIPOS:
            return ToolResult(content=f"'tipo' debe ser uno de: {', '.join(_TIPOS)}.")

        titulo = str(args.get("titulo") or "").strip() or "Gráfico"
        etiquetas = _limpiar_etiquetas(args.get("etiquetas"))
        if not etiquetas:
            return ToolResult(content="Necesito al menos una etiqueta en 'etiquetas'.")
        if len(etiquetas) > _MAX_CATEGORIAS:
            return ToolResult(
                content=f"Demasiadas etiquetas ({len(etiquetas)}); el máximo es {_MAX_CATEGORIAS}."
            )

        if tipo == "lineas":
            series, error = _normalizar_series(args, len(etiquetas))
            if error:
                return ToolResult(content=error)
            svg = _grafico_lineas(titulo, etiquetas, series)
        else:
            valores, error = _normalizar_valores(args.get("valores"), len(etiquetas))
            if error:
                return ToolResult(content=error)
            if tipo == "barras":
                svg = _grafico_barras(titulo, etiquetas, valores)
            else:
                error_dona = _validar_dona(valores)
                if error_dona:
                    return ToolResult(content=error_dona)
                svg = _grafico_dona(titulo, etiquetas, valores)

        filename = f"{slugify(titulo, default=tipo)}-{tipo}.svg"
        file_id = await _s3.subir_resultado(
            ctx, filename=filename, mime="image/svg+xml", contenido=svg.encode("utf-8")
        )
        return ToolResult(
            content=f"Generé el gráfico de {tipo} «{titulo}» y lo guardé como «{filename}».",
            data={"file_id": str(file_id), "filename": filename, "mime": "image/svg+xml"},
        )


def generar_svg(
    tipo: str,
    titulo: str,
    etiquetas: Any,
    *,
    valores: Any = None,
    series: Any = None,
) -> str:
    """Versión pura (sin S3) de lo que hace `GenerarGraficoTool.run()` antes de subir el
    resultado: misma validación (`_limpiar_etiquetas`/`_normalizar_valores`/
    `_normalizar_series`/`_validar_dona`, sin cambios) + mismo render determinista
    (`_grafico_barras`/`_grafico_lineas`/`_grafico_dona`, sin cambios) — devuelve el string SVG
    directo en vez de subirlo como archivo nuevo. Pensada para consumidores que solo necesitan
    el SVG en la respuesta (p. ej. `apps/api/edecan_api/routers/analista.py`, WP-V6-06,
    `POST /v1/analista/{file_id}/grafico` → `{"svg": "..."}`).

    Lanza `ValueError` con el mismo mensaje en español que ya usa `GenerarGraficoTool` si
    `tipo`/`etiquetas`/`valores`/`series` no son válidos — el caller decide cómo mapearlo (p.
    ej. a un `400` HTTP).
    """
    tipo_normalizado = str(tipo or "").strip().lower()
    if tipo_normalizado not in _TIPOS:
        raise ValueError(f"'tipo' debe ser uno de: {', '.join(_TIPOS)}.")

    titulo_limpio = str(titulo or "").strip() or "Gráfico"
    etiquetas_limpias = _limpiar_etiquetas(etiquetas)
    if not etiquetas_limpias:
        raise ValueError("Necesito al menos una etiqueta en 'etiquetas'.")
    if len(etiquetas_limpias) > _MAX_CATEGORIAS:
        raise ValueError(
            f"Demasiadas etiquetas ({len(etiquetas_limpias)}); el máximo es {_MAX_CATEGORIAS}."
        )

    if tipo_normalizado == "lineas":
        series_norm, error = _normalizar_series(
            {"valores": valores, "series": series}, len(etiquetas_limpias)
        )
        if error:
            raise ValueError(error)
        return _grafico_lineas(titulo_limpio, etiquetas_limpias, series_norm)

    valores_norm, error = _normalizar_valores(valores, len(etiquetas_limpias))
    if error:
        raise ValueError(error)
    if tipo_normalizado == "barras":
        return _grafico_barras(titulo_limpio, etiquetas_limpias, valores_norm)

    error_dona = _validar_dona(valores_norm)
    if error_dona:
        raise ValueError(error_dona)
    return _grafico_dona(titulo_limpio, etiquetas_limpias, valores_norm)


# ---------------------------------------------------------------------------
# Validación / normalización de argumentos
# ---------------------------------------------------------------------------


def _limpiar_etiquetas(valor: Any) -> list[str]:
    if not isinstance(valor, list) or not valor:
        return []
    return [str(v).strip() or "(vacío)" for v in valor]


def _normalizar_valores(valor: Any, n_esperado: int) -> tuple[list[float], str | None]:
    if not isinstance(valor, list) or len(valor) != n_esperado:
        return [], f"'valores' debe tener exactamente {n_esperado} número(s) (uno por etiqueta)."
    numeros: list[float] = []
    for v in valor:
        try:
            numeros.append(float(v))
        except (TypeError, ValueError):
            return [], f"'{v}' en 'valores' no es un número."
    error = _rechazar_negativos(numeros)
    if error:
        return [], error
    return numeros, None


def _normalizar_series(
    args: dict[str, Any], n_esperado: int
) -> tuple[list[tuple[str, list[float]]], str | None]:
    series_arg = args.get("series")
    if not series_arg:
        valores, error = _normalizar_valores(args.get("valores"), n_esperado)
        if error:
            return [], error
        return [("Serie 1", valores)], None

    if not isinstance(series_arg, list):
        return [], "'series' debe ser una lista de objetos {nombre, valores}."
    if len(series_arg) > _MAX_SERIES:
        return [], f"Demasiadas series ({len(series_arg)}); el máximo es {_MAX_SERIES}."

    resultado: list[tuple[str, list[float]]] = []
    for i, serie in enumerate(series_arg):
        if not isinstance(serie, dict):
            return [], f"La serie #{i + 1} debe ser un objeto {{nombre, valores}}."
        nombre = str(serie.get("nombre") or f"Serie {i + 1}").strip() or f"Serie {i + 1}"
        valores, error = _normalizar_valores(serie.get("valores"), n_esperado)
        if error:
            return [], f"Serie «{nombre}»: {error}"
        resultado.append((nombre, valores))
    return resultado, None


def _rechazar_negativos(valores: list[float]) -> str | None:
    if any(v < 0 for v in valores):
        return "Todos los valores deben ser >= 0 (no se soportan valores negativos)."
    return None


def _validar_dona(valores: list[float]) -> str | None:
    if sum(valores) <= 0:
        return "La dona necesita que la suma de los valores sea mayor que 0."
    return None


# ---------------------------------------------------------------------------
# Helpers de formato / escala (deterministas)
# ---------------------------------------------------------------------------


def _fmt(x: float) -> str:
    """Formato determinista de números en el SVG: 2 decimales fijos, `.` como
    separador decimal siempre (independiente del locale del sistema)."""
    return f"{x:.2f}"


def _color(indice: int) -> str:
    return _PALETA[indice % len(_PALETA)]


def _nice_step(paso_crudo: float) -> float:
    """Redondea `paso_crudo` hacia arriba al siguiente número "bonito"
    (1, 2 o 5 × 10^n) — escala automática determinista para los ejes."""
    if paso_crudo <= 0:
        return 1.0
    exponente = math.floor(math.log10(paso_crudo))
    base = 10.0**exponente
    for multiplo in (1.0, 2.0, 5.0, 10.0):
        candidato = multiplo * base
        if paso_crudo <= candidato + 1e-9:
            return candidato
    return 10.0 * base  # inalcanzable: el 10.0 del for ya cubre este caso


def _escala_eje(valor_max: float, pasos: int = 4) -> tuple[float, float]:
    """`(tope, paso)`: `tope` es el primer múltiplo de `paso` >= `valor_max`
    (ambos números "bonitos"). `(1.0, 1.0)` si `valor_max <= 0`."""
    if valor_max <= 0:
        return 1.0, 1.0
    paso = _nice_step(valor_max / pasos)
    tope = paso
    while tope < valor_max - 1e-9:
        tope += paso
    return tope, paso


def _svg_abrir(titulo: str) -> list[str]:
    return [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_ANCHO} {_ALTO}" '
        f'width="{_ANCHO}" height="{_ALTO}" role="img" aria-label={quoteattr(titulo)}>',
        f'<rect x="0" y="0" width="{_ANCHO}" height="{_ALTO}" fill="#FFFFFF" />',
        f'<text x="{_fmt(_ANCHO / 2)}" y="28" font-family="{_FUENTE}" font-size="18" '
        f'font-weight="bold" fill="#1A1A1A" text-anchor="middle">{escape(titulo)}</text>',
    ]


def _svg_cerrar() -> str:
    return "</svg>"


def _eje_y_con_cuadricula(y_base: float, tope: float, paso: float, escala: float) -> list[str]:
    partes: list[str] = []
    n_lineas = round(tope / paso)
    for i in range(n_lineas + 1):
        valor_linea = i * paso
        y = y_base - valor_linea * escala
        partes.append(
            f'<line x1="{_fmt(_MARGEN_IZQ)}" y1="{_fmt(y)}" x2="{_fmt(_ANCHO - _MARGEN_DER)}" '
            f'y2="{_fmt(y)}" stroke="#E0E0E0" stroke-width="1" />'
        )
        partes.append(
            f'<text x="{_fmt(_MARGEN_IZQ - 8)}" y="{_fmt(y + 4)}" font-family="{_FUENTE}" '
            f'font-size="11" fill="#555555" text-anchor="end">{_fmt(valor_linea)}</text>'
        )
    return partes


# ---------------------------------------------------------------------------
# Barras
# ---------------------------------------------------------------------------


def _grafico_barras(titulo: str, etiquetas: list[str], valores: list[float]) -> str:
    n = len(etiquetas)
    ancho_plot = _ANCHO - _MARGEN_IZQ - _MARGEN_DER
    alto_plot = _ALTO - _MARGEN_SUP - _MARGEN_INF
    y_base = float(_ALTO - _MARGEN_INF)

    tope, paso = _escala_eje(max(valores))
    escala = alto_plot / tope

    partes = _svg_abrir(titulo)
    partes.extend(_eje_y_con_cuadricula(y_base, tope, paso, escala))

    ancho_categoria = ancho_plot / n
    ancho_barra = ancho_categoria * 0.6
    color = _color(0)
    for i, (etiqueta, valor) in enumerate(zip(etiquetas, valores, strict=True)):
        x_centro = _MARGEN_IZQ + ancho_categoria * (i + 0.5)
        x_barra = x_centro - ancho_barra / 2
        altura_barra = valor * escala
        y_barra = y_base - altura_barra
        partes.append(
            f'<rect x="{_fmt(x_barra)}" y="{_fmt(y_barra)}" width="{_fmt(ancho_barra)}" '
            f'height="{_fmt(altura_barra)}" fill="{color}" />'
        )
        partes.append(
            f'<text x="{_fmt(x_centro)}" y="{_fmt(y_barra - 6)}" font-family="{_FUENTE}" '
            f'font-size="11" fill="#1A1A1A" text-anchor="middle">{_fmt(valor)}</text>'
        )
        partes.append(
            f'<text x="{_fmt(x_centro)}" y="{_fmt(y_base + 18)}" font-family="{_FUENTE}" '
            f'font-size="11" fill="#333333" text-anchor="middle">{escape(etiqueta)}</text>'
        )

    partes.append(
        f'<line x1="{_fmt(_MARGEN_IZQ)}" y1="{_fmt(y_base)}" x2="{_fmt(_ANCHO - _MARGEN_DER)}" '
        f'y2="{_fmt(y_base)}" stroke="#1A1A1A" stroke-width="1.5" />'
    )
    partes.append(_svg_cerrar())
    return "\n".join(partes) + "\n"


# ---------------------------------------------------------------------------
# Líneas
# ---------------------------------------------------------------------------


def _grafico_lineas(
    titulo: str, etiquetas: list[str], series: list[tuple[str, list[float]]]
) -> str:
    n = len(etiquetas)
    ancho_plot = _ANCHO - _MARGEN_IZQ - _MARGEN_DER
    alto_plot = _ALTO - _MARGEN_SUP - _MARGEN_INF
    y_base = float(_ALTO - _MARGEN_INF)

    todos_los_valores = [v for _nombre, valores in series for v in valores]
    tope, paso = _escala_eje(max(todos_los_valores) if todos_los_valores else 0.0)
    escala = alto_plot / tope

    partes = _svg_abrir(titulo)
    partes.extend(_eje_y_con_cuadricula(y_base, tope, paso, escala))

    paso_x = ancho_plot / (n - 1) if n > 1 else 0.0

    def _x_de(indice: int) -> float:
        return _MARGEN_IZQ + (paso_x * indice if n > 1 else ancho_plot / 2)

    for i, etiqueta in enumerate(etiquetas):
        partes.append(
            f'<text x="{_fmt(_x_de(i))}" y="{_fmt(y_base + 18)}" font-family="{_FUENTE}" '
            f'font-size="11" fill="#333333" text-anchor="middle">{escape(etiqueta)}</text>'
        )

    for indice_serie, (_nombre, valores) in enumerate(series):
        color = _color(indice_serie)
        puntos = [(_x_de(i), y_base - v * escala) for i, v in enumerate(valores)]
        puntos_attr = " ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in puntos)
        partes.append(
            f'<polyline points="{puntos_attr}" fill="none" stroke="{color}" stroke-width="2" '
            'stroke-linejoin="round" stroke-linecap="round" />'
        )
        for x, y in puntos:
            partes.append(f'<circle cx="{_fmt(x)}" cy="{_fmt(y)}" r="3" fill="{color}" />')

    partes.extend(_leyenda_lineas(series))

    partes.append(
        f'<line x1="{_fmt(_MARGEN_IZQ)}" y1="{_fmt(y_base)}" x2="{_fmt(_ANCHO - _MARGEN_DER)}" '
        f'y2="{_fmt(y_base)}" stroke="#1A1A1A" stroke-width="1.5" />'
    )
    partes.append(_svg_cerrar())
    return "\n".join(partes) + "\n"


def _leyenda_lineas(series: list[tuple[str, list[float]]]) -> list[str]:
    if len(series) <= 1:
        return []  # una sola serie ya se identifica por el título; no hace falta leyenda
    ancho_plot = _ANCHO - _MARGEN_IZQ - _MARGEN_DER
    espacio = min(110.0, ancho_plot / len(series))
    y_leyenda = 44.0
    partes: list[str] = []
    for indice_serie, (nombre, _valores) in enumerate(series):
        color = _color(indice_serie)
        x_leyenda = _MARGEN_IZQ + indice_serie * espacio
        partes.append(
            f'<rect x="{_fmt(x_leyenda)}" y="{_fmt(y_leyenda - 9)}" width="10" height="10" '
            f'fill="{color}" />'
        )
        partes.append(
            f'<text x="{_fmt(x_leyenda + 14)}" y="{_fmt(y_leyenda)}" font-family="{_FUENTE}" '
            f'font-size="11" fill="#333333">{escape(nombre)}</text>'
        )
    return partes


# ---------------------------------------------------------------------------
# Dona
# ---------------------------------------------------------------------------


def _grafico_dona(titulo: str, etiquetas: list[str], valores: list[float]) -> str:
    total = sum(valores)
    circunferencia = 2 * math.pi * _DONA_RADIO

    partes = _svg_abrir(titulo)
    partes.append(
        f'<circle cx="{_fmt(_DONA_CX)}" cy="{_fmt(_DONA_CY)}" r="{_fmt(_DONA_RADIO)}" '
        f'fill="none" stroke="#E0E0E0" stroke-width="{_fmt(_DONA_GROSOR)}" />'
    )

    offset_acumulado = 0.0
    for i, valor in enumerate(valores):
        if valor <= 0:
            continue
        color = _color(i)
        largo = circunferencia * (valor / total)
        dasharray = f"{_fmt(largo)} {_fmt(circunferencia - largo)}"
        # Un <circle> arranca a las 3 en punto (0°); rotamos -90° para que la
        # primera porción empiece arriba (12 en punto) y avance en sentido
        # horario. `stroke-dashoffset` NEGATIVO acumulado encadena cada
        # porción justo donde terminó la anterior (offset positivo movería el
        # trazo hacia atrás, que es el sentido contrario al que queremos).
        partes.append(
            f'<circle cx="{_fmt(_DONA_CX)}" cy="{_fmt(_DONA_CY)}" r="{_fmt(_DONA_RADIO)}" '
            f'fill="none" stroke="{color}" stroke-width="{_fmt(_DONA_GROSOR)}" '
            f'stroke-dasharray="{dasharray}" stroke-dashoffset="{_fmt(-offset_acumulado)}" '
            f'transform="rotate(-90 {_fmt(_DONA_CX)} {_fmt(_DONA_CY)})" />'
        )
        offset_acumulado += largo

    partes.extend(_leyenda_dona(etiquetas, valores, total))
    partes.append(_svg_cerrar())
    return "\n".join(partes) + "\n"


def _leyenda_dona(etiquetas: list[str], valores: list[float], total: float) -> list[str]:
    x_leyenda = _DONA_CX + _DONA_RADIO + 40
    y_inicial = _DONA_CY - _DONA_RADIO + 10
    paso_y = min(24.0, (2 * _DONA_RADIO - 20) / max(len(etiquetas), 1))
    partes: list[str] = []
    for i, (etiqueta, valor) in enumerate(zip(etiquetas, valores, strict=True)):
        color = _color(i)
        y = y_inicial + i * paso_y
        porcentaje = (valor / total * 100) if total else 0.0
        partes.append(
            f'<rect x="{_fmt(x_leyenda)}" y="{_fmt(y - 9)}" width="10" height="10" '
            f'fill="{color}" />'
        )
        partes.append(
            f'<text x="{_fmt(x_leyenda + 14)}" y="{_fmt(y)}" font-family="{_FUENTE}" '
            f'font-size="11" fill="#333333">{escape(etiqueta)} ({_fmt(porcentaje)}%)</text>'
        )
    return partes
