"""Escalamiento por desacuerdo entre modelos (PHASE2.md §111).

Para pasos de alto riesgo (perfiles con `permite_dangerous_con_confirmacion`),
el resultado primario se contrasta con un segundo modelo sobre el MISMO
objetivo. Si ambos discrepan sustantivamente, el desacuerdo se señala en la
respuesta final en vez de publicarse en silencio un único veredicto.

El cross-check usa un alias de modelo DISTINTO al primario a propósito (mismo
criterio que el refutador del IDE: un reviewer del mismo modelo se aprueba a
sí mismo). El LLM inyectado (`llm_router`) ya soporta varios alias
(`"principal"`, `"profundo"`, `"rapido"`), así que no hace falta proveedor
nuevo.
"""

from __future__ import annotations

from typing import Any

from edecan_core.llm_types import ChatMessage, CompletionRequest

_ALIAS_CRUZADO = "rapido"
"""Alias del segundo modelo. Distinto del primario (`profundo`) para que el
contraste sea real, no un eco del mismo modelo."""

_MAX_TOKENS_CRUZADO = 256

_SYSTEM_CRUZADO = (
    "Eres Edecán verificando la respuesta de otro sub-agente. Lee el objetivo "
    "y la respuesta primaria, y responde SOLO con 'ACUERDA' o 'DISCREPA' "
    "seguido de una explicación de una frase. Discrepa solo si la respuesta "
    "contradice el objetivo, afirma un dato sin fundamento evidente, o saca "
    "una conclusión incompatible con lo pedido."
)


async def cross_check(
    llm_router: Any,
    flags: dict[str, Any],
    objetivo: str,
    resultado_primario: str,
) -> dict[str, Any]:
    """Compara el resultado primario con un segundo modelo.

    Devuelve `{"disagree": bool, "summary": str}`. Nunca lanza: cualquier
    fallo (proveedor caído, alias inexistente, timeout) devuelve
    `{"disagree": False, "summary": ""}` — el cross-check es una señal de
    seguridad ADICIONAL, no una frontera que pueda tumbar un paso ya
    completado."""
    try:
        provider, model = llm_router.resolve(_ALIAS_CRUZADO, flags)
        request = CompletionRequest(
            model=model,
            system=_SYSTEM_CRUZADO,
            messages=[
                ChatMessage(
                    role="user",
                    content=(
                        f"Objetivo: {objetivo}\n\n"
                        f"Respuesta primaria: {resultado_primario}"
                    ),
                )
            ],
            max_tokens=_MAX_TOKENS_CRUZADO,
            temperature=0.0,
        )
        response = await provider.complete(request)
        texto = (getattr(response, "text", "") or "").strip()
        if not texto:
            return {"disagree": False, "summary": ""}
        upper = texto.upper()
        disagree = upper.startswith("DISCREPA")
        return {"disagree": disagree, "summary": texto}
    except Exception:  # noqa: BLE001 - cross-check best-effort
        return {"disagree": False, "summary": ""}


__all__ = ["cross_check"]