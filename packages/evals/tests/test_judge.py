"""Tests de `edecan_evals.judge` — 100% offline: inyecta `FakeLLMProvider` en
un `LLMRouter` real (mismo patrón que `packages/llm/tests/test_llm_router.py`,
`router._provider = ...`), así que nunca hay red real ni costo. `edecan_llm`
ya es un paquete real del workspace, así que este test SÍ puede importarlo.
"""

from __future__ import annotations

from types import SimpleNamespace

from edecan_evals import judge
from edecan_evals.fakes import FakeLLMProvider
from edecan_evals.schema import GuionEntry
from edecan_llm.router import LLMRouter


def _router_con_respuesta(texto: str) -> LLMRouter:
    router = LLMRouter(SimpleNamespace())
    router._provider = FakeLLMProvider({".*": GuionEntry(texto=texto)})  # noqa: SLF001
    return router


async def test_evaluar_tono_persona_parsea_formato_esperado() -> None:
    router = _router_con_respuesta(
        "PUNTUACIÓN: 4\nJUSTIFICACIÓN: Trata de usted consistentemente y sin emojis."
    )
    veredicto = await judge.evaluar_tono_persona(
        router,
        persona={"formalidad": 3, "emojis": False},
        mensaje_usuario="¿Cómo estás?",
        respuesta_asistente="Estoy muy bien, ¿en qué puedo ayudarle?",
    )
    assert veredicto.puntuacion == 4
    assert "usted" in veredicto.justificacion.lower()


async def test_evaluar_tono_persona_recupera_digito_suelto_sin_formato() -> None:
    router = _router_con_respuesta("Yo le pondría un 3 de 5, mezcla de aciertos y errores.")
    veredicto = await judge.evaluar_tono_persona(
        router, persona={}, mensaje_usuario="hola", respuesta_asistente="hola"
    )
    assert veredicto.puntuacion == 3


async def test_evaluar_tono_persona_sin_ningun_digito_da_cero() -> None:
    router = _router_con_respuesta("No sigo ningún formato reconocible aquí.")
    veredicto = await judge.evaluar_tono_persona(
        router, persona={}, mensaje_usuario="hola", respuesta_asistente="hola"
    )
    assert veredicto.puntuacion == 0


def test_parsear_veredicto_guarda_texto_crudo() -> None:
    crudo = "PUNTUACIÓN: 5\nJUSTIFICACIÓN: Perfecto."
    veredicto = judge._parsear_veredicto(crudo)  # noqa: SLF001 (acceso intencional en test)
    assert veredicto.puntuacion == 5
    assert veredicto.justificacion == "Perfecto."
    assert veredicto.texto_crudo == crudo


def test_construir_prompt_incluye_persona_mensaje_y_respuesta() -> None:
    prompt = judge._construir_prompt(  # noqa: SLF001
        persona={"formalidad": 3},
        mensaje_usuario="¿qué hora es?",
        respuesta_asistente="son las 5",
    )
    assert "formalidad=3" in prompt
    assert "¿qué hora es?" in prompt
    assert "son las 5" in prompt
