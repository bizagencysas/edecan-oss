"""Quien pregunta tiene que poder oír la respuesta (y preguntar una sola vez).

Regresión de un fallo real, con capturas del dueño. La conversación fue:

1. "Créame un post de LinkedIn sobre Venezuela".
2. Edecán escribió el post y mostró DOS tarjetas preguntando lo mismo: la determinista
   de la herramienta de contenido ("Destino") y encima una improvisada con
   `preguntar_al_usuario` ("Cuenta").
3. El usuario contestó "Personal".
4. Edecán volvió a preguntar lo mismo, en texto y en tarjeta.
5. El usuario tocó la opción, que manda "Escríbelo con la voz de 'personal'.".
6. Se quedó girando más de cinco minutos sin producir nada.

Causa: el selector de capacidades decidía qué tools ofrecer solo con las palabras del
mensaje actual más las de los dos mensajes anteriores del usuario. Al contestar dos veces
seguidas, el mensaje original ("post", "linkedin") se salía de esa ventana y
`crear_contenido_social` desaparecía del catálogo. Al modelo le quedaba `buscar_web` y
`preguntar_al_usuario`: por eso volvía a preguntar, y por eso se iba a buscar en círculos
hasta agotar las vueltas del turno.

Contestar una pregunta es, por definición, una continuación: la intención vive en la
pregunta que la tool hizo, no en las palabras del usuario. Estos tests fijan las dos mitades
del arreglo -- la tool que preguntó vuelve a ofrecerse, y una sola tarjeta por turno.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from edecan_core.agent import Agent, question_tool_names_from_tool_log
from edecan_core.capability_routing import select_tool_specs
from edecan_core.tools.base import Tool, ToolContext, ToolResult
from edecan_core.tools.registry import ToolRegistry
from edecan_schemas import DoneEvent, ToolEndEvent, ToolStartEvent
from test_agent import (
    FakeLLMRouter,
    FakeProvider,
    FakeTool,
    _ctx,
    _persona,
    text_chunk,
    tool_call_chunk,
)
from test_capability_routing import ALL_SPECS

# El mensaje exacto que manda el cliente cuando el usuario TOCA la opción "Personal" de la
# tarjeta de destino (`QuestionOption.value` en `edecan_creative.social`).
RESPUESTA_TOCADA = "Publicalo con el destino 'personal'."
LA_QUE_PREGUNTO = "crear_contenido_social"


def _bloque_de_pregunta(header: str = "Destino") -> dict[str, Any]:
    return {
        "type": "question",
        "question": "¿En cuál de tus cuentas publico esto?",
        "header": header,
        "options": [
            {"label": "Personal", "value": RESPUESTA_TOCADA},
            {"label": "Acme", "value": "Escríbelo con la voz de 'organization'."},
        ],
    }


def _tool_log_con_pregunta(name: str = LA_QUE_PREGUNTO) -> list[dict[str, Any]]:
    """Bitácora de un turno que terminó mostrando la tarjeta, como la persiste la API."""

    return [
        ToolStartEvent(tool_call_id="call_1", name=name, args={}).model_dump(mode="json"),
        ToolEndEvent(
            tool_call_id="call_1",
            name=name,
            result_preview="Le pregunté a dónde va.",
            blocks=[_bloque_de_pregunta()],
        ).model_dump(mode="json"),
    ]


# --------------------------------------------------------------------------
# 1. El caso exacto que falla hoy
# --------------------------------------------------------------------------


def test_sin_el_arreglo_el_selector_pierde_la_tool_que_pregunto() -> None:
    """Fija la causa raíz: sin saber quién preguntó, las palabras no alcanzan.

    Si algún día este test falla, casi seguro es porque alguien metió "publicalo"/"destino"/
    "personal" como palabras clave de una familia. Eso tapa este caso y deja abierto el
    siguiente ("la segunda", "el de la empresa", ...). El arreglo es pasar
    `tools_con_pregunta_pendiente`, no ampliar el diccionario.
    """

    ofrecidas = {
        spec.name
        for spec in select_tool_specs(
            ALL_SPECS,
            RESPUESTA_TOCADA,
            recent_user_texts=["Personal", RESPUESTA_TOCADA],
        )
    }

    assert LA_QUE_PREGUNTO not in ofrecidas


def test_responder_la_tarjeta_de_destino_vuelve_a_ofrecer_la_tool_que_pregunto() -> None:
    ofrecidas = {
        spec.name
        for spec in select_tool_specs(
            ALL_SPECS,
            RESPUESTA_TOCADA,
            recent_user_texts=["Personal", RESPUESTA_TOCADA],
            tools_con_pregunta_pendiente=[LA_QUE_PREGUNTO],
        )
    }

    assert LA_QUE_PREGUNTO in ofrecidas


@pytest.mark.parametrize(
    "respuesta",
    [
        "Personal",
        "la segunda",
        "el de la empresa",
        "esa misma",
        RESPUESTA_TOCADA,
    ],
)
def test_cualquier_forma_de_contestar_conserva_la_tool(respuesta: str) -> None:
    """La respuesta a una pregunta puede ser CUALQUIER texto.

    Por eso el arreglo no puede ser una palabra clave más en `_FAMILIES`: no hay lista de
    palabras que cubra todas las maneras de contestar un modal.
    """

    ofrecidas = {
        spec.name
        for spec in select_tool_specs(
            ALL_SPECS,
            respuesta,
            recent_user_texts=["Personal", respuesta],
            tools_con_pregunta_pendiente=[LA_QUE_PREGUNTO],
        )
    }

    assert LA_QUE_PREGUNTO in ofrecidas


def test_la_pregunta_pendiente_no_le_quita_nada_al_turno() -> None:
    """Es aditivo: refuerza el catálogo, nunca reemplaza lo que ya se seleccionaba."""

    texto = "Créame un post de LinkedIn sobre Venezuela."
    sin_pendiente = {spec.name for spec in select_tool_specs(ALL_SPECS, texto)}
    con_pendiente = {
        spec.name
        for spec in select_tool_specs(
            ALL_SPECS, texto, tools_con_pregunta_pendiente=["listar_recordatorios"]
        )
    }

    assert sin_pendiente <= con_pendiente
    assert "listar_recordatorios" in con_pendiente


def test_una_heuristica_posterior_no_puede_volver_a_quitar_la_tool_que_pregunto() -> None:
    """`crear_documento` normalmente se descarta ante una petición de creación.

    Si la pregunta pendiente se añadiera antes de esos `discard`, el arreglo se caería en
    silencio justo en el flujo donde nació (crear + publicar).
    """

    ofrecidas = {
        spec.name
        for spec in select_tool_specs(
            ALL_SPECS,
            "Créame un post nuevo y publícalo.",
            tools_con_pregunta_pendiente=["crear_documento"],
        )
    }

    assert "crear_documento" in ofrecidas


# --------------------------------------------------------------------------
# 2. La pregunta pendiente se CONSUME: no se hereda para siempre
# --------------------------------------------------------------------------


def test_peticion_nueva_sin_relacion_no_arrastra_la_tool_de_la_pregunta_vieja() -> None:
    """Turno siguiente al siguiente: ya no hay pregunta abierta, no hay refuerzo."""

    ofrecidas = {
        spec.name
        for spec in select_tool_specs(
            ALL_SPECS,
            "¿Cuánto es 300 por 12?",
            recent_user_texts=[RESPUESTA_TOCADA, "¿Cuánto es 300 por 12?"],
            tools_con_pregunta_pendiente=[],
        )
    }

    assert LA_QUE_PREGUNTO not in ofrecidas
    assert "publicar_social" not in ofrecidas


def test_un_turno_que_no_pregunto_no_deja_nada_pendiente() -> None:
    """Este es el mecanismo que consume la pregunta.

    El llamador mira solo el ÚLTIMO turno del asistente. En cuanto ese turno termina sin
    tarjeta de pregunta, `question_tool_names_from_tool_log` devuelve vacío y el refuerzo
    desaparece solo, sin ningún estado que limpiar ni TTL que ajustar.
    """

    tool_log_del_turno_que_si_pregunto = _tool_log_con_pregunta()
    assert question_tool_names_from_tool_log(tool_log_del_turno_que_si_pregunto) == [
        LA_QUE_PREGUNTO
    ]

    tool_log_del_turno_siguiente = [
        ToolStartEvent(tool_call_id="call_9", name=LA_QUE_PREGUNTO, args={}).model_dump(
            mode="json"
        ),
        ToolEndEvent(
            tool_call_id="call_9",
            name=LA_QUE_PREGUNTO,
            result_preview="Borrador listo.",
            blocks=[],
        ).model_dump(mode="json"),
    ]
    assert question_tool_names_from_tool_log(tool_log_del_turno_siguiente) == []


@pytest.mark.parametrize("basura", [None, "", [], {}, "crear_contenido_social", [None, 3]])
def test_una_bitacora_rota_nunca_tumba_el_turno(basura: Any) -> None:
    assert question_tool_names_from_tool_log(basura) == []


def test_solo_cuenta_la_tool_que_realmente_mostro_la_tarjeta() -> None:
    tool_log = [
        ToolEndEvent(
            tool_call_id="call_1",
            name="generar_imagen",
            result_preview="Imagen lista.",
            blocks=[],
        ).model_dump(mode="json"),
        *_tool_log_con_pregunta(),
    ]

    assert question_tool_names_from_tool_log(tool_log) == [LA_QUE_PREGUNTO]


# --------------------------------------------------------------------------
# 3. Del historial al catálogo del turno (extremo a extremo dentro del agente)
# --------------------------------------------------------------------------


def _registry_grande() -> ToolRegistry:
    """Catálogo por encima del umbral que activa la selección por subconjunto."""

    registry = ToolRegistry()
    for spec in ALL_SPECS:
        registry.register(FakeTool(name=spec.name, description=spec.description))
    return registry


@pytest.mark.asyncio
async def test_el_agente_ofrece_la_tool_de_la_pregunta_pendiente_que_recibe_en_extras() -> None:
    provider = FakeProvider([[text_chunk("Listo.")]])
    agent = Agent(FakeLLMRouter(provider), _registry_grande())

    async for _ in agent.run_turn(
        ctx=_ctx(tools_con_pregunta_pendiente=[LA_QUE_PREGUNTO]),
        persona=_persona(),
        history=[],
        user_text=RESPUESTA_TOCADA,
        flags={},
    ):
        pass

    ofrecidas = {tool.name for tool in provider.received_requests[0].tools}
    assert LA_QUE_PREGUNTO in ofrecidas


@pytest.mark.asyncio
async def test_sin_pregunta_pendiente_el_agente_no_ofrece_esa_tool() -> None:
    provider = FakeProvider([[text_chunk("Listo.")]])
    agent = Agent(FakeLLMRouter(provider), _registry_grande())

    async for _ in agent.run_turn(
        ctx=_ctx(),
        persona=_persona(),
        history=[],
        user_text=RESPUESTA_TOCADA,
        flags={},
    ):
        pass

    ofrecidas = {tool.name for tool in provider.received_requests[0].tools}
    assert LA_QUE_PREGUNTO not in ofrecidas


# --------------------------------------------------------------------------
# 4. Una sola tarjeta de pregunta por turno
# --------------------------------------------------------------------------


class PreguntaTool(Tool):
    """Doble de una tool de dominio que devuelve su propio modal (como `social.py`)."""

    name = LA_QUE_PREGUNTO
    description = "Crea posts e imágenes para redes."
    input_schema = {"type": "object", "properties": {}}

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        self.calls.append(args)
        return ToolResult(
            content="Le pregunté a dónde va.",
            presentation=[_bloque_de_pregunta()],
        )


class PreguntarAlUsuarioFake(FakeTool):
    def __init__(self) -> None:
        super().__init__(
            name="preguntar_al_usuario",
            description="Muestra una pregunta con opciones.",
            result=ToolResult(
                content="Le mostré la pregunta.",
                presentation=[_bloque_de_pregunta(header="Cuenta")],
            ),
        )


def _bloques_de_pregunta(eventos: list[Any]) -> list[Any]:
    return [
        block
        for event in eventos
        if isinstance(event, ToolEndEvent)
        for block in event.blocks
        if block.type == "question"
    ]


@pytest.mark.asyncio
async def test_no_se_pueden_mostrar_dos_tarjetas_de_pregunta_en_el_mismo_turno() -> None:
    """Exactamente lo que vio el dueño: "Destino" y "Cuenta" preguntando lo mismo.

    Se impide en código y no en el prompt a propósito: el modelo no ve las tarjetas que ya
    se pintaron, así que pedírselo por escrito no es una garantía.
    """

    provider = FakeProvider(
        [
            [tool_call_chunk("call_1", LA_QUE_PREGUNTO, {})],
            [tool_call_chunk("call_2", "preguntar_al_usuario", {"pregunta": "¿Cuál cuenta?"})],
            [text_chunk("Listo.")],
        ]
    )
    dominio = PreguntaTool()
    preguntar = PreguntarAlUsuarioFake()
    registry = ToolRegistry()
    registry.register(dominio)
    registry.register(preguntar)

    eventos = [
        event
        async for event in Agent(FakeLLMRouter(provider), registry).run_turn(
            ctx=_ctx(),
            persona=_persona(),
            history=[],
            user_text="Créame un post de LinkedIn sobre Venezuela.",
            flags={},
        )
    ]

    assert dominio.calls, "la tool de dominio sí debía ejecutarse"
    assert preguntar.calls == [], "la segunda pregunta no debe siquiera ejecutarse"
    assert len(_bloques_de_pregunta(eventos)) == 1


@pytest.mark.asyncio
async def test_la_tarjeta_improvisada_primero_tampoco_deja_pasar_la_determinista() -> None:
    """El mismo bug con las tools al revés: `preguntar_al_usuario` primero.

    Frenar solo la tool de preguntar a demanda deja pasar este orden -- la tarjeta
    improvisada sale, y la determinista de la tool de dominio sale ENCIMA. Son dos
    tarjetas seguidas preguntando lo mismo otra vez, que es el fallo original. Lo único
    que lo cierra sin importar quién preguntó primero es que la tarjeta termine el turno.
    """

    provider = FakeProvider(
        [
            [tool_call_chunk("call_1", "preguntar_al_usuario", {"pregunta": "¿Qué enfoque?"})],
            [tool_call_chunk("call_2", LA_QUE_PREGUNTO, {})],
            [text_chunk("Listo.")],
        ]
    )
    dominio = PreguntaTool()
    registry = ToolRegistry()
    registry.register(dominio)
    registry.register(PreguntarAlUsuarioFake())

    eventos = [
        event
        async for event in Agent(FakeLLMRouter(provider), registry).run_turn(
            ctx=_ctx(),
            persona=_persona(),
            history=[],
            user_text="Créame un post de LinkedIn sobre Venezuela.",
            flags={},
        )
    ]

    assert len(_bloques_de_pregunta(eventos)) == 1
    assert dominio.calls == [], "el turno ya había terminado: la vuelta siguiente no ocurre"


@pytest.mark.asyncio
async def test_la_tarjeta_de_pregunta_cierra_el_turno() -> None:
    """Contrato de `QuestionBlock`: mostrarla TERMINA el turno.

    Estaba escrito en el esquema y no lo hacía cumplir nadie. Sin esto el loop sigue
    girando con una pregunta sin contestar en pantalla: el modelo gasta las vueltas que le
    quedan (fueron cinco minutos reales) y puede pintar una segunda tarjeta.
    """

    provider = FakeProvider(
        [
            [tool_call_chunk("call_1", LA_QUE_PREGUNTO, {})],
            [tool_call_chunk("call_2", "generar_imagen", {})],
            [text_chunk("Listo.")],
        ]
    )
    imagen = FakeTool(name="generar_imagen")
    registry = ToolRegistry()
    registry.register(PreguntaTool())
    registry.register(imagen)

    eventos = [
        event
        async for event in Agent(FakeLLMRouter(provider), registry).run_turn(
            ctx=_ctx(),
            persona=_persona(),
            history=[],
            user_text="Créame un post de LinkedIn sobre Venezuela.",
            flags={},
        )
    ]

    assert imagen.calls == [], "no debe haber una vuelta más después de la tarjeta"
    # Una sola llamada al modelo: la que pidió la tool que preguntó.
    assert len(provider.received_requests) == 1
    assert isinstance(eventos[-1], DoneEvent), "el turno tiene que cerrar limpio, no colgado"


@pytest.mark.asyncio
async def test_las_dos_preguntas_en_el_mismo_lote_tampoco_pasan() -> None:
    """El modelo puede pedir las dos tools de una sola vez, en paralelo."""

    provider = FakeProvider(
        [
            [
                tool_call_chunk("call_1", LA_QUE_PREGUNTO, {}),
                tool_call_chunk("call_2", "preguntar_al_usuario", {"pregunta": "¿Cuál cuenta?"}),
            ],
            [text_chunk("Listo.")],
        ]
    )
    preguntar = PreguntarAlUsuarioFake()
    registry = ToolRegistry()
    registry.register(PreguntaTool())
    registry.register(preguntar)

    eventos = [
        event
        async for event in Agent(FakeLLMRouter(provider), registry).run_turn(
            ctx=_ctx(),
            persona=_persona(),
            history=[],
            user_text="Créame un post de LinkedIn sobre Venezuela.",
            flags={},
        )
    ]

    assert preguntar.calls == []
    assert len(_bloques_de_pregunta(eventos)) == 1


@pytest.mark.asyncio
async def test_el_modelo_se_entera_de_por_que_no_se_mostro_la_segunda() -> None:
    """Un silencio lo dejaría suponiendo que la pregunta sí salió.

    Las dos van en el MISMO lote porque una tarjeta ya cierra el turno (ver
    `test_la_tarjeta_de_pregunta_cierra_el_turno`) el modelo ya no tiene una vuelta más
    donde leerlo, así que se verifica donde sigue siendo observable: el `tool_result` que
    el ejecutor devuelve por la llamada frenada. Ese bloque es lo que vería el modelo si
    el turno continuara, y lo que queda en la transcripción del turno.
    """

    agente = Agent(FakeLLMRouter(FakeProvider([])), ToolRegistry())
    preguntar = PreguntarAlUsuarioFake()
    call = SimpleNamespace(id="call_2", name="preguntar_al_usuario", arguments={})

    bloques = [
        bloque
        async for _evento, bloque in agente._execute_resolved_calls(
            ctx=_ctx(),
            resolved_calls=[(call, preguntar, None)],
            tool_log=_tool_log_con_pregunta(),
        )
        if bloque is not None
    ]

    assert preguntar.calls == [], "la segunda pregunta no debe siquiera ejecutarse"
    assert len(bloques) == 1
    assert bloques[0]["tool_use_id"] == "call_2"
    assert "YA le dejó al usuario una tarjeta" in bloques[0]["content"]


@pytest.mark.asyncio
async def test_preguntar_al_usuario_sigue_funcionando_cuando_ninguna_tool_pregunto() -> None:
    """El caso legítimo: nadie preguntó todavía, el modal es la única forma de no adivinar."""

    provider = FakeProvider(
        [
            [tool_call_chunk("call_1", "preguntar_al_usuario", {"pregunta": "¿Cuál cuenta?"})],
            [text_chunk("Listo.")],
        ]
    )
    preguntar = PreguntarAlUsuarioFake()
    registry = ToolRegistry()
    registry.register(preguntar)

    eventos = [
        event
        async for event in Agent(FakeLLMRouter(provider), registry).run_turn(
            ctx=_ctx(),
            persona=_persona(),
            history=[],
            user_text="Publica algo por mí.",
            flags={},
        )
    ]

    assert len(preguntar.calls) == 1
    assert len(_bloques_de_pregunta(eventos)) == 1


@pytest.mark.asyncio
async def test_una_tool_de_dominio_nunca_se_frena_por_una_pregunta_previa() -> None:
    """El candado es solo para la tool de preguntar a demanda.

    Frenar una tool de dominio porque en el mismo lote hubo una tarjeta le impediría hacer
    su trabajo real, que es exactamente el agujero que este archivo existe para cerrar. Lo
    que sí termina es el TURNO, después de ejecutar el lote entero: la vuelta siguiente ya
    no ocurre (ver `test_la_tarjeta_de_pregunta_cierra_el_turno`).
    """

    provider = FakeProvider(
        [
            [
                tool_call_chunk("call_1", LA_QUE_PREGUNTO, {}),
                tool_call_chunk("call_2", "generar_imagen", {}),
            ],
            [text_chunk("Listo.")],
        ]
    )
    imagen = FakeTool(name="generar_imagen")
    registry = ToolRegistry()
    registry.register(PreguntaTool())
    registry.register(imagen)

    async for _ in Agent(FakeLLMRouter(provider), registry).run_turn(
        ctx=_ctx(),
        persona=_persona(),
        history=[],
        user_text="Créame un post de LinkedIn sobre Venezuela.",
        flags={},
    ):
        pass

    assert len(imagen.calls) == 1
