"""Plantillas internas de `generar_borrador_legal` (ROADMAP_V2.md §7.7:
"plantillas internas en español con placeholders {parte_a}, {fecha}...").

`renderizar` nunca lanza `KeyError` por un campo faltante: `_CamposSeguros`
devuelve `"[campo]"` como valor visible en su lugar, así el borrador sale
completo (aunque incompleto en ese punto) en vez de reventar la herramienta
por un campo opcional que el modelo no mandó — el LLM que lo pule después
(`legal._pulir_redaccion`) puede notar el hueco, y el disclaimer/advertencia
de "BORRADOR" ya le dice al usuario que debe revisarlo de todas formas con un
abogado antes de usarlo.
"""

from __future__ import annotations

from datetime import date
from typing import Any

#: Tipos soportados por `generar_borrador_legal` (ROADMAP_V2.md §7.7:
#: "tipo: nda|carta_formal|acuerdo_simple").
TIPOS_BORRADOR: tuple[str, ...] = ("nda", "carta_formal", "acuerdo_simple")

ETIQUETAS_BORRADOR: dict[str, str] = {
    "nda": "un acuerdo de confidencialidad (NDA)",
    "carta_formal": "una carta formal",
    "acuerdo_simple": "un acuerdo simple",
}

_NDA = """ACUERDO DE CONFIDENCIALIDAD (NDA)

Fecha: {fecha}

Entre {parte_a} ("la Parte Reveladora") y {parte_b} ("la Parte Receptora"), en relación
con {objeto}, ambas partes acuerdan lo siguiente:

1. La Parte Receptora mantendrá confidencial toda información compartida por la Parte
   Reveladora relacionada con {objeto}, y solo la usará para el propósito aquí descrito.
2. Esta obligación de confidencialidad tendrá una vigencia de {vigencia}.
3. La información confidencial no podrá divulgarse a terceros sin autorización escrita
   previa de la Parte Reveladora.
4. Este documento se rige por las leyes de {jurisdiccion}.

_________________________               _________________________
{parte_a}                                 {parte_b}
"""

_CARTA_FORMAL = """{fecha}

{destinatario}

Asunto: {asunto}

Estimado/a {destinatario}:

{cuerpo}

Atentamente,

{remitente}
"""

_ACUERDO_SIMPLE = """ACUERDO SIMPLE

Fecha: {fecha}

Entre {parte_a} y {parte_b}, referente a {objeto}, ambas partes acuerdan lo siguiente:

{terminos}

Vigencia: {vigencia}

_________________________               _________________________
{parte_a}                                 {parte_b}
"""

_PLANTILLAS: dict[str, str] = {
    "nda": _NDA,
    "carta_formal": _CARTA_FORMAL,
    "acuerdo_simple": _ACUERDO_SIMPLE,
}


class _CamposSeguros(dict):
    """`dict` que nunca lanza `KeyError` en `str.format_map`: un placeholder
    sin valor se renderiza como `[campo]` en vez de reventar la herramienta."""

    def __missing__(self, key: str) -> str:
        return f"[{key}]"


def renderizar(tipo: str, campos: dict[str, Any]) -> str:
    """Rellena la plantilla `tipo` con `campos` (placeholders faltantes →
    `[campo]`, ver `_CamposSeguros`; `fecha` cae a hoy si no viene en
    `campos`). Lanza `ValueError` si `tipo` no es uno de `TIPOS_BORRADOR`."""
    plantilla = _PLANTILLAS.get(tipo)
    if plantilla is None:
        raise ValueError(f"tipo de borrador desconocido: {tipo!r}")
    valores = _CamposSeguros({k: str(v) for k, v in campos.items() if v is not None})
    valores.setdefault("fecha", date.today().isoformat())
    return plantilla.format_map(valores)
