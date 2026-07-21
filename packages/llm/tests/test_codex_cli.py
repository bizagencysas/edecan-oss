"""Tests de `CodexCLIProvider` — sin binarios reales: cada caso usa un
script ejecutable fake (`tmp_path`) como `binary_path`. Análogo a
`test_claude_cli.py`, adaptado al parseo JSONL tolerante de Codex (busca
`type` que contenga "message"/"agent", usa el ÚLTIMO como texto).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from edecan_llm.base import ChatMessage, CompletionRequest, ToolSpec
from edecan_llm.codex_cli import CodexCLIProvider
from edecan_llm.errors import CLINotAuthenticatedError, CLINotInstalledError, LLMError


def _make_fake_cli(
    tmp_path: Path,
    name: str = "codex",
    *,
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 0,
    sleep_seconds: float = 0,
    stdin_capture_name: str | None = None,
    args_capture_name: str | None = None,
) -> str:
    script = tmp_path / name
    lines = ["#!/bin/sh"]
    if args_capture_name:
        lines.append(f'printf "%s\\n" "$@" > "{tmp_path / args_capture_name}"')
    if stdin_capture_name:
        lines.append(f'cat > "{tmp_path / stdin_capture_name}"')
    else:
        lines.append("cat >/dev/null")
    if sleep_seconds:
        lines.append(f"sleep {sleep_seconds}")
    if stderr:
        lines.extend(["cat <<'EOF' >&2", stderr, "EOF"])
    if stdout:
        lines.extend(["cat <<'EOF'", stdout, "EOF"])
    lines.append(f"exit {exit_code}")
    script.write_text("\n".join(lines) + "\n")
    script.chmod(0o755)
    return str(script)


def _req(**overrides: object) -> CompletionRequest:
    base: dict[str, object] = dict(
        model="o3",
        system="Eres Edecán, un mayordomo de IA.",
        messages=[ChatMessage(role="user", content="¿Qué hora es?")],
    )
    base.update(overrides)
    return CompletionRequest(**base)


def test_binary_no_encontrado_lanza_cli_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    with pytest.raises(CLINotInstalledError):
        CodexCLIProvider()


def test_binary_path_explicito_no_pasa_por_which(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    fake = _make_fake_cli(tmp_path, stdout=json.dumps({"type": "agent_message", "text": "hola"}))
    provider = CodexCLIProvider(binary_path=fake)
    assert provider._binary_path == fake  # type: ignore[attr-defined]


def test_binary_resuelto_via_which(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _make_fake_cli(tmp_path, stdout=json.dumps({"type": "agent_message", "text": "hola"}))
    monkeypatch.setattr("shutil.which", lambda name: fake if name == "codex" else None)
    provider = CodexCLIProvider()
    assert provider._binary_path == fake  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_complete_extrae_texto_de_evento_message(tmp_path: Path) -> None:
    line = json.dumps(
        {
            "type": "agent_message",
            "text": "Son las 10:00am.",
            "usage": {"input_tokens": 11, "output_tokens": 4},
        }
    )
    fake = _make_fake_cli(tmp_path, stdout=line)
    provider = CodexCLIProvider(binary_path=fake)

    response = await provider.complete(_req())

    assert response.text == "Son las 10:00am."
    assert response.tool_calls == []
    assert response.stop_reason == "end"
    assert response.usage.input_tokens == 11
    assert response.usage.output_tokens == 4


@pytest.mark.asyncio
async def test_complete_usa_el_ultimo_mensaje_de_agente_no_los_concatena(tmp_path: Path) -> None:
    lines = [
        json.dumps({"type": "agent_message_delta", "text": "Pensando..."}),
        json.dumps({"type": "agent_message", "text": "Primera versión."}),
        json.dumps({"type": "agent_message", "text": "Respuesta final."}),
    ]
    fake = _make_fake_cli(tmp_path, stdout="\n".join(lines))
    provider = CodexCLIProvider(binary_path=fake)

    response = await provider.complete(_req())

    assert response.text == "Respuesta final."


@pytest.mark.asyncio
async def test_complete_message_con_content_como_lista_de_bloques(tmp_path: Path) -> None:
    line = json.dumps(
        {
            "type": "item.completed",
            "message": {"content": [{"type": "text", "text": "Bloque de texto"}]},
        }
    )
    fake = _make_fake_cli(tmp_path, stdout=line)
    provider = CodexCLIProvider(binary_path=fake)

    response = await provider.complete(_req())

    assert response.text == "Bloque de texto"


@pytest.mark.asyncio
async def test_complete_extrae_item_agent_message_del_jsonl_actual(tmp_path: Path) -> None:
    """Regresión del Codex CLI 0.144: el mensaje vive dentro de `item`."""
    tool_call_text = json.dumps(
        {"tool_call": {"name": "crear_artefactos", "arguments": {"formatos": ["pdf"]}}}
    )
    lines = [
        json.dumps({"type": "thread.started", "thread_id": "thread-test"}),
        json.dumps(
            {
                "type": "item.completed",
                "item": {"id": "warning", "type": "error", "message": "warning interno"},
            }
        ),
        json.dumps(
            {
                "type": "item.completed",
                "item": {"id": "answer", "type": "agent_message", "text": tool_call_text},
            }
        ),
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 21, "output_tokens": 7},
            }
        ),
    ]
    fake = _make_fake_cli(tmp_path, stdout="\n".join(lines))
    provider = CodexCLIProvider(binary_path=fake)
    tools = [ToolSpec(name="crear_artefactos", description="Crea archivos", input_schema={})]

    response = await provider.complete(_req(tools=tools))

    assert response.text == ""
    assert response.stop_reason == "tool_use"
    assert response.tool_calls[0].name == "crear_artefactos"
    assert response.usage.input_tokens == 21
    assert response.usage.output_tokens == 7


@pytest.mark.asyncio
async def test_complete_ningun_evento_reconocido_cae_a_stdout_crudo(tmp_path: Path) -> None:
    fake = _make_fake_cli(tmp_path, stdout=json.dumps({"type": "otro_tipo", "foo": "bar"}))
    provider = CodexCLIProvider(binary_path=fake)

    response = await provider.complete(_req())

    assert "otro_tipo" in response.text
    assert response.usage.input_tokens == 0


@pytest.mark.asyncio
async def test_complete_prompt_viaja_por_stdin(tmp_path: Path) -> None:
    fake = _make_fake_cli(
        tmp_path,
        stdout=json.dumps({"type": "agent_message", "text": "ok"}),
        stdin_capture_name="stdin.txt",
    )
    provider = CodexCLIProvider(binary_path=fake)

    await provider.complete(_req())

    stdin_content = (tmp_path / "stdin.txt").read_text()
    assert "motor de decisión de Edecan" in stdin_content
    assert "no ejecutes comandos" in stdin_content
    assert "únicamente el mensaje final destinado a la persona" in stdin_content
    assert "Eres Edecán, un mayordomo de IA." in stdin_content
    assert "Usuario: ¿Qué hora es?" in stdin_content


@pytest.mark.asyncio
async def test_complete_elimina_autonarracion_pero_conserva_respuesta_final(tmp_path: Path) -> None:
    leaked = (
        "El usuario dijo que era una broma. Debo responder con calma. "
        "Respondo con humor ligero."
        "JAJAJA, entendido."
    )
    line = json.dumps({"type": "agent_message", "text": leaked})
    fake = _make_fake_cli(tmp_path, stdout=line)
    provider = CodexCLIProvider(binary_path=fake)

    response = await provider.complete(_req())

    assert response.text == "JAJAJA, entendido."


@pytest.mark.asyncio
async def test_complete_agrega_flag_model_si_req_trae_modelo(tmp_path: Path) -> None:
    fake = _make_fake_cli(
        tmp_path,
        stdout=json.dumps({"type": "agent_message", "text": "ok"}),
        args_capture_name="args.txt",
    )
    provider = CodexCLIProvider(binary_path=fake)

    await provider.complete(_req(model="o3"))

    args = (tmp_path / "args.txt").read_text().splitlines()
    assert "exec" in args
    assert "--json" in args
    assert "--model" in args
    assert args[args.index("--model") + 1] == "o3"


@pytest.mark.asyncio
async def test_complete_aisla_codex_del_repo_y_sus_herramientas(tmp_path: Path) -> None:
    fake = _make_fake_cli(
        tmp_path,
        stdout=json.dumps({"type": "agent_message", "text": "ok"}),
        args_capture_name="args.txt",
    )
    provider = CodexCLIProvider(binary_path=fake)

    await provider.complete(_req())

    args = (tmp_path / "args.txt").read_text().splitlines()
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert "--ephemeral" in args
    assert "--ignore-user-config" in args
    assert "--skip-git-repo-check" in args
    disabled = [args[index + 1] for index, value in enumerate(args) if value == "--disable"]
    assert set(disabled) >= {
        "shell_tool",
        "unified_exec",
        "apps",
        "browser_use",
        "computer_use",
        "image_generation",
        "multi_agent",
    }
    isolated_workdir = Path(args[args.index("-C") + 1])
    assert isolated_workdir.name.startswith("edecan-codex-")
    assert not isolated_workdir.exists()


@pytest.mark.asyncio
async def test_complete_sin_modelo_no_agrega_flag(tmp_path: Path) -> None:
    fake = _make_fake_cli(
        tmp_path,
        stdout=json.dumps({"type": "agent_message", "text": "ok"}),
        args_capture_name="args.txt",
    )
    provider = CodexCLIProvider(binary_path=fake)

    await provider.complete(_req(model=""))

    args = (tmp_path / "args.txt").read_text().splitlines()
    assert "--model" not in args


@pytest.mark.asyncio
async def test_complete_con_tool_call_detectado(tmp_path: Path) -> None:
    tool_call_text = json.dumps(
        {"tool_call": {"name": "agenda_eventos", "arguments": {"dia": "hoy"}}}
    )
    line = json.dumps({"type": "agent_message", "text": tool_call_text})
    fake = _make_fake_cli(tmp_path, stdout=line)
    provider = CodexCLIProvider(binary_path=fake)
    tools = [ToolSpec(name="agenda_eventos", description="Lista eventos", input_schema={})]

    response = await provider.complete(_req(tools=tools))

    assert response.text == ""
    assert response.stop_reason == "tool_use"
    assert response.tool_calls[0].name == "agenda_eventos"
    assert response.tool_calls[0].arguments == {"dia": "hoy"}


@pytest.mark.asyncio
async def test_complete_exit_1_con_mensaje_de_login_lanza_not_authenticated(tmp_path: Path) -> None:
    fake = _make_fake_cli(tmp_path, stderr="Please run `codex login`", exit_code=1)
    provider = CodexCLIProvider(binary_path=fake)

    with pytest.raises(CLINotAuthenticatedError):
        await provider.complete(_req())


@pytest.mark.asyncio
async def test_complete_exit_1_generico_lanza_llm_error(tmp_path: Path) -> None:
    fake = _make_fake_cli(tmp_path, stderr="algo salió mal", exit_code=1)
    provider = CodexCLIProvider(binary_path=fake)

    with pytest.raises(LLMError) as excinfo:
        await provider.complete(_req())
    assert not isinstance(excinfo.value, CLINotAuthenticatedError)


@pytest.mark.asyncio
async def test_complete_timeout_lanza_llm_error(tmp_path: Path) -> None:
    fake = _make_fake_cli(tmp_path, sleep_seconds=5)
    provider = CodexCLIProvider(binary_path=fake, timeout_seconds=0.2)

    with pytest.raises(LLMError, match="LLM_CLI_TIMEOUT_SECONDS"):
        await provider.complete(_req())


@pytest.mark.asyncio
async def test_complete_binario_desaparece_lanza_cli_not_installed(tmp_path: Path) -> None:
    fake_path = str(tmp_path / "codex-fantasma")
    provider = CodexCLIProvider(binary_path=fake_path)

    with pytest.raises(CLINotInstalledError):
        await provider.complete(_req())


@pytest.mark.asyncio
async def test_stream_emite_un_chunk_por_evento_de_mensaje(tmp_path: Path) -> None:
    lines = [
        json.dumps({"type": "agent_message", "text": "Hola"}),
        json.dumps(
            {
                "type": "agent_message",
                "text": "Hola mundo",
                "usage": {"input_tokens": 6, "output_tokens": 2},
            }
        ),
    ]
    fake = _make_fake_cli(tmp_path, stdout="\n".join(lines))
    provider = CodexCLIProvider(binary_path=fake)

    chunks = [chunk async for chunk in provider.stream(_req())]

    text_chunks = [c for c in chunks if c.type == "text"]
    # `_extract_last_agent_message` usa el ÚLTIMO mensaje encontrado, no cada uno.
    assert [c.text for c in text_chunks] == ["Hola mundo"]
    usage_chunks = [c for c in chunks if c.type == "usage"]
    assert usage_chunks[-1].usage is not None
    assert usage_chunks[-1].usage.input_tokens == 6
    assert chunks[-1].type == "stop"


@pytest.mark.asyncio
async def test_stream_formato_no_reconocido_degrada_a_complete(tmp_path: Path) -> None:
    fake = _make_fake_cli(tmp_path, stdout=json.dumps({"type": "otro_tipo", "foo": "bar"}))
    provider = CodexCLIProvider(binary_path=fake)

    chunks = [chunk async for chunk in provider.stream(_req())]

    assert chunks[-1].type == "stop"
    text_chunks = [c for c in chunks if c.type == "text"]
    assert len(text_chunks) == 1
    assert "otro_tipo" in (text_chunks[0].text or "")
