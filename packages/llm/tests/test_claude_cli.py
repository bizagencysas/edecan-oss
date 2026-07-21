"""Tests de `ClaudeCLIProvider` — sin binarios reales: cada caso usa un
script ejecutable fake (`tmp_path`) como `binary_path`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from edecan_llm.base import ChatMessage, CompletionRequest, ToolSpec
from edecan_llm.claude_cli import ClaudeCLIProvider
from edecan_llm.errors import CLINotAuthenticatedError, CLINotInstalledError, LLMError


def _make_fake_cli(
    tmp_path: Path,
    name: str = "claude",
    *,
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 0,
    sleep_seconds: float = 0,
    stdin_capture_name: str | None = None,
    args_capture_name: str | None = None,
) -> str:
    """Crea un `#!/bin/sh` ejecutable en `tmp_path` que consume stdin, opcionalmente
    duerme, y escribe `stdout`/`stderr` (vía heredoc con delimitador entre comillas
    simples: pasa el contenido tal cual, sin que la shell interprete `$`/backticks/
    backslashes dentro del JSON).
    """
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
        model="claude-sonnet-4-5",
        system="Eres Edecán, un mayordomo de IA.",
        messages=[ChatMessage(role="user", content="¿Qué hora es?")],
    )
    base.update(overrides)
    return CompletionRequest(**base)


def test_binary_no_encontrado_lanza_cli_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    with pytest.raises(CLINotInstalledError):
        ClaudeCLIProvider()


def test_binary_path_explicito_no_pasa_por_which(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    fake = _make_fake_cli(tmp_path, stdout=json.dumps({"result": "hola"}))
    provider = ClaudeCLIProvider(binary_path=fake)
    assert provider._binary_path == fake  # type: ignore[attr-defined]


def test_binary_resuelto_via_which(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _make_fake_cli(tmp_path, stdout=json.dumps({"result": "hola"}))
    monkeypatch.setattr("shutil.which", lambda name: fake if name == "claude" else None)
    provider = ClaudeCLIProvider()
    assert provider._binary_path == fake  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_complete_texto_normal(tmp_path: Path) -> None:
    payload = json.dumps(
        {"result": "Son las 10:00am.", "usage": {"input_tokens": 12, "output_tokens": 5}}
    )
    fake = _make_fake_cli(tmp_path, stdout=payload)
    provider = ClaudeCLIProvider(binary_path=fake)

    response = await provider.complete(_req())

    assert response.text == "Son las 10:00am."
    assert response.tool_calls == []
    assert response.stop_reason == "end"
    assert response.usage.input_tokens == 12
    assert response.usage.output_tokens == 5


@pytest.mark.asyncio
async def test_complete_prompt_viaja_por_stdin(tmp_path: Path) -> None:
    fake = _make_fake_cli(
        tmp_path,
        stdout=json.dumps({"result": "ok"}),
        stdin_capture_name="stdin.txt",
    )
    provider = ClaudeCLIProvider(binary_path=fake)

    await provider.complete(_req())

    stdin_content = (tmp_path / "stdin.txt").read_text()
    assert "respuesta final destinada a la persona" in stdin_content
    assert "No muestres analisis, razonamiento" in stdin_content
    assert "Eres Edecán, un mayordomo de IA." in stdin_content
    assert "Usuario: ¿Qué hora es?" in stdin_content


@pytest.mark.asyncio
async def test_complete_elimina_autonarracion_pero_conserva_respuesta_final(tmp_path: Path) -> None:
    leaked = (
        "El usuario aclaró que era un video de TikTok, no una petición real. "
        "No necesito herramientas. Nada de tool calls aquí. "
        "Respondo con calidez, aligerando pero sin dramatizar de más. "
        "Tono profesional-cálido, sin emojis."
        "JAJAJA, vale, ahora sí entendí."
    )
    fake = _make_fake_cli(tmp_path, stdout=json.dumps({"result": leaked}))
    provider = ClaudeCLIProvider(binary_path=fake)

    response = await provider.complete(_req())

    assert response.text == "JAJAJA, vale, ahora sí entendí."
    assert "El usuario" not in response.text
    assert "herramientas" not in response.text


@pytest.mark.asyncio
async def test_complete_no_altera_respuesta_legitima_sobre_herramientas(tmp_path: Path) -> None:
    visible = "No hace falta ninguna herramienta para esto. Puedo explicártelo aquí."
    fake = _make_fake_cli(tmp_path, stdout=json.dumps({"result": visible}))
    provider = ClaudeCLIProvider(binary_path=fake)

    response = await provider.complete(_req())

    assert response.text == visible


@pytest.mark.asyncio
async def test_complete_serializa_historial_multi_turno(tmp_path: Path) -> None:
    fake = _make_fake_cli(
        tmp_path,
        stdout=json.dumps({"result": "ok"}),
        stdin_capture_name="stdin.txt",
    )
    provider = ClaudeCLIProvider(binary_path=fake)
    req = _req(
        messages=[
            ChatMessage(role="user", content="Hola"),
            ChatMessage(role="assistant", content="¡Hola! ¿En qué ayudo?"),
            ChatMessage(role="user", content="¿Qué hora es?"),
        ]
    )

    await provider.complete(req)

    stdin_content = (tmp_path / "stdin.txt").read_text()
    assert "Usuario: Hola" in stdin_content
    assert "Asistente: ¡Hola! ¿En qué ayudo?" in stdin_content
    assert "Usuario: ¿Qué hora es?" in stdin_content
    # El turno de usuario más reciente queda al final del prompt.
    assert stdin_content.index("Usuario: ¿Qué hora es?") > stdin_content.index("Asistente:")


@pytest.mark.asyncio
async def test_complete_json_invalido_cae_a_texto_crudo(tmp_path: Path) -> None:
    fake = _make_fake_cli(tmp_path, stdout="Esto no es JSON, es texto plano.")
    provider = ClaudeCLIProvider(binary_path=fake)

    response = await provider.complete(_req())

    assert response.text == "Esto no es JSON, es texto plano."
    assert response.usage.input_tokens == 0
    assert response.usage.output_tokens == 0
    assert response.stop_reason == "end"


@pytest.mark.asyncio
async def test_complete_agrega_flag_model_si_req_trae_modelo(tmp_path: Path) -> None:
    fake = _make_fake_cli(
        tmp_path,
        stdout=json.dumps({"result": "ok"}),
        args_capture_name="args.txt",
    )
    provider = ClaudeCLIProvider(binary_path=fake)

    await provider.complete(_req(model="claude-opus-4-5"))

    args = (tmp_path / "args.txt").read_text().splitlines()
    assert "--model" in args
    assert args[args.index("--model") + 1] == "claude-opus-4-5"


@pytest.mark.asyncio
async def test_complete_sin_modelo_no_agrega_flag(tmp_path: Path) -> None:
    fake = _make_fake_cli(
        tmp_path,
        stdout=json.dumps({"result": "ok"}),
        args_capture_name="args.txt",
    )
    provider = ClaudeCLIProvider(binary_path=fake)

    await provider.complete(_req(model=""))

    args = (tmp_path / "args.txt").read_text().splitlines()
    assert "--model" not in args


@pytest.mark.asyncio
async def test_complete_con_tool_call_detectado(tmp_path: Path) -> None:
    tool_call_text = json.dumps(
        {"tool_call": {"name": "agenda_eventos", "arguments": {"dia": "hoy"}}}
    )
    payload = json.dumps(
        {"result": tool_call_text, "usage": {"input_tokens": 20, "output_tokens": 8}}
    )
    fake = _make_fake_cli(tmp_path, stdout=payload)
    provider = ClaudeCLIProvider(binary_path=fake)
    tools = [ToolSpec(name="agenda_eventos", description="Lista eventos", input_schema={})]

    response = await provider.complete(_req(tools=tools))

    assert response.text == ""
    assert response.stop_reason == "tool_use"
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "agenda_eventos"
    assert response.tool_calls[0].arguments == {"dia": "hoy"}
    assert response.tool_calls[0].id  # uuid4 generado, no vacío


@pytest.mark.asyncio
async def test_complete_tool_call_envuelto_en_fence_markdown(tmp_path: Path) -> None:
    inner = '```json\n{"tool_call": {"name": "agenda_eventos", "arguments": {"dia": "hoy"}}}\n```'
    payload = json.dumps({"result": inner})
    fake = _make_fake_cli(tmp_path, stdout=payload)
    provider = ClaudeCLIProvider(binary_path=fake)
    tools = [ToolSpec(name="agenda_eventos", description="Lista eventos", input_schema={})]

    response = await provider.complete(_req(tools=tools))

    assert response.stop_reason == "tool_use"
    assert response.tool_calls[0].name == "agenda_eventos"


@pytest.mark.asyncio
async def test_complete_con_tools_pero_respuesta_de_texto_normal(tmp_path: Path) -> None:
    payload = json.dumps({"result": "No hace falta ninguna herramienta para esto."})
    fake = _make_fake_cli(tmp_path, stdout=payload)
    provider = ClaudeCLIProvider(binary_path=fake)
    tools = [ToolSpec(name="agenda_eventos", description="Lista eventos", input_schema={})]

    response = await provider.complete(_req(tools=tools))

    assert response.tool_calls == []
    assert response.stop_reason == "end"
    assert response.text == "No hace falta ninguna herramienta para esto."


@pytest.mark.asyncio
async def test_complete_agrega_bloque_de_tools_al_prompt(tmp_path: Path) -> None:
    fake = _make_fake_cli(
        tmp_path,
        stdout=json.dumps({"result": "ok"}),
        stdin_capture_name="stdin.txt",
    )
    provider = ClaudeCLIProvider(binary_path=fake)
    tools = [ToolSpec(name="agenda_eventos", description="Lista eventos", input_schema={})]

    await provider.complete(_req(tools=tools))

    stdin_content = (tmp_path / "stdin.txt").read_text()
    assert "tool_call" in stdin_content
    assert "agenda_eventos" in stdin_content


@pytest.mark.asyncio
async def test_complete_exit_1_con_mensaje_de_login_lanza_not_authenticated(tmp_path: Path) -> None:
    fake = _make_fake_cli(tmp_path, stderr="Please run `claude login`", exit_code=1)
    provider = ClaudeCLIProvider(binary_path=fake)

    with pytest.raises(CLINotAuthenticatedError):
        await provider.complete(_req())


@pytest.mark.asyncio
async def test_complete_exit_1_generico_lanza_llm_error(tmp_path: Path) -> None:
    fake = _make_fake_cli(tmp_path, stderr="algo salió mal", exit_code=1)
    provider = ClaudeCLIProvider(binary_path=fake)

    with pytest.raises(LLMError) as excinfo:
        await provider.complete(_req())
    assert not isinstance(excinfo.value, CLINotAuthenticatedError)


@pytest.mark.asyncio
async def test_complete_timeout_lanza_llm_error(tmp_path: Path) -> None:
    fake = _make_fake_cli(tmp_path, sleep_seconds=5)
    provider = ClaudeCLIProvider(binary_path=fake, timeout_seconds=0.2)

    with pytest.raises(LLMError, match="LLM_CLI_TIMEOUT_SECONDS"):
        await provider.complete(_req())


@pytest.mark.asyncio
async def test_complete_binario_desaparece_lanza_cli_not_installed(tmp_path: Path) -> None:
    # `binary_path` explícito se confía sin chequear que exista en __init__
    # (a diferencia de la resolución vía `shutil.which`) — el error solo
    # aparece al intentar ejecutarlo de verdad.
    fake_path = str(tmp_path / "claude-fantasma")
    provider = ClaudeCLIProvider(binary_path=fake_path)

    with pytest.raises(CLINotInstalledError):
        await provider.complete(_req())


@pytest.mark.asyncio
async def test_stream_formato_reconocido_emite_texto_incremental(tmp_path: Path) -> None:
    lines = [
        json.dumps(
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Hola"}]}}
        ),
        json.dumps(
            {"type": "assistant", "message": {"content": [{"type": "text", "text": " mundo"}]}}
        ),
        json.dumps(
            {
                "type": "result",
                "result": "Hola mundo",
                "usage": {"input_tokens": 9, "output_tokens": 3},
            }
        ),
    ]
    fake = _make_fake_cli(tmp_path, stdout="\n".join(lines))
    provider = ClaudeCLIProvider(binary_path=fake)

    chunks = [chunk async for chunk in provider.stream(_req())]

    text_chunks = [c for c in chunks if c.type == "text"]
    assert [c.text for c in text_chunks] == ["Hola", " mundo"]
    usage_chunks = [c for c in chunks if c.type == "usage"]
    assert usage_chunks[-1].usage is not None
    assert usage_chunks[-1].usage.input_tokens == 9
    assert usage_chunks[-1].usage.output_tokens == 3
    assert chunks[-1].type == "stop"


@pytest.mark.asyncio
async def test_stream_no_emite_prefacio_de_autonarracion(tmp_path: Path) -> None:
    lines = [
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": "El usuario aclaró que era una broma. ",
                        }
                    ]
                },
            }
        ),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": "Debo responder con humor. JAJAJA, entendido.",
                        }
                    ]
                },
            }
        ),
        json.dumps({"type": "result", "result": "resultado completo"}),
    ]
    fake = _make_fake_cli(tmp_path, stdout="\n".join(lines))
    provider = ClaudeCLIProvider(binary_path=fake)

    chunks = [chunk async for chunk in provider.stream(_req())]

    visible = "".join(chunk.text or "" for chunk in chunks if chunk.type == "text")
    assert visible == "JAJAJA, entendido."
    assert "El usuario" not in visible
    assert "Debo responder" not in visible


@pytest.mark.asyncio
async def test_stream_formato_no_reconocido_degrada_a_complete(tmp_path: Path) -> None:
    # Ninguna línea tiene type "assistant"/"result": no hay nada reconocible.
    fake = _make_fake_cli(tmp_path, stdout=json.dumps({"type": "otro_evento", "foo": "bar"}))
    provider = ClaudeCLIProvider(binary_path=fake)

    chunks = [chunk async for chunk in provider.stream(_req())]

    assert chunks[-1].type == "stop"
    usage_chunks = [c for c in chunks if c.type == "usage"]
    assert len(usage_chunks) == 1
    # Fallback: el stdout crudo entero se usó como texto (vía `_parse_response`).
    text_chunks = [c for c in chunks if c.type == "text"]
    assert len(text_chunks) == 1
    assert "otro_evento" in (text_chunks[0].text or "")
