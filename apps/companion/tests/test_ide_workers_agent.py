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
from edecan_companion.ide_modos import EsfuerzoStore, max_tokens_para_esfuerzo
from edecan_companion.ide_workers_agent import (
    WorkersIDEAgent,
    _model_for_turn,
    build_failure_final,
)
from edecan_llm.base import CompletionRequest, StreamChunk, ToolCall
from edecan_llm.workers_ai import (
    MODELO_IDE_POR_DEFECTO,
    MODELO_IDE_VISION_POR_DEFECTO,
    CredencialInvalidaError,
    PeticionInvalidaError,
    ProveedorInalcanzableError,
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


class _CambioDeEsfuerzoProvider(_ProviderBase):
    """Doble de proveedor que registra cada ``CompletionRequest`` y, justo
    después de recibir la primera, cambia el nivel vigente en el
    ``EsfuerzoStore`` -- simula a la persona escribiendo ``/effort alto`` (o
    tocando el selector de la UI) MIENTRAS el turno sigue corriendo. La
    prueba real es que la SEGUNDA vuelta ya salga con el nivel nuevo."""

    def __init__(
        self,
        model: str,
        *,
        effort_store: EsfuerzoStore,
        session_id: str,
        nivel_nuevo: str,
    ) -> None:
        super().__init__(model)
        self.requests: list[CompletionRequest] = []
        self._effort_store = effort_store
        self._session_id = session_id
        self._nivel_nuevo = nivel_nuevo

    async def stream(self, request: CompletionRequest):
        self.requests.append(request)
        if len(self.requests) == 1:
            # El cambio ocurre ENTRE la vuelta 1 y la vuelta 2 -- exactamente
            # el escenario "el dueño está en Ultra, lo cambia a mitad del
            # turno". Requiere una herramienta para forzar una vuelta 2.
            self._effort_store.fijar(self._session_id, self._nivel_nuevo)
            yield StreamChunk(
                type="tool_call",
                tool_call=ToolCall(
                    id="leer-1",
                    name="leer_archivo",
                    arguments={"ruta": "README.md"},
                ),
            )
            return
        yield StreamChunk(type="text", text="Listo con el nivel nuevo.")


class _EmptyProvider(_ProviderBase):
    def __init__(self, model: str) -> None:
        super().__init__(model)
        self.calls = 0

    async def stream(self, request: CompletionRequest):
        self.calls += 1
        if False:  # pragma: no cover - conserva la forma de generador asíncrono
            yield StreamChunk(type="text", text="")


class _ExceptionProvider(_ProviderBase):
    """Simula al proveedor REALMENTE caído: ``ProveedorInalcanzableError`` es
    justo el tipo que ``WorkersAIProvider`` levanta cuando no logra abrir la
    conexión (ver ``packages/llm/edecan_llm/workers_ai.py``), a diferencia de
    un ``ConnectionError`` crudo -- que en Python es un ``OSError`` y por eso
    ya no cae en la rama "el servicio de IA interrumpió" tras el arreglo de
    ``build_failure_final`` (ver ``test_credencial_faltante_no_se_le_cuelga_
    al_proveedor`` para el caso local que este archivo distingue)."""

    async def stream(self, request: CompletionRequest):
        if False:  # pragma: no cover - conserva la forma de generador asíncrono
            yield StreamChunk(type="text", text="")
        raise ProveedorInalcanzableError("Workers AI no está disponible", provider="workers_ai")


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
async def test_cambiar_effort_a_mitad_de_turno_cambia_la_vuelta_que_sigue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """El encargo literal: cambiar ``/effort`` NO espera al turno siguiente.
    ``EsfuerzoStore`` se lee EN CADA VUELTA (no una sola vez al entrar a
    ``run``), así que la vuelta 2 de ESTE MISMO turno ya sale con
    ``reasoning_effort``/``max_tokens`` del nivel nuevo."""
    session_id = "sesion-1"
    effort_store = EsfuerzoStore()
    effort_store.fijar(session_id, "bajo")

    provider = _CambioDeEsfuerzoProvider(
        MODELO_IDE_POR_DEFECTO,
        effort_store=effort_store,
        session_id=session_id,
        nivel_nuevo="alto",
    )
    monkeypatch.setattr(agent_module, "WorkersAIProvider", lambda model: provider)
    events: list[tuple[str, str]] = []

    await _agent(tmp_path).run(
        workspace_id="workspace",
        prompt="Lee README.md y explícame el proyecto.",
        write_event=lambda kind, text: events.append((kind, text)),
        cancelled=lambda: False,
        session_id=session_id,
        effort_store=effort_store,
    )

    assert len(provider.requests) == 2
    vuelta_1, vuelta_2 = provider.requests

    # Vuelta 1: nivel "bajo" fijado ANTES de arrancar -- se lee de entrada.
    assert vuelta_1.reasoning_effort == "low"
    assert vuelta_1.max_tokens == max_tokens_para_esfuerzo(8192, "bajo")

    # Vuelta 2: el cambio a "alto" ocurrió DENTRO del turno (en la propia
    # ``stream`` de la vuelta 1) -- si el código solo leyera el nivel una vez
    # al entrar a ``run``, esta aserción fallaría con "low" otra vez.
    assert vuelta_2.reasoning_effort == "high"
    assert vuelta_2.max_tokens == max_tokens_para_esfuerzo(8192, "alto")
    assert vuelta_2.max_tokens > vuelta_1.max_tokens

    # Punto 4 del encargo: el cambio se anuncia por ``write_event``, y solo
    # una vez (no un log por vuelta).
    avisos = [text for kind, text in events if kind == "status" and "esfuerzo" in text.lower()]
    assert len(avisos) == 1
    assert "alto" in avisos[0]


@pytest.mark.asyncio
async def test_sin_effort_store_o_sin_session_id_se_comporta_como_antes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Regla de compatibilidad de la firma: una corrida que no provee
    ``effort_store``/``session_id`` (tests viejos, un sub-agente que a
    propósito no debe heredar nada) tiene que salir EXACTAMENTE con el
    default fijo de siempre -- "high"/8192 -- no con el default del store
    ("medio"), que sería una regresión silenciosa."""
    provider = _ReadmeProvider(MODELO_IDE_POR_DEFECTO)
    monkeypatch.setattr(agent_module, "WorkersAIProvider", lambda model: provider)

    await _agent(tmp_path).run(
        workspace_id="workspace",
        prompt="Lee README.md y explícame el proyecto.",
        write_event=lambda kind, text: None,
        cancelled=lambda: False,
    )

    assert len(provider.requests) == 2
    for peticion in provider.requests:
        assert peticion.reasoning_effort == "high"
        assert peticion.max_tokens == 8192


@pytest.mark.asyncio
async def test_sesion_nueva_con_effort_store_real_sin_tocar_effort_usa_alto(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """La ruta REAL de producción: ``ide_sessions._run_workers_agent`` siempre
    pasa ``effort_store=self.effort_store`` con el ``session_id`` real de la
    sesión -- nunca ``None`` -- así que el test de compatibilidad de arriba
    (sin store) no cubre el camino que de verdad corre en el IDE. Aquí se usa
    un ``EsfuerzoStore`` real recién creado y un ``session_id`` que JAMÁS
    llamó ``/effort``: exactamente la sesión nueva típica. Tiene que salir
    "high"/8192 igual que el default fijo de siempre -- si
    ``NIVEL_POR_DEFECTO`` cae a "medio" (o cualquier cosa que no sea "alto"),
    esta aserción revienta y así queda enganchada la regresión silenciosa que
    el test de arriba, al no pasar ``effort_store``, no puede ver."""
    session_id = "sesion-nunca-toco-effort"
    effort_store = EsfuerzoStore()  # sin ningún .fijar() -- sesión virgen.

    provider = _ReadmeProvider(MODELO_IDE_POR_DEFECTO)
    monkeypatch.setattr(agent_module, "WorkersAIProvider", lambda model: provider)

    await _agent(tmp_path).run(
        workspace_id="workspace",
        prompt="Lee README.md y explícame el proyecto.",
        write_event=lambda kind, text: None,
        cancelled=lambda: False,
        session_id=session_id,
        effort_store=effort_store,
    )

    assert len(provider.requests) == 2
    for peticion in provider.requests:
        assert peticion.reasoning_effort == "high"
        assert peticion.max_tokens == max_tokens_para_esfuerzo(8192, "alto")
        assert peticion.max_tokens == 8192 + 2000


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

    with pytest.raises(ProveedorInalcanzableError, match="Workers AI no está disponible"):
        await _agent(tmp_path).run(
            workspace_id="workspace",
            prompt="Revisa el proyecto.",
            write_event=lambda kind, text: events.append((kind, text)),
            cancelled=lambda: False,
        )

    finals = [text for kind, text in events if kind == "assistant_final"]
    assert provider.closed is True
    assert len(finals) == 1
    # El proveedor SÍ fue contactado y SÍ es quien falló (no pudo conectar) --
    # acá "el servicio de IA" es honesto, y el nombre del tipo real
    # (``ProveedorInalcanzableError``) le da al dueño algo concreto.
    assert "servicio de IA interrumpió la ejecución" in finals[0]
    assert "ProveedorInalcanzableError" in finals[0]
    assert "No alcancé a ejecutar ni verificar" in finals[0]
    assert ("status", "Trabajo completado.") not in events


@pytest.mark.asyncio
async def test_credencial_faltante_no_se_le_cuelga_al_proveedor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """El bug medido en vivo (auditoría, hallazgo "(a)"): sin credenciales de
    Workers AI configuradas, la petición HTTP NUNCA sale -- así que decir "el
    servicio de IA interrumpió la ejecución" es una mentira. El cierre debe
    culpar a ESTA instalación, no al proveedor."""

    class _SinCredencialesProvider(_ProviderBase):
        async def stream(self, request: CompletionRequest):
            if False:  # pragma: no cover
                yield StreamChunk(type="text", text="")
            raise CredencialInvalidaError(
                "Faltan credenciales de Workers AI: CLOUDFLARE_ACCOUNT_ID, "
                "CLOUDFLARE_API_TOKEN",
                provider="workers_ai",
            )

    provider = _SinCredencialesProvider(MODELO_IDE_POR_DEFECTO)
    monkeypatch.setattr(agent_module, "WorkersAIProvider", lambda model: provider)
    events: list[tuple[str, str]] = []

    with pytest.raises(CredencialInvalidaError):
        await _agent(tmp_path).run(
            workspace_id="workspace",
            prompt="Revisa el proyecto.",
            write_event=lambda kind, text: events.append((kind, text)),
            cancelled=lambda: False,
        )

    finals = [text for kind, text in events if kind == "assistant_final"]
    assert len(finals) == 1
    # Lo que NO debe decir: que el servicio de IA interrumpió algo que nunca
    # llegó a contactar.
    assert "servicio de IA interrumpió" not in finals[0]
    # Lo que SÍ debe decir: que es esta instalación, y el tipo exacto (sin
    # secretos -- ni el token ni el account id aparecen, solo el nombre de
    # la clase).
    assert "no fue una caída del proveedor" in finals[0]
    assert "CredencialInvalidaError" in finals[0]
    assert "CLOUDFLARE_API_TOKEN" not in finals[0]


@pytest.mark.asyncio
async def test_error_interno_desconocido_no_se_le_cuelga_al_proveedor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Cualquier excepción sin tipar (un bug de programación, no un
    ``LLMError``) también caía antes en el mismo ``else`` que le colgaba la
    falla al "servicio de IA". El default correcto es declarar la culpa
    hacia ADENTRO."""

    class _BugInternoProvider(_ProviderBase):
        async def stream(self, request: CompletionRequest):
            if False:  # pragma: no cover
                yield StreamChunk(type="text", text="")
            raise KeyError("algo que el propio companion rompió")

    provider = _BugInternoProvider(MODELO_IDE_POR_DEFECTO)
    monkeypatch.setattr(agent_module, "WorkersAIProvider", lambda model: provider)
    events: list[tuple[str, str]] = []

    with pytest.raises(KeyError):
        await _agent(tmp_path).run(
            workspace_id="workspace",
            prompt="Revisa el proyecto.",
            write_event=lambda kind, text: events.append((kind, text)),
            cancelled=lambda: False,
        )

    finals = [text for kind, text in events if kind == "assistant_final"]
    assert len(finals) == 1
    assert "servicio de IA interrumpió" not in finals[0]
    assert "fallo interno de Edecán" in finals[0]
    assert "KeyError" in finals[0]


def test_build_failure_final_distingue_peticion_invalida_de_caida_de_proveedor() -> None:
    """Unitario directo sobre ``build_failure_final`` (sin correr un turno
    completo): ``PeticionInvalidaError`` es un ``LLMError`` pero NO un
    ``ProviderDownError``/``RateLimitedError`` -- la petición sí llegó al
    proveedor, pero estaba mal armada desde acá, así que tampoco es "el
    servicio de IA interrumpió"."""

    error = PeticionInvalidaError("Workers AI devolvió 400: cuerpo inválido", provider="workers_ai")
    texto = build_failure_final(error)
    assert "servicio de IA interrumpió" not in texto
    assert "no fue una caída del proveedor" in texto
    assert "PeticionInvalidaError" in texto


def test_build_failure_final_oserror_culpa_al_disco_local() -> None:
    error = OSError(28, "No space left on device")
    texto = build_failure_final(error)
    assert "servicio de IA interrumpió" not in texto
    assert "sistema de archivos" in texto
    assert "OSError" in texto


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


def test_terminate_process_en_windows_usa_taskkill_arbol_completo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auditoría de portabilidad (docs/edecan-windows.md §1.2): en Windows,
    ``process.terminate()``/``process.kill()`` SOLO matan el PID raíz --
    ``TerminateProcess`` no conoce hijos. Sin ``taskkill /T`` un
    ``npm run test`` cancelado dejaría procesos huérfanos vivos. No hay PC
    Windows a mano para ejecutar esta rama, pero sí se puede fijar el
    contrato: con ``os.name`` mockeado a ``"nt"``, ``_terminate_process``
    debe invocar ``taskkill /T`` (y ``/T /F`` en el martillo) en vez de
    ``process.terminate()``/``process.kill()``.
    """

    class _FakeProcess:
        def __init__(self) -> None:
            self.pid = 4321
            self._polls = iter([None, 0])
            self.terminate_called = False
            self.kill_called = False

        def poll(self) -> int | None:
            return next(self._polls, 0)

        def wait(self, timeout: float | None = None) -> None:
            return None

        def terminate(self) -> None:
            self.terminate_called = True

        def kill(self) -> None:
            self.kill_called = True

    monkeypatch.setattr(agent_module.os, "name", "nt")
    comandos_ejecutados: list[list[str]] = []

    def _fake_run(comando: list[str], **kwargs: Any) -> Any:
        comandos_ejecutados.append(comando)

        class _Resultado:
            returncode = 0

        return _Resultado()

    monkeypatch.setattr(agent_module.subprocess, "run", _fake_run)

    proceso = _FakeProcess()
    agent_module.WorkersIDEAgent._terminate_process(proceso)  # type: ignore[arg-type]

    assert proceso.terminate_called is False
    assert proceso.kill_called is False
    assert comandos_ejecutados == [["taskkill", "/T", "/PID", "4321"]]


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


# --------------------------------------------------------------------------- #
# Portón de salida (Cable 1 del plan de verificación, ver ide_verificacion.py
# y MAX_REINTENTOS_DE_CIERRE_SIN_VERIFICAR en ide_workers_agent.py): el turno
# no puede declarar terminado un cambio real que nunca se verificó. Los tres
# tests de abajo necesitan escrituras REALES (``ok`` True) para disparar el
# portón -- `_FileStub` (usado en el resto del archivo) no implementa
# `write`/`edit`, así que cualquier intento ahí falla con `ok: False` y nunca
# activaría el portón; por eso usan un ``WorkspaceStore``/``FileService`` real.
# --------------------------------------------------------------------------- #


def _agent_real(tmp_path: Path) -> tuple[WorkersIDEAgent, str]:
    from edecan_companion.ide_files import FileService
    from edecan_companion.ide_workspaces import WorkspaceStore

    proyecto = tmp_path / "proyecto"
    proyecto.mkdir(exist_ok=True)
    workspaces = WorkspaceStore(tmp_path / "state")
    workspace_id = workspaces.authorize(str(proyecto))["id"]
    files = FileService(workspaces)
    return WorkersIDEAgent(workspaces, files), workspace_id


class _EscribeSinVerificarProvider(_ProviderBase):
    """Escribe un archivo real y trata de cerrar SIN llamar 'verificar'.

    Cuando el portón lo obliga (un mensaje de usuario inyectado le pide
    'verificar'), en la vuelta siguiente sí la llama con un comando que pasa.
    """

    def __init__(self, model: str, comando_ok: list[str]) -> None:
        super().__init__(model)
        self.comando_ok = comando_ok
        self.calls = 0
        self.mensajes_de_usuario: list[str] = []

    async def stream(self, request: CompletionRequest):
        self.calls += 1
        ultimo = request.messages[-1]
        if ultimo.role == "user" and isinstance(ultimo.content, str):
            self.mensajes_de_usuario.append(ultimo.content)

        if self.calls == 1:
            yield StreamChunk(
                type="tool_call",
                tool_call=ToolCall(
                    id="escribir-1",
                    name="escribir_archivo",
                    arguments={"ruta": "nuevo.txt", "contenido": "hola"},
                ),
            )
            return
        if self.calls == 2:
            # Intenta cerrar a ciegas -- el portón tiene que negarse.
            yield StreamChunk(type="text", text="Listo, terminé.")
            return
        if self.calls == 3:
            yield StreamChunk(
                type="tool_call",
                tool_call=ToolCall(
                    id="verificar-1", name="verificar", arguments={"comando": self.comando_ok}
                ),
            )
            return
        yield StreamChunk(type="text", text="Listo, esta vez sí verifiqué.")


@pytest.mark.asyncio
async def test_porton_niega_el_cierre_tras_escribir_sin_verificar_y_luego_acepta(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    comando_ok = [sys.executable, "-c", "import sys; sys.exit(0)"]
    provider = _EscribeSinVerificarProvider(MODELO_IDE_POR_DEFECTO, comando_ok)
    monkeypatch.setattr(agent_module, "WorkersAIProvider", lambda model: provider)
    events: list[tuple[str, str]] = []
    agent, workspace_id = _agent_real(tmp_path)

    await agent.run(
        workspace_id=workspace_id,
        prompt="Crea nuevo.txt.",
        write_event=lambda kind, text: events.append((kind, text)),
        cancelled=lambda: False,
    )

    # El primer intento de cierre (vuelta 2) NO alcanzó a producir un final:
    # el portón lo negó pidiendo 'verificar' de verdad, y solo tras esa
    # llamada (vuelta 3) se aceptó el cierre en la vuelta 4.
    assert provider.calls == 4
    assert any("usa 'verificar'" in m for m in provider.mensajes_de_usuario)
    assert [kind for kind, _ in events].count("assistant_final") == 1
    final_text = next(text for kind, text in events if kind == "assistant_final")
    assert "Listo, esta vez sí verifiqué." in final_text
    assert "Aviso automático" not in final_text


class _EscribeYNuncaVerificaProvider(_ProviderBase):
    """Escribe un archivo real y jamás llama 'verificar', ni siquiera cuando
    el portón se lo pide -- fija el TOPE de reintentos del portón."""

    def __init__(self, model: str) -> None:
        super().__init__(model)
        self.calls = 0

    async def stream(self, request: CompletionRequest):
        self.calls += 1
        if self.calls == 1:
            yield StreamChunk(
                type="tool_call",
                tool_call=ToolCall(
                    id="escribir-1",
                    name="escribir_archivo",
                    arguments={"ruta": "otro.txt", "contenido": "x"},
                ),
            )
            return
        yield StreamChunk(type="text", text="Ya quedó.")


@pytest.mark.asyncio
async def test_porton_cede_tras_el_tope_y_estampa_la_verdad_en_el_cierre(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = _EscribeYNuncaVerificaProvider(MODELO_IDE_POR_DEFECTO)
    monkeypatch.setattr(agent_module, "WorkersAIProvider", lambda model: provider)
    events: list[tuple[str, str]] = []
    agent, workspace_id = _agent_real(tmp_path)

    await agent.run(
        workspace_id=workspace_id,
        prompt="Crea otro.txt.",
        write_event=lambda kind, text: events.append((kind, text)),
        cancelled=lambda: False,
    )

    # 1 vuelta que escribe + tantas negaciones como el tope permite + 1 vuelta
    # final donde el portón ya cedió -- nunca un bucle sin fin.
    assert provider.calls == 1 + agent_module.MAX_REINTENTOS_DE_CIERRE_SIN_VERIFICAR + 1
    final_text = next(text for kind, text in events if kind == "assistant_final")
    assert "Aviso automático" in final_text
    assert "sin confirmar el resultado con 'verificar'" in final_text


class _EscribeYVerificaEnRojoProvider(_ProviderBase):
    """Escribe, verifica (con un comando que SIEMPRE falla) y se rinde."""

    def __init__(self, model: str, comando_falla: list[str]) -> None:
        super().__init__(model)
        self.comando_falla = comando_falla
        self.calls = 0

    async def stream(self, request: CompletionRequest):
        self.calls += 1
        if self.calls == 1:
            yield StreamChunk(
                type="tool_call",
                tool_call=ToolCall(
                    id="escribir-1",
                    name="escribir_archivo",
                    arguments={"ruta": "rojo.txt", "contenido": "x"},
                ),
            )
            return
        if self.calls == 2:
            yield StreamChunk(
                type="tool_call",
                tool_call=ToolCall(
                    id="verificar-1",
                    name="verificar",
                    arguments={"comando": self.comando_falla},
                ),
            )
            return
        yield StreamChunk(type="text", text="No logré arreglarlo, me detengo.")


@pytest.mark.asyncio
async def test_porton_no_bloquea_si_ya_verifico_pero_avisa_que_sigue_en_rojo(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    comando_falla = [sys.executable, "-c", "import sys; sys.exit(1)"]
    provider = _EscribeYVerificaEnRojoProvider(MODELO_IDE_POR_DEFECTO, comando_falla)
    monkeypatch.setattr(agent_module, "WorkersAIProvider", lambda model: provider)
    events: list[tuple[str, str]] = []
    agent, workspace_id = _agent_real(tmp_path)

    await agent.run(
        workspace_id=workspace_id,
        prompt="Crea rojo.txt.",
        write_event=lambda kind, text: events.append((kind, text)),
        cancelled=lambda: False,
    )

    # Ya se verificó DESPUÉS de la escritura (portón satisfecho) -- no hace
    # falta ni una vuelta extra -- pero el resultado sigue en rojo, así que
    # el cierre no puede sonar a éxito.
    assert provider.calls == 3
    final_text = next(text for kind, text in events if kind == "assistant_final")
    assert "Aviso automático" in final_text
    assert "sigue en rojo" in final_text


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


# --------------------------------------------------------------------- #
# Encargo: cablear ``METODO_FABLE.md`` y el maestro de seguridad
# (``security/security.md``) en el prompt del agente del IDE, en el orden
# MÉTODO -> SEGURIDAD -> MEMORIA (``memory_block``, destilada de
# ``MAIN_MEMORY.md`` -- ver ``ide_semilla_proyecto``, nunca el documento
# completo) -> reglas duras de ``SYSTEM_PROMPT`` AL FINAL, para que estas
# últimas ganen si algo choca con lo anterior.
# --------------------------------------------------------------------- #


class _FinalTextProvider(_ProviderBase):
    """Responde texto y termina en una sola vuelta -- solo sirve para
    capturar el ``CompletionRequest.system`` que le llegó, no para ejercitar
    herramientas."""

    def __init__(self, model: str) -> None:
        super().__init__(model)
        self.requests: list[CompletionRequest] = []

    async def stream(self, request: CompletionRequest):
        self.requests.append(request)
        yield StreamChunk(type="text", text="listo.")


def test_metodo_fable_prompt_block_carga_el_archivo_completo() -> None:
    bloque = agent_module._metodo_fable_prompt_block()
    assert bloque is not None
    assert "MÉTODO FABLE" in bloque
    # Cacheado: dos llamadas devuelven el mismo objeto, no releen el disco.
    assert bloque is agent_module._metodo_fable_prompt_block()


@pytest.mark.asyncio
async def test_metodo_fable_va_antes_que_las_reglas_duras_del_system_prompt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = _FinalTextProvider(MODELO_IDE_POR_DEFECTO)
    monkeypatch.setattr(agent_module, "WorkersAIProvider", lambda model: provider)

    await _agent(tmp_path).run(
        workspace_id="workspace",
        prompt="hola",
        write_event=lambda kind, text: None,
        cancelled=lambda: False,
    )

    system = provider.requests[0].system
    idx_metodo = system.find("MÉTODO FABLE")
    idx_reglas = system.find(agent_module.SYSTEM_PROMPT[:40])
    assert idx_metodo != -1
    assert idx_reglas != -1
    assert idx_metodo < idx_reglas, "el método debe ir ANTES de las reglas duras"
    # Las reglas duras viajan completas y sin recortar dentro del prompt final.
    assert agent_module.SYSTEM_PROMPT in system


@pytest.mark.asyncio
async def test_las_cuatro_piezas_estan_en_orden_metodo_seguridad_memoria_reglas(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Encargo textual: método, luego el maestro de seguridad, luego lo que
    llega por ``memory_block`` -- el canal por el que surgen las notas YA
    destiladas de ``MAIN_MEMORY.md`` (nunca el documento completo, ver
    ``ide_semilla_proyecto``) -- y las reglas duras de ``SYSTEM_PROMPT`` AL
    FINAL de las cuatro, para que ganen si algo choca."""
    provider = _FinalTextProvider(MODELO_IDE_POR_DEFECTO)
    monkeypatch.setattr(agent_module, "WorkersAIProvider", lambda model: provider)

    memory_block = (
        "Memoria de sesiones anteriores sobre este proyecto (lo que ya se "
        "descubrió y vale la pena no repetir):\n"
        "- [convencion] destilado-de-prueba-main-memory"
    )

    await _agent(tmp_path).run(
        workspace_id="workspace",
        prompt="hola",
        write_event=lambda kind, text: None,
        cancelled=lambda: False,
        memory_block=memory_block,
    )

    system = provider.requests[0].system
    idx_metodo = system.find("MÉTODO FABLE")
    idx_seguridad = system.find("edecan-security-engine")
    idx_memoria = system.find("destilado-de-prueba-main-memory")
    idx_reglas = system.find(agent_module.SYSTEM_PROMPT[:40])
    assert -1 not in (idx_metodo, idx_seguridad, idx_memoria, idx_reglas)
    assert idx_metodo < idx_seguridad < idx_memoria < idx_reglas, (
        "el orden debe ser método -> seguridad -> memoria -> reglas duras al final"
    )
    # Las reglas duras y el maestro de seguridad viajan completos, sin recortar.
    assert agent_module.SYSTEM_PROMPT in system
    assert agent_module._security_master_prompt_block() in system


@pytest.mark.asyncio
async def test_sin_ninguna_capa_en_disco_el_turno_no_revienta(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Mismo criterio que ``MAIN_MEMORY.md`` en ``ide_semilla_proyecto``: la
    ausencia de un documento no puede tumbar un turno -- el agente sigue con
    las reglas duras intactas, sin método ni maestro de seguridad."""
    agent_module._metodo_fable_prompt_block.cache_clear()
    agent_module._security_master_prompt_block.cache_clear()
    monkeypatch.setattr(
        agent_module, "_METODO_FABLE_PATH", tmp_path / "no-existe" / "METODO_FABLE.md"
    )
    monkeypatch.setattr(
        agent_module, "_SECURITY_MASTER_PATH", tmp_path / "no-existe" / "security.md"
    )
    provider = _FinalTextProvider(MODELO_IDE_POR_DEFECTO)
    monkeypatch.setattr(agent_module, "WorkersAIProvider", lambda model: provider)
    try:
        await _agent(tmp_path).run(
            workspace_id="workspace",
            prompt="hola",
            write_event=lambda kind, text: None,
            cancelled=lambda: False,
        )
        system = provider.requests[0].system
        assert system == agent_module.SYSTEM_PROMPT
    finally:
        # No dejar en caché el resultado "ausente" para el resto de la suite.
        agent_module._metodo_fable_prompt_block.cache_clear()
        agent_module._security_master_prompt_block.cache_clear()


@pytest.mark.asyncio
async def test_sin_security_master_en_disco_las_demas_capas_siguen(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Si solo falta ``security/security.md`` (paquete no empaquetado en este
    release), el método y las reglas duras siguen intactos -- ninguna capa
    depende de que las otras existan."""
    agent_module._security_master_prompt_block.cache_clear()
    monkeypatch.setattr(
        agent_module, "_SECURITY_MASTER_PATH", tmp_path / "no-existe" / "security.md"
    )
    provider = _FinalTextProvider(MODELO_IDE_POR_DEFECTO)
    monkeypatch.setattr(agent_module, "WorkersAIProvider", lambda model: provider)
    try:
        await _agent(tmp_path).run(
            workspace_id="workspace",
            prompt="hola",
            write_event=lambda kind, text: None,
            cancelled=lambda: False,
        )
        system = provider.requests[0].system
        assert "MÉTODO FABLE" in system
        assert agent_module.SYSTEM_PROMPT in system
        assert "edecan-security-engine" not in system
    finally:
        agent_module._security_master_prompt_block.cache_clear()


def test_security_master_prompt_block_carga_el_archivo_completo() -> None:
    bloque = agent_module._security_master_prompt_block()
    assert bloque is not None
    # El maestro (``security/security.md``) es el mismo contenido que
    # ``security/SKILL.md`` -- ver el brief: "el maestro es security/security.md
    # (== security/SKILL.md)".
    assert "name: edecan-security-engine" in bloque
    assert "security.md" in bloque or "SKILL.md" in bloque
    # Cacheado: dos llamadas devuelven el mismo objeto, no releen el disco.
    assert bloque is agent_module._security_master_prompt_block()


def test_chat_personal_no_recibe_el_maestro_de_seguridad() -> None:
    """El paquete de seguridad es SOLO para el agente del IDE. El chat
    personal lo maneja ``persona_v3``/Fable (``cognitive_architecture.py``),
    que no debe importar ni mencionar nada de ``security/``."""
    import edecan_core.cognitive_architecture as core_module

    fuente = Path(core_module.__file__).read_text(encoding="utf-8")
    assert "edecan-security-engine" not in fuente
    assert "security/security.md" not in fuente
    assert "security.md" not in fuente
