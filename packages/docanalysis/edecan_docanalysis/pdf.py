"""Extracción de tablas de PDF (`extraer_tablas_pdf`, ROADMAP_V2.md §7.7, §5).

`pypdf` extrae texto por página (`page.extract_text()`); un PDF de texto
plano NO trae layout real (coordenadas de celda), así que las "tablas" se
detectan con una heurística sobre ese texto: una línea es candidata a fila de
tabla si, al partirla por tabs o corridas de 2+ espacios (`_SEPARADOR_RE`),
quedan al menos `_MIN_CAMPOS_FILA` campos no vacíos (≥3 campos ⇔ ≥2
separadores). Un bloque de `_MIN_FILAS_TABLA` o más líneas CONSECUTIVAS con
el MISMO número de campos ("consistentes") se considera una tabla y se
serializa a CSV (`csv.writer`, con comillas donde haga falta).

Esta heurística funciona bien con PDFs generados por herramientas de oficina
(columnas alineadas con espacios/tabs). Limitaciones conocidas, documentadas
también en `docs/analista.md`: no reconstruye tablas con solo bordes
gráficos y sin texto realmente tabulado, no funciona en PDFs escaneados como
imagen (ahí el texto ni siquiera existe — para eso hace falta OCR, ver
`edecan_docanalysis.vision`), y una celda vacía en medio de una fila puede
fusionarse con el separador vecino (limitación inherente a cualquier
detección de tablas basada solo en espacios).

Límites duros (`docs/analista.md`): `_MAX_PAGINAS` páginas procesadas por
llamada, `_MAX_TABLAS_POR_PAGINA` tablas detectadas por página,
`_MAX_TABLAS_TOTAL` tablas totales devueltas, `_MAX_FILAS_TABLA` filas por
tabla.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any

from edecan_core import Tool, ToolContext, ToolResult

from . import _s3
from ._util import parse_uuid

_MAX_PAGINAS = 30
_MAX_TABLAS_POR_PAGINA = 10
_MAX_TABLAS_TOTAL = 50
_MAX_FILAS_TABLA = 200
_MIN_FILAS_TABLA = 2
_MIN_CAMPOS_FILA = 3

_PDF_MIMES = {"application/pdf"}
_SEPARADOR_RE = re.compile(r"\t+| {2,}")


class ExtraerTablasPdfTool(Tool):
    name = "extraer_tablas_pdf"
    description = (
        "Extrae el texto de un PDF ya subido y detecta tablas dentro de ese texto "
        "por alineación de columnas (espacios múltiples o tabs), devolviéndolas "
        "como CSV. Funciona mejor con PDFs generados por herramientas de oficina; "
        "no reconstruye tablas de PDFs escaneados como imagen ni con layouts "
        "gráficos complejos (para PDFs escaneados usa analizar_imagen, que sí hace "
        "OCR vía visión)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_id": {"type": "string", "description": "id del archivo PDF ya subido."},
            "paginas": {
                "type": "array",
                "items": {"type": "integer"},
                "description": (
                    "Páginas a procesar (1-indexadas). Si se omite, procesa todas "
                    f"hasta un máximo de {_MAX_PAGINAS}."
                ),
            },
        },
        "required": ["file_id"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        file_id = parse_uuid(args.get("file_id"))
        if file_id is None:
            return ToolResult(content="'file_id' no es un identificador válido.")

        archivo = await _s3.descargar_archivo(ctx, file_id)
        if archivo is None:
            return ToolResult(content="No encontré ese archivo.")

        mime_normalizado = (archivo.mime or "").split(";")[0].strip().lower()
        if mime_normalizado not in _PDF_MIMES and not archivo.filename.lower().endswith(".pdf"):
            return ToolResult(content=f"'{archivo.filename}' no es un PDF.")

        try:
            paginas_texto, total_paginas = _extraer_texto_por_pagina(
                archivo.contenido, args.get("paginas")
            )
        except Exception as exc:  # noqa: BLE001 - PDF corrupto = error de negocio, no de programación
            return ToolResult(content=f"No pude leer '{archivo.filename}': {exc}")

        if not paginas_texto:
            return ToolResult(
                content=(
                    f"'{archivo.filename}' no tiene páginas para procesar "
                    "(o el rango pedido no existe en el documento)."
                )
            )

        tablas: list[dict[str, Any]] = []
        for numero_pagina, texto in paginas_texto:
            if len(tablas) >= _MAX_TABLAS_TOTAL:
                break
            bloques = _detectar_tablas(texto)[:_MAX_TABLAS_POR_PAGINA]
            for indice, bloque in enumerate(bloques):
                if len(tablas) >= _MAX_TABLAS_TOTAL:
                    break
                bloque_capado = bloque[:_MAX_FILAS_TABLA]
                tablas.append(
                    {
                        "pagina": numero_pagina,
                        "indice": indice,
                        "csv": _bloque_a_csv(bloque_capado),
                        "filas": len(bloque_capado),
                    }
                )

        resumen = _resumen_texto(archivo.filename, total_paginas, paginas_texto, tablas)
        return ToolResult(
            content=resumen,
            data={"paginas": [n for n, _ in paginas_texto], "tablas_csv": tablas},
        )


def _paginas_pedidas(paginas_arg: Any, total: int) -> list[int]:
    """Normaliza `paginas` (lista de números 1-indexados) a un rango válido,
    deduplicado (preservando el orden de aparición) y capado a `_MAX_PAGINAS`.

    Si `paginas_arg` no es una lista utilizable, procesa todas las páginas del
    documento (1..total) hasta `_MAX_PAGINAS`.
    """
    if isinstance(paginas_arg, list) and paginas_arg:
        vistas: set[int] = set()
        seleccion: list[int] = []
        for valor in paginas_arg:
            try:
                n = int(valor)
            except (TypeError, ValueError):
                continue
            if 1 <= n <= total and n not in vistas:
                vistas.add(n)
                seleccion.append(n)
        return seleccion[:_MAX_PAGINAS]
    return list(range(1, min(total, _MAX_PAGINAS) + 1))


def _extraer_texto_por_pagina(
    contenido: bytes, paginas_arg: Any
) -> tuple[list[tuple[int, str]], int]:
    from pypdf import PdfReader  # import perezoso: solo lo necesita esta función

    lector = PdfReader(io.BytesIO(contenido))
    total = len(lector.pages)
    seleccion = _paginas_pedidas(paginas_arg, total)

    resultado: list[tuple[int, str]] = []
    for numero in seleccion:
        pagina = lector.pages[numero - 1]
        resultado.append((numero, pagina.extract_text() or ""))
    return resultado, total


def _detectar_tablas(texto: str) -> list[list[list[str]]]:
    """Agrupa líneas consecutivas con el mismo número de campos (≥`_MIN_CAMPOS_FILA`)
    en bloques de tabla; cada bloque necesita al menos `_MIN_FILAS_TABLA` líneas."""
    bloques: list[list[list[str]]] = []
    bloque_actual: list[list[str]] = []
    campos_actuales: int | None = None

    def _cerrar_bloque() -> None:
        if len(bloque_actual) >= _MIN_FILAS_TABLA:
            bloques.append(list(bloque_actual))

    for linea in texto.splitlines():
        campos = [c for c in (p.strip() for p in _SEPARADOR_RE.split(linea.strip())) if c]
        if len(campos) >= _MIN_CAMPOS_FILA and len(campos) == campos_actuales:
            bloque_actual.append(campos)
            continue
        _cerrar_bloque()
        if len(campos) >= _MIN_CAMPOS_FILA:
            bloque_actual = [campos]
            campos_actuales = len(campos)
        else:
            bloque_actual = []
            campos_actuales = None
    _cerrar_bloque()
    return bloques


def _bloque_a_csv(bloque: list[list[str]]) -> str:
    buffer = io.StringIO()
    escritor = csv.writer(buffer)
    for fila in bloque:
        escritor.writerow(fila)
    return buffer.getvalue()


def _resumen_texto(
    filename: str,
    total_paginas: int,
    paginas_texto: list[tuple[int, str]],
    tablas: list[dict[str, Any]],
) -> str:
    procesadas = [n for n, _ in paginas_texto]
    lineas = [
        f"Revisé «{filename}» ({total_paginas} página(s) en total, "
        f"{len(procesadas)} procesada(s): {procesadas})."
    ]
    if not tablas:
        lineas.append("No detecté tablas con la heurística de columnas alineadas.")
    else:
        lineas.append(f"Detecté {len(tablas)} tabla(s):")
        for t in tablas:
            lineas.append(f"- página {t['pagina']}, tabla #{t['indice'] + 1}: {t['filas']} fila(s)")
    return "\n".join(lineas)
