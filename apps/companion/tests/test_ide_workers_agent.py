"""Contrato funcional del agente de ingeniería de Workers AI."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

import pytest
from edecan_companion import ide_workers_agent as agent_module
from edecan_companion.ide_workers_agent import WorkersIDEAgent, _model_for_turn
from edecan_llm.base import CompletionRequest, StreamChunk, ToolCall
from edecan_llm.workers_ai import (
    MODELO_IDE_POR_DEFECTO,
    MODELO_IDE_VISION_POR_DEFECTO,
)


class _WorkspaceStub:
    def __init__(self, root: Path) -> None:
        self._root = root

    def root(self, workspace_id: str) -> Path:
        assert workspace_id == "workspace"
        return self._root


class _FileStub:
    def __init__(self, readme: str = "# Proyecto\n\nContenido real.") -> None:
        self.readme = readme

    def read(self, workspace_id: str, path: str) -> dict[str, Any]:
        assert workspace_id == "workspace"
        assert path == "README.md"
        return {"path": path, "content": self.readme}


class _ProviderBase:
    name = "fake"

    def __init__(self, model: str) -> None:
        self.model = model
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class _ReadmeProvider(_ProviderBase):
    def __init__(self, model: str) -> None:
        super().__init__(model)
        self.requests: list[CompletionRequest] = []

    async def stream(self, request: CompletionRequest):
        self.requests.append(request)
        if len(self.requests) == 1:
            yield StreamChunk(type="text", text="Voy a leer el ")
            yield StreamChunk(type="text", text="README real.")
            yield StreamChunk(
                type="tool_call",
                tool_call=ToolCall(
                    id="leer-1",
                    name="leer_archivo",
                    arguments={"ruta": "README.md"},
                ),
            )
            return

        tool_message = request.messages[-1]
        assert tool_message.role == "tool"
        assert isinstance(tool_message.content, list)
        assert "Contenido real" in str(tool_message.content[0]["content"])
        yield StreamChunk(type="text", text="Leí `README.md`. ")
        yield StreamChunk(type="text", text="El proyecto contiene documentación real.")


class _EmptyProvider(_ProviderBase):
    def __init__(self, model: str) -> None:
        super().__init__(model)
        self.calls = 0

    async def stream(self, request: CompletionRequest):
        self.calls += 1
        if False:  # pragma: no cover - conserva la forma de generador asíncrono
            yield StreamChunk(type="text", text="")


class _ExceptionProvider(_ProviderBase):
    async def stream(self, request: CompletionRequest):
        if False:  # pragma: no cover - conserva la forma de generador asíncrono
            yield StreamChunk(type="text", text="")
        raise ConnectionError("Workers AI no está disponible")


class _MaxRoundsProvider(_ProviderBase):
    def __init__(self, model: str) -> None:
        super().__init__(model)
        self.calls = 0

    async def stream(self, request: CompletionRequest):
        self.calls += 1
        yield StreamChunk(
            type="tool_call",
            tool_call=ToolCall(
                id=f"leer-{self.calls}",
                name="leer_archivo",
                arguments={"ruta": "README.md"},
            ),
        )


class _CommandProvider(_ProviderBase):
    def __init__(self, model: str, pid_file: Path) -> None:
        super().__init__(model)
        self.calls = 0
        self.pid_file = pid_file

    async def stream(self, request: CompletionRequest):
        self.calls += 1
        if self.calls == 1:
            script = (
                "import os,time,pathlib;"
                f"pathlib.Path({str(self.pid_file)!r}).write_text(str(os.getpid()));"
                "time.sleep(30)"
            )
            yield StreamChunk(
                type="tool_call",
                tool_call=ToolCall(
                    id="command-1",
                    name="ejecutar_comando",
                    arguments={
                        "argv": [sys.executable, "-c", script],
                        "timeout_segundos": 60,
                    },
                ),
            )
            return
        yield StreamChunk(type="text", text="No debería alcanzar una respuesta final.")


def _agent(tmp_path: Path) -> WorkersIDEAgent:
    return WorkersIDEAgent(
        _WorkspaceStub(tmp_path),  # type: ignore[arg-type]
        _FileStub(),  # type: ignore[arg-type]
    )


def test_turno_de_texto_usa_modelo_de_ingenieria(monkeypatch) -> None:
    monkeypatch.delenv("WORKERS_AI_IDE_MODEL", raising=False)
    assert _model_for_turn(requested_model=None, attachments=[]) == MODELO_IDE_POR_DEFECTO


def test_imagen_usa_modelo_multimodal_sin_selector_manual(monkeypatch) -> None:
    monkeypatch.delenv("WORKERS_AI_IDE_VISION_MODEL", raising=False)
    assert (
        _model_for_turn(
            requested_model=None,
            attachments=[
                {
                    "media_type": "image/png",
                    "data": "iVBORw0KGgo=",
                }
            ],
        )
        == MODELO_IDE_VISION_POR_DEFECTO
    )


def test_archivo_no_visual_no_cambia_de_modelo(monkeypatch) -> None:
    monkeypatch.delenv("WORKERS_AI_IDE_MODEL", raising=False)
    assert (
        _model_for_turn(
            requested_model=None,
            attachments=[
                {
                    "media_type": "application/pdf",
                    "data": "JVBERi0=",
                }
            ],
        )
        == MODELO_IDE_POR_DEFECTO
    )


def test_override_explicito_conserva_compatibilidad() -> None:
    assert (
        _model_for_turn(
            requested_model="@cf/example/custom",
            attachments=[{"media_type": "image/png", "data": "x"}],
        )
        == "@cf/example/custom"
    )


# --------------------------------------------------------------------- #
# ``_initial_content`` -- 2.1 del plan de paridad: imágenes de verdad
# validadas (no solo el ``media_type`` declarado) y un aviso claro, nunca un
# turno reventado, cuando el modelo elegido no tiene capacidad de visión.
# --------------------------------------------------------------------- #


def _png_1x1_base64() -> str:
    import base64
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (1, 1), color=(10, 20, 30)).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def test_initial_content_sin_adjuntos_devuelve_el_prompt_tal_cual() -> None:
    content, avisos = agent_module._initial_content(
        "hola", None, MODELO_IDE_VISION_POR_DEFECTO
    )
    assert content == "hola"
    assert avisos == []


def test_initial_content_con_modelo_con_vision_arma_el_bloque_de_imagen() -> None:
    content, avisos = agent_module._initial_content(
        "revisa esta captura",
        [{"name": "captura.png", "media_type": "image/png", "data": _png_1x1_base64()}],
        MODELO_IDE_VISION_POR_DEFECTO,
    )
    assert avisos == []
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "revisa esta captura"}
    assert content[1]["type"] == "image"
    assert content[1]["source"]["media_type"] == "image/png"


def test_initial_content_con_modelo_sin_vision_no_manda_la_imagen_y_avisa() -> None:
    content, avisos = agent_module._initial_content(
        "revisa esta captura",
        [{"name": "captura.png", "media_type": "image/png", "data": _png_1x1_base64()}],
        MODELO_IDE_POR_DEFECTO,
    )
    assert content == "revisa esta captura"
    assert len(avisos) == 1
    assert "no tiene capacidad de visión" in avisos[0]
    assert "captura.png" in avisos[0]


def test_initial_content_rechaza_bytes_que_no_son_una_imagen_real() -> None:
    import base64

    disfrazada = base64.b64encode(b"esto no es una imagen de verdad").decode("ascii")
    content, avisos = agent_module._initial_content(
        "revisa esto",
        [{"name": "falsa.png", "media_type": "image/png", "data": disfrazada}],
        MODELO_IDE_VISION_POR_DEFECTO,
    )
    assert content == "revisa esto"
    assert len(avisos) == 1
    assert "falsa.png" in avisos[0]


@pytest.mark.asyncio
async def test_run_con_imagen_y_modelo_sin_vision_reporta_status_y_sigue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Extremo a extremo (T2.1): un turno con imagen adjunta pero un modelo
    SIN visión explícitamente elegido no debe reventar ni mandar la imagen --
    debe avisar por un evento ``status`` legible y seguir el turno con solo
    texto."""
    provider = _EmptyProvider(MODELO_IDE_POR_DEFECTO)
    monkeypatch.setattr(agent_module, "WorkersAIProvider", lambda model: provider)
    events: list[tuple[str, str]] = []

    # ``_EmptyProvider`` no responde nada -- el turno termina en
    # ``RuntimeError`` de todos modos (ver ``test_empty_terminal_response_is_a_
    # real_error``). Lo que este test fija es que, ANTES de llegar a eso, el
    # aviso de "modelo sin visión" ya salió por un evento real.
    with pytest.raises(RuntimeError):
        await _agent(tmp_path).run(
            workspace_id="workspace",
            prompt="¿Qué ves en esta imagen?",
            write_event=lambda kind, text: events.append((kind, text)),
            cancelled=lambda: False,
            attachments=[
                {"name": "captura.png", "media_type": "image/png", "data": _png_1x1_base64()}
            ],
            model=MODELO_IDE_POR_DEFECTO,
        )

    assert provider.model == MODELO_IDE_POR_DEFECTO
    assert any(
        kind == "status" and "no tiene capacidad de visión" in text for kind, text in events
    )


@pytest.mark.asyncio
async def test_readme_tool_result_returns_one_cohesive_final(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = _ReadmeProvider(MODELO_IDE_POR_DEFECTO)
    monkeypatch.setattr(agent_module, "WorkersAIProvider", lambda model: provider)
    events: list[tuple[str, str]] = []

    await _agent(tmp_path).run(
        workspace_id="workspace",
        prompt="Lee README.md y explícame el proyecto.",
        write_event=lambda kind, text: events.append((kind, text)),
        cancelled=lambda: False,
    )

    assert provider.closed is True
    assert len(provider.requests) == 2
    assert [kind for kind, _ in events].count("assistant_final") == 1
    assert not any(kind == "assistant" for kind, _ in events)
    assert (
        "Leí `README.md`. El proyecto contiene documentación real."
        in dict(events)["assistant_final"]
    )
    assert ("progress", "Voy a leer el README real.") in events
    assert any(kind == "tool" and "leer_archivo" in text for kind, text in events)
    assert events[-1] == ("status", "Trabajo completado.")


@pytest.mark.asyncio
async def test_empty_terminal_response_is_a_real_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = _EmptyProvider(MODELO_IDE_POR_DEFECTO)
    monkeypatch.setattr(agent_module, "WorkersAIProvider", lambda model: provider)
    events: list[tuple[str, str]] = []

    with pytest.raises(
        RuntimeError,
        match="terminó sin entregar una respuesta final",
    ):
        await _agent(tmp_path).run(
            workspace_id="workspace",
            prompt="Responde algo útil.",
            write_event=lambda kind, text: events.append((kind, text)),
            cancelled=lambda: False,
        )

    assert provider.calls == 3
    assert provider.closed is True
    finals = [text for kind, text in events if kind == "assistant_final"]
    assert len(finals) == 1
    assert "sin entregar una respuesta final" in finals[0]
    assert "No marqué la tarea como completada" in finals[0]
    assert ("status", "Trabajo completado.") not in events


@pytest.mark.asyncio
async def test_provider_exception_has_one_honest_final_and_stays_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = _ExceptionProvider(MODELO_IDE_POR_DEFECTO)
    monkeypatch.setattr(agent_module, "WorkersAIProvider", lambda model: provider)
    events: list[tuple[str, str]] = []

    with pytest.raises(ConnectionError, match="Workers AI no está disponible"):
        await _agent(tmp_path).run(
            workspace_id="workspace",
            prompt="Revisa el proyecto.",
            write_event=lambda kind, text: events.append((kind, text)),
            cancelled=lambda: False,
        )

    finals = [text for kind, text in events if kind == "assistant_final"]
    assert provider.closed is True
    assert len(finals) == 1
    assert "servicio de IA interrumpió la ejecución" in finals[0]
    assert "No alcancé a ejecutar ni verificar" in finals[0]
    assert ("status", "Trabajo completado.") not in events


@pytest.mark.asyncio
async def test_max_tool_rounds_has_one_honest_final_and_stays_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = _MaxRoundsProvider(MODELO_IDE_POR_DEFECTO)
    monkeypatch.setattr(agent_module, "WorkersAIProvider", lambda model: provider)
    monkeypatch.setattr(agent_module, "MAX_TOOL_ROUNDS", 3)
    events: list[tuple[str, str]] = []

    with pytest.raises(RuntimeError, match="límite de pasos"):
        await _agent(tmp_path).run(
            workspace_id="workspace",
            prompt="Sigue leyendo para siempre.",
            write_event=lambda kind, text: events.append((kind, text)),
            cancelled=lambda: False,
        )

    finals = [text for kind, text in events if kind == "assistant_final"]
    assert provider.calls == 3
    assert provider.closed is True
    assert len(finals) == 1
    assert "alcancé el límite de pasos" in finals[0]
    assert "leer_archivo (3)" in finals[0]
    assert ("status", "Trabajo completado.") not in events


@pytest.mark.asyncio
async def test_cancelling_command_kills_process_and_never_completes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "command.pid"
    provider = _CommandProvider(MODELO_IDE_POR_DEFECTO, pid_file)
    monkeypatch.setattr(agent_module, "WorkersAIProvider", lambda model: provider)
    cancelled = threading.Event()
    events: list[tuple[str, str]] = []

    task = asyncio.create_task(
        _agent(tmp_path).run(
            workspace_id="workspace",
            prompt="Ejecuta el trabajo largo.",
            write_event=lambda kind, text: events.append((kind, text)),
            cancelled=cancelled.is_set,
        )
    )
    for _ in range(100):
        if pid_file.exists():
            break
        await asyncio.sleep(0.02)
    assert pid_file.exists(), "el comando no llegó a iniciar"
    pid = int(pid_file.read_text(encoding="utf-8"))

    cancelled.set()
    await asyncio.wait_for(task, timeout=5)

    assert provider.closed is True
    assert ("status", "Trabajo cancelado.") in events
    assert not any(kind == "status" and text == "Trabajo completado." for kind, text in events)
    assert not any(kind == "assistant_final" for kind, _ in events)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


# --------------------------------------------------------------------------- #
# Herramienta nueva 1: 'verificar' (envuelve ide_verificacion).
# --------------------------------------------------------------------------- #


class _VerificarProvider(_ProviderBase):
    """Pide 'verificar' con el MISMO comando dos veces seguidas, luego cierra."""

    def __init__(self, model: str, comando: list[str]) -> None:
        super().__init__(model)
        self.comando = comando
        self.calls = 0
        self.resultados: list[dict[str, Any]] = []

    async def stream(self, request: CompletionRequest):
        self.calls += 1
        if self.calls > 1:
            tool_message = request.messages[-1]
            assert tool_message.role == "tool"
            self.resultados.append(json.loads(tool_message.content[0]["content"]))
        if self.calls <= 2:
            yield StreamChunk(
                type="tool_call",
                tool_call=ToolCall(
                    id=f"verificar-{self.calls}",
                    name="verificar",
                    arguments={"comando": self.comando},
                ),
            )
            return
        yield StreamChunk(type="text", text="Dos intentos con el mismo error, me detengo.")


class _VerificarSinDeteccionProvider(_ProviderBase):
    """Pide 'verificar' SIN 'comando' en un proyecto que no se puede autodetectar."""

    def __init__(self, model: str) -> None:
        super().__init__(model)
        self.calls = 0
        self.resultado: dict[str, Any] | None = None

    async def stream(self, request: CompletionRequest):
        self.calls += 1
        if self.calls == 1:
            yield StreamChunk(
                type="tool_call",
                tool_call=ToolCall(id="verificar-1", name="verificar", arguments={}),
            )
            return
        tool_message = request.messages[-1]
        self.resultado = json.loads(tool_message.content[0]["content"])
        yield StreamChunk(type="text", text="No pude verificar este proyecto.")


@pytest.mark.asyncio
async def test_verificar_ejecuta_una_vez_por_llamada_y_marca_error_repetido(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    script = tmp_path / "fail.py"
    script.write_text(
        "import sys\nsys.stderr.write('boom explicito')\nsys.exit(1)\n", encoding="utf-8"
    )
    comando = [sys.executable, str(script)]
    provider = _VerificarProvider(MODELO_IDE_POR_DEFECTO, comando)
    monkeypatch.setattr(agent_module, "WorkersAIProvider", lambda model: provider)
    events: list[tuple[str, str]] = []

    await _agent(tmp_path).run(
        workspace_id="workspace",
        prompt="Corre la verificación y arregla lo que falle.",
        write_event=lambda kind, text: events.append((kind, text)),
        cancelled=lambda: False,
    )

    assert len(provider.resultados) == 2
    primero, segundo = provider.resultados
    assert primero["aprobado"] is False
    assert primero["exit_code"] == 1
    assert primero["mismo_error_que_el_intento_anterior"] is False
    assert segundo["mismo_error_que_el_intento_anterior"] is True
    assert any(kind == "assistant_final" for kind, _ in events)


@pytest.mark.asyncio
async def test_verificar_sin_comando_y_sin_autodeteccion_es_un_error_claro(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = _VerificarSinDeteccionProvider(MODELO_IDE_POR_DEFECTO)
    monkeypatch.setattr(agent_module, "WorkersAIProvider", lambda model: provider)
    events: list[tuple[str, str]] = []

    await _agent(tmp_path).run(
        workspace_id="workspace",
        prompt="Verifica el proyecto.",
        write_event=lambda kind, text: events.append((kind, text)),
        cancelled=lambda: False,
    )

    assert provider.resultado is not None
    assert provider.resultado["ok"] is False
    assert "No reconocí un comando de verificación" in provider.resultado["error"]


def test_detectar_interprete_python_prefiere_el_venv_del_proyecto(tmp_path: Path) -> None:
    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("#!/bin/sh\n", encoding="utf-8")
    assert agent_module._detectar_interprete_python(tmp_path) == str(venv_python)


def test_detectar_interprete_python_cae_al_path_sin_venv(tmp_path: Path) -> None:
    assert agent_module._detectar_interprete_python(tmp_path)


def test_tools_incluyen_las_tres_herramientas_nuevas() -> None:
    names = {tool.name for tool in agent_module.TOOLS}
    assert {"verificar", "auditar_seguridad_proyecto", "ejecutar_pentestgpt_autorizado"} <= names
    pentest = next(t for t in agent_module.TOOLS if t.name == "ejecutar_pentestgpt_autorizado")
    assert set(pentest.input_schema["required"]) == {
        "objetivo",
        "alcance_autorizado",
        "confirmo_que_tengo_autorizacion",
    }


# --------------------------------------------------------------------------- #
# Herramienta nueva 2: 'auditar_seguridad_proyecto' (estática, sin gate).
# --------------------------------------------------------------------------- #


class _AuditoriaProvider(_ProviderBase):
    def __init__(self, model: str) -> None:
        super().__init__(model)
        self.calls = 0
        self.resultado: dict[str, Any] | None = None

    async def stream(self, request: CompletionRequest):
        self.calls += 1
        if self.calls == 1:
            yield StreamChunk(
                type="tool_call",
                tool_call=ToolCall(
                    id="auditoria-1",
                    name="auditar_seguridad_proyecto",
                    arguments={"ruta": "."},
                ),
            )
            return
        tool_message = request.messages[-1]
        self.resultado = json.loads(tool_message.content[0]["content"])
        yield StreamChunk(type="text", text="Auditoría lista.")


@pytest.mark.asyncio
async def test_auditar_seguridad_proyecto_detecta_secreto_sin_revelarlo(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    secreto = "sk-super-secreta-1234567890123456"
    (tmp_path / "config.py").write_text(f'API_KEY = "{secreto}"\n', encoding="utf-8")
    provider = _AuditoriaProvider(MODELO_IDE_POR_DEFECTO)
    monkeypatch.setattr(agent_module, "WorkersAIProvider", lambda model: provider)
    events: list[tuple[str, str]] = []

    await _agent(tmp_path).run(
        workspace_id="workspace",
        prompt="Audita el proyecto antes de cerrar la tarea.",
        write_event=lambda kind, text: events.append((kind, text)),
        cancelled=lambda: False,
    )

    assert provider.resultado is not None
    data = provider.resultado["data"]
    assert data["summary"]["findings"] >= 1
    assert secreto not in provider.resultado["content"]
    assert secreto not in json.dumps(data)


# --------------------------------------------------------------------------- #
# Herramienta nueva 3: 'ejecutar_pentestgpt_autorizado' (dangerous + gate).
# --------------------------------------------------------------------------- #


class _PentestProvider(_ProviderBase):
    def __init__(self, model: str, tool_call_id: str = "pentest-1") -> None:
        super().__init__(model)
        self.calls = 0
        self.tool_call_id = tool_call_id
        self.resultado: dict[str, Any] | None = None

    async def stream(self, request: CompletionRequest):
        self.calls += 1
        if self.calls == 1:
            yield StreamChunk(
                type="tool_call",
                tool_call=ToolCall(
                    id=self.tool_call_id,
                    name="ejecutar_pentestgpt_autorizado",
                    arguments={
                        "objetivo": "https://midominio-de-prueba.example",
                        "alcance_autorizado": "https://midominio-de-prueba.example",
                        "confirmo_que_tengo_autorizacion": True,
                    },
                ),
            )
            return
        tool_message = request.messages[-1]
        self.resultado = json.loads(tool_message.content[0]["content"])
        yield StreamChunk(type="text", text="Pentest completado.")


@pytest.mark.asyncio
async def test_pentest_sin_aprobacion_humana_pausa_el_turno_y_no_ejecuta_nada(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = _PentestProvider(MODELO_IDE_POR_DEFECTO)
    monkeypatch.setattr(agent_module, "WorkersAIProvider", lambda model: provider)

    class _NuncaDebeLlamarse:
        async def run(self, ctx: Any, args: dict[str, Any]) -> Any:  # pragma: no cover
            raise AssertionError("no debía ejecutarse sin confirmación humana real")

    monkeypatch.setattr(
        "edecan_toolkit.seguridad.EjecutarPentestGPTAutorizadoTool",
        lambda: _NuncaDebeLlamarse(),
    )
    events: list[tuple[str, str]] = []

    await _agent(tmp_path).run(
        workspace_id="workspace",
        prompt="Corre un pentest contra mi propio dominio de pruebas.",
        write_event=lambda kind, text: events.append((kind, text)),
        cancelled=lambda: False,
    )

    assert provider.calls == 1
    kinds = [kind for kind, _ in events]
    assert "confirmation_required" in kinds
    assert not any(kind == "assistant_final" for kind, _ in events)
    assert ("status", "Trabajo completado.") not in events
    payload = json.loads(dict(events)["confirmation_required"])
    assert payload == {
        "tool_call_id": "pentest-1",
        "name": "ejecutar_pentestgpt_autorizado",
        "args": {
            "objetivo": "https://midominio-de-prueba.example",
            "alcance_autorizado": "https://midominio-de-prueba.example",
            "confirmo_que_tengo_autorizacion": True,
        },
    }


@pytest.mark.asyncio
async def test_pentest_con_aprobacion_humana_ejecuta_y_no_relaja_controles_propios(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = _PentestProvider(MODELO_IDE_POR_DEFECTO)
    monkeypatch.setattr(agent_module, "WorkersAIProvider", lambda model: provider)
    captured: dict[str, Any] = {}

    class _ToolFalsa:
        async def run(self, ctx: Any, args: dict[str, Any]) -> Any:
            from edecan_core.tools.base import ToolResult

            captured["root"] = ctx.settings.EDECAN_LOCAL_REPO_PATH
            captured["args"] = args
            return ToolResult(content="Pentest ejecutado.", data={"return_code": 0})

    monkeypatch.setattr(
        "edecan_toolkit.seguridad.EjecutarPentestGPTAutorizadoTool",
        lambda: _ToolFalsa(),
    )
    events: list[tuple[str, str]] = []

    await _agent(tmp_path).run(
        workspace_id="workspace",
        prompt="Corre un pentest contra mi propio dominio de pruebas.",
        write_event=lambda kind, text: events.append((kind, text)),
        cancelled=lambda: False,
        approved_tool_call_ids=frozenset({"pentest-1"}),
    )

    assert provider.calls == 2
    assert captured["root"] == str(tmp_path)
    assert captured["args"]["confirmo_que_tengo_autorizacion"] is True
    assert provider.resultado == {"content": "Pentest ejecutado.", "data": {"return_code": 0}}
    assert not any(kind == "confirmation_required" for kind, _ in events)
    assert ("status", "Trabajo completado.") in events


# --------------------------------------------------------------------------- #
# Herramienta nueva 4: 'proponer_plan' (cabo 1 -- gate de plan previo).
# --------------------------------------------------------------------------- #


class _PlanTrivialProvider(_ProviderBase):
    """Propone un plan trivial (2 pasos) Y pide 'leer_archivo' en el MISMO
    lote -- fija que una tarea que no amerita plan no bloquea nada: el resto
    del lote se ejecuta en la misma ronda."""

    def __init__(self, model: str) -> None:
        super().__init__(model)
        self.calls = 0

    async def stream(self, request: CompletionRequest):
        self.calls += 1
        if self.calls == 1:
            yield StreamChunk(
                type="tool_call",
                tool_call=ToolCall(
                    id="plan-1",
                    name="proponer_plan",
                    arguments={
                        "meta": "Agregar un botón",
                        "pasos": [{"descripcion": "paso 1"}, {"descripcion": "paso 2"}],
                    },
                ),
            )
            yield StreamChunk(
                type="tool_call",
                tool_call=ToolCall(
                    id="leer-1", name="leer_archivo", arguments={"ruta": "README.md"}
                ),
            )
            return
        yield StreamChunk(type="text", text="Listo, sin plan hizo falta.")


@pytest.mark.asyncio
async def test_proponer_plan_trivial_no_pausa_y_el_resto_del_lote_se_ejecuta(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from edecan_companion.ide_plan import PlanStore

    provider = _PlanTrivialProvider(MODELO_IDE_POR_DEFECTO)
    monkeypatch.setattr(agent_module, "WorkersAIProvider", lambda model: provider)
    plan_store = PlanStore()
    events: list[tuple[str, str]] = []

    await _agent(tmp_path).run(
        workspace_id="workspace",
        prompt="Agrega un botón nuevo.",
        write_event=lambda kind, text: events.append((kind, text)),
        cancelled=lambda: False,
        plan_store=plan_store,
        session_id="sesion-1",
    )

    assert provider.calls == 2
    # Nunca se propuso un plan de verdad -- 2 pasos sin riesgo no llega al
    # umbral de ``requires_plan``.
    assert plan_store.get_active_for_session("sesion-1") is None
    assert not any(kind == "plan_proposed" for kind, _ in events)
    # El resto del lote (leer_archivo) SÍ corrió en la misma ronda.
    assert any(kind == "tool" and "leer_archivo" in text for kind, text in events)
    assert any(kind == "assistant_final" for kind, _ in events)


class _PlanRequierePlanProvider(_ProviderBase):
    """Propone un plan que SÍ amerita aprobación (palabra de alto riesgo) Y
    pide 'escribir_archivo' en el MISMO lote -- fija que ni esa ni ninguna
    otra llamada del lote se ejecuta antes de que la persona apruebe."""

    def __init__(self, model: str) -> None:
        super().__init__(model)
        self.calls = 0

    async def stream(self, request: CompletionRequest):
        self.calls += 1
        if self.calls > 1:
            # Solo se llega aquí cuando el gate NO pausó el turno (sin
            # ``plan_store``, o con un plan ya activo): el lote anterior sí
            # se ejecutó, y el turno cierra normal.
            yield StreamChunk(type="text", text="Listo.")
            return
        yield StreamChunk(
            type="tool_call",
            tool_call=ToolCall(
                id="plan-1",
                name="proponer_plan",
                arguments={
                    "meta": "Refactoriza la autenticación del API",
                    "pasos": [
                        {"descripcion": "tocar el middleware", "rutas": ["apps/api/auth.py"]},
                        {"descripcion": "actualizar los tests"},
                    ],
                },
            ),
        )
        yield StreamChunk(
            type="tool_call",
            tool_call=ToolCall(
                id="escribir-1",
                name="escribir_archivo",
                arguments={"ruta": "apps/api/auth.py", "contenido": "x"},
            ),
        )


@pytest.mark.asyncio
async def test_proponer_plan_no_trivial_pausa_el_turno_y_no_toca_archivos(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from edecan_companion.ide_plan import PlanStore

    provider = _PlanRequierePlanProvider(MODELO_IDE_POR_DEFECTO)
    monkeypatch.setattr(agent_module, "WorkersAIProvider", lambda model: provider)
    plan_store = PlanStore()
    events: list[tuple[str, str]] = []

    await _agent(tmp_path).run(
        workspace_id="workspace",
        prompt="Refactoriza la autenticación del API.",
        write_event=lambda kind, text: events.append((kind, text)),
        cancelled=lambda: False,
        plan_store=plan_store,
        session_id="sesion-2",
    )

    assert provider.calls == 1
    assert not (tmp_path / "apps").exists()
    assert not any(kind == "file" for kind, _ in events)
    assert not any(kind == "assistant_final" for kind, _ in events)
    assert ("status", "Trabajo completado.") not in events

    kinds = [kind for kind, _ in events]
    assert "plan_proposed" in kinds
    payload = json.loads(dict(events)["plan_proposed"])
    assert payload["plan"]["status"] == "proposed"
    assert [s["description"] for s in payload["plan"]["steps"]] == [
        "tocar el middleware",
        "actualizar los tests",
    ]
    assert payload["rutas_por_paso"] == [["apps/api/auth.py"], None]

    plan = plan_store.get_active_for_session("sesion-2")
    assert plan is not None
    assert plan.goal == "Refactoriza la autenticación del API"


@pytest.mark.asyncio
async def test_proponer_plan_sin_store_conectado_no_bloquea_el_turno(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Sin ``plan_store``/``session_id`` (p. ej. un llamador que todavía no
    los cablea), el gate se degrada con seguridad: informa y sigue, en vez de
    dejar el turno colgado para siempre esperando una aprobación imposible."""
    provider = _PlanRequierePlanProvider(MODELO_IDE_POR_DEFECTO)
    monkeypatch.setattr(agent_module, "WorkersAIProvider", lambda model: provider)
    events: list[tuple[str, str]] = []

    await _agent(tmp_path).run(
        workspace_id="workspace",
        prompt="Refactoriza la autenticación del API.",
        write_event=lambda kind, text: events.append((kind, text)),
        cancelled=lambda: False,
    )

    assert not any(kind == "plan_proposed" for kind, _ in events)
    # El lote sigue: 'escribir_archivo' SÍ se intentó ejecutar esta vez (el
    # ``_FileStub`` de prueba no implementa escritura real; lo que importa
    # acá es que el gate no lo bloqueó).
    assert any(kind == "tool" and "escribir_archivo" in text for kind, text in events)


@pytest.mark.asyncio
async def test_proponer_plan_con_plan_activo_no_pausa_de_nuevo(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Si ya hay un plan vivo para esta sesión (p. ej. el modelo insiste en
    proponer otro), ``PlanStore.propose`` lo rechaza -- el gate no pausa un
    segundo turno sobre una aprobación que ya está en curso, solo informa el
    error y sigue el lote."""
    from edecan_companion.ide_plan import PlanStore

    provider = _PlanRequierePlanProvider(MODELO_IDE_POR_DEFECTO)
    monkeypatch.setattr(agent_module, "WorkersAIProvider", lambda model: provider)
    plan_store = PlanStore()
    plan_store.propose("sesion-3", "un plan ya vivo", ["paso 1"])
    events: list[tuple[str, str]] = []

    await _agent(tmp_path).run(
        workspace_id="workspace",
        prompt="Refactoriza la autenticación del API.",
        write_event=lambda kind, text: events.append((kind, text)),
        cancelled=lambda: False,
        plan_store=plan_store,
        session_id="sesion-3",
    )

    assert not any(kind == "plan_proposed" for kind, _ in events)
    assert any(kind == "tool" and "escribir_archivo" in text for kind, text in events)


def test_proponer_plan_esta_en_las_tools() -> None:
    names = {tool.name for tool in agent_module.TOOLS}
    assert "proponer_plan" in names
    plan_tool = next(t for t in agent_module.TOOLS if t.name == "proponer_plan")
    assert set(plan_tool.input_schema["required"]) == {"meta", "pasos"}


# --------------------------------------------------------------------------- #
# 'recordar_nota_proyecto': el porqué de una decisión llega hasta el store.
#
# Sin esta ruta, los tres campos de ADR de `ide_memoria` son código
# inalcanzable: el store sabe guardarlos, pero nada en el producto puede
# pedírselo.
# --------------------------------------------------------------------------- #


def _memoria_real(tmp_path: Path):
    from edecan_companion.ide_memoria import MemoriaStore
    from edecan_companion.ide_workspaces import WorkspaceStore

    proyecto = tmp_path / "proyecto"
    proyecto.mkdir(exist_ok=True)
    workspaces = WorkspaceStore(tmp_path / "state")
    workspace_id = workspaces.authorize(str(proyecto))["id"]
    return MemoriaStore(tmp_path / "state", workspaces), workspace_id


async def _recordar(tmp_path: Path, memoria, workspace_id: str, args: dict[str, Any]):
    return await _agent(tmp_path)._execute(
        workspace_id,
        "recordar_nota_proyecto",
        args,
        lambda kind, text: None,
        cancelled=lambda: False,
        memoria=memoria,
    )


def test_recordar_nota_proyecto_acepta_el_porque_de_una_decision() -> None:
    """El esquema es lo único que el modelo ve: un campo que no está ahí no
    existe, por buena que sea la razón para llenarlo."""

    tool = next(t for t in agent_module.TOOLS if t.name == "recordar_nota_proyecto")
    propiedades = tool.input_schema["properties"]

    assert {"alternativas", "por_que_no", "se_invalida_si"} <= set(propiedades)
    # Y siguen siendo opcionales: una convención no descarta nada.
    assert set(tool.input_schema["required"]) == {"contenido", "tipo"}
    assert propiedades["alternativas"]["type"] == "array"


@pytest.mark.asyncio
async def test_recordar_una_decision_guarda_sus_alternativas_y_se_encuentra_por_ellas(
    tmp_path: Path,
) -> None:
    memoria, workspace_id = _memoria_real(tmp_path)

    fila = await _recordar(
        tmp_path,
        memoria,
        workspace_id,
        {
            "contenido": "La memoria del proyecto es 100% local: no habla con base de datos.",
            "tipo": "decision",
            "alternativas": ["Postgres con pgvector", "SQLite"],
            "por_que_no": "El companion se instala solo; exigir un motor aparte rompe eso.",
            "se_invalida_si": "El companion pase a depender de un servidor propio de todas formas.",
        },
    )

    assert fila["alternativas"] == ["Postgres con pgvector", "SQLite"]
    assert fila["se_invalida_si"].startswith("El companion pase a depender")
    # Lo que justifica la ruta entera: la sesión que está por reproponer una
    # alternativa descartada encuentra la decisión aunque no nombre su
    # conclusión.
    assert memoria.recall(workspace_id, "¿y si indexamos con pgvector?")


@pytest.mark.asyncio
async def test_el_porque_en_un_tipo_que_no_es_decision_vuelve_como_error_legible(
    tmp_path: Path,
) -> None:
    """El store rechaza la incoherencia; la tool tiene que devolvérsela al
    modelo como texto accionable, no reventar el turno."""

    memoria, workspace_id = _memoria_real(tmp_path)

    resultado = await _recordar(
        tmp_path,
        memoria,
        workspace_id,
        {
            "contenido": "Español LATAM con tú, nunca voseo, tampoco en el contenido generado.",
            "tipo": "convencion",
            "alternativas": ["Español neutro"],
        },
    )

    assert resultado["ok"] is False
    assert "decision" in resultado["error"]
    assert memoria.list_notes(workspace_id) == []


@pytest.mark.asyncio
async def test_recordar_sin_porque_sigue_funcionando_igual_que_antes(tmp_path: Path) -> None:
    """Los campos nuevos son opcionales de punta a punta: quien no los manda
    guarda exactamente la misma fila de siempre."""

    memoria, workspace_id = _memoria_real(tmp_path)

    fila = await _recordar(
        tmp_path,
        memoria,
        workspace_id,
        {
            "contenido": "El intérprete de tests vive en .venv/bin/python, no en el del PATH.",
            "tipo": "error_evitar",
        },
    )

    assert fila["kind"] == "error_evitar"
    assert "alternativas" not in fila
