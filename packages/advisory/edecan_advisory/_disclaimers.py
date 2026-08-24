"""Disclaimers obligatorios de `edecan_advisory` (ROADMAP_V2.md §8.3:
"Salud/Legal/Finanzas: informativo + disclaimer" — regla NO negociable del
producto, no una sugerencia de estilo).

Las tres constantes de abajo son el texto EXACTO que debe cerrar el
`content` de cualquier `ToolResult` que este paquete devuelva en su camino
feliz — nunca se parafrasean, nunca se traducen, nunca se recortan.
`tests/test_disclaimers.py::test_disclaimers_en_todas` verifica, para las 8
tools del paquete, que `resultado.content` TERMINA exactamente con uno de
estos tres strings — es el test más importante del WP.

`with_disclaimer` es el ÚNICO punto por el que debe pasar ese `content` antes
de volver al agente: cada módulo (`legal.py`, `salud.py`, `educacion.py`)
llama a `with_disclaimer(<kind>, texto)` como el último paso antes de
construir el `ToolResult`, nunca concatena el disclaimer a mano.
"""

from __future__ import annotations

from typing import Literal

DISCLAIMER_LEGAL = (
    "⚖️ Este análisis es informativo y no constituye asesoría legal. "
    "Consulta a un abogado antes de tomar decisiones."
)
DISCLAIMER_SALUD = (
    "🩺 Esta información es orientativa y no reemplaza a un profesional de la "
    "salud. No es un diagnóstico."
)
DISCLAIMER_EDU = (
    "🧑‍🏫 Contenido educativo generado por IA; verifica con fuentes oficiales "
    "para evaluaciones formales."
)

DisclaimerKind = Literal["legal", "salud", "edu"]

_DISCLAIMERS: dict[DisclaimerKind, str] = {
    "legal": DISCLAIMER_LEGAL,
    "salud": DISCLAIMER_SALUD,
    "edu": DISCLAIMER_EDU,
}


def with_disclaimer(kind: DisclaimerKind, texto: str) -> str:
    """Devuelve `texto` con el disclaimer de `kind` al final, separado por una
    línea en blanco.

    Idempotente: si `texto` ya termina con ese disclaimer exacto (p. ej.
    porque el caller ya lo agregó, o porque `analizar_laboratorio` antepuso
    su advertencia reforzada y luego llama a esta función igual) no lo
    duplica — así cualquier módulo puede tratar esta función como el paso
    final obligatorio sin tener que rastrear si alguien más ya la invocó
    antes sobre el mismo texto.
    """
    disclaimer = _DISCLAIMERS[kind]
    cuerpo = texto.rstrip()
    if cuerpo.endswith(disclaimer):
        return cuerpo
    if not cuerpo:
        return disclaimer
    return f"{cuerpo}\n\n{disclaimer}"
