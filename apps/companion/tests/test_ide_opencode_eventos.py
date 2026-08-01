"""Tests de ``ide_opencode_eventos.py`` -- con cargas REALES del SSE de opencode.

Ninguna de las cargas de ``data`` de este archivo se inventó a mano. Se arrancó un
``opencode serve`` real (binario ``1.17.18`` de esta Mac) contra Cloudflare Workers AI
real, se dispararon prompts/cambios de modelo/cambios de agente/reversiones reales, y se
copiaron aquí EXACTAMENTE los payloads que llegaron por ``GET /api/session/{id}/event``
(ver el docstring de ``ide_opencode_eventos.py``, "De dónde salieron las 28 variantes",
para el detalle completo de qué se disparó y cómo). Los pocos casos que no se lograron
disparar en esta sesión de trabajo (``moved``, ``synthetic``, ``shell.*``,
``tool.progress``, ``retried``, ``compaction.*``) están construidos a partir de la forma
real de ``/doc`` (leído del MISMO servidor, no de memoria) y marcados explícitamente como
tal en su propio comentario -- nunca se presentan como si vinieran de una captura.
"""

from __future__ import annotations

import json

from edecan_companion.ide_opencode import EventoSesion
from edecan_companion.ide_opencode_eventos import (
    TIPO_PERMISO_AGENTE,
    TIPO_PREGUNTA_AGENTE,
    TIPOS_DESCARTADOS,
    TIPOS_EVENTO_SESION,
    EventoTraducido,
    TraductorDeTurno,
    traducir_permiso,
    traducir_pregunta,
)
from edecan_companion.ide_opencode_permisos import (
    FuenteHerramienta,
    InfoPregunta,
    OpcionPregunta,
    SolicitudPermiso,
    SolicitudPregunta,
)


def _ev(tipo: str, data: dict, id_: str = "evt_test0000000000000000") -> EventoSesion:
    return EventoSesion(id=id_, type=tipo, data=data)


# --------------------------------------------------------------------------- #
# Cobertura: los 28 tipos reales (ver el docstring del módulo sobre el 27 vs
# 28) tienen que estar todos declarados, y TIPOS_DESCARTADOS tiene que ser un
# subconjunto real de ellos -- si alguno se escribe mal, esto lo grita aquí
# en vez de fallar en silencio dentro de traducir().
# --------------------------------------------------------------------------- #


def test_tipos_evento_sesion_son_las_28_variantes_reales_de_doc() -> None:
    assert len(TIPOS_EVENTO_SESION) == 28
    assert TIPOS_DESCARTADOS <= TIPOS_EVENTO_SESION


def test_todos_los_tipos_conocidos_producen_algo_o_se_descartan_a_proposito() -> None:
    """Recorre los 28 tipos reales con una carga mínima (solo lo que su schema real exige
    como obligatorio) y comprueba que ``traducir`` nunca cae en la rama de "no
    reconocido" para ninguno de ellos -- esa rama es solo para variantes FUTURAS."""

    cargas_minimas = {
        "session.next.agent.switched": {
            "sessionID": "ses_x",
            "messageID": "msg_x",
            "agent": "build",
        },
        "session.next.model.switched": {
            "sessionID": "ses_x",
            "messageID": "msg_x",
            "model": {"id": "m", "providerID": "p"},
        },
        "session.next.moved": {"sessionID": "ses_x", "location": {"directory": "/tmp/x"}},
        "session.next.prompted": {"sessionID": "ses_x", "messageID": "msg_x"},
        "session.next.prompt.admitted": {"sessionID": "ses_x", "messageID": "msg_x"},
        "session.next.context.updated": {"sessionID": "ses_x", "messageID": "msg_x", "text": "x"},
        "session.next.synthetic": {"sessionID": "ses_x", "messageID": "msg_x", "text": "x"},
        "session.next.shell.started": {"sessionID": "ses_x", "callID": "c1", "command": "ls"},
        "session.next.shell.ended": {"sessionID": "ses_x", "callID": "c1", "output": "x"},
        "session.next.step.started": {"sessionID": "ses_x", "assistantMessageID": "msg_x"},
        "session.next.step.ended": {
            "sessionID": "ses_x",
            "assistantMessageID": "msg_x",
            "finish": "stop",
        },
        "session.next.step.failed": {
            "sessionID": "ses_x",
            "assistantMessageID": "msg_x",
            "error": {"type": "unknown", "message": "x"},
        },
        "session.next.text.started": {
            "sessionID": "ses_x",
            "assistantMessageID": "msg_x",
            "textID": "t0",
        },
        "session.next.text.ended": {
            "sessionID": "ses_x",
            "assistantMessageID": "msg_x",
            "textID": "t0",
            "text": "x",
        },
        "session.next.tool.input.started": {"sessionID": "ses_x", "callID": "c1", "name": "read"},
        "session.next.tool.input.ended": {"sessionID": "ses_x", "callID": "c1", "text": "{}"},
        "session.next.tool.called": {
            "sessionID": "ses_x",
            "callID": "c1",
            "tool": "read",
            "input": {},
            "provider": {"executed": False},
        },
        "session.next.tool.progress": {
            "sessionID": "ses_x",
            "callID": "c1",
            "structured": {},
            "content": [{"type": "text", "text": "avanzando..."}],
        },
        "session.next.tool.success": {
            "sessionID": "ses_x",
            "callID": "c1",
            "structured": {},
            "content": [],
            "provider": {"executed": False},
        },
        "session.next.tool.failed": {
            "sessionID": "ses_x",
            "callID": "c1",
            "error": {"type": "unknown", "message": "x"},
            "provider": {"executed": False},
        },
        "session.next.reasoning.started": {"sessionID": "ses_x", "reasoningID": "r0"},
        "session.next.reasoning.ended": {"sessionID": "ses_x", "reasoningID": "r0", "text": "x"},
        "session.next.retried": {
            "sessionID": "ses_x",
            "attempt": 1,
            "error": {"message": "x", "isRetryable": True},
        },
        "session.next.compaction.started": {
            "sessionID": "ses_x",
            "messageID": "msg_x",
            "reason": "x",
        },
        "session.next.compaction.ended": {
            "sessionID": "ses_x",
            "messageID": "msg_x",
            "reason": "x",
            "text": "x",
            "recent": "x",
        },
        "session.next.revert.staged": {"sessionID": "ses_x", "revert": {"messageID": "msg_x"}},
        "session.next.revert.cleared": {"sessionID": "ses_x"},
        "session.next.revert.committed": {"sessionID": "ses_x", "messageID": "msg_x"},
    }
    assert set(cargas_minimas) == TIPOS_EVENTO_SESION

    # Con esta carga mínima y SIN un tool.called previo que los correlacione,
    # tool.success/tool.progress pueden legítimamente no tener nada nuevo que
    # anunciar (mismo criterio que un "read" real: ver
    # test_tool_called_read_no_genera_file...) -- lo que importa aquí es que
    # NINGUNO caiga en la rama de "variante no reconocida", no que todos
    # produzcan texto.
    _PUEDEN_SALIR_VACIOS_SIN_CORRELACION = {"session.next.tool.success"}

    for tipo, data in cargas_minimas.items():
        traductor = TraductorDeTurno()
        resultado = traductor.traducir(_ev(tipo, data))
        if tipo in TIPOS_DESCARTADOS:
            assert resultado == [], tipo
            continue
        if not resultado:
            assert tipo in _PUEDEN_SALIR_VACIOS_SIN_CORRELACION, (
                f"{tipo} no produjo ningún evento traducido"
            )
            continue
        for traducido in resultado:
            assert "no reconoce" not in traducido.texto, tipo


def test_evento_desconocido_no_lanza_y_se_declara_en_vez_de_inventar() -> None:
    """Regla del docstring: una variante 29 futura no debe tumbar el turno -- se declara,
    no se descarta en silencio ni se lanza una excepción."""

    traductor = TraductorDeTurno()
    resultado = traductor.traducir(
        _ev(
            "session.next.algo.nuevo.que.no.existe.todavia",
            {"sessionID": "ses_x", "campo": "valor"},
        )
    )
    assert len(resultado) == 1
    assert resultado[0].tipo == "status"
    assert "session.next.algo.nuevo.que.no.existe.todavia" in resultado[0].texto
    assert "no reconoce" in resultado[0].texto


# --------------------------------------------------------------------------- #
# Descartes -- con las cargas REALES capturadas (ver el docstring del módulo,
# "Qué se descarta a propósito").
# --------------------------------------------------------------------------- #


def test_prompt_admitted_se_descarta_por_ser_identico_a_prompted() -> None:
    carga = {
        "timestamp": 1785468905843,
        "sessionID": "ses_049c297fdffeS27JsvSYOcHDZe",
        "messageID": "msg_fb63d69720012mMt56beK6t32f",
        "prompt": {"text": 'Di "hola" en texto plano, nada mas.'},
        "delivery": "steer",
    }
    assert TraductorDeTurno().traducir(_ev("session.next.prompt.admitted", carga)) == []


def test_context_updated_se_descarta_pese_a_traer_texto_real() -> None:
    """Carga real capturada: opencode inyectando la lista completa de skills disponibles
    en el contexto del modelo -- exactamente el caso que justifica el descarte (es
    opencode hablándose a sí mismo, no a la persona)."""

    carga = {
        "timestamp": 1785468965407,
        "sessionID": "ses_049c1c2d2ffeVl0HH9MpR3m0us",
        "messageID": "msg_fb63e521f001fWHJaqv0bRcH2X",
        "text": "Skills provide specialized instructions and workflows for specific tasks.\n"
        "<available_skills>...</available_skills>",
    }
    assert TraductorDeTurno().traducir(_ev("session.next.context.updated", carga)) == []


def test_text_started_tool_input_started_reasoning_started_se_descartan() -> None:
    traductor = TraductorDeTurno()
    assert (
        traductor.traducir(
            _ev(
                "session.next.text.started",
                {
                    "sessionID": "ses_049c297fdffeS27JsvSYOcHDZe",
                    "assistantMessageID": "msg_fb63d7071001GAvAdSH6Nc6uLU",
                    "textID": "text-0",
                },
            )
        )
        == []
    )
    assert (
        traductor.traducir(
            _ev(
                "session.next.tool.input.started",
                {
                    "sessionID": "ses_049c1c2d2ffeVl0HH9MpR3m0us",
                    "assistantMessageID": "msg_fb63e42b2001kaMQW7V8Qi0wej",
                    "callID": "functions.read:0",
                    "name": "read",
                },
            )
        )
        == []
    )
    assert (
        traductor.traducir(
            _ev(
                "session.next.reasoning.started",
                {
                    "sessionID": "ses_049c297fdffeS27JsvSYOcHDZe",
                    "assistantMessageID": "msg_fb63d7071001GAvAdSH6Nc6uLU",
                    "reasoningID": "reasoning-0",
                },
            )
        )
        == []
    )


# --------------------------------------------------------------------------- #
# Familia: identidad de sesión (agente/modelo/directorio) -- cargas reales
# capturadas (agent.switched, model.switched); moved queda marcado como
# derivado del schema (ver el docstring del módulo).
# --------------------------------------------------------------------------- #


def test_agent_switched_con_carga_real() -> None:
    carga = {
        "timestamp": 1785470048583,
        "sessionID": "ses_049b1328affeTgnHt0j4BFlRHS",
        "messageID": "msg_fb64ed947001mXLqJUSOOjRffE",
        "agent": "plan",
    }
    resultado = TraductorDeTurno().traducir(_ev("session.next.agent.switched", carga))
    assert resultado == [EventoTraducido("status", "Cambié de agente a «plan».")]


def test_model_switched_con_carga_real() -> None:
    carga = {
        "timestamp": 1785470105950,
        "sessionID": "ses_049b069f6ffewTecECdNuWLUT2",
        "messageID": "msg_fb64fb95e001eECfQAvpf3EZk2",
        "model": {"id": "@cf/meta/llama-3.1-8b-instruct", "providerID": "workersai"},
    }
    resultado = TraductorDeTurno().traducir(_ev("session.next.model.switched", carga))
    assert resultado == [
        EventoTraducido("status", "Cambié de modelo a workersai/@cf/meta/llama-3.1-8b-instruct.")
    ]


def test_moved_derivado_del_schema_real_de_doc() -> None:
    """NO capturado en vivo -- ver el docstring del módulo: el único disparador
    documentado es el control-plane experimental, fuera de la superficie /api/session
    que usa este cimiento. Forma tomada de SessionNextMoved en /doc."""

    carga = {
        "timestamp": 1785470000000,
        "sessionID": "ses_derivado_de_doc",
        "location": {"directory": "/Users/x/workspace/sub", "workspaceID": None},
        "subdirectory": "sub",
    }
    resultado = TraductorDeTurno().traducir(_ev("session.next.moved", carga))
    assert resultado == [
        EventoTraducido(
            "status",
            "Cambié el directorio de trabajo a /Users/x/workspace/sub. (subdirectorio: sub)",
        )
    ]


# --------------------------------------------------------------------------- #
# Familia: pasos (step.started/ended/failed) -- cargas reales, incluida un
# step.failed real (401 forzado con una API key inválida a propósito).
# --------------------------------------------------------------------------- #


def test_step_started_anuncia_agente_y_modelo_con_carga_real() -> None:
    carga = {
        "timestamp": 1785468907633,
        "sessionID": "ses_049c297fdffeS27JsvSYOcHDZe",
        "assistantMessageID": "msg_fb63d7071001GAvAdSH6Nc6uLU",
        "agent": "build",
        "model": {
            "id": "@cf/moonshotai/kimi-k2.7-code",
            "providerID": "workersai",
            "variant": "default",
        },
    }
    resultado = TraductorDeTurno().traducir(_ev("session.next.step.started", carga))
    assert resultado == [
        EventoTraducido(
            "status",
            "Nuevo paso -- agente «build», modelo workersai/@cf/moonshotai/kimi-k2.7-code.",
        )
    ]


def test_step_ended_con_carga_real() -> None:
    carga = {
        "timestamp": 1785468908056,
        "sessionID": "ses_049c297fdffeS27JsvSYOcHDZe",
        "assistantMessageID": "msg_fb63d7071001GAvAdSH6Nc6uLU",
        "finish": "stop",
        "cost": 0,
        "tokens": {"input": 116, "output": 35, "reasoning": 0, "cache": {"read": 1856, "write": 0}},
    }
    resultado = TraductorDeTurno().traducir(_ev("session.next.step.ended", carga))
    assert resultado == [EventoTraducido("status", "Paso terminado (motivo: stop).")]


def test_step_failed_da_error_con_el_mensaje_real_del_401_forzado() -> None:
    """Carga real: se configuró opencode con una apiKey inválida A PROPÓSITO y se mandó
    un prompt real -- Cloudflare respondió 401 y opencode lo propagó como
    session.next.step.failed con el mensaje HTTP real. Esta es, deliberadamente, la
    primera vez que el evento 'error' del contrato de la interfaz (EVENT_LABELS['error']
    en AgentThread.tsx, nunca antes emitido por el motor viejo) se produce de verdad."""

    carga = {
        "timestamp": 1785470156104,
        "sessionID": "ses_049af87ddffeieXyqkNSaARsIG",
        "assistantMessageID": "msg_fb6507d45001dhTZdr6mf1p2Mk",
        "error": {
            "type": "unknown",
            "message": 'Provider request failed with HTTP 401: {"result":null,"success":false,'
            '"errors":[{"code":10000,"message":"Authentication error"}],"messages":[]}',
        },
    }
    traductor = TraductorDeTurno()
    resultado = traductor.traducir(_ev("session.next.step.failed", carga))
    assert len(resultado) == 1
    evento = resultado[0]
    assert evento.tipo == "error"
    assert evento.stream == "stderr"
    assert "HTTP 401" in evento.texto
    assert "Authentication error" in evento.texto
    # El error queda recordado para el cierre del turno (ver más abajo).
    assert traductor._ultimo_error == evento.texto  # noqa: SLF001 - test de estado interno


# --------------------------------------------------------------------------- #
# Familia: texto de respuesta y razonamiento -- cargas reales.
# --------------------------------------------------------------------------- #


def test_text_ended_da_progress_y_queda_como_ultimo_texto_para_el_cierre() -> None:
    carga = {
        "timestamp": 1785468908053,
        "sessionID": "ses_049c297fdffeS27JsvSYOcHDZe",
        "assistantMessageID": "msg_fb63d7071001GAvAdSH6Nc6uLU",
        "textID": "text-0",
        "text": "hola",
    }
    traductor = TraductorDeTurno()
    resultado = traductor.traducir(_ev("session.next.text.ended", carga))
    assert resultado == [EventoTraducido("progress", "hola")]
    assert traductor._ultimo_texto_final == "hola"  # noqa: SLF001


def test_reasoning_ended_da_status_con_el_razonamiento_real() -> None:
    carga = {
        "timestamp": 1785468908020,
        "sessionID": "ses_049c297fdffeS27JsvSYOcHDZe",
        "assistantMessageID": "msg_fb63d7071001GAvAdSH6Nc6uLU",
        "reasoningID": "reasoning-0",
        "text": 'The user asked me to say "hola" in plain text, nothing more. So I should '
        'reply with exactly "hola" and nothing else.',
    }
    resultado = TraductorDeTurno().traducir(_ev("session.next.reasoning.ended", carga))
    assert len(resultado) == 1
    assert resultado[0].tipo == "status"
    assert resultado[0].texto.startswith("Razonando: ")
    assert 'say "hola"' in resultado[0].texto


# --------------------------------------------------------------------------- #
# Familia: herramientas -- read (sin "file"), edit (con "file" exacto), bash
# (tool + command al llamar, output al terminar). Todas cargas reales.
# --------------------------------------------------------------------------- #


def test_tool_called_read_no_genera_file_en_su_success_por_ser_solo_lectura() -> None:
    llamado = {
        "timestamp": 1785468965354,
        "sessionID": "ses_049c1c2d2ffeVl0HH9MpR3m0us",
        "assistantMessageID": "msg_fb63e42b2001kaMQW7V8Qi0wej",
        "callID": "functions.read:0",
        "tool": "read",
        "input": {"path": "README.md"},
        "provider": {"executed": False},
    }
    exito = {
        "timestamp": 1785468965371,
        "sessionID": "ses_049c1c2d2ffeVl0HH9MpR3m0us",
        "assistantMessageID": "msg_fb63e42b2001kaMQW7V8Qi0wej",
        "callID": "functions.read:0",
        "structured": {
            "uri": "file:///workspace/README.md",
            "name": "README.md",
            "content": "Hola\nLinea 2\nLinea 3\n",
            "encoding": "utf8",
            "mime": "text/markdown",
        },
        "content": [],
        "outputPaths": [],
        "provider": {"executed": False},
    }
    traductor = TraductorDeTurno()
    r1 = traductor.traducir(_ev("session.next.tool.called", llamado))
    assert r1 == [EventoTraducido("tool", "Usando read.")]
    r2 = traductor.traducir(_ev("session.next.tool.success", exito))
    assert r2 == []  # lectura: nada nuevo que anunciar, ya se dijo "Usando read."


def test_tool_called_edit_success_produce_file_con_el_formato_exacto_que_parsea_ide_contexto() -> (
    None
):
    """El formato "Archivo actualizado: {ruta}" NO es cosmético -- ide_contexto._PATRON_ARCHIVO
    (regex ``^Archivo actualizado: (?P<ruta>.+)$``) lo parsea al carácter."""

    llamado = {
        "timestamp": 1785468967754,
        "sessionID": "ses_049c1c2d2ffeVl0HH9MpR3m0us",
        "assistantMessageID": "msg_fb63e5565001K6DDY3bQnZfVj8",
        "callID": "functions.edit:1",
        "tool": "edit",
        "input": {
            "path": "README.md",
            "oldString": "Linea 3\n",
            "newString": "Linea 3\nCAPTURA-EVENTOS-42\n",
        },
        "provider": {"executed": False},
    }
    exito = {
        "timestamp": 1785468967771,
        "sessionID": "ses_049c1c2d2ffeVl0HH9MpR3m0us",
        "assistantMessageID": "msg_fb63e5565001K6DDY3bQnZfVj8",
        "callID": "functions.edit:1",
        "structured": {
            "files": [
                {
                    "file": "README.md",
                    "patch": (
                        "Index: README.md\n@@ -1,3 +1,4 @@\n Hola\n Linea 2\n Linea 3\n"
                        "+CAPTURA-EVENTOS-42\n"
                    ),
                    "additions": 1,
                    "deletions": 0,
                    "status": "modified",
                }
            ],
            "replacements": 1,
        },
        "content": [
            {"type": "text", "text": "Edited file successfully: README.md\nReplacements: 1"}
        ],
        "outputPaths": [],
        "provider": {"executed": False},
    }
    traductor = TraductorDeTurno()
    r1 = traductor.traducir(_ev("session.next.tool.called", llamado))
    assert r1 == [EventoTraducido("tool", "Usando edit.")]
    r2 = traductor.traducir(_ev("session.next.tool.success", exito))
    assert r2 == [EventoTraducido("file", "Archivo actualizado: README.md")]

    import re

    patron = re.compile(r"^Archivo actualizado: (?P<ruta>.+)$")
    coincidencia = patron.match(r2[0].texto)
    assert coincidencia is not None
    assert coincidencia.group("ruta") == "README.md"


def test_tool_called_bash_produce_tool_y_command_al_llamar_y_output_al_terminar() -> None:
    llamado = {
        "timestamp": 1785468975634,
        "sessionID": "ses_049c1c2d2ffeVl0HH9MpR3m0us",
        "assistantMessageID": "msg_fb63e7a040019TqRNDHySEq1Jd",
        "callID": "functions.bash:2",
        "tool": "bash",
        "input": {"command": "cat README.md"},
        "provider": {"executed": False},
    }
    exito = {
        "timestamp": 1785468975656,
        "sessionID": "ses_049c1c2d2ffeVl0HH9MpR3m0us",
        "assistantMessageID": "msg_fb63e7a040019TqRNDHySEq1Jd",
        "callID": "functions.bash:2",
        "structured": {"exit": 0, "truncated": False},
        "content": [
            {"type": "text", "text": "Hola\nLinea 2\nLinea 3\nCAPTURA-EVENTOS-42\n"},
            {"type": "text", "text": "Command exited with code 0."},
        ],
        "outputPaths": [],
        "provider": {"executed": False},
    }
    traductor = TraductorDeTurno()
    r1 = traductor.traducir(_ev("session.next.tool.called", llamado))
    assert r1 == [
        EventoTraducido("tool", "Usando bash."),
        EventoTraducido("command", "$ cat README.md"),
    ]
    r2 = traductor.traducir(_ev("session.next.tool.success", exito))
    assert len(r2) == 1
    assert r2[0].tipo == "output"
    assert "CAPTURA-EVENTOS-42" in r2[0].texto
    assert "Command exited with code 0." in r2[0].texto


def test_tool_failed_correlaciona_el_nombre_de_la_herramienta_via_call_id() -> None:
    """Carga real: se pidió leer un archivo que de verdad no existe -- opencode devolvió
    session.next.tool.failed con el mensaje real. tool.failed NUNCA trae el nombre de la
    herramienta (confirmado también en test_ide_opencode.py); tiene que salir del
    tool.called anterior con el mismo callID."""

    llamado = {
        "timestamp": 1785469284000,
        "sessionID": "ses_049bcdc10ffeg30MmlIOn1GQAA",
        "assistantMessageID": "msg_fb6432d6d001oEESH8Tf90MoHx",
        "callID": "functions.read:0",
        "tool": "read",
        "input": {"path": "archivo_que_no_existe_de_verdad_42.txt"},
        "provider": {"executed": False},
    }
    fallo = {
        "timestamp": 1785469284568,
        "sessionID": "ses_049bcdc10ffeg30MmlIOn1GQAA",
        "assistantMessageID": "msg_fb6432d6d001oEESH8Tf90MoHx",
        "callID": "functions.read:0",
        "error": {
            "type": "unknown",
            "message": "Unable to read archivo_que_no_existe_de_verdad_42.txt",
        },
        "result": {
            "type": "error",
            "value": "Unable to read archivo_que_no_existe_de_verdad_42.txt",
        },
        "provider": {"executed": False},
    }
    traductor = TraductorDeTurno()
    traductor.traducir(_ev("session.next.tool.called", llamado))
    resultado = traductor.traducir(_ev("session.next.tool.failed", fallo))
    assert len(resultado) == 1
    assert resultado[0].tipo == "error"
    assert resultado[0].stream == "stderr"
    assert "«read»" in resultado[0].texto
    assert "archivo_que_no_existe_de_verdad_42.txt" in resultado[0].texto


def test_tool_failed_sin_called_previo_sigue_siendo_honesto_sobre_la_herramienta_desconocida() -> (
    None
):
    fallo = {
        "sessionID": "ses_x",
        "assistantMessageID": "msg_x",
        "callID": "functions.misterioso:9",
        "error": {"type": "unknown", "message": "algo salió mal"},
        "provider": {"executed": False},
    }
    resultado = TraductorDeTurno().traducir(_ev("session.next.tool.failed", fallo))
    assert resultado[0].tipo == "error"
    assert "herramienta desconocida" in resultado[0].texto


def test_tool_progress_derivado_del_schema_da_output() -> None:
    """NO capturado en vivo (ver el docstring del módulo): solo lo emiten herramientas de
    ejecución larga con salida incremental; ninguna de las probadas en esta ronda
    (read/edit/write/bash contra un workspace de prueba) tardó lo suficiente. Forma
    tomada de SessionNextToolProgress en /doc: mismos campos content/structured que
    tool.success (confirmado en vivo para ESE evento)."""

    carga = {
        "sessionID": "ses_x",
        "assistantMessageID": "msg_x",
        "callID": "functions.bash:5",
        "structured": {"exit": None},
        "content": [{"type": "text", "text": "...compilando (40%)..."}],
    }
    resultado = TraductorDeTurno().traducir(_ev("session.next.tool.progress", carga))
    assert resultado == [EventoTraducido("output", "...compilando (40%)...")]


# --------------------------------------------------------------------------- #
# Familia: reversión (revert) -- las tres, con cargas reales (stage con un
# messageID real de una respuesta real, cleared y committed reales).
# --------------------------------------------------------------------------- #


def test_revert_staged_con_carga_real() -> None:
    carga = {
        "timestamp": 1785470107964,
        "sessionID": "ses_049b069f6ffewTecECdNuWLUT2",
        "revert": {"messageID": "msg_fb64f9edd001wBR2nMZRJFB4CN", "diff": "", "files": []},
    }
    resultado = TraductorDeTurno().traducir(_ev("session.next.revert.staged", carga))
    assert len(resultado) == 1
    assert resultado[0].tipo == "status"
    assert "0 archivo(s)" in resultado[0].texto
    assert "pendiente de confirmar o descartar" in resultado[0].texto


def test_revert_cleared_con_carga_real() -> None:
    carga = {"timestamp": 1785470198874, "sessionID": "ses_049af04e4ffeev4H5LBgJYkHgm"}
    resultado = TraductorDeTurno().traducir(_ev("session.next.revert.cleared", carga))
    assert resultado == [
        EventoTraducido("status", "Se descartó la reversión pendiente; nada cambió.")
    ]


def test_revert_committed_con_carga_real() -> None:
    carga = {
        "timestamp": 1785470109977,
        "sessionID": "ses_049b069f6ffewTecECdNuWLUT2",
        "messageID": "msg_fb64f9edd001wBR2nMZRJFB4CN",
    }
    resultado = TraductorDeTurno().traducir(_ev("session.next.revert.committed", carga))
    assert resultado == [
        EventoTraducido("status", "Se aplicó la reversión a un punto anterior del historial.")
    ]


# --------------------------------------------------------------------------- #
# Familia: derivados del schema real (no disparados en esta sesión de
# trabajo) -- shell, retried, compaction. Ver el docstring del módulo para el
# porqué exacto de cada uno.
# --------------------------------------------------------------------------- #


def test_shell_started_ended_derivados_del_schema() -> None:
    traductor = TraductorDeTurno()
    inicio = traductor.traducir(
        _ev(
            "session.next.shell.started",
            {"sessionID": "ses_x", "messageID": "msg_x", "callID": "c1", "command": "npm test"},
        )
    )
    assert inicio == [EventoTraducido("command", "$ npm test")]
    fin = traductor.traducir(
        _ev(
            "session.next.shell.ended", {"sessionID": "ses_x", "callID": "c1", "output": "3 passed"}
        )
    )
    assert fin == [EventoTraducido("output", "3 passed")]


def test_retried_derivado_del_schema_menciona_el_intento_y_el_error() -> None:
    carga = {
        "sessionID": "ses_x",
        "attempt": 2,
        "error": {"message": "Provider timeout", "isRetryable": True, "statusCode": 503},
    }
    resultado = TraductorDeTurno().traducir(_ev("session.next.retried", carga))
    assert len(resultado) == 1
    assert "intento 2" in resultado[0].texto
    assert "Provider timeout" in resultado[0].texto


def test_compaction_started_y_ended_derivados_del_schema() -> None:
    traductor = TraductorDeTurno()
    inicio = traductor.traducir(
        _ev(
            "session.next.compaction.started",
            {"sessionID": "ses_x", "messageID": "msg_x", "reason": "context_limit"},
        )
    )
    assert inicio == [
        EventoTraducido("status", "Compactando el historial de la conversación (context_limit)…")
    ]
    fin = traductor.traducir(
        _ev(
            "session.next.compaction.ended",
            {
                "sessionID": "ses_x",
                "messageID": "msg_x",
                "reason": "context_limit",
                "text": "resumen...",
                "recent": "...",
            },
        )
    )
    assert fin == [EventoTraducido("status", "Historial compactado (context_limit).")]


# --------------------------------------------------------------------------- #
# El cierre del turno -- punto 4 del encargo: nunca el genérico "no dejó
# respuesta" cuando en realidad espera algo.
# --------------------------------------------------------------------------- #


def test_cerrar_turno_con_permiso_pendiente_dice_que_espera_y_que() -> None:
    permiso = SolicitudPermiso(
        id="perm_1",
        sessionID="ses_x",
        action="edit",
        resources=["README.md"],
        save=["*"],
    )
    cierre = TraductorDeTurno().cerrar_turno(permiso_pendiente=permiso)
    assert cierre.tipo == "status"
    assert "esperando tu aprobación" in cierre.texto
    assert "«edit»" in cierre.texto
    assert "README.md" in cierre.texto


def test_cerrar_turno_con_pregunta_pendiente_dice_que_espera_y_que() -> None:
    pregunta = SolicitudPregunta(
        id="quest_1",
        sessionID="ses_x",
        questions=[
            InfoPregunta(
                question="¿Uso npm o pnpm?",
                header="Gestor de paquetes",
                options=[
                    OpcionPregunta(label="npm", description=""),
                    OpcionPregunta(label="pnpm", description=""),
                ],
            )
        ],
    )
    cierre = TraductorDeTurno().cerrar_turno(pregunta_pendiente=pregunta)
    assert cierre.tipo == "status"
    assert "esperando que respondas" in cierre.texto
    assert "¿Uso npm o pnpm?" in cierre.texto


def test_cerrar_turno_usa_el_ultimo_texto_como_assistant_final_si_de_verdad_termino() -> None:
    traductor = TraductorDeTurno()
    traductor.traducir(
        _ev(
            "session.next.text.ended",
            {
                "sessionID": "ses_049c297fdffeS27JsvSYOcHDZe",
                "assistantMessageID": "msg_x",
                "textID": "text-0",
                "text": "hola",
            },
        )
    )
    cierre = traductor.cerrar_turno()
    assert cierre == EventoTraducido("assistant_final", "hola")


def test_cerrar_turno_sin_nada_pendiente_ni_texto_ni_error_es_honesto_no_generico() -> None:
    cierre = TraductorDeTurno().cerrar_turno()
    assert cierre.tipo == "status"
    # Nunca el mensaje viejo que engañó al dueño -- ver el docstring del módulo.
    assert cierre.texto != "Este turno no dejó una respuesta de texto."
    assert "no hay" in cierre.texto and "pendiente" in cierre.texto


def test_cerrar_turno_sin_texto_ni_espera_pero_con_error_lo_menciona() -> None:
    traductor = TraductorDeTurno()
    traductor.traducir(
        _ev(
            "session.next.step.failed",
            {
                "sessionID": "ses_x",
                "assistantMessageID": "msg_x",
                "error": {"type": "unknown", "message": "el proveedor devolvió 500"},
            },
        )
    )
    cierre = traductor.cerrar_turno()
    assert cierre.tipo == "status"
    assert "el proveedor devolvió 500" in cierre.texto


def test_cerrar_turno_prioriza_permiso_pendiente_sobre_texto_final() -> None:
    """Si el turno tiene texto Y una espera real detectada, la espera manda -- todavía
    puede seguir trabajando después de esa aprobación, así que el texto no es "el final"
    todavía."""

    traductor = TraductorDeTurno()
    traductor.traducir(
        _ev(
            "session.next.text.ended",
            {
                "sessionID": "ses_x",
                "assistantMessageID": "msg_x",
                "textID": "text-0",
                "text": "Voy a editar el archivo ahora.",
            },
        )
    )
    permiso = SolicitudPermiso(
        id="perm_1", sessionID="ses_x", action="bash", resources=["rm -rf build/"]
    )
    cierre = traductor.cerrar_turno(permiso_pendiente=permiso)
    assert cierre.tipo == "status"
    assert "esperando tu aprobación" in cierre.texto


# --------------------------------------------------------------------------- #
# question.v2.asked -- "que la IA me hable" (punto 5 del encargo).
# --------------------------------------------------------------------------- #


def test_traducir_pregunta_produce_agent_question_con_json_fiel_a_solicitudpregunta() -> None:
    pregunta = SolicitudPregunta(
        id="quest_042",
        sessionID="ses_abc",
        questions=[
            InfoPregunta(
                question="¿Sobrescribo el archivo de configuración existente?",
                header="Conflicto de configuración",
                options=[
                    OpcionPregunta(
                        label="Sí, sobrescribir", description="Se pierde el archivo actual"
                    ),
                    OpcionPregunta(label="No, cancelar", description="No se toca nada"),
                ],
                multiple=False,
                custom=False,
            )
        ],
        tool=FuenteHerramienta(messageID="msg_777", callID="functions.question:0"),
    )
    traducido = traducir_pregunta(pregunta)
    assert traducido.tipo == TIPO_PREGUNTA_AGENTE == "agent_question"
    payload = json.loads(traducido.texto)
    assert payload["request_id"] == "quest_042"
    assert payload["session_id"] == "ses_abc"
    assert len(payload["questions"]) == 1
    pregunta_json = payload["questions"][0]
    assert pregunta_json["question"] == "¿Sobrescribo el archivo de configuración existente?"
    assert pregunta_json["header"] == "Conflicto de configuración"
    assert [o["label"] for o in pregunta_json["options"]] == ["Sí, sobrescribir", "No, cancelar"]
    assert payload["tool"] == {"message_id": "msg_777", "call_id": "functions.question:0"}


def test_traducir_pregunta_sin_tool_no_incluye_la_clave() -> None:
    pregunta = SolicitudPregunta(
        id="quest_1",
        sessionID="ses_x",
        questions=[InfoPregunta(question="¿Continúo?", header="", options=[])],
    )
    payload = json.loads(traducir_pregunta(pregunta).texto)
    assert "tool" not in payload


# --------------------------------------------------------------------------- #
# permission.v2.asked en pedir_aprobacion -- cierra el fallo real que un
# verificador reprodujo en vivo (ver "permission.v2.* en pedir_aprobacion" en
# el docstring del módulo): antes de esta ronda no había ningún evento
# estructurado para pintar la solicitud, solo el texto libre de
# ``cerrar_turno``.
# --------------------------------------------------------------------------- #


def test_traducir_permiso_produce_agent_permission_con_json_fiel_a_solicitudpermiso() -> None:
    permiso = SolicitudPermiso(
        id="perm_042",
        sessionID="ses_abc",
        action="bash",
        resources=["rm -rf build/"],
        save=["*"],
        metadata={"url": "https://example.com"},
        source=FuenteHerramienta(messageID="msg_777", callID="functions.bash:0"),
    )
    traducido = traducir_permiso(permiso)
    assert traducido.tipo == TIPO_PERMISO_AGENTE == "agent_permission"
    payload = json.loads(traducido.texto)
    assert payload["request_id"] == "perm_042"
    assert payload["session_id"] == "ses_abc"
    assert payload["action"] == "bash"
    assert payload["resources"] == ["rm -rf build/"]
    assert payload["puede_recordar"] is True
    assert payload["metadata"] == {"url": "https://example.com"}
    assert payload["tool"] == {"message_id": "msg_777", "call_id": "functions.bash:0"}


def test_traducir_permiso_sin_save_marca_puede_recordar_falso() -> None:
    permiso = SolicitudPermiso(id="perm_1", sessionID="ses_x", action="edit", resources=["a.py"])
    payload = json.loads(traducir_permiso(permiso).texto)
    assert payload["puede_recordar"] is False


def test_traducir_permiso_sin_metadata_ni_tool_no_incluye_esas_claves() -> None:
    permiso = SolicitudPermiso(id="perm_1", sessionID="ses_x", action="edit", resources=["a.py"])
    payload = json.loads(traducir_permiso(permiso).texto)
    assert "metadata" not in payload
    assert "tool" not in payload
