"""`edecan_core.llm_call_log.log_llm_call` -- bitácora local de cada llamada
al proveedor (idea 1 del dueño, ver el docstring del módulo: nace de un bug
que costó horas porque el `stdout` del sidecar de escritorio se evapora).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from edecan_core.llm_call_log import log_llm_call
from edecan_core.llm_types import ChatMessage


class _FakeProvider:
    name = "FakeProvider"


_SIN_PASAR = object()
"""Sentinela propio: distingue "no pasó `settings`" (usar el default con
`DATA_DIR=tmp_path`) de "pasó `settings=None` a propósito" -- `None` también
es el default real de un parámetro, así que no sirve como sentinela."""


def _settings(**overrides: Any) -> SimpleNamespace:
    return SimpleNamespace(**overrides)


def _call(
    tmp_path: Path,
    *,
    settings: Any = _SIN_PASAR,
    tools_offered: list[str] | None = None,
    tools_requested: list[str] | None = None,
    system_prompt: str = "Eres Edecán.",
    messages: list[ChatMessage] | None = None,
    response_text: str = "listo",
) -> None:
    log_llm_call(
        settings=_settings(DATA_DIR=str(tmp_path)) if settings is _SIN_PASAR else settings,
        tenant_id="tenant-1",
        user_id="user-1",
        provider=_FakeProvider(),
        model="modelo-x",
        iteration=0,
        system_prompt=system_prompt,
        messages=messages if messages is not None else [ChatMessage(role="user", content="hola")],
        tools_offered=tools_offered or [],
        tools_requested=tools_requested or [],
        response_text=response_text,
        duration_seconds=0.842,
        input_tokens=123,
        output_tokens=45,
    )


def _read_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# --------------------------------------------------------------------------
# No-op sin configuración -- exactamente lo que ven TODOS los demás tests de
# este paquete (`ctx.settings=None`): la bitácora no debe tocar el
# filesystem si nadie configuró `DATA_DIR`.
# --------------------------------------------------------------------------


def test_sin_settings_no_toca_el_filesystem(tmp_path: Path) -> None:
    _call(tmp_path, settings=None)
    assert list(tmp_path.iterdir()) == []


def test_settings_sin_data_dir_no_toca_el_filesystem(tmp_path: Path) -> None:
    _call(tmp_path, settings=_settings())
    assert list(tmp_path.iterdir()) == []


def test_apagador_explicito_edecan_llm_call_log_false(tmp_path: Path) -> None:
    _call(tmp_path, settings=_settings(DATA_DIR=str(tmp_path), EDECAN_LLM_CALL_LOG=False))
    assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------
# Escritura real -- el contenido que le importa al dueño para depurar.
# --------------------------------------------------------------------------


def test_escribe_una_linea_json_con_los_campos_clave(tmp_path: Path) -> None:
    _call(
        tmp_path,
        tools_offered=["buscar_correo", "crear_recordatorio"],
        tools_requested=["crear_recordatorio"],
    )

    path = tmp_path / "llm-calls.jsonl"
    assert path.exists()
    (registro,) = _read_lines(path)

    # Estos dos campos son el punto entero de la idea 1: comparar lo
    # OFRECIDO contra lo PEDIDO delata un selector que dejó una tool fuera
    # sin tener que reproducir la conversación.
    assert registro["tools_offered"] == ["buscar_correo", "crear_recordatorio"]
    assert registro["tools_requested"] == ["crear_recordatorio"]
    assert registro["model"] == "modelo-x"
    assert registro["provider"] == "FakeProvider"
    assert registro["input_tokens"] == 123
    assert registro["output_tokens"] == 45
    assert registro["duration_ms"] == 842
    assert "Eres Edecán." in registro["system_preview"]
    assert registro["response_text_preview"] == "listo"
    assert registro["messages_preview"] == [{"role": "user", "content_preview": "hola"}]


def test_incluye_solo_los_ultimos_n_mensajes_configurados(tmp_path: Path) -> None:
    mensajes = [ChatMessage(role="user", content=f"mensaje {i}") for i in range(10)]
    _call(
        tmp_path,
        settings=_settings(DATA_DIR=str(tmp_path), EDECAN_LLM_CALL_LOG_LAST_MESSAGES=3),
        messages=mensajes,
    )

    (registro,) = _read_lines(tmp_path / "llm-calls.jsonl")
    previews = [item["content_preview"] for item in registro["messages_preview"]]
    assert previews == ["mensaje 7", "mensaje 8", "mensaje 9"]


def test_resume_bloques_de_tool_use_y_tool_result_sin_json_crudo(tmp_path: Path) -> None:
    mensajes = [
        ChatMessage(
            role="assistant",
            content=[
                {"type": "text", "text": "voy a revisar"},
                {"type": "tool_use", "id": "c1", "name": "buscar_correo", "input": {"q": "Ana"}},
            ],
        ),
        ChatMessage(
            role="tool",
            content=[{"type": "tool_result", "tool_use_id": "c1", "content": "3 correos"}],
        ),
    ]
    _call(tmp_path, messages=mensajes)

    (registro,) = _read_lines(tmp_path / "llm-calls.jsonl")
    assistant_preview = registro["messages_preview"][0]["content_preview"]
    tool_preview = registro["messages_preview"][1]["content_preview"]
    assert "voy a revisar" in assistant_preview
    assert "tool_use:buscar_correo" in assistant_preview
    assert "tool_result:3 correos" in tool_preview


def test_redacta_secretos_evidentes_antes_de_persistir(tmp_path: Path) -> None:
    _call(
        tmp_path,
        system_prompt="usa esta clave: sk-ant-abcdefghijklmnop",
        response_text="listo, usé sk-ant-abcdefghijklmnop",
    )

    (registro,) = _read_lines(tmp_path / "llm-calls.jsonl")
    assert "sk-ant-abcdefghijklmnop" not in registro["system_preview"]
    assert "sk-ant-abcdefghijklmnop" not in registro["response_text_preview"]
    assert "[REDACTED]" in registro["system_preview"]


def test_trunca_textos_largos_segun_el_limite_configurado(tmp_path: Path) -> None:
    _call(
        tmp_path,
        settings=_settings(DATA_DIR=str(tmp_path), EDECAN_LLM_CALL_LOG_TRUNCATE_CHARS=20),
        system_prompt="x" * 500,
    )

    (registro,) = _read_lines(tmp_path / "llm-calls.jsonl")
    assert registro["system_preview"].startswith("x" * 20)
    assert "truncado" in registro["system_preview"]
    assert len(registro["system_preview"]) < 500


# --------------------------------------------------------------------------
# Rotación por tamaño -- no debe llenar el disco del dueño.
# --------------------------------------------------------------------------


def test_rota_el_archivo_al_superar_el_tamano_maximo(tmp_path: Path) -> None:
    settings = _settings(
        DATA_DIR=str(tmp_path),
        EDECAN_LLM_CALL_LOG_MAX_BYTES=1,  # cualquier línea ya lo supera
        EDECAN_LLM_CALL_LOG_BACKUP_COUNT=2,
    )
    _call(tmp_path, settings=settings, response_text="primera")
    _call(tmp_path, settings=settings, response_text="segunda")
    _call(tmp_path, settings=settings, response_text="tercera")

    path = tmp_path / "llm-calls.jsonl"
    assert path.exists()
    assert (tmp_path / "llm-calls.jsonl.1").exists()
    assert (tmp_path / "llm-calls.jsonl.2").exists()
    # El archivo activo siempre trae la escritura más reciente.
    (ultimo,) = _read_lines(path)
    assert ultimo["response_text_preview"] == "tercera"


def test_nunca_lanza_si_data_dir_es_invalido(tmp_path: Path) -> None:
    # Un archivo normal no puede usarse como directorio -- `mkdir` sobre él
    # lanza `NotADirectoryError`/`FileExistsError`. La bitácora debe tragarse
    # eso, nunca tumbar el turno real que la llamó.
    archivo_no_directorio = tmp_path / "esto-es-un-archivo"
    archivo_no_directorio.write_text("no soy un directorio")

    _call(tmp_path, settings=_settings(DATA_DIR=str(archivo_no_directorio)))
