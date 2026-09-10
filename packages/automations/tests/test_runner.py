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
    """Runs headless genéricos pisan `approved_tool_calls` a vacío aunque el
    caller haya dejado algo — sin humano no hay confirmación posible."""
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


async def test_run_automation_builder_mode_respeta_preaprobaciones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    visto: dict[str, Any] = {}

    class ScriptedAgent:
        def __init__(self, llm_router, registry) -> None:
            pass

        async def run_turn(self, *, ctx, persona, history, user_text, flags, **kwargs):
            visto["approved"] = set(ctx.extras["approved_tool_calls"])
            visto["extra_tools"] = kwargs.get("extra_tools")
            yield {"type": "done", "usage": {}}

    monkeypatch.setattr(runner_module, "Agent", ScriptedAgent)
    save_run, _ = _recorder()
    deps = _make_deps(registry=FakeRegistry([]), save_run=save_run)
    deps.ctx.extras["approved_tool_calls"] = {"acceder_codigo_local", "mcp_test"}
    deps.builder_mode = True
    extra = [FakeTool("mcp_test", dangerous=True)]
    deps.extra_tools = extra

    await run_automation(AUTOMATION_BASE, deps)

    assert visto["approved"] == {"acceder_codigo_local", "mcp_test"}
    assert visto["extra_tools"] == extra


async def test_build_builder_registry_incluye_dangerous_excluye_recursion() -> None:
    registry = FakeRegistry(
        [
            FakeTool("acceder_codigo_local", dangerous=True),
            FakeTool("enviar_correo", dangerous=True),
            FakeTool("delegar_mision", dangerous=False),
            FakeTool("crear_recordatorio"),
        ]
    )

    builder = runner_module._build_builder_registry(registry, flags={})

    assert builder.get("acceder_codigo_local") is not None
    assert builder.get("enviar_correo") is not None
    assert builder.get("crear_recordatorio") is not None
    assert builder.get("delegar_mision") is None


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


# ---------------------------------------------------------------------------
# BOTS-02 — la autonomía del worker restringe el registro ANTES de ejecutar
# ---------------------------------------------------------------------------


def test_build_level_registry_read_only_excluye_write_y_send() -> None:
    registry = FakeRegistry(
        [
            FakeTool("crear_recordatorio"),  # no-dangerous → read
            FakeTool("acceder_codigo_local", dangerous=True),  # write
            FakeTool("enviar_correo", dangerous=True),  # send (gated)
            FakeTool("delegar_mision", dangerous=False),  # recursion, siempre excluida
        ]
    )

    restricted = runner_module._build_level_registry(registry, flags={}, autonomy_level="read_only")

    assert restricted.get("crear_recordatorio") is not None
    assert restricted.get("acceder_codigo_local") is None
    assert restricted.get("enviar_correo") is None
    assert restricted.get("delegar_mision") is None


def test_build_level_registry_full_conserva_todo_autorizado() -> None:
    registry = FakeRegistry(
        [
            FakeTool("crear_recordatorio"),
            FakeTool("acceder_codigo_local", dangerous=True),
            FakeTool("enviar_correo", dangerous=True),
        ]
    )

    full = runner_module._build_level_registry(registry, flags={}, autonomy_level="full")

    assert full.get("crear_recordatorio") is not None
    assert full.get("acceder_codigo_local") is not None
    assert full.get("enviar_correo") is not None


def test_build_level_registry_draft_rechaza_send_pero_permite_write() -> None:
    registry = FakeRegistry(
        [
            FakeTool("crear_recordatorio"),
            FakeTool("acceder_codigo_local", dangerous=True),  # write
            FakeTool("enviar_correo", dangerous=True),  # send (gated)
        ]
    )

    draft = runner_module._build_level_registry(registry, flags={}, autonomy_level="draft")

    assert draft.get("crear_recordatorio") is not None
    assert draft.get("acceder_codigo_local") is not None
    assert draft.get("enviar_correo") is None


def test_filter_extra_tools_by_level_read_only_rechaza_mcp_write() -> None:
    leer = FakeTool("mcp_demo_buscar", dangerous=True)
    borrar = FakeTool("mcp_demo_borrar", dangerous=True)

    filtradas = runner_module._filter_extra_tools_by_level(
        [leer, borrar], autonomy_level="read_only"
    )
    assert [t.name for t in filtradas] == ["mcp_demo_buscar"]

    todas = runner_module._filter_extra_tools_by_level([leer, borrar], autonomy_level="full")
    assert [t.name for t in todas] == ["mcp_demo_buscar", "mcp_demo_borrar"]


async def test_run_automation_read_only_rechaza_escritura_del_registro(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BOTS-02: read_only rechaza escritura en headless ANTES de ejecutar, aunque
    el modelo la pida — el registry que recibe el Agent no la contiene."""
    captured: dict[str, Any] = {}

    class ScriptedAgent:
        def __init__(self, llm_router, registry) -> None:
            captured["registry"] = registry

        async def run_turn(self, *, ctx, persona, history, user_text, flags, **kwargs):
            captured["extra_tools"] = kwargs.get("extra_tools")
            yield {"type": "done", "usage": {}}

    monkeypatch.setattr(runner_module, "Agent", ScriptedAgent)
    save_run, _ = _recorder()
    registry = FakeRegistry(
        [
            FakeTool("crear_recordatorio"),
            FakeTool("acceder_codigo_local", dangerous=True),
            FakeTool("enviar_correo", dangerous=True),
        ]
    )
    deps = _make_deps(registry=registry, save_run=save_run)
    deps.builder_mode = True
    deps.autonomy_level = "read_only"
    deps.extra_tools = [
        FakeTool("mcp_demo_buscar", dangerous=True),
        FakeTool("mcp_demo_borrar", dangerous=True),
    ]

    await run_automation(AUTOMATION_BASE, deps)

    safe = captured["registry"]
    assert safe.get("crear_recordatorio") is not None
    assert safe.get("acceder_codigo_local") is None
    assert safe.get("enviar_correo") is None
    # Tools MCP de escritura también se descartan del set de extras.
    assert [t.name for t in captured["extra_tools"]] == ["mcp_demo_buscar"]


async def test_run_automation_full_sigue_funcionando(monkeypatch: pytest.MonkeyPatch) -> None:
    """BOTS-02: full conserva la funcionalidad autorizada completa (builder)."""
    captured: dict[str, Any] = {}

    class ScriptedAgent:
        def __init__(self, llm_router, registry) -> None:
            captured["registry"] = registry

        async def run_turn(self, *, ctx, persona, history, user_text, flags, **kwargs):
            captured["extra_tools"] = kwargs.get("extra_tools")
            yield {"type": "done", "usage": {}}

    monkeypatch.setattr(runner_module, "Agent", ScriptedAgent)
    save_run, _ = _recorder()
    registry = FakeRegistry(
        [
            FakeTool("crear_recordatorio"),
            FakeTool("acceder_codigo_local", dangerous=True),
            FakeTool("enviar_correo", dangerous=True),
        ]
    )
    deps = _make_deps(registry=registry, save_run=save_run)
    deps.builder_mode = True
    deps.autonomy_level = "full"
    deps.extra_tools = [
        FakeTool("mcp_demo_buscar", dangerous=True),
        FakeTool("mcp_demo_borrar", dangerous=True),
    ]

    await run_automation(AUTOMATION_BASE, deps)

    safe = captured["registry"]
    assert safe.get("crear_recordatorio") is not None
    assert safe.get("acceder_codigo_local") is not None
    assert safe.get("enviar_correo") is not None
    assert [t.name for t in captured["extra_tools"]] == ["mcp_demo_buscar", "mcp_demo_borrar"]


async def test_run_automation_read_only_no_recupera_mac_con_companion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BOTS-02: la presencia de companion NO re-introduce `usar_computadora`
    (escritura) en un run read_only — la re-registración también respeta la
    autonomía del worker."""
    captured: dict[str, Any] = {}

    class ScriptedAgent:
        def __init__(self, llm_router, registry) -> None:
            captured["registry"] = registry

        async def run_turn(self, *, ctx, persona, history, user_text, flags, **kwargs):
            yield {"type": "done", "usage": {}}

    monkeypatch.setattr(runner_module, "Agent", ScriptedAgent)
    save_run, _ = _recorder()
    registry = FakeRegistry([FakeTool("usar_computadora", dangerous=True)])
    deps = _make_deps(registry=registry, save_run=save_run)
    deps.builder_mode = True
    deps.autonomy_level = "read_only"
    deps.ctx.extras["companion"] = object()  # companion presente

    await run_automation(AUTOMATION_BASE, deps)

    safe = captured["registry"]
    assert safe.get("usar_computadora") is None


async def test_run_automation_full_recupera_mac_con_companion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BOTS-02: en `full` la capability de Mac SÍ se recupera con companion
    (funcionalidad autorizada completa, sin regresión)."""
    captured: dict[str, Any] = {}

    class ScriptedAgent:
        def __init__(self, llm_router, registry) -> None:
            captured["registry"] = registry

        async def run_turn(self, *, ctx, persona, history, user_text, flags, **kwargs):
            yield {"type": "done", "usage": {}}

    monkeypatch.setattr(runner_module, "Agent", ScriptedAgent)
    save_run, _ = _recorder()
    registry = FakeRegistry([FakeTool("usar_computadora", dangerous=True)])
    deps = _make_deps(registry=registry, save_run=save_run)
    deps.builder_mode = True
    deps.autonomy_level = "full"
    deps.ctx.extras["companion"] = object()

    await run_automation(AUTOMATION_BASE, deps)

    safe = captured["registry"]
    assert safe.get("usar_computadora") is not None
