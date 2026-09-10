"""BOTS-14: los workers headless cargan los plugins DEL tenant (no el root).

`run_mission.py`/`run_automation.py` usaban `ToolRegistry.load_plugin_dir(root)`
sobre el nivel raíz, que solo ve `*.py` de ESE nivel — no los subdirectorios por
tenant que escribe `crear_herramienta` (`persona_tools.py`). Este módulo fija que
`_build_registry(tenant_id)` (factoría común `registry_para_tenant`) carga solo
los plugins del tenant y que `run_persistent_agent` le pasa el `tenant_id`.
"""

from __future__ import annotations

import sys
import types
import uuid
from pathlib import Path
from typing import Any

import edecan_worker.handlers.run_automation as run_automation_module
import edecan_worker.handlers.run_mission as run_mission_module
import pytest
from edecan_schemas import JobEnvelope
from fakes import make_deps

_PLUGIN_TEMPLATE = """
from edecan_core.tools import Tool, ToolResult


class {clase}(Tool):
    name = "{tool_name}"
    description = "plugin del tenant"
    input_schema = {{"type": "object", "properties": {{}}}}

    async def run(self, ctx, args):
        return ToolResult(content="ok")


def get_all_tools():
    return [{clase}()]
"""


def _escribir_plugin(directorio: Path, nombre_archivo: str, tool_name: str) -> None:
    directorio.mkdir(parents=True, exist_ok=True)
    clase = "".join(part.capitalize() for part in tool_name.split("_")) + "Tool"
    (directorio / nombre_archivo).write_text(
        _PLUGIN_TEMPLATE.format(clase=clase, tool_name=tool_name),
        encoding="utf-8",
    )


@pytest.fixture()
def plugins_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "plugins"
    monkeypatch.setenv("EDECAN_PLUGINS_DIR", str(root))
    # Aisla de los entry points reales instalados en el venv (determinista).
    monkeypatch.setattr(
        "edecan_core.tools.registry.entry_points", lambda *args, **kwargs: []
    )
    return root


def test_run_mission_build_registry_carga_solo_plugins_del_tenant(plugins_root: Path) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    _escribir_plugin(plugins_root / str(tenant_a), "a_mission.py", "a_mission")
    _escribir_plugin(plugins_root / str(tenant_b), "b_mission.py", "b_mission")

    registry = run_mission_module._build_registry(tenant_a)
    assert registry.get("a_mission") is not None
    assert registry.get("b_mission") is None


def test_run_automation_build_registry_carga_solo_plugins_del_tenant(
    plugins_root: Path,
) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    _escribir_plugin(plugins_root / str(tenant_a), "a_auto.py", "a_auto")
    _escribir_plugin(plugins_root / str(tenant_b), "b_auto.py", "b_auto")

    registry = run_automation_module._build_registry(tenant_a)
    assert registry.get("a_auto") is not None
    assert registry.get("b_auto") is None


def test_build_registry_sin_tenant_conserva_el_nivel_raiz(plugins_root: Path) -> None:
    """La rama legacy (`tenant_id=None`, usada por `run_companion_turn`) no
    regresa: sigue cargando solo `*.py` del nivel raíz, sin el subdirectorio."""
    _escribir_plugin(plugins_root, "root_only.py", "root_only")

    registry = run_automation_module._build_registry()
    assert registry.get("root_only") is not None


class _Sentinel(Exception):
    pass


def _instalar_imports_perezosos_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pre-registra módulos falsos para los imports perezosos de `handle()`.

    `run_persistent_agent.handle` hace `from X import Y` DENTRO de la función
    para paquetes hermanos que pueden no existir en un workspace parcial
    (`edecan_api`, `edecan_automations`). Acá se inyectan fakes en `sys.modules`
    para no depender de que estén instalados en el venv de tests del worker.
    """

    def _modulo(name: str, **attrs: Any) -> types.ModuleType:
        mod = types.ModuleType(name)
        for clave, valor in attrs.items():
            setattr(mod, clave, valor)
        return mod

    api = _modulo("edecan_api")
    api_persona = _modulo(
        "edecan_api.persona_tools", conversation_persona_tools=lambda: []
    )
    api.persona_tools = api_persona

    automations = _modulo("edecan_automations")
    runner = _modulo(
        "edecan_automations.runner",
        RunnerDeps=object,
        run_automation=lambda *a, **k: None,
    )
    automations.runner = runner

    monkeypatch.setitem(sys.modules, "edecan_api", api)
    monkeypatch.setitem(sys.modules, "edecan_api.persona_tools", api_persona)
    monkeypatch.setitem(sys.modules, "edecan_automations", automations)
    monkeypatch.setitem(sys.modules, "edecan_automations.runner", runner)

    # `edecan_core.bot_*`/`companion_access` SÍ son importables, pero sus
    # funciones solo se invocan DESPUÉS de la línea de carga de plugins; se
    # faken igual para no depender de su comportamiento real acá.
    monkeypatch.setitem(
        sys.modules,
        "edecan_core.bot_harness",
        _modulo(
            "edecan_core.bot_harness",
            autonomy_allowed_operations=lambda *a, **k: frozenset(),
            build_skills_context=lambda *a, **k: None,
            worker_chat_extras=lambda *a, **k: {},
            mcp_preapproved_tokens=lambda *a, **k: frozenset(),
            parse_mcp_grants=lambda *a, **k: [],
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "edecan_core.bot_persona",
        _modulo("edecan_core.bot_persona", persona_from_worker=lambda worker: object()),
    )
    monkeypatch.setitem(
        sys.modules,
        "edecan_core.bot_registry",
        _modulo(
            "edecan_core.bot_registry",
            bot_chat_preapproved_tool_calls=lambda *a, **k: set(),
            build_worker_registry=lambda registry, worker, local_mode: registry,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "edecan_core.companion_access",
        _modulo("edecan_core.companion_access", companion_para=lambda tenant_id: None),
    )


async def test_run_persistent_agent_pasa_tenant_id_a_build_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El executor persistente usa la MISMA `_build_registry(tenant_id)`.

    La línea de carga de plugins en `run_persistent_agent.handle` importa
    `_build_registry` de `run_automation` y debe llamarlo CON el `tenant_id`
    (BOTS-14) — no sin argumentos (que cargaría solo el root). Se verifica
    ejecutando `handle` hasta la línea de carga y capturando el argumento.
    """
    import edecan_worker.handlers.run_persistent_agent as pa_module
    from edecan_worker.handlers.run_persistent_agent import _BotRunClaim

    _instalar_imports_perezosos_fake(monkeypatch)

    capturados: list[Any] = []

    def _spy(tenant_id: Any) -> Any:
        capturados.append(tenant_id)
        raise _Sentinel()

    monkeypatch.setattr(run_automation_module, "_build_registry", _spy)

    async def _load_worker(session: Any, tid: Any, wid: Any) -> dict[str, Any]:
        return {
            "enabled": True,
            "status": "idle",
            "user_id": str(uuid.uuid4()),
            "budget": None,
            "name": "bot",
        }

    async def _claim_bot_run(deps: Any, **kwargs: Any) -> _BotRunClaim:
        return _BotRunClaim(
            claimed=True, terminal=False, generation=0, durability_enabled=False
        )

    async def _claim_worker_and_handoff(session: Any, **kwargs: Any) -> bool:
        return True

    monkeypatch.setattr(pa_module, "_load_worker", _load_worker)
    monkeypatch.setattr(pa_module, "_claim_bot_run", _claim_bot_run)
    monkeypatch.setattr(pa_module, "_claim_worker_and_handoff", _claim_worker_and_handoff)

    class _Result:
        def __init__(self, row: dict[str, Any] | None) -> None:
            self._row = row

        def mappings(self) -> _Result:
            return self

        def first(self) -> dict[str, Any] | None:
            return self._row

    class _Session:
        async def execute(self, stmt: Any, params: Any) -> _Result:
            if "plan_key FROM tenants" in str(stmt):
                return _Result({"plan_key": "hosted_pro"})
            return _Result(None)

        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *args: Any) -> bool:
            return False

    deps = make_deps(session_factory=lambda _tenant: _Session())

    tenant_id = uuid.uuid4()
    worker_id = uuid.uuid4()
    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="run_persistent_agent",
        payload={
            "worker_id": str(worker_id),
            "instruction": "hola",
            "task_id": "tarea-1",
            # `source` que no sea agent_message evita el bloque de narración.
            "source": "delegacion_resultado",
        },
    )

    with pytest.raises(_Sentinel):
        await pa_module.handle(env, deps)

    assert capturados == [tenant_id]