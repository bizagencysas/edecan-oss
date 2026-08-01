"""Juez LLM (alias `"rapido"`) para calidad de tono/persona — SOLO modo `--live`.

`edecan_evals.runner` invoca `evaluar_tono_persona` únicamente cuando se pasa
`--live` a `python -m edecan_evals.run`: cada llamada consume tokens reales
del proveedor LLM configurado (Anthropic u OpenAI-compatible) vía
`edecan_llm.LLMRouter`. **Nunca se activa desde `packages/evals/tests/`** —
los tests de este módulo inyectan `edecan_evals.fakes.FakeLLMProvider` en un
`LLMRouter` real (mismo patrón que `packages/llm/tests/test_llm_router.py`),
así que no hay red real ni costo en `make test`.

La rúbrica de abajo es la copia embebida de `prompts/juez_v1.md` (§ versionado
en `prompts/README.md`): si iteras el prompt allí, actualiza también esta
constante para que `edecan_evals` evalúe con la misma rúbrica que se
documenta como fuente canónica.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from edecan_llm.base import ChatMessage, CompletionRequest
from edecan_llm.router import LLMRouter
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

RUBRICA_JUEZ = """\
Eres un juez de calidad que evalúa UNA respuesta de un asistente conversacional \
en español, comparándola contra la configuración de personalidad ("persona") \
que se supone debía respetar. No evalúes si el contenido es correcto: solo \
evalúa TONO, FORMALIDAD y CONSISTENCIA DE PERSONA.

Criterios (pondera los cuatro por igual):
1. Formalidad: si formalidad >= 2 la respuesta debe tratar de "usted"; si \
formalidad <= 1 debe tratar de "tú". Un desliz aislado resta poco; tratar \
sistemáticamente en el registro contrario resta mucho.
2. Emojis: si `emojis` es falso, la respuesta NO debe usar emojis. Si es \
verdadero, su ausencia total no es un error grave, pero su presencia natural \
suma.
3. Tono: la respuesta debe sonar como el `tono` declarado (p. ej. "cálido y \
profesional", "directo y conciso").
4. Instrucciones/rasgos: si la persona declara instrucciones o rasgos \
permanentes, la respuesta no debería contradecirlos.

Responde EXACTAMENTE en este formato, sin texto adicional antes o después:
PUNTUACIÓN: <un entero 1-5>
JUSTIFICACIÓN: <una o dos frases breves>

Escala:
5 = respeta todos los criterios aplicables sin deslices.
4 = respeta casi todo; a lo sumo un desliz menor.
3 = mezcla aciertos y errores perceptibles pero no groseros.
2 = contradice al menos un criterio de forma clara (p. ej. tutea con formalidad alta).
1 = ignora la persona por completo o sería inaceptable para el usuario.
"""


class VeredictoJuez(BaseModel):
    """Resultado de `evaluar_tono_persona`."""

    puntuacion: int = Field(ge=0, le=5)
    """1-5 si el juez respondió en el formato esperado; 0 si no se pudo parsear."""
    justificacion: str
    texto_crudo: str
    """Respuesta completa del juez, sin procesar — útil para depurar/auditar."""


def _construir_prompt(
    *, persona: dict[str, Any], mensaje_usuario: str, respuesta_asistente: str
) -> str:
    persona_legible = ", ".join(f"{clave}={valor!r}" for clave, valor in sorted(persona.items()))
    return (
        f"Persona configurada: {persona_legible or '(usa todos los valores por defecto)'}\n\n"
        f"Mensaje del usuario:\n{mensaje_usuario}\n\n"
        f"Respuesta del asistente a evaluar:\n{respuesta_asistente}"
    )


_PATRON_PUNTUACION = re.compile(r"PUNTUACI[OÓ]N:\s*([1-5])", re.IGNORECASE)
_PATRON_JUSTIFICACION = re.compile(r"JUSTIFICACI[OÓ]N:\s*(.+)", re.IGNORECASE | re.DOTALL)
_PATRON_DIGITO_SUELTO = re.compile(r"[1-5]")


def _parsear_veredicto(texto: str) -> VeredictoJuez:
    coincidencia_puntuacion = _PATRON_PUNTUACION.search(texto)
    if coincidencia_puntuacion:
        puntuacion = int(coincidencia_puntuacion.group(1))
    else:
        # El juez no siguió el formato pedido al pie de la letra: intenta
        # rescatar un solo dígito 1-5 en vez de descartar el veredicto entero.
        logger.warning(
            "Veredicto del juez sin el formato esperado, se intenta recuperar: %r", texto
        )
        digito_suelto = _PATRON_DIGITO_SUELTO.search(texto)
        puntuacion = int(digito_suelto.group(0)) if digito_suelto else 0

    coincidencia_justificacion = _PATRON_JUSTIFICACION.search(texto)
    justificacion = (
        coincidencia_justificacion.group(1).strip() if coincidencia_justificacion else texto.strip()
    )
    return VeredictoJuez(puntuacion=puntuacion, justificacion=justificacion, texto_crudo=texto)


async def evaluar_tono_persona(
    router: LLMRouter,
    *,
    persona: dict[str, Any],
    mensaje_usuario: str,
    respuesta_asistente: str,
    tenant_flags: dict[str, Any] | None = None,
) -> VeredictoJuez:
    """Pide al alias LLM `"rapido"` que puntúe 1-5 la consistencia de tono/persona.

    SOLO se debe llamar en modo `--live` (consume una completion real). Ver
    docstring del módulo.
    """
    prompt = _construir_prompt(
        persona=persona, mensaje_usuario=mensaje_usuario, respuesta_asistente=respuesta_asistente
    )
    req = CompletionRequest(
        model="",  # `LLMRouter.complete` sobrescribe `model` con el resuelto para el alias.
        system=RUBRICA_JUEZ,
        messages=[ChatMessage(role="user", content=prompt)],
        max_tokens=300,
        temperature=0.0,
    )
    respuesta = await router.complete("rapido", tenant_flags or {}, req)
    return _parsear_veredicto(respuesta.text)
