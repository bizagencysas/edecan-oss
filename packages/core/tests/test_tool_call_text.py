from edecan_core.tool_call_text import (
    parece_json_de_tool,
    parece_llamada_en_corchetes,
    parse_emitted_tool_call,
    parse_emitted_tool_calls,
)


def test_parsea_el_json_que_copla_escribe_en_produccion() -> None:
    texto = """```json
{
    "name": "calculadora",
    "parameters": {
        "expresion": "(439000000 / 365 / 24 / 60 / 60)"
    }
}
```"""
    llamada = parse_emitted_tool_call(texto, {"calculadora", "buscar_web"})
    assert llamada is not None
    assert llamada.name == "calculadora"
    assert llamada.arguments == {"expresion": "(439000000 / 365 / 24 / 60 / 60)"}


def test_parsea_tool_call_envuelto_y_arguments() -> None:
    texto = '{"tool_call": {"name": "hora_actual", "arguments": {"zona": "UTC"}}}'
    llamada = parse_emitted_tool_call(texto, {"hora_actual"})
    assert llamada is not None
    assert llamada.name == "hora_actual"
    assert llamada.arguments == {"zona": "UTC"}


def test_rechaza_nombre_que_no_se_ofrecio() -> None:
    texto = '{"name": "calculadora", "parameters": {"expresion": "1+1"}}'
    assert parse_emitted_tool_call(texto, {"buscar_web"}) is None


def test_rechaza_prosa_alrededor_del_json() -> None:
    texto = 'Claro, lo calculo:\n{"name": "calculadora", "parameters": {"expresion": "1+1"}}'
    assert parse_emitted_tool_call(texto, {"calculadora"}) is None


def test_rechaza_json_que_no_es_tool() -> None:
    assert parse_emitted_tool_call('{"ok": true, "valor": 42}', {"calculadora"}) is None


def test_parece_json_de_tool_solo_en_el_prefijo() -> None:
    assert parece_json_de_tool('{"name":')
    assert parece_json_de_tool("```json\n{")
    assert not parece_json_de_tool("Netflix gana dinero con suscripciones.")


def test_parsea_corchetes_de_scout_en_produccion() -> None:
    texto = (
        '[excited] [curious] Voy a Cursor.\n'
        '[usar_computadora accion="open_app" parametros={app: Cursor}]\n'
        '[usar_computadora accion="input_key" parametros='
        '{texto: "Eso estoy haciendo justo al momento de escribir este mensaje"}]\n'
        '[usar_computadora accion="input_key" parametros={tecla: enter}]\n'
        '[usar_computadora accion="screenshot" parametros={}]\n'
        "Entiendo. He enviado el mensaje. ¿Quieres que haga algo más?"
    )
    llamadas = parse_emitted_tool_calls(texto, {"usar_computadora", "enviar_mensaje"})
    assert [c.name for c in llamadas] == ["usar_computadora"] * 4
    assert llamadas[0].arguments == {"accion": "open_app", "parametros": {"app": "Cursor"}}
    assert llamadas[1].arguments["accion"] == "input_key"
    assert llamadas[1].arguments["parametros"]["texto"] == (
        "Eso estoy haciendo justo al momento de escribir este mensaje"
    )
    assert llamadas[2].arguments == {"accion": "input_key", "parametros": {"tecla": "enter"}}
    assert llamadas[3].arguments == {"accion": "screenshot", "parametros": {}}


def test_no_confunde_tags_de_voz_con_herramientas() -> None:
    texto = "[excited] [pause] Hola, ¿cómo vas?"
    assert parse_emitted_tool_calls(texto, {"usar_computadora"}) == []
    assert not parece_llamada_en_corchetes(texto, {"usar_computadora"})
    assert parece_llamada_en_corchetes(
        '[usar_computadora accion="screenshot" parametros={}]', {"usar_computadora"}
    )
    assert parece_llamada_en_corchetes("[usar_comp", {"usar_computadora"})
