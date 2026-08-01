"""El nombre de quien preguntó sobrevive de un turno al siguiente.

`edecan_core.capability_routing` sabe qué hacer con una pregunta pendiente (volver a ofrecer
la tool que la hizo, sin depender de palabras clave), pero ese dato tiene que llegarle. El
`tool_log` con los bloques ricos se guarda en `messages.tool_calls`; el historial que recibe
el agente se aplana a texto puro y lo tira. Este archivo cubre el puente: de la fila
persistida a `ctx.extras["tools_con_pregunta_pendiente"]`.

Regresión de un fallo real: el usuario pidió publicar algo, contestó la tarjeta de
destino, y el turno siguiente ya no tenía `crear_contenido_social` en el catálogo. Ver
`packages/core/tests/test_pregunta_pendiente.py` para el resto de la historia.

Nota sobre el disparador: un pedido EXPLÍCITO ("créame un post de LinkedIn sobre X") ya
NO llega al agente -- lo intercepta el atajo del servidor (`_es_pedido_directo_de_post_linkedin`
-> `_stream_direct_linkedin_post` en `routers/conversations.py`), que resuelve el destino
por su cuenta y nunca usa este puente. Por eso estos dos casos de extremo a extremo manejan
al agente con el pedido AMBIGUO que el propio código deja pasar al agente ("escríbeme algo
para publicar", documentado en `_es_pedido_directo_de_post_linkedin`): ahí el agente sí pregunta
el destino con `crear_contenido_social`, que es donde vive el mecanismo que este archivo cubre.
El puente es agnóstico a la tool: cualquier herramienta que deje una pregunta abierta lo usa.
"""

from __future__ import annotations

import uuid
from typing import Any

from conftest import auth_headers

from edecan_api.routers.conversations import _tools_con_pregunta_pendiente

LA_QUE_PREGUNTO = "crear_contenido_social"


def _bloque_de_pregunta() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "type": "question",
        "question": "¿En cuál de tus cuentas publico esto?",
        "header": "Destino",
        "options": [{"label": "Personal"}, {"label": "Acme"}],
        "multi_select": False,
        "allow_free_text": True,
    }


def _tool_log(*, con_pregunta: bool, name: str = LA_QUE_PREGUNTO) -> list[dict[str, Any]]:
    return [
        {"type": "tool_start", "tool_call_id": "call_1", "name": name, "args": {}},
        {
            "type": "tool_end",
            "tool_call_id": "call_1",
            "name": name,
            "result_preview": "listo",
            "artifacts": [],
            "blocks": [_bloque_de_pregunta()] if con_pregunta else [],
        },
    ]


def _fila(role: str, *, tool_calls: Any = None) -> dict[str, Any]:
    return {"role": role, "content": {"text": "..."}, "tool_calls": tool_calls}


# ---------------------------------------------------------------------------
# La regla: solo el ÚLTIMO turno del asistente
# ---------------------------------------------------------------------------


def test_la_tool_que_dejo_la_tarjeta_abierta_se_reconoce() -> None:
    filas = [_fila("user"), _fila("assistant", tool_calls=_tool_log(con_pregunta=True))]

    assert _tools_con_pregunta_pendiente(filas) == [LA_QUE_PREGUNTO]


def test_la_pregunta_se_consume_cuando_el_asistente_vuelve_a_hablar_sin_preguntar() -> None:
    """Lo que impide que la tool vieja se arrastre para siempre."""

    filas = [
        _fila("user"),
        _fila("assistant", tool_calls=_tool_log(con_pregunta=True)),
        _fila("user"),
        _fila("assistant", tool_calls=_tool_log(con_pregunta=False)),
    ]

    assert _tools_con_pregunta_pendiente(filas) == []


def test_un_turno_de_texto_puro_tambien_consume_la_pregunta() -> None:
    filas = [
        _fila("user"),
        _fila("assistant", tool_calls=_tool_log(con_pregunta=True)),
        _fila("user"),
        _fila("assistant"),
    ]

    assert _tools_con_pregunta_pendiente(filas) == []


def test_una_conversacion_sin_asistente_no_tiene_nada_pendiente() -> None:
    assert _tools_con_pregunta_pendiente([]) == []
    assert _tools_con_pregunta_pendiente([_fila("user")]) == []


# ---------------------------------------------------------------------------
# De extremo a extremo: se persiste en un turno y llega en el siguiente
# ---------------------------------------------------------------------------


class _ScriptedAgent:
    """Doble de `Agent`: guion por turno y captura del `ctx.extras` recibido."""

    guion: list[list[dict[str, Any]]] = []
    extras_vistos: list[dict[str, Any]] = []

    def __init__(self, llm_router: Any, registry: Any) -> None:
        self.llm_router = llm_router
        self.registry = registry

    async def run_turn(
        self, *, ctx, persona, history, user_text, flags, extra_tools=None, seleccion=None
    ):
        type(self).extras_vistos.append(dict(ctx.extras))
        eventos = type(self).guion.pop(0) if type(self).guion else []
        for evento in eventos:
            yield evento
        yield {"type": "done", "usage": {}}


async def _crear_conversacion(client: Any, headers: dict[str, str]) -> str:
    response = await client.post("/v1/conversations", json={}, headers=headers)
    assert response.status_code == 201
    return response.json()["id"]


async def _mandar(client: Any, headers: dict[str, str], conversation_id: str, texto: str) -> Any:
    return await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"text": texto},
        headers=headers,
    )


async def test_la_tarjeta_de_un_turno_llega_como_pregunta_pendiente_al_siguiente(
    client, monkeypatch
) -> None:
    import edecan_api.routers.conversations as conversations_module

    _ScriptedAgent.extras_vistos = []
    _ScriptedAgent.guion = [
        [
            {
                "type": "tool_end",
                "tool_call_id": "call_1",
                "name": LA_QUE_PREGUNTO,
                "result_preview": "Le pregunté a dónde va.",
                "artifacts": [],
                "blocks": [_bloque_de_pregunta()],
            }
        ],
        [],
    ]
    monkeypatch.setattr(conversations_module, "Agent", _ScriptedAgent)

    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_pro")
    conversation_id = await _crear_conversacion(client, headers)

    primera = await _mandar(client, headers, conversation_id, "Escríbeme algo para publicar.")
    assert primera.status_code == 200
    # Nadie preguntó antes del primer turno.
    assert _ScriptedAgent.extras_vistos[0]["tools_con_pregunta_pendiente"] == []

    segunda = await _mandar(client, headers, conversation_id, "Personal")
    assert segunda.status_code == 200
    assert _ScriptedAgent.extras_vistos[1]["tools_con_pregunta_pendiente"] == [LA_QUE_PREGUNTO]


async def test_un_turno_sin_tarjeta_no_deja_pregunta_pendiente(client, monkeypatch) -> None:
    import edecan_api.routers.conversations as conversations_module

    _ScriptedAgent.extras_vistos = []
    _ScriptedAgent.guion = [
        [
            {
                "type": "tool_end",
                "tool_call_id": "call_1",
                "name": LA_QUE_PREGUNTO,
                "result_preview": "Borrador listo.",
                "artifacts": [],
                "blocks": [],
            }
        ],
        [],
    ]
    monkeypatch.setattr(conversations_module, "Agent", _ScriptedAgent)

    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_pro")
    conversation_id = await _crear_conversacion(client, headers)

    await _mandar(client, headers, conversation_id, "Escríbeme algo para publicar.")
    await _mandar(client, headers, conversation_id, "Gracias")

    assert _ScriptedAgent.extras_vistos[1]["tools_con_pregunta_pendiente"] == []
