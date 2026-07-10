"""Tests de `edecan_automations.runner.run_automation` — `Agent` monkeypatched
(mismo patrón que `apps/api/tests/test_conversations.py`: se sustituye el
símbolo `Agent` ya importado en el módulo bajo prueba, en vez de importar el
`Agent` real de `edecan_core` para armar un doble)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import edecan_automations.runner as runner_module
import pytest
from edecan_automations.runner import EXCLUDED_TOOL_NAMES, RunnerDeps, run_automation


class FakeToolSpec:
    def __init__(self, name: str) -> None:
        self.name = name
        self.description = f"tool falsa {name}"
        self.input_schema: dict[str, Any] = {}


class FakeTool:
    def __init__(self, name: str, *, dangerous: bool = False) -> None:
        self.name = name
        self.description = f"tool falsa {name}"
        self.dangerous = dangerous


class FakeRegistry:
    """Doble de `edecan_core.tools.ToolRegistry`: solo implementa la API
    pública que usa `_build_safe_registry` (`specs`/`get`/`register`)."""

    def __init__(self, tools: list[FakeTool]) -> None:
        self._tools = {t.name: t for t in tools}
        self.registered: list[str] = []

    def specs(self, flags: dict[str, Any]) -> list[FakeToolSpec]:
        return [FakeToolSpec(name) for name in self._tools]

    def get(self, name: str) -> FakeTool | None:
        return self._tools.get(name)

    def register(self, tool: FakeTool) -> None:
        self.registered.append(tool.name)
        self._tools[tool.name] = tool


def _make_deps(*, registry: FakeRegistry, save_run) -> RunnerDeps:
    ctx = SimpleNamespace(extras={"approved_tool_calls": {"algo-viejo"}})
    return RunnerDeps(
        ctx=ctx,
        llm_router=SimpleNamespace(),
        registry=registry,  # type: ignore[arg-type]
        persona=SimpleNamespace(),
        flags={},
        save_run=save_run,
    )


def _recorder():
    llamadas: list[tuple[str, dict[str, Any]]] = []

    async def save_run(status: str, detalle: dict[str, Any]) -> None:
        llamadas.append((status, detalle))

    return save_run, llamadas


AUTOMATION_BASE = {"accion": {"kind": "agent_instruction", "instruccion": "Resume mis correos."}}


async def test_run_automation_sin_instruccion_guarda_error(monkeypatch: pytest.MonkeyPatch) -> None:
    save_run, llamadas = _recorder()
    deps = _make_deps(registry=FakeRegistry([]), save_run=save_run)

    await run_automation({"accion": {"kind": "agent_instruction", "instruccion": "   "}}, deps)

    assert llamadas == [("error", {"error": "La automatización no tiene instrucción."})]


async def test_run_automation_texto_termina_en_done(monkeypatch: pytest.MonkeyPatch) -> None:
    class ScriptedAgent:
        def __init__(self, llm_router, registry) -> None:
            self.registry = registry

        async def run_turn(self, *, ctx, persona, history, user_text, flags):
            assert user_text == "Resume mis correos."
            assert history == []
            yield {"type": "text_delta", "text": "Hola "}
            yield {"type": "text_delta", "text": "mundo"}
            yield {"type": "done", "usage": {"input_tokens": 5, "output_tokens": 3}}

    monkeypatch.setattr(runner_module, "Agent", ScriptedAgent)
    save_run, llamadas = _recorder()
    deps = _make_deps(registry=FakeRegistry([]), save_run=save_run)

    await run_automation(AUTOMATION_BASE, deps)

    assert len(llamadas) == 1
    status, detalle = llamadas[0]
    assert status == "done"
    assert detalle["resultado"] == "Hola mundo"
    assert detalle["usage"] == {"input_tokens": 5, "output_tokens": 3}


async def test_run_automation_tool_dangerous_pausa_en_waiting_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ScriptedAgent:
        def __init__(self, llm_router, registry) -> None:
            pass

        async def run_turn(self, *, ctx, persona, history, user_text, flags):
            yield {"type": "tool_start", "name": "enviar_correo", "args": {"a": "x@example.com"}}
            yield {
                "type": "confirmation_required",
                "tool_call_id": "call-1",
                "name": "enviar_correo",
                "args": {"a": "x@example.com"},
            }

    monkeypatch.setattr(runner_module, "Agent", ScriptedAgent)
    save_run, llamadas = _recorder()
    deps = _make_deps(registry=FakeRegistry([]), save_run=save_run)

    await run_automation(AUTOMATION_BASE, deps)

    assert len(llamadas) == 1
    status, detalle = llamadas[0]
    assert status == "waiting_confirmation"
    assert detalle["pendiente"] == {
        "tool_call_id": "call-1",
        "name": "enviar_correo",
        "args": {"a": "x@example.com"},
    }


async def test_run_automation_error_del_turno_se_persiste(monkeypatch: pytest.MonkeyPatch) -> None:
    class ScriptedAgent:
        def __init__(self, llm_router, registry) -> None:
            pass

        async def run_turn(self, *, ctx, persona, history, user_text, flags):
            yield {"type": "error", "message": "el proveedor LLM no respondió"}

    monkeypatch.setattr(runner_module, "Agent", ScriptedAgent)
    save_run, llamadas = _recorder()
    deps = _make_deps(registry=FakeRegistry([]), save_run=save_run)

    await run_automation(AUTOMATION_BASE, deps)

    assert llamadas == [("error", {"error": "el proveedor LLM no respondió", "tool_log": []})]


async def test_run_automation_fuerza_approved_tool_calls_vacio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Aunque el `ctx` que llega tenga algo en `approved_tool_calls` (bug de
    wiring del caller), `run_automation` lo pisa con un `set()` vacío."""
    visto: dict[str, Any] = {}

    class ScriptedAgent:
        def __init__(self, llm_router, registry) -> None:
            pass

        async def run_turn(self, *, ctx, persona, history, user_text, flags):
            visto["approved"] = set(ctx.extras["approved_tool_calls"])
            yield {"type": "done", "usage": {}}

    monkeypatch.setattr(runner_module, "Agent", ScriptedAgent)
    save_run, _ = _recorder()
    deps = _make_deps(registry=FakeRegistry([]), save_run=save_run)
    assert deps.ctx.extras["approved_tool_calls"] == {"algo-viejo"}  # precondición del fixture

    await run_automation(AUTOMATION_BASE, deps)

    assert visto["approved"] == set()


async def test_build_safe_registry_excluye_dangerous_y_nombres_de_recursion() -> None:
    registry = FakeRegistry(
        [
            FakeTool("crear_recordatorio"),
            FakeTool("enviar_correo", dangerous=True),
            FakeTool("gestionar_automatizacion", dangerous=True),
            FakeTool("delegar_mision", dangerous=False),  # excluida por NOMBRE, no por dangerous
        ]
    )

    safe = runner_module._build_safe_registry(registry, flags={})

    assert safe.get("crear_recordatorio") is not None
    assert safe.get("enviar_correo") is None
    assert safe.get("gestionar_automatizacion") is None
    assert safe.get("delegar_mision") is None
    assert EXCLUDED_TOOL_NAMES == {"delegar_mision", "gestionar_automatizacion"}
