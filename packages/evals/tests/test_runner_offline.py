"""Test de integración offline de `edecan_evals.runner` contra un DOBLE LOCAL
del contrato de `edecan_core.agent.Agent`/`Tool` (§10.7).

Por qué un doble en vez de importar `edecan_core` de verdad: `ARCHITECTURE.md`
§10.1 exige que los tests de un paquete no importen paquetes hermanos (ver
también `packages/connectors/tests/conftest.py`, que hace exactamente esto
mismo con `FakeTokenBundle` en vez de `edecan_schemas.TokenBundle`). Este
archivo replica la FORMA del loop de tool-use documentado en §10.7 (máx. 8
iteraciones; eventos `tool_start`/`text_delta`/`error`) usando únicamente
`edecan_llm` (ya real) y sustituye el único punto de `edecan_evals.runner`
que importaría `edecan_core` (`runner._construir_agente`) vía
`monkeypatch.setattr`. Así se ejercita la lógica REAL de
`ejecutar_caso`/`ejecutar_suite`/`evaluar_caso` — guion, historial
multi-turno, evaluación de `Esperado` — de punta a punta, hoy, offline y
determinista, sin esperar a que `edecan_core` exista en este checkout.

En el monorepo ensamblado (`edecan_core` instalado), `runner._construir_agente`
sin parchear construye el `Agent` real: el camino de producción no cambia.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from typing import Any

import pytest
from edecan_evals import loader, runner
from edecan_llm.base import ChatMessage, CompletionRequest
from edecan_llm.router import LLMRouter


@dataclass
class _EventoDePrueba:
    """Doble local de un `AgentEvent` (§10.7): solo los campos que
    `runner._leer` consulta (`type`/`text`/`name`/`message`)."""

    type: str
    text: str | None = None
    name: str | None = None
    message: str | None = None


class _HerramientaLocalDePrueba:
    """Doble local del contrato `Tool.run(ctx, args) -> ToolResult` (§10.7):
    no ejecuta nada real, solo confirma la llamada."""

    def __init__(self, nombre: str) -> None:
        self.name = nombre

    async def run(self, ctx: Any, args: dict[str, Any]) -> Any:
        return _ResultadoDePrueba(content=f"[doble local] '{self.name}' ejecutada con {args!r}.")


@dataclass
class _ResultadoDePrueba:
    content: str


class _AgenteLocalDePrueba:
    """Doble local de `edecan_core.agent.Agent`: mismo loop de tool-use (máx.
    8 iteraciones) que describe §10.7, implementado solo con las primitivas
    de `edecan_llm` — nunca importa `edecan_core`."""

    def __init__(self, router: LLMRouter, nombres_tools: Iterable[str]) -> None:
        self._router = router
        self._tools = {nombre: _HerramientaLocalDePrueba(nombre) for nombre in nombres_tools}

    async def run_turn(
        self,
        *,
        ctx: Any,
        persona: Any,
        history: list[ChatMessage],
        user_text: str,
        flags: dict[str, Any],
    ) -> AsyncIterator[_EventoDePrueba]:
        mensajes = [*history, ChatMessage(role="user", content=user_text)]
        for _ in range(8):
            respuesta = await self._router.complete(
                "principal", flags, CompletionRequest(model="doble-de-prueba", messages=mensajes)
            )
            if respuesta.tool_calls:
                for llamada in respuesta.tool_calls:
                    yield _EventoDePrueba(type="tool_start", name=llamada.name)
                    herramienta = self._tools.get(llamada.name)
                    resultado = (
                        await herramienta.run(ctx, llamada.arguments)
                        if herramienta is not None
                        else _ResultadoDePrueba(content="herramienta desconocida")
                    )
                    yield _EventoDePrueba(type="tool_end", name=llamada.name)
                    mensajes = [
                        *mensajes,
                        ChatMessage(
                            role="assistant",
                            content=[
                                {
                                    "type": "tool_use",
                                    "id": llamada.id,
                                    "name": llamada.name,
                                    "input": llamada.arguments,
                                }
                            ],
                        ),
                        ChatMessage(
                            role="tool",
                            content=[
                                {
                                    "type": "tool_result",
                                    "tool_use_id": llamada.id,
                                    "content": resultado.content,
                                }
                            ],
                        ),
                    ]
                continue
            if respuesta.text:
                yield _EventoDePrueba(type="text_delta", text=respuesta.text)
            return
        yield _EventoDePrueba(type="error", message="máximo de iteraciones alcanzado")


def _construir_agente_de_prueba(
    router: LLMRouter, nombres_tools: Iterable[str]
) -> _AgenteLocalDePrueba:
    return _AgenteLocalDePrueba(router, nombres_tools)


@pytest.fixture(autouse=True)
def _usar_agente_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sustituye el único punto de `edecan_evals.runner` que importaría
    `edecan_core` por el doble local de este archivo."""
    monkeypatch.setattr(runner, "_construir_agente", _construir_agente_de_prueba)


async def test_tool_choice_offline_con_guion_fake_aprueba_todo() -> None:
    suite = loader.cargar_suite("tool_choice")
    resultado = await runner.ejecutar_suite(suite, live=False)

    fallos = [(c.caso_id, c.razones) for c in resultado.casos if not c.aprobado]
    assert fallos == []
    assert resultado.total == len(suite.casos) == 8
    assert resultado.aprobados == resultado.total


async def test_sin_linkedin_offline_rechaza_sin_llamar_herramientas() -> None:
    suite = loader.cargar_suite("sin_linkedin")
    resultado = await runner.ejecutar_suite(suite, live=False)

    fallos = [(c.caso_id, c.razones) for c in resultado.casos if not c.aprobado]
    assert fallos == []
    for caso in resultado.casos:
        assert caso.tools_llamadas == []


async def test_memoria_offline_multi_turno_acumula_historial() -> None:
    suite = loader.cargar_suite("memoria")
    resultado = await runner.ejecutar_suite(suite, live=False)

    fallos = [(c.caso_id, c.razones) for c in resultado.casos if not c.aprobado]
    assert fallos == []


async def test_ejecutar_caso_individual_persona_se_construye_desde_dict() -> None:
    suite = loader.cargar_suite("persona_consistencia")
    caso = next(c for c in suite.casos if c.id == "pc01_formal_sin_emoji")

    resultado = await runner.ejecutar_caso(caso, suite.guion, live=False)

    assert resultado.aprobado, resultado.razones


async def test_ejecutar_suite_completa_devuelve_conteo_correcto() -> None:
    suite = loader.cargar_suite("seguridad_prompt_injection")
    resultado = await runner.ejecutar_suite(suite, live=False)

    assert resultado.total == len(suite.casos)
    assert resultado.aprobados == resultado.total
